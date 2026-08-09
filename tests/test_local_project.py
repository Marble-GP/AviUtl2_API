from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

import pytest

from aviutl2_api import (
    EditPlan,
    LiveProject,
    LocalProject,
    SyncSession,
    effect,
    linear,
    native_effect,
)
from aviutl2_api.editing import (
    InvalidMediaArgumentsError,
    PlanValidationError,
)
from aviutl2_api.local import (
    LocalCapabilityUnavailableError,
    LocalFileChangedError,
    LocalOverwriteRequiredError,
)
from aviutl2_api.models.values import parse_property_value

SAMPLE = Path(__file__).parents[1] / "samples" / "EmptyProject.aup2"


def test_numeric_looking_text_and_file_values_remain_strings() -> None:
    assert parse_property_value("テキスト", "001") == "001"
    assert parse_property_value("ファイル", "123") == "123"


def _source(tmp_path: Path, *, extra: str = "") -> Path:
    path = tmp_path / "project.aup2"
    text = SAMPLE.read_text(encoding="utf-8") + extra
    path.write_bytes(text.encode("utf-8"))
    return path


def test_checkpoint_is_numbered_lossless_and_does_not_rebind(tmp_path: Path) -> None:
    marker = "[third.party]\nopaque.value=keep-me\nraw line without equals\n"
    source = _source(tmp_path, extra=marker)
    original = source.read_bytes()
    local = LocalProject.load(source)
    plan = EditPlan().add_text(
        "第一章",
        duration=30,
        effects=[effect("glow", strength=50)],
    )

    result = local.apply(plan)
    first = local.checkpoint()
    second = local.checkpoint()

    assert result.atomic is True
    assert result.effects["command-0"][0].profile == "glow"
    assert first.path.name == "project.ai-0001.aup2"
    assert second.path.name == "project.ai-0002.aup2"
    assert local.path == source.resolve()
    assert source.read_bytes() == original
    payload = first.path.read_bytes()
    assert not payload.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in payload
    rendered = payload.decode("utf-8")
    assert marker.replace("\n", "\r\n") in rendered
    assert str(first.path.resolve()) in rendered


def test_save_as_requires_hash_to_replace_existing_file(tmp_path: Path) -> None:
    source = _source(tmp_path)
    local = LocalProject.load(source)
    target = tmp_path / "existing.aup2"
    target.write_bytes(source.read_bytes())

    with pytest.raises(FileExistsError):
        local.save_as(target)
    with pytest.raises(LocalFileChangedError):
        local.save_as(target, overwrite=True, expected_sha256="0" * 64)

    expected = hashlib.sha256(target.read_bytes()).hexdigest()
    receipt = local.save_as(target, overwrite=True, expected_sha256=expected)
    assert receipt.replaced and receipt.rebound
    assert local.path == target.resolve()
    assert not local.dirty


def test_save_source_rechecks_hash_and_creates_backup(tmp_path: Path) -> None:
    source = _source(tmp_path)
    local = LocalProject.load(source)
    local.apply(EditPlan().add_shape("circle", duration=20))
    source.write_bytes(source.read_bytes() + b"\r\n[external]\r\nvalue=1\r\n")

    with pytest.raises(LocalOverwriteRequiredError) as permission:
        local.save_source()
    assert permission.value.code == "LOCAL_OVERWRITE_REQUIRED"
    assert source.read_bytes().endswith(b"value=1\r\n")
    with pytest.raises(LocalFileChangedError):
        local.save_source(overwrite=True)

    local.reload(discard_changes=True)
    local.apply(EditPlan().add_shape("circle", duration=20))
    receipt = local.save_source(overwrite=True)
    assert receipt.replaced and receipt.backup_path is not None
    assert receipt.backup_path.is_file()
    assert not local.dirty


def test_invalid_plan_is_atomic_and_does_not_consume(tmp_path: Path) -> None:
    local = LocalProject.load(_source(tmp_path))
    plan = EditPlan()
    plan.add_text("A", at=0, layer=0, duration=30)
    plan.add_text("B", at=0, layer=0, duration=30)
    before = local.summary()

    validation = local.validate(plan)

    assert not validation.valid
    assert local.summary() == before
    assert not plan.consumed


