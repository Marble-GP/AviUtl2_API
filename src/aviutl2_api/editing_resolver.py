"""Shared semantic templates used by local and Live editing backends."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from aviutl2_api.editing import (
    FramePosition,
    LinearMotion,
    PlanSequence,
    Transform,
    TransformValue,
)
from aviutl2_api.models import (
    AnimatedValue,
    AnimationParams,
    Effect,
    StaticValue,
    TimelineObject,
)


@dataclass(frozen=True, slots=True)
class TimelineRange:
    layer: int
    frame_start: int
    frame_end: int
    reference: str


def resolve_frame(
    at: FramePosition,
    *,
    sequence: PlanSequence,
    cursor_frame: int,
    serial_frame: int,
    ranges: Sequence[TimelineRange],
) -> int:
    if at == "end":
        return max((value.frame_end for value in ranges), default=-1) + 1
    if at is None:
        return serial_frame if sequence == "serial" else cursor_frame
    return at


def conflicts(
    ranges: Sequence[TimelineRange],
    *,
    layer: int,
    frame: int,
    duration: int,
) -> tuple[str, ...]:
    frame_end = frame + duration - 1
    return tuple(
        value.reference
        for value in ranges
        if value.layer == layer
        and value.frame_start <= frame_end
        and frame <= value.frame_end
    )


def suggested_layer(
    ranges: Sequence[TimelineRange],
    *,
    frame: int,
    duration: int,
    locked_layers: set[int] | frozenset[int] = frozenset(),
    layer_max: int = 999,
) -> int | None:
    return next(
        (
            candidate
            for candidate in range(layer_max + 1)
            if candidate not in locked_layers
            and not conflicts(
                ranges,
                layer=candidate,
                frame=frame,
                duration=duration,
            )
        ),
        None,
    )


def normalized_color(value: str) -> str:
    color = value.removeprefix("#").lower()
    if len(color) != 6 or any(char not in "0123456789abcdef" for char in color):
        raise ValueError("color must contain six hexadecimal RGB digits")
    return color


def number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    return float(value)


def offset_transform_value(
    value: TransformValue | None,
    offset: float,
) -> TransformValue:
    if isinstance(value, LinearMotion):
        return LinearMotion(value.start + offset, value.end + offset)
    return (0.0 if value is None else float(value)) + offset


def image_exif_orientation(path: Path) -> int:
    try:
        with Image.open(path) as image:
            raw = image.getexif().get(274, 1)
    except (OSError, ValueError) as error:
        raise ValueError(f"could not read image EXIF orientation: {path}") from error
    if isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= 8:
        return raw
    return 1


def track_value(
    value: TransformValue | None,
    *,
    default: float = 0.0,
    invert_opacity: bool = False,
) -> StaticValue | AnimatedValue:
    def convert(raw: float) -> float:
        return (1.0 - raw) * 100.0 if invert_opacity else raw

    if value is None:
        return StaticValue(convert(default))
    if isinstance(value, LinearMotion):
        return AnimatedValue(
            convert(value.start),
            convert(value.end),
            AnimationParams("直線移動", "0"),
        )
    return StaticValue(convert(float(value)))


def draw_properties(transform: Transform) -> dict[str, object]:
    return {
        "X": track_value(transform.x),
        "Y": track_value(transform.y),
        "Z": track_value(transform.z),
        "Group": StaticValue(1.0),
        "中心X": StaticValue(0.0),
        "中心Y": StaticValue(0.0),
        "中心Z": StaticValue(0.0),
        "X軸回転": track_value(transform.rotation_x),
        "Y軸回転": track_value(transform.rotation_y),
        "Z軸回転": track_value(transform.effective_rotation_z),
        "拡大率": track_value(transform.scale, default=100.0),
        "縦横比": StaticValue(0.0),
        "透明度": track_value(
            transform.opacity,
            default=1.0,
            invert_opacity=True,
        ),
        "合成モード": "通常",
    }


def video_object(
    path: Path,
    *,
    layer: int,
    frame: int,
    duration: int,
    transform: Transform,
    include_audio: bool,
) -> TimelineObject:
    """Build the canonical Alias fallback for one combined video object."""

    if duration < 1:
        raise ValueError("duration must be positive")
    playback = draw_properties(transform)
    playback.update(
        {
            "音量": StaticValue(100.0),
            "左右": StaticValue(0.0),
        }
    )
    return TimelineObject(
        object_id=0,
        layer=layer,
        frame_start=frame,
        frame_end=frame + duration - 1,
        effects=[
            Effect(
                effect_id=0,
                name="動画ファイル",
                properties={
                    "再生位置": "0.000,0.000,再生範囲,0",
                    "再生速度": StaticValue(100.0),
                    "ファイル": str(path.expanduser().resolve()),
                    "トラック": 0,
                    "ループ再生": False,
                    "音声付き": include_audio,
                    "YUV": "",
                    "fps調整": False,
                },
            ),
            Effect(effect_id=1, name="映像再生", properties=playback),
        ],
    )


def text_object(
    text: str,
    *,
    layer: int,
    frame: int,
    length: int,
    x: float = 0.0,
    y: float = 0.0,
    size: float = 32.0,
    color: str = "ffffff",
) -> TimelineObject:
    if length < 1:
        raise ValueError("length must be positive")
    return TimelineObject(
        object_id=0,
        layer=layer,
        frame_start=frame,
        frame_end=frame + length - 1,
        effects=[
            Effect(
                effect_id=0,
                name="テキスト",
                properties={
                    "サイズ": StaticValue(float(size)),
                    "文字色": normalized_color(color),
                    "テキスト": text,
                },
            ),
            Effect(
                effect_id=1,
                name="標準描画",
                properties={
                    "X": StaticValue(float(x)),
                    "Y": StaticValue(float(y)),
                },
            ),
        ],
    )


def shape_object(
    shape: str,
    *,
    layer: int,
    frame: int,
    duration: int,
    color: str,
    width: float,
    height: float,
    transform: Transform,
) -> TimelineObject:
    names = {
        "circle": "円",
        "rectangle": "四角形",
        "triangle": "三角形",
        "pentagon": "五角形",
        "hexagon": "六角形",
        "star": "星型",
        "heart": "ハート",
        "background": "背景",
    }
    if shape not in names:
        raise ValueError(
            "shape must be circle, rectangle, triangle, pentagon, hexagon, "
            "star, heart, or background"
        )
    if width <= 0.0 or height <= 0.0:
        raise ValueError("shape width/height must be positive")
    aspect = (height / width - 1.0) * 100.0
    return TimelineObject(
        object_id=0,
        layer=layer,
        frame_start=frame,
        frame_end=frame + duration - 1,
        effects=[
            Effect(
                effect_id=0,
                name="図形",
                properties={
                    "図形の種類": names[shape],
                    "サイズ": StaticValue(float(width)),
                    "縦横比": StaticValue(aspect),
                    "ライン幅": StaticValue(4000.0),
                    "色": normalized_color(color),
                    "角を丸くする": StaticValue(0.0),
                },
            ),
            Effect(1, "標準描画", draw_properties(transform)),
        ],
    )


__all__ = [
    "TimelineRange",
    "conflicts",
    "draw_properties",
    "image_exif_orientation",
    "normalized_color",
    "number",
    "offset_transform_value",
    "resolve_frame",
    "shape_object",
    "suggested_layer",
    "text_object",
    "track_value",
]
