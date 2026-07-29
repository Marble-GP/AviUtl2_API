"""Current-scene settings exposed by the official AviUtl2 SDK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocol import ProtocolError


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class SceneInfo:
    scene_id: int
    revision: int
    name: str
    width: int
    height: int
    rate: int
    scale: int
    sample_rate: int
    non_undoable: bool

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> SceneInfo:
        frame_rate = result.get("frame_rate")
        if (
            not isinstance(frame_rate, dict)
            or not _integer(frame_rate.get("rate"))
            or not _integer(frame_rate.get("scale"))
            or not _integer(result.get("scene_id"))
            or not _integer(result.get("revision"))
            or not isinstance(result.get("name"), str)
            or not _integer(result.get("width"))
            or not _integer(result.get("height"))
            or not _integer(result.get("sample_rate"))
            or not isinstance(result.get("non_undoable"), bool)
        ):
            raise ProtocolError("Live Bridge returned invalid scene information")
        return cls(
            scene_id=result["scene_id"],
            revision=result["revision"],
            name=result["name"],
            width=result["width"],
            height=result["height"],
            rate=frame_rate["rate"],
            scale=frame_rate["scale"],
            sample_rate=result["sample_rate"],
            non_undoable=result["non_undoable"],
        )


__all__ = ["SceneInfo"]
