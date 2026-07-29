"""Typed Phase 2 commands and high-level AviUtl2 object builders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aviutl2_api.models import (
    AnimatedValue,
    Effect,
    StaticValue,
    TimelineObject,
)

from .alias import serialize_object_alias


@dataclass(frozen=True)
class CreateFromAliasCommand:
    """Create one AviUtl2 object from native Alias data."""

    alias: str
    layer: int
    frame: int
    length: int
    client_id: str | None = None

    def __post_init__(self) -> None:
        if not self.alias:
            raise ValueError("alias must not be empty")
        if self.layer < 0:
            raise ValueError("layer must be non-negative")
        if self.frame < 0:
            raise ValueError("frame must be non-negative")
        if self.length < 1:
            raise ValueError("length must be positive")
        if self.client_id is not None and not self.client_id:
            raise ValueError("client_id must not be empty")

    @classmethod
    def from_object(
        cls,
        obj: TimelineObject,
        *,
        client_id: str | None = None,
    ) -> CreateFromAliasCommand:
        return cls(
            alias=serialize_object_alias(obj),
            layer=obj.layer,
            frame=obj.frame_start,
            length=obj.duration_frames,
            client_id=client_id,
        )

    def to_wire(self) -> dict[str, Any]:
        command: dict[str, Any] = {
            "op": "object.create_from_alias",
            "alias": self.alias,
            "layer": self.layer,
            "frame": self.frame,
            "length": self.length,
        }
        if self.client_id is not None:
            command["client_id"] = self.client_id
        return command


@dataclass(frozen=True)
class ItemUpdate:
    """One AviUtl2 effect setting update in Alias value format."""

    effect: str
    item: str
    value: str | int | float | bool | StaticValue | AnimatedValue

    def __post_init__(self) -> None:
        for field_name, field_value in (
            ("effect", self.effect),
            ("item", self.item),
        ):
            if (
                not field_value
                or "\x00" in field_value
                or "\r" in field_value
                or "\n" in field_value
            ):
                raise ValueError(
                    f"{field_name} must be a non-empty single-line string"
                )

    def to_wire(self) -> dict[str, str]:
        return {
            "effect": self.effect,
            "item": self.item,
            "value": _format_item_value(self.value),
        }


def _format_item_value(
    value: str | int | float | bool | StaticValue | AnimatedValue,
) -> str:
    if isinstance(value, (StaticValue, AnimatedValue)):
        formatted = value.to_aup2()
    elif isinstance(value, bool):
        formatted = "1" if value else "0"
    elif isinstance(value, float):
        formatted = f"{value:.6f}"
    else:
        formatted = str(value)
    if "\x00" in formatted:
        raise ValueError("item value must not contain NUL characters")
    return formatted.replace("\r\n", "\\n").replace("\r", "\\n").replace(
        "\n", "\\n"
    )


def make_text_object(
    text: str,
    *,
    layer: int,
    frame: int,
    length: int,
    x: float = 0.0,
    y: float = 0.0,
    size: float = 34.0,
    color: str = "ffffff",
) -> TimelineObject:
    """Build a text object using AviUtl2's native effect/property names."""
    normalized_color = color.removeprefix("#").lower()
    if len(normalized_color) != 6 or any(
        character not in "0123456789abcdef"
        for character in normalized_color
    ):
        raise ValueError("color must be a six-digit hexadecimal RGB value")
    if length < 1:
        raise ValueError("length must be positive")

    return TimelineObject(
        object_id=0,
        layer=layer,
        frame_start=frame,
        frame_end=frame + length - 1,
        effects=[
            Effect(
                effect_id=0,
                name="テキスト",
                properties={
                    "サイズ": StaticValue(float(size)),
                    "文字色": normalized_color,
                    "テキスト": text,
                },
            ),
            Effect(
                effect_id=1,
                name="標準描画",
                properties={
                    "X": StaticValue(float(x)),
                    "Y": StaticValue(float(y)),
                },
            ),
        ],
    )


__all__ = [
    "CreateFromAliasCommand",
    "ItemUpdate",
    "make_text_object",
]
