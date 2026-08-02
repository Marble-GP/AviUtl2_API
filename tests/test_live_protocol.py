"""Protocol and transport tests shared with native fixtures."""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from pathlib import Path

import pytest

from aviutl2_api.live import (
    AmbiguousInstanceError,
    CreateFromAliasCommand,
    InstanceInfo,
    ProjectSnapshot,
    SnapshotObject,
    make_text_object,
    serialize_object_alias,
)
from aviutl2_api.live import client as client_module
from aviutl2_api.live.client import LiveClient
from aviutl2_api.live.protocol import (
    MAX_PAYLOAD_BYTES,
    BridgeRemoteError,
    ProtocolError,
    decode_response,
    encode_frame,
    encode_request,
)
from aviutl2_api.live.transport import FramedTransport
from aviutl2_api.models import AnimatedValue, AnimationParams

FIXTURES = Path(__file__).parents[1] / "protocol" / "fixtures"


class ScriptedStream:
    def __init__(
        self,
        incoming: bytes,
        *,
        read_chunk: int = 3,
        write_chunk: int = 5,
    ) -> None:
        self.incoming = bytearray(incoming)
        self.written = bytearray()
        self.read_chunk = read_chunk
        self.write_chunk = write_chunk
        self.closed = False
        self.read_timeouts: list[float] = []
        self.write_timeouts: list[float] = []

    def read(self, size: int, timeout: float) -> bytes:
        assert timeout > 0
        self.read_timeouts.append(timeout)
        count = min(size, self.read_chunk, len(self.incoming))
        data = bytes(self.incoming[:count])
        del self.incoming[:count]
        return data

    def write(self, data: bytes, timeout: float) -> int:
        assert timeout > 0
        self.write_timeouts.append(timeout)
        count = min(len(data), self.write_chunk)
        self.written.extend(data[:count])
        return count

    def close(self) -> None:
        self.closed = True


def test_shared_ping_fixtures() -> None:
    request = (FIXTURES / "system_ping.request.json").read_bytes()
    response = (FIXTURES / "system_ping.response.json").read_bytes()

    decoded = decode_response(response)
    assert decoded.request_id == "fixture-ping-1"
    assert decoded.result == {"pong": True}
    request_document = json.loads(request)
    assert request_document["method"] == "system.ping"
    assert request_document["future_field"] == "ignored"


def test_shared_batch_validation_fixtures() -> None:
    request = json.loads(
        (FIXTURES / "batch_validate.request.json").read_bytes()
    )
    response = decode_response(
        (FIXTURES / "batch_validate.response.json").read_bytes()
    )

    assert request["method"] == "batch.validate"
    assert request["params"]["commands"][0]["client_id"] == "title"
    assert response.result["valid"] is True
    assert response.result["alias_semantics"] == "verified_on_apply"


def test_framed_transport_handles_partial_io() -> None:
    response = b'{"id":"py-00000001","ok":true,"result":{"pong":true}}'
    stream = ScriptedStream(encode_frame(response))
    transport = FramedTransport(stream)

    request = encode_request("py-00000001", "system.ping")
    assert transport.exchange(request, timeout=1.0) == response
    assert bytes(stream.written) == encode_frame(request)

    transport.close()
    assert stream.closed


def test_live_client_validates_matching_id() -> None:
    response = b'{"id":"different","ok":true,"result":{"pong":true}}'
    client = LiveClient(FramedTransport(ScriptedStream(encode_frame(response))))

    with pytest.raises(ConnectionError, match="does not match"):
        client.ping()


def test_live_client_refuses_ambiguous_process_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def instance(pid: int) -> InstanceInfo:
        return InstanceInfo(
            pid=pid,
            pipe=rf"\\.\pipe\AviUtl2.LiveBridge.{pid}",
            protocol_version=1,
            plugin_version="0.4.1",
            sdk_baseline="mirror-2026-07-25",
            project_path=None,
            scene_id=0,
            started_at=f"2026-07-26T00:00:00.{pid:03d}Z",
        )

    monkeypatch.setattr(
        client_module,
        "discover_instances",
        lambda: [instance(101), instance(202)],
    )

    with pytest.raises(AmbiguousInstanceError, match="101, 202"):
        LiveClient.connect()


