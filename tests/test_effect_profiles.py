from __future__ import annotations

import copy
from pathlib import Path

import pytest
from click.testing import CliRunner

from aviutl2_api import (
    AUP2_EFFECT_COMPATIBLE_PROJECT_VERSIONS,
    apply_effects,
    compare_aup2_roundtrip,
    parse_file,
    parse_string,
    serialize,
    serialize_to_file,
    validate_standard_effects,
)
from aviutl2_api.cli import main
from aviutl2_api.editing import EFFECT_PROFILES, effect, linear, native_effect
from aviutl2_api.effect_profiles import (
    EffectProfileUnavailableError,
    available_effect_profiles,
    describe_effect_profile,
    resolve_effect,
)
from aviutl2_api.models import Effect, Project, Scene, StaticValue, TimelineObject
from aviutl2_api.presets import get_sample_presets

FIXTURES = Path(__file__).parent / "fixtures"


def _shape_project() -> tuple[Project, TimelineObject]:
    obj = TimelineObject(
        object_id=0,
        layer=0,
        frame_start=0,
        frame_end=59,
        effects=[
            Effect(
                0,
                "図形",
                {
                    "図形の種類": "円",
                    "サイズ": StaticValue(100.0),
                    "縦横比": StaticValue(0.0),
                    "ライン幅": StaticValue(4000.0),
                    "色": "ffffff",
                    "角を丸くする": StaticValue(0.0),
                },
            ),
            Effect(1, "標準描画", {"X": StaticValue(0.0)}),
        ],
    )
    return Project(scenes=[Scene(scene_id=0, objects=[obj])]), obj


def _audio_project() -> tuple[Project, TimelineObject]:
    obj = TimelineObject(
        object_id=0,
        layer=0,
        frame_start=0,
        frame_end=59,
        effects=[
            Effect(
                0,
                "音声ファイル",
                {
                    "再生位置": StaticValue(0.0),
                    "再生速度": StaticValue(100.0),
                    "ファイル": "audio.wav",
                    "トラック": StaticValue(0.0),
                    "ループ再生": StaticValue(0.0),
                },
            ),
            Effect(1, "音声再生", {"音量": StaticValue(100.0)}),
        ],
    )
    return Project(scenes=[Scene(scene_id=0, objects=[obj])]), obj


def test_all_twenty_profiles_have_complete_ordered_templates() -> None:
    assert available_effect_profiles() == EFFECT_PROFILES
    assert len(EFFECT_PROFILES) == 20

    for profile in EFFECT_PROFILES:
        resolved = resolve_effect(effect(profile))
        assert resolved.profile == profile
        assert resolved.items
        assert tuple(resolved.item_types) == tuple(name for name, _ in resolved.items)


def test_semantic_values_use_natural_units_and_strict_enums() -> None:
    resolved = resolve_effect(
        effect(
            "drop_shadow",
            x_px=12,
            opacity=0.25,
            color="#FFD966",
        )
    )
    values = dict(resolved.items)
    assert values["X"] == StaticValue(12.0)
    assert values["濃さ"] == StaticValue(25.0)
    assert values["影色"] == "ffd966"

    animated = resolve_effect(effect("blur", radius_px=linear(2, 9)))
    assert dict(animated.items)["範囲"].start == 2
    assert dict(animated.items)["範囲"].end == 9

    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        resolve_effect(effect("drop_shadow", opacity=1.1))
    with pytest.raises(ValueError, match="must be one of"):
        resolve_effect(effect("wipe", kind="guessed"))
    with pytest.raises(TypeError, match="unsupported glow parameter"):
        resolve_effect(effect("glow", guessed_strength=1))
    with pytest.raises(ValueError, match="unknown effect profile"):
        effect("not-a-profile")


