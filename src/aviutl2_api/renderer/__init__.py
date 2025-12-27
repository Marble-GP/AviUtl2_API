"""Frame Preview Renderer for AviUtl2 projects.

This module provides functionality to render preview images of AviUtl2 project
timelines at specific frames, enabling Vision-capable LLMs to verify layout
and composition.

Usage:
    from aviutl2_api import parse_file
    from aviutl2_api.renderer import FrameRenderer

    project = parse_file("project.aup2")
    renderer = FrameRenderer(project)

    # Render single frame
    result = renderer.render_frame(150)
    result.buffer.save("preview.png")

    # Render with PIL image
    pil_image = result.buffer.to_pil()

    # Render filmstrip
    strip = renderer.render_strip(0, 300, interval=30)
    strip.buffer.save("timeline.png")
"""

from __future__ import annotations

from aviutl2_api.renderer.canvas import FrameBuffer
from aviutl2_api.renderer.core import FrameRenderer, RenderResult
from aviutl2_api.renderer.interpolation import interpolate_value

__all__ = [
    "FrameRenderer",
    "RenderResult",
    "FrameBuffer",
    "interpolate_value",
]
