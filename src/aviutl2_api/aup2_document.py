"""Ordered, loss-preserving representation of an AviUtl2 project file."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from aviutl2_api.models import AnimatedValue, Project, StaticValue
from aviutl2_api.models.values import parse_property_value
from aviutl2_api.serializer import serialize

_SECTION_RE = re.compile(r"^\[([^\]\r\n]+)\]$")
_KEY_VALUE_RE = re.compile(r"^([^=\r\n]+)=(.*)$")
_OBJECT_RE = re.compile(r"^\d+(?:\.\d+)?$")
_PROJECT_PATH_KEYS = frozenset({"file", "ファイル", "繝輔ぃ繧､繝ｫ"})


class Aup2DocumentError(ValueError):
    """Raised when a document cannot be edited without guessing."""


@dataclass(frozen=True, slots=True)
class Aup2Section:
    """One ordered section, retaining every original body line."""

    name: str
    body: tuple[str, ...]

    def key_values(self) -> tuple[tuple[str, str], ...]:
        values: list[tuple[str, str]] = []
        for line in self.body:
            matched = _KEY_VALUE_RE.fullmatch(line)
            if matched is not None:
                values.append((matched.group(1), matched.group(2)))
        return tuple(values)


class Aup2Document:
    """Loss-preserving document used by :class:`LocalProject`.

    The legacy parser remains the semantic authority.  This representation
    keeps unknown sections, keys, and raw lines so a high-level edit only
    rewrites the object/effect sections it actually changed.
    """

    def __init__(
        self,
        *,
        preamble: Iterable[str] = (),
        sections: Iterable[Aup2Section] = (),
    ) -> None:
        self.preamble = tuple(preamble)
        self.sections = tuple(sections)
        self._validate()

    @classmethod
    def parse_bytes(cls, payload: bytes) -> Aup2Document:
        if payload.startswith(b"\xef\xbb\xbf"):
            payload = payload[3:]
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise Aup2DocumentError(".aup2 must be valid UTF-8") from error
        return cls.parse_text(text)

    @classmethod
    def parse_text(cls, text: str) -> Aup2Document:
        if "\x00" in text:
            raise Aup2DocumentError(".aup2 must not contain NUL characters")
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        preamble: list[str] = []
        sections: list[Aup2Section] = []
        name: str | None = None
        body: list[str] = []
        for line_number, line in enumerate(lines, 1):
            match = _SECTION_RE.fullmatch(line)
            if match is not None:
                if name is not None:
                    sections.append(Aup2Section(name, tuple(body)))
                name = match.group(1)
                body = []
            elif name is None:
                preamble.append(line)
            else:
                body.append(line)
            if "\x00" in line:
                raise Aup2DocumentError(
                    f"line {line_number}: NUL characters are not allowed"
                )
        if name is not None:
            sections.append(Aup2Section(name, tuple(body)))
        if not sections:
            raise Aup2DocumentError(".aup2 does not contain any sections")
        return cls(preamble=preamble, sections=sections)

    @classmethod
    def from_project(cls, project: Project) -> Aup2Document:
        return cls.parse_text(serialize(project))

    def _validate(self) -> None:
        seen_known: set[str] = set()
        for section in self.sections:
            known = (
                section.name == "project"
                or section.name.startswith("scene.")
                or _OBJECT_RE.fullmatch(section.name) is not None
            )
            if known and section.name in seen_known:
                raise Aup2DocumentError(f"duplicate known section: [{section.name}]")
            if known:
                seen_known.add(section.name)
            seen_keys: set[str] = set()
            for key, _value in section.key_values():
                if key in seen_keys and known:
                    raise Aup2DocumentError(f"duplicate key in [{section.name}]: {key}")
                seen_keys.add(key)
        if "project" not in seen_known:
            raise Aup2DocumentError(".aup2 is missing [project]")

    @property
    def section_names(self) -> tuple[str, ...]:
        return tuple(section.name for section in self.sections)

    def section(self, name: str) -> Aup2Section | None:
        return next((value for value in self.sections if value.name == name), None)

    def render(
        self,
        project: Project,
        *,
        dirty_sections: Iterable[str] = (),
        deleted_sections: Iterable[str] = (),
        project_path: Path | None = None,
    ) -> str:
        """Patch changed known sections and preserve everything else."""

        dirty = set(dirty_sections)
        deleted = set(deleted_sections)
        canonical = Aup2Document.from_project(project)
        canonical_by_name = {section.name: section for section in canonical.sections}
        original_by_name = {section.name: section for section in self.sections}
        output: list[Aup2Section] = []
        emitted: set[str] = set()

        for original in self.sections:
            name = original.name
            if name in deleted:
                continue
            replacement = canonical_by_name.get(name)
            if replacement is None or name not in dirty:
                section = original
            else:
                section = self._merge_known(original, replacement)
            if name == "project" and project_path is not None:
                section = self._with_project_path(section, project_path)
            output.append(section)
            emitted.add(name)

        for section in canonical.sections:
            if section.name in emitted or section.name in deleted:
                continue
            if section.name in dirty or section.name not in original_by_name:
                if section.name == "project" and project_path is not None:
                    section = self._with_project_path(section, project_path)
                output.append(section)

        lines = list(self.preamble)
        for section in output:
            lines.append(f"[{section.name}]")
            lines.extend(section.body)
        return "\r\n".join(lines) + "\r\n"

    @staticmethod
    def _merge_known(
        original: Aup2Section,
        replacement: Aup2Section,
    ) -> Aup2Section:
        replacement_values = dict(replacement.key_values())
        emitted: set[str] = set()
        body: list[str] = []
        for line in original.body:
            match = _KEY_VALUE_RE.fullmatch(line)
            if match is None:
                body.append(line)
                continue
            key, original_value = match.groups()
            replacement_value = replacement_values.get(key)
            if replacement_value is None:
                # Enabling an Effect intentionally removes this metadata.
                if key != "effect.disable":
                    body.append(line)
                continue
            emitted.add(key)
            if Aup2Document._equivalent_original_value(
                key,
                original_value,
                replacement_value,
            ):
                body.append(line)
            else:
                body.append(f"{key}={replacement_value}")
        for key, value in replacement.key_values():
            if key not in emitted:
                body.append(f"{key}={value}")
        return Aup2Section(original.name, tuple(body))

    @staticmethod
    def _equivalent_original_value(
        key: str,
        original: str,
        replacement: str,
    ) -> bool:
        if original == replacement:
            return True
        if key == "effect.name":
            return False
        try:
            parsed = parse_property_value(key, original)
        except (TypeError, ValueError):
            return False
        if isinstance(parsed, (StaticValue, AnimatedValue)):
            return parsed.to_aup2() == replacement
        return str(parsed) == replacement

    @staticmethod
    def _with_project_path(
        section: Aup2Section,
        path: Path,
    ) -> Aup2Section:
        absolute = str(path.expanduser().resolve())
        body: list[str] = []
        found = False
        for line in section.body:
            match = _KEY_VALUE_RE.fullmatch(line)
            if match is not None and match.group(1) in _PROJECT_PATH_KEYS:
                if not found:
                    body.append(f"{match.group(1)}={absolute}")
                    found = True
                continue
            body.append(line)
        if not found:
            insert_at = 1 if body and body[0].startswith("version=") else 0
            body.insert(insert_at, f"file={absolute}")
        return Aup2Section(section.name, tuple(body))


__all__ = ["Aup2Document", "Aup2DocumentError", "Aup2Section"]
