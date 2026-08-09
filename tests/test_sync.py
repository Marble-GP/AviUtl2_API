from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aviutl2_api import LocalProject
from aviutl2_api.editing import EditPlan, PlannedPlacement, PlanResult, effect
from aviutl2_api.live.events import BridgeEvent, EventWatchResult
from aviutl2_api.live.scene import SceneInfo
from aviutl2_api.live.snapshot import ProjectSnapshot, SnapshotObject
from aviutl2_api.sync import (
    SyncCapabilityUnavailableError,
    SyncConflictError,
    SyncPartialApplyError,
    SyncSession,
    SyncValidationError,
    _canonical_alias,
    _plan_copy,
)

SAMPLE = Path(__file__).parents[1] / "samples" / "EmptyProject.aup2"


def test_alias_normalization_keeps_text_and_color_semantics() -> None:
    text_001 = "[Object]\r\n[Object.0]\r\neffect.name=テキスト\r\nテキスト=001\r\n"
    text_1 = "[Object]\r\n[Object.0]\r\neffect.name=テキスト\r\nテキスト=1\r\n"
    numeric_a = "[Object]\r\n[Object.0]\r\neffect.name=標準描画\r\nX=1.000\r\n"
    numeric_b = "[Object]\r\n[Object.0]\r\neffect.name=標準描画\r\nX=1\r\n"
    animated_a = (
        "[Object]\r\n[Object.0]\r\neffect.name=標準描画\r\nX=1.000,2.000,直線移動,0\r\n"
    )
    animated_b = (
        "[Object]\r\n[Object.0]\r\neffect.name=標準描画\r\nX=1,2,直線移動,0.0\r\n"
    )

    assert _canonical_alias(text_001) != _canonical_alias(text_1)
    assert _canonical_alias(numeric_a) == _canonical_alias(numeric_b)
    assert _canonical_alias(animated_a) == _canonical_alias(animated_b)


def test_sync_local_simulation_defers_native_media_effect_routing() -> None:
    plan = EditPlan().add_video(
        "clip.mp4",
        duration=30,
        effects=[effect("audio_gain", gain=80)],
    )
    copied = _plan_copy(
        plan,
        (PlannedPlacement(0, "command-0", 2, 10, 30),),
        native_media_readback=True,
    )

    assert copied.commands[0].values["effects"] == ()
    assert plan.commands[0].values["effects"]


class _FakeLive:
    def __init__(self, backend: LocalProject, path: Path) -> None:
        self.backend = backend
        self.client = self
        self.path = path
        self.snapshot_calls = 0

    def get_snapshot(self, *, include_alias: bool = False) -> ProjectSnapshot:
        self.snapshot_calls += 1
        snapshot = self.backend.get_snapshot()
        objects = tuple(
            SnapshotObject(
                f"obj-{value.local_id}",
                snapshot.revision,
                value.layer,
                value.frame_start,
                value.frame_end,
                None,
                value.alias if include_alias else None,
            )
            for value in snapshot.objects
        )
        return ProjectSnapshot(
            snapshot.revision,
            snapshot.scene_id,
            objects,
            total=len(objects),
        )

    def get_project_info(self) -> dict[str, Any]:
        return {"project_file_path": str(self.path)}

    def get_current_scene(self) -> SceneInfo:
        summary = self.backend.summary()
        frame_rate = summary["frame_rate"]
        assert isinstance(frame_rate, dict)
        return SceneInfo(
            int(summary["scene_id"]),
            self.backend.revision,
            "Root",
            int(summary["width"]),
            int(summary["height"]),
            int(frame_rate["rate"]),
            int(frame_rate["scale"]),
            int(summary["sample_rate"]),
            False,
        )

    def validate(self, plan: EditPlan):  # type: ignore[no-untyped-def]
        return self.backend.validate(plan)

    def apply(
        self,
        plan: EditPlan,
        *,
        operation_id: str | None = None,
    ) -> PlanResult:
        del operation_id
        return self.backend.apply(plan)


class _LifecycleLive(_FakeLive):
    def __init__(self, backend: LocalProject, path: Path) -> None:
        super().__init__(backend, path)
        self.events: list[BridgeEvent] = []

    def watch_events(
        self,
        *,
        after_sequence: int,
        timeout_ms: int,
        types: tuple[str, ...],
    ) -> EventWatchResult:
        del timeout_ms
        selected = tuple(
            value
            for value in self.events
            if value.sequence > after_sequence and value.type in types
        )
        latest = self.events[-1].sequence if self.events else 0
        return EventWatchResult(selected, latest, False, not selected)


class _OldCapabilityLive(_FakeLive):
    @property
    def capabilities(self) -> dict[str, object]:
        return {
            "methods": [
                "edit.plan.apply",
                "edit.plan.validate",
                "project.get_snapshot",
                "scene.get_current",
            ]
        }


def _projects(tmp_path: Path) -> tuple[LocalProject, _FakeLive, Path]:
    source = tmp_path / "project.aup2"
    source.write_bytes(SAMPLE.read_bytes())
    return (
        LocalProject.load(source),
        _FakeLive(LocalProject.load(source), source),
        source,
    )


