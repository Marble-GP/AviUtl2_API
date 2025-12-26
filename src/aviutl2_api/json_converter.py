"""JSON conversion for AviUtl2 projects.

Provides bidirectional conversion between Project objects and JSON,
enabling LLM-friendly data interchange.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aviutl2_api.models import (
    AnimatedValue,
    AnimationParams,
    Effect,
    Project,
    Scene,
    StaticValue,
    TimelineObject,
)


class JsonConverter:
    """Converter between Project objects and JSON format."""

    def to_dict(self, project: Project) -> dict[str, Any]:
        """Convert a Project to a dictionary (JSON-serializable)."""
        return {
            "version": project.version,
            "file_path": project.file_path,
            "display_scene": project.display_scene,
            "scenes": [self._scene_to_dict(scene) for scene in project.scenes],
        }

    def to_json(self, project: Project, indent: int = 2) -> str:
        """Convert a Project to a JSON string."""
        data = self.to_dict(project)
        return json.dumps(data, indent=indent, ensure_ascii=False)

    def to_json_file(
        self,
        project: Project,
        file_path: str | Path,
        indent: int = 2
    ) -> None:
        """Write a Project to a JSON file."""
        content = self.to_json(project, indent)
        Path(file_path).write_text(content, encoding="utf-8")

    def from_dict(self, data: dict[str, Any]) -> Project:
        """Create a Project from a dictionary."""
        project = Project(
            version=data.get("version", 2001901),
            file_path=data.get("file_path", ""),
            display_scene=data.get("display_scene", 0),
        )

        for scene_data in data.get("scenes", []):
            scene = self._dict_to_scene(scene_data)
            project.scenes.append(scene)

        return project

    def from_json(self, json_str: str) -> Project:
        """Create a Project from a JSON string."""
        data = json.loads(json_str)
        return self.from_dict(data)

    def from_json_file(self, file_path: str | Path) -> Project:
        """Load a Project from a JSON file."""
        content = Path(file_path).read_text(encoding="utf-8")
        return self.from_json(content)

    def _scene_to_dict(self, scene: Scene) -> dict[str, Any]:
        """Convert a Scene to a dictionary."""
        return {
            "scene_id": scene.scene_id,
            "name": scene.name,
            "width": scene.width,
            "height": scene.height,
            "fps": scene.fps,
            "video_scale": scene.video_scale,
            "audio_rate": scene.audio_rate,
            "cursor": {
                "frame": scene.cursor_frame,
                "layer": scene.cursor_layer,
            },
            "display": {
                "frame": scene.display_frame,
                "layer": scene.display_layer,
                "zoom": scene.display_zoom,
                "order": scene.display_order,
                "camera": scene.display_camera,
            },
            "grid": {
                "x": scene.grid_x,
                "y": scene.grid_y,
                "width": scene.grid_width,
                "height": scene.grid_height,
                "step": scene.grid_step,
                "range": scene.grid_range,
            },
            "tempo": {
                "bpm": scene.tempo_bpm,
                "beat": scene.tempo_beat,
                "offset": scene.tempo_offset,
            },
            "objects": [self._object_to_dict(obj) for obj in scene.objects],
        }

    def _dict_to_scene(self, data: dict[str, Any]) -> Scene:
        """Create a Scene from a dictionary."""
        cursor = data.get("cursor", {})
        display = data.get("display", {})
        grid = data.get("grid", {})
        tempo = data.get("tempo", {})

        scene = Scene(
            scene_id=data.get("scene_id", 0),
            name=data.get("name", "Root"),
            width=data.get("width", 1920),
            height=data.get("height", 1080),
            fps=data.get("fps", 30),
            video_scale=data.get("video_scale", 1),
            audio_rate=data.get("audio_rate", 44100),
            cursor_frame=cursor.get("frame", 0),
            cursor_layer=cursor.get("layer", 0),
            display_frame=display.get("frame", 0),
            display_layer=display.get("layer", 0),
            display_zoom=display.get("zoom", 10000),
            display_order=display.get("order", 0),
            display_camera=display.get("camera", ""),
            grid_x=grid.get("x", "16,-16"),
            grid_y=grid.get("y", "16,-16"),
            grid_width=grid.get("width", 200),
            grid_height=grid.get("height", 200),
            grid_step=grid.get("step", 200.0),
            grid_range=grid.get("range", 10000.0),
            tempo_bpm=tempo.get("bpm", 120.0),
            tempo_beat=tempo.get("beat", 4),
            tempo_offset=tempo.get("offset", 0.0),
        )

        for obj_data in data.get("objects", []):
            obj = self._dict_to_object(obj_data)
            scene.objects.append(obj)

        return scene

    def _object_to_dict(self, obj: TimelineObject) -> dict[str, Any]:
        """Convert a TimelineObject to a dictionary."""
        return {
            "object_id": obj.object_id,
            "layer": obj.layer,
            "frame_start": obj.frame_start,
            "frame_end": obj.frame_end,
            "focus": obj.focus,
            "type": obj.object_type,
            "effects": [self._effect_to_dict(eff) for eff in obj.effects],
        }

    def _dict_to_object(self, data: dict[str, Any]) -> TimelineObject:
        """Create a TimelineObject from a dictionary."""
        obj = TimelineObject(
            object_id=data.get("object_id", 0),
            layer=data.get("layer", 0),
            frame_start=data.get("frame_start", 0),
            frame_end=data.get("frame_end", 0),
            focus=data.get("focus", False),
        )

        for eff_data in data.get("effects", []):
            effect = self._dict_to_effect(eff_data)
            obj.effects.append(effect)

        return obj

    def _effect_to_dict(self, effect: Effect) -> dict[str, Any]:
        """Convert an Effect to a dictionary."""
        properties: dict[str, Any] = {}

        for key, value in effect.properties.items():
            properties[key] = self._value_to_dict(value)

        return {
            "effect_id": effect.effect_id,
            "name": effect.name,
            "properties": properties,
        }

    def _dict_to_effect(self, data: dict[str, Any]) -> Effect:
        """Create an Effect from a dictionary."""
        properties: dict[str, Any] = {}

        for key, value in data.get("properties", {}).items():
            properties[key] = self._dict_to_value(value)

        return Effect(
            effect_id=data.get("effect_id", 0),
            name=data.get("name", ""),
            properties=properties,
        )

    def _value_to_dict(self, value: Any) -> Any:
        """Convert a property value to JSON-serializable format."""
        if isinstance(value, StaticValue):
            return {"type": "static", "value": value.value}
        elif isinstance(value, AnimatedValue):
            return {
                "type": "animated",
                "start": value.start,
                "end": value.end,
                "motion_type": value.animation.motion_type,
                "param": value.animation.param,
            }
        else:
            # String or other primitive
            return value

    def _dict_to_value(self, data: Any) -> Any:
        """Convert a JSON value back to a property value."""
        if isinstance(data, dict):
            value_type = data.get("type")
            if value_type == "static":
                return StaticValue(value=data["value"])
            elif value_type == "animated":
                return AnimatedValue(
                    start=data["start"],
                    end=data["end"],
                    animation=AnimationParams(
                        motion_type=data["motion_type"],
                        param=data.get("param", ""),
                    ),
                )
        return data


# Convenience functions

def to_json(project: Project, indent: int = 2) -> str:
    """Convert a Project to a JSON string."""
    return JsonConverter().to_json(project, indent)


def from_json(json_str: str) -> Project:
    """Create a Project from a JSON string."""
    return JsonConverter().from_json(json_str)


def to_dict(project: Project) -> dict[str, Any]:
    """Convert a Project to a dictionary."""
    return JsonConverter().to_dict(project)


def from_dict(data: dict[str, Any]) -> Project:
    """Create a Project from a dictionary."""
    return JsonConverter().from_dict(data)
