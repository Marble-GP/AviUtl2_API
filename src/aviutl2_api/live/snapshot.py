"""Typed snapshots of the currently open AviUtl2 scene."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocol import ProtocolError


@dataclass(frozen=True, slots=True)
class SnapshotObject:
    """A revision-scoped reference to one AviUtl2 timeline object."""

    object_id: str
    revision: int
    layer: int
    frame_start: int
    frame_end: int
    name: str | None
    alias: str | None
    api_locked: bool = False

    @property
    def duration_frames(self) -> int:
        return self.frame_end - self.frame_start + 1

    def target_params(self) -> dict[str, Any]:
        return {
            "expected_revision": self.revision,
            "target": {"object_id": self.object_id},
        }


@dataclass(frozen=True, slots=True)
class ProjectSnapshot:
    """An immutable view used for optimistic, stale-safe editing."""

    revision: int
    scene_id: int
    objects: tuple[SnapshotObject, ...]
    offset: int = 0
    total: int = 0
    next_offset: int | None = None

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> ProjectSnapshot:
        revision = result.get("revision")
        scene_id = result.get("scene_id")
        object_count = result.get("object_count")
        objects = result.get("objects")
        if (
            not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision <= 0
            or not isinstance(scene_id, int)
            or isinstance(scene_id, bool)
            or not isinstance(object_count, int)
            or isinstance(object_count, bool)
            or not isinstance(objects, list)
            or object_count != len(objects)
        ):
            raise ProtocolError("Live Bridge returned an invalid snapshot")

        parsed: list[SnapshotObject] = []
        for value in objects:
            if not isinstance(value, dict):
                raise ProtocolError("Live Bridge returned an invalid snapshot object")
            object_id = value.get("object_id")
            layer = value.get("layer")
            frame_start = value.get("frame_start")
            frame_end = value.get("frame_end")
            name = value.get("name")
            alias = value.get("alias")
            api_locked = value.get("api_locked", False)
            if (
                not isinstance(object_id, str)
                or not object_id.startswith(f"obj-{revision}-")
                or not isinstance(layer, int)
                or isinstance(layer, bool)
                or layer < 0
                or not isinstance(frame_start, int)
                or isinstance(frame_start, bool)
                or frame_start < 0
                or not isinstance(frame_end, int)
                or isinstance(frame_end, bool)
                or frame_end < frame_start
                or (name is not None and not isinstance(name, str))
                or (alias is not None and not isinstance(alias, str))
                or not isinstance(api_locked, bool)
            ):
                raise ProtocolError("Live Bridge returned an invalid snapshot object")
            parsed.append(
                SnapshotObject(
                    object_id=object_id,
                    revision=revision,
                    layer=layer,
                    frame_start=frame_start,
                    frame_end=frame_end,
                    name=name,
                    alias=alias,
                    api_locked=api_locked,
                )
            )
        offset = result.get("offset", 0)
        total = result.get("total", len(parsed))
        next_offset = result.get("next_offset")
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or not isinstance(total, int)
            or isinstance(total, bool)
            or total < len(parsed)
            or (
                next_offset is not None
                and (
                    not isinstance(next_offset, int)
                    or isinstance(next_offset, bool)
                    or next_offset <= offset
                )
            )
        ):
            raise ProtocolError("Live Bridge returned invalid snapshot paging")
        return cls(
            revision=revision,
            scene_id=scene_id,
            objects=tuple(parsed),
            offset=offset,
            total=total,
            next_offset=next_offset,
        )


__all__ = ["ProjectSnapshot", "SnapshotObject"]
