"""Value types for AviUtl2 properties (static and animated)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class AnimationParams:
    """Parameters for animation motion."""

    motion_type: str  # 直線移動, 反復移動, 回転, etc.
    param: str  # Raw parameter string (e.g., "0", "4|15|0.1,0.2,0.3,0.4")

    def to_aup2(self) -> str:
        """Convert to .aup2 format string."""
        return f"{self.motion_type},{self.param}"

    @classmethod
    def from_parts(cls, motion_type: str, param: str) -> AnimationParams:
        return cls(motion_type=motion_type, param=param)


@dataclass
class AnimatedValue:
    """A value that changes over time."""

    start: float
    end: float
    animation: AnimationParams

    def to_aup2(self) -> str:
        """Convert to .aup2 format string."""
        # Format floats appropriately
        start_str = _format_float(self.start)
        end_str = _format_float(self.end)
        return f"{start_str},{end_str},{self.animation.to_aup2()}"

    @classmethod
    def parse(cls, value_str: str) -> AnimatedValue:
        """Parse animated value from string like '-100.00,100.00,直線移動,0'."""
        parts = value_str.split(",", 3)
        if len(parts) < 4:
            raise ValueError(f"Invalid animated value: {value_str}")

        start = float(parts[0])
        end = float(parts[1])
        motion_type = parts[2]
        param = parts[3] if len(parts) > 3 else ""

        return cls(
            start=start,
            end=end,
            animation=AnimationParams(motion_type=motion_type, param=param)
        )


@dataclass
class StaticValue:
    """A static (non-animated) value."""

    value: float | str

    def to_aup2(self) -> str:
        """Convert to .aup2 format string."""
        if isinstance(self.value, float):
            return _format_float(self.value)
        return str(self.value)


# Type alias for property values
PropertyValue = StaticValue | AnimatedValue | str


def _format_float(v: float) -> str:
    """Format float for .aup2 output."""
    # Preserve precision as seen in samples
    if v == int(v):
        return f"{int(v)}.00"
    return f"{v:.2f}"


def parse_property_value(key: str, value_str: str) -> PropertyValue:
    """Parse a property value, detecting if it's animated or static.

    Animated values have format: start,end,motion_type,params
    Static numeric values: just a number
    String values: anything else
    """
    # Check if this looks like an animated value
    # Animated values have at least 4 comma-separated parts with a motion type
    parts = value_str.split(",")

    if len(parts) >= 4:
        # Check if 3rd part is a known motion type
        motion_types = [
            # Basic motion types
            "直線移動",
            "直線移動(時間制御)",
            "補間移動",
            "補間移動(時間制御)",
            "瞬間移動",
            "移動量指定",
            "反復移動",
            "回転",
            "ランダム移動",
            # Legacy/special types
            "加減速移動",
            "曲線移動",
            "再生範囲",
        ]
        if parts[2] in motion_types:
            return AnimatedValue.parse(value_str)

    # Check if this looks like a hex color code (6 digits, hex only)
    # These are used for: 色, 文字色, 影・縁色, 縁色, 影色, 光色, etc.
    if len(value_str) == 6 and all(c in "0123456789abcdefABCDEF" for c in value_str):
        return value_str

    # Try to parse as float
    try:
        return StaticValue(value=float(value_str))
    except ValueError:
        # Return as string
        return value_str
