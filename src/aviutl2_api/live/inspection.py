"""Typed structured inspection of native AviUtl2 objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocol import ProtocolError


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class TrackInspection:
    mode: str | None
    parameters: tuple[float, ...]
    sampled_value: float | None
    accelerate: bool
    decelerate: bool
    ignore_midpoints: bool
    time_control: bool
    group_count: int
    group_index: int
    group_name: str | None
    group_items: tuple[str, ...] = ()

    @classmethod
    def from_wire(cls, value: object) -> TrackInspection | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ProtocolError("Live Bridge returned invalid track information")
        mode = value.get("mode")
        parameters = value.get("parameters")
        sampled = value.get("sampled_value")
        group_name = value.get("group_name")
        group_items = value.get("group_items", [])
        boolean_fields = (
            "accelerate",
            "decelerate",
            "ignore_midpoints",
            "time_control",
        )
        if (
            (mode is not None and not isinstance(mode, str))
            or not isinstance(parameters, list)
            or any(
                not isinstance(item, (int, float)) or isinstance(item, bool)
                for item in parameters
            )
            or (
                sampled is not None
                and (not isinstance(sampled, (int, float)) or isinstance(sampled, bool))
            )
            or (group_name is not None and not isinstance(group_name, str))
            or not isinstance(group_items, list)
            or any(not isinstance(item, str) for item in group_items)
            or not _integer(value.get("group_count"))
            or not _integer(value.get("group_index"))
            or any(not isinstance(value.get(name), bool) for name in boolean_fields)
        ):
            raise ProtocolError("Live Bridge returned invalid track information")
        return cls(
            mode=mode,
            parameters=tuple(float(item) for item in parameters),
            sampled_value=None if sampled is None else float(sampled),
            accelerate=value["accelerate"],
            decelerate=value["decelerate"],
            ignore_midpoints=value["ignore_midpoints"],
            time_control=value["time_control"],
            group_count=value["group_count"],
            group_index=value["group_index"],
            group_name=group_name,
            group_items=tuple(group_items),
        )


@dataclass(frozen=True, slots=True)
class ItemInspection:
    name: str
    type: str
    type_code: int
    raw_value: str | None
    track: TrackInspection | None
    sampled_check_value: bool | None = None

    @classmethod
    def from_wire(cls, value: object) -> ItemInspection:
        if not isinstance(value, dict):
            raise ProtocolError("Live Bridge returned an invalid effect item")
        name = value.get("name")
        item_type = value.get("type")
        type_code = value.get("type_code")
        raw_value = value.get("raw_value")
        sampled_check_value = value.get("sampled_check_value")
        if (
            not isinstance(name, str)
            or not isinstance(item_type, str)
            or not _integer(type_code)
            or (raw_value is not None and not isinstance(raw_value, str))
            or (
                sampled_check_value is not None
                and not isinstance(sampled_check_value, bool)
            )
        ):
            raise ProtocolError("Live Bridge returned an invalid effect item")
        assert isinstance(type_code, int)
        return cls(
            name=name,
            type=item_type,
            type_code=type_code,
            raw_value=raw_value,
            track=TrackInspection.from_wire(value.get("track")),
            sampled_check_value=sampled_check_value,
        )


@dataclass(frozen=True, slots=True)
class EffectInspection:
    index: int
    occurrence: int
    name: str
    selector: str
    enabled: bool
    locked: bool
    items: tuple[ItemInspection, ...]

    @classmethod
    def from_wire(cls, value: object) -> EffectInspection:
        if not isinstance(value, dict):
            raise ProtocolError("Live Bridge returned an invalid effect")
        items = value.get("items")
        if (
            not _integer(value.get("index"))
            or not _integer(value.get("occurrence"))
            or not isinstance(value.get("name"), str)
            or not isinstance(value.get("selector"), str)
            or not isinstance(value.get("enabled"), bool)
            or not isinstance(value.get("locked"), bool)
            or not isinstance(items, list)
        ):
            raise ProtocolError("Live Bridge returned an invalid effect")
        return cls(
            index=value["index"],
            occurrence=value["occurrence"],
            name=value["name"],
            selector=value["selector"],
            enabled=value["enabled"],
            locked=value["locked"],
            items=tuple(ItemInspection.from_wire(item) for item in items),
        )


@dataclass(frozen=True, slots=True)
class ObjectInspection:
    object_id: str
    revision: int
    sample_frame: int
    effects: tuple[EffectInspection, ...]

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> ObjectInspection:
        object_id = result.get("object_id")
        revision = result.get("revision")
        sample_frame = result.get("sample_frame")
        effect_count = result.get("effect_count")
        effects = result.get("effects")
        if (
            not isinstance(object_id, str)
            or not _integer(revision)
            or not _integer(sample_frame)
            or not _integer(effect_count)
            or not isinstance(effects, list)
            or effect_count != len(effects)
        ):
            raise ProtocolError("Live Bridge returned an invalid object inspection")
        assert isinstance(object_id, str)
        assert isinstance(revision, int)
        assert isinstance(sample_frame, int)
        if (
            revision <= 0
            or not object_id.startswith(f"obj-{revision}-")
            or sample_frame < 0
        ):
            raise ProtocolError("Live Bridge returned an invalid object inspection")
        return cls(
            object_id=object_id,
            revision=revision,
            sample_frame=sample_frame,
            effects=tuple(EffectInspection.from_wire(item) for item in effects),
        )


__all__ = [
    "EffectInspection",
    "ItemInspection",
    "ObjectInspection",
    "TrackInspection",
]
