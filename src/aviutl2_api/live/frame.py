"""Typed AviUtl2-native frame render results."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from os import PathLike
from pathlib import Path

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
    if (
        not frames
        or columns < 1
        or thumbnail_width < 32
        or label_height < 0
    ):
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
    "make_contact_sheet",
    "review_sample_frames",
]
