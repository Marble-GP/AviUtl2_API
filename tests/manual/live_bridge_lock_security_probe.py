"""Adversarial, state-checked manual probes for the Live Bridge API lock."""

from __future__ import annotations

import argparse
import json
import struct
from collections.abc import Callable
from typing import Any

from aviutl2_api.live import (
    BridgeRemoteError,
    CreateFromAliasCommand,
    LiveClient,
    SnapshotObject,
    discover_instances,
)
from aviutl2_api.live._win32_pipe import Win32NamedPipeStream
from aviutl2_api.live.transport import connect_named_pipe


def expect_remote_error(
    label: str,
    operation: Callable[[], object],
    expected: str,
) -> None:
    try:
        operation()
    except BridgeRemoteError as error:
        if error.code != expected:
            raise AssertionError(
                f"{label}: expected {expected}, got {error.code}"
            ) from error
        print(f"PASS {label}: {error.code}")
        return
    raise AssertionError(f"{label}: operation unexpectedly succeeded")


def raw_exchange(pipe: str, payload: bytes) -> dict[str, Any]:
    with connect_named_pipe(pipe, timeout=5.0) as transport:
        response = transport.exchange(payload, timeout=5.0)
    document = json.loads(response.decode("utf-8"))
    if not isinstance(document, dict):
        raise AssertionError("raw response is not an object")
    return document


def require_raw_error(
    pipe: str,
    label: str,
    payload: bytes,
    expected: set[str],
) -> None:
    response = raw_exchange(pipe, payload)
    error = response.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if response.get("ok") is not False or code not in expected:
        raise AssertionError(
            f"{label}: expected one of {sorted(expected)}, got {response}"
        )
    print(f"PASS {label}: {code}")


def send_invalid_frame(pipe: str, frame: bytes) -> None:
    stream = Win32NamedPipeStream.connect(pipe, 5.0)
    try:
        written = stream.write(frame, 5.0)
        if written != len(frame):
            raise AssertionError(f"short invalid-frame write: {written}/{len(frame)}")
    finally:
        stream.close()


def require_pipe_recovery(pipe: str, pid: int, label: str) -> None:
    with LiveClient.connect(pid=pid, timeout=5.0) as client:
        if not client.ping():
            raise AssertionError(f"{label}: recovery ping failed")
    print(f"PASS {label}: pipe recovered")


