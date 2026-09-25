"""Host-agnostic core logic for the AviUtl2 Live Bridge MCP server.

The method catalog, confirmation gates, and status summarization stay free of
the optional ``mcp`` dependency so they remain testable in every environment.
All text is ASCII-only to keep the Windows transfer path byte-clean.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SERVER_NAME = "aviutl2-live-bridge"

SERVER_INSTRUCTIONS = (
    "Tools talk to the AviUtl2 Live Bridge named pipe of ONE running "
    "AviUtl2 process. Call bridge_card first. Mutations are revision-checked "
    "and stale-safe; non-undoable methods require confirm_non_undoable=true."
)

# Methods the plugin itself rejects unless confirm_non_undoable=true travels
# on the wire. The key is forwarded unchanged.
WIRE_CONFIRMATION_METHODS: frozenset[str] = frozenset(
    {
        "scene.update_current",
        "mark.set",
        "mark.clear",
        "mark.move",
    }
)

# Non-undoable methods where the MCP layer itself demands an explicit
# acknowledgment before the call. The key is stripped because the wire
# protocol does not expect it for these methods.
AGENT_CONFIRMATION_METHODS: frozenset[str] = frozenset(
    {
        "scene.create",
        "scene.switch",
        "project.create",
        "project.open",
        "project.save",
        "export.start",
    }
)

METHOD_DESCRIPTIONS: dict[str, str] = {
    "system.hello": "Handshake: plugin/protocol versions and edit state.",
    "system.ping": "Liveness probe.",
    "system.get_capabilities": "Full capability manifest for this host.",
    "session.open": "Bind a session for idempotent mutation operation ids.",
    "event.watch": ("Long-poll sequenced host events such as edit_state_changed."),
    "scene.get_current": "Current scene id, revision, name, and settings.",
    "scene.update_current": (
        "Update current scene settings; non-undoable; needs confirm_non_undoable."
    ),
    "scene.list": "List every scene in the open project.",
    "scene.create": "Create a scene; non-undoable; needs MCP confirmation.",
    "scene.switch": "Switch the active scene; non-undoable; needs MCP confirmation.",
    "project.create": (
        "Replace the running project with a new one; needs MCP confirmation."
    ),
    "project.open": (
        "Open one .aup2 file in the running process; needs MCP confirmation."
    ),
    "project.save": (
        "Save the running project to one .aup2 path; needs MCP confirmation."
    ),
    "export.start": (
        "Start an asynchronous file export; completion arrives via "
        "edit_state_changed; needs MCP confirmation."
    ),
    "object.flag.get": "Read one object flag (group/camera/clipping).",
    "object.flag.set": "Write one object flag; revision-checked with rollback.",
    "object.id.get": "Stable int64 id for one object.",
    "effect.id.get": "Stable int64 id for one named effect on one object.",
    "effect.catalog": "Paged effect and setting-item catalog.",
    "edit.plan.validate": "Validate an EditPlan without applying it.",
    "edit.plan.apply": "Apply an EditPlan as one undo unit.",
    "font.catalog": "Paged font name catalog.",
    "palette.catalog": "Paged palette catalog with RGBA colors.",
    "module.catalog": "Paged module (plugin DLL) catalog.",
    "project.get_info": "Project path, cursor frame, and scene summary.",
    "project.get_layers": "Paged layer list with object counts.",
    "project.get_snapshot": ("Revision-scoped object snapshot for stale-safe editing."),
    "layer.update": "Rename, move, or toggle visibility of one layer.",
    "media.probe": "Probe one media file for duration, fps, and size.",
    "media.inventory": "List media sources referenced by the project.",
    "media.relink": "Repoint one media source to a new file.",
    "object.create_from_alias": "Create one object from a serialized alias.",
    "object.create_from_media_file": "Create one image/video/audio object.",
    "object.effect.add": "Add one effect to an object.",
    "object.effect.delete": "Delete one effect from an object.",
    "object.effect.set_enabled": "Enable or disable one effect.",
    "object.effect.reorder": (
        "Reorder effects natively; object identity stays stable."
    ),
    "object.inspect": "Inspect one object's items and effects.",
    "object.section.list": "List clip sections of one object.",
    "object.section.create": "Create a clip section on one object.",
    "object.section.delete": "Delete one clip section.",
    "object.section.move": "Move one clip section in time.",
    "frame.render": "Render one full frame; returns PNG metadata.",
    "frame.read_chunk": "Read one PNG byte chunk.",
    "frame.release": "Release a finished PNG capture.",
    "audio.render": "Render one audio range; returns PCM metadata.",
    "audio.read_chunk": "Read one PCM byte chunk.",
    "audio.release": "Release a finished PCM capture.",
    "object.render_frame": "Render one object to a verified PNG capture.",
    "object.render_audio": ("Render one object to verified stereo f32le PCM."),
    "object.set_item": "Set one inspection item on an object.",
    "object.set_items": "Set several inspection items atomically.",
    "object.set_name": "Rename one object.",
    "object.split_media": "Split one media object at a frame.",
    "object.set_duration": "Change one object's duration.",
    "media.trim": "Trim media in/out points with preflight and rollback.",
    "timeline.transaction.validate": "Validate a timeline transaction.",
    "timeline.transaction.apply": "Apply a timeline transaction.",
    "timeline.shift_after": "Shift objects after a frame.",
    "timeline.ripple_insert": "Ripple-insert empty time at a frame.",
    "timeline.ripple_delete": "Ripple-delete a frame range.",
    "timeline.close_gap": "Close a gap after a frame.",
    "object.move": "Move one object to another placement.",
    "object.delete": "Delete one object in one undo unit.",
    "batch.validate": "Validate a batch of commands.",
    "batch.apply": "Apply a batch of commands atomically.",
    "mark.list": "List frame marks on the timeline.",
    "mark.set": "Set one frame mark memo; non-undoable; needs confirmation.",
    "mark.clear": "Clear one frame mark; non-undoable; needs confirmation.",
    "mark.move": "Move one frame mark; non-undoable; needs confirmation.",
}


def check_confirmation(
    method: str,
    params: Mapping[str, Any],
) -> None:
    """Require the explicit acknowledgment for non-undoable methods."""

    if (
        method in WIRE_CONFIRMATION_METHODS or method in AGENT_CONFIRMATION_METHODS
    ) and params.get("confirm_non_undoable") is not True:
        raise ValueError(
            f"{method} is non-undoable and requires confirm_non_undoable=true"
        )


def strip_confirmation(
    method: str,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """Drop the MCP-level acknowledgment key where the wire does not take it."""

    if method in AGENT_CONFIRMATION_METHODS:
        return {
            key: value for key, value in params.items() if key != "confirm_non_undoable"
        }
    return dict(params)


def is_known_method(method: str) -> bool:
    """Return whether the MCP catalog knows the method."""

    return method in METHOD_DESCRIPTIONS


def method_category(method: str) -> str:
    """Return the dotted prefix category of a method name."""

    return method.split(".", 1)[0]


def grouped_methods() -> dict[str, list[str]]:
    """Return catalog methods grouped by category, alphabetically sorted."""

    groups: dict[str, list[str]] = {}
    for method in sorted(METHOD_DESCRIPTIONS):
        groups.setdefault(method_category(method), []).append(method)
    return groups


HOST_FLAG_GATES: dict[str, str] = {
    "scene.": "sdk_scene_crud",
    "project.create": "sdk_scene_crud",
    "project.open": "sdk_scene_crud",
    "project.save": "sdk_scene_crud",
    "export.start": "sdk_scene_crud",
    "object.flag.": "sdk_scene_crud",
    "object.id.get": "sdk_scene_crud",
    "effect.id.get": "sdk_scene_crud",
    "object.render_frame": "sdk_object_rendering",
    "object.render_audio": "sdk_object_rendering",
    "mark.": "sdk_frame_marks",
    "object.section.": "sdk_section_endpoints",
    "object.effect.reorder": "sdk_move_effect",
    "media.trim": "sdk_move_effect",
}


def required_host_flag(method: str) -> str | None:
    """Return the capabilities.host flag required for one method."""

    for prefix, flag in HOST_FLAG_GATES.items():
        if method == prefix or method.startswith(prefix):
            return flag
    return None


def is_host_capable(
    method: str,
    host: Mapping[str, Any],
) -> bool:
    """Fail closed when the running host lacks the required feature flag."""

    flag = required_host_flag(method)
    if flag is None:
        return True
    return host.get(flag) is True


def gate_call(
    method: str,
    params: Mapping[str, Any],
    capabilities: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Validate one bridge call against the host capability manifest.

    Returns ``(forwarded_params, None)`` when the call may proceed, or
    ``(None, error_payload)`` when it must fail closed.
    """

    if not is_known_method(method):
        return None, method_not_found(method)
    host_methods: list[str] = []
    methods_value = capabilities.get("methods")
    if isinstance(methods_value, list):
        host_methods = [value for value in methods_value if isinstance(value, str)]
    if method not in host_methods:
        return None, method_unavailable(method, host_methods)
    host = capabilities.get("host")
    if not isinstance(host, dict) or not is_host_capable(method, host):
        return None, error_payload(
            "HOST_FEATURE_UNAVAILABLE",
            f"the running AviUtl2 host lacks the feature required by {method!r}",
            required_flag=required_host_flag(method),
        )
    try:
        check_confirmation(method, params)
    except ValueError as error:
        return None, error_payload("CONFIRMATION_REQUIRED", str(error))
    return strip_confirmation(method, params), None