def test_remote_error_is_structured() -> None:
    response = (
        b'{"id":"x","ok":false,"error":{"code":"HOST_BUSY",'
        b'"message":"busy","details":{"state":"save"},"retryable":true}}'
    )
    with pytest.raises(BridgeRemoteError) as raised:
        decode_response(response)
    assert raised.value.code == "HOST_BUSY"
    assert raised.value.retryable is True
    assert raised.value.details == {"state": "save"}


def test_frame_rejects_empty_payload() -> None:
    with pytest.raises(ProtocolError):
        encode_frame(b"")


def test_frame_rejects_oversized_payload() -> None:
    with pytest.raises(ProtocolError):
        encode_frame(b"x" * (MAX_PAYLOAD_BYTES + 1))


def test_frame_is_little_endian() -> None:
    frame = encode_frame(b"abc")
    assert frame[:4] == struct.pack("<I", 3)
    assert frame[4:] == b"abc"


def test_invalid_response_json_is_rejected() -> None:
    with pytest.raises(ProtocolError, match="UTF-8 JSON"):
        decode_response(b"\xff")


def test_timeline_object_serializes_to_native_alias() -> None:
    obj = make_text_object(
        "1行目\n2行目",
        layer=2,
        frame=10,
        length=30,
        x=12.5,
        size=48,
        color="#12ABef",
    )

    alias = serialize_object_alias(obj)

    assert alias.startswith("[Object]\r\n[Object.0]\r\n")
    assert "effect.name=テキスト\r\n" in alias
    assert "文字色=12abef\r\n" in alias
    assert "テキスト=1行目\\n2行目\r\n" in alias
    assert "[Object.1]\r\neffect.name=標準描画\r\n" in alias
    assert "layer=" not in alias
    assert "frame=" not in alias


def test_create_command_uses_inclusive_model_duration() -> None:
    obj = make_text_object(
        "Phase 2",
        layer=3,
        frame=20,
        length=45,
    )

    command = CreateFromAliasCommand.from_object(obj, client_id="title")

    assert command.layer == 3
    assert command.frame == 20
    assert command.length == 45
    assert command.to_wire()["op"] == "object.create_from_alias"
    assert command.to_wire()["client_id"] == "title"


def test_live_client_sends_typed_batch_command() -> None:
    response = (
        b'{"id":"py-00000001","ok":true,"result":'
        b'{"valid":true,"command_count":1}}'
    )
    stream = ScriptedStream(encode_frame(response), write_chunk=4096)
    client = LiveClient(FramedTransport(stream))
    command = CreateFromAliasCommand(
        alias="[Object]\r\n[Object.0]\r\neffect.name=テキスト\r\n",
        layer=1,
        frame=5,
        length=10,
        client_id="caption",
    )

    result = client.validate_batch([command])

    assert result["valid"] is True
    payload_size = struct.unpack("<I", stream.written[:4])[0]
    request = json.loads(stream.written[4 : 4 + payload_size])
    assert request["method"] == "batch.validate"
    assert request["params"]["commands"] == [command.to_wire()]


def test_live_client_sends_unified_edit_plan_and_operation_id() -> None:
    command = {
        "op": "object.delete",
        "key": "remove-old-title",
        "target": {"object_id": "obj-123-4"},
    }
    response = (
        b'{"id":"py-00000001","ok":true,"result":'
        b'{"applied_count":1,"undo_grouped":true,"atomic":false}}'
    )
    stream = ScriptedStream(encode_frame(response), write_chunk=4096)
    client = LiveClient(FramedTransport(stream))

    result = client.apply_edit_plan(
        expected_revision=123,
        commands=[command],
        operation_id="agent-edit-42",
    )

    assert result["applied_count"] == 1
    payload_size = struct.unpack("<I", stream.written[:4])[0]
    request = json.loads(stream.written[4 : 4 + payload_size])
    assert request["method"] == "edit.plan.apply"
    assert request["params"] == {
        "expected_revision": 123,
        "commands": [command],
        "operation_id": "agent-edit-42",
    }


