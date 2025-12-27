"""Text content renderer using PIL/Pillow."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from aviutl2_api.renderer.canvas import parse_hex_color
from aviutl2_api.renderer.content.base import ContentRenderer
from aviutl2_api.renderer.interpolation import (
    get_property_string,
    get_property_value_at_frame,
)

if TYPE_CHECKING:
    from aviutl2_api.models import Effect, Scene, TimelineObject


class TextRenderer(ContentRenderer):
    """Renders text objects using PIL/Pillow fonts."""

    # Common system font directories
    FONT_DIRS = [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".fonts",
        Path.home() / ".local/share/fonts",
        # Windows fonts (via WSL)
        Path("/mnt/c/Windows/Fonts"),
        # macOS
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
    ]

    # Fallback font mapping
    FONT_ALIASES: dict[str, list[str]] = {
        "Yu Gothic UI": [
            "YuGothic-Regular.ttf",
            "yugothic.ttf",
            "msgothic.ttc",
            "NotoSansJP-Regular.ttf",
            "NotoSansCJK-Regular.ttc",
        ],
        "メイリオ": [
            "meiryo.ttc",
            "Meiryo.ttf",
            "NotoSansJP-Regular.ttf",
        ],
        "MS Gothic": [
            "msgothic.ttc",
            "NotoSansJP-Regular.ttf",
        ],
        "ＭＳ ゴシック": [
            "msgothic.ttc",
            "NotoSansJP-Regular.ttf",
        ],
    }

    def __init__(self, font_dirs: list[Path] | None = None):
        """Initialize text renderer.

        Args:
            font_dirs: Additional font directories to search
        """
        self._font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        self._font_dirs = list(self.FONT_DIRS)
        if font_dirs:
            self._font_dirs.extend(font_dirs)

    def can_render(self, effect_name: str) -> bool:
        """Check if this renderer handles the effect type."""
        return effect_name == "テキスト"

    def render(
        self,
        effect: Effect,
        frame: int,
        obj: TimelineObject,
        scene: Scene,
    ) -> tuple[np.ndarray, tuple[int, int]]:
        """Render text.

        Args:
            effect: The text effect
            frame: Current frame number
            obj: Parent timeline object
            scene: Parent scene

        Returns:
            Tuple of (RGBA image array, (width, height))
        """
        # Get text properties
        text = get_property_string(effect.properties, "テキスト", "")
        font_name = get_property_string(effect.properties, "フォント", "Yu Gothic UI")
        size = get_property_value_at_frame(
            effect.properties, "サイズ", frame, obj, 34.0
        )
        letter_spacing = get_property_value_at_frame(
            effect.properties, "字間", frame, obj, 0.0
        )
        line_spacing = get_property_value_at_frame(
            effect.properties, "行間", frame, obj, 0.0
        )
        text_color = get_property_string(effect.properties, "文字色", "ffffff")
        shadow_color = get_property_string(effect.properties, "影・縁色", "000000")
        decoration = get_property_string(effect.properties, "文字装飾", "標準文字")
        bold = get_property_value_at_frame(effect.properties, "B", frame, obj, 0.0)
        italic = get_property_value_at_frame(effect.properties, "I", frame, obj, 0.0)

        if not text:
            # Return empty 1x1 transparent image
            return np.zeros((1, 1, 4), dtype=np.uint8), (1, 1)

        # Parse colors
        r, g, b = parse_hex_color(text_color)
        sr, sg, sb = parse_hex_color(shadow_color)

        # Get font
        font_size = max(8, int(size))
        font = self._get_font(font_name, font_size)

        # Replace literal \n with newlines
        text = text.replace("\\n", "\n")

        # Render text
        img = self._render_text(
            text=text,
            font=font,
            color=(r, g, b),
            shadow_color=(sr, sg, sb),
            decoration=decoration,
            letter_spacing=letter_spacing,
            line_spacing=line_spacing,
        )

        return img, (img.shape[1], img.shape[0])

    def _get_font(
        self,
        font_name: str,
        size: int,
    ) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Get a font, with caching.

        Args:
            font_name: Font family name
            size: Font size in points

        Returns:
            PIL font object
        """
        cache_key = (font_name, size)
        if cache_key in self._font_cache:
            return self._font_cache[cache_key]

        # Try to find font file
        font = self._find_font(font_name, size)
        if font:
            self._font_cache[cache_key] = font
            return font

        # Fallback to default font
        try:
            font = ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()

        return font

    def _find_font(
        self,
        font_name: str,
        size: int,
    ) -> ImageFont.FreeTypeFont | None:
        """Search for a font file.

        Args:
            font_name: Font family name
            size: Font size

        Returns:
            Font object or None if not found
        """
        # Get candidate filenames
        candidates = self.FONT_ALIASES.get(font_name, [])
        candidates.append(f"{font_name}.ttf")
        candidates.append(f"{font_name}.ttc")
        candidates.append(f"{font_name}.otf")

        # Search directories
        for font_dir in self._font_dirs:
            if not font_dir.exists():
                continue

            for candidate in candidates:
                # Try direct match
                font_path = font_dir / candidate
                if font_path.exists():
                    try:
                        return ImageFont.truetype(str(font_path), size)
                    except Exception:
                        continue

                # Try recursive search
                try:
                    for path in font_dir.rglob(candidate):
                        try:
                            return ImageFont.truetype(str(path), size)
                        except Exception:
                            continue
                except Exception:
                    continue

        # Try common fallback fonts
        fallbacks = [
            "DejaVuSans.ttf",
            "NotoSansCJK-Regular.ttc",
            "NotoSansJP-Regular.ttf",
            "arial.ttf",
        ]

        for font_dir in self._font_dirs:
            if not font_dir.exists():
                continue

            for fallback in fallbacks:
                try:
                    for path in font_dir.rglob(fallback):
                        try:
                            return ImageFont.truetype(str(path), size)
                        except Exception:
                            continue
                except Exception:
                    continue

        return None

    def _render_text(
        self,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        color: tuple[int, int, int],
        shadow_color: tuple[int, int, int],
        decoration: str,
        letter_spacing: float,
        line_spacing: float,
    ) -> np.ndarray:
        """Render text to an RGBA array.

        Args:
            text: Text to render
            font: Font to use
            color: Text color RGB
            shadow_color: Shadow/outline color RGB
            decoration: Text decoration style
            letter_spacing: Additional spacing between characters
            line_spacing: Additional spacing between lines

        Returns:
            RGBA numpy array
        """
        # Calculate text size
        # Create a temporary image to measure
        temp_img = Image.new("RGBA", (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)

        # Handle multiline
        lines = text.split("\n")

        # Get font metrics
        try:
            bbox = temp_draw.textbbox((0, 0), "Ay", font=font)
            line_height = bbox[3] - bbox[1]
        except Exception:
            line_height = 20

        # Calculate total size
        max_width = 0
        total_height = 0

        for i, line in enumerate(lines):
            if line:
                try:
                    bbox = temp_draw.textbbox((0, 0), line, font=font)
                    w = bbox[2] - bbox[0] + int(letter_spacing * len(line))
                    max_width = max(max_width, w)
                except Exception:
                    max_width = max(max_width, len(line) * 10)

            total_height += line_height + int(line_spacing)

        # Add padding
        padding = 8
        width = max(1, max_width + padding * 2)
        height = max(1, total_height + padding * 2)

        # Increase size for decorations
        if decoration in ("影付き文字", "影付き"):
            width += 4
            height += 4
        elif decoration in ("縁取り文字", "縁取り"):
            width += 6
            height += 6

        # Create image
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Draw text
        y = padding

        for line in lines:
            x = padding

            if decoration in ("影付き文字", "影付き"):
                # Draw shadow
                draw.text(
                    (x + 2, y + 2),
                    line,
                    font=font,
                    fill=(*shadow_color, 200),
                )
                draw.text((x, y), line, font=font, fill=(*color, 255))

            elif decoration in ("縁取り文字", "縁取り"):
                # Draw outline
                for dx in [-1, 0, 1]:
                    for dy in [-1, 0, 1]:
                        if dx != 0 or dy != 0:
                            draw.text(
                                (x + dx, y + dy),
                                line,
                                font=font,
                                fill=(*shadow_color, 255),
                            )
                draw.text((x, y), line, font=font, fill=(*color, 255))

            else:
                # Standard text
                draw.text((x, y), line, font=font, fill=(*color, 255))

            y += line_height + int(line_spacing)

        return np.array(img)
