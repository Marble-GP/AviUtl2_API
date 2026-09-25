"""Tests for the dependency-free core logic of the Live Bridge MCP server."""

from __future__ import annotations

from typing import Any

import pytest

from aviutl2_api.mcp.core import (
    AGENT_CONFIRMATION_METHODS,
    METHOD_DESCRIPTIONS,
    WIRE_CONFIRMATION_METHODS,
    build_card,
    check_confirmation,
    error_payload,
    gate_call,
    grouped_methods,
    is_host_capable,
    is_known_method,
    method_category,
    method_not_found,
    method_unavailable,
    required_host_flag,
    strip_confirmation,
    summarize_status,
)

CAPABILITIES: dict[str, Any] = {
    "methods": [
        "system.hello",
        "scene.create",
        "scene.list",
        "scene.update_current",
        "mark.set",
        "project.get_info",
        "object.render_frame",
    ],
    "host": {
        "sdk_scene_crud": True,
        "sdk_object_rendering": False,
        "sdk_frame_marks": True,
        "version": 2011000,
    },
}


def test_every_catalog_method_has_nonempty_ascii_description() -> None:
    for method, description in METHOD_DESCRIPTIONS.items():
        assert description
        assert description.isascii(), method


def test_confirmation_sets_are_disjoint() -> None:
    assert not WIRE_CONFIRMATION_METHODS & AGENT_CONFIRMATION_METHODS


def test_confirmation_methods_are_documented() -> None:
    for method in WIRE_CONFIRMATION_METHODS | AGENT_CONFIRMATION_METHODS:
        assert method in METHOD_DESCRIPTIONS


def test_grouped_methods_covers_catalog() -> None:
    groups = grouped_methods()
    flattened = [name for names in groups.values() for name in names]
    assert flattened == sorted(METHOD_DESCRIPTIONS)
    assert list(groups) == sorted(groups)


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("system.hello", "system"),
        ("scene.create", "scene"),
        ("object.flag.set", "object"),
        ("timeline.close_gap", "timeline"),
    ],
)
def test_method_category(method: str, expected: str) -> None:
    assert method_category(method) == expected


def test_is_known_method() -> None:
    assert is_known_method("scene.list")
    assert not is_known_method("not.a.method")


@pytest.mark.parametrize(
    ("method", "flag"),
    [
        ("scene.create", "sdk_scene_crud"),
        ("scene.switch", "sdk_scene_crud"),
        ("project.open", "sdk_scene_crud"),
        ("export.start", "sdk_scene_crud"),
        ("object.flag.set", "sdk_scene_crud"),
        ("object.id.get", "sdk_scene_crud"),
        ("effect.id.get", "sdk_scene_crud"),
        ("object.render_frame", "sdk_object_rendering"),
        ("object.render_audio", "sdk_object_rendering"),
        ("mark.set", "sdk_frame_marks"),
        ("object.section.move", "sdk_section_endpoints"),
        ("object.effect.reorder", "sdk_move_effect"),
        ("media.trim", "sdk_move_effect"),
        ("system.hello", None),
        ("project.get_info", None),
    ],
)
def test_required_host_flag(method: str, flag: str | None) -> None:
    assert required_host_flag(method) == flag


def test_is_host_capable_fails_closed() -> None:
    host = {"sdk_object_rendering": False}
    assert not is_host_capable("object.render_frame", host)
    assert is_host_capable("object.render_frame", {"sdk_object_rendering": True})
    assert is_host_capable("system.hello", {})
    assert not is_host_capable("mark.set", {})


def test_check_confirmation_wire_methods() -> None:
    for method in WIRE_CONFIRMATION_METHODS:
        with pytest.raises(ValueError, match="confirm_non_undoable"):
            check_confirmation(
                method,
                {},
            )
        check_confirmation(
            method,
            {"confirm_non_undoable": True},
        )


def test_check_confirmation_agent_methods() -> None:
    for method in AGENT_CONFIRMATION_METHODS:
        with pytest.raises(ValueError, match="confirm_non_undoable"):
            check_confirmation(
                method,
                {},
            )
        check_confirmation(
            method,
            {"confirm_non_undoable": True},
        )


def test_check_confirmation_regular_methods_pass() -> None:
    check_confirmation(
        "scene.list",
        {},
    )
    check_confirmation(
        "object.render_frame",
        {},
    )


def test_strip_confirmation_agent_methods() -> None:
    forwarded = strip_confirmation(
        "scene.create",
        {"name": "new", "confirm_non_undoable": True},
    )
    assert forwarded == {"name": "new"}


