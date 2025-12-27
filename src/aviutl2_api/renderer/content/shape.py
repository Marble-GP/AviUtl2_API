"""Shape content renderer for geometric shapes."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import cv2
import numpy as np

from aviutl2_api.renderer.canvas import parse_hex_color
from aviutl2_api.renderer.content.base import ContentRenderer
from aviutl2_api.renderer.interpolation import (
    get_property_string,
    get_property_value_at_frame,
)

if TYPE_CHECKING:
    from aviutl2_api.models import Effect, Scene, TimelineObject


class ShapeRenderer(ContentRenderer):
    """Renders shape objects (circle, rectangle, triangle, etc.)."""

    # Mapping of Japanese shape names to internal identifiers
    SHAPE_MAP: dict[str, str] = {
        "円": "circle",
        "四角形": "rectangle",
        "三角形": "triangle",
        "五角形": "pentagon",
        "六角形": "hexagon",
        "星型": "star",
        "ハート": "heart",
        "背景": "background",
    }

    def can_render(self, effect_name: str) -> bool:
        """Check if this renderer handles the effect type."""
        return effect_name == "図形"

    def render(
        self,
        effect: Effect,
        frame: int,
        obj: TimelineObject,
        scene: Scene,
    ) -> tuple[np.ndarray, tuple[int, int]]:
        """Render a shape.

        Args:
            effect: The shape effect
            frame: Current frame number
            obj: Parent timeline object
            scene: Parent scene

        Returns:
            Tuple of (RGBA image array, (width, height))
        """
        # Get shape properties
        shape_type = get_property_string(effect.properties, "図形の種類", "円")
        size = get_property_value_at_frame(
            effect.properties, "サイズ", frame, obj, 100.0
        )
        aspect = get_property_value_at_frame(
            effect.properties, "縦横比", frame, obj, 0.0
        )
        line_width = get_property_value_at_frame(
            effect.properties, "ライン幅", frame, obj, 4000.0
        )
        color_hex = get_property_string(effect.properties, "色", "ffffff")
        round_corners = get_property_value_at_frame(
            effect.properties, "角を丸くする", frame, obj, 0.0
        )

        # Parse color
        r, g, b = parse_hex_color(color_hex)

        # Calculate dimensions
        # "サイズ" is the bounding box size - the shape fits within this box
        # with its centroid at the center and at least one point touching the edge
        width = int(size)
        height = int(size * (1 + aspect / 100.0))

        if width <= 0:
            width = 2
        if height <= 0:
            height = 2

        # Determine if filled or outlined
        # line_width >= 4000 means filled
        is_filled = line_width >= 4000

        # Get internal shape type
        internal_type = self.SHAPE_MAP.get(shape_type, "circle")

        # Draw the shape
        if internal_type == "circle":
            img = self._draw_circle(width, height, r, g, b, is_filled, line_width)
        elif internal_type == "rectangle":
            img = self._draw_rectangle(
                width, height, r, g, b, is_filled, line_width, round_corners > 0
            )
        elif internal_type == "triangle":
            img = self._draw_triangle(width, height, r, g, b, is_filled, line_width)
        elif internal_type == "pentagon":
            img = self._draw_polygon(width, height, r, g, b, is_filled, line_width, 5)
        elif internal_type == "hexagon":
            img = self._draw_polygon(width, height, r, g, b, is_filled, line_width, 6)
        elif internal_type == "star":
            img = self._draw_star(width, height, r, g, b, is_filled, line_width)
        elif internal_type == "heart":
            img = self._draw_heart(width, height, r, g, b, is_filled, line_width)
        elif internal_type == "background":
            # Background fills the entire scene
            img = self._draw_background(scene.width, scene.height, r, g, b)
            return img, (scene.width, scene.height)
        else:
            img = self._draw_circle(width, height, r, g, b, is_filled, line_width)

        return img, (img.shape[1], img.shape[0])

    def _draw_circle(
        self,
        width: int,
        height: int,
        r: int,
        g: int,
        b: int,
        filled: bool,
        line_width: float,
    ) -> np.ndarray:
        """Draw a circle/ellipse."""
        # Add padding for anti-aliasing
        padding = 4
        canvas_w = width + padding * 2
        canvas_h = height + padding * 2

        img = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)

        center = (canvas_w // 2, canvas_h // 2)
        axes = (width // 2, height // 2)

        if filled:
            cv2.ellipse(img, center, axes, 0, 0, 360, (r, g, b, 255), -1, cv2.LINE_AA)
        else:
            thickness = max(1, int(line_width / 100))
            cv2.ellipse(
                img, center, axes, 0, 0, 360, (r, g, b, 255), thickness, cv2.LINE_AA
            )

        return img

    def _draw_rectangle(
        self,
        width: int,
        height: int,
        r: int,
        g: int,
        b: int,
        filled: bool,
        line_width: float,
        rounded: bool,
    ) -> np.ndarray:
        """Draw a rectangle."""
        padding = 4
        canvas_w = width + padding * 2
        canvas_h = height + padding * 2

        img = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)

        pt1 = (padding, padding)
        pt2 = (padding + width, padding + height)

        if filled:
            cv2.rectangle(img, pt1, pt2, (r, g, b, 255), -1, cv2.LINE_AA)
        else:
            thickness = max(1, int(line_width / 100))
            cv2.rectangle(img, pt1, pt2, (r, g, b, 255), thickness, cv2.LINE_AA)

        return img

    def _draw_triangle(
        self,
        width: int,
        height: int,
        r: int,
        g: int,
        b: int,
        filled: bool,
        line_width: float,
    ) -> np.ndarray:
        """Draw an equilateral triangle."""
        padding = 4
        canvas_w = width + padding * 2
        canvas_h = height + padding * 2

        img = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)

        # Triangle points (pointing up)
        cx = canvas_w // 2
        cy = canvas_h // 2
        half_w = width // 2
        half_h = height // 2

        pts = np.array(
            [
                [cx, cy - half_h],  # Top
                [cx - half_w, cy + half_h],  # Bottom left
                [cx + half_w, cy + half_h],  # Bottom right
            ],
            np.int32,
        )

        if filled:
            cv2.fillPoly(img, [pts], (r, g, b, 255), cv2.LINE_AA)
        else:
            thickness = max(1, int(line_width / 100))
            cv2.polylines(img, [pts], True, (r, g, b, 255), thickness, cv2.LINE_AA)

        return img

    def _draw_polygon(
        self,
        width: int,
        height: int,
        r: int,
        g: int,
        b: int,
        filled: bool,
        line_width: float,
        sides: int,
    ) -> np.ndarray:
        """Draw a regular polygon."""
        padding = 4
        canvas_w = width + padding * 2
        canvas_h = height + padding * 2

        img = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)

        cx = canvas_w // 2
        cy = canvas_h // 2
        radius_x = width // 2
        radius_y = height // 2

        # Generate polygon vertices
        pts = []
        for i in range(sides):
            angle = 2 * math.pi * i / sides - math.pi / 2  # Start from top
            x = int(cx + radius_x * math.cos(angle))
            y = int(cy + radius_y * math.sin(angle))
            pts.append([x, y])

        pts = np.array(pts, np.int32)

        if filled:
            cv2.fillPoly(img, [pts], (r, g, b, 255), cv2.LINE_AA)
        else:
            thickness = max(1, int(line_width / 100))
            cv2.polylines(img, [pts], True, (r, g, b, 255), thickness, cv2.LINE_AA)

        return img

    def _draw_star(
        self,
        width: int,
        height: int,
        r: int,
        g: int,
        b: int,
        filled: bool,
        line_width: float,
    ) -> np.ndarray:
        """Draw a 5-pointed star."""
        padding = 4
        canvas_w = width + padding * 2
        canvas_h = height + padding * 2

        img = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)

        cx = canvas_w // 2
        cy = canvas_h // 2
        outer_r_x = width // 2
        outer_r_y = height // 2
        inner_r_x = outer_r_x * 0.4
        inner_r_y = outer_r_y * 0.4

        # Generate star vertices (alternating outer and inner)
        pts = []
        for i in range(10):
            angle = math.pi * i / 5 - math.pi / 2  # Start from top
            if i % 2 == 0:
                x = int(cx + outer_r_x * math.cos(angle))
                y = int(cy + outer_r_y * math.sin(angle))
            else:
                x = int(cx + inner_r_x * math.cos(angle))
                y = int(cy + inner_r_y * math.sin(angle))
            pts.append([x, y])

        pts = np.array(pts, np.int32)

        if filled:
            cv2.fillPoly(img, [pts], (r, g, b, 255), cv2.LINE_AA)
        else:
            thickness = max(1, int(line_width / 100))
            cv2.polylines(img, [pts], True, (r, g, b, 255), thickness, cv2.LINE_AA)

        return img

    def _draw_heart(
        self,
        width: int,
        height: int,
        r: int,
        g: int,
        b: int,
        filled: bool,
        line_width: float,
    ) -> np.ndarray:
        """Draw a heart shape."""
        padding = 4
        canvas_w = width + padding * 2
        canvas_h = height + padding * 2

        img = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)

        cx = canvas_w // 2
        cy = canvas_h // 2

        # Generate heart shape using parametric equations
        pts = []
        for t in range(0, 360, 5):
            t_rad = math.radians(t)
            x = 16 * (math.sin(t_rad) ** 3)
            y = -(
                13 * math.cos(t_rad)
                - 5 * math.cos(2 * t_rad)
                - 2 * math.cos(3 * t_rad)
                - math.cos(4 * t_rad)
            )

            # Scale to fit
            x = int(cx + x * width / 40)
            y = int(cy + y * height / 40)
            pts.append([x, y])

        pts = np.array(pts, np.int32)

        if filled:
            cv2.fillPoly(img, [pts], (r, g, b, 255), cv2.LINE_AA)
        else:
            thickness = max(1, int(line_width / 100))
            cv2.polylines(img, [pts], True, (r, g, b, 255), thickness, cv2.LINE_AA)

        return img

    def _draw_background(
        self,
        width: int,
        height: int,
        r: int,
        g: int,
        b: int,
    ) -> np.ndarray:
        """Draw a solid background."""
        img = np.zeros((height, width, 4), dtype=np.uint8)
        img[:, :] = (r, g, b, 255)
        return img
