"""Core data models for AviUtl2 project structure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Effect:
    """An effect/component attached to a timeline object.

    Represents [ObjectID.EffectID] sections in .aup2 files.
    """

    effect_id: int
    name: str  # effect.name value
    properties: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a property value."""
        return self.properties.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a property value."""
        self.properties[key] = value


@dataclass
class TimelineObject:
    """A timeline object (clip) on a layer.

    Represents [ObjectID] sections in .aup2 files.
    """

    object_id: int
    layer: int
    frame_start: int
    frame_end: int
    effects: list[Effect] = field(default_factory=list)
    focus: bool = False

    @property
    def duration_frames(self) -> int:
        """Get duration in frames (inclusive)."""
        return self.frame_end - self.frame_start + 1

    def duration_seconds(self, fps: int) -> float:
        """Get duration in seconds."""
        return self.duration_frames / fps

    def get_effect(self, name: str) -> Effect | None:
        """Get the first effect with the given name."""
        for effect in self.effects:
            if effect.name == name:
                return effect
        return None

    def get_effects(self, name: str) -> list[Effect]:
        """Get all effects with the given name."""
        return [e for e in self.effects if e.name == name]

    @property
    def main_effect(self) -> Effect | None:
        """Get the main effect (first one, typically the media/content type)."""
        return self.effects[0] if self.effects else None

    @property
    def object_type(self) -> str | None:
        """Get the object type based on main effect name."""
        main = self.main_effect
        return main.name if main else None


@dataclass
class Scene:
    """A scene containing timeline objects.

    Represents [scene.N] sections in .aup2 files.
    """

    scene_id: int
    name: str = "Root"
    width: int = 1920
    height: int = 1080
    fps: int = 30
    video_scale: int = 1
    audio_rate: int = 44100
    objects: list[TimelineObject] = field(default_factory=list)

    # Cursor/display state (editor state, not content)
    cursor_frame: int = 0
    cursor_layer: int = 0
    display_frame: int = 0
    display_layer: int = 0
    display_zoom: int = 10000
    display_order: int = 0
    display_camera: str = ""

    # Grid settings
    grid_x: str = "16,-16"
    grid_y: str = "16,-16"
    grid_width: int = 200
    grid_height: int = 200
    grid_step: float = 200.0
    grid_range: float = 10000.0

    # Tempo settings
    tempo_bpm: float = 120.0
    tempo_beat: int = 4
    tempo_offset: float = 0.0

    def get_objects_at_frame(self, frame: int) -> list[TimelineObject]:
        """Get all objects visible at a specific frame."""
        return [
            obj for obj in self.objects
            if obj.frame_start <= frame <= obj.frame_end
        ]

    def get_objects_on_layer(self, layer: int) -> list[TimelineObject]:
        """Get all objects on a specific layer."""
        return [obj for obj in self.objects if obj.layer == layer]

    @property
    def max_frame(self) -> int:
        """Get the last frame with content."""
        if not self.objects:
            return 0
        return max(obj.frame_end for obj in self.objects)

    @property
    def max_layer(self) -> int:
        """Get the highest layer number with content."""
        if not self.objects:
            return 0
        return max(obj.layer for obj in self.objects)


@dataclass
class Project:
    """Root container for an AviUtl2 project.

    Represents the entire .aup2 file structure.
    """

    version: int = 2001901
    file_path: str = ""
    display_scene: int = 0
    scenes: list[Scene] = field(default_factory=list)

    @property
    def current_scene(self) -> Scene | None:
        """Get the currently displayed scene."""
        for scene in self.scenes:
            if scene.scene_id == self.display_scene:
                return scene
        return self.scenes[0] if self.scenes else None

    def get_scene(self, scene_id: int) -> Scene | None:
        """Get a scene by ID."""
        for scene in self.scenes:
            if scene.scene_id == scene_id:
                return scene
        return None

    def add_scene(self, scene: Scene) -> None:
        """Add a scene to the project."""
        self.scenes.append(scene)

    @classmethod
    def create_empty(
        cls,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        file_path: str = ""
    ) -> Project:
        """Create an empty project with default settings."""
        project = cls(file_path=file_path)
        scene = Scene(
            scene_id=0,
            name="Root",
            width=width,
            height=height,
            fps=fps
        )
        project.add_scene(scene)
        return project
