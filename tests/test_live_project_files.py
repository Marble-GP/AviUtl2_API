"""Tests for scene CRUD, project file, export, flag, and stable-ID clients."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aviutl2_api.live import (
    BackgroundColor,
    ExportStartReceipt,
    ObjectFlagState,
    ProjectMutation,
    SceneList,
    SceneListEntry,
    SnapshotObject,
    StableId,
)
from aviutl2_api.live.client import LiveClient
from aviutl2_api.live.protocol import ProtocolError, encode_frame
from aviutl2_api.live.transport import FramedTransport


class ScriptedStream:
    """A deterministic byte stream served to exactly one exchange call."""

    def __init__(self, incoming: bytes) -> None:
        self.incoming = bytearray(incoming)
        self.written = bytearray()
        self.closed = False

    def read(self, size: int, timeout: float) -> bytes:
        count = min(size, len(self.incoming))
        data = bytes(self.incoming[:count])
        del self.incoming[:count]
        return data

    def write(self, data: bytes, timeout: float) -> int:
        self.written.extend(data)
        return len(data)

    def close(self) -> None:
        self.closed = True


def _client_with(response: dict[str, object]) -> tuple[LiveClient, ScriptedStream]:
    stream = ScriptedStream(encode_frame(json.dumps(response).encode("utf-8")))
    client = LiveClient(FramedTransport(stream))
    return client, stream


def _success(
    result: dict[str, object],
    request_id: str = "py-00000001",
) -> dict[str, object]:
    return {"id": request_id, "ok": True, "result": result}


def _client_with_success(
    result: dict[str, object],
) -> tuple[LiveClient, ScriptedStream]:
    return _client_with(_success(result))


_TARGET = SnapshotObject(
    "obj-42-3",
    42,
    1,
    0,
    29,
    None,
    None,
)


def test_scene_list_parses_entries() -> None:
    client, stream = _client_with_success(
        {"count": 1, "scenes": [{"name": "A", "scene_id": 2}]}
    )
    scenes = client.list_scenes()
    assert isinstance(scenes, SceneList)
    assert scenes.scenes == (SceneListEntry(name="A", scene_id=2),)
    request = json.loads(bytes(stream.written[4:]))
    assert request["method"] == "scene.list"
    assert request["params"] == {}


def test_scene_list_rejects_count_mismatch() -> None:
    client, _ = _client_with_success(
        {"count": 2, "scenes": [{"name": "A", "scene_id": 2}]}
    )
    with pytest.raises(ProtocolError):
        client.list_scenes()


def test_create_scene_wire_and_defaults() -> None:
    client, stream = _client_with_success({"non_undoable": True, "revision": 7})
    mutation = client.create_scene(
        "New",
        width=1920,
        height=1080,
        rate=30,
        scale=1,
        sample_rate=48000,
    )
    assert mutation == ProjectMutation(revision=7)
    request = json.loads(bytes(stream.written[4:]))
    assert request["method"] == "scene.create"
    assert request["params"]["name"] == "New"
    assert "label" not in request["params"]
    assert "background" not in request["params"]


def test_create_scene_background_wire() -> None:
    client, stream = _client_with_success({"non_undoable": True, "revision": 7})
    client.create_scene(
        "New",
        width=1920,
        height=1080,
        rate=30,
        scale=1,
        sample_rate=48000,
        background={"r": 16, "g": 32, "b": 64},
    )
    request = json.loads(bytes(stream.written[4:]))
    assert request["params"]["background"] == {"r": 16, "g": 32, "b": 64, "a": 255}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("width", 0),
        ("height", -1),
        ("rate", 0),
        ("scale", -3),
        ("sample_rate", 0),
    ],
)
def test_create_scene_rejects_non_positive(field: str, value: int) -> None:
    client, _ = _client_with_success({"non_undoable": True, "revision": 7})
    values = {
        "width": 1920,
        "height": 1080,
        "rate": 30,
        "scale": 1,
        "sample_rate": 48000,
    }
    values[field] = value
    with pytest.raises(ValueError):
        client.create_scene("New", **values)


def test_switch_scene_wire() -> None:
    client, stream = _client_with_success({"non_undoable": True, "revision": 9})
    mutation = client.switch_scene(2)
    assert mutation.revision == 9
    request = json.loads(bytes(stream.written[4:]))
    assert request["method"] == "scene.switch"
    assert request["params"] == {"scene_id": 2}


def test_switch_scene_rejects_negative() -> None:
    client, _ = _client_with_success({"non_undoable": True, "revision": 9})
    with pytest.raises(ValueError):
        client.switch_scene(-1)


def test_create_project_wire() -> None:
    client, stream = _client_with_success({"non_undoable": True, "revision": 3})
    mutation = client.create_project(
        width=1920,
        height=1080,
        rate=60,
        scale=1,
        sample_rate=48000,
        background=BackgroundColor(r=1, g=2, b=3, a=255),
    )
    assert mutation.revision == 3
    request = json.loads(bytes(stream.written[4:]))
    assert request["method"] == "project.create"
    assert request["params"]["background"] == {"r": 1, "g": 2, "b": 3, "a": 255}
    assert "show_confirm" not in request["params"]


def test_open_project_wire() -> None:
    client, stream = _client_with_success({"non_undoable": True, "revision": 8})
    mutation = client.open_project("/projects/sample.aup2")
    assert mutation.revision == 8
    request = json.loads(bytes(stream.written[4:]))
    assert request["method"] == "project.open"
    assert request["params"] == {
        "file": str(Path("/projects/sample.aup2").expanduser().resolve()),
    }


def test_save_project_wire() -> None:
    client, stream = _client_with_success({"non_undoable": True, "revision": 9})
    mutation = client.save_project("/projects/out.aup2")
    assert mutation.revision == 9
    request = json.loads(bytes(stream.written[4:]))
    assert request["method"] == "project.save"


def test_start_export_wire() -> None:
    client, stream = _client_with_success({"non_undoable": True, "started": True})
    receipt = client.start_export(
        "/out/video.mp4",
        output_plugin="拡張編集MP4出力",
    )
    assert receipt == ExportStartReceipt()
    request = json.loads(bytes(stream.written[4:]))
    assert request["method"] == "export.start"
    assert request["params"]["output_plugin"] == "拡張編集MP4出力"


def test_start_export_rejects_blank_plugin() -> None:
    client, _ = _client_with_success({"non_undoable": True, "started": True})
    with pytest.raises(ValueError):
        client.start_export("/out/video.mp4", output_plugin="")


def test_object_flag_get_wire() -> None:
    client, stream = _client_with_success({"flag": False, "kind": "enable_group"})
    state = client.get_object_flag(_TARGET, "enable_group")
    assert state == ObjectFlagState("enable_group", False)
    request = json.loads(bytes(stream.written[4:]))
    assert request["method"] == "object.flag.get"
    assert request["params"]["expected_revision"] == 42
    assert request["params"]["target"] == {"object_id": "obj-42-3"}
    assert request["params"]["kind"] == "enable_group"


def test_object_flag_set_wire() -> None:
    client, stream = _client_with_success(
        {"kind": "enable_group", "non_undoable": True, "revision": 43}
    )
    mutation = client.set_object_flag(_TARGET, "enable_group", flag=True)
    assert mutation.revision == 43
    request = json.loads(bytes(stream.written[4:]))
    assert request["method"] == "object.flag.set"
    assert request["params"]["flag"] is True


@pytest.mark.parametrize(
    "kind",
    ["", "bogus", "enable_group ", "ENABLE_GROUP"],
)
def test_object_flag_rejects_unknown_kind(kind: str) -> None:
    client, _ = _client_with_success({"flag": True, "kind": kind})
    with pytest.raises(ValueError):
        client.get_object_flag(_TARGET, kind)
    with pytest.raises(ValueError):
        client.set_object_flag(_TARGET, kind, flag=True)


def test_object_id_wire() -> None:
    client, stream = _client_with_success({"id": 9001, "scope": "object"})
    stable = client.get_object_id(_TARGET)
    assert stable == StableId(9001, "object")
    request = json.loads(bytes(stream.written[4:]))
    assert request["method"] == "object.id.get"


def test_effect_id_wire() -> None:
    client, stream = _client_with_success({"id": 9002, "scope": "effect"})
    stable = client.get_effect_id(_TARGET, effect="標準描画")
    assert stable == StableId(9002, "effect")
    request = json.loads(bytes(stream.written[4:]))
    assert request["method"] == "effect.id.get"
    assert request["params"]["effect"] == "標準描画"


def test_stable_id_rejects_bad_scope() -> None:
    client, _ = _client_with_success({"id": 9001, "scope": "timeline"})
    with pytest.raises(ProtocolError):
        client.get_object_id(_TARGET)


def test_project_mutation_rejects_undoable() -> None:
    client, _ = _client_with_success({"non_undoable": False, "revision": 5})
    with pytest.raises(ProtocolError):
        client.switch_scene(0)


def test_export_receipt_rejects_not_started() -> None:
    client, _ = _client_with_success({"non_undoable": True, "started": False})
    with pytest.raises(ProtocolError):
        client.start_export("/out/video.mp4", output_plugin="MP4")


def test_background_color_validation() -> None:
    BackgroundColor()
    with pytest.raises(ValueError):
        BackgroundColor(r=256)
    with pytest.raises(ValueError):
        BackgroundColor(a=-1)
    with pytest.raises(TypeError):
        BackgroundColor.from_value(7)


def test_mutation_methods_cover_new_operations() -> None:
    from aviutl2_api.live.client import _MUTATION_METHODS

    for method in (
        "export.start",
        "object.flag.set",
        "project.create",
        "project.open",
        "project.save",
        "scene.create",
        "scene.switch",
    ):
        assert method in _MUTATION_METHODS


def test_exports_from_package_root() -> None:
    import aviutl2_api.live as live

    for name in (
        "BackgroundColor",
        "ExportStartReceipt",
        "OBJECT_FLAG_KINDS",
        "ObjectFlagState",
        "ProjectMutation",
        "SceneList",
        "SceneListEntry",
        "StableId",
    ):
        assert hasattr(live, name)
        assert name in live.__all__
