"""Frame marks exposed by the official AviUtl2 SDK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocol import ProtocolError


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class FrameMark:
    frame: int
    memo: str

    @classmethod
    def from_wire(cls, entry: dict[str, Any]) -> FrameMark:
        if not _integer(entry.get("frame")) or not isinstance(entry.get("memo"), str):
            raise ProtocolError("Live Bridge returned an invalid frame mark")
        return cls(frame=entry["frame"], memo=entry["memo"])


@dataclass(frozen=True, slots=True)
class FrameMarkList:
    revision: int
    marks: tuple[FrameMark, ...]

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> FrameMarkList:
        entries = result.get("marks")
        if (
            not _integer(result.get("revision"))
            or not _integer(result.get("count"))
            or not isinstance(entries, list)
        ):
            raise ProtocolError("Live Bridge returned invalid frame marks")
        if result["count"] != len(entries):
            raise ProtocolError("Live Bridge frame mark count mismatch")
        marks = tuple(FrameMark.from_wire(entry) for entry in entries)
        if len(marks) != len({mark.frame for mark in marks}):
            raise ProtocolError("Live Bridge returned duplicate frame marks")
        return cls(revision=result["revision"], marks=marks)


@dataclass(frozen=True, slots=True)
class MarkEditResult:
    frame: int
    revision: int
    non_undoable: bool
    memo: str | None = None

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> MarkEditResult:
        memo = result.get("memo")
        if (
            not _integer(result.get("frame"))
            or not _integer(result.get("revision"))
            or not isinstance(result.get("non_undoable"), bool)
            or (memo is not None and not isinstance(memo, str))
        ):
            raise ProtocolError("Live Bridge returned an invalid mark edit result")
        return cls(
            frame=result["frame"],
            revision=result["revision"],
            non_undoable=result["non_undoable"],
            memo=memo,
        )
