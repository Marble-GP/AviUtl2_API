"""Main frame renderer orchestrating all components."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from aviutl2_api.renderer.blend import blend_onto, opacity_from_transparency
from aviutl2_api.renderer.canvas import FrameBuffer
from aviutl2_api.renderer.content.base import ContentRenderer, RenderContext
from aviutl2_api.renderer.interpolation import (
    get_property_string,
    get_property_value_at_frame,
)
from aviutl2_api.renderer.transform import apply_transform

if TYPE_CHECKING:
    from aviutl2_api.models import Effect, Project, Scene, TimelineObject


@dataclass
class RenderResult:
    """Result of a frame render operation.

    Attributes:
        frame: The rendered frame number
        buffer: The rendered frame buffer
        warnings: List of warning messages
        errors: List of error messages
        missing_media: List of missing media file paths
        render_time_ms: Render time in milliseconds
    """

    frame: int
    buffer: FrameBuffer
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    missing_media: list[str] = field(default_factory=list)
    render_time_ms: float = 0.0

    @property
    def success(self) -> bool:
        """Check if render completed without errors."""
        return len(self.errors) == 0


class FrameRenderer:
    """Main renderer for AviUtl2 project frames.

    This class orchestrates the rendering of individual frames from an
    AviUtl2 project, handling object layering, transformations, effects,
    and compositing.

    Usage:
        project = parse_file("project.aup2")
        renderer = FrameRenderer(project)
        result = renderer.render_frame(150)
        result.buffer.save("preview.png")
    """

    def __init__(
        self,
        project: Project,
        scene_id: int = 0,
        background_color: tuple[int, int, int, int] = (0, 0, 0, 255),
    ):
        """Initialize frame renderer.

        Args:
            project: The AviUtl2 project to render
            scene_id: Scene index to render (default: 0)
            background_color: Background color as RGBA tuple
        """
        self.project = project
        self.scene = project.get_scene(scene_id)
        self.background_color = background_color

        if self.scene is None:
            raise ValueError(f"Scene {scene_id} not found in project")

        # Register content renderers
        self._content_renderers: list[ContentRenderer] = []
        self._register_default_renderers()

        # Filter effects registry
        self._filter_effects: dict[str, object] = {}
        self._register_default_filters()

    def _register_default_renderers(self) -> None:
        """Register default content renderers."""
        # Import here to avoid circular imports
        try:
            from aviutl2_api.renderer.content.shape import ShapeRenderer

            self._content_renderers.append(ShapeRenderer())
        except ImportError:
            pass

        try:
            from aviutl2_api.renderer.content.text import TextRenderer

            self._content_renderers.append(TextRenderer())
        except ImportError:
            pass

        try:
            from aviutl2_api.renderer.content.image import ImageRenderer

            self._content_renderers.append(ImageRenderer())
        except ImportError:
            pass

        try:
            from aviutl2_api.renderer.content.video import VideoRenderer

            self._content_renderers.append(VideoRenderer())
        except ImportError:
            pass

    def _register_default_filters(self) -> None:
        """Register default filter effects."""
        from aviutl2_api.renderer.filters.base import FilterEffect

        self._filter_effects: list[FilterEffect] = []

        try:
            from aviutl2_api.renderer.filters.blur import BlurFilter

            self._filter_effects.append(BlurFilter())
        except ImportError:
            pass

        try:
            from aviutl2_api.renderer.filters.border import BorderFilter

            self._filter_effects.append(BorderFilter())
        except ImportError:
            pass

        try:
            from aviutl2_api.renderer.filters.shadow import ShadowFilter

            self._filter_effects.append(ShadowFilter())
        except ImportError:
            pass

        try:
            from aviutl2_api.renderer.filters.glow import GlowFilter

            self._filter_effects.append(GlowFilter())
        except ImportError:
            pass

    def register_content_renderer(self, renderer: ContentRenderer) -> None:
        """Register a custom content renderer.

        Args:
            renderer: ContentRenderer instance to register
        """
        self._content_renderers.append(renderer)

    def render_frame(self, frame: int) -> RenderResult:
        """Render a single frame.

        Args:
            frame: Frame number to render

        Returns:
            RenderResult containing the rendered buffer and status
        """
        if self.scene is None:
            return RenderResult(
                frame=frame,
                buffer=FrameBuffer.create(1, 1),
                errors=["No scene available"],
            )

        start_time = time.time()

        # Create render context
        context = RenderContext(
            scene_width=self.scene.width,
            scene_height=self.scene.height,
            scene_fps=self.scene.fps,
        )

        # Create canvas
        canvas = FrameBuffer.create(
            self.scene.width,
            self.scene.height,
            self.background_color,
        )

        # Get objects at this frame, sorted by layer (lower layers first = back)
        objects = self.scene.get_objects_at_frame(frame)
        objects = sorted(objects, key=lambda o: o.layer)

        # Render each object
        for obj in objects:
            try:
                self._render_object(obj, frame, canvas, context)
            except Exception as e:
                context.add_error(f"Object {obj.object_id}: {e}")

        render_time = (time.time() - start_time) * 1000

        return RenderResult(
            frame=frame,
            buffer=canvas,
            warnings=context.warnings,
            errors=context.errors,
            missing_media=context.missing_files,
            render_time_ms=render_time,
        )

    def render_frames(self, frames: list[int]) -> list[RenderResult]:
        """Render multiple frames.

        Args:
            frames: List of frame numbers to render

        Returns:
            List of RenderResult for each frame
        """
        return [self.render_frame(f) for f in frames]

    def render_strip(
        self,
        start: int,
        end: int,
        interval: int = 30,
        padding: int = 2,
    ) -> RenderResult:
        """Render a filmstrip of frames horizontally combined.

        Args:
            start: Start frame
            end: End frame
            interval: Frame interval
            padding: Padding between frames in pixels

        Returns:
            RenderResult with combined filmstrip image
        """
        if self.scene is None:
            return RenderResult(
                frame=start,
                buffer=FrameBuffer.create(1, 1),
                errors=["No scene available"],
            )

        start_time = time.time()

        # Calculate frames to render
        frames = list(range(start, end + 1, interval))
        if not frames:
            frames = [start]

        # Render each frame
        results = self.render_frames(frames)

        # Calculate strip dimensions
        frame_w = self.scene.width
        frame_h = self.scene.height
        strip_w = len(frames) * frame_w + (len(frames) - 1) * padding
        strip_h = frame_h

        # Create strip canvas
        strip = FrameBuffer.create(strip_w, strip_h, (32, 32, 32, 255))

        # Combine warnings and errors
        all_warnings: list[str] = []
        all_errors: list[str] = []
        all_missing: list[str] = []

        # Place each frame
        for i, result in enumerate(results):
            x = i * (frame_w + padding)
            strip.blit(result.buffer.data, x, 0)
            all_warnings.extend(result.warnings)
            all_errors.extend(result.errors)
            all_missing.extend(result.missing_media)

        render_time = (time.time() - start_time) * 1000

        return RenderResult(
            frame=start,
            buffer=strip,
            warnings=list(set(all_warnings)),
            errors=list(set(all_errors)),
            missing_media=list(set(all_missing)),
            render_time_ms=render_time,
        )

    def _render_object(
        self,
        obj: TimelineObject,
        frame: int,
        canvas: FrameBuffer,
        context: RenderContext,
    ) -> None:
        """Render a single timeline object onto the canvas.

        Args:
            obj: Timeline object to render
            frame: Current frame number
            canvas: Target canvas buffer
            context: Render context
        """
        # Get main effect (first effect defines object type)
        main_effect = obj.main_effect
        if main_effect is None:
            return

        # Find a renderer that can handle this effect
        content_array = None
        for renderer in self._content_renderers:
            if renderer.can_render(main_effect.name):
                try:
                    content_array, size = renderer.render(
                        main_effect, frame, obj, self.scene
                    )
                except Exception as e:
                    context.add_warning(
                        f"Content render failed for {main_effect.name}: {e}"
                    )
                break

        if content_array is None:
            # No renderer available - create placeholder
            content_array = self._create_placeholder(main_effect.name)

        # Get standard draw effect for transforms
        draw_effect = obj.get_effect("標準描画")
        if draw_effect is None:
            draw_effect = obj.get_effect("映像再生")

        if draw_effect is None:
            # No draw effect - just place content at center
            dest_x = (canvas.width - content_array.shape[1]) // 2
            dest_y = (canvas.height - content_array.shape[0]) // 2
            blend_onto(canvas.data, content_array, dest_x, dest_y, "通常", 1.0)
            return

        # Get transform properties
        x = get_property_value_at_frame(draw_effect.properties, "X", frame, obj, 0.0)
        y = get_property_value_at_frame(draw_effect.properties, "Y", frame, obj, 0.0)
        z = get_property_value_at_frame(draw_effect.properties, "Z", frame, obj, 0.0)

        center_x = get_property_value_at_frame(
            draw_effect.properties, "中心X", frame, obj, 0.0
        )
        center_y = get_property_value_at_frame(
            draw_effect.properties, "中心Y", frame, obj, 0.0
        )
        center_z = get_property_value_at_frame(
            draw_effect.properties, "中心Z", frame, obj, 0.0
        )

        x_rotation = get_property_value_at_frame(
            draw_effect.properties, "X軸回転", frame, obj, 0.0
        )
        y_rotation = get_property_value_at_frame(
            draw_effect.properties, "Y軸回転", frame, obj, 0.0
        )
        z_rotation = get_property_value_at_frame(
            draw_effect.properties, "Z軸回転", frame, obj, 0.0
        )

        scale = get_property_value_at_frame(
            draw_effect.properties, "拡大率", frame, obj, 100.0
        )
        aspect = get_property_value_at_frame(
            draw_effect.properties, "縦横比", frame, obj, 0.0
        )
        transparency = get_property_value_at_frame(
            draw_effect.properties, "透明度", frame, obj, 0.0
        )

        blend_mode = get_property_string(
            draw_effect.properties, "合成モード", "通常"
        )

        # Apply transforms
        transformed, dest_x, dest_y = apply_transform(
            content_array,
            x,
            y,
            z,
            center_x,
            center_y,
            center_z,
            x_rotation,
            y_rotation,
            z_rotation,
            scale,
            aspect,
            (canvas.width, canvas.height),
        )

        # Apply filter effects (Phase 3)
        for effect in obj.effects[2:]:  # Skip main effect and draw effect
            transformed = self._apply_filter(transformed, effect, frame, obj, context)

        # Calculate opacity
        opacity = opacity_from_transparency(transparency)

        # Composite onto canvas
        if opacity > 0 and transformed.size > 0:
            blend_onto(canvas.data, transformed, dest_x, dest_y, blend_mode, opacity)

    def _apply_filter(
        self,
        image: np.ndarray,
        effect: Effect,
        frame: int,
        obj: TimelineObject,
        context: RenderContext,
    ) -> np.ndarray:
        """Apply a filter effect to an image.

        Args:
            image: Source image
            effect: Filter effect to apply
            frame: Current frame
            obj: Parent timeline object
            context: Render context

        Returns:
            Filtered image
        """
        # Find a filter that can handle this effect
        for filter_effect in self._filter_effects:
            if filter_effect.can_apply(effect.name):
                try:
                    return filter_effect.apply(image, effect, frame, obj)
                except Exception as e:
                    context.add_warning(f"Filter {effect.name} failed: {e}")
                    return image

        # No filter found - return unchanged
        return image

    def _create_placeholder(self, effect_name: str) -> np.ndarray:
        """Create a placeholder image for unsupported content types.

        Args:
            effect_name: Name of the unsupported effect

        Returns:
            Placeholder RGBA image
        """
        # Create a simple gray box with text
        from PIL import Image, ImageDraw

        size = 200
        img = Image.new("RGBA", (size, size), (128, 128, 128, 200))
        draw = ImageDraw.Draw(img)

        # Draw border
        draw.rectangle([0, 0, size - 1, size - 1], outline=(200, 200, 200, 255))

        # Draw text (simplified - just first character)
        if effect_name:
            text = effect_name[:4] if len(effect_name) > 4 else effect_name
            draw.text((10, size // 2 - 10), text, fill=(255, 255, 255, 255))

        return np.array(img)
