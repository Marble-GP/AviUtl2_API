"""Live Bridge playback-rate and media-split host test."""

from __future__ import annotations

import argparse
import json
import math
import struct
import tempfile
import wave
from pathlib import Path
from typing import Any

from aviutl2_api.live import LiveClient, SnapshotObject


def emit(event: str, **values: Any) -> None:
    print(
        json.dumps(
            {"event": event, **values},
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )


def create_test_wave(path: Path, *, seconds: int = 3) -> None:
    sample_rate = 48_000
    frequency = 440.0
    amplitude = 6_000
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        frames = bytearray()
        for index in range(sample_rate * seconds):
            value = int(
                amplitude * math.sin(2.0 * math.pi * frequency * index / sample_rate)
            )
            frames.extend(struct.pack("<h", value))
        output.writeframes(frames)


def objects_in_range(
    client: LiveClient,
    *,
    layer: int,
    start: int,
    end: int,
) -> list[SnapshotObject]:
    return [
        obj
        for obj in client.get_snapshot().objects
        if obj.layer == layer and obj.frame_start <= end and obj.frame_end >= start
    ]


def inspect_media_values(
    client: LiveClient,
    obj: SnapshotObject,
) -> dict[str, str | None]:
    inspection = client.inspect_object(obj)
    media = [
        effect
        for effect in inspection.effects
        if effect.name in {"動画ファイル", "音声ファイル"}
    ]
    if len(media) != 1:
        raise RuntimeError("expected exactly one media input effect")
    return {item.name: item.raw_value for item in media[0].items}


def cleanup_range(
    client: LiveClient,
    *,
    layer: int,
    start: int,
    end: int,
) -> None:
    while True:
        matches = [
            obj
            for obj in objects_in_range(
                client,
                layer=layer,
                start=start,
                end=end,
            )
            if not obj.api_locked
        ]
        if not matches:
            return
        target = matches[-1]
        client.delete_object(target)
        emit(
            "cleanup_deleted",
            frames=[target.frame_start, target.frame_end],
            object_id=target.object_id,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--layer", type=int, default=2)
    parser.add_argument("--frame", type=int, default=0)
    parser.add_argument("--length", type=int, default=90)
    parser.add_argument("--split-frame", type=int, default=30)
    args = parser.parse_args()
    end = args.frame + args.length - 1
    if not args.frame < args.split_frame <= end:
        raise ValueError("split frame must be inside the requested range")

    path = Path(tempfile.gettempdir()) / (f"aviutl2-live-media-smoke-{args.pid}.wav")
    if path.exists() and path.stat().st_size == 288_044:
        emit("wave_reused", bytes=path.stat().st_size, path=str(path))
    else:
        create_test_wave(path)
        emit("wave_created", bytes=path.stat().st_size, path=str(path))
    try:
        with LiveClient.connect(pid=args.pid) as client:
            if objects_in_range(
                client,
                layer=args.layer,
                start=args.frame,
                end=end,
            ):
                raise RuntimeError("requested disposable media range is occupied")
            initial_count = len(client.get_snapshot().objects)
            try:
                probe = client.probe_media(path)
                emit(
                    "probed",
                    duration=probe.duration_seconds,
                    kind=probe.kind,
                    readable=probe.readable,
                )
                if not probe.readable or probe.kind != "audio":
                    raise RuntimeError("AviUtl2 did not accept the test WAV")

                created = client.add_audio(
                    path,
                    layer=args.layer,
                    frame=args.frame,
                    length=args.length,
                )
                if created.frame_start != args.frame or created.frame_end != end:
                    raise RuntimeError(f"unexpected media range: {created!r}")
                source = objects_in_range(
                    client,
                    layer=args.layer,
                    start=args.frame,
                    end=end,
                )[0]
                before_values = inspect_media_values(client, source)
                emit("media_created", values=before_values)

                rate_result = client.set_playback_rate(source, 2.0)
                source = objects_in_range(
                    client,
                    layer=args.layer,
                    start=args.frame,
                    end=end,
                )[0]
                if source.duration_frames != args.length:
                    raise RuntimeError("keep_timeline changed object duration")
                rate_values = inspect_media_values(client, source)
                emit(
                    "rate_changed",
                    result=rate_result,
                    values=rate_values,
                )

                split = client.split_media(source, args.split_frame)
                split_objects = sorted(
                    objects_in_range(
                        client,
                        layer=args.layer,
                        start=args.frame,
                        end=end,
                    ),
                    key=lambda item: item.frame_start,
                )
                if len(split_objects) != 2:
                    raise RuntimeError("split did not produce exactly two clips")
                left, right = split_objects
                right_values = inspect_media_values(client, right)
                emit(
                    "split",
                    left=[left.frame_start, left.frame_end],
                    result={
                        "playback_rate": split.playback_rate,
                        "source_left": split.source_position_left,
                        "source_right": split.source_position_right,
                    },
                    right=[right.frame_start, right.frame_end],
                    right_values=right_values,
                )

                rendered_left = client.render_frame(args.split_frame - 1)
                rendered_right = client.render_frame(args.split_frame)
                emit(
                    "rendered_boundary",
                    left_sha256=rendered_left.sha256,
                    right_sha256=rendered_right.sha256,
                    size=[rendered_left.width, rendered_left.height],
                )
            finally:
                cleanup_range(
                    client,
                    layer=args.layer,
                    start=args.frame,
                    end=end,
                )
            final_count = len(client.get_snapshot().objects)
            if final_count != initial_count:
                raise RuntimeError(
                    f"cleanup object count mismatch: {initial_count} -> {final_count}"
                )
            emit("complete", final_object_count=final_count)
    finally:
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            emit("wave_delete_deferred_host_lock", path=str(path))
        else:
            emit("wave_removed", path=str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
