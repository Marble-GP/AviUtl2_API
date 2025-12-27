"""Animation value interpolation at specific frames."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aviutl2_api.models import TimelineObject
    from aviutl2_api.models.values import AnimatedValue, StaticValue


def interpolate_value(
    prop_value: StaticValue | AnimatedValue | float | str | None,
    frame: int,
    obj: TimelineObject,
) -> float:
    """Interpolate a property value at a specific frame.

    Handles:
    - StaticValue: Return constant value
    - AnimatedValue: Interpolate based on motion type
    - float/int: Direct value
    - str/None: Return 0.0

    Args:
        prop_value: The property value (static or animated)
        frame: Current frame number
        obj: Timeline object for frame range context

    Returns:
        Interpolated float value at the given frame
    """
    from aviutl2_api.models.values import AnimatedValue, StaticValue

    if prop_value is None:
        return 0.0

    if isinstance(prop_value, StaticValue):
        if isinstance(prop_value.value, (int, float)):
            return float(prop_value.value)
        return 0.0

    if isinstance(prop_value, AnimatedValue):
        return _interpolate_animated(prop_value, frame, obj)

    if isinstance(prop_value, (int, float)):
        return float(prop_value)

    # String or unknown type
    return 0.0


def _interpolate_animated(
    anim: AnimatedValue, frame: int, obj: TimelineObject
) -> float:
    """Interpolate an animated value at a specific frame.

    Args:
        anim: AnimatedValue instance
        frame: Current frame number
        obj: Timeline object for frame range

    Returns:
        Interpolated value
    """
    # Calculate normalized progress [0, 1]
    duration = obj.frame_end - obj.frame_start
    if duration <= 0:
        return anim.start

    t = (frame - obj.frame_start) / duration
    t = max(0.0, min(1.0, t))

    motion_type = anim.animation.motion_type
    param = anim.animation.param

    if motion_type == "直線移動":
        return _lerp(anim.start, anim.end, t)

    elif motion_type == "直線移動(時間制御)":
        # Linear with bezier time control
        eased_t = _apply_bezier_timing(t, param)
        return _lerp(anim.start, anim.end, eased_t)

    elif motion_type == "補間移動":
        # Easing with power curve
        eased_t = _ease_in_out(t)
        return _lerp(anim.start, anim.end, eased_t)

    elif motion_type == "補間移動(時間制御)":
        # Easing with bezier control
        eased_t = _apply_bezier_timing(t, param)
        eased_t = _ease_in_out(eased_t)
        return _lerp(anim.start, anim.end, eased_t)

    elif motion_type == "瞬間移動":
        # Instant jump at the end
        return anim.end if t >= 1.0 else anim.start

    elif motion_type == "反復移動":
        return _oscillate(anim.start, anim.end, t, param)

    elif motion_type == "回転":
        return _rotation_motion(anim.start, anim.end, t, param)

    elif motion_type == "ランダム移動":
        # Random movement - use a deterministic random based on frame
        return _random_motion(anim.start, anim.end, frame, obj.object_id, param)

    elif motion_type == "加減速移動":
        # Accelerate then decelerate
        eased_t = _ease_in_out(t, power=2)
        return _lerp(anim.start, anim.end, eased_t)

    elif motion_type == "曲線移動":
        # Bezier curve motion
        eased_t = _apply_bezier_timing(t, param)
        return _lerp(anim.start, anim.end, eased_t)

    else:
        # Fallback to linear
        return _lerp(anim.start, anim.end, t)


def _lerp(start: float, end: float, t: float) -> float:
    """Linear interpolation.

    Args:
        start: Start value
        end: End value
        t: Progress (0.0 to 1.0)

    Returns:
        Interpolated value
    """
    return start + (end - start) * t


def _ease_in_out(t: float, power: int = 3) -> float:
    """Apply ease-in-out easing curve.

    Args:
        t: Input progress (0.0 to 1.0)
        power: Easing power (higher = more pronounced)

    Returns:
        Eased progress value
    """
    if t < 0.5:
        return 0.5 * pow(2 * t, power)
    else:
        return 1 - 0.5 * pow(2 * (1 - t), power)


def _oscillate(start: float, end: float, t: float, param: str) -> float:
    """Oscillating (反復移動) motion.

    param format: "count|period|bezier_data..." or just count

    Args:
        start: Start value
        end: End value
        t: Progress (0.0 to 1.0)
        param: Motion parameters

    Returns:
        Oscillated value
    """
    # Parse parameters
    parts = param.split("|") if param else []

    try:
        count = int(parts[0]) if len(parts) > 0 and parts[0] else 1
    except ValueError:
        count = 1

    # Oscillate using sine wave
    # At t=0 -> start, oscillates between start and end
    cycles = count * t
    phase = math.sin(2 * math.pi * cycles)

    # phase goes from -1 to 1, map to start-end range
    mid = (start + end) / 2
    amplitude = (end - start) / 2
    return mid + amplitude * phase


def _rotation_motion(start: float, end: float, t: float, param: str) -> float:
    """Rotation (回転) motion - circular/orbital movement.

    The start value represents the center position, and end represents
    the initial position. The radius is derived from the difference.

    param format: "4|angle|bezier_data..."

    Args:
        start: Center position
        end: Initial position
        t: Progress (0.0 to 1.0)
        param: Motion parameters

    Returns:
        Position on the circular path
    """
    # Parse parameters
    parts = param.split("|") if param else []

    try:
        rotation_angle = float(parts[1]) if len(parts) > 1 else 360.0
    except (ValueError, IndexError):
        rotation_angle = 360.0

    # Calculate radius and initial angle
    radius = abs(end - start)
    if radius == 0:
        return start

    # Initial angle (where end is relative to start)
    initial_angle = 0.0 if end >= start else math.pi

    # Calculate current angle
    current_angle = initial_angle + math.radians(rotation_angle * t)

    # Return position on circle
    # For X coordinate: center + radius * cos(angle)
    # For Y coordinate: center + radius * sin(angle)
    # This function is called separately for X and Y, so we use the relationship
    # between start/end to determine which axis component to return
    return start + radius * math.cos(current_angle)


def _random_motion(
    start: float, end: float, frame: int, object_id: int, param: str
) -> float:
    """Random motion with deterministic pseudo-random based on frame.

    Args:
        start: Start value
        end: End value
        frame: Current frame number
        object_id: Object ID for unique seed
        param: Motion parameters

    Returns:
        Random value between start and end
    """
    # Use a simple deterministic random
    seed = frame * 12345 + object_id * 67890
    random_val = (math.sin(seed) * 43758.5453) % 1.0
    random_val = abs(random_val)

    return _lerp(start, end, random_val)


def _apply_bezier_timing(t: float, param: str) -> float:
    """Apply bezier curve timing control.

    param contains bezier control points after the type prefix.

    Args:
        t: Input progress (0.0 to 1.0)
        param: Parameter string with bezier data

    Returns:
        Eased progress value
    """
    if not param:
        return t

    # Try to parse bezier control points
    # Format: "type|angle|x1,y1,cx1,cy1,x2,y2,cx2,cy2" or similar
    parts = param.split("|")

    if len(parts) < 3:
        return t

    try:
        bezier_data = parts[2].split(",")
        if len(bezier_data) >= 4:
            # Simple cubic bezier approximation
            # Use first 4 values as control points
            # p0 = 0, p3 = 1 (start and end)
            # p1 and p2 are control points
            p1 = float(bezier_data[0]) if bezier_data[0] else 0.25
            p2 = float(bezier_data[2]) if len(bezier_data) > 2 and bezier_data[2] else 0.75

            # Simple cubic bezier evaluation for timing
            return _cubic_bezier(t, 0, p1, p2, 1)
    except (ValueError, IndexError):
        pass

    return t


def _cubic_bezier(t: float, p0: float, p1: float, p2: float, p3: float) -> float:
    """Evaluate cubic bezier curve.

    Args:
        t: Parameter (0 to 1)
        p0, p1, p2, p3: Control points

    Returns:
        Bezier curve value at t
    """
    mt = 1 - t
    return mt * mt * mt * p0 + 3 * mt * mt * t * p1 + 3 * mt * t * t * p2 + t * t * t * p3


def get_property_value_at_frame(
    properties: dict,
    key: str,
    frame: int,
    obj: TimelineObject,
    default: float = 0.0,
) -> float:
    """Get a property value at a specific frame with proper interpolation.

    Args:
        properties: Effect properties dictionary
        key: Property key name
        frame: Current frame number
        obj: Timeline object for context
        default: Default value if property not found

    Returns:
        Property value at the given frame
    """
    if key not in properties:
        return default

    return interpolate_value(properties[key], frame, obj)


def get_property_string(
    properties: dict,
    key: str,
    default: str = "",
) -> str:
    """Get a string property value (non-animated).

    Args:
        properties: Effect properties dictionary
        key: Property key name
        default: Default value if not found

    Returns:
        String property value
    """
    from aviutl2_api.models.values import StaticValue

    value = properties.get(key)

    if value is None:
        return default

    if isinstance(value, str):
        return value

    if isinstance(value, StaticValue):
        return str(value.value)

    return default
