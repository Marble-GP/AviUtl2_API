"""Typed layer state from the currently open AviUtl2 scene."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocol import ProtocolError


def _integer(value: Any, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ProtocolError("Live Bridge returned an invalid layer page")
    return value


@dataclass(frozen=True, slots=True)
class LayerInfo:
    layer: int
    name: str | None
    enabled: bool
    locked: bool
    visible: bool
    object_count: int

    @classmethod
    def from_wire(cls, value: Any) -> LayerInfo:
        if not isinstance(value, dict):
            raise ProtocolError("Live Bridge returned an invalid layer")
        name = value.get("name")
        enabled = value.get("enabled")
        locked = value.get("locked")
        visible = value.get("visible")
        if (
            (name is not None and not isinstance(name, str))
            or not isinstance(enabled, bool)
            or not isinstance(locked, bool)
            or not isinstance(visible, bool)
        ):
            raise ProtocolError("Live Bridge returned an invalid layer")
        return cls(
            layer=_integer(value.get("layer")),
            name=name,
            enabled=enabled,
            locked=locked,
            visible=visible,
            object_count=_integer(value.get("object_count")),
        )


@dataclass(frozen=True, slots=True)
class LayerPage:
    revision: int
    scene_id: int
    layer_max: int
    display_start: int
    display_count: int
    start: int
    layers: tuple[LayerInfo, ...]

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> LayerPage:
        revision = _integer(result.get("revision"), minimum=1)
        scene_id = _integer(result.get("scene_id"))
        layer_max = _integer(result.get("layer_max"))
        start = _integer(result.get("start"))
        count = _integer(result.get("count"))
        display = result.get("display")
        layers_value = result.get("layers")
        if not isinstance(display, dict) or not isinstance(layers_value, list):
            raise ProtocolError("Live Bridge returned an invalid layer page")
        display_start = _integer(display.get("start"))
        display_count = _integer(display.get("count"))
        if count != len(layers_value):
            raise ProtocolError("Live Bridge returned an invalid layer page")
        layers = tuple(LayerInfo.from_wire(layer) for layer in layers_value)
        if any(layer.layer != start + index for index, layer in enumerate(layers)):
            raise ProtocolError("Live Bridge returned a non-contiguous layer page")
        return cls(
            revision=revision,
            scene_id=scene_id,
            layer_max=layer_max,
            display_start=display_start,
            display_count=display_count,
            start=start,
            layers=layers,
        )


__all__ = ["LayerInfo", "LayerPage"]