def test_add_text_uses_apply_batch() -> None:
    response = (
        b'{"id":"py-00000001","ok":true,"result":'
        b'{"applied_count":1,"undo_grouped":true,"created":[]}}'
    )
    stream = ScriptedStream(encode_frame(response), write_chunk=4096)
    client = LiveClient(FramedTransport(stream))

    result = client.add_text(
        "SDKから追加",
        layer=0,
        frame=0,
        length=60,
        client_id="live-text",
    )

    assert result["undo_grouped"] is True
    payload_size = struct.unpack("<I", stream.written[:4])[0]
    request = json.loads(stream.written[4 : 4 + payload_size])
    assert request["method"] == "batch.apply"
    command = request["params"]["commands"][0]
    assert command["client_id"] == "live-text"
    assert "effect.name=テキスト" in command["alias"]


def test_snapshot_returns_revision_scoped_objects() -> None:
    response = (
        b'{"id":"py-00000001","ok":true,"result":{'
        b'"revision":123,"scene_id":0,"object_count":1,"objects":[{'
        b'"object_id":"obj-123-0","layer":2,"frame_start":10,'
        b'"frame_end":39,"name":"Title",'
        b'"alias":"[Object]\\r\\n[Object.0]\\r\\neffect.name=Text\\r\\n"'
        b"}]}}"
    )
    stream = ScriptedStream(encode_frame(response), write_chunk=4096)
    client = LiveClient(FramedTransport(stream))

    snapshot = client.get_snapshot()

    assert snapshot.revision == 123
    assert snapshot.objects[0].object_id == "obj-123-0"
    assert snapshot.objects[0].duration_frames == 30
    assert snapshot.objects[0].target_params() == {
        "expected_revision": 123,
        "target": {"object_id": "obj-123-0"},
    }
    assert snapshot.objects[0].api_locked is False


def test_media_inventory_uses_long_default_timeout() -> None:
    result = {
        "revision": 123,
        "scene_id": 0,
        "file_item_count": 0,
        "unique_file_count": 0,
        "missing_count": 0,
        "unreadable_count": 0,
        "files": [],
    }
    response = json.dumps(
        {"id": "py-00000001", "ok": True, "result": result}
    ).encode()
    stream = ScriptedStream(encode_frame(response), write_chunk=4096)
    client = LiveClient(FramedTransport(stream), default_timeout=5.0)

    inventory = client.get_media_inventory()

    assert inventory.revision == 123
    assert stream.read_timeouts
    assert stream.write_timeouts
    assert min(stream.read_timeouts + stream.write_timeouts) > 100.0


def test_effect_catalog_returns_typed_page() -> None:
    result = {
        "start": 0,
        "count": 1,
        "total": 2,
        "next_start": 1,
        "effects": [
            {
                "name": "動画ファイル",
                "type": "input",
                "type_code": 2,
                "flags": {
                    "video": True,
                    "audio": True,
                    "filter_object": False,
                    "camera": False,
                },
                "items": [
                    {"name": "再生速度", "type": "number", "type_code": 2}
                ],
            }
        ],
    }
    response = json.dumps(
        {"id": "py-00000001", "ok": True, "result": result},
        ensure_ascii=False,
    ).encode()
    stream = ScriptedStream(encode_frame(response), write_chunk=4096)
    client = LiveClient(FramedTransport(stream))

    page = client.get_effect_catalog(count=1)

    assert page.total == 2
    assert page.next_start == 1
    assert page.effects[0].type == "input"
    assert page.effects[0].flags.video is True
    assert page.effects[0].items[0].name == "再生速度"
    payload_size = struct.unpack("<I", stream.written[:4])[0]
    request = json.loads(stream.written[4 : 4 + payload_size])
    assert request["method"] == "effect.catalog"
    assert request["params"] == {"start": 0, "count": 1}


