"""Simple, stateful editing facade for one open AviUtl2 project."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any, Literal

from aviutl2_api.editing import (
    AppliedEffect,
    EditPlan,
    EffectDefinition,
    EffectSpec,
    FramePosition,
    LinearMotion,
    ObjectReference,
    PlanApplyError,
    PlanCommandResult,
    PlannedPlacement,
    PlanResult,
    PlanValidation,
    PlanValidationError,
    ProjectChangedError,
    RollbackReceipt,
    Transform,
    TransformValue,
)
from aviutl2_api.editing import ObjectGroup as EditObjectGroup
from aviutl2_api.effect_profiles import (
    ResolvedEffect,
    resolve_effect,
)
from aviutl2_api.effect_profiles import (
    available_effect_profiles as _available_effect_profiles,
)
from aviutl2_api.models import (
    AnimatedValue,
    AnimationParams,
    Effect,
    StaticValue,
    TimelineObject,
)

from .alias import serialize_object_alias
from .audio import AudioAnalysis, RenderedAudio
from .client import LiveClient
from .commands import CreateFromAliasCommand, ItemUpdate, make_text_object
from .editing import (
    CapabilityUnavailableError,
    EditingSession,
    ReviewBundle,
)
from .effects import EffectApplication, EffectSemantic, EffectValue
from .frame import ContactSheet, RenderedFrame
from .inspection import EffectInspection, ObjectInspection
from .media import MediaSplit
from .protocol import BridgeRemoteError
from .qc import PreflightReport
from .snapshot import ProjectSnapshot, SnapshotObject
from .timeline import TimelineTransactionCommand


@dataclass(frozen=True, slots=True)
class LiveObject:
    """Compact revision-scoped object reference used by :class:`LiveProject`."""

    snapshot_object: SnapshotObject

    @property
    def object_id(self) -> str:
        return self.snapshot_object.object_id

    @property
    def revision(self) -> int:
        return self.snapshot_object.revision

    @property
    def layer(self) -> int:
        return self.snapshot_object.layer

    @property
    def frame_start(self) -> int:
        return self.snapshot_object.frame_start

    @property
    def frame_end(self) -> int:
        return self.snapshot_object.frame_end

    @property
    def duration(self) -> int:
        return self.snapshot_object.duration_frames

    @property
    def midpoint(self) -> int:
        return (self.frame_start + self.frame_end) // 2

    @property
    def name(self) -> str | None:
        return self.snapshot_object.name

    @property
    def api_locked(self) -> bool:
        return self.snapshot_object.api_locked


@dataclass(frozen=True, slots=True)
class ObjectSelection:
    objects: tuple[LiveObject, ...]

    def __iter__(self) -> Iterator[LiveObject]:
        return iter(self.objects)

    def __len__(self) -> int:
        return len(self.objects)

    def __getitem__(self, index: int) -> LiveObject:
        return self.objects[index]

    def one(self) -> LiveObject:
        if len(self.objects) != 1:
            raise LookupError(f"expected exactly one object, found {len(self.objects)}")
        return self.objects[0]

    def first(self) -> LiveObject | None:
        return self.objects[0] if self.objects else None


@dataclass(frozen=True, slots=True)
class _ResolvedPlan:
    snapshot: ProjectSnapshot
    wire_commands: tuple[dict[str, Any], ...]
    placements: tuple[PlannedPlacement, ...]
    keys: tuple[str, ...]
    effects: tuple[tuple[ResolvedEffect, ...], ...]


@dataclass(frozen=True, slots=True)
class _CatalogEffectSchema:
    items: Mapping[str, str]
    video: bool
    audio: bool


def _normalized_color(value: str) -> str:
    color = value.removeprefix("#").lower()
    if len(color) != 6 or any(char not in "0123456789abcdef" for char in color):
        raise ValueError("color must contain six hexadecimal RGB digits")
    return color


def _number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    return float(value)


def _track_value(
    value: TransformValue | None,
    *,
    default: float = 0.0,
    invert_opacity: bool = False,
) -> StaticValue | AnimatedValue:
    def convert(number: float) -> float:
        return (1.0 - number) * 100.0 if invert_opacity else number

    if value is None:
        return StaticValue(convert(default))
    if isinstance(value, LinearMotion):
        return AnimatedValue(
            convert(value.start),
            convert(value.end),
            AnimationParams("直線移動", "0"),
        )
    return StaticValue(convert(float(value)))


def _draw_properties(transform: Transform) -> dict[str, object]:
    return {
        "X": _track_value(transform.x),
        "Y": _track_value(transform.y),
        "Z": _track_value(transform.z),
        "Group": StaticValue(1.0),
        "中心X": StaticValue(0.0),
        "中心Y": StaticValue(0.0),
        "中心Z": StaticValue(0.0),
        "X軸回転": _track_value(transform.rotation_x),
        "Y軸回転": _track_value(transform.rotation_y),
        "Z軸回転": _track_value(transform.effective_rotation_z),
        "拡大率": _track_value(transform.scale, default=100.0),
        "縦横比": StaticValue(0.0),
        "透明度": _track_value(
            transform.opacity,
            default=1.0,
            invert_opacity=True,
        ),
        "合成モード": "通常",
    }


def _transform_updates(
    transform: Transform,
    *,
    effect: str = "標準描画",
) -> tuple[ItemUpdate, ...]:
    values = (
        ("X", transform.x, False),
        ("Y", transform.y, False),
        ("Z", transform.z, False),
        ("拡大率", transform.scale, False),
        ("X軸回転", transform.rotation_x, False),
        ("Y軸回転", transform.rotation_y, False),
        ("Z軸回転", transform.effective_rotation_z, False),
        ("透明度", transform.opacity, True),
    )
    return tuple(
        ItemUpdate(
            effect,
            name,
            _track_value(value, invert_opacity=invert_opacity),
        )
        for name, value, invert_opacity in values
        if value is not None
    )


def _shape_object(
    shape: str,
    *,
    layer: int,
    frame: int,
    duration: int,
    color: str,
    width: float,
    height: float,
    transform: Transform,
) -> TimelineObject:
    names = {
        "circle": "円",
        "rectangle": "四角形",
        "triangle": "三角形",
        "pentagon": "五角形",
        "hexagon": "六角形",
        "star": "星型",
        "heart": "ハート",
        "background": "背景",
    }
    if shape not in names:
        raise ValueError(
            "shape must be circle, rectangle, triangle, pentagon, hexagon, "
            "star, heart, or background"
        )
    if width <= 0.0 or height <= 0.0:
        raise ValueError("shape width/height must be positive")
    aspect = (height / width - 1.0) * 100.0
    return TimelineObject(
        object_id=0,
        layer=layer,
        frame_start=frame,
        frame_end=frame + duration - 1,
        effects=[
            Effect(
                effect_id=0,
                name="図形",
                properties={
                    "図形の種類": names[shape],
                    "サイズ": StaticValue(float(width)),
                    "縦横比": StaticValue(aspect),
                    "ライン幅": StaticValue(4000.0),
                    "色": _normalized_color(color),
                    "角を丸くする": StaticValue(0.0),
                },
            ),
            Effect(1, "標準描画", _draw_properties(transform)),
        ],
    )


class LiveProject:
    """High-level, compact wrapper around :class:`LiveClient`."""

    def __init__(self, client: LiveClient) -> None:
        self._client = client
        self._editing = EditingSession(client)
        self._methods = frozenset(self._editing.capabilities["methods"])
        self._snapshot: ProjectSnapshot | None = None
        self._project_info: dict[str, Any] | None = None
        self._effect_schemas: dict[str, tuple[frozenset[str], ...]] | None = None
        self._effect_catalog_schemas: (
            dict[str, tuple[_CatalogEffectSchema, ...]] | None
        ) = None
        self._font_names: frozenset[str] | None = None

    @property
    def client(self) -> LiveClient:
        """Return the low-level escape hatch for advanced protocol operations."""
        return self._client

    @classmethod
    def connect(
        cls,
        *,
        pid: int | None = None,
        pipe_name: str | None = None,
        timeout: float = 5.0,
    ) -> LiveProject:
        return cls(LiveClient.connect(pid=pid, pipe_name=pipe_name, timeout=timeout))

    @property
    def snapshot(self) -> ProjectSnapshot | None:
        return self._snapshot

    @staticmethod
    def _cursor_value(info: Mapping[str, object], name: str) -> int | None:
        legacy = info.get(f"cursor_{name}")
        if isinstance(legacy, int) and not isinstance(legacy, bool):
            return legacy
        cursor = info.get("cursor")
        if isinstance(cursor, Mapping):
            value = cursor.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    def refresh(self) -> ObjectSelection:
        self._snapshot = self.client.get_snapshot(include_alias=False)
        self._project_info = self.client.get_project_info()
        return ObjectSelection(
            tuple(LiveObject(value) for value in self._snapshot.objects)
        )

    def summary(self) -> dict[str, object]:
        if self._snapshot is None or self._project_info is None:
            self.refresh()
        assert self._snapshot is not None and self._project_info is not None
        return {
            "revision": self._snapshot.revision,
            "scene_id": self._snapshot.scene_id,
            "object_count": self._snapshot.total,
            "cursor_frame": self._cursor_value(self._project_info, "frame"),
            "cursor_layer": self._cursor_value(self._project_info, "layer"),
            "frame_max": self._project_info.get("frame_max"),
            "layer_max": self._project_info.get("layer_max"),
        }

    def find(
        self,
        *,
        name: str | None = None,
        text: str | None = None,
        file: str | Path | None = None,
        effect: str | None = None,
        layer: int | None = None,
        at: int | None = None,
        api_locked: bool | None = None,
    ) -> ObjectSelection:
        self.refresh()
        assert self._snapshot is not None
        candidates = [
            value
            for value in self._snapshot.objects
            if (name is None or value.name == name)
            and (layer is None or value.layer == layer)
            and (at is None or value.frame_start <= at <= value.frame_end)
            and (api_locked is None or value.api_locked is api_locked)
        ]
        file_ids: set[str] | None = None
        if file is not None:
            requested = str(Path(file).expanduser().resolve()).casefold()
            inventory = self.client.get_media_inventory()
            if inventory.revision != self._snapshot.revision:
                raise ProjectChangedError(
                    "the project changed while media inventory was read"
                )
            file_ids = {
                item.object_id
                for item in inventory.files
                if str(Path(item.file).expanduser().resolve()).casefold() == requested
            }
            candidates = [value for value in candidates if value.object_id in file_ids]
        if text is not None or effect is not None:
            inspected: list[SnapshotObject] = []
            for value in candidates:
                details = self.client.inspect_object(value)
                effect_match = effect is None or any(
                    item.name == effect or item.selector == effect
                    for item in details.effects
                )
                text_match = text is None or any(
                    item.name == "テキスト" and item.raw_value == text
                    for candidate_effect in details.effects
                    for item in candidate_effect.items
                )
                if effect_match and text_match:
                    inspected.append(value)
            candidates = inspected
        return ObjectSelection(tuple(LiveObject(value) for value in candidates))

    def preflight(self, **kwargs: Any) -> PreflightReport:
        report = self._editing.preflight(**kwargs)
        self._snapshot = report.snapshot
        self._project_info = self.client.get_project_info()
        return report

    def render(self, target: int | ObjectReference | None = None) -> RenderedFrame:
        if target is None:
            info = self.client.get_project_info()
            frame = self._cursor_value(info, "frame")
            if frame is None or frame < 0:
                raise ConnectionError("Live Bridge returned an invalid cursor frame")
        elif isinstance(target, int):
            frame = target
        else:
            frame = target.midpoint
        return self.client.render_frame(frame)

    def review(self, **kwargs: Any) -> ReviewBundle:
        return self._editing.review(**kwargs)

    def contact_sheet(
        self,
        frames: Sequence[int] | None = None,
        *,
        columns: int = 4,
        thumbnail_width: int = 320,
    ) -> ContactSheet:
        return self._editing.review(
            frames=frames,
            columns=columns,
            thumbnail_width=thumbnail_width,
        ).contact_sheet

    def render_audio(self, frame_start: int, frame_end: int) -> RenderedAudio:
        snapshot = self.client.get_snapshot(include_alias=False)
        self._snapshot = snapshot
        self._project_info = self.client.get_project_info()
        return self.client.render_audio(
            frame_start=frame_start,
            frame_end=frame_end,
            expected_revision=snapshot.revision,
        )

    def audio_review(
        self,
        frame_start: int,
        frame_end: int,
    ) -> tuple[RenderedAudio, AudioAnalysis]:
        audio = self.render_audio(frame_start, frame_end)
        return audio, audio.analyze()

    def _current(self, target: object) -> SnapshotObject:
        if isinstance(target, LiveObject):
            value = target.snapshot_object
        elif isinstance(target, SnapshotObject):
            value = target
        else:
            raise TypeError("target must be a LiveObject or SnapshotObject")
        if self._snapshot is None:
            self.refresh()
        assert self._snapshot is not None
        if value.revision != self._snapshot.revision:
            raise ProjectChangedError(
                "the object reference is stale; call find() or refresh() again"
            )
        if not any(
            item.object_id == value.object_id for item in self._snapshot.objects
        ):
            raise ProjectChangedError("the object is absent from the current snapshot")
        return value

    def _transform_effect_selector(
        self,
        target: SnapshotObject,
        transform: Transform,
    ) -> str:
        requested_items = {update.item for update in _transform_updates(transform)}
        if not requested_items:
            raise ValueError("a transform effect was requested without transform items")
        inspection = self.client.inspect_object(target)
        candidates = [
            effect.selector
            for effect in inspection.effects
            if requested_items.issubset({item.name for item in effect.items})
        ]
        if len(candidates) != 1:
            raise ValueError(
                "the object does not expose exactly one Effect containing "
                f"the requested transform items: {sorted(requested_items)!r}"
            )
        return candidates[0]

    def _layers(self, revision: int) -> tuple[set[int], int]:
        first = self.client.get_layers(start=0, count=256)
        if first.revision != revision:
            raise ProjectChangedError("the project changed while layers were read")
        locked = {layer.layer for layer in first.layers if layer.locked}
        layer_max = max(
            first.layer_max,
            max((layer.layer for layer in first.layers), default=first.layer_max),
        )
        start = first.start + len(first.layers)
        while start <= layer_max:
            page = self.client.get_layers(
                start=start,
                count=min(256, layer_max - start + 1),
            )
            if page.revision != revision:
                raise ProjectChangedError("the project changed while layers were read")
            locked.update(layer.layer for layer in page.layers if layer.locked)
            layer_max = max(
                layer_max,
                max((layer.layer for layer in page.layers), default=layer_max),
            )
            start += len(page.layers)
            if not page.layers:
                break
        return locked, layer_max

    def _effects(self) -> dict[str, tuple[frozenset[str], ...]]:
        if self._effect_schemas is not None:
            return self._effect_schemas
        detailed = self._catalog_effect_schemas()
        self._effect_schemas = {
            name: tuple(frozenset(entry.items) for entry in entries)
            for name, entries in detailed.items()
        }
        return self._effect_schemas

    def _catalog_effect_schemas(
        self,
    ) -> dict[str, tuple[_CatalogEffectSchema, ...]]:
        if self._effect_catalog_schemas is not None:
            return self._effect_catalog_schemas
        found: dict[str, list[_CatalogEffectSchema]] = {}
        start = 0
        while True:
            page = self.client.get_effect_catalog(start=start, count=128)
            for effect in page.effects:
                found.setdefault(effect.name, []).append(
                    _CatalogEffectSchema(
                        {item.name: item.type for item in effect.items},
                        effect.flags.video,
                        effect.flags.audio,
                    )
                )
            if page.next_start is None:
                break
            start = page.next_start
        self._effect_catalog_schemas = {
            name: tuple(entries) for name, entries in found.items()
        }
        return self._effect_catalog_schemas

    def _resolve_effect_stack(
        self,
        specs: Sequence[EffectDefinition],
    ) -> tuple[ResolvedEffect, ...]:
        resolved = tuple(resolve_effect(spec) for spec in specs)
        if not resolved:
            return ()
        catalog = self._catalog_effect_schemas()
        for value in resolved:
            entries = catalog.get(value.native_name, ())
            if len(entries) != 1:
                raise ValueError(
                    "effect must identify exactly one live catalog entry: "
                    f"{value.native_name!r}"
                )
            entry = entries[0]
            requested = {name for name, _ in value.items}
            missing = sorted(requested - set(entry.items))
            if missing:
                raise ValueError(
                    "effect items are absent from the live catalog: "
                    + ", ".join(missing)
                )
            if value.verified:
                unexpected = sorted(set(entry.items) - requested)
                if unexpected:
                    raise ValueError(
                        "live catalog has items absent from the compatibility "
                        "manifest: " + ", ".join(unexpected)
                    )
                mismatched = sorted(
                    name
                    for name, expected_type in value.item_types.items()
                    if entry.items.get(name) != expected_type
                )
                if mismatched:
                    raise ValueError(
                        "effect item types differ from the compatibility manifest: "
                        + ", ".join(mismatched)
                    )
            if value.scope == "video" and not entry.video:
                raise ValueError(f"{value.native_name} is not a video effect")
            if value.scope == "audio" and not entry.audio:
                raise ValueError(f"{value.native_name} is not an audio effect")
        return resolved

    @staticmethod
    def _wire_effects(
        effects: Sequence[ResolvedEffect],
    ) -> list[dict[str, Any]]:
        return [
            {
                "effect": effect.native_name,
                "profile": effect.profile,
                "scope": effect.scope,
                "enabled": effect.enabled,
                "items": [
                    ItemUpdate(effect.native_name, name, value).to_wire()
                    for name, value in effect.items
                ],
            }
            for effect in effects
        ]

    def _fonts(self) -> frozenset[str]:
        if self._font_names is not None:
            return self._font_names
        found: set[str] = set()
        start = 0
        while True:
            page = self.client.get_font_catalog(start=start, count=256)
            entries = page.get("entries")
            next_start = page.get("next_start")
            if not isinstance(entries, list) or any(
                not isinstance(value, str) for value in entries
            ):
                raise ConnectionError("Live Bridge returned an invalid font catalog")
            found.update(entries)
            if next_start is None:
                break
            if (
                not isinstance(next_start, int)
                or isinstance(next_start, bool)
                or next_start <= start
            ):
                raise ConnectionError("Live Bridge returned invalid font paging")
            start = next_start
        self._font_names = frozenset(found)
        return self._font_names

    @staticmethod
    def _occupied(
        layer: int,
        frame: int,
        duration: int,
        ranges: Sequence[tuple[int, int, int]],
    ) -> bool:
        end = frame + duration - 1
        return any(
            item_layer == layer and item_start <= end and frame <= item_end
            for item_layer, item_start, item_end in ranges
        )

    def _resolve(self, plan: EditPlan) -> _ResolvedPlan:
        if plan.consumed:
            raise RuntimeError("a successfully applied EditPlan cannot be reused")
        if not plan.commands:
            raise ValueError("an EditPlan must contain at least one command")
        self.refresh()
        assert self._snapshot is not None and self._project_info is not None
        snapshot = self._snapshot
        cursor = self._cursor_value(self._project_info, "frame")
        if cursor is None or cursor < 0:
            raise ConnectionError("Live Bridge returned an invalid cursor frame")
        locked_layers, layer_max = self._layers(snapshot.revision)
        projected = [
            (item.layer, item.frame_start, item.frame_end) for item in snapshot.objects
        ]
        virtual_ranges = {
            item.object_id: (item.layer, item.frame_start, item.frame_end)
            for item in snapshot.objects
        }
        serial_frame = cursor
        wire: list[dict[str, Any]] = []
        placements: list[PlannedPlacement] = []
        keys: list[str] = []
        effect_stacks: list[tuple[ResolvedEffect, ...]] = []
        moved_ids: set[str] = set()
        for index, command in enumerate(plan.commands):
            key = command.key or f"command-{index}"
            keys.append(key)
            if command.op in {"add_text", "add_shape", "add_media"}:
                raw_effects = command.values.get("effects", ())
                if not isinstance(raw_effects, Sequence) or isinstance(
                    raw_effects, (str, bytes)
                ):
                    raise TypeError("initial effects must be a sequence")
                if (
                    raw_effects
                    and self._editing.capabilities.get("edit_plan_create_effect_stack")
                    is not True
                ):
                    raise CapabilityUnavailableError(
                        "the running plugin does not support create-time effect stacks"
                    )
                initial_effects = self._resolve_effect_stack(raw_effects)
                at = command.values["at"]
                if at == "end":
                    frame = max((item[2] for item in projected), default=-1) + 1
                elif at is None:
                    frame = serial_frame if plan.sequence == "serial" else cursor
                else:
                    assert isinstance(at, int)
                    frame = at
                duration_value = command.values["duration"]
                media_probe = None
                if command.op == "add_media":
                    media_file = command.values["file"]
                    if not isinstance(media_file, Path):
                        raise TypeError("media file must be a path")
                    media_probe = self.client.probe_media(media_file)
                    if not media_probe.readable:
                        raise ValueError("AviUtl2 cannot read the requested media")
                    requested_kind = command.values["kind"]
                    if requested_kind != "auto" and media_probe.kind != requested_kind:
                        raise ValueError(
                            "media kind is "
                            f"{media_probe.kind!r}, not {requested_kind!r}"
                        )
                if duration_value is None:
                    if command.op == "add_media" and media_probe is not None:
                        if media_probe.kind in {"video", "audio"}:
                            if (
                                not media_probe.has_media_info
                                or media_probe.duration_seconds <= 0.0
                            ):
                                raise ValueError(
                                    "native media duration is unavailable; "
                                    "specify duration explicitly"
                                )
                            scene = self.client.get_current_scene()
                            duration = max(
                                1,
                                math.ceil(
                                    media_probe.duration_seconds
                                    * scene.rate
                                    / scene.scale
                                ),
                            )
                        else:
                            duration = 60
                    else:
                        duration = 60
                else:
                    assert isinstance(duration_value, int)
                    duration = duration_value
                requested_layer = command.values["layer"]
                if requested_layer is None:
                    layer = next(
                        (
                            candidate
                            for candidate in range(layer_max + 1)
                            if candidate not in locked_layers
                            and not self._occupied(
                                candidate,
                                frame,
                                duration,
                                projected,
                            )
                        ),
                        -1,
                    )
                    if layer < 0:
                        raise ValueError(
                            "no unlocked collision-free layer is available"
                        )
                else:
                    assert isinstance(requested_layer, int)
                    layer = requested_layer
                    if layer > layer_max:
                        raise ValueError(
                            "requested layer is outside the host layer range"
                        )
                    if layer in locked_layers:
                        raise PermissionError("requested layer is locked")
                    if self._occupied(layer, frame, duration, projected):
                        raise ValueError("requested timeline placement is occupied")
                projected.append((layer, frame, frame + duration - 1))
                if plan.sequence == "serial" and at is None:
                    serial_frame = frame + duration
                placement = PlannedPlacement(index, key, layer, frame, duration)
                placements.append(placement)
                transform = command.values["transform"]
                assert isinstance(transform, Transform)
                if (
                    command.op == "add_media"
                    and media_probe is not None
                    and media_probe.kind == "audio"
                    and not transform.empty
                ):
                    raise ValueError(
                        "visual transforms are not supported for audio-only media"
                    )
                if command.op == "add_text":
                    obj = make_text_object(
                        str(command.values["text"]),
                        layer=layer,
                        frame=frame,
                        length=duration,
                        size=_number(command.values["size"], "size"),
                        color=_normalized_color(str(command.values["color"])),
                    )
                    obj.effects[1].properties.update(_draw_properties(transform))
                    font = command.values["font"]
                    if font is not None:
                        if str(font) not in self._fonts():
                            raise ValueError(
                                f"font is absent from the live catalog: {font!r}"
                            )
                        obj.effects[0].properties["フォント"] = str(font)
                    wire.append(
                        {
                            "op": "object.create_from_alias",
                            "key": key,
                            "alias": serialize_object_alias(obj),
                            "layer": layer,
                            "frame": frame,
                            "length": duration,
                            "effects": self._wire_effects(initial_effects),
                        }
                    )
                elif command.op == "add_shape":
                    obj = _shape_object(
                        str(command.values["shape"]),
                        layer=layer,
                        frame=frame,
                        duration=duration,
                        color=str(command.values["color"]),
                        width=_number(command.values["width"], "width"),
                        height=_number(command.values["height"], "height"),
                        transform=transform,
                    )
                    wire.append(
                        {
                            "op": "object.create_from_alias",
                            "key": key,
                            "alias": serialize_object_alias(obj),
                            "layer": layer,
                            "frame": frame,
                            "length": duration,
                            "effects": self._wire_effects(initial_effects),
                        }
                    )
                else:
                    media_file = command.values["file"]
                    if not isinstance(media_file, Path):
                        raise TypeError("media file must be a path")
                    assert media_probe is not None
                    transform_effect = (
                        "映像再生" if media_probe.kind == "video" else "標準描画"
                    )
                    wire.append(
                        {
                            "op": "object.create_from_media_file",
                            "key": key,
                            "file": str(media_file.expanduser().resolve()),
                            "layer": layer,
                            "frame": frame,
                            "length": duration,
                            "items": [
                                value.to_wire()
                                for value in _transform_updates(
                                    transform,
                                    effect=transform_effect,
                                )
                            ],
                            "effects": self._wire_effects(initial_effects),
                        }
                    )
                effect_stacks.append(initial_effects)
                continue
            target = self._current(command.target)
            if target.object_id not in virtual_ranges:
                raise ValueError(
                    "a plan command targets an object deleted earlier in the plan"
                )
            target_wire = {"object_id": target.object_id}
            if command.op == "update":
                transform = command.values["transform"]
                assert isinstance(transform, Transform)
                updates: list[ItemUpdate] = []
                if not transform.empty:
                    transform_effect = self._transform_effect_selector(
                        target,
                        transform,
                    )
                    updates.extend(
                        _transform_updates(transform, effect=transform_effect)
                    )
                text = command.values["text"]
                if text is not None:
                    updates.insert(0, ItemUpdate("テキスト", "テキスト", str(text)))
                item: dict[str, Any] = {
                    "op": "object.update",
                    "key": key,
                    "target": target_wire,
                    "items": [value.to_wire() for value in updates],
                }
                name = command.values["name"]
                if name is not None:
                    item["name"] = str(name)
                wire.append(item)
            elif command.op == "move":
                if target.object_id in moved_ids:
                    raise ValueError("an object may be moved only once in one plan")
                old_range = virtual_ranges[target.object_id]
                projected.remove(old_range)
                layer_value = command.values["layer"]
                frame_value = command.values["at"]
                assert isinstance(layer_value, int)
                assert isinstance(frame_value, int)
                if layer_value > layer_max:
                    raise ValueError("requested layer is outside the host layer range")
                if layer_value in locked_layers:
                    raise PermissionError("requested layer is locked")
                if self._occupied(
                    layer_value,
                    frame_value,
                    target.duration_frames,
                    projected,
                ):
                    projected.append(old_range)
                    raise ValueError("requested timeline placement is occupied")
                new_range = (
                    layer_value,
                    frame_value,
                    frame_value + target.duration_frames - 1,
                )
                projected.append(new_range)
                virtual_ranges[target.object_id] = new_range
                moved_ids.add(target.object_id)
                wire.append(
                    {
                        "op": "object.move",
                        "key": key,
                        "target": target_wire,
                        "layer": command.values["layer"],
                        "frame": command.values["at"],
                    }
                )
            elif command.op == "delete":
                if target.object_id in moved_ids:
                    raise ValueError(
                        "an object cannot be moved and deleted in the same plan"
                    )
                old_range = virtual_ranges.pop(target.object_id)
                projected.remove(old_range)
                wire.append({"op": "object.delete", "key": key, "target": target_wire})
            elif command.op == "add_effect":
                effect_items = command.values["items"]
                if not isinstance(effect_items, Mapping):
                    raise TypeError("effect items must be a mapping")
                effect_name = str(command.values["effect"])
                schemas = self._effects().get(effect_name, ())
                if len(schemas) != 1:
                    raise ValueError(
                        "effect must identify exactly one live catalog entry: "
                        f"{effect_name!r}"
                    )
                missing_items = sorted(set(effect_items) - schemas[0])
                if missing_items:
                    raise ValueError(
                        "effect items are absent from the live catalog: "
                        + ", ".join(str(value) for value in missing_items)
                    )
                wire.append(
                    {
                        "op": "object.effect.add",
                        "key": key,
                        "target": target_wire,
                        "effect": effect_name,
                        "items": [
                            ItemUpdate(
                                effect_name,
                                str(name),
                                value,
                            ).to_wire()
                            for name, value in effect_items.items()
                        ],
                        "enabled": command.values["enabled"],
                    }
                )
            elif command.op == "set_effect_enabled":
                wire.append(
                    {
                        "op": "object.effect.set_enabled",
                        "key": key,
                        "target": target_wire,
                        "selector": command.values["selector"],
                        "enabled": command.values["enabled"],
                    }
                )
            else:
                raise ValueError(f"unsupported edit-plan command: {command.op}")
            effect_stacks.append(())
        return _ResolvedPlan(
            snapshot,
            tuple(wire),
            tuple(placements),
            tuple(keys),
            tuple(effect_stacks),
        )

    def _validate_resolved(self, resolved: _ResolvedPlan) -> PlanValidation:
        if "edit.plan.validate" in self._methods:
            result = self.client.validate_edit_plan(
                expected_revision=resolved.snapshot.revision,
                commands=resolved.wire_commands,
            )
            valid = result.get("valid") is True
            warnings = tuple(
                value for value in result.get("warnings", []) if isinstance(value, str)
            )
            errors = () if valid else ("native plan validation failed",)
            return PlanValidation(
                valid,
                resolved.snapshot.revision,
                resolved.placements,
                warnings,
                errors,
            )
        self._validate_fallback(resolved)
        return PlanValidation(
            True,
            resolved.snapshot.revision,
            resolved.placements,
            ("LEGACY_PLUGIN_FALLBACK",),
        )

    def validate(self, plan: EditPlan) -> PlanValidation:
        try:
            return self._validate_resolved(self._resolve(plan))
        except (
            BridgeRemoteError,
            ConnectionError,
            PermissionError,
            ValueError,
        ) as error:
            revision = self._snapshot.revision if self._snapshot is not None else 0
            return PlanValidation(False, revision, errors=(str(error),))

    def _validate_fallback(self, resolved: _ResolvedPlan) -> None:
        operations = {value["op"] for value in resolved.wire_commands}
        if operations == {"object.create_from_alias"}:
            self.client.validate_batch(
                [
                    CreateFromAliasCommand(
                        alias=value["alias"],
                        layer=value["layer"],
                        frame=value["frame"],
                        length=value["length"],
                        client_id=value["key"],
                    )
                    for value in resolved.wire_commands
                ]
            )
            return
        timeline = self._timeline_commands(resolved.wire_commands)
        if timeline is not None:
            receipt = self.client.validate_transaction(
                expected_revision=resolved.snapshot.revision,
                commands=timeline,
            )
            if not receipt.valid:
                raise ValueError("legacy timeline validation failed")
            return
        if len(resolved.wire_commands) == 1:
            command = resolved.wire_commands[0]
            operation = command["op"]
            if operation == "object.effect.add":
                return
            if operation == "object.create_from_media_file" and not command.get(
                "items"
            ):
                return
            raise CapabilityUnavailableError(
                "the running plugin cannot apply this EditPlan"
            )
        raise CapabilityUnavailableError(
            "the running plugin cannot apply this mixed EditPlan"
        )

    def apply(self, plan: EditPlan) -> PlanResult:
        resolved = self._resolve(plan)
        try:
            validation = self._validate_resolved(resolved)
        except BridgeRemoteError as error:
            if error.code == "STALE_PROJECT_STATE":
                raise ProjectChangedError(error.message) from error
            validation = PlanValidation(
                False,
                resolved.snapshot.revision,
                resolved.placements,
                errors=(str(error),),
            )
        except (
            ConnectionError,
            PermissionError,
            ValueError,
        ) as error:
            validation = PlanValidation(
                False,
                resolved.snapshot.revision,
                resolved.placements,
                errors=(str(error),),
            )
        if not validation.valid:
            raise PlanValidationError(validation)
        if (
            self._snapshot is None
            or self._snapshot.revision != resolved.snapshot.revision
        ):
            raise ProjectChangedError("the project changed while the plan was prepared")
        before = resolved.snapshot
        raw: Mapping[str, Any]
        try:
            if "edit.plan.apply" in self._methods:
                raw = self.client.apply_edit_plan(
                    expected_revision=before.revision,
                    commands=resolved.wire_commands,
                )
            else:
                raw = self._apply_fallback(resolved)
        except BridgeRemoteError as error:
            if error.code == "STALE_PROJECT_STATE":
                raise ProjectChangedError(error.message) from error
            raise PlanApplyError(
                str(error),
                result=self._failed_result(resolved, error),
            ) from error
        plan._mark_consumed()
        return self._result_from_apply(resolved, raw)

    @staticmethod
    def _rollback_from_wire(value: object) -> RollbackReceipt:
        if not isinstance(value, Mapping):
            return RollbackReceipt()
        warnings_value = value.get("warnings", [])
        warnings = (
            tuple(item for item in warnings_value if isinstance(item, str))
            if isinstance(warnings_value, Sequence)
            and not isinstance(warnings_value, (str, bytes))
            else ()
        )
        return RollbackReceipt(
            attempted=value.get("attempted") is True,
            complete=value.get("complete") is not False,
            restored_count=(
                int(value["restored_count"])
                if isinstance(value.get("restored_count"), int)
                and not isinstance(value.get("restored_count"), bool)
                else 0
            ),
            gui_undo_required=value.get("gui_undo_required") is True,
            warnings=warnings,
        )

    def _failed_result(
        self,
        resolved: _ResolvedPlan,
        error: BridgeRemoteError,
    ) -> PlanResult:
        failed_value = error.details.get("failed_command_index")
        failed_index = (
            failed_value
            if isinstance(failed_value, int)
            and not isinstance(failed_value, bool)
            and 0 <= failed_value < len(resolved.wire_commands)
            else None
        )
        rollback = self._rollback_from_wire(error.details.get("rollback"))
        commands: list[PlanCommandResult] = []
        for index, key in enumerate(resolved.keys):
            if failed_index is None:
                status = "unknown"
            elif index < failed_index:
                status = "rolled_back" if rollback.complete else "applied"
            elif index == failed_index:
                status = "failed"
            else:
                status = "skipped"
            commands.append(PlanCommandResult(index, key, status))
        revision_value = error.details.get("current_revision")
        revision = (
            revision_value
            if isinstance(revision_value, int)
            and not isinstance(revision_value, bool)
            and revision_value > 0
            else resolved.snapshot.revision
        )
        return PlanResult(
            revision_before=resolved.snapshot.revision,
            revision=revision,
            applied_count=failed_index or 0,
            undo_grouped=False,
            atomic=False,
            commands=tuple(commands),
            objects={},
            rollback=rollback,
            warnings=(error.code,),
        )

    @staticmethod
    def _timeline_commands(
        commands: Sequence[Mapping[str, Any]],
    ) -> list[TimelineTransactionCommand] | None:
        result: list[TimelineTransactionCommand] = []
        for value in commands:
            target = value.get("target")
            if not isinstance(target, dict):
                return None
            object_id = target.get("object_id")
            if not isinstance(object_id, str):
                return None
            revision_text = object_id.split("-")[1]
            snapshot = SnapshotObject(
                object_id,
                int(revision_text),
                0,
                0,
                0,
                None,
                None,
            )
            op = value["op"]
            if op == "object.update":
                items = value.get("items", [])
                if items:
                    result.append(
                        TimelineTransactionCommand(
                            {
                                "op": "set_items",
                                "target": {"object_id": object_id},
                                "items": list(items),
                            }
                        )
                    )
                if "name" in value:
                    result.append(
                        TimelineTransactionCommand.set_name(snapshot, value["name"])
                    )
            elif op == "object.move":
                result.append(
                    TimelineTransactionCommand(
                        {
                            "op": "move",
                            "target": {"object_id": object_id},
                            "layer": value["layer"],
                            "frame": value["frame"],
                        }
                    )
                )
            elif op == "object.delete":
                result.append(
                    TimelineTransactionCommand(
                        {"op": "delete", "target": {"object_id": object_id}}
                    )
                )
            elif op == "object.effect.set_enabled":
                result.append(
                    TimelineTransactionCommand(
                        {
                            "op": "effect.set_enabled",
                            "target": {"object_id": object_id},
                            "selector": value["selector"],
                            "enabled": value["enabled"],
                        }
                    )
                )
            else:
                return None
        return result or None

    def _apply_fallback(self, resolved: _ResolvedPlan) -> Mapping[str, Any]:
        commands = resolved.wire_commands
        operations = {value["op"] for value in commands}
        warnings = ("LEGACY_PLUGIN_FALLBACK",)
        if operations == {"object.create_from_alias"}:
            self.client.apply_batch(
                [
                    CreateFromAliasCommand(
                        alias=value["alias"],
                        layer=value["layer"],
                        frame=value["frame"],
                        length=value["length"],
                        client_id=value["key"],
                    )
                    for value in commands
                ]
            )
        else:
            timeline = self._timeline_commands(commands)
            if timeline is not None:
                self.client.apply_transaction(
                    expected_revision=resolved.snapshot.revision,
                    commands=timeline,
                )
            elif len(commands) == 1:
                value = commands[0]
                op = value["op"]
                if op == "object.create_from_media_file" and not value["items"]:
                    self.client.create_from_media_file(
                        value["file"],
                        layer=value["layer"],
                        frame=value["frame"],
                        length=value["length"],
                    )
                elif op == "object.effect.add":
                    target = self._target_by_id(value["target"]["object_id"])
                    self.client.add_effect(
                        target,
                        value["effect"],
                        items={item["item"]: item["value"] for item in value["items"]}
                        or None,
                    )
                else:
                    raise CapabilityUnavailableError(
                        "the running plugin cannot apply this EditPlan"
                    )
            else:
                raise CapabilityUnavailableError(
                    "the running plugin cannot apply this mixed EditPlan"
                )
        return {
            "applied_count": len(commands),
            "undo_grouped": True,
            "atomic": False,
            "warnings": list(warnings),
        }

    def _target_by_id(self, object_id: str) -> SnapshotObject:
        assert self._snapshot is not None
        matches = [
            value for value in self._snapshot.objects if value.object_id == object_id
        ]
        if len(matches) != 1:
            raise ProjectChangedError("the target object cannot be resolved")
        return matches[0]

    @staticmethod
    def _signature(value: SnapshotObject) -> tuple[int, int, int, str | None]:
        return value.layer, value.frame_start, value.frame_end, value.name

    def _effect_receipts(
        self,
        resolved: _ResolvedPlan,
        groups: Mapping[str, EditObjectGroup],
        revision: int,
    ) -> dict[str, tuple[AppliedEffect, ...]]:
        receipts: dict[str, tuple[AppliedEffect, ...]] = {}
        for index, stack in enumerate(resolved.effects):
            if not stack:
                continue
            key = resolved.keys[index]
            group = groups.get(key)
            if group is None:
                continue
            inspections: list[tuple[LiveObject, ObjectInspection]] = []
            for reference in group.objects:
                if not isinstance(reference, LiveObject):
                    continue
                inspections.append(
                    (
                        reference,
                        self.client.inspect_object(reference.snapshot_object),
                    )
                )
            used: set[tuple[str, str]] = set()
            applied: list[AppliedEffect] = []
            for requested in stack:
                candidates = inspections
                if requested.scope != "audio" and inspections:
                    candidates = inspections[:1] + inspections[1:]
                selected: tuple[LiveObject, EffectInspection] | None = None
                for reference, inspection in candidates:
                    for inspected in inspection.effects:
                        marker = (reference.object_id, inspected.selector)
                        if (
                            inspected.name == requested.native_name
                            and marker not in used
                        ):
                            selected = reference, inspected
                            break
                    if selected is not None:
                        break
                if selected is None:
                    message = (
                        "created effect could not be inspected: "
                        f"{requested.native_name}"
                    )
                    raise PlanApplyError(message)
                reference, inspected = selected
                used.add((reference.object_id, inspected.selector))
                applied.append(
                    AppliedEffect(
                        requested.profile,
                        requested.native_name,
                        inspected.selector,
                        inspected.index,
                        inspected.enabled,
                        {item.name: item.raw_value or "" for item in inspected.items},
                        reference.object_id,
                        revision,
                    )
                )
            receipts[key] = tuple(applied)
        return receipts

    def _result_from_apply(
        self,
        resolved: _ResolvedPlan,
        raw: Mapping[str, Any],
    ) -> PlanResult:
        before = resolved.snapshot
        after = self.client.get_snapshot(include_alias=False)
        reported_revision = raw.get("revision")
        if (
            isinstance(reported_revision, int)
            and not isinstance(reported_revision, bool)
            and reported_revision > 0
            and after.revision != reported_revision
        ):
            raise ProjectChangedError(
                "the project changed before the post-plan snapshot was read"
            )
        self._snapshot = after
        self._project_info = self.client.get_project_info()
        before_counts = Counter(self._signature(value) for value in before.objects)
        created: list[SnapshotObject] = []
        for value in after.objects:
            signature = self._signature(value)
            if before_counts[signature] > 0:
                before_counts[signature] -= 1
            else:
                created.append(value)
        groups: dict[str, EditObjectGroup] = {}
        placements_by_index = {
            value.command_index: value for value in resolved.placements
        }
        primary_by_index: dict[int, SnapshotObject] = {}
        for index, placement in placements_by_index.items():
            primary = next(
                (
                    value
                    for value in created
                    if value.layer == placement.layer
                    and value.frame_start == placement.frame
                    and value.frame_end == placement.frame + placement.duration - 1
                ),
                None,
            )
            if primary is not None:
                primary_by_index[index] = primary
        reserved_primary_ids = {value.object_id for value in primary_by_index.values()}
        assigned_created_ids: set[str] = set()
        for index, command in enumerate(resolved.wire_commands):
            key = resolved.keys[index]
            current_placement = placements_by_index.get(index)
            matches: list[SnapshotObject] = []
            if current_placement is not None:
                primary = primary_by_index.get(index)
                if primary is not None:
                    matches = [primary]
                    if command["op"] == "object.create_from_media_file":
                        matches.extend(
                            value
                            for value in created
                            if value not in matches
                            and value.object_id not in assigned_created_ids
                            and value.object_id not in reserved_primary_ids
                            and value.frame_start == current_placement.frame
                            and value.frame_end
                            == current_placement.frame + current_placement.duration - 1
                        )
            elif command["op"] != "object.delete":
                target = command.get("target")
                if isinstance(target, dict):
                    old_id = target.get("object_id")
                    old = next(
                        (
                            value
                            for value in before.objects
                            if value.object_id == old_id
                        ),
                        None,
                    )
                    if old is not None:
                        if command["op"] == "object.move":
                            layer = command["layer"]
                            start = command["frame"]
                            end = start + old.duration_frames - 1
                        else:
                            layer, start, end = (
                                old.layer,
                                old.frame_start,
                                old.frame_end,
                            )
                        matches = [
                            value
                            for value in after.objects
                            if value.layer == layer
                            and value.frame_start == start
                            and value.frame_end == end
                        ]
            if matches:
                assigned_created_ids.update(value.object_id for value in matches)
                groups[key] = EditObjectGroup(
                    tuple(LiveObject(value) for value in matches)
                )
        raw_warnings = raw.get("warnings", [])
        warnings = tuple(value for value in raw_warnings if isinstance(value, str))
        raw_command_results = raw.get("commands")
        parsed_command_results: list[PlanCommandResult] = []
        if isinstance(raw_command_results, Sequence) and not isinstance(
            raw_command_results, (str, bytes)
        ):
            for value in raw_command_results:
                if not isinstance(value, Mapping):
                    continue
                raw_index = value.get("command_index")
                status = value.get("status")
                if (
                    isinstance(raw_index, int)
                    and not isinstance(raw_index, bool)
                    and 0 <= raw_index < len(resolved.keys)
                    and isinstance(status, str)
                ):
                    parsed_command_results.append(
                        PlanCommandResult(
                            raw_index,
                            resolved.keys[raw_index],
                            status,
                        )
                    )
        command_results = tuple(parsed_command_results) or tuple(
            PlanCommandResult(index, resolved.keys[index], "applied")
            for index in range(len(resolved.wire_commands))
        )
        effect_receipts = self._effect_receipts(resolved, groups, after.revision)
        return PlanResult(
            revision_before=before.revision,
            revision=after.revision,
            applied_count=int(raw.get("applied_count", len(resolved.wire_commands))),
            undo_grouped=raw.get("undo_grouped") is True,
            atomic=raw.get("atomic") is True,
            commands=command_results,
            objects=groups,
            effects=effect_receipts,
            rollback=self._rollback_from_wire(raw.get("rollback")),
            warnings=warnings,
        )

    def _single_group(self, plan: EditPlan) -> EditObjectGroup:
        result = self.apply(plan)
        group = next(iter(result.objects.values()), None)
        if group is None:
            raise PlanApplyError(
                "the host did not return an updated object", result=result
            )
        return group

    def add_text(
        self,
        text: str,
        *,
        key: str | None = None,
        at: FramePosition = None,
        layer: int | None = None,
        duration: int | None = None,
        x: TransformValue | None = None,
        y: TransformValue | None = None,
        z: TransformValue | None = None,
        scale: TransformValue | None = None,
        rotation: TransformValue | None = None,
        rotation_x: TransformValue | None = None,
        rotation_y: TransformValue | None = None,
        rotation_z: TransformValue | None = None,
        opacity: TransformValue | None = None,
        size: float = 34.0,
        color: str = "ffffff",
        font: str | None = None,
        effects: Sequence[EffectDefinition] | None = None,
    ) -> EditObjectGroup:
        return self._single_group(
            EditPlan().add_text(
                text,
                key=key,
                at=at,
                layer=layer,
                duration=duration,
                x=x,
                y=y,
                z=z,
                scale=scale,
                rotation=rotation,
                rotation_x=rotation_x,
                rotation_y=rotation_y,
                rotation_z=rotation_z,
                opacity=opacity,
                size=size,
                color=color,
                font=font,
                effects=list(effects) if effects is not None else None,
            )
        )

    def add_media(
        self,
        file: str | PathLike[str],
        *,
        kind: Literal["auto", "image", "video", "audio"] = "auto",
        key: str | None = None,
        at: FramePosition = None,
        layer: int | None = None,
        duration: int | None = None,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        scale: float | None = None,
        rotation: float | None = None,
        rotation_x: float | None = None,
        rotation_y: float | None = None,
        rotation_z: float | None = None,
        opacity: float | None = None,
        effects: Sequence[EffectDefinition] | None = None,
    ) -> EditObjectGroup:
        return self._single_group(
            EditPlan().add_media(
                file,
                kind=kind,
                key=key,
                at=at,
                layer=layer,
                duration=duration,
                x=x,
                y=y,
                z=z,
                scale=scale,
                rotation=rotation,
                rotation_x=rotation_x,
                rotation_y=rotation_y,
                rotation_z=rotation_z,
                opacity=opacity,
                effects=list(effects) if effects is not None else None,
            )
        )

    def _add_typed_media(
        self,
        file: str | PathLike[str],
        kind: Literal["image", "video", "audio"],
        *,
        key: str | None,
        at: FramePosition,
        layer: int | None,
        duration: int | None,
        x: float | None,
        y: float | None,
        z: float | None,
        scale: float | None,
        rotation: float | None,
        rotation_x: float | None,
        rotation_y: float | None,
        rotation_z: float | None,
        opacity: float | None,
        effects: Sequence[EffectDefinition] | None,
    ) -> EditObjectGroup:
        return self.add_media(
            file,
            kind=kind,
            key=key,
            at=at,
            layer=layer,
            duration=duration,
            x=x,
            y=y,
            z=z,
            scale=scale,
            rotation=rotation,
            rotation_x=rotation_x,
            rotation_y=rotation_y,
            rotation_z=rotation_z,
            opacity=opacity,
            effects=effects,
        )

    def add_image(
        self,
        file: str | PathLike[str],
        *,
        key: str | None = None,
        at: FramePosition = None,
        layer: int | None = None,
        duration: int | None = None,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        scale: float | None = None,
        rotation: float | None = None,
        rotation_x: float | None = None,
        rotation_y: float | None = None,
        rotation_z: float | None = None,
        opacity: float | None = None,
        effects: Sequence[EffectDefinition] | None = None,
    ) -> EditObjectGroup:
        return self._add_typed_media(
            file,
            "image",
            key=key,
            at=at,
            layer=layer,
            duration=duration,
            x=x,
            y=y,
            z=z,
            scale=scale,
            rotation=rotation,
            rotation_x=rotation_x,
            rotation_y=rotation_y,
            rotation_z=rotation_z,
            opacity=opacity,
            effects=effects,
        )

    def add_video(
        self,
        file: str | PathLike[str],
        *,
        key: str | None = None,
        at: FramePosition = None,
        layer: int | None = None,
        duration: int | None = None,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        scale: float | None = None,
        rotation: float | None = None,
        rotation_x: float | None = None,
        rotation_y: float | None = None,
        rotation_z: float | None = None,
        opacity: float | None = None,
        effects: Sequence[EffectDefinition] | None = None,
    ) -> EditObjectGroup:
        return self._add_typed_media(
            file,
            "video",
            key=key,
            at=at,
            layer=layer,
            duration=duration,
            x=x,
            y=y,
            z=z,
            scale=scale,
            rotation=rotation,
            rotation_x=rotation_x,
            rotation_y=rotation_y,
            rotation_z=rotation_z,
            opacity=opacity,
            effects=effects,
        )

    def add_audio(
        self,
        file: str | PathLike[str],
        *,
        key: str | None = None,
        at: FramePosition = None,
        layer: int | None = None,
        duration: int | None = None,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        scale: float | None = None,
        rotation: float | None = None,
        rotation_x: float | None = None,
        rotation_y: float | None = None,
        rotation_z: float | None = None,
        opacity: float | None = None,
        effects: Sequence[EffectDefinition] | None = None,
    ) -> EditObjectGroup:
        return self._add_typed_media(
            file,
            "audio",
            key=key,
            at=at,
            layer=layer,
            duration=duration,
            x=x,
            y=y,
            z=z,
            scale=scale,
            rotation=rotation,
            rotation_x=rotation_x,
            rotation_y=rotation_y,
            rotation_z=rotation_z,
            opacity=opacity,
            effects=effects,
        )

    def add_shape(
        self,
        shape: str,
        *,
        key: str | None = None,
        at: FramePosition = None,
        layer: int | None = None,
        duration: int | None = None,
        color: str = "ffffff",
        width: float = 200.0,
        height: float = 200.0,
        x: TransformValue | None = None,
        y: TransformValue | None = None,
        z: TransformValue | None = None,
        scale: TransformValue | None = None,
        rotation: TransformValue | None = None,
        rotation_x: TransformValue | None = None,
        rotation_y: TransformValue | None = None,
        rotation_z: TransformValue | None = None,
        opacity: TransformValue | None = None,
        effects: Sequence[EffectDefinition] | None = None,
    ) -> EditObjectGroup:
        return self._single_group(
            EditPlan().add_shape(
                shape,
                key=key,
                at=at,
                layer=layer,
                duration=duration,
                color=color,
                width=width,
                height=height,
                x=x,
                y=y,
                z=z,
                scale=scale,
                rotation=rotation,
                rotation_x=rotation_x,
                rotation_y=rotation_y,
                rotation_z=rotation_z,
                opacity=opacity,
                effects=list(effects) if effects is not None else None,
            )
        )

    def update(
        self,
        target: object,
        *,
        key: str | None = None,
        text: str | None = None,
        name: str | None = None,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        scale: float | None = None,
        rotation: float | None = None,
        rotation_x: float | None = None,
        rotation_y: float | None = None,
        rotation_z: float | None = None,
        opacity: float | None = None,
    ) -> EditObjectGroup:
        return self._single_group(
            EditPlan().update(
                target,
                key=key,
                text=text,
                name=name,
                x=x,
                y=y,
                z=z,
                scale=scale,
                rotation=rotation,
                rotation_x=rotation_x,
                rotation_y=rotation_y,
                rotation_z=rotation_z,
                opacity=opacity,
            )
        )

    def move(
        self,
        target: object,
        *,
        at: int,
        layer: int,
    ) -> EditObjectGroup:
        return self._single_group(EditPlan().move(target, at=at, layer=layer))

    def delete(self, target: object) -> PlanResult:
        return self.apply(EditPlan().delete(target))

    def add_effect(
        self,
        target: object,
        effect: str,
        *,
        values: Mapping[str, object] | None = None,
    ) -> EditObjectGroup:
        return self._single_group(EditPlan().add_effect(target, effect, values=values))

    def available_effect_profiles(self) -> tuple[str, ...]:
        """Return curated profiles whose exact schema exists in this host."""

        available: list[str] = []
        for profile in _available_effect_profiles():
            try:
                self._resolve_effect_stack((EffectSpec(profile, {}),))
            except (TypeError, ValueError):
                continue
            available.append(profile)
        return tuple(available)

    def apply_effect(
        self,
        target: object,
        spec: EffectDefinition,
    ) -> AppliedEffect:
        """Apply one semantic/native effect and return its fresh selector."""

        resolved = self._resolve_effect_stack((spec,))[0]
        if (
            not resolved.enabled
            and self._editing.capabilities.get("semantic_effect_profiles") is not True
        ):
            raise CapabilityUnavailableError(
                "the running plugin cannot create an initially disabled effect"
            )
        group = self._single_group(
            EditPlan().add_effect(
                target,
                resolved.native_name,
                values=dict(resolved.items),
                enabled=resolved.enabled,
            )
        )
        reference = group.primary
        if not isinstance(reference, LiveObject):
            raise PlanApplyError("the host did not return a live effect target")
        inspection = self.client.inspect_object(reference.snapshot_object)
        matches = [
            value for value in inspection.effects if value.name == resolved.native_name
        ]
        if not matches:
            raise PlanApplyError("the created effect could not be inspected")
        selected = matches[-1]
        return AppliedEffect(
            resolved.profile,
            resolved.native_name,
            selected.selector,
            selected.index,
            selected.enabled,
            {item.name: item.raw_value or "" for item in selected.items},
            reference.object_id,
            reference.revision,
        )

    def update_effect(
        self,
        target: object,
        applied: AppliedEffect,
        spec: EffectDefinition,
    ) -> AppliedEffect:
        """Update one previously-receipted effect in a grouped transaction."""

        self.refresh()
        current = self._current(target)
        if (
            applied.object_id != current.object_id
            or applied.revision != current.revision
        ):
            raise ProjectChangedError("the applied effect receipt is stale")
        resolved = self._resolve_effect_stack((spec,))[0]
        if resolved.native_name != applied.native_name:
            raise ValueError("update_effect cannot replace an effect with another type")
        inspection = self.client.inspect_object(current)
        selected = self._select_effect(inspection, applied.selector)
        if selected.locked:
            raise PermissionError("the selected effect is locked")
        commands = [
            TimelineTransactionCommand.set_items(
                current,
                tuple(
                    ItemUpdate(selected.selector, name, value)
                    for name, value in resolved.items
                ),
            )
        ]
        if selected.enabled != resolved.enabled:
            commands.append(
                TimelineTransactionCommand.set_effect_enabled(
                    current,
                    selected.selector,
                    resolved.enabled,
                )
            )
        self.client.apply_transaction(
            expected_revision=current.revision,
            commands=commands,
        )
        snapshot = self.refresh()
        updated = next(
            (
                value
                for value in snapshot.objects
                if value.object_id == current.object_id
            ),
            None,
        )
        if updated is None:
            raise PlanApplyError("the updated effect target disappeared")
        updated_inspection = self.client.inspect_object(updated.snapshot_object)
        refreshed = self._select_effect(updated_inspection, selected.selector)
        return AppliedEffect(
            resolved.profile,
            resolved.native_name,
            refreshed.selector,
            refreshed.index,
            refreshed.enabled,
            {item.name: item.raw_value or "" for item in refreshed.items},
            updated.object_id,
            updated.revision,
        )

    @staticmethod
    def _select_effect(
        inspection: ObjectInspection,
        reference: str,
    ) -> EffectInspection:
        exact = [
            effect for effect in inspection.effects if effect.selector == reference
        ]
        if len(exact) == 1:
            return exact[0]
        named = [effect for effect in inspection.effects if effect.name == reference]
        if len(named) != 1:
            raise ValueError(
                "effect must identify exactly one inspected name or selector"
            )
        return named[0]

    def set_effect_values(
        self,
        target: object,
        effect: str,
        values: Mapping[str, EffectValue],
    ) -> ObjectSelection:
        if not values:
            raise ValueError("at least one effect value is required")
        self.refresh()
        current = self._current(target)
        inspection = self.client.inspect_object(current)
        selected = self._select_effect(inspection, effect)
        if selected.locked:
            raise PermissionError("the selected effect is locked")
        available = {item.name for item in selected.items}
        missing = sorted(set(values) - available)
        if missing:
            raise ValueError(
                "effect items are absent from inspection: " + ", ".join(missing)
            )
        self.client.set_items(
            current,
            tuple(
                ItemUpdate(selected.selector, name, value)
                for name, value in values.items()
            ),
        )
        return self.find(layer=current.layer, at=current.frame_start)

    def apply_common_effect(
        self,
        target: object,
        semantic: EffectSemantic,
        values: Mapping[str, EffectValue],
        *,
        effect_name: str | None = None,
    ) -> tuple[EffectApplication, ObjectSelection]:
        self.refresh()
        current = self._current(target)
        application = self.client.apply_common_effect(
            current,
            semantic,
            values,
            effect_name=effect_name,
        )
        return application, self.find(layer=current.layer, at=current.frame_start)

    def set_effect_enabled(
        self,
        target: object,
        effect: str,
        enabled: bool,
    ) -> EditObjectGroup:
        self.refresh()
        current = self._current(target)
        selected = self._select_effect(
            self.client.inspect_object(current),
            effect,
        )
        return self._single_group(
            EditPlan().set_effect_enabled(current, selected.selector, enabled)
        )

    def split(self, target: object, *, frame: int) -> MediaSplit:
        self.refresh()
        return self.client.split_media(self._current(target), frame)

    def trim(
        self,
        target: object,
        *,
        frame_start: int,
        frame_end: int,
        source_position: float | None = None,
    ) -> ObjectSelection:
        self.refresh()
        self.client.trim_media(
            self._current(target),
            frame_start=frame_start,
            frame_end=frame_end,
            source_position=source_position,
        )
        return self.find(layer=self._current(target).layer, at=frame_start)

    def set_duration(self, target: object, duration: int) -> ObjectSelection:
        self.refresh()
        current = self._current(target)
        self.client.set_duration(current, duration)
        return self.find(layer=current.layer, at=current.frame_start)

    def delete_effect(self, target: object, effect: str) -> ObjectSelection:
        self.refresh()
        current = self._current(target)
        selected = self._select_effect(
            self.client.inspect_object(current),
            effect,
        )
        self.client.delete_effect(current, selected.selector)
        return self.find(layer=current.layer, at=current.frame_start)

    def reorder_effects(
        self,
        target: object,
        effects: Sequence[str],
    ) -> ObjectSelection:
        self.refresh()
        current = self._current(target)
        inspection = self.client.inspect_object(current)
        selectors = tuple(
            self._select_effect(inspection, effect).selector for effect in effects
        )
        if len(set(selectors)) != len(selectors):
            raise ValueError("effect reorder entries must identify unique effects")
        self.client.reorder_effects(current, selectors)
        return self.find(layer=current.layer, at=current.frame_start)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> LiveProject:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


__all__ = ["LiveObject", "LiveProject", "ObjectSelection"]