def test_strip_confirmation_wire_methods_keep_key() -> None:
    wire_params = {"confirm_non_undoable": True, "memo": "x"}
    forwarded = strip_confirmation(
        "mark.set",
        wire_params,
    )
    assert forwarded == wire_params


def test_strip_confirmation_regular_methods_copy() -> None:
    original = {"start": 0}
    forwarded = strip_confirmation(
        "scene.list",
        original,
    )
    assert forwarded == original
    assert forwarded is not original


def test_error_payload() -> None:
    payload = error_payload("CODE", "message", retryable=True)
    assert payload == {
        "ok": False,
        "error": {
            "code": "CODE",
            "message": "message",
            "retryable": True,
        },
    }


def test_method_not_failed_payload() -> None:
    payload = method_not_found("no.such.method")
    assert payload["error"]["code"] == "METHOD_NOT_FOUND"
    assert payload["error"]["known_categories"] == sorted(grouped_methods())


def test_method_unavailable_lists_host_methods() -> None:
    payload = method_unavailable(
        "mark.list",
        ["system.hello"],
    )
    assert payload["error"]["code"] == "METHOD_UNAVAILABLE"
    assert payload["error"]["host_methods"] == ["system.hello"]
    assert payload["error"]["host_method_count"] == 1


def test_gate_call_accepts_supported_method() -> None:
    forwarded, failure = gate_call(
        "scene.list",
        {},
        CAPABILITIES,
    )
    assert failure is None
    assert forwarded == {}


def test_gate_call_fails_closed_on_unknown_method() -> None:
    forwarded, failure = gate_call(
        "no.such.method",
        {},
        CAPABILITIES,
    )
    assert forwarded is None
    assert failure is not None
    assert failure["error"]["code"] == "METHOD_NOT_FOUND"


def test_gate_call_fails_closed_on_missing_host_method() -> None:
    forwarded, failure = gate_call(
        "mark.move",
        {},
        CAPABILITIES,
    )
    assert failure is not None
    assert failure["error"]["code"] == "METHOD_UNAVAILABLE"
    assert failure["error"]["host_method_count"] == len(CAPABILITIES["methods"])


def test_gate_call_forwards_wire_confirmation() -> None:
    wire_params = {"expected_revision": 1, "memo": "x", "confirm_non_undoable": True}
    forwarded, failure = gate_call(
        "mark.set",
        wire_params,
        CAPABILITIES,
    )
    assert failure is None
    assert forwarded == wire_params


def test_gate_call_requires_wire_confirmation() -> None:
    forwarded, failure = gate_call(
        "mark.set",
        {"expected_revision": 1, "memo": "x"},
        CAPABILITIES,
    )
    assert forwarded is None
    assert failure is not None
    assert failure["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_gate_call_strips_agent_confirmation() -> None:
    forwarded, failure = gate_call(
        "scene.create",
        {"name": "new", "confirm_non_undoable": True},
        CAPABILITIES,
    )
    assert failure is None
    assert forwarded == {"name": "new"}


def test_gate_call_requires_agent_confirmation() -> None:
    forwarded, failure = gate_call(
        "scene.create",
        {"name": "new"},
        CAPABILITIES,
    )
    assert forwarded is None
    assert failure is not None
    assert failure["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_gate_call_ignores_stale_key_for_agent_methods() -> None:
    forwarded, failure = gate_call(
        "scene.create",
        {"name": "new", "confirm_non_undoable": False},
        CAPABILITIES,
    )
    assert forwarded is None
    assert failure is not None
    assert failure["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_summarize_status_merges_reports() -> None:
    summary = summarize_status(
        {"pid": 123, "edit_state": "idle"},
        {"host": {"version": 2011000}, "methods": ["system.hello"]},
        {"path": "demo.aup2"},
    )
    assert summary["pid"] == 123
    assert summary["edit_state"] == "idle"
    assert summary["host"] == {"version": 2011000}
    assert summary["methods"] == ["system.hello"]
    assert summary["project"] == {"path": "demo.aup2"}


def test_build_card_is_ascii_and_complete() -> None:
    card = build_card()
    assert card.isascii()
    for tool in (
        "bridge_card",
        "bridge_status",
        "bridge_call",
        "bridge_help",
        "bridge_find",
        "bridge_snapshot",
        "bridge_render_object",
        "bridge_render_object_audio",
        "bridge_watch_events",
    ):
        assert tool in card, tool
    assert "confirm_non_undoable" in card
    assert "STALE_PROJECT_STATE" in card
    assert "METHOD_UNAVAILABLE" in card
