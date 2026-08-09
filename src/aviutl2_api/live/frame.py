"""Typed AviUtl2-native frame render results."""

from __future__ import annotations

import base64
import hashlib
import io
import math
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw

from .snapshot import ProjectSnapshot


@dataclass(frozen=True, slots=True)
class RenderedFrame:
    """A PNG rendered by the currently running AviUtl2 process."""

    frame: int
    width: int
    height: int
    scene_id: int
    revision: int
    sha256: str
    png: bytes

    def save(
        self,
        path: str | PathLike[str],
        *,
        overwrite: bool = False,
    ) -> Path:
        destination = Path(path).expanduser().resolve()
        if destination.exists() and not overwrite:
            raise FileExistsError(
                f"refusing to overwrite existing frame: {destination}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.png)
        return destination

    def preview(
        self,
        *,
        max_width: int | None = 480,
        max_height: int | None = None,
        format: Literal["jpg", "jpeg", "png"] = "jpeg",
        quality: int = 85,
    ) -> RenderedPreview:
        """Create a compact in-memory image suitable for an agent adapter."""

        return make_preview(
            self,
            max_width=max_width,
            max_height=max_height,
            format=format,
            quality=quality,
        )


@dataclass(frozen=True, slots=True)
class RenderedPreview:
    """A compact rendered image ready to attach to a model vision input."""

    frame: int
    width: int
    height: int
    scene_id: int
    revision: int
    sha256: str
    mime_type: str
    data: bytes

    def save(
        self,
        path: str | PathLike[str],
        *,
        overwrite: bool = False,
    ) -> Path:
        destination = Path(path).expanduser().resolve()
        if destination.exists() and not overwrite:
            raise FileExistsError(
                f"refusing to overwrite existing preview: {destination}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.data)
        return destination

    def to_base64(self) -> str:
        """Return ASCII base64 for transports that cannot carry binary data."""

        return base64.b64encode(self.data).decode("ascii")

    def to_data_url(self) -> str:
        """Return a directly embeddable image data URL."""

        return f"data:{self.mime_type};base64,{self.to_base64()}"


def make_preview(
    frame: RenderedFrame,
    *,
    max_width: int | None = 480,
    max_height: int | None = None,
    format: Literal["jpg", "jpeg", "png"] = "jpeg",
    quality: int = 85,
) -> RenderedPreview:
    """Resize one native frame without writing a host-side temporary file."""

    if max_width is None and max_height is None:
        raise ValueError("max_width or max_height is required")
    if max_width is not None and max_width < 1:
        raise ValueError("max_width must be positive or None")
    if max_height is not None and max_height < 1:
        raise ValueError("max_height must be positive or None")
    normalized_format = "jpeg" if format == "jpg" else format
    if normalized_format not in {"jpeg", "png"}:
        raise ValueError("format must be 'jpeg', 'jpg', or 'png'")
    if not 1 <= quality <= 95:
        raise ValueError("quality must be between 1 and 95")
    with Image.open(io.BytesIO(frame.png)) as source:
        image = source.convert("RGB")
        width_limit = max_width if max_width is not None else image.width
        height_limit = max_height if max_height is not None else image.height
        image.thumbnail((width_limit, height_limit), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        if normalized_format == "jpeg":
            image.save(output, format="JPEG", quality=quality, optimize=True)
            mime_type = "image/jpeg"
        else:
            image.save(output, format="PNG", optimize=True)
            mime_type = "image/png"
        data = output.getvalue()
        return RenderedPreview(
            frame=frame.frame,
            width=image.width,
            height=image.height,
            scene_id=frame.scene_id,
            revision=frame.revision,
            sha256=hashlib.sha256(data).hexdigest(),
            mime_type=mime_type,
            data=data,
        )


@dataclass(frozen=True, slots=True)
class ContactSheet:
    """An in-memory PNG assembled only from AviUtl2-native frame renders."""

    frames: tuple[int, ...]
    revision: int
    width: int
    height: int
    png: bytes

    def save(
        self,
        path: str | PathLike[str],
        *,
        overwrite: bool = False,
    ) -> Path:
        destination = Path(path).expanduser().resolve()
        if destination.exists() and not overwrite:
            raise FileExistsError(
                f"refusing to overwrite existing contact sheet: {destination}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.png)
        return destination


def make_contact_sheet(
    frames: tuple[RenderedFrame, ...],
    *,
    columns: int = 4,
    thumbnail_width: int = 320,
    label_height: int = 24,
) -> ContactSheet:
    if not frames or columns < 1 or thumbnail_width < 32 or label_height < 0:
        raise ValueError("contact sheet dimensions are invalid")
    if len(frames) > 64:
        raise ValueError("a contact sheet supports at most 64 frames")
    revisions = {frame.revision for frame in frames}
    if len(revisions) != 1:
        raise ValueError("contact sheet frames must share one revision")
    first = frames[0]
    aspect = first.height / first.width
    thumbnail_height = max(1, round(thumbnail_width * aspect))
    rows = math.ceil(len(frames) / columns)
    sheet = Image.new(
        "RGBA",
        (
            columns * thumbnail_width,
            rows * (thumbnail_height + label_height),
        ),
        (24, 24, 24, 255),
    )
    draw = ImageDraw.Draw(sheet)
    for index, rendered in enumerate(frames):
        with Image.open(io.BytesIO(rendered.png)) as source:
            image = source.convert("RGBA")
            image.thumbnail(
                (thumbnail_width, thumbnail_height),
                Image.Resampling.LANCZOS,
            )
            column = index % columns
            row = index // columns
            x = column * thumbnail_width
            y = row * (thumbnail_height + label_height)
            sheet.alpha_composite(image, (x, y))
            if label_height:
                draw.text(
                    (x + 6, y + thumbnail_height + 4),
                    f"frame {rendered.frame}",
                    fill=(255, 255, 255, 255),
                )
    output = io.BytesIO()
    sheet.convert("RGB").save(output, format="PNG", optimize=True)
    return ContactSheet(
        frames=tuple(frame.frame for frame in frames),
        revision=first.revision,
        width=sheet.width,
        height=sheet.height,
        png=output.getvalue(),
    )


def review_sample_frames(
    snapshot: ProjectSnapshot,
    *,
    boundary_padding: int = 1,
    include_midpoints: bool = True,
    max_frames: int = 64,
) -> tuple[int, ...]:
    """Choose cut/subtitle boundaries directly from a fresh snapshot."""
    if boundary_padding < 0 or max_frames < 1:
        raise ValueError("review sampling limits are invalid")
    candidates: set[int] = {0}
    for obj in snapshot.objects:
        candidates.update(
            {
                max(0, obj.frame_start - boundary_padding),
                obj.frame_start,
                obj.frame_end,
                obj.frame_end + boundary_padding,
            }
        )
        if include_midpoints:
            candidates.add((obj.frame_start + obj.frame_end) // 2)
    ordered = sorted(candidates)
    if len(ordered) <= max_frames:
        return tuple(ordered)
    if max_frames == 1:
        return (ordered[0],)
    selected_indices = {
        round(index * (len(ordered) - 1) / (max_frames - 1))
        for index in range(max_frames)
    }
    return tuple(ordered[index] for index in sorted(selected_indices))


__all__ = [
    "ContactSheet",
    "RenderedFrame",
    "RenderedPreview",
    "make_contact_sheet",
    "make_preview",
    "review_sample_frames",
]
