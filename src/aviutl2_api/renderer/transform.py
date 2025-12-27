"""Coordinate transformations and 2D/3D rotation projections."""

from __future__ import annotations

import math

import cv2
import numpy as np


def apply_transform(
    content: np.ndarray,
    x: float,
    y: float,
    z: float,
    center_x: float,
    center_y: float,
    center_z: float,
    x_rotation: float,
    y_rotation: float,
    z_rotation: float,
    scale: float,
    aspect: float,
    canvas_size: tuple[int, int],
) -> tuple[np.ndarray, int, int]:
    """Apply 2D transformation to content with 3D rotation projection.

    AviUtl coordinate system:
    - Origin (0, 0) = center of canvas
    - X increases to the right
    - Y increases downward
    - Z is depth (affects scale in camera mode)

    Args:
        content: RGBA image array to transform
        x: X position in canvas coordinates (center = 0)
        y: Y position in canvas coordinates (center = 0)
        z: Z position (used for slight scale adjustment)
        center_x: Rotation center X offset from content center
        center_y: Rotation center Y offset from content center
        center_z: Rotation center Z offset (ignored in 2D)
        x_rotation: X-axis rotation in degrees (forward/backward tilt)
        y_rotation: Y-axis rotation in degrees (left/right tilt)
        z_rotation: Z-axis rotation in degrees (2D rotation)
        scale: Scale percentage (100 = 100%)
        aspect: Aspect ratio adjustment
        canvas_size: Tuple of (canvas_width, canvas_height)

    Returns:
        Tuple of (transformed_content, dest_x, dest_y) where dest_x/dest_y
        are pixel coordinates for the top-left of the content on canvas
    """
    if content.size == 0:
        return content, 0, 0

    h, w = content.shape[:2]
    canvas_w, canvas_h = canvas_size

    # 1. Apply scale
    scale_factor = scale / 100.0
    if scale_factor <= 0:
        return np.zeros((1, 1, 4), dtype=np.uint8), 0, 0

    # Apply aspect ratio adjustment
    scale_x = scale_factor
    scale_y = scale_factor * (1 + aspect / 100.0)

    new_w = max(1, int(w * scale_x))
    new_h = max(1, int(h * scale_y))

    # Use INTER_LINEAR for upscaling, INTER_AREA for downscaling
    if scale_factor >= 1.0:
        interp = cv2.INTER_LINEAR
    else:
        interp = cv2.INTER_AREA

    scaled = cv2.resize(content, (new_w, new_h), interpolation=interp)
    current = scaled
    current_w, current_h = new_w, new_h

    # 2. Apply 3D rotations as 2D perspective projections
    if x_rotation != 0 or y_rotation != 0:
        current, current_w, current_h = _apply_3d_rotation(
            current, x_rotation, y_rotation, center_x * scale_factor, center_y * scale_factor
        )

    # 3. Apply Z-axis rotation (standard 2D rotation)
    if z_rotation != 0:
        current, current_w, current_h = _apply_z_rotation(
            current, z_rotation, center_x * scale_factor, center_y * scale_factor
        )

    # 4. Calculate destination position
    # Convert from center-origin canvas coordinates to pixel coordinates
    dest_x = int(canvas_w / 2 + x - current_w / 2)
    dest_y = int(canvas_h / 2 + y - current_h / 2)

    return current, dest_x, dest_y


