"""Inspect native objects created for one media file, then remove them."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from aviutl2_api.editing import EditPlan
from aviutl2_api.live import LiveObject, LiveProject


def run_probe(media: Path, *, pid: int | None) -> dict[str, Any]:
    source = media.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"media does not exist: {source}")

    with LiveProject.connect(pid=pid) as project:
        group = project.add_media(source, at="end")
        objects: list[dict[str, Any]] = []
        live_objects: list[LiveObject] = []
        for obj in group:
            if not isinstance(obj, LiveObject):
                raise TypeError("LiveProject returned a non-live object reference")
            live_objects.append(obj)
            detail = project.client.inspect_object(obj.snapshot_object)
            objects.append(
                {
                    "object_id": obj.object_id,
                    "layer": obj.layer,
                    "frame_start": obj.frame_start,
                    "frame_end": obj.frame_end,
                    "effects": [
                        {
                            "name": inspected.name,
                            "selector": inspected.selector,
                            "items": [
                                {
                                    "name": item.name,
                                    "type": item.type,
                                    "value": item.raw_value,
                                }
                                for item in inspected.items
                            ],
                        }
                        for inspected in detail.effects
                    ],
                }
            )

        cleanup = EditPlan()
        for index, obj in enumerate(live_objects):
            cleanup.delete(obj, key=f"cleanup-{index}")
        receipt = project.apply(cleanup)
        remaining = project.refresh()
        return {
            "media": str(source),
            "objects": objects,
            "cleanup": {
                "applied_count": receipt.applied_count,
                "revision": receipt.revision,
                "remaining_object_count": len(remaining),
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("media", type=Path)
    parser.add_argument("--pid", type=int)
    args = parser.parse_args()
    print(
        json.dumps(
            run_probe(args.media, pid=args.pid),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
