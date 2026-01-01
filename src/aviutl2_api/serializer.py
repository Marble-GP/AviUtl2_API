"""Serializer for AviUtl2 project files (.aup2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TextIO

from aviutl2_api.models import (
    AnimatedValue,
    Effect,
    Project,
    Scene,
    StaticValue,
    TimelineObject,
)


class Aup2Serializer:
    """Serializer to convert Project objects to .aup2 format."""

    LINE_ENDING = "\r\n"  # CRLF as per spec

    def __init__(self):
        self._lines: list[str] = []

    def serialize(self, project: Project) -> str:
        """Serialize a Project to .aup2 format string."""
        self._lines = []

        # Write [project] section
        self._write_project_section(project)

        # Write [scene.N] sections
        for scene in project.scenes:
            self._write_scene_section(scene)

        # Write object and effect sections
        for scene in project.scenes:
            for obj in scene.objects:
                self._write_object_section(obj)

        # Join with CRLF
        return self.LINE_ENDING.join(self._lines) + self.LINE_ENDING

    def serialize_to_file(self, project: Project, file_path: str | Path) -> None:
        """Serialize a Project and write to a file."""
        content = self.serialize(project)
        path = Path(file_path)
        path.write_text(content, encoding="utf-8", newline="")

    def _write_line(self, line: str) -> None:
        """Add a line to output."""
        self._lines.append(line)

    def _write_section(self, name: str) -> None:
        """Write a section header."""
        self._write_line(f"[{name}]")

    def _write_kv(self, key: str, value: Any) -> None:
        """Write a key=value line."""
        value_str = self._format_value(value)
        self._write_line(f"{key}={value_str}")

    def _format_value(self, value: Any) -> str:
        """Format a value for output."""
        if isinstance(value, StaticValue):
            return value.to_aup2()
        elif isinstance(value, AnimatedValue):
            return value.to_aup2()
        elif isinstance(value, bool):
            return "1" if value else "0"
        elif isinstance(value, float):
            return self._format_float(value)
        elif isinstance(value, int):
            return str(value)
        else:
            return str(value)

    def _format_float(self, v: float, decimals: int = 6) -> str:
        """Format a float value with fixed decimal places."""
        # Keep all decimal places to match original AviUtl format
        return f"{v:.{decimals}f}"

    def _write_project_section(self, project: Project) -> None:
        """Write [project] section."""
        self._write_section("project")
        self._write_kv("version", project.version)
        self._write_kv("ファイル", project.file_path)
        self._write_kv("display.scene", project.display_scene)

    def _write_scene_section(self, scene: Scene) -> None:
        """Write [scene.N] section."""
        self._write_section(f"scene.{scene.scene_id}")
        self._write_kv("scene", scene.scene_id)
        self._write_kv("name", scene.name)
        self._write_kv("video.width", scene.width)
        self._write_kv("video.height", scene.height)
        self._write_kv("video.rate", scene.fps)
        self._write_kv("video.scale", scene.video_scale)
        self._write_kv("audio.rate", scene.audio_rate)
        self._write_kv("cursor.frame", scene.cursor_frame)
        self._write_kv("cursor.layer", scene.cursor_layer)
        self._write_kv("display.frame", scene.display_frame)
        self._write_kv("display.layer", scene.display_layer)
        self._write_kv("display.zoom", scene.display_zoom)
        self._write_kv("display.order", scene.display_order)
        self._write_kv("display.camera", scene.display_camera)
        self._write_kv("display.grid.x", scene.grid_x)
        self._write_kv("display.grid.y", scene.grid_y)
        self._write_kv("display.grid.width", scene.grid_width)
        self._write_kv("display.grid.height", scene.grid_height)
        self._write_kv("display.grid.step", self._format_float(scene.grid_step))
        self._write_kv("display.grid.range", self._format_float(scene.grid_range))
        self._write_kv("display.tempo.bpm", self._format_float(scene.tempo_bpm))
        self._write_kv("display.tempo.beat", scene.tempo_beat)
        self._write_kv("display.tempo.offset", self._format_float(scene.tempo_offset))

    def _write_object_section(self, obj: TimelineObject) -> None:
        """Write [ObjectID] and [ObjectID.EffectID] sections."""
        # Object header
        self._write_section(str(obj.object_id))
        self._write_kv("layer", obj.layer)
        if obj.focus:
            self._write_kv("focus", 1)
        self._write_kv("frame", f"{obj.frame_start},{obj.frame_end}")

        # Effects
        for effect in obj.effects:
            self._write_effect_section(obj.object_id, effect)

    def _write_effect_section(self, obj_id: int, effect: Effect) -> None:
        """Write [ObjectID.EffectID] section."""
        self._write_section(f"{obj_id}.{effect.effect_id}")
        self._write_kv("effect.name", effect.name)

        # Write properties in a consistent order
        for key, value in effect.properties.items():
            self._write_kv(key, value)


def serialize(project: Project) -> str:
    """Serialize a Project to .aup2 format string.

    This is a convenience function wrapping Aup2Serializer.

    Args:
        project: Project object to serialize

    Returns:
        .aup2 format string
    """
    serializer = Aup2Serializer()
    return serializer.serialize(project)


def serialize_to_file(project: Project, file_path: str | Path) -> None:
    """Serialize a Project and write to a file.

    Args:
        project: Project object to serialize
        file_path: Path to write the .aup2 file
    """
    serializer = Aup2Serializer()
    serializer.serialize_to_file(project, file_path)