def test_layers_returns_typed_revision_page() -> None:
    result = {
        "revision": 123,
        "scene_id": 7,
        "layer_max": 8,
        "display": {"start": 0, "count": 10},
        "start": 0,
        "count": 2,
        "layers": [
            {
                "layer": 0,
                "name": "Video",
                "enabled": True,
                "locked": False,
                "object_count": 1,
                "visible": True,
            },
            {
                "layer": 1,
                "name": None,
                "enabled": False,
                "locked": True,
                "object_count": 0,
                "visible": True,
            },
        ],
    }
    response = json.dumps(
        {"id": "py-00000001", "ok": True, "result": result},
        ensure_ascii=False,
    ).encode()
    stream = ScriptedStream(encode_frame(response), write_chunk=4096)
    client = LiveClient(FramedTransport(stream))

    page = client.get_layers(count=2)

    assert page.revision == 123
    assert page.layers[0].name == "Video"
    assert page.layers[1].locked is True
    assert page.layers[1].enabled is False
    payload_size = struct.unpack("<I", stream.written[:4])[0]
    request = json.loads(stream.written[4 : 4 + payload_size])
    assert request["method"] == "project.get_layers"


def test_snapshot_exposes_api_lock_state() -> None:
    result = {
        "revision": 321,
        "scene_id": 0,
        "object_count": 1,
        "objects": [
            {
                "object_id": "obj-321-0",
                "layer": 1,
                "frame_start": 0,
                "frame_end": 29,
                "name": "\N{LOCK} Title",
                "alias": "[Object]\r\n",
                "api_locked": True,
            }
        ],
    }

    obj = ProjectSnapshot.from_wire(result).objects[0]

    assert obj.api_locked is True
    assert obj.name == "\N{LOCK} Title"


def test_set_position_groups_x_and_y() -> None:
    snapshot_response = {
        "revision": 123,
        "scene_id": 0,
        "object_count": 1,
        "objects": [
            {
                "object_id": "obj-123-0",
                "layer": 0,
                "frame_start": 0,
                "frame_end": 89,
                "name": None,
                "alias": "[Object]\r\n",
                "api_locked": False,
            }
        ],
    }
    obj = ProjectSnapshot.from_wire(snapshot_response).objects[0]
    response = (
        b'{"id":"py-00000001","ok":true,"result":'
        b'{"applied_count":2,"revision":124,'
        b'"snapshot_required":false,"undo_grouped":true}}'
    )
    stream = ScriptedStream(encode_frame(response), write_chunk=4096)
    client = LiveClient(FramedTransport(stream))

    result = client.set_position(obj, x=120.0, y=-45.5)

    assert result["revision"] == 124
    payload_size = struct.unpack("<I", stream.written[:4])[0]
    request = json.loads(stream.written[4 : 4 + payload_size])
    assert request["method"] == "object.set_items"
    assert request["params"]["expected_revision"] == 123
    assert request["params"]["items"] == [
        {"effect": "標準描画", "item": "X", "value": "120.000000"},
        {"effect": "標準描画", "item": "Y", "value": "-45.500000"},
    ]


@pytest.mark.parametrize(
    ("method_name", "wire_method", "argument", "field"),
    [
        ("add_effect", "object.effect.add", "ぼかし", "effect"),
        (
            "delete_effect",
            "object.effect.delete",
            "ぼかし:1",
            "selector",
        ),
    ],
)
def test_effect_mutation_uses_revision_scoped_selector(
    method_name: str,
    wire_method: str,
    argument: str,
    field: str,
) -> None:
    obj = SnapshotObject(
        object_id="obj-50-3",
        revision=50,
        layer=0,
        frame_start=0,
        frame_end=29,
        name=None,
        alias="[Object]\r\n",
    )
    response = (
        b'{"id":"py-00000001","ok":true,"result":'
        b'{"applied_count":1,"revision":51,'
        b'"snapshot_required":false,"undo_grouped":true}}'
    )
    stream = ScriptedStream(encode_frame(response), write_chunk=4096)
    client = LiveClient(FramedTransport(stream))

    getattr(client, method_name)(obj, argument)

    payload_size = struct.unpack("<I", stream.written[:4])[0]
    request = json.loads(stream.written[4 : 4 + payload_size])
    assert request["method"] == wire_method
    assert request["params"]["expected_revision"] == 50
    assert request["params"][field] == argument


