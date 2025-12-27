"""Blur filter effect (ぼかし)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np

from aviutl2_api.renderer.filters.base import FilterEffect
from aviutl2_api.renderer.interpolation import get_property_value_at_frame

if TYPE_CHECKING:
    from aviutl2_api.models import Effect, TimelineObject


class BlurFilter(FilterEffect):
    """Gaussian blur filter (ぼかし)."""

    def can_apply(self, effect_name: str) -> bool:
        """Check if this filter handles the effect type."""
        return effect_name == "ぼかし"

    def apply(
        self,
        image: np.ndarray,
        effect: Effect,
        frame: int,
        obj: TimelineObject,
    ) -> np.ndarray:
        """Apply Gaussian blur to the image.

        Args:
            image: Source RGBA image
            effect: Blur effect parameters
            frame: Current frame
            obj: Parent timeline object

        Returns:
            Blurred image
        """
        # Get blur parameters
        radius = get_property_value_at_frame(
            effect.properties, "範囲", frame, obj, 5.0
        )
        aspect = get_property_value_at_frame(
            effect.properties, "縦横比", frame, obj, 0.0
        )

        if radius <= 0:
            return image

        # Calculate blur kernel size (must be odd)
        kernel_size = int(radius * 2) | 1  # Make odd
        kernel_size = max(3, kernel_size)

        # Adjust for aspect ratio
        if aspect != 0:
            # Positive aspect = more horizontal blur
            # Negative aspect = more vertical blur
            aspect_factor = 1 + abs(aspect) / 100.0
            if aspect > 0:
                sigma_x = radius * aspect_factor
                sigma_y = radius
            else:
                sigma_x = radius
                sigma_y = radius * aspect_factor
        else:
            sigma_x = radius
            sigma_y = radius

        # Apply Gaussian blur
        # OpenCV GaussianBlur works with the kernel size, and sigma is computed from it
        # For more control, we use sigmaX and sigmaY
        blurred = cv2.GaussianBlur(
            image,
            (kernel_size, kernel_size),
            sigmaX=sigma_x,
            sigmaY=sigma_y,
        )

        return blurred

    def get_padding(self, effect: Effect) -> int:
        """Get padding needed for blur effect."""
        # Blur doesn't expand bounds significantly
        return 0