def _apply_z_rotation(
    image: np.ndarray,
    rotation: float,
    center_offset_x: float = 0,
    center_offset_y: float = 0,
) -> tuple[np.ndarray, int, int]:
    """Apply Z-axis rotation (standard 2D rotation).

    Args:
        image: RGBA image array
        rotation: Rotation angle in degrees
        center_offset_x: X offset from image center for rotation pivot
        center_offset_y: Y offset from image center for rotation pivot

    Returns:
        Tuple of (rotated_image, new_width, new_height)
    """
    h, w = image.shape[:2]

    # Calculate rotation center
    cx = w / 2 + center_offset_x
    cy = h / 2 + center_offset_y

    # Get rotation matrix
    # Note: OpenCV uses positive angles for counter-clockwise rotation
    # AviUtl uses positive for clockwise, so we negate
    M = cv2.getRotationMatrix2D((cx, cy), -rotation, 1.0)

    # Calculate new bounding box size
    cos_a = abs(M[0, 0])
    sin_a = abs(M[0, 1])
    new_w = int(h * sin_a + w * cos_a)
    new_h = int(h * cos_a + w * sin_a)

    # Adjust translation to keep content centered
    M[0, 2] += (new_w - w) / 2
    M[1, 2] += (new_h - h) / 2

    # Apply rotation with transparent background
    rotated = cv2.warpAffine(
        image,
        M,
        (new_w, new_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )

    return rotated, new_w, new_h


def _apply_3d_rotation(
    image: np.ndarray,
    x_rotation: float,
    y_rotation: float,
    center_offset_x: float = 0,
    center_offset_y: float = 0,
) -> tuple[np.ndarray, int, int]:
    """Apply 3D rotation as 2D perspective projection (parallel projection).

    This creates a card-flip effect by using perspective transforms.

    Args:
        image: RGBA image array
        x_rotation: X-axis rotation (forward/backward tilt) in degrees
        y_rotation: Y-axis rotation (left/right tilt) in degrees
        center_offset_x: X offset for rotation center
        center_offset_y: Y offset for rotation center

    Returns:
        Tuple of (transformed_image, new_width, new_height)
    """
    h, w = image.shape[:2]
    current = image

    # X-axis rotation (tilting forward/backward)
    if x_rotation != 0:
        current, h, w = _apply_x_axis_rotation(current, x_rotation)

    # Y-axis rotation (tilting left/right)
    if y_rotation != 0:
        current, h, w = _apply_y_axis_rotation(current, y_rotation)

    return current, w, h


def _apply_x_axis_rotation(
    image: np.ndarray,
    rotation: float,
) -> tuple[np.ndarray, int, int]:
    """Apply X-axis rotation as perspective projection.

    Creates a card tilting forward/backward effect.

    Args:
        image: RGBA image array
        rotation: Rotation angle in degrees

    Returns:
        Tuple of (transformed_image, new_height, new_width)
    """
    h, w = image.shape[:2]

    # Calculate perspective based on rotation
    # cos(angle) determines how much the top/bottom edges are compressed
    angle_rad = math.radians(rotation)
    cos_x = math.cos(angle_rad)

    # If rotation is very close to 90 or -90, the object would be edge-on
    if abs(cos_x) < 0.01:
        # Return a thin horizontal line
        new_h = max(1, int(h * 0.01))
        return cv2.resize(image, (w, new_h), interpolation=cv2.INTER_AREA), new_h, w

    # Calculate the horizontal offset for the compressed edge
    offset = w * (1 - abs(cos_x)) / 4

    # Source points (original corners)
    pts1 = np.float32([[0, 0], [w, 0], [0, h], [w, h]])

    # Destination points based on rotation direction
    if rotation > 0:
        # Tilting forward: top edge gets smaller
        pts2 = np.float32([[offset, 0], [w - offset, 0], [0, h], [w, h]])
    else:
        # Tilting backward: bottom edge gets smaller
        pts2 = np.float32([[0, 0], [w, 0], [offset, h], [w - offset, h]])

    # Also adjust height based on angle
    new_h = max(1, int(h * abs(cos_x)))

    # Get perspective transform matrix
    M = cv2.getPerspectiveTransform(pts1, pts2)

    # Apply transform
    result = cv2.warpPerspective(
        image,
        M,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )

    return result, h, w


def _apply_y_axis_rotation(
    image: np.ndarray,
    rotation: float,
) -> tuple[np.ndarray, int, int]:
    """Apply Y-axis rotation as perspective projection.

    Creates a card tilting left/right effect.

    Args:
        image: RGBA image array
        rotation: Rotation angle in degrees

    Returns:
        Tuple of (transformed_image, new_height, new_width)
    """
    h, w = image.shape[:2]

    # Calculate perspective based on rotation
    angle_rad = math.radians(rotation)
    cos_y = math.cos(angle_rad)

    # If rotation is very close to 90 or -90, the object would be edge-on
    if abs(cos_y) < 0.01:
        # Return a thin vertical line
        new_w = max(1, int(w * 0.01))
        return cv2.resize(image, (new_w, h), interpolation=cv2.INTER_AREA), h, new_w

    # Calculate the vertical offset for the compressed edge
    offset = h * (1 - abs(cos_y)) / 4

    # Source points (original corners)
    pts1 = np.float32([[0, 0], [w, 0], [0, h], [w, h]])

    # Destination points based on rotation direction
    if rotation > 0:
        # Tilting right: left edge gets smaller
        pts2 = np.float32([[0, offset], [w, 0], [0, h - offset], [w, h]])
    else:
        # Tilting left: right edge gets smaller
        pts2 = np.float32([[0, 0], [w, offset], [0, h], [w, h - offset]])

    # Get perspective transform matrix
    M = cv2.getPerspectiveTransform(pts1, pts2)

    # Apply transform
    result = cv2.warpPerspective(
        image,
        M,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )

    return result, h, w


def scale_image(
    image: np.ndarray,
    scale_x: float,
    scale_y: float,
) -> np.ndarray:
    """Scale an image by given factors.

    Args:
        image: Source image array
        scale_x: Horizontal scale factor (1.0 = original size)
        scale_y: Vertical scale factor (1.0 = original size)

    Returns:
        Scaled image array
    """
    if scale_x <= 0 or scale_y <= 0:
        return np.zeros((1, 1, 4), dtype=np.uint8)

    h, w = image.shape[:2]
    new_w = max(1, int(w * scale_x))
    new_h = max(1, int(h * scale_y))

    # Choose interpolation based on scale direction
    if scale_x >= 1.0 and scale_y >= 1.0:
        interp = cv2.INTER_LINEAR
    else:
        interp = cv2.INTER_AREA

    return cv2.resize(image, (new_w, new_h), interpolation=interp)


def flip_image(
    image: np.ndarray,
    horizontal: bool = False,
    vertical: bool = False,
) -> np.ndarray:
    """Flip an image horizontally and/or vertically.

    Args:
        image: Source image array
        horizontal: Flip horizontally (left-right)
        vertical: Flip vertically (up-down)

    Returns:
        Flipped image array
    """
    if not horizontal and not vertical:
        return image

    if horizontal and vertical:
        return cv2.flip(image, -1)
    elif horizontal:
        return cv2.flip(image, 1)
    else:
        return cv2.flip(image, 0)