def test_duplicate_object_verifies_fresh_snapshot() -> None:
    alias = "[Object]\r\n[Object.0]\r\neffect.name=Text\r\n"
    obj = SnapshotObject(
        object_id="obj-50-3",
        revision=50,
        layer=0,
        frame_start=0,
        frame_end=29,
        name=None,
        alias=alias,
    )
    create_response = (
        b'{"id":"py-00000001","ok":true,"result":'
        b'{"applied_count":1,"atomic":false,"created":['
        b'{"command_index":0}],"undo_grouped":true}}'
    )
    snapshot_result = {
        "revision": 51,
        "scene_id": 0,
        "object_count": 2,
        "objects": [
            {
                "object_id": "obj-51-0",
                "layer": 0,
                "frame_start": 0,
                "frame_end": 29,
                "name": None,
                "alias": alias,
            },
            {
                "object_id": "obj-51-1",
                "layer": 2,
                "frame_start": 40,
                "frame_end": 69,
                "name": None,
                "alias": alias,
            },
        ],
    }
    snapshot_response = json.dumps(
        {
            "id": "py-00000002",
            "ok": True,
            "result": snapshot_result,
        }
    ).encode()
    stream = ScriptedStream(
        encode_frame(create_response) + encode_frame(snapshot_response),
        write_chunk=4096,
    )
    client = LiveClient(FramedTransport(stream))

    duplicate = client.duplicate_object(obj, layer=2, frame=40)

    assert duplicate.object_id == "obj-51-1"
    assert duplicate.duration_frames == 30
    first_size = struct.unpack("<I", stream.written[:4])[0]
    second_offset = 4 + first_size
    second_size = struct.unpack(
        "<I", stream.written[second_offset : second_offset + 4]
    )[0]
    second_request = json.loads(
        stream.written[
            second_offset + 4 : second_offset + 4 + second_size
        ]
    )
    assert second_request["method"] == "project.get_snapshot"


def test_duplicate_object_honors_api_lock_without_transport() -> None:
    obj = SnapshotObject(
        object_id="obj-50-3",
        revision=50,
        layer=0,
        frame_start=0,
        frame_end=29,
        name="\N{LOCK} Secret",
        alias="[Object]\r\n",
        api_locked=True,
    )
    stream = ScriptedStream(b"")
    client = LiveClient(FramedTransport(stream))

    with pytest.raises(PermissionError, match="API-locked"):
        client.duplicate_object(obj, layer=2, frame=40)

    assert stream.written == b""


def test_split_media_returns_typed_contiguous_ranges() -> None:
    obj = SnapshotObject(
        object_id="obj-50-3",
        revision=50,
        layer=2,
        frame_start=10,
        frame_end=39,
        name=None,
        alias="[Object]\r\n",
    )
    result = {
        "left": {"layer": 2, "frame_start": 10, "frame_end": 24},
        "right": {"layer": 2, "frame_start": 25, "frame_end": 39},
        "source_position": {"left": 3.0, "right": 33.0},
        "playback_rate": 2.0,
        "revision": 51,
        "snapshot_required": False,
        "undo_grouped": True,
    }
    response = json.dumps(
        {"id": "py-00000001", "ok": True, "result": result}
    ).encode()
    stream = ScriptedStream(encode_frame(response), write_chunk=4096)
    client = LiveClient(FramedTransport(stream))

    split = client.split_media(obj, 25)

    assert split.left.frame_end == 24
    assert split.right.frame_start == 25
    assert split.source_position_right == 33.0
    assert split.playback_rate == 2.0
    payload_size = struct.unpack("<I", stream.written[:4])[0]
    request = json.loads(stream.written[4 : 4 + payload_size])
    assert request["method"] == "object.split_media"
    assert request["params"]["frame"] == 25
    assert request["params"]["expected_revision"] == 50


