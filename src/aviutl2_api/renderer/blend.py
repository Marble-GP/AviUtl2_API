"""Blend mode implementations for compositing."""

from __future__ import annotations

from typing import Callable

import numpy as np

# Mapping from Japanese blend mode names to internal names
BLEND_MODES: dict[str, str] = {
    "通常": "normal",
    "加算": "add",
    "減算": "subtract",
    "乗算": "multiply",
    "スクリーン": "screen",
    "オーバーレイ": "overlay",
    "比較(明)": "lighten",
    "比較(暗)": "darken",
    "輝度": "luminosity",
    "色差": "color_difference",
    "陰影": "shadow",
    "明暗": "hard_light",
    "差分": "exclusion",
}


def blend(
    canvas: np.ndarray,
    layer: np.ndarray,
    mode: str = "通常",
    opacity: float = 1.0,
) -> np.ndarray:
    """Composite layer onto canvas using specified blend mode.

    Args:
        canvas: Background RGBA array, shape (H, W, 4), dtype uint8
        layer: Foreground RGBA array, shape (H, W, 4), dtype uint8
        mode: Japanese blend mode name (e.g., "通常", "加算", "乗算")
        opacity: Layer opacity 0.0 (transparent) to 1.0 (opaque)
                 Note: AviUtl uses inverted scale (0=opaque, 100=transparent)
                 The caller should convert: opacity = (100 - transparency) / 100

    Returns:
        Composited RGBA array, shape (H, W, 4), dtype uint8
    """
    internal_mode = BLEND_MODES.get(mode, "normal")
    blend_func = _get_blend_function(internal_mode)

    # Convert to float for calculations
    canvas_rgb = canvas[..., :3].astype(np.float32) / 255.0
    canvas_a = canvas[..., 3].astype(np.float32) / 255.0
    layer_rgb = layer[..., :3].astype(np.float32) / 255.0
    layer_a = (layer[..., 3].astype(np.float32) / 255.0) * opacity

    # Apply blend function to RGB
    blended_rgb = blend_func(canvas_rgb, layer_rgb)

    # Porter-Duff "over" compositing
    out_a = layer_a + canvas_a * (1 - layer_a)
    out_a_safe = np.maximum(out_a, 1e-6)

    out_rgb = (
        blended_rgb * layer_a[..., np.newaxis]
        + canvas_rgb * canvas_a[..., np.newaxis] * (1 - layer_a[..., np.newaxis])
    ) / out_a_safe[..., np.newaxis]

    # Combine and convert back to uint8
    result = np.zeros_like(canvas)
    result[..., :3] = np.clip(out_rgb * 255, 0, 255).astype(np.uint8)
    result[..., 3] = np.clip(out_a * 255, 0, 255).astype(np.uint8)

    return result


def blend_onto(
    canvas: np.ndarray,
    layer: np.ndarray,
    x: int,
    y: int,
    mode: str = "通常",
    opacity: float = 1.0,
) -> None:
    """Composite layer onto canvas at specified position (in-place).

    Args:
        canvas: Background RGBA array to modify in-place
        layer: Foreground RGBA array
        x: X position on canvas (top-left of layer)
        y: Y position on canvas (top-left of layer)
        mode: Japanese blend mode name
        opacity: Layer opacity 0.0 to 1.0
    """
    ch, cw = canvas.shape[:2]
    lh, lw = layer.shape[:2]

    # Calculate overlap region
    # Source (layer) bounds
    sx1 = max(0, -x)
    sy1 = max(0, -y)
    sx2 = min(lw, cw - x)
    sy2 = min(lh, ch - y)

    # Destination (canvas) bounds
    dx1 = max(0, x)
    dy1 = max(0, y)
    dx2 = min(cw, x + lw)
    dy2 = min(ch, y + lh)

    # Check for valid overlap
    if sx1 >= sx2 or sy1 >= sy2 or dx1 >= dx2 or dy1 >= dy2:
        return

    # Extract regions
    canvas_region = canvas[dy1:dy2, dx1:dx2].copy()
    layer_region = layer[sy1:sy2, sx1:sx2]

    # Blend the regions
    blended = blend(canvas_region, layer_region, mode, opacity)

    # Write back
    canvas[dy1:dy2, dx1:dx2] = blended


# Blend function implementations


def _blend_normal(bg: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """Normal blend: foreground replaces background."""
    return fg


def _blend_add(bg: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """Additive blend: add colors (clamped)."""
    return np.minimum(bg + fg, 1.0)


def _blend_subtract(bg: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """Subtractive blend: subtract foreground from background."""
    return np.maximum(bg - fg, 0.0)


def _blend_multiply(bg: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """Multiply blend: multiply colors."""
    return bg * fg


def _blend_screen(bg: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """Screen blend: inverse of multiply."""
    return 1 - (1 - bg) * (1 - fg)


def _blend_overlay(bg: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """Overlay blend: multiply or screen based on background."""
    mask = bg < 0.5
    result = np.where(mask, 2 * bg * fg, 1 - 2 * (1 - bg) * (1 - fg))
    return result


def _blend_lighten(bg: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """Lighten blend: take maximum of each channel."""
    return np.maximum(bg, fg)


def _blend_darken(bg: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """Darken blend: take minimum of each channel."""
    return np.minimum(bg, fg)


def _blend_difference(bg: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """Difference blend: absolute difference."""
    return np.abs(bg - fg)


def _blend_exclusion(bg: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """Exclusion blend: similar to difference but lower contrast."""
    return bg + fg - 2 * bg * fg


def _blend_hard_light(bg: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """Hard light blend: multiply or screen based on foreground."""
    mask = fg < 0.5
    result = np.where(mask, 2 * bg * fg, 1 - 2 * (1 - bg) * (1 - fg))
    return result


def _blend_soft_light(bg: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """Soft light blend: gentler version of hard light."""
    return (1 - 2 * fg) * bg * bg + 2 * fg * bg


def _blend_color_dodge(bg: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """Color dodge blend: brighten background based on foreground."""
    return np.minimum(bg / np.maximum(1 - fg, 1e-6), 1.0)


def _blend_color_burn(bg: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """Color burn blend: darken background based on foreground."""
    return 1 - np.minimum((1 - bg) / np.maximum(fg, 1e-6), 1.0)


BlendFunction = Callable[[np.ndarray, np.ndarray], np.ndarray]


def _get_blend_function(mode: str) -> BlendFunction:
    """Get blend function by internal mode name.

    Args:
        mode: Internal blend mode name

    Returns:
        Blend function
    """
    funcs: dict[str, BlendFunction] = {
        "normal": _blend_normal,
        "add": _blend_add,
        "subtract": _blend_subtract,
        "multiply": _blend_multiply,
        "screen": _blend_screen,
        "overlay": _blend_overlay,
        "lighten": _blend_lighten,
        "darken": _blend_darken,
        "difference": _blend_difference,
        "exclusion": _blend_exclusion,
        "hard_light": _blend_hard_light,
        "soft_light": _blend_soft_light,
        "color_dodge": _blend_color_dodge,
        "color_burn": _blend_color_burn,
        # Aliases and additional modes
        "luminosity": _blend_normal,  # Simplified
        "color_difference": _blend_difference,
        "shadow": _blend_multiply,  # Simplified approximation
    }
    return funcs.get(mode, _blend_normal)


def opacity_from_transparency(transparency: float) -> float:
    """Convert AviUtl transparency (0=opaque, 100=transparent) to opacity.

    Args:
        transparency: AviUtl transparency value (0-100)

    Returns:
        Opacity value (0.0-1.0)
    """
    return (100.0 - transparency) / 100.0
