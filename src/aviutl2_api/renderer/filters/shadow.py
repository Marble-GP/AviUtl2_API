"""Drop shadow filter effect (ドロップシャドウ)."""

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


class ShadowFilter(FilterEffect):
    """Drop shadow filter (ドロップシャドウ)."""

    def can_apply(self, effect_name: str) -> bool:
        """Check if this filter handles the effect type."""
        return effect_name == "ドロップシャドウ"

    def apply(
        self,
        image: np.ndarray,
        effect: Effect,
        frame: int,
        obj: TimelineObject,
    ) -> np.ndarray:
        """Apply drop shadow effect to the image.

        Args:
            image: Source RGBA image
            effect: Shadow effect parameters
            frame: Current frame
            obj: Parent timeline object

        Returns:
            Image with shadow
        """
        # Get shadow parameters
        offset_x = get_property_value_at_frame(
            effect.properties, "X", frame, obj, 5.0
        )
        offset_y = get_property_value_at_frame(
            effect.properties, "Y", frame, obj, 5.0
        )
        opacity = get_property_value_at_frame(
            effect.properties, "濃さ", frame, obj, 60.0
        )
        diffusion = get_property_value_at_frame(
            effect.properties, "拡散", frame, obj, 5.0
        )
        color_hex = get_property_string(effect.properties, "影色", "000000")

        # Parse color
        r, g, b = parse_hex_color(color_hex)

        # Get image dimensions
        h, w = image.shape[:2]

        # Calculate canvas size (need padding for shadow offset and blur)
        padding = int(max(abs(offset_x), abs(offset_y)) + diffusion * 2) + 10
        canvas_w = w + padding * 2
        canvas_h = h + padding * 2

        # Create result canvas
        result = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)

        # Create shadow layer
        shadow = np.zeros((h, w, 4), dtype=np.uint8)
        shadow[:, :, 0] = r
        shadow[:, :, 1] = g
        shadow[:, :, 2] = b
        # Use original alpha for shadow shape
        shadow_alpha = (image[:, :, 3].astype(np.float32) * opacity / 100.0).astype(
            np.uint8
        )
        shadow[:, :, 3] = shadow_alpha

        # Apply blur for diffusion
        if diffusion > 0:
            blur_kernel = int(diffusion * 2) | 1
            blur_kernel = max(3, blur_kernel)
            shadow = cv2.GaussianBlur(shadow, (blur_kernel, blur_kernel), diffusion)

        # Place shadow in result (with offset)
        shadow_x = padding + int(offset_x)
        shadow_y = padding + int(offset_y)

        # Clip to valid range
        src_x1 = max(0, -shadow_x)
        src_y1 = max(0, -shadow_y)
        src_x2 = min(w, canvas_w - shadow_x)
        src_y2 = min(h, canvas_h - shadow_y)

        dst_x1 = max(0, shadow_x)
        dst_y1 = max(0, shadow_y)
        dst_x2 = min(canvas_w, shadow_x + w)
        dst_y2 = min(canvas_h, shadow_y + h)

        if dst_x2 > dst_x1 and dst_y2 > dst_y1:
            result[dst_y1:dst_y2, dst_x1:dst_x2] = shadow[
                src_y1:src_y2, src_x1:src_x2
            ]

        # Place original image on top (centered in padding)
        orig_x = padding
        orig_y = padding

        # Blend original on top of shadow
        orig_alpha = image[:, :, 3].astype(np.float32) / 255.0

        for c in range(3):
            result[orig_y : orig_y + h, orig_x : orig_x + w, c] = (
                image[:, :, c] * orig_alpha
                + result[orig_y : orig_y + h, orig_x : orig_x + w, c]
                * (1 - orig_alpha)
            ).astype(np.uint8)

        # Combine alpha channels
        result[orig_y : orig_y + h, orig_x : orig_x + w, 3] = np.maximum(
            image[:, :, 3],
            result[orig_y : orig_y + h, orig_x : orig_x + w, 3],
        )

        return result

    def get_padding(self, effect: Effect) -> int:
        """Get padding needed for shadow effect."""
        from aviutl2_api.models.values import StaticValue

        def get_val(key: str, default: float) -> float:
            val = effect.properties.get(key, default)
            if isinstance(val, StaticValue):
                return abs(float(val.value))
            elif isinstance(val, (int, float)):
                return abs(float(val))
            return default

        offset_x = get_val("X", 5)
        offset_y = get_val("Y", 5)
        diffusion = get_val("拡散", 5)

        return int(max(offset_x, offset_y) + diffusion * 2) + 10