def test_split_media_rejects_boundary_without_transport() -> None:
    obj = SnapshotObject(
        object_id="obj-50-3",
        revision=50,
        layer=2,
        frame_start=10,
        frame_end=39,
        name=None,
        alias="[Object]\r\n",
    )
    stream = ScriptedStream(b"")
    client = LiveClient(FramedTransport(stream))

    with pytest.raises(ValueError, match="strictly inside"):
        client.split_media(obj, 10)

    assert stream.written == b""


def test_set_text_escapes_line_breaks() -> None:
    obj = SnapshotObject(
        object_id="obj-50-3",
        revision=50,
        layer=0,
        frame_start=0,
        frame_end=29,
        name=None,
        alias="[Object]\r\n",
    )
    response = (
        b'{"id":"py-00000001","ok":true,"result":'
        b'{"applied_count":1,"revision":51,'
        b'"snapshot_required":false,"undo_grouped":true}}'
    )
    stream = ScriptedStream(encode_frame(response), write_chunk=4096)
    client = LiveClient(FramedTransport(stream))

    client.set_text(obj, "one\ntwo")

    payload_size = struct.unpack("<I", stream.written[:4])[0]
    request = json.loads(stream.written[4 : 4 + payload_size])
    assert request["method"] == "object.set_item"
    assert request["params"]["value"] == "one\\ntwo"


def test_probe_media_returns_typed_native_metadata(tmp_path: Path) -> None:
    media_file = tmp_path / "still.png"
    response = (
        b'{"id":"py-00000001","ok":true,"result":{'
        b'"exists":true,"regular_file":true,'
        b'"extension_supported":true,"readable":true,'
        b'"has_media_info":true,"kind":"image",'
        b'"video_track_count":1,"audio_track_count":0,'
        b'"duration_seconds":0.0,"width":1920,"height":1080}}'
    )
    stream = ScriptedStream(encode_frame(response), write_chunk=4096)
    client = LiveClient(FramedTransport(stream))

    probe = client.probe_media(media_file)

    assert probe.kind == "image"
    assert probe.width == 1920
    payload_size = struct.unpack("<I", stream.written[:4])[0]
    request = json.loads(stream.written[4 : 4 + payload_size])
    assert request["method"] == "media.probe"
    assert Path(request["params"]["file"]).is_absolute()


def test_create_media_uses_native_auto_length(tmp_path: Path) -> None:
    media_file = tmp_path / "clip.mp4"
    response = (
        b'{"id":"py-00000001","ok":true,"result":{'
        b'"applied_count":1,"created":{"layer":3,'
        b'"frame_start":20,"frame_end":79},'
        b'"snapshot_required":true,"undo_grouped":true}}'
    )
    stream = ScriptedStream(encode_frame(response), write_chunk=4096)
    client = LiveClient(FramedTransport(stream))

    created = client.add_video(media_file, layer=3, frame=20)

    assert created.duration_frames == 60
    payload_size = struct.unpack("<I", stream.written[:4])[0]
    request = json.loads(stream.written[4 : 4 + payload_size])
    assert request["method"] == "object.create_from_media_file"
    assert request["params"]["length"] == 0


def test_inspect_object_returns_typed_track_metadata() -> None:
    obj = SnapshotObject(
        object_id="obj-50-3",
        revision=50,
        layer=0,
        frame_start=10,
        frame_end=29,
        name=None,
        alias="[Object]\r\n",
    )
    response = (
        b'{"id":"py-00000001","ok":true,"result":{'
        b'"object_id":"obj-50-3","revision":50,"sample_frame":15,'
        b'"effect_count":1,"effects":[{"index":0,"occurrence":0,'
        b'"name":"standard","selector":"standard","enabled":true,'
        b'"locked":false,"items":[{"name":"X","type":"number",'
        b'"type_code":2,"raw_value":"0.0","track":{"mode":"linear",'
        b'"parameters":[0.0,100.0],"sampled_value":50.0,'
        b'"accelerate":false,"decelerate":false,'
        b'"ignore_midpoints":false,"time_control":false,'
        b'"group_count":1,"group_index":0,"group_name":null}}]}]}}'
    )
    stream = ScriptedStream(encode_frame(response), write_chunk=4096)
    client = LiveClient(FramedTransport(stream))

    inspection = client.inspect_object(obj, sample_frame=15)

    item = inspection.effects[0].items[0]
    assert item.track is not None
    assert item.track.sampled_value == 50.0
    payload_size = struct.unpack("<I", stream.written[:4])[0]
    request = json.loads(stream.written[4 : 4 + payload_size])
    assert request["method"] == "object.inspect"
    assert request["params"]["expected_revision"] == 50


