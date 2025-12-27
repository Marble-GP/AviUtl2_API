"""Border/outline filter effect (縁取り)."""

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


class BorderFilter(FilterEffect):
    """Border/outline filter (縁取り)."""

    def can_apply(self, effect_name: str) -> bool:
        """Check if this filter handles the effect type."""
        return effect_name == "縁取り"

    def apply(
        self,
        image: np.ndarray,
        effect: Effect,
        frame: int,
        obj: TimelineObject,
    ) -> np.ndarray:
        """Apply border/outline effect to the image.

        Args:
            image: Source RGBA image
            effect: Border effect parameters
            frame: Current frame
            obj: Parent timeline object

        Returns:
            Image with border
        """
        # Get border parameters
        size = get_property_value_at_frame(
            effect.properties, "サイズ", frame, obj, 5.0
        )
        blur = get_property_value_at_frame(
            effect.properties, "ぼかし", frame, obj, 0.0
        )
        color_hex = get_property_string(effect.properties, "縁色", "ffffff")

        if size <= 0:
            return image

        # Parse color
        r, g, b = parse_hex_color(color_hex)

        # Get image dimensions
        h, w = image.shape[:2]

        # Calculate padding for border
        pad = int(size) + 2

        # Create padded canvas
        padded_h = h + pad * 2
        padded_w = w + pad * 2
        result = np.zeros((padded_h, padded_w, 4), dtype=np.uint8)

        # Extract alpha channel for outline detection
        alpha = image[:, :, 3]

        # Create border mask by dilating the alpha channel
        kernel_size = int(size * 2) + 1
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)
        )

        # Dilate alpha to create outline
        dilated_alpha = cv2.dilate(alpha, kernel, iterations=1)

        # Create border (dilated - original)
        border_alpha = dilated_alpha.astype(np.int16) - alpha.astype(np.int16)
        border_alpha = np.clip(border_alpha, 0, 255).astype(np.uint8)

        # Apply blur if specified
        if blur > 0:
            blur_kernel = int(blur * 2) | 1
            blur_kernel = max(3, blur_kernel)
            border_alpha = cv2.GaussianBlur(
                border_alpha, (blur_kernel, blur_kernel), blur
            )

        # Create border layer with color
        border_layer = np.zeros((h, w, 4), dtype=np.uint8)
        border_layer[:, :, 0] = r
        border_layer[:, :, 1] = g
        border_layer[:, :, 2] = b
        border_layer[:, :, 3] = border_alpha

        # Composite: border behind original
        # Place border in padded canvas
        result[pad : pad + h, pad : pad + w] = border_layer

        # Blend original on top
        orig_alpha = image[:, :, 3].astype(np.float32) / 255.0
        for c in range(3):
            result[pad : pad + h, pad : pad + w, c] = (
                image[:, :, c] * orig_alpha
                + result[pad : pad + h, pad : pad + w, c] * (1 - orig_alpha)
            ).astype(np.uint8)

        result[pad : pad + h, pad : pad + w, 3] = np.maximum(
            image[:, :, 3], border_alpha
        )

        return result

    def get_padding(self, effect: Effect) -> int:
        """Get padding needed for border effect."""
        from aviutl2_api.models.values import StaticValue

        size = effect.properties.get("サイズ", 5)
        if isinstance(size, StaticValue):
            return int(size.value) + 2
        elif isinstance(size, (int, float)):
            return int(size) + 2
        return 7
