"""Destructive-but-cleaned-up AviUtl2 Live Bridge host smoke test.

Run only against a disposable empty range in an explicitly selected process.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from aviutl2_api.live import BridgeRemoteError, LiveClient, SnapshotObject


def emit(event: str, **values: Any) -> None:
    print(
        json.dumps(
            {"event": event, **values},
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )


def object_at(
    client: LiveClient,
    *,
    layer: int,
    frame_start: int,
    frame_end: int,
) -> SnapshotObject:
    matches = [
        obj
        for obj in client.get_snapshot().objects
        if obj.layer == layer
        and obj.frame_start == frame_start
        and obj.frame_end == frame_end
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one object at layer {layer}, "
            f"frames {frame_start}-{frame_end}; found {len(matches)}"
        )
    return matches[0]


def delete_at_if_present(
    client: LiveClient,
    *,
    layer: int,
    frame_start: int,
    frame_end: int,
) -> None:
    matches = [
        obj
        for obj in client.get_snapshot().objects
        if obj.layer == layer
        and obj.frame_start == frame_start
        and obj.frame_end == frame_end
        and not obj.api_locked
    ]
    for obj in reversed(matches):
        client.delete_object(obj)
        emit("cleanup_deleted", layer=layer, object_id=obj.object_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--layer", type=int, default=2)
    parser.add_argument("--duplicate-layer", type=int, default=3)
    parser.add_argument("--frame", type=int, default=0)
    parser.add_argument("--length", type=int, default=30)
    parser.add_argument("--effect", default="ぼかし")
    args = parser.parse_args()
    frame_end = args.frame + args.length - 1

    with LiveClient.connect(pid=args.pid) as client:
        hello = client.hello()
        if hello.get("protocol_version") != 1:
            raise RuntimeError(f"expected protocol v1, got {hello!r}")
        emit("connected", hello=hello)

        snapshot = client.get_snapshot()
        locked = [obj for obj in snapshot.objects if obj.api_locked]
        if locked:
            try:
                client.delete_object(locked[0])
            except BridgeRemoteError as error:
                if error.code != "OBJECT_API_LOCKED":
                    raise
                emit("locked_object_rejected", code=error.code)
            else:
                raise RuntimeError("API-locked object unexpectedly allowed deletion")

        occupied = [
            obj
            for obj in snapshot.objects
            if obj.layer in {args.layer, args.duplicate_layer}
            and obj.frame_start <= frame_end
            and obj.frame_end >= args.frame
        ]
        if occupied:
            raise RuntimeError("requested disposable test range is occupied")

        catalog_names: set[str] = set()
        next_start: int | None = 0
        pages = 0
        while next_start is not None:
            page = client.get_effect_catalog(start=next_start, count=128)
            catalog_names.update(effect.name for effect in page.effects)
            next_start = page.next_start
            pages += 1
        if args.effect not in catalog_names:
            raise RuntimeError(f"effect not found in host catalog: {args.effect}")
        emit(
            "catalog_complete",
            effects=len(catalog_names),
            pages=pages,
            test_effect=args.effect,
        )

        try:
            client.add_text(
                "Live Bridge host smoke",
                layer=args.layer,
                frame=args.frame,
                length=args.length,
                size=48,
            )
            source = object_at(
                client,
                layer=args.layer,
                frame_start=args.frame,
                frame_end=frame_end,
            )
            inspection = client.inspect_object(source)
            emit(
                "created_and_inspected",
                effects=[effect.name for effect in inspection.effects],
                object_id=source.object_id,
            )

            client.add_effect(source, args.effect)
            source = object_at(
                client,
                layer=args.layer,
                frame_start=args.frame,
                frame_end=frame_end,
            )
            inspection = client.inspect_object(source)
            added = [
                effect for effect in inspection.effects if effect.name == args.effect
            ]
            if len(added) != 1:
                raise RuntimeError("added effect was not observed exactly once")
            emit(
                "effect_added",
                selector=added[0].selector,
                item_count=len(added[0].items),
            )

            client.delete_effect(source, added[0].selector)
            source = object_at(
                client,
                layer=args.layer,
                frame_start=args.frame,
                frame_end=frame_end,
            )
            inspection = client.inspect_object(source)
            if any(effect.name == args.effect for effect in inspection.effects):
                raise RuntimeError("deleted effect is still present")
            emit("effect_deleted")

            stale_source = source
            duplicate = client.duplicate_object(
                source,
                layer=args.duplicate_layer,
                frame=args.frame,
            )
            emit(
                "duplicated",
                duration=duplicate.duration_frames,
                object_id=duplicate.object_id,
            )

            try:
                client.set_text(stale_source, "must not be applied")
            except BridgeRemoteError as error:
                if error.code != "STALE_PROJECT_STATE":
                    raise
                emit("stale_rejected", code=error.code)
            else:
                raise RuntimeError("stale object unexpectedly allowed mutation")
        finally:
            delete_at_if_present(
                client,
                layer=args.duplicate_layer,
                frame_start=args.frame,
                frame_end=frame_end,
            )
            delete_at_if_present(
                client,
                layer=args.layer,
                frame_start=args.frame,
                frame_end=frame_end,
            )

        final_snapshot = client.get_snapshot()
        emit(
            "complete",
            final_object_count=len(final_snapshot.objects),
            final_revision=final_snapshot.revision,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
