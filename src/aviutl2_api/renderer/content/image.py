"""Image content renderer for image files."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw

from aviutl2_api.renderer.content.base import ContentRenderer
from aviutl2_api.renderer.interpolation import get_property_string

if TYPE_CHECKING:
    from aviutl2_api.models import Effect, Scene, TimelineObject


class ImageRenderer(ContentRenderer):
    """Renders image file objects."""

    # Supported image extensions
    EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"}

    def __init__(self, placeholder_color: tuple[int, int, int] = (128, 128, 128)):
        """Initialize image renderer.

        Args:
            placeholder_color: RGB color for placeholder images
        """
        self._placeholder_color = placeholder_color
        self._cache: dict[str, np.ndarray] = {}
        self._missing_files: set[str] = set()

    def can_render(self, effect_name: str) -> bool:
        """Check if this renderer handles the effect type."""
        return effect_name == "画像ファイル"

    def render(
        self,
        effect: Effect,
        frame: int,
        obj: TimelineObject,
        scene: Scene,
    ) -> tuple[np.ndarray, tuple[int, int]]:
        """Render an image file.

        Args:
            effect: The image effect
            frame: Current frame number
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
            img = self._create_placeholder((200, 200), "No file specified")
            return img, (img.shape[1], img.shape[0])

        # Check cache
        if file_path in self._cache:
            cached = self._cache[file_path]
            return cached, (cached.shape[1], cached.shape[0])

        # Check if already known missing
        if file_path in self._missing_files:
            img = self._create_placeholder(
                (200, 200), f"Missing:\n{Path(file_path).name}"
            )
            return img, (img.shape[1], img.shape[0])

        # Try to load image
        path = Path(file_path)
        if not path.exists():
            self._missing_files.add(file_path)
            img = self._create_placeholder(
                (200, 200), f"Not found:\n{path.name}"
            )
            return img, (img.shape[1], img.shape[0])

        try:
            pil_img = Image.open(path).convert("RGBA")
            img = np.array(pil_img)

            # Cache the image
            self._cache[file_path] = img

            return img, (img.shape[1], img.shape[0])

        except Exception as e:
            self._missing_files.add(file_path)
            img = self._create_placeholder(
                (200, 200), f"Error:\n{path.name}\n{str(e)[:20]}"
            )
            return img, (img.shape[1], img.shape[0])

    def _create_placeholder(
        self,
        size: tuple[int, int],
        text: str,
    ) -> np.ndarray:
        """Create a placeholder image with error message.

        Args:
            size: (width, height) of placeholder
            text: Text to display

        Returns:
            RGBA numpy array
        """
        width, height = size
        img = Image.new("RGBA", (width, height), (*self._placeholder_color, 200))
        draw = ImageDraw.Draw(img)

        # Draw X pattern
        draw.line([(0, 0), (width, height)], fill=(200, 50, 50, 255), width=2)
        draw.line([(width, 0), (0, height)], fill=(200, 50, 50, 255), width=2)

        # Draw border
        draw.rectangle([0, 0, width - 1, height - 1], outline=(100, 100, 100, 255))

        # Draw text
        try:
            from PIL import ImageFont

            font = ImageFont.load_default()
        except Exception:
            font = None

        # Center text
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

    def clear_cache(self) -> None:
        """Clear the image cache."""
        self._cache.clear()
        self._missing_files.clear()
