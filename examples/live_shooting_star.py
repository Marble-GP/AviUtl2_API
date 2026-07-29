"""Create and natively render a practical shooting-star animation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from aviutl2_api.live import (
    CreateFromAliasCommand,
    LiveClient,
    discover_instances,
)
from aviutl2_api.models import (
    AnimatedValue,
    AnimationParams,
    Effect,
    StaticValue,
    TimelineObject,
)


def moving_value(start: float, end: float) -> AnimatedValue:
    return AnimatedValue(
        start=start,
        end=end,
        animation=AnimationParams("直線移動", "0"),
    )


def star_object(
    *,
    text: str,
    layer: int,
    duration: int,
    x_start: float,
    x_end: float,
    y_start: float,
    y_end: float,
    size: float,
    color: str,
    opacity: float,
    glow: bool = False,
) -> TimelineObject:
    effects = [
        Effect(
            effect_id=0,
            name="テキスト",
            properties={
                "サイズ": StaticValue(size),
                "文字色": color,
                "テキスト": text,
            },
        ),
        Effect(
            effect_id=1,
            name="標準描画",
            properties={
                "X": moving_value(x_start, x_end),
                "Y": moving_value(y_start, y_end),
                "Z軸回転": (
                    moving_value(0.0, 360.0) if text == "★" else 0.0
                ),
                "透明度": StaticValue(opacity),
            },
        ),
    ]
    if glow:
        effects.append(
            Effect(
                effect_id=2,
                name="グロー",
                properties={
                    "強さ": StaticValue(42.0),
                    "拡散": 55,
                    "角度": StaticValue(25.0),
                    "しきい値": StaticValue(45.0),
                    "比率": StaticValue(100.0),
                    "ぼかし": 1,
                    "形状": "クロス(4本)",
                    "光色": "bfefff",
                    "光成分のみ": 0,
                    "サイズ固定": 0,
                },
            )
        )
    return TimelineObject(
        object_id=0,
        layer=layer,
        frame_start=0,
        frame_end=duration - 1,
        effects=effects,
    )


def build_animation(base_layer: int, duration: int) -> list[TimelineObject]:
    x_start, x_end = 1100.0, -1100.0
    y_start, y_end = -540.0, 540.0
    specs = [
        ("★", 0.0, 0.0, 110.0, "ffffff", 0.0, True),
        ("●", 115.0, -56.0, 48.0, "d9f8ff", 12.0, False),
        ("●", 220.0, -108.0, 36.0, "a8edff", 28.0, False),
        ("●", 320.0, -157.0, 27.0, "70dcff", 43.0, False),
        ("●", 415.0, -204.0, 19.0, "45c5ff", 58.0, False),
        ("●", 505.0, -248.0, 13.0, "2f94db", 72.0, False),
    ]
    return [
        star_object(
            text=text,
            layer=base_layer + index,
            duration=duration,
            x_start=x_start + x_offset,
            x_end=x_end + x_offset,
            y_start=y_start + y_offset,
            y_end=y_end + y_offset,
            size=size,
            color=color,
            opacity=opacity,
            glow=glow,
        )
        for index, (
            text,
            x_offset,
            y_offset,
            size,
            color,
            opacity,
            glow,
        ) in enumerate(specs)
    ]


def choose_pid(requested_pid: int | None) -> int:
    if requested_pid is not None:
        return requested_pid
    instances = discover_instances()
    if len(instances) != 1:
        raise RuntimeError(
            f"expected exactly one AviUtl2 instance, found {len(instances)}; "
            "specify --pid"
        )
    return instances[0].pid


def compact_object(obj: Any) -> dict[str, Any]:
    return {
        "object_id": obj.object_id,
        "layer": obj.layer,
        "frame_start": obj.frame_start,
        "frame_end": obj.frame_end,
        "name": obj.name,
        "api_locked": obj.api_locked,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int)
    parser.add_argument("--base-layer", type=int, default=0)
    parser.add_argument("--duration", type=int, default=90)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("render-tests") / "shooting-star",
    )
    args = parser.parse_args()
    if args.duration < 3:
        parser.error("--duration must be at least 3")

    pid = choose_pid(args.pid)
    objects = build_animation(args.base_layer, args.duration)
    commands = [
        CreateFromAliasCommand.from_object(
            obj,
            client_id=f"shooting-star-{index}",
        )
        for index, obj in enumerate(objects)
    ]

    with LiveClient.connect(pid=pid) as client:
        before = client.get_snapshot()
        occupied = [
            obj
            for obj in before.objects
            if args.base_layer <= obj.layer < args.base_layer + len(objects)
            and obj.frame_start <= args.duration - 1
            and obj.frame_end >= 0
        ]
        if occupied:
            raise RuntimeError(
                "target layers are occupied: "
                + ", ".join(str(obj.object_id) for obj in occupied)
            )

        validation = client.validate_batch(commands)
        applied = client.apply_batch(commands)
        after = client.get_snapshot()
        args.output_dir.mkdir(parents=True, exist_ok=True)
        render_frames = sorted({0, args.duration // 2, args.duration - 1})
        renders = []
        for frame in render_frames:
            output_path = (
                args.output_dir / f"frame-{frame:03d}.png"
            ).resolve()
            rendered = client.render_frame(frame, output_path=output_path)
            renders.append((rendered, output_path))

    print(
        json.dumps(
            {
                "pid": pid,
                "before_revision": before.revision,
                "validation": validation,
                "applied": applied,
                "after_revision": after.revision,
                "objects": [compact_object(obj) for obj in after.objects],
                "renders": [
                    {
                        "frame": render.frame,
                        "path": str(output_path),
                        "width": render.width,
                        "height": render.height,
                        "byte_length": len(render.png),
                        "sha256": render.sha256,
                        "revision": render.revision,
                    }
                    for render, output_path in renders
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
