"""Typed AviUtl2 object section boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocol import ProtocolError


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class ObjectSection:
    index: int
    frame: int


@dataclass(frozen=True, slots=True)
class ObjectSections:
    revision: int
    sections: tuple[ObjectSection, ...]

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> ObjectSections:
        revision = result.get("revision")
        count = result.get("count")
        sections = result.get("sections")
        if (
            not _integer(revision)
            or not _integer(count)
            or not isinstance(sections, list)
            or count != len(sections)
        ):
            raise ProtocolError("Live Bridge returned invalid object sections")
        assert isinstance(revision, int)
        parsed: list[ObjectSection] = []
        for value in sections:
            if (
                not isinstance(value, dict)
                or not _integer(value.get("index"))
                or not _integer(value.get("frame"))
            ):
                raise ProtocolError("Live Bridge returned an invalid section")
            parsed.append(
                ObjectSection(
                    index=value["index"],
                    frame=value["frame"],
                )
            )
        if any(section.index != index for index, section in enumerate(parsed)):
            raise ProtocolError("Live Bridge returned non-contiguous sections")
        if any(
            left.frame >= right.frame
            for left, right in zip(parsed, parsed[1:])
        ):
            raise ProtocolError("Live Bridge returned unordered sections")
        return cls(revision=revision, sections=tuple(parsed))


__all__ = ["ObjectSection", "ObjectSections"]
