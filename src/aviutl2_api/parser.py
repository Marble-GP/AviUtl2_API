"""Parser for AviUtl2 project files (.aup2)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, TextIO

from aviutl2_api.models import (
    Effect,
    Project,
    Scene,
    TimelineObject,
    parse_property_value,
)


class Aup2ParseError(Exception):
    """Error during .aup2 parsing."""

    def __init__(self, message: str, line_number: int | None = None):
        self.line_number = line_number
        if line_number:
            message = f"Line {line_number}: {message}"
        super().__init__(message)


class Aup2Parser:
    """Parser for .aup2 files."""

    # Regex patterns
    SECTION_PATTERN = re.compile(r"^\[(.+)\]$")
    KEY_VALUE_PATTERN = re.compile(r"^([^=]+)=(.*)$")

    def __init__(self):
        self._line_number = 0
        self._current_section: str | None = None
        self._sections: dict[str, dict[str, str]] = {}

    def parse_file(self, file_path: str | Path) -> Project:
        """Parse a .aup2 file and return a Project object."""
        path = Path(file_path)
        with path.open("r", encoding="utf-8") as f:
            return self.parse(f)

    def parse_string(self, content: str) -> Project:
        """Parse a .aup2 string and return a Project object."""
        # Normalize line endings
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        lines = content.split("\n")
        return self._parse_lines(lines)

    def parse(self, file: TextIO) -> Project:
        """Parse from a file-like object."""
        content = file.read()
        return self.parse_string(content)

    def _parse_lines(self, lines: list[str]) -> Project:
        """Parse lines into sections."""
        self._line_number = 0
        self._current_section = None
        self._sections = {}

        for line in lines:
            self._line_number += 1
            self._parse_line(line)

        return self._build_project()

    def _parse_line(self, line: str) -> None:
        """Parse a single line."""
        line = line.strip()

        # Skip empty lines
        if not line:
            return

        # Check for section header
        section_match = self.SECTION_PATTERN.match(line)
        if section_match:
            self._current_section = section_match.group(1)
            if self._current_section not in self._sections:
                self._sections[self._current_section] = {}
            return

        # Check for key=value
        kv_match = self.KEY_VALUE_PATTERN.match(line)
        if kv_match and self._current_section:
            key = kv_match.group(1)
            value = kv_match.group(2)
            self._sections[self._current_section][key] = value
            return

        # Unknown line format (skip silently for robustness)

    def _build_project(self) -> Project:
        """Build Project object from parsed sections."""
        project = Project()

        # Parse [project] section
        if "project" in self._sections:
            proj_data = self._sections["project"]
            project.version = int(proj_data.get("version", "2001901"))
            # Support both "ファイル" (correct Japanese) and "file" (old format)
            project.file_path = proj_data.get("ファイル", proj_data.get("file", ""))
            project.display_scene = int(proj_data.get("display.scene", "0"))

        # Parse [scene.N] sections
        scene_sections = [
            (key, data) for key, data in self._sections.items()
            if key.startswith("scene.")
        ]

        for section_name, scene_data in sorted(scene_sections):
            scene = self._build_scene(section_name, scene_data)
            project.scenes.append(scene)

        # If no scenes, create default
        if not project.scenes:
            project.scenes.append(Scene(scene_id=0))

        # Parse object sections and assign to scenes
        self._parse_objects(project)

        return project

    def _build_scene(self, section_name: str, data: dict[str, str]) -> Scene:
        """Build a Scene from section data."""
        scene_id = int(section_name.split(".")[1])

        scene = Scene(
            scene_id=scene_id,
            name=data.get("name", "Root"),
            width=int(data.get("video.width", "1920")),
            height=int(data.get("video.height", "1080")),
            fps=int(data.get("video.rate", "30")),
            video_scale=int(data.get("video.scale", "1")),
            audio_rate=int(data.get("audio.rate", "44100")),
            cursor_frame=int(data.get("cursor.frame", "0")),
            cursor_layer=int(data.get("cursor.layer", "0")),
            display_frame=int(data.get("display.frame", "0")),
            display_layer=int(data.get("display.layer", "0")),
            display_zoom=int(data.get("display.zoom", "10000")),
            display_order=int(data.get("display.order", "0")),
            display_camera=data.get("display.camera", ""),
            grid_x=data.get("display.grid.x", "16,-16"),
            grid_y=data.get("display.grid.y", "16,-16"),
            grid_width=int(data.get("display.grid.width", "200")),
            grid_height=int(data.get("display.grid.height", "200")),
            grid_step=float(data.get("display.grid.step", "200.0")),
            grid_range=float(data.get("display.grid.range", "10000.0")),
            tempo_bpm=float(data.get("display.tempo.bpm", "120.0")),
            tempo_beat=int(data.get("display.tempo.beat", "4")),
            tempo_offset=float(data.get("display.tempo.offset", "0.0")),
        )

        return scene

    def _parse_objects(self, project: Project) -> None:
        """Parse timeline objects and their effects."""
        # Find object sections (numeric IDs like [0], [1], etc.)
        object_sections: dict[int, dict[str, str]] = {}
        effect_sections: dict[tuple[int, int], dict[str, str]] = {}

        for section_name, data in self._sections.items():
            # Skip non-object sections
            if section_name in ("project",) or section_name.startswith("scene."):
                continue

            # Check if it's an object or effect section
            if "." in section_name:
                # Effect section: [0.0], [0.1], etc.
                parts = section_name.split(".")
                try:
                    obj_id = int(parts[0])
                    effect_id = int(parts[1])
                    effect_sections[(obj_id, effect_id)] = data
                except ValueError:
                    continue
            else:
                # Object section: [0], [1], etc.
                try:
                    obj_id = int(section_name)
                    object_sections[obj_id] = data
                except ValueError:
                    continue

        # Build objects
        for obj_id in sorted(object_sections.keys()):
            obj_data = object_sections[obj_id]
            obj = self._build_object(obj_id, obj_data, effect_sections)

            # Add to first scene (TODO: handle multi-scene)
            if project.scenes:
                project.scenes[0].objects.append(obj)

    def _build_object(
        self,
        obj_id: int,
        data: dict[str, str],
        effect_sections: dict[tuple[int, int], dict[str, str]]
    ) -> TimelineObject:
        """Build a TimelineObject from section data."""
        # Parse frame range
        frame_str = data.get("frame", "0,0")
        frame_parts = frame_str.split(",")
        frame_start = int(frame_parts[0])
        frame_end = int(frame_parts[1]) if len(frame_parts) > 1 else frame_start

        obj = TimelineObject(
            object_id=obj_id,
            layer=int(data.get("layer", "0")),
            frame_start=frame_start,
            frame_end=frame_end,
            focus=data.get("focus", "0") == "1",
        )

        # Find and parse effects for this object
        effect_ids = sorted([
            eff_id for (o_id, eff_id) in effect_sections.keys()
            if o_id == obj_id
        ])

        for effect_id in effect_ids:
            effect_data = effect_sections[(obj_id, effect_id)]
            effect = self._build_effect(effect_id, effect_data)
            obj.effects.append(effect)

        return obj

    def _build_effect(self, effect_id: int, data: dict[str, str]) -> Effect:
        """Build an Effect from section data."""
        effect_name = data.get("effect.name", "")

        # Parse properties (excluding effect.name)
        properties: dict[str, Any] = {}
        for key, value in data.items():
            if key == "effect.name":
                continue
            # Parse value (detect animation vs static)
            properties[key] = parse_property_value(key, value)

        return Effect(
            effect_id=effect_id,
            name=effect_name,
            properties=properties,
        )


def parse_file(file_path: str | Path) -> Project:
    """Parse a .aup2 file and return a Project object.

    This is a convenience function wrapping Aup2Parser.

    Args:
        file_path: Path to the .aup2 file

    Returns:
        Project object representing the file contents
    """
    parser = Aup2Parser()
    return parser.parse_file(file_path)


def parse_string(content: str) -> Project:
    """Parse a .aup2 string and return a Project object.

    Args:
        content: .aup2 file content as string

    Returns:
        Project object representing the content
    """
    parser = Aup2Parser()
    return parser.parse_string(content)
