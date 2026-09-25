"""Scene CRUD, project file, export, and stable-ID protocol types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .protocol import ProtocolError

OBJECT_FLAG_KINDS = (
    "enable_group",
    "enable_camera",
    "clipping_object",
    "clipping_upper_object",
)


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class BackgroundColor:
    """Scene background color with 0-255 RGBA channels."""

    r: int = 0
    g: int = 0
    b: int = 0
    a: int = 255

    def __post_init__(self) -> None:
        for name in ("r", "g", "b", "a"):
            value = getattr(self, name)
            if not _integer(value) or value < 0 or value > 255:
                raise ValueError(
                    f"background {name} must be an integer between 0 and 255"
                )

    def to_wire(self) -> dict[str, int]:
        return {"r": self.r, "g": self.g, "b": self.b, "a": self.a}

    @classmethod
    def from_value(cls, value: object) -> BackgroundColor:
        """Accept a :class:`BackgroundColor` or an r/g/b/a mapping."""
        if isinstance(value, BackgroundColor):
            return value
        if isinstance(value, Mapping):
            return cls(
                r=value.get("r", 0),
                g=value.get("g", 0),
                b=value.get("b", 0),
                a=value.get("a", 255),
            )
        raise TypeError("background must be BackgroundColor or a mapping with r/g/b/a")


@dataclass(frozen=True, slots=True)
class SceneListEntry:
    """One scene exposed by ``scene.list``."""

    name: str
    scene_id: int


@dataclass(frozen=True, slots=True)
class SceneList:
    """Every scene in the currently open project."""

    scenes: tuple[SceneListEntry, ...]

    def __len__(self) -> int:
        return len(self.scenes)

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> SceneList:
        entries = result.get("scenes")
        if (
            not _integer(result.get("count"))
            or not isinstance(entries, list)
            or result["count"] != len(entries)
        ):
            raise ProtocolError("Live Bridge returned an invalid scene list")
        parsed: list[SceneListEntry] = []
        for value in entries:
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("name"), str)
                or not _integer(value.get("scene_id"))
                or value["scene_id"] < 0
            ):
                raise ProtocolError("Live Bridge returned an invalid scene entry")
            parsed.append(SceneListEntry(value["name"], value["scene_id"]))
        return cls(tuple(parsed))


@dataclass(frozen=True, slots=True)
class ProjectMutation:
    """Receipt for a non-undoable project-level mutation."""

    revision: int

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> ProjectMutation:
        if (
            result.get("non_undoable") is not True
            or not _integer(result.get("revision"))
            or result["revision"] <= 0
        ):
            raise ProtocolError("Live Bridge returned an invalid mutation receipt")
        return cls(result["revision"])


@dataclass(frozen=True, slots=True)
class ExportStartReceipt:
    """Receipt for an accepted ``export.start`` request."""

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> ExportStartReceipt:
        if result.get("started") is not True or result.get("non_undoable") is not True:
            raise ProtocolError("Live Bridge returned an invalid export receipt")
        return cls()


@dataclass(frozen=True, slots=True)
class ObjectFlagState:
    """One object flag read through ``object.flag.get``."""

    kind: str
    flag: bool

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> ObjectFlagState:
        kind = result.get("kind")
        if (
            not isinstance(result.get("flag"), bool)
            or not isinstance(kind, str)
            or kind not in OBJECT_FLAG_KINDS
        ):
            raise ProtocolError("Live Bridge returned an invalid object flag")
        return cls(kind, result["flag"])


@dataclass(frozen=True, slots=True)
class StableId:
    """A stable int64 identifier scoped to objects or effects."""

    id: int
    scope: str

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> StableId:
        scope = result.get("scope")
        if (
            not _integer(result.get("id"))
            or not isinstance(scope, str)
            or scope not in {"object", "effect"}
        ):
            raise ProtocolError("Live Bridge returned an invalid stable id")
        return cls(result["id"], scope)


__all__ = [
    "BackgroundColor",
    "ExportStartReceipt",
    "OBJECT_FLAG_KINDS",
    "ObjectFlagState",
    "ProjectMutation",
    "SceneList",
    "SceneListEntry",
    "StableId",
]