def test_effect_schema_is_machine_readable_without_guessing_ranges() -> None:
    glow = describe_effect_profile("glow")
    parameters = glow["parameters"]

    assert glow["native_name"] == "グロー"
    assert glow["scope"] == "video"
    assert isinstance(parameters, dict)
    assert parameters["shape"]["values"] == ("cross4", "cross6", "circle")
    assert parameters["shape"]["default"] == "cross4"
    assert parameters["color"]["unit"] == "#RRGGBB"
    assert "minimum" not in parameters["strength"]

    opacity = describe_effect_profile("drop_shadow")["parameters"]["opacity"]
    assert opacity["minimum"] == 0.0
    assert opacity["maximum"] == 1.0


def test_native_effect_is_explicitly_unverified() -> None:
    resolved = resolve_effect(
        native_effect("Third Party", {"custom": 10}, scope="video")
    )
    assert resolved.profile is None
    assert resolved.verified is False
    assert resolved.items == (("custom", 10),)


def test_effect_manifest_explicitly_accepts_host_saved_2010200() -> None:
    assert AUP2_EFFECT_COMPATIBLE_PROJECT_VERSIONS == frozenset({2001901, 2010200})
    assert resolve_effect(effect("glow"), project_version=2010200).verified

    with pytest.raises(EffectProfileUnavailableError, match="9999999"):
        resolve_effect(effect("glow"), project_version=9999999)


def test_apply_effects_uses_host_canonical_order_and_validates_domains() -> None:
    project, obj = _shape_project()
    added = apply_effects(
        project,
        obj,
        effect("glow", strength=50, color="#FFD966"),
        effect("outline", size_px=4),
    )

    assert [value.name for value in obj.effects] == [
        "図形",
        "標準描画",
        "グロー",
        "縁取り",
    ]
    assert added[0].properties["光色"] == "ffd966"
    assert len(added[0].properties) == 10
    assert validate_standard_effects(project).valid

    with pytest.raises(ValueError, match="domain"):
        apply_effects(project, obj, effect("audio_gain"))

    audio_project, audio = _audio_project()
    apply_effects(
        audio_project,
        audio,
        effect("audio_gain", volume=80),
        effect("audio_fade", in_seconds=0.2, out_seconds=0.5),
    )
    assert [value.name for value in audio.effects] == [
        "音声ファイル",
        "音声再生",
        "音量調整",
        "音量フェード",
    ]
    assert validate_standard_effects(audio_project).valid


def test_host_saved_version_and_disabled_effect_are_supported() -> None:
    project, obj = _shape_project()
    project.version = 2010200

    added = apply_effects(
        project,
        obj,
        effect("glow", enabled=False, strength=25, color="#FFFFFF"),
    )

    assert added[0].properties["effect.disable"] == StaticValue(1.0)
    serialized = serialize(project)
    assert "effect.disable=1.00" in serialized
    validation = validate_standard_effects(parse_string(serialized))
    assert validation.valid, validation.errors


def test_validator_accepts_host_preserved_effect_after_standard_drawing() -> None:
    project, obj = _shape_project()
    added = apply_effects(
        project,
        obj,
        effect("fade", in_seconds=0.5, out_seconds=0.5),
    )
    obj.effects.remove(added[0])
    obj.effects.append(added[0])

    assert [value.name for value in obj.effects] == ["図形", "標準描画", "フェード"]
    assert validate_standard_effects(project).valid


def test_roundtrip_comparator_reports_effect_reorder_precisely() -> None:
    before, obj = _shape_project()
    apply_effects(before, obj, effect("fade"))
    after = parse_string(serialize(before))
    after_obj = after.scenes[0].objects[0]
    after_obj.effects[1], after_obj.effects[2] = (
        after_obj.effects[2],
        after_obj.effects[1],
    )

    report = compare_aup2_roundtrip(before, after)

    assert [difference.code for difference in report.differences] == [
        "EFFECT_ORDER_CHANGED"
    ]


def test_all_profiles_survive_library_roundtrip_semantically() -> None:
    project, obj = _shape_project()
    video_profiles = [
        profile for profile in EFFECT_PROFILES if not profile.startswith("audio_")
    ]
    apply_effects(project, obj, *(effect(profile) for profile in video_profiles))
    parsed = parse_string(serialize(project))

    validation = validate_standard_effects(parsed)
    comparison = compare_aup2_roundtrip(project, parsed)
    assert validation.valid, validation.errors
    assert comparison.compatible, comparison.differences