def test_set_animation_inspects_track_before_native_update() -> None:
    obj = SnapshotObject(
        object_id="obj-50-3",
        revision=50,
        layer=0,
        frame_start=10,
        frame_end=29,
        name=None,
        alias="[Object]\r\n",
    )
    inspect_response = (
        b'{"id":"py-00000001","ok":true,"result":{'
        b'"object_id":"obj-50-3","revision":50,"sample_frame":10,'
        b'"effect_count":1,"effects":[{"index":0,"occurrence":0,'
        b'"name":"standard","selector":"standard","enabled":true,'
        b'"locked":false,"items":[{"name":"X","type":"number",'
        b'"type_code":2,"raw_value":"0.0","track":{"mode":"linear",'
        b'"parameters":[],"sampled_value":0.0,'
        b'"accelerate":false,"decelerate":false,'
        b'"ignore_midpoints":false,"time_control":false,'
        b'"group_count":1,"group_index":0,"group_name":null}}]}]}}'
    )
    update_response = (
        b'{"id":"py-00000002","ok":true,"result":'
        b'{"applied_count":1,"revision":51,'
        b'"snapshot_required":false,"undo_grouped":true}}'
    )
    stream = ScriptedStream(
        encode_frame(inspect_response) + encode_frame(update_response),
        write_chunk=4096,
    )
    client = LiveClient(FramedTransport(stream))

    result = client.set_animation(
        obj,
        effect="standard",
        item="X",
        value=AnimatedValue(
            0.0,
            100.0,
            AnimationParams("直線移動", "0"),
        ),
    )

    assert result["revision"] == 51
    first_size = struct.unpack("<I", stream.written[:4])[0]
    second_offset = 4 + first_size
    second_size = struct.unpack(
        "<I", stream.written[second_offset : second_offset + 4]
    )[0]
    request = json.loads(
        stream.written[
            second_offset + 4 : second_offset + 4 + second_size
        ]
    )
    assert request["method"] == "object.set_item"
    assert request["params"]["value"] == "0.00,100.00,直線移動,0"


def test_set_playback_rate_converts_multiplier_without_changing_length() -> None:
    obj = SnapshotObject(
        object_id="obj-50-3",
        revision=50,
        layer=0,
        frame_start=10,
        frame_end=109,
        name=None,
        alias="[Object]\r\n",
    )
    inspect_response = (
        b'{"id":"py-00000001","ok":true,"result":{'
        b'"object_id":"obj-50-3","revision":50,"sample_frame":10,'
        b'"effect_count":1,"effects":[{"index":0,"occurrence":0,'
        b'"name":"\xe5\x8b\x95\xe7\x94\xbb\xe3\x83\x95\xe3\x82\xa1'
        b'\xe3\x82\xa4\xe3\x83\xab","selector":"\xe5\x8b\x95\xe7\x94'
        b'\xbb\xe3\x83\x95\xe3\x82\xa1\xe3\x82\xa4\xe3\x83\xab",'
        b'"enabled":true,"locked":false,"items":[{"name":"\xe5\x86\x8d'
        b'\xe7\x94\x9f\xe9\x80\x9f\xe5\xba\xa6","type":"number",'
        b'"type_code":2,"raw_value":"100.00","track":{'
        b'"mode":null,"parameters":[],"sampled_value":100.0,'
        b'"accelerate":false,"decelerate":false,'
        b'"ignore_midpoints":false,"time_control":false,'
        b'"group_count":1,"group_index":0,"group_name":null}}]}]}}'
    )
    update_response = (
        b'{"id":"py-00000002","ok":true,"result":'
        b'{"applied_count":1,"revision":51,'
        b'"snapshot_required":false,"undo_grouped":true}}'
    )
    stream = ScriptedStream(
        encode_frame(inspect_response) + encode_frame(update_response),
        write_chunk=4096,
    )
    client = LiveClient(FramedTransport(stream))

    result = client.set_playback_rate(obj, 2.0)

    assert result["revision"] == 51
    assert result["playback_rate"] == 2.0
    assert result["raw_percent"] == 200.0
    assert result["duration_mode"] == "keep_timeline"
    first_size = struct.unpack("<I", stream.written[:4])[0]
    second_offset = 4 + first_size
    second_size = struct.unpack(
        "<I", stream.written[second_offset : second_offset + 4]
    )[0]
    request = json.loads(
        stream.written[
            second_offset + 4 : second_offset + 4 + second_size
        ]
    )
    assert request["method"] == "object.set_item"
    assert request["params"]["effect"] == "動画ファイル"
    assert request["params"]["item"] == "再生速度"
    assert request["params"]["value"] == "200.000000"


