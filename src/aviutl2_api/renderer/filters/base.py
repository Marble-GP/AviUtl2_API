"""Base class for filter effects."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from aviutl2_api.models import Effect, TimelineObject


class FilterEffect(ABC):
    """Abstract base class for filter effects.

    Filter effects are applied to rendered content to modify its appearance,
    such as blur, glow, shadow, or border effects.
    """

    @abstractmethod
    def can_apply(self, effect_name: str) -> bool:
        """Check if this filter handles the specified effect type.

        Args:
            effect_name: The effect name (e.g., "ぼかし", "グロー")

        Returns:
            True if this filter can handle the effect type
        """
        pass

    @abstractmethod
    def apply(
        self,
        image: np.ndarray,
        effect: Effect,
        frame: int,
        obj: TimelineObject,
    ) -> np.ndarray:
        """Apply the filter effect to an image.

        Args:
            image: Source RGBA image array
            effect: The filter effect parameters
            frame: Current frame number
            obj: Parent timeline object

        Returns:
            Filtered RGBA image array
        """
        pass

    def get_padding(self, effect: Effect) -> int:
        """Get the padding needed for this filter effect.

        Some filters (like blur, glow) may expand the image bounds.
        This method returns the additional padding needed.

        Args:
            effect: The filter effect parameters

        Returns:
            Padding in pixels (applied to all sides)
        """
        return 0
