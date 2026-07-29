"""Typed descriptions of effects exposed by the running AviUtl2 host."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocol import ProtocolError


def _integer(value: Any, *, minimum: int = 0) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
    ):
        raise ProtocolError("Live Bridge returned an invalid effect catalog")
    return value


@dataclass(frozen=True, slots=True)
class EffectFlags:
    video: bool
    audio: bool
    filter_object: bool
    camera: bool

    @classmethod
    def from_wire(cls, value: Any) -> EffectFlags:
        if not isinstance(value, dict):
            raise ProtocolError("Live Bridge returned invalid effect flags")
        video = value.get("video")
        audio = value.get("audio")
        filter_object = value.get("filter_object")
        camera = value.get("camera")
        if (
            not isinstance(video, bool)
            or not isinstance(audio, bool)
            or not isinstance(filter_object, bool)
            or not isinstance(camera, bool)
        ):
            raise ProtocolError("Live Bridge returned invalid effect flags")
        return cls(
            video=video,
            audio=audio,
            filter_object=filter_object,
            camera=camera,
        )


@dataclass(frozen=True, slots=True)
class CatalogItem:
    name: str
    type: str
    type_code: int

    @classmethod
    def from_wire(cls, value: Any) -> CatalogItem:
        if not isinstance(value, dict):
            raise ProtocolError("Live Bridge returned an invalid catalog item")
        name = value.get("name")
        item_type = value.get("type")
        if not isinstance(name, str) or not isinstance(item_type, str):
            raise ProtocolError("Live Bridge returned an invalid catalog item")
        return cls(
            name=name,
            type=item_type,
            type_code=_integer(value.get("type_code")),
        )


@dataclass(frozen=True, slots=True)
class CatalogEffect:
    name: str
    type: str
    type_code: int
    flags: EffectFlags
    items: tuple[CatalogItem, ...]

    @classmethod
    def from_wire(cls, value: Any) -> CatalogEffect:
        if not isinstance(value, dict):
            raise ProtocolError("Live Bridge returned an invalid effect")
        name = value.get("name")
        effect_type = value.get("type")
        items = value.get("items")
        if (
            not isinstance(name, str)
            or not isinstance(effect_type, str)
            or not isinstance(items, list)
        ):
            raise ProtocolError("Live Bridge returned an invalid effect")
        return cls(
            name=name,
            type=effect_type,
            type_code=_integer(value.get("type_code")),
            flags=EffectFlags.from_wire(value.get("flags")),
            items=tuple(CatalogItem.from_wire(item) for item in items),
        )


@dataclass(frozen=True, slots=True)
class EffectCatalogPage:
    start: int
    total: int
    next_start: int | None
    effects: tuple[CatalogEffect, ...]

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> EffectCatalogPage:
        start = _integer(result.get("start"))
        total = _integer(result.get("total"))
        count = _integer(result.get("count"))
        next_start_value = result.get("next_start")
        effects_value = result.get("effects")
        if (
            not isinstance(effects_value, list)
            or count != len(effects_value)
            or start > total
            or (
                next_start_value is not None
                and (
                    not isinstance(next_start_value, int)
                    or isinstance(next_start_value, bool)
                    or next_start_value <= start
                    or next_start_value > total
                )
            )
        ):
            raise ProtocolError(
                "Live Bridge returned an invalid effect catalog page"
            )
        effects = tuple(
            CatalogEffect.from_wire(effect) for effect in effects_value
        )
        if next_start_value is None and start + len(effects) < total:
            raise ProtocolError(
                "Live Bridge returned an invalid effect catalog page"
            )
        return cls(
            start=start,
            total=total,
            next_start=next_start_value,
            effects=effects,
        )


__all__ = [
    "CatalogEffect",
    "CatalogItem",
    "EffectCatalogPage",
    "EffectFlags",
]