def error_payload(
    code: str,
    message: str,
    **extra: Any,
) -> dict[str, Any]:
    """Build a structured MCP error result."""

    error: dict[str, Any] = {"code": code, "message": message}
    error.update(extra)
    return {"ok": False, "error": error}


def method_not_found(method: str) -> dict[str, Any]:
    """Fail closed on methods outside the MCP catalog."""

    return error_payload(
        "METHOD_NOT_FOUND",
        f"{method!r} is not in the MCP method catalog",
        known_categories=sorted(grouped_methods()),
    )


def method_unavailable(
    method: str,
    host_methods: list[str],
) -> dict[str, Any]:
    """Fail closed on methods the running host does not expose."""

    return error_payload(
        "METHOD_UNAVAILABLE",
        f"the running AviUtl2 host does not expose {method!r}; "
        "see host_methods for the exposed set",
        host_method_count=len(host_methods),
        host_methods=sorted(host_methods),
    )


def summarize_status(
    hello: Mapping[str, Any],
    capabilities: Mapping[str, Any],
    project_info: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge handshake, capabilities, and project info into one report."""

    return {
        "pid": hello.get("pid"),
        "edit_state": hello.get("edit_state"),
        "plugin_version": hello.get("plugin_version"),
        "protocol_version": hello.get("protocol_version"),
        "sdk_baseline": hello.get("sdk_baseline"),
        "host": capabilities.get("host"),
        "sessions": capabilities.get("sessions"),
        "notifications": capabilities.get("notifications"),
        "release_gate": capabilities.get("release_gate"),
        "methods": capabilities.get("methods"),
        "project": dict(project_info),
    }


def build_card() -> str:
    """Return the self-contained usage card for the MCP tools."""

    lines = [
        "AviUtl2 Live Bridge MCP (Phase 1)",
        "=================================",
        "",
        "All tools talk to the Live Bridge named pipe of ONE running AviUtl2",
        "ver.2 process on this machine.",
        "",
        "Tools",
        "- bridge_card                 This card.",
        "- bridge_status               Host report: flags, methods, project info.",
        "- bridge_call                 Generic bridge call (capability-gated).",
        "- bridge_help                 Effect/property/API/method schema help.",
        "- bridge_find                 Search effect/font/palette/module catalogs.",
        "- bridge_snapshot             Revision-scoped object list of the scene.",
        "- bridge_render_object        Render one object to a PNG file.",
        "- bridge_render_object_audio  Render one object to f32le PCM file.",
        "- bridge_watch_events         Long-poll host events (export/playback).",
        "",
        "Typical workflow",
        "1. bridge_status -> confirm the host and project.",
        "2. bridge_snapshot -> collect object_id + expected_revision pairs.",
        "3. bridge_call mutations with expected_revision (stale-safe rollback).",
        "4. bridge_render_object / bridge_watch_events to verify results.",
        "",
        "Safety contract",
        "- Mutations are revision-checked; a changed host returns "
        "STALE_PROJECT_STATE and the bridge rolls the change back.",
        "- Non-undoable methods need confirm_non_undoable=true in params:",
        "  "
        + ", ".join(sorted(WIRE_CONFIRMATION_METHODS | AGENT_CONFIRMATION_METHODS))
        + " (the key is stripped where the wire does not take it).",
        "- mark.set memo: one line up to 1024 chars; at most 256 marks.",
        "- export.start returns immediately; watch edit_state_changed through",
        "  bridge_watch_events to detect completion.",
        "- Unsupported features are never exposed: methods missing from the",
        "  running host fail closed with METHOD_UNAVAILABLE.",
        "",
        "Host version gates (capabilities.host flags)",
        "- sdk_scene_crud (scene.*, project.*, export, flags, stable ids): 2.1.10+",
        "- sdk_object_rendering / sdk_move_effect: 2.1.3+ (2010300)",
        "- sdk_frame_marks / sdk_section_endpoints: 2.1.4+ (2010400)",
        "",
        "Deep documentation (Python workflow)",
        "- docs/AGENT_API_CARD.md (compact agent card)",
        "- docs/LIVE_BRIDGE_AGENT_API_MANUAL.md (full manual)",
        "- docs/LIVE_BRIDGE_PROTOCOL.md (wire protocol)",
    ]
    return "\n".join(lines)


__all__ = [
    "AGENT_CONFIRMATION_METHODS",
    "METHOD_DESCRIPTIONS",
    "SERVER_INSTRUCTIONS",
    "SERVER_NAME",
    "WIRE_CONFIRMATION_METHODS",
    "build_card",
    "check_confirmation",
    "error_payload",
    "grouped_methods",
    "is_known_method",
    "method_category",
    "method_not_found",
    "method_unavailable",
    "strip_confirmation",
    "summarize_status",
]