def target_params(obj: SnapshotObject) -> dict[str, Any]:
    return {
        "expected_revision": obj.revision,
        "target": {"object_id": obj.object_id},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int)
    parser.add_argument(
        "--delete-probe",
        action="store_true",
        help="Send object.delete to a locked target; restore from Alias on failure.",
    )
    args = parser.parse_args()

    instances = discover_instances()
    if args.pid is not None:
        instances = [item for item in instances if item.pid == args.pid]
    if len(instances) != 1:
        raise RuntimeError(f"expected one selected instance, got {instances}")
    instance = instances[0]

    with LiveClient.connect(pid=instance.pid) as client:
        hello = client.hello()
        if hello.get("protocol_version") != 1:
            raise AssertionError(f"unexpected protocol: {hello}")
        before = client.get_snapshot()
        locked = [obj for obj in before.objects if obj.api_locked]
        if not locked:
            raise RuntimeError("at least one GUI-locked object is required")
        obj = locked[0]

        expect_remote_error(
            "object.set_item",
            lambda: client.call(
                "object.set_item",
                {
                    **target_params(obj),
                    "effect": "__LOCK_PROBE__",
                    "item": "__NONE__",
                    "value": "x",
                },
            ),
            "OBJECT_API_LOCKED",
        )
        expect_remote_error(
            "object.set_items",
            lambda: client.call(
                "object.set_items",
                {
                    **target_params(obj),
                    "items": [
                        {
                            "effect": "__LOCK_PROBE__",
                            "item": "__A__",
                            "value": "x",
                        },
                        {
                            "effect": "__LOCK_PROBE__",
                            "item": "__B__",
                            "value": "y",
                        },
                    ],
                },
            ),
            "OBJECT_API_LOCKED",
        )
        expect_remote_error(
            "object.move",
            lambda: client.move_object(
                obj,
                layer=obj.layer,
                frame=obj.frame_start,
            ),
            "OBJECT_API_LOCKED",
        )

        if args.delete_probe:
            try:
                client.delete_object(obj)
            except BridgeRemoteError as error:
                if error.code != "OBJECT_API_LOCKED":
                    raise
                print("PASS object.delete: OBJECT_API_LOCKED")
            else:
                client.create_from_alias(
                    obj.alias,
                    layer=obj.layer,
                    frame=obj.frame_start,
                    length=obj.duration_frames,
                    client_id="security-probe-emergency-restore",
                )
                raise AssertionError(
                    "object.delete bypassed the lock; Alias was restored"
                )

        forged = target_params(obj)
        forged.update(
            {
                "unlock": True,
                "api_locked": False,
                "name": "",
            }
        )
        expect_remote_error(
            "ignored unlock/api_locked fields",
            lambda: client.call("object.delete", forged),
            "OBJECT_API_LOCKED",
        )

        leading_zero_id = f"obj-{obj.revision}-0{obj.object_id.rsplit('-', 1)[1]}"
        expect_remote_error(
            "noncanonical leading-zero index",
            lambda: client.call(
                "object.delete",
                {
                    "expected_revision": obj.revision,
                    "target": {"object_id": leading_zero_id},
                },
            ),
            "OBJECT_API_LOCKED",
        )
        expect_remote_error(
            "out-of-range object index",
            lambda: client.call(
                "object.delete",
                {
                    "expected_revision": obj.revision,
                    "target": {"object_id": f"obj-{obj.revision}-999999999"},
                },
            ),
            "OBJECT_NOT_FOUND",
        )
        expect_remote_error(
            "stale matching id",
            lambda: client.call(
                "object.delete",
                {
                    "expected_revision": obj.revision - 1,
                    "target": {"object_id": f"obj-{obj.revision - 1}-0"},
                },
            ),
            "STALE_PROJECT_STATE",
        )
        expect_remote_error(
            "revision/id mismatch",
            lambda: client.call(
                "object.delete",
                {
                    "expected_revision": obj.revision - 1,
                    "target": {"object_id": obj.object_id},
                },
            ),
            "INVALID_ARGUMENT",
        )
        expect_remote_error(
            "boolean revision",
            lambda: client.call(
                "object.delete",
                {
                    "expected_revision": True,
                    "target": {"object_id": "obj-1-0"},
                },
            ),
            "INVALID_ARGUMENT",
        )

        collision = CreateFromAliasCommand(
            alias=obj.alias,
            layer=obj.layer,
            frame=obj.frame_start,
            length=obj.duration_frames,
            client_id="security-probe-collision",
        )
        expect_remote_error(
            "batch.validate over locked placement",
            lambda: client.validate_batch([collision]),
            "PLACEMENT_COLLISION",
        )
        expect_remote_error(
            "batch.apply over locked placement",
            lambda: client.apply_batch([collision]),
            "PLACEMENT_COLLISION",
        )
        expect_remote_error(
            "direct create over locked placement",
            lambda: client.create_from_alias(
                obj.alias,
                layer=obj.layer,
                frame=obj.frame_start,
                length=obj.duration_frames,
                client_id="security-probe-direct-collision",
            ),
            "PLACEMENT_COLLISION",
        )

        free_layer = max(item.layer for item in before.objects) + 1
        overlay_candidate = CreateFromAliasCommand(
            alias=obj.alias,
            layer=free_layer,
            frame=obj.frame_start,
            length=obj.duration_frames,
            client_id="security-probe-other-layer",
        )
        overlay_validation = client.validate_batch([overlay_candidate])
        if overlay_validation.get("valid") is not True:
            raise AssertionError("expected another-layer creation to remain in scope")
        print(
            "EXPECTED LIMITATION other-layer creation validates; "
            "the object lock does not protect the composed image"
        )
        if not obj.alias:
            raise AssertionError("locked snapshot unexpectedly hid Alias")
        print(
            "EXPECTED LIMITATION locked Alias remains readable; "
            "the object lock is not a confidentiality control"
        )

    duplicate_revision = (
        b'{"id":"raw-duplicate","protocol_version":1,'
        b'"method":"object.delete","params":{'
        + f'"expected_revision":{obj.revision},'.encode()
        + f'"expected_revision":{obj.revision},'.encode()
        + f'"target":{{"object_id":"{obj.object_id}"}}}}}}'.encode()
    )
    require_raw_error(
        instance.pipe,
        "duplicate JSON key",
        duplicate_revision,
        {"INVALID_REQUEST"},
    )
    require_raw_error(
        instance.pipe,
        "invalid UTF-8",
        b'{"id":"raw-utf8","protocol_version":1,"method":"'
        + b"\xff"
        + b'","params":{}}',
        {"INVALID_REQUEST"},
    )
    confusable_method = json.dumps(
        {
            "id": "raw-confusable",
            "protocol_version": 1,
            "method": "object.delete\u200b",
            "params": target_params(obj),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    require_raw_error(
        instance.pipe,
        "confusable method name",
        confusable_method,
        {"METHOD_NOT_FOUND"},
    )
    excessive_nesting = (
        b'{"id":"raw-depth","protocol_version":1,'
        b'"method":"system.ping","params":{"x":'
        + (b"[" * 300)
        + b"0"
        + (b"]" * 300)
        + b"}}"
    )
    require_raw_error(
        instance.pipe,
        "excessive JSON nesting",
        excessive_nesting,
        {"INVALID_REQUEST"},
    )

    send_invalid_frame(instance.pipe, struct.pack("<I", 0))
    require_pipe_recovery(instance.pipe, instance.pid, "zero-length frame")
    send_invalid_frame(instance.pipe, struct.pack("<I", 1024 * 1024 + 1))
    require_pipe_recovery(instance.pipe, instance.pid, "oversized frame")
    send_invalid_frame(instance.pipe, struct.pack("<I", 32) + b"{}")
    require_pipe_recovery(instance.pipe, instance.pid, "incomplete frame")

    with LiveClient.connect(pid=instance.pid) as client:
        after = client.get_snapshot()
        if after.revision != before.revision:
            raise AssertionError(
                f"project changed during probes: {before.revision} -> {after.revision}"
            )
        if [
            (item.object_id, item.name, item.api_locked, item.alias)
            for item in after.objects
        ] != [
            (item.object_id, item.name, item.api_locked, item.alias)
            for item in before.objects
        ]:
            raise AssertionError("snapshot contents changed during probes")
        if not client.ping():
            raise AssertionError("pipe did not recover after malformed probes")

    print("PASS invariant: revision and all snapshot contents are unchanged")
    print("PASS recovery: valid ping succeeds after malformed payloads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
