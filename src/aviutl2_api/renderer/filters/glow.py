"""Glow filter effect (グロー)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np

from aviutl2_api.renderer.canvas import parse_hex_color
from aviutl2_api.renderer.filters.base import FilterEffect
from aviutl2_api.renderer.interpolation import (
    get_property_string,
    get_property_value_at_frame,
)

if TYPE_CHECKING:
    from aviutl2_api.models import Effect, TimelineObject


class GlowFilter(FilterEffect):
    """Glow filter (グロー).

    Creates a light bloom effect around bright areas of the image.
    """

    def can_apply(self, effect_name: str) -> bool:
        """Check if this filter handles the effect type."""
        return effect_name == "グロー"

    def apply(
        self,
        image: np.ndarray,
        effect: Effect,
        frame: int,
        obj: TimelineObject,
    ) -> np.ndarray:
        """Apply glow effect to the image.

        Args:
            image: Source RGBA image
            effect: Glow effect parameters
            frame: Current frame
            obj: Parent timeline object

        Returns:
            Image with glow effect
        """
        # Get glow parameters
        strength = get_property_value_at_frame(
            effect.properties, "強さ", frame, obj, 50.0
        )
        diffusion = get_property_value_at_frame(
            effect.properties, "拡散", frame, obj, 60.0
        )
        threshold = get_property_value_at_frame(
            effect.properties, "しきい値", frame, obj, 50.0
        )
        ratio = get_property_value_at_frame(
            effect.properties, "比率", frame, obj, 100.0
        )
        blur_amount = get_property_value_at_frame(
            effect.properties, "ぼかし", frame, obj, 1.0
        )
        glow_color_hex = get_property_string(effect.properties, "光色", "")
        light_only = get_property_value_at_frame(
            effect.properties, "光成分のみ", frame, obj, 0.0
        )

        if strength <= 0:
            return image

        h, w = image.shape[:2]

        # Parse glow color (empty = use image colors)
        use_custom_color = bool(glow_color_hex)
        if use_custom_color:
            gr, gg, gb = parse_hex_color(glow_color_hex)

        # Create glow mask based on brightness threshold
        # Convert to grayscale for luminance calculation
        rgb = image[:, :, :3].astype(np.float32)
        alpha = image[:, :, 3].astype(np.float32) / 255.0

        # Calculate luminance
        luminance = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]

        # Apply threshold (0-255 scale, threshold is 0-100)
        threshold_value = threshold * 2.55  # Convert to 0-255
        glow_mask = np.clip(luminance - threshold_value, 0, 255) / (255 - threshold_value + 1)
        glow_mask = glow_mask * alpha  # Apply alpha

        # Create glow layer (always 4 channels)
        glow = np.zeros((h, w, 4), dtype=np.float32)
        if use_custom_color:
            glow[:, :, 0] = gr
            glow[:, :, 1] = gg
            glow[:, :, 2] = gb
        else:
            glow[:, :, :3] = rgb

        # Apply glow mask to RGB
        for c in range(3):
            glow[:, :, c] = glow[:, :, c] * glow_mask

        glow[:, :, 3] = glow_mask * 255

        # Apply blur for diffusion
        blur_size = int(diffusion / 5) * 2 + 1
        blur_size = max(3, blur_size)

        if blur_amount > 0:
            glow = cv2.GaussianBlur(glow, (blur_size, blur_size), diffusion / 10)

        # Adjust strength
        glow[:, :, :3] = glow[:, :, :3] * (strength / 50.0) * (ratio / 100.0)
        glow = np.clip(glow, 0, 255).astype(np.uint8)

        # Add padding for glow expansion
        padding = blur_size
        result = np.zeros((h + padding * 2, w + padding * 2, 4), dtype=np.uint8)

        # Place glow in center
        glow_padded = np.zeros((h + padding * 2, w + padding * 2, 4), dtype=np.float32)
        glow_padded[padding : padding + h, padding : padding + w] = glow.astype(
            np.float32
        )

        # Blur the padded glow
        glow_padded = cv2.GaussianBlur(
            glow_padded, (blur_size, blur_size), diffusion / 10
        )

        if light_only > 0:
            # Return only the glow component
            result = np.clip(glow_padded, 0, 255).astype(np.uint8)
        else:
            # Composite: glow behind original using additive blend
            result = glow_padded.copy()

            # Place original on top
            orig_alpha = image[:, :, 3].astype(np.float32) / 255.0

            for c in range(3):
                # Additive blend for glow
                result[padding : padding + h, padding : padding + w, c] = np.clip(
                    result[padding : padding + h, padding : padding + w, c]
                    + image[:, :, c].astype(np.float32) * orig_alpha,
                    0,
                    255,
                )

            # Max alpha
            result[padding : padding + h, padding : padding + w, 3] = np.maximum(
                result[padding : padding + h, padding : padding + w, 3],
                image[:, :, 3].astype(np.float32),
            )

            result = np.clip(result, 0, 255).astype(np.uint8)

        return result

    def get_padding(self, effect: Effect) -> int:
        """Get padding needed for glow effect."""
        from aviutl2_api.models.values import StaticValue

        diffusion = effect.properties.get("拡散", 60)
        if isinstance(diffusion, StaticValue):
            return int(diffusion.value / 5) * 2 + 5
        elif isinstance(diffusion, (int, float)):
            return int(diffusion / 5) * 2 + 5
        return 25
