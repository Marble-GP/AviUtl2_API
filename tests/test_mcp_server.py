"""Tests for the FastMCP integration of the Live Bridge MCP server."""

from __future__ import annotations

from typing import Any

import pytest

from aviutl2_api.live import BridgeRemoteError
from aviutl2_api.mcp import server as mcp_server
from aviutl2_api.mcp.server import (
    TOOL_EXPORTS,
    BridgeConnection,
    bridge_call,
    bridge_help,
)

HOST_FLAGS = {
    "sdk_scene_crud": True,
    "sdk_object_rendering": True,
    "sdk_frame_marks": True,
    "sdk_section_endpoints": True,
    "sdk_move_effect": True,
    "version": 2011000,
}
HOST_METHODS = [
    "system.hello",
    "system.ping",
    "system.get_capabilities",
    "session.open",
    "scene.list",
    "scene.create",
    "scene.get_current",
    "mark.set",
    "project.get_info",
    "object.render_frame",
]


class FakeClient:
    """Scripted LiveClient double recording every bridge call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def open_session(self, client_name: str) -> dict[str, Any]:
        return {"id": 1, "client_name": client_name}

    def close(self) -> None:
        pass

    def hello(self) -> dict[str, Any]:
        return {"pid": 42, "edit_state": "idle", "plugin_version": "0.10.0"}

    def get_capabilities(self) -> dict[str, Any]:
        return {"methods": list(HOST_METHODS), "host": dict(HOST_FLAGS)}

    def get_project_info(self) -> dict[str, Any]:
        return {"path": "demo.aup2", "frame": 0}

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, params))
        return {"echo": method}


class FakeConnection:
    """BridgeConnection double that never touches the named pipe."""

    def __init__(self) -> None:
        self._client = FakeClient()

    def client(self) -> FakeClient:
        return self._client

    def close(self) -> None:
        pass


@pytest.fixture()
def fake_connection(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    connection = FakeConnection()
    monkeypatch.setattr(mcp_server, "_CONNECTION", connection)
    return connection._client


def test_tool_exports_are_unique_and_documented() -> None:
    names = [tool.__name__ for tool in TOOL_EXPORTS]
    assert len(names) == len(set(names))
    for tool in TOOL_EXPORTS:
        assert tool.__doc__


def test_bridge_call_unknown_method_fails_closed(
    fake_connection: FakeClient,
) -> None:
    result = bridge_call("no.such.method")
    assert result["ok"] is False
    assert result["error"]["code"] == "METHOD_NOT_FOUND"
    assert fake_connection.calls == []


def test_bridge_call_method_unavailable_fails_closed(
    fake_connection: FakeClient,
) -> None:
    result = bridge_call("mark.move")
    assert result["ok"] is False
    assert result["error"]["code"] == "METHOD_UNAVAILABLE"
    assert fake_connection.calls == []


def test_bridge_call_success(fake_connection: FakeClient) -> None:
    result = bridge_call("scene.list")
    assert result == {"ok": True, "result": {"echo": "scene.list"}}


def test_bridge_call_requires_confirmation(
    fake_connection: FakeClient,
) -> None:
    result = bridge_call(
        "mark.set",
        {"expected_revision": 1, "frame": 2, "memo": "x"},
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert fake_connection.calls == []


def test_bridge_call_strips_agent_confirmation(
    fake_connection: FakeClient,
) -> None:
    result = bridge_call(
        "scene.create",
        {"name": "new", "confirm_non_undoable": True},
    )
    assert result["ok"] is True
    method, params = fake_connection.calls[-1]
    assert method == "scene.create"
    assert params == {"name": "new"}


def test_bridge_call_remote_error_payload(
    fake_connection: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_call(
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        raise BridgeRemoteError("STALE_PROJECT_STATE", "project changed")

    monkeypatch.setattr(fake_connection, "call", failing_call)
    result = bridge_call("scene.list")
    assert result["ok"] is False
    assert result["error"]["code"] == "STALE_PROJECT_STATE"


def test_bridge_call_connection_error_reconnects_next_time(
    fake_connection: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_call(
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        raise ConnectionError("pipe closed")

    monkeypatch.setattr(fake_connection, "call", failing_call)
    result = bridge_call("scene.list")
    assert result["ok"] is False
    assert result["error"]["code"] == "BRIDGE_UNAVAILABLE"


def test_bridge_help_method(fake_connection: FakeClient) -> None:
    help_result = bridge_help("scene.create")
    assert help_result["kind"] == "method"
    assert help_result["required_host_flag"] == "sdk_scene_crud"


def test_bridge_help_falls_back_to_profiles(
    fake_connection: FakeClient,
) -> None:
    help_result = bridge_help("mosaic")
    assert help_result.get("kind") not in (None, "method")


def test_bridge_help_unknown_subject(fake_connection: FakeClient) -> None:
    help_result = bridge_help("no-such-subject-xyz")
    assert help_result["error"]["code"] == "HELP_NOT_FOUND"
    assert help_result["error"]["effect_profiles"]


def test_bridge_connection_is_lazy() -> None:
    connection = BridgeConnection()
    assert connection._client is None
    connection.close()
    assert connection._client is None