def test_local_mutations_return_fresh_revision_scoped_references(
    tmp_path: Path,
) -> None:
    local = LocalProject.load(_source(tmp_path))
    created = (
        local.apply(EditPlan().add_text("before", key="title", duration=30))
        .objects["title"]
        .primary
    )

    updated_result = local.apply(
        EditPlan().update(created, key="title", text="after", x=25)
    )
    updated = updated_result.objects["title"].primary
    assert updated.revision == local.revision
    assert local.find(text="after").one().local_id == updated.local_id

    with pytest.raises(PlanValidationError):
        local.apply(EditPlan().move(created, at=40, layer=1))

    moved = (
        local.apply(EditPlan().move(updated, key="title", at=40, layer=1))
        .objects["title"]
        .primary
    )
    effected_result = local.apply(
        EditPlan().apply_effect(
            moved,
            effect("glow", strength=50),
            key="glow",
        )
    )
    effected = effected_result.objects["glow"].primary
    applied = effected_result.effects["glow"][0]

    enabled_result = local.apply(
        EditPlan().set_effect_enabled(
            effected,
            applied.selector,
            False,
            key="disabled",
        )
    )
    assert enabled_result.objects["disabled"].primary.revision == local.revision


def test_agent_facing_imports_and_media_signatures_are_consistent() -> None:
    assert all(
        value is not None
        for value in (
            EditPlan,
            LiveProject,
            LocalProject,
            SyncSession,
            effect,
            linear,
            native_effect,
        )
    )
    for owner in (EditPlan, LocalProject, LiveProject):
        audio = inspect.signature(owner.add_audio).parameters
        video = inspect.signature(owner.add_video).parameters
        assert "x" not in audio and "opacity" not in audio
        assert "fit" in video

    with pytest.raises(InvalidMediaArgumentsError) as raised:
        EditPlan().add_media("audio.wav", kind="audio", x=10)
    assert raised.value.code == "INVALID_MEDIA_ARGUMENTS"


def test_local_immediate_api_and_unified_find(tmp_path: Path) -> None:
    local = LocalProject.load(_source(tmp_path))
    created = local.add_text(
        "First Chapter",
        key="title",
        duration=30,
        effects=[effect("glow", strength=50)],
    )
    updated = local.update(created.primary, text="First Chapter: Start", x=25)
    applied = local.apply_effect(updated.primary, effect("outline", size_px=4))

    assert applied.profile == "outline"
    assert local.find(text="First Chapter: Start").one().revision == local.revision
    assert len(local.find(text_contains="chapter", overlap=(0, 29))) == 1
    assert len(local.find(effect="glow")) == 1

    with pytest.raises(LocalCapabilityUnavailableError) as raised:
        local.find(name="title")
    assert raised.value.code == "LOCAL_QUERY_FILTER_UNAVAILABLE"


def test_edit_preserves_untouched_property_order_and_formatting(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    seed = LocalProject.load(source)
    seed.apply(EditPlan().add_text("Title", duration=30))
    generated = seed.checkpoint(tmp_path / "seed.aup2").path
    original = generated.read_text(encoding="utf-8")
    original = original.replace("Y=0.00", "Y=0.000000", 1)
    generated.write_text(original, encoding="utf-8")
    local = LocalProject.load(generated)
    target = local.objects.one()

    local.apply(EditPlan().update(target, x=25))
    rendered = local.checkpoint(tmp_path / "edited.aup2").path.read_text(
        encoding="utf-8"
    )

    assert "Y=0.000000" in rendered
    assert rendered.index("X=25.00") < rendered.index("Y=0.000000")


def test_multi_scene_edit_is_rejected_without_ownership_guessing(
    tmp_path: Path,
) -> None:
    source = _source(
        tmp_path,
        extra=(
            "[scene.1]\nscene=1\nname=Other\nvideo.width=1920\n"
            "video.height=1080\nvideo.rate=30\nvideo.scale=1\n"
            "audio.rate=44100\n"
        ),
    )
    local = LocalProject.load(source)

    validation = local.validate(EditPlan().add_text("unsafe"))

    assert not validation.valid
    assert validation.issues[0].code == "LOCAL_SCENE_OWNERSHIP_UNVERIFIED"
    with pytest.raises(PlanValidationError):
        local.apply(EditPlan().add_text("unsafe"))


def test_unknown_version_is_checkpoint_only(tmp_path: Path) -> None:
    source = _source(tmp_path)
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "version=2001901",
            "version=2999999",
        ),
        encoding="utf-8",
    )
    local = LocalProject.load(source)

    assert local.checkpoint().path.is_file()
    assert not local.validate(EditPlan().add_text("unsupported")).valid
    with pytest.raises(LocalCapabilityUnavailableError):
        local.save_as(tmp_path / "rebound.aup2")
    with pytest.raises(LocalCapabilityUnavailableError):
        local.save_source()