@pytest.mark.parametrize("rate", [0.0, -1.0, float("inf"), float("nan")])
def test_set_playback_rate_rejects_invalid_multiplier(rate: float) -> None:
    stream = ScriptedStream(b"")
    client = LiveClient(FramedTransport(stream))
    obj = SnapshotObject(
        object_id="obj-50-3",
        revision=50,
        layer=0,
        frame_start=10,
        frame_end=109,
        name=None,
        alias="[Object]\r\n",
    )

    with pytest.raises(ValueError):
        client.set_playback_rate(obj, rate)

    assert stream.written == b""


def test_set_playback_rate_refuses_unimplemented_duration_replacement() -> None:
    stream = ScriptedStream(b"")
    client = LiveClient(FramedTransport(stream))
    obj = SnapshotObject(
        object_id="obj-50-3",
        revision=50,
        layer=0,
        frame_start=10,
        frame_end=109,
        name=None,
        alias="[Object]\r\n",
    )

    with pytest.raises(NotImplementedError):
        client.set_playback_rate(
            obj,
            2.0,
            duration_mode="preserve_source_range",  # type: ignore[arg-type]
        )

    assert stream.written == b""


def test_render_frame_reassembles_and_verifies_native_png(
    tmp_path: Path,
) -> None:
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
        "AAAAC0lEQVR42mP8/x8AAusB9Y9ZlKsAAAAASUVORK5CYII="
    )
    digest = hashlib.sha256(png).hexdigest()
    render_result = {
        "byte_size": len(png),
        "capture_id": "cap-99-1",
        "chunk_bytes": 524288,
        "chunk_count": 1,
        "format": "png",
        "frame": 12,
        "height": 1,
        "native_renderer": True,
        "revision": 500,
        "scene_id": 0,
        "sha256": digest,
        "ttl_seconds": 60,
        "width": 1,
    }
    chunk_result = {
        "byte_offset": 0,
        "capture_id": "cap-99-1",
        "data_base64": base64.b64encode(png).decode("ascii"),
        "data_size": len(png),
        "eof": True,
        "index": 0,
    }
    responses = (
        encode_frame(
            json.dumps(
                {"id": "py-00000001", "ok": True, "result": render_result}
            ).encode()
        )
        + encode_frame(
            json.dumps(
                {"id": "py-00000002", "ok": True, "result": chunk_result}
            ).encode()
        )
        + encode_frame(
            b'{"id":"py-00000003","ok":true,'
            b'"result":{"released":true}}'
        )
    )
    stream = ScriptedStream(responses, write_chunk=4096)
    client = LiveClient(FramedTransport(stream))
    destination = tmp_path / "native.png"

    rendered = client.render_frame(12, output_path=destination)

    assert rendered.png == png
    assert rendered.sha256 == digest
    assert rendered.revision == 500
    assert destination.read_bytes() == png