def test_sync_apply_changes_memory_only_and_remains_clean(tmp_path: Path) -> None:
    local, live, source = _projects(tmp_path)
    original = source.read_bytes()
    session = SyncSession.bind(local, live)  # type: ignore[arg-type]
    plan = EditPlan().add_text("Agent Title", key="title", duration=45)

    validation = session.validate(plan)
    calls_before_apply = live.snapshot_calls
    result = session.apply(plan, operation_id="test-operation")

    assert validation.valid
    assert result.operation_id == "test-operation"
    assert not result.atomic and not result.disk_written
    assert local.dirty
    assert result.local_snapshot.revision == local.revision
    assert result.local_simulation_result.revision == local.revision
    assert result.local_result is result.local_simulation_result
    assert result.undo_grouped == result.live_result.undo_grouped
    assert live.snapshot_calls - calls_before_apply == 2
    assert source.read_bytes() == original
    status = session.status()
    assert status.state == "clean"
    assert status.file_unsaved
    assert not status.local_changed_since_bind
    assert not status.live_changed_since_bind
    assert len(session.diff().synced) == 1
    assert session.find(text="Agent Title").one().object_id.startswith("obj-")


def test_sync_refuses_plugin_without_096_safety_capabilities(
    tmp_path: Path,
) -> None:
    local, live, source = _projects(tmp_path)
    old = _OldCapabilityLive(live.backend, source)

    with pytest.raises(SyncCapabilityUnavailableError):
        SyncSession.bind(local, old)  # type: ignore[arg-type]


def test_gui_side_change_is_reported_but_never_auto_merged(tmp_path: Path) -> None:
    local, live, _source = _projects(tmp_path)
    session = SyncSession.bind(local, live)  # type: ignore[arg-type]
    live.backend.apply(EditPlan().add_shape("circle", duration=20))

    status = session.status()

    assert status.state == "diverged"
    assert status.live_changed_since_bind
    assert not status.local_changed_since_bind
    assert len(local.objects) == 0
    assert len(live.backend.objects) == 1


def test_sync_move_preserves_existing_local_object_id(tmp_path: Path) -> None:
    local, live, _source = _projects(tmp_path)
    session = SyncSession.bind(local, live)  # type: ignore[arg-type]
    session.apply(EditPlan().add_text("move me", duration=20))
    before = session.find()[0]

    session.apply(EditPlan().move(before, at=40, layer=2))
    after = session.find()[0]

    assert after.local.local_id == before.local.local_id
    assert (after.layer, after.frame_start) == (2, 40)


def test_local_side_change_is_reported_but_never_pushed(tmp_path: Path) -> None:
    local, live, _source = _projects(tmp_path)
    session = SyncSession.bind(local, live)  # type: ignore[arg-type]
    local.apply(EditPlan().add_text("local only", duration=20))

    status = session.status()

    assert status.state == "diverged"
    assert status.local_changed_since_bind
    assert not status.live_changed_since_bind
    assert len(live.backend.objects) == 0


def test_sync_distinguishes_invalid_plan_from_dirty_binding(tmp_path: Path) -> None:
    local, live, _source = _projects(tmp_path)
    session = SyncSession.bind(local, live)  # type: ignore[arg-type]
    invalid = EditPlan()
    invalid.add_text("A", at=0, layer=0, duration=20)
    invalid.add_text("B", at=0, layer=0, duration=20)

    with pytest.raises(SyncValidationError) as invalid_error:
        session.apply(invalid)
    assert invalid_error.value.code == "SYNC_VALIDATION_FAILED"
    assert invalid_error.value.validation.errors

    local.add_text("local only", duration=20)
    with pytest.raises(SyncConflictError) as conflict_error:
        session.apply(EditPlan().add_text("never applied", duration=20))
    assert conflict_error.value.code == "SYNC_NOT_CLEAN"
    assert conflict_error.value.status is not None


def test_live_success_local_failure_can_recover_without_reapplying_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local, live, source = _projects(tmp_path)
    original = source.read_bytes()
    session = SyncSession.bind(local, live)  # type: ignore[arg-type]
    original_adopt = local._adopt

    def fail_once(_other: LocalProject, *, expected_revision: int) -> None:
        del expected_revision
        raise RuntimeError("simulated local commit failure")

    monkeypatch.setattr(local, "_adopt", fail_once)
    plan = EditPlan().add_text("recover", duration=20)
    with pytest.raises(SyncPartialApplyError) as raised:
        session.apply(plan, operation_id="recover-operation")

    assert raised.value.receipt.live_applied
    assert raised.value.receipt.recovery_required
    assert len(live.backend.objects) == 1
    assert len(local.objects) == 0
    monkeypatch.setattr(local, "_adopt", original_adopt)

    receipt = session.recover(raised.value.receipt)

    assert receipt.local_committed and not receipt.recovery_required
    assert len(local.objects) == 1
    assert len(live.backend.objects) == 1
    assert source.read_bytes() == original


def test_project_load_event_invalidates_binding_without_automatic_reload(
    tmp_path: Path,
) -> None:
    source = tmp_path / "project.aup2"
    source.write_bytes(SAMPLE.read_bytes())
    local = LocalProject.load(source)
    live = _LifecycleLive(LocalProject.load(source), source)
    session = SyncSession.bind(local, live)  # type: ignore[arg-type]
    live.events.append(BridgeEvent(1, 100, "project_loaded"))

    status = session.status()

    assert status.state == "incompatible"
    assert "project_loaded" in status.diagnostics[-1]
    assert len(local.objects) == 0
