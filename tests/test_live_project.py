from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest

from aviutl2_api.editing import (
    AddTextInstruction,
    EditPlan,
    LinearMotion,
    PlanApplyError,
    ProjectChangedError,
    Transform,
    effect,
    linear,
)
from aviutl2_api.effect_profiles import get_effect_profile
from aviutl2_api.live import (
    CapabilityUnavailableError,
    LiveClient,
    LiveObject,
    LiveProject,
)
from aviutl2_api.live.catalog import (
    CatalogEffect,
    CatalogItem,
    EffectCatalogPage,
    EffectFlags,
)
from aviutl2_api.live.commands import CreateFromAliasCommand
from aviutl2_api.live.inspection import (
    EffectInspection,
    ItemInspection,
    ObjectInspection,
)
from aviutl2_api.live.layers import LayerInfo, LayerPage
from aviutl2_api.live.media import MediaProbe
from aviutl2_api.live.protocol import BridgeRemoteError
from aviutl2_api.live.scene import SceneInfo
from aviutl2_api.live.snapshot import ProjectSnapshot, SnapshotObject


class FakeHighLevelClient:
    def __init__(
        self,
        *,
        native_plan: bool = True,
        linked_media: bool = False,
    ) -> None:
        plan_methods = ["edit.plan.validate", "edit.plan.apply"] if native_plan else []
        self.methods = [
            "project.get_info",
            "project.get_layers",
            "project.get_snapshot",
            "batch.validate",
            "batch.apply",
            "timeline.transaction.validate",
            "timeline.transaction.apply",
            *plan_methods,
        ]
        self.revision = 10
        self.objects = [SnapshotObject("obj-10-0", 10, 0, 0, 29, "existing", None)]
        self.snapshot_calls: list[bool] = []
        self.validated: list[dict[str, Any]] = []
        self.applied: list[dict[str, Any]] = []
        self.closed = False
        self.linked_media = linked_media

    def get_capabilities(self) -> dict[str, Any]:
        return {"methods": self.methods}

    def get_snapshot(self, *, include_alias: bool = False) -> ProjectSnapshot:
        self.snapshot_calls.append(include_alias)
        return ProjectSnapshot(
            self.revision,
            0,
            tuple(self.objects),
            total=len(self.objects),
        )

    def get_project_info(self) -> dict[str, Any]:
        return {
            "cursor_frame": 100,
            "cursor_layer": 0,
            "frame_max": 999,
            "layer_max": 2,
        }

    def get_layers(self, *, start: int = 0, count: int = 128) -> LayerPage:
        layers = tuple(
            LayerInfo(index, None, True, False, True, 0)
            for index in range(start, min(3, start + count))
        )
        return LayerPage(self.revision, 0, 2, 0, 3, start, layers)

    def get_current_scene(self) -> SceneInfo:
        return SceneInfo(0, self.revision, "Root", 1920, 1080, 30, 1, 48000, False)

    def probe_media(self, _file: object) -> MediaProbe:
        return MediaProbe(True, True, True, True, True, "video", 1, 1, 2.0, 1920, 1080)

    def validate_edit_plan(
        self,
        *,
        expected_revision: int,
        commands: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        assert expected_revision == self.revision
        self.validated = [dict(command) for command in commands]
        return {"valid": True, "warnings": []}

    def inspect_object(self, obj: SnapshotObject) -> ObjectInspection:
        return ObjectInspection(
            obj.object_id,
            obj.revision,
            obj.frame_start,
            (
                EffectInspection(0, 0, "Glow", "Glow#0", True, False, ()),
                EffectInspection(
                    1,
                    0,
                    "標準描画",
                    "標準描画",
                    True,
                    False,
                    (
                        ItemInspection("X", "number", 2, "0.00", None),
                        ItemInspection("Y", "number", 2, "0.00", None),
                        ItemInspection("拡大率", "number", 2, "100.00", None),
                    ),
                ),
            ),
        )

    def _mutate(self, commands: Sequence[Mapping[str, Any]]) -> None:
        before = self.objects
        self.revision += 1
        after = [
            SnapshotObject(
                f"obj-{self.revision}-{index}",
                self.revision,
                value.layer,
                value.frame_start,
                value.frame_end,
                value.name,
                None,
            )
            for index, value in enumerate(before)
        ]
        for command in commands:
            op = command["op"]
            if op in {"object.create_from_alias", "object.create_from_media_file"}:
                index = len(after)
                frame = int(command["frame"])
                length = int(command["length"])
                after.append(
                    SnapshotObject(
                        f"obj-{self.revision}-{index}",
                        self.revision,
                        int(command["layer"]),
                        frame,
                        frame + length - 1,
                        None,
                        None,
                    )
                )
                if op == "object.create_from_media_file" and self.linked_media:
                    linked_index = len(after)
                    after.append(
                        SnapshotObject(
                            f"obj-{self.revision}-{linked_index}",
                            self.revision,
                            int(command["layer"]) + 1,
                            frame,
                            frame + length - 1,
                            None,
                            None,
                        )
                    )
        self.objects = after

    def apply_edit_plan(
        self,
        *,
        expected_revision: int,
        commands: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        assert expected_revision == self.revision
        self.applied = [dict(command) for command in commands]
        self._mutate(commands)
        return {
            "applied_count": len(commands),
            "revision": self.revision,
            "undo_grouped": True,
            "atomic": False,
            "rollback": {
                "attempted": False,
                "complete": True,
                "restored_count": 0,
                "gui_undo_required": False,
            },
            "commands": [
                {"command_index": index, "key": value["key"], "status": "applied"}
                for index, value in enumerate(commands)
            ],
        }

    def validate_batch(self, commands: Sequence[CreateFromAliasCommand]) -> None:
        self.validated = [command.to_wire() for command in commands]

    def apply_batch(self, commands: Sequence[CreateFromAliasCommand]) -> None:
        wire = [
            {
                "op": "object.create_from_alias",
                "key": command.client_id,
                "layer": command.layer,
                "frame": command.frame,
                "length": command.length,
            }
            for command in commands
        ]
        self.applied = wire
        self._mutate(wire)

    def close(self) -> None:
        self.closed = True


class FakeSemanticEffectClient(FakeHighLevelClient):
    def __init__(self) -> None:
        super().__init__()
        self.created_effects: list[dict[str, Any]] = []

    def get_capabilities(self) -> dict[str, Any]:
        result = super().get_capabilities()
        result.update(
            {
                "semantic_effect_profiles": True,
                "edit_plan_create_effect_stack": True,
                "media_group_effect_routing": True,
                "linear_effect_values": True,
                "aup2_effect_manifest_version": 2001901,
            }
        )
        return result

    def get_effect_catalog(
        self,
        *,
        start: int = 0,
        count: int = 128,
    ) -> EffectCatalogPage:
        profiles = ("glow", "outline", "audio_gain")
        effects = []
        for type_code, profile in enumerate(profiles, start=1):
            definition = get_effect_profile(profile)
            effects.append(
                CatalogEffect(
                    definition.native_name,
                    "filter",
                    type_code,
                    EffectFlags(
                        video=definition.scope == "video",
                        audio=definition.scope == "audio",
                        filter_object=False,
                        camera=False,
                    ),
                    tuple(
                        CatalogItem(item.name, item.kind, item_code)
                        for item_code, item in enumerate(definition.items, start=1)
                    ),
                )
            )
        page = tuple(effects[start : start + count])
        next_start = start + len(page) if start + len(page) < len(effects) else None
        return EffectCatalogPage(start, len(effects), next_start, page)

    def apply_edit_plan(
        self,
        *,
        expected_revision: int,
        commands: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        self.created_effects = [
            dict(value)
            for command in commands
            for value in cast(Sequence[Mapping[str, Any]], command.get("effects", ()))
        ]
        return super().apply_edit_plan(
            expected_revision=expected_revision,
            commands=commands,
        )

    def inspect_object(self, obj: SnapshotObject) -> ObjectInspection:
        effects = []
        for index, value in enumerate(self.created_effects):
            items = tuple(
                ItemInspection(
                    item["item"],
                    "number",
                    1,
                    item["value"],
                    None,
                )
                for item in value["items"]
            )
            effects.append(
                EffectInspection(
                    index,
                    sum(
                        1
                        for previous in self.created_effects[:index]
                        if previous["effect"] == value["effect"]
                    ),
                    value["effect"],
                    f"{value['effect']}#{index}",
                    value["enabled"],
                    False,
                    items,
                )
            )
        if not effects:
            return super().inspect_object(obj)
        return ObjectInspection(
            obj.object_id,
            obj.revision,
            obj.frame_start,
            tuple(effects),
        )


def _project(fake: FakeHighLevelClient) -> LiveProject:
    return LiveProject(cast(LiveClient, fake))


def test_parallel_plan_uses_cursor_and_first_free_layers() -> None:
    fake = FakeHighLevelClient()
    project = _project(fake)
    plan = EditPlan(sequence="parallel")
    plan.add_text("第一章", key="title", opacity=0.5, rotation=12)
    plan.add_shape("rectangle", key="background")

    assert isinstance(plan.commands[0], AddTextInstruction)

    validation = project.validate(plan)

    assert validation.valid
    assert [(item.frame, item.layer) for item in validation.placements] == [
        (100, 0),
        (100, 1),
    ]
    opacity = fake.validated[0]["alias"]
    assert "透明度=50.0" in opacity
    assert "Z軸回転=12.0" in opacity
    assert fake.snapshot_calls == [False]


def test_linear_motion_and_extended_shape_use_native_alias_tracks() -> None:
    fake = FakeHighLevelClient()
    project = _project(fake)
    plan = EditPlan().add_shape(
        "star",
        key="star",
        x=linear(100, -100),
        rotation=linear(0, 360),
        opacity=linear(1.0, 0.5),
    )

    validation = project.validate(plan)

    assert validation.valid
    alias = fake.validated[0]["alias"]
    assert "図形の種類=星型" in alias
    assert "X=100.00,-100.00,直線移動,0" in alias
    assert "Z軸回転=0.00,360.00,直線移動,0" in alias
    assert "透明度=0.00,50.00,直線移動,0" in alias


def test_linear_motion_validates_endpoints_and_transform_ranges() -> None:
    assert linear(1, 2) == LinearMotion(1.0, 2.0)
    with pytest.raises(ValueError, match="finite"):
        linear(0, float("inf"))
    with pytest.raises(ValueError, match="opacity"):
        Transform(opacity=linear(1.0, 1.1))
    with pytest.raises(ValueError, match="scale"):
        Transform(scale=linear(100, 0))


def test_serial_plan_places_default_durations_back_to_back() -> None:
    fake = FakeHighLevelClient()
    project = _project(fake)
    plan = EditPlan(sequence="serial")
    plan.add_text("A", key="a").add_shape("circle", key="b")

    validation = project.validate(plan)

    placement_values = [
        (item.frame, item.layer, item.duration) for item in validation.placements
    ]
    assert placement_values == [
        (100, 0, 60),
        (160, 0, 60),
    ]


def test_apply_resolves_once_returns_objects_and_consumes_plan() -> None:
    fake = FakeHighLevelClient()
    project = _project(fake)
    plan = EditPlan().add_text("Title", key="title", duration=30)

    result = project.apply(plan)

    assert result.revision_before == 10
    assert result.revision == 11
    assert result.undo_grouped is True
    assert result.atomic is False
    assert result.objects["title"].primary.midpoint == 114
    assert plan.consumed is True
    assert fake.snapshot_calls == [False, False]
    with pytest.raises(RuntimeError, match="cannot be reused"):
        project.apply(plan)


def test_media_duration_comes_from_native_probe() -> None:
    fake = FakeHighLevelClient()
    project = _project(fake)

    validation = project.validate(EditPlan().add_video("clip.mp4", key="clip"))

    assert validation.placements[0].duration == 60
    assert fake.validated[0]["length"] == 60


def test_native_video_transform_targets_video_playback_effect() -> None:
    fake = FakeHighLevelClient()
    project = _project(fake)

    validation = project.validate(
        EditPlan().add_video("clip.mp4", key="clip", y=300, scale=45)
    )

    assert validation.valid
    assert fake.validated[0]["items"] == [
        {"effect": "映像再生", "item": "Y", "value": "300.00"},
        {"effect": "映像再生", "item": "拡大率", "value": "45.00"},
    ]


def test_existing_native_video_transform_uses_inspected_effect() -> None:
    class NativeVideoClient(FakeHighLevelClient):
        def inspect_object(self, obj: SnapshotObject) -> ObjectInspection:
            return ObjectInspection(
                obj.object_id,
                obj.revision,
                obj.frame_start,
                (
                    EffectInspection(
                        1,
                        0,
                        "映像再生",
                        "映像再生",
                        True,
                        False,
                        (
                            ItemInspection("Y", "number", 2, "0.00", None),
                            ItemInspection("拡大率", "number", 2, "100.00", None),
                        ),
                    ),
                ),
            )

    fake = NativeVideoClient()
    project = _project(fake)
    target = project.find(name="existing").one()

    project.update(target, y=240, scale=55)

    assert fake.applied[0]["items"] == [
        {"effect": "映像再生", "item": "Y", "value": "240.00"},
        {"effect": "映像再生", "item": "拡大率", "value": "55.00"},
    ]


def test_parallel_media_group_excludes_other_command_primaries() -> None:
    fake = FakeHighLevelClient(linked_media=True)
    project = _project(fake)
    plan = EditPlan(sequence="parallel")
    plan.add_text("Title", key="title", duration=30)
    plan.add_shape("circle", key="shape", duration=30)
    plan.add_video("clip.mp4", key="video", duration=30)

    result = project.apply(plan)

    assert len(result.objects["title"]) == 1
    assert len(result.objects["shape"]) == 1
    assert [value.layer for value in result.objects["video"]] == [2, 3]


def test_explicit_collision_and_stale_reference_are_rejected() -> None:
    fake = FakeHighLevelClient()
    project = _project(fake)
    collision = project.validate(EditPlan().add_text("x", at=10, layer=0))
    assert not collision.valid
    assert "occupied" in collision.errors[0]

    stale = LiveObject(SnapshotObject("obj-9-0", 9, 0, 0, 1, None, None))
    with pytest.raises(ProjectChangedError, match="stale"):
        project.update(stale, x=10)


def test_native_failure_exposes_rollback_receipt() -> None:
    class FailingClient(FakeHighLevelClient):
        def apply_edit_plan(
            self,
            *,
            expected_revision: int,
            commands: Sequence[Mapping[str, Any]],
        ) -> dict[str, Any]:
            raise BridgeRemoteError(
                "PLAN_PARTIAL_FAILURE",
                "command failed",
                details={
                    "failed_command_index": 1,
                    "current_revision": 11,
                    "rollback": {
                        "attempted": True,
                        "complete": False,
                        "restored_count": 1,
                        "gui_undo_required": True,
                    },
                },
            )

    project = _project(FailingClient())
    plan = EditPlan().add_text("A", key="a").add_shape("circle", key="b")

    with pytest.raises(PlanApplyError) as raised:
        project.apply(plan)

    assert raised.value.result is not None
    assert raised.value.result.rollback.attempted is True
    assert raised.value.result.rollback.complete is False
    assert raised.value.result.rollback.gui_undo_required is True
    assert [item.status for item in raised.value.result.commands] == [
        "applied",
        "failed",
    ]
    assert plan.consumed is False


def test_legacy_alias_fallback_and_mixed_plan_refusal() -> None:
    fake = FakeHighLevelClient(native_plan=False)
    project = _project(fake)
    result = project.apply(EditPlan().add_text("legacy", key="title"))
    assert "LEGACY_PLUGIN_FALLBACK" in result.warnings
    assert result.objects["title"].primary.frame_start == 100

    current = project.find(name="existing").one()
    mixed = EditPlan().add_text("new").update(current, x=10)
    with pytest.raises(RuntimeError, match="mixed EditPlan"):
        project.apply(mixed)

    transformed_media = EditPlan().add_video("clip.mp4", x=10)
    with pytest.raises(CapabilityUnavailableError, match="cannot apply"):
        project.validate(transformed_media)


def test_context_manager_closes_low_level_client() -> None:
    fake = FakeHighLevelClient()
    with _project(fake) as project:
        assert project.summary()["object_count"] == 1
    assert fake.closed is True


def test_high_level_effect_name_is_resolved_to_native_selector() -> None:
    fake = FakeHighLevelClient()
    project = _project(fake)
    target = project.find(name="existing").one()

    project.set_effect_enabled(target, "Glow", False)

    assert fake.applied[0]["selector"] == "Glow#0"


def test_nested_host_cursor_and_visible_empty_layers_are_supported() -> None:
    class NestedHostClient(FakeHighLevelClient):
        def get_project_info(self) -> dict[str, Any]:
            return {
                "cursor": {"frame": 42, "layer": 0},
                "frame_max": 0,
                "layer_max": 0,
            }

        def get_layers(self, *, start: int = 0, count: int = 128) -> LayerPage:
            layers = tuple(
                LayerInfo(index, None, True, False, True, 0)
                for index in range(start, min(8, start + count))
            )
            return LayerPage(self.revision, 0, 0, 0, 8, start, layers)

    project = _project(NestedHostClient())
    plan = EditPlan(sequence="parallel")
    for index in range(6):
        plan.add_text(str(index), key=f"particle-{index}")

    validation = project.validate(plan)

    assert project.summary()["cursor_frame"] == 42
    assert [item.layer for item in validation.placements] == list(range(6))


def test_create_time_effect_stack_is_ordered_and_receipted() -> None:
    fake = FakeSemanticEffectClient()
    project = _project(fake)
    plan = EditPlan().add_text(
        "Title",
        key="title",
        effects=[
            effect("glow", strength=50, color="#FFD966"),
            effect("outline", size_px=4, enabled=False),
            effect("glow", strength=20),
        ],
    )

    validation = project.validate(plan)
    wire_effects = fake.validated[0]["effects"]
    assert validation.valid
    assert [value["profile"] for value in wire_effects] == [
        "glow",
        "outline",
        "glow",
    ]
    assert wire_effects[1]["enabled"] is False
    assert len(wire_effects[0]["items"]) == 10

    result = project.apply(plan)
    assert [value.profile for value in result.effects["title"]] == [
        "glow",
        "outline",
        "glow",
    ]
    assert result.effects["title"][1].enabled is False


def test_create_effect_stack_requires_new_plugin_capability() -> None:
    project = _project(FakeHighLevelClient())
    plan = EditPlan().add_text("Title", effects=[effect("glow")])

    with pytest.raises(CapabilityUnavailableError, match="create-time"):
        project.validate(plan)


def test_live_manifest_rejects_catalog_schema_mismatch() -> None:
    class MissingItemClient(FakeSemanticEffectClient):
        def get_effect_catalog(
            self,
            *,
            start: int = 0,
            count: int = 128,
        ) -> EffectCatalogPage:
            page = super().get_effect_catalog(start=start, count=count)
            glow = page.effects[0]
            return EffectCatalogPage(
                0,
                1,
                None,
                (
                    CatalogEffect(
                        glow.name,
                        glow.type,
                        glow.type_code,
                        glow.flags,
                        glow.items[:-1],
                    ),
                ),
            )

    project = _project(MissingItemClient())
    validation = project.validate(EditPlan().add_text("x", effects=[effect("glow")]))
    assert not validation.valid
    assert "absent from the live catalog" in validation.errors[0]
