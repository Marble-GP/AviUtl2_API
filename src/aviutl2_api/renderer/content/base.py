"""Base class for content type renderers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from aviutl2_api.models import Effect, Scene, TimelineObject


class ContentRenderer(ABC):
    """Abstract base class for content type renderers.

    Content renderers are responsible for generating the visual content
    of different object types (shapes, text, images, video, etc.).
    """

    @abstractmethod
    def can_render(self, effect_name: str) -> bool:
        """Check if this renderer handles the specified effect type.

        Args:
            effect_name: The effect name (e.g., "図形", "テキスト")

        Returns:
            True if this renderer can handle the effect type
        """
        pass

    @abstractmethod
    def render(
        self,
        effect: Effect,
        frame: int,
        obj: TimelineObject,
        scene: Scene,
    ) -> tuple[np.ndarray, tuple[int, int]]:
        """Render the content.

        Args:
            effect: The effect to render
            frame: Current frame number
            obj: Parent timeline object
            scene: Parent scene

        Returns:
            Tuple of (RGBA image array, (width, height))
        """
        pass

    def get_content_size(
        self,
        effect: Effect,
        scene: Scene,
    ) -> tuple[int, int]:
        """Get the natural size of the content (optional).

        Args:
            effect: The effect
            scene: Parent scene

        Returns:
            Tuple of (width, height). Default implementation returns (100, 100).
        """
        return (100, 100)


class RenderContext:
    """Context information passed to renderers.

    This class holds shared state and resources during rendering.
    """

    def __init__(
        self,
        scene_width: int,
        scene_height: int,
        scene_fps: int,
    ):
        """Initialize render context.

        Args:
            scene_width: Scene canvas width in pixels
            scene_height: Scene canvas height in pixels
            scene_fps: Scene frame rate
        """
        self.scene_width = scene_width
        self.scene_height = scene_height
        self.scene_fps = scene_fps

        # Cache for media files
        self._media_cache: dict[str, np.ndarray] = {}

        # Missing file tracking
        self._missing_files: set[str] = set()

        # Warnings and errors
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def cache_media(self, path: str, data: np.ndarray) -> None:
        """Cache a loaded media file.

        Args:
            path: File path
            data: Loaded image/frame data
        """
        self._media_cache[path] = data

    def get_cached_media(self, path: str) -> np.ndarray | None:
        """Get cached media data.

        Args:
            path: File path

        Returns:
            Cached data or None if not cached
        """
        return self._media_cache.get(path)

    def mark_missing_file(self, path: str) -> None:
        """Mark a file as missing.

        Args:
            path: File path
        """
        self._missing_files.add(path)

    def is_file_missing(self, path: str) -> bool:
        """Check if a file is marked as missing.

        Args:
            path: File path

        Returns:
            True if file was previously marked as missing
        """
        return path in self._missing_files

    def add_warning(self, message: str) -> None:
        """Add a warning message.

        Args:
            message: Warning message
        """
        self.warnings.append(message)

    def add_error(self, message: str) -> None:
        """Add an error message.

        Args:
            message: Error message
        """
        self.errors.append(message)

    @property
    def missing_files(self) -> list[str]:
        """Get list of missing files."""
        return list(self._missing_files)
