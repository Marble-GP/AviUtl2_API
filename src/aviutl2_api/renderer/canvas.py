"""Frame buffer and canvas management for rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from PIL import Image


@dataclass
class FrameBuffer:
    """RGBA frame buffer with numpy backend.

    The coordinate system uses center-origin:
    - (0, 0) is the center of the canvas
    - X increases to the right
    - Y increases downward

    Attributes:
        width: Canvas width in pixels
        height: Canvas height in pixels
        data: RGBA pixel data as numpy array, shape (height, width, 4)
    """

    width: int
    height: int
    data: np.ndarray = field(repr=False)

    @classmethod
    def create(
        cls,
        width: int,
        height: int,
        background: tuple[int, int, int, int] = (0, 0, 0, 255),
    ) -> FrameBuffer:
        """Create a new frame buffer with specified background color.

        Args:
            width: Canvas width in pixels
            height: Canvas height in pixels
            background: RGBA background color tuple (default: opaque black)

        Returns:
            New FrameBuffer instance
        """
        data = np.zeros((height, width, 4), dtype=np.uint8)
        data[:, :] = background
        return cls(width=width, height=height, data=data)

    def canvas_to_pixel(self, x: float, y: float) -> tuple[int, int]:
        """Convert canvas coordinates (center-origin) to pixel coordinates.

        Args:
            x: X coordinate in canvas space (center = 0)
            y: Y coordinate in canvas space (center = 0)

        Returns:
            Tuple of (pixel_x, pixel_y) in image space
        """
        px = int(self.width / 2 + x)
        py = int(self.height / 2 + y)
        return px, py

    def pixel_to_canvas(self, px: int, py: int) -> tuple[float, float]:
        """Convert pixel coordinates to canvas coordinates (center-origin).

        Args:
            px: Pixel X coordinate
            py: Pixel Y coordinate

        Returns:
            Tuple of (canvas_x, canvas_y) in canvas space
        """
        x = px - self.width / 2
        y = py - self.height / 2
        return x, y

    def to_pil(self) -> Image.Image:
        """Convert frame buffer to PIL Image.

        Returns:
            PIL Image in RGBA mode
        """
        from PIL import Image

        return Image.fromarray(self.data, mode="RGBA")

    def to_numpy(self) -> np.ndarray:
        """Get the raw numpy array.

        Returns:
            Numpy array of shape (height, width, 4) with dtype uint8
        """
        return self.data

    def save(self, path: Path | str, format: str | None = None) -> None:
        """Save frame buffer to image file.

        Args:
            path: Output file path
            format: Image format (e.g., "PNG", "JPEG"). If None, inferred from path.
        """
        img = self.to_pil()
        img.save(path, format=format)

    def resize(
        self,
        width: int | None = None,
        height: int | None = None,
        scale: float | None = None,
        maintain_aspect: bool = True,
    ) -> tuple["FrameBuffer", list[str]]:
        """リサイズされたFrameBufferを返す。

        Args:
            width: 目標幅（ピクセル）
            height: 目標高さ（ピクセル）
            scale: スケール係数（例: 0.5で50%縮小）
            maintain_aspect: アスペクト比を維持するか（デフォルト: True）

        Returns:
            (リサイズされたFrameBuffer, 警告リスト)のタプル

        Note:
            - scale指定時はwidth/heightは無視される
            - width/heightのみ指定時、maintain_aspect=Trueなら比率維持
            - 両方指定でmaintain_aspect=Falseならアスペクト比変更
        """
        import cv2

        warnings: list[str] = []

        if scale is not None:
            # スケール指定
            new_width = int(self.width * scale)
            new_height = int(self.height * scale)
        elif width is not None and height is not None:
            if maintain_aspect:
                # アスペクト比維持で収まるサイズ
                orig_aspect = self.width / self.height
                target_aspect = width / height

                if abs(orig_aspect - target_aspect) > 0.01:
                    warnings.append(
                        f"警告: 指定サイズ({width}x{height})のアスペクト比が"
                        f"元({self.width}x{self.height})と異なります。比率を維持します。"
                    )

                if orig_aspect > target_aspect:
                    new_width = width
                    new_height = int(width / orig_aspect)
                else:
                    new_height = height
                    new_width = int(height * orig_aspect)
            else:
                # アスペクト比変更
                orig_aspect = self.width / self.height
                target_aspect = width / height
                if abs(orig_aspect - target_aspect) > 0.01:
                    warnings.append(
                        f"警告: アスペクト比が変更されます "
                        f"({self.width}:{self.height} → {width}:{height})"
                    )
                new_width = width
                new_height = height
        elif width is not None:
            # 幅のみ指定
            new_width = width
            new_height = int(self.height * (width / self.width))
        elif height is not None:
            # 高さのみ指定
            new_height = height
            new_width = int(self.width * (height / self.height))
        else:
            # 何も指定なし
            return self.copy(), warnings

        # 最小サイズチェック
        new_width = max(1, new_width)
        new_height = max(1, new_height)

        # 縮小率計算
        scale_factor = min(new_width / self.width, new_height / self.height)

        # 品質低下警告
        if scale_factor < 0.25:
            warnings.append(
                f"警告: 縮小率が25%未満({scale_factor:.1%})のため、"
                "細部が判別困難になる可能性があります。"
            )
        elif scale_factor < 0.5:
            warnings.append(
                f"注意: 縮小率が50%未満({scale_factor:.1%})です。"
                "テキストや細い線が見づらくなる可能性があります。"
            )

        # リサイズ実行（LANCZOSで高品質）
        resized = cv2.resize(
            self.data,
            (new_width, new_height),
            interpolation=cv2.INTER_LANCZOS4 if scale_factor < 1.0 else cv2.INTER_LINEAR,
        )

        return FrameBuffer(width=new_width, height=new_height, data=resized), warnings

    def copy(self) -> FrameBuffer:
        """Create a copy of this frame buffer.

        Returns:
            New FrameBuffer with copied data
        """
        return FrameBuffer(
            width=self.width,
            height=self.height,
            data=self.data.copy(),
        )

    def clear(self, color: tuple[int, int, int, int] = (0, 0, 0, 255)) -> None:
        """Clear the buffer with specified color.

        Args:
            color: RGBA color tuple to fill with
        """
        self.data[:, :] = color

    def blit(
        self,
        source: np.ndarray,
        dest_x: int,
        dest_y: int,
        src_x: int = 0,
        src_y: int = 0,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        """Copy pixels from source array to this buffer (simple copy, no blending).

        Args:
            source: Source RGBA array
            dest_x: Destination X coordinate in this buffer
            dest_y: Destination Y coordinate in this buffer
            src_x: Source X offset (default 0)
            src_y: Source Y offset (default 0)
            width: Width to copy (default: full source width)
            height: Height to copy (default: full source height)
        """
        src_h, src_w = source.shape[:2]
        width = width or src_w
        height = height or src_h

        # Calculate actual copy region with clipping
        # Source bounds
        sx1 = max(0, src_x)
        sy1 = max(0, src_y)
        sx2 = min(src_w, src_x + width)
        sy2 = min(src_h, src_y + height)

        # Adjust for source offset
        dx1 = dest_x + (sx1 - src_x)
        dy1 = dest_y + (sy1 - src_y)

        # Destination bounds
        dx1 = max(0, dx1)
        dy1 = max(0, dy1)
        dx2 = min(self.width, dest_x + width)
        dy2 = min(self.height, dest_y + height)

        # Calculate final dimensions
        copy_w = min(sx2 - sx1, dx2 - dx1)
        copy_h = min(sy2 - sy1, dy2 - dy1)

        if copy_w <= 0 or copy_h <= 0:
            return

        # Perform copy
        self.data[dy1 : dy1 + copy_h, dx1 : dx1 + copy_w] = source[
            sy1 : sy1 + copy_h, sx1 : sx1 + copy_w
        ]


def parse_hex_color(color_str: str) -> tuple[int, int, int]:
    """Parse a hex color string to RGB tuple.

    Args:
        color_str: 6-digit hex color string (e.g., "ff0000" for red)

    Returns:
        Tuple of (R, G, B) values, each 0-255
    """
    if len(color_str) != 6:
        return (255, 255, 255)  # Default white

    try:
        r = int(color_str[0:2], 16)
        g = int(color_str[2:4], 16)
        b = int(color_str[4:6], 16)
        return (r, g, b)
    except ValueError:
        return (255, 255, 255)


def rgba_from_hex(color_str: str, alpha: int = 255) -> tuple[int, int, int, int]:
    """Convert hex color string to RGBA tuple.

    Args:
        color_str: 6-digit hex color string
        alpha: Alpha value 0-255

    Returns:
        Tuple of (R, G, B, A)
    """
    r, g, b = parse_hex_color(color_str)
    return (r, g, b, alpha)