def test_invalid_host_fallback_items_are_errors() -> None:
    project, obj = _shape_project()
    source = obj.effects[0]
    source.properties.pop("図形の種類")
    source.properties["図形"] = "四角形"

    validation = validate_standard_effects(project)

    assert not validation.valid
    assert "STANDARD_EFFECT_UNKNOWN_ITEM" in {issue.code for issue in validation.errors}

    audio_project, audio = _audio_project()
    audio.effects[0].properties["動画ファイルと連携"] = StaticValue(1.0)
    assert not validate_standard_effects(audio_project).valid


def test_anonymous_open_save_fallback_fixture_is_rejected() -> None:
    before = parse_file(FIXTURES / "effect_fallback_before.aup2")
    after = parse_file(FIXTURES / "effect_fallback_after.aup2")

    validation = validate_standard_effects(before)
    comparison = compare_aup2_roundtrip(before, after)

    assert {issue.code for issue in validation.errors} == {
        "STANDARD_EFFECT_UNKNOWN_ITEM"
    }
    assert not comparison.compatible
    assert [difference.code for difference in comparison.differences] == [
        "EXPLICIT_EFFECT_ITEM_REMOVED",
        "EXPLICIT_EFFECT_ITEM_REMOVED",
    ]
    assert comparison.normalizations == 5


def test_roundtrip_comparison_allows_only_known_host_normalization() -> None:
    before, before_obj = _shape_project()
    before_obj.effects[0].properties.pop("角を丸くする")
    after = copy.deepcopy(before)
    after.scenes[0].objects[0].effects[0].properties["角を丸くする"] = StaticValue(0.0)

    accepted = compare_aup2_roundtrip(before, after)
    assert accepted.compatible
    assert accepted.normalizations == 1

    broken = copy.deepcopy(after)
    broken.scenes[0].objects[0].effects[0].properties.pop("図形の種類")
    rejected = compare_aup2_roundtrip(after, broken)
    assert not rejected.compatible
    assert rejected.differences[0].code == "EXPLICIT_EFFECT_ITEM_REMOVED"


def test_legacy_cli_filter_uses_complete_manifest_and_safe_order(tmp_path) -> None:
    project, _ = _shape_project()
    file = tmp_path / "effect.aup2"
    serialize_to_file(project, file)

    result = CliRunner().invoke(
        main,
        [
            "filter",
            "add",
            str(file),
            "0",
            "gradient",
            "--strength",
            "75",
            "--color",
            "FFD966",
        ],
    )

    assert result.exit_code == 0, result.output
    parsed = parse_string(file.read_text(encoding="utf-8"))
    obj = parsed.scenes[0].objects[0]
    assert [value.name for value in obj.effects] == [
        "図形",
        "標準描画",
        "グラデーション",
    ]
    gradient = obj.effects[2]
    assert gradient.properties["合成モード"] == "通常"
    assert gradient.properties["形状"] == "線形"
    assert validate_standard_effects(parsed).valid


def test_builtin_effect_presets_are_complete_shared_templates() -> None:
    presets = {preset.id: preset for preset in get_sample_presets()}

    assert set(presets["glow-pulse"].effects[0].properties) == {
        name for name, _ in resolve_effect(effect("glow")).items
    }
    assert set(presets["blur-soft"].effects[0].properties) == {
        name for name, _ in resolve_effect(effect("blur")).items
    }
    assert "ランダムに強さを変える" in presets["shake"].effects[0].properties
    assert "ランダム" not in presets["shake"].effects[0].properties


def test_cli_general_text_overlap_warning_is_off_by_default(tmp_path) -> None:
    project = Project.create_empty()
    file = tmp_path / "text-overlap.aup2"
    serialize_to_file(project, file)
    runner = CliRunner()

    first = runner.invoke(
        main,
        ["add", "text", str(file), "Title", "--layer", "0", "--from", "0"],
    )
    second = runner.invoke(
        main,
        ["add", "text", str(file), "Note", "--layer", "1", "--from", "0"],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "警告" not in first.output + second.output
