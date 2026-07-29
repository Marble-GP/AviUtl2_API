"""AviUtl2 object Alias serialization for live editing."""

from __future__ import annotations

from typing import Any

from aviutl2_api.models import AnimatedValue, StaticValue, TimelineObject

LINE_ENDING = "\r\n"


def _safe_line(value: str, *, field: str, escape_newlines: bool = False) -> str:
    if "\x00" in value:
        raise ValueError(f"{field} must not contain NUL characters")
    if escape_newlines:
        return value.replace("\r\n", "\\n").replace("\r", "\\n").replace(
            "\n", "\\n"
        )
    if "\r" in value or "\n" in value:
        raise ValueError(f"{field} must not contain line breaks")
    return value


def _format_value(value: Any) -> str:
    if isinstance(value, (StaticValue, AnimatedValue)):
        formatted = value.to_aup2()
    elif isinstance(value, bool):
        formatted = "1" if value else "0"
    elif isinstance(value, float):
        formatted = f"{value:.6f}"
    else:
        formatted = str(value)
    return _safe_line(formatted, field="property value", escape_newlines=True)


def serialize_object_alias(obj: TimelineObject) -> str:
    """Serialize one timeline object to AviUtl2's UTF-8 Alias format.

    Placement and duration are intentionally omitted. They are supplied to the
    SDK separately so that the currently open project's timeline remains the
    source of truth.
    """
    if not obj.effects:
        raise ValueError("a live object Alias requires at least one effect")

    lines = ["[Object]"]
    for index, effect in enumerate(obj.effects):
        effect_name = _safe_line(effect.name, field="effect name")
        if not effect_name:
            raise ValueError("effect name must not be empty")
        lines.extend((f"[Object.{index}]", f"effect.name={effect_name}"))
        for key, value in effect.properties.items():
            safe_key = _safe_line(str(key), field="property name")
            if not safe_key or "=" in safe_key:
                raise ValueError(
                    "property name must be non-empty and must not contain '='"
                )
            lines.append(f"{safe_key}={_format_value(value)}")
    return LINE_ENDING.join(lines) + LINE_ENDING


__all__ = ["serialize_object_alias"]
