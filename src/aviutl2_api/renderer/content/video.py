"""Video content renderer using OpenCV."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np
from PIL import Image, ImageDraw

from aviutl2_api.renderer.content.base import ContentRenderer
from aviutl2_api.renderer.interpolation import (
    get_property_string,
    get_property_value_at_frame,
)

if TYPE_CHECKING:
    from aviutl2_api.models import Effect, Scene, TimelineObject


class VideoRenderer(ContentRenderer):
    """Renders video file frames using OpenCV."""

    # Supported video extensions
    EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".wmv", ".flv"}

    def __init__(
        self,
        cache_size: int = 50,
        placeholder_color: tuple[int, int, int] = (80, 80, 80),
    ):
        """Initialize video renderer.

        Args:
            cache_size: Maximum number of frames to cache per video
            placeholder_color: RGB color for placeholder frames
        """
        self._placeholder_color = placeholder_color
        self._video_cache: dict[str, cv2.VideoCapture] = {}
        self._frame_cache: dict[str, np.ndarray] = {}
        self._cache_size = cache_size
        self._missing_files: set[str] = set()
        self._video_info: dict[str, dict] = {}

    def can_render(self, effect_name: str) -> bool:
        """Check if this renderer handles the effect type."""
        return effect_name == "動画ファイル"

    def render(
        self,
        effect: Effect,
        frame: int,
        obj: TimelineObject,
        scene: Scene,
    ) -> tuple[np.ndarray, tuple[int, int]]:
        """Render a video frame.

        Args:
            effect: The video effect
            frame: Current project frame number
            obj: Parent timeline object
            scene: Parent scene

        Returns:
            Tuple of (RGBA image array, (width, height))
        """
        # Get file path (try Japanese key first, fallback to English for compatibility)
        file_path = get_property_string(effect.properties, "ファイル", "")
        if not file_path:
            file_path = get_property_string(effect.properties, "file", "")

        if not file_path:
            img = self._create_placeholder((640, 360), "No file specified")
            return img, (img.shape[1], img.shape[0])

        # Get playback parameters
        play_pos = get_property_value_at_frame(
            effect.properties, "再生位置", frame, obj, 1.0
        )
        play_speed = get_property_value_at_frame(
            effect.properties, "再生速度", frame, obj, 100.0
        )

        # Calculate video frame number
        relative_frame = frame - obj.frame_start
        video_frame = int(play_pos + relative_frame * (play_speed / 100.0))
        video_frame = max(1, video_frame)  # Video frames are 1-indexed in AviUtl

        # Check cache
        cache_key = f"{file_path}:{video_frame}"
        if cache_key in self._frame_cache:
            cached = self._frame_cache[cache_key]
            return cached, (cached.shape[1], cached.shape[0])

        # Check if already known missing
        if file_path in self._missing_files:
            img = self._create_placeholder(
                (640, 360), f"Missing:\n{Path(file_path).name}"
            )
            return img, (img.shape[1], img.shape[0])

        # Get or open video
        cap = self._get_video(file_path)
        if cap is None:
            self._missing_files.add(file_path)
            img = self._create_placeholder(
                (640, 360), f"Cannot open:\n{Path(file_path).name}"
            )
            return img, (img.shape[1], img.shape[0])

        # Get video info
        info = self._video_info.get(file_path, {})
        total_frames = info.get("total_frames", 1)

        # Clamp frame to valid range
        video_frame = max(0, min(video_frame, total_frames - 1))

        # Seek to frame
        cap.set(cv2.CAP_PROP_POS_FRAMES, video_frame)
        ret, bgr_frame = cap.read()

        if not ret or bgr_frame is None:
            img = self._create_placeholder(
                (640, 360), f"Frame {video_frame}\nnot available"
            )
            return img, (img.shape[1], img.shape[0])

        # Convert BGR to RGBA
        rgba = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGBA)

        # Cache the frame
        self._cache_frame(cache_key, rgba)

        return rgba, (rgba.shape[1], rgba.shape[0])

    def _get_video(self, path: str) -> cv2.VideoCapture | None:
        """Get or open a video file.

        Args:
            path: Video file path

        Returns:
            VideoCapture object or None if failed
        """
        if path in self._video_cache:
            return self._video_cache[path]

        # Check if file exists
        if not Path(path).exists():
            return None

        # Open video
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return None

        # Store video info
        self._video_info[path] = {
            "total_frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
            "fps": cap.get(cv2.CAP_PROP_FPS),
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        }

        self._video_cache[path] = cap
        return cap

    def _cache_frame(self, key: str, frame: np.ndarray) -> None:
        """Cache a frame with size limit.

        Args:
            key: Cache key
            frame: Frame data
        """
        # Simple cache eviction: remove oldest when limit reached
        if len(self._frame_cache) >= self._cache_size:
            # Remove first item
            first_key = next(iter(self._frame_cache))
            del self._frame_cache[first_key]

        self._frame_cache[key] = frame

    def _create_placeholder(
        self,
        size: tuple[int, int],
        text: str,
    ) -> np.ndarray:
        """Create a placeholder frame with message.

        Args:
            size: (width, height) of placeholder
            text: Text to display

        Returns:
            RGBA numpy array
        """
        width, height = size
        img = Image.new("RGBA", (width, height), (*self._placeholder_color, 255))
        draw = ImageDraw.Draw(img)

        # Draw film strip pattern
        strip_height = 20
        hole_width = 10
        hole_height = 8
        hole_spacing = 15

        # Top strip
        draw.rectangle([0, 0, width, strip_height], fill=(40, 40, 40, 255))
        # Bottom strip
        draw.rectangle(
            [0, height - strip_height, width, height], fill=(40, 40, 40, 255)
        )

        # Sprocket holes
        x = 5
        while x < width:
            draw.rectangle(
                [x, 5, x + hole_width, 5 + hole_height], fill=(100, 100, 100, 255)
            )
            draw.rectangle(
                [x, height - 5 - hole_height, x + hole_width, height - 5],
                fill=(100, 100, 100, 255),
            )
            x += hole_spacing + hole_width

        # Draw text
        try:
            from PIL import ImageFont

            font = ImageFont.load_default()
        except Exception:
            font = None

        lines = text.split("\n")
        y = height // 2 - len(lines) * 8

        for line in lines:
            try:
                bbox = draw.textbbox((0, 0), line, font=font)
                tw = bbox[2] - bbox[0]
                x = (width - tw) // 2
            except Exception:
                x = 10

            draw.text((x, y), line, fill=(255, 255, 255, 255), font=font)
            y += 16

        return np.array(img)

    def cleanup(self) -> None:
        """Release all video capture resources."""
        for cap in self._video_cache.values():
            cap.release()
        self._video_cache.clear()
        self._frame_cache.clear()

    def __del__(self):
        """Cleanup on deletion."""
        self.cleanup()
