"""FastMCP stdio server for the AviUtl2 Live Bridge (Phase 1).

Windows-local only: tools attach to the named pipe of ONE running
AviUtl2 process on this machine. Method exposure derives from the
running host capability manifest and fails closed for unsupported
features.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aviutl2_api.effect_profiles import (
    available_effect_profiles,
    describe_effect_profile,
)
from aviutl2_api.live import (
    AmbiguousInstanceError,
    BridgeRemoteError,
    LiveClient,
    LiveProject,
    SnapshotObject,
)

from .core import (
    METHOD_DESCRIPTIONS,
    SERVER_INSTRUCTIONS,
    SERVER_NAME,
    build_card,
    error_payload,
    gate_call,
    grouped_methods,
    is_known_method,
    method_not_found,
    required_host_flag,
    summarize_status,
)

DEFAULT_TIMEOUT = 30.0
CATALOG_PAGE_SIZE = 256
CATALOG_MAX_PAGES = 64
DEFAULT_RENDER_DIR = Path(tempfile.gettempdir()) / "aviutl2-mcp"


class BridgeConnection:
    """One lazily-connected LiveClient shared by every tool call."""

    def __init__(self) -> None:
        self._client: LiveClient | None = None

    def client(self) -> LiveClient:
        """Connect on first use; reconnect if the pipe went away."""

        if self._client is None:
            self._client = LiveClient.connect()
            self._client.open_session(client_name="aviutl2-mcp")
        return self._client

    def close(self) -> None:
        """Drop the current connection (if any)."""

        if self._client is not None:
            self._client.close()
            self._client = None


_CONNECTION = BridgeConnection()


def _remote_error(error: BridgeRemoteError) -> dict[str, Any]:
    """Convert one structured bridge error into an MCP error payload."""

    return error_payload(
        error.code,
        error.message,
        retryable=error.retryable,
        details=dict(error.details),
    )


def _connection_error(error: Exception) -> dict[str, Any]:
    """Convert one connection failure into an MCP error payload."""

    _CONNECTION.close()
    return error_payload("BRIDGE_UNAVAILABLE", str(error))


def _default_render_name(prefix: str, suffix: str) -> Path:
    """Return a unique default path under the render output directory."""

    DEFAULT_RENDER_DIR.mkdir(parents=True, exist_ok=True)
    stamp = f"{time.time_ns():016x}"
    return DEFAULT_RENDER_DIR / f"{prefix}-{stamp}{suffix}"


def _iter_catalog_pages(kind: str) -> Iterator[list[Any]]:
    """Yield every page of one runtime catalog until exhaustion."""

    client = _CONNECTION.client()
    start = 0
    for _page in range(CATALOG_MAX_PAGES):
        result = client.call(
            kind + ".catalog",
            {"start": start, "count": CATALOG_PAGE_SIZE},
            timeout=DEFAULT_TIMEOUT,
        )
        entries = result.get("entries")
        yield entries if isinstance(entries, list) else []
        next_start = result.get("next_start")
        if not isinstance(next_start, int) or isinstance(next_start, bool):
            return
        start = next_start


def _iter_effect_catalog() -> Iterator[Any]:
    """Yield every effect of the paged effect catalog."""

    client = _CONNECTION.client()
    start = 0
    for _page in range(CATALOG_MAX_PAGES):
        page = client.get_effect_catalog(start=start, count=CATALOG_PAGE_SIZE)
        yield from page.effects
        if page.next_start is None:
            return
        start = page.next_start


def bridge_card() -> str:
    """Return the AviUtl2 Live Bridge usage card; read this first."""

    return build_card()


def bridge_status() -> dict[str, Any]:
    """Report host flags, exposed methods, and current project info."""

    try:
        client = _CONNECTION.client()
        return summarize_status(
            client.hello(),
            client.get_capabilities(),
            client.get_project_info(),
        )
    except BridgeRemoteError as error:
        return _remote_error(error)
    except (ConnectionError, FileNotFoundError, OSError, RuntimeError) as error:
        return _connection_error(error)


def bridge_call(
    method: str,
    params: dict[str, Any] | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Call one Live Bridge method; gated on the running host capabilities.

    Non-undoable methods require confirm_non_undoable=true inside params.
    """

    call_params = params if params is not None else {}
    if not is_known_method(method):
        return method_not_found(method)
    try:
        client = _CONNECTION.client()
        forwarded, failure = gate_call(method, call_params, client.get_capabilities())
        if failure is not None:
            return failure
        timeout = DEFAULT_TIMEOUT
        if method == "event.watch" and timeout_seconds is None:
            requested = call_params.get("timeout_ms")
            if isinstance(requested, int) and not isinstance(requested, bool):
                timeout = max(timeout, requested / 1000.0 + 2.0)
        elif timeout_seconds is not None:
            timeout = timeout_seconds
        result = client.call(method, forwarded, timeout=timeout)
    except BridgeRemoteError as error:
        return _remote_error(error)
    except (
        AmbiguousInstanceError,
        ConnectionError,
        FileNotFoundError,
        OSError,
    ) as error:
        return _connection_error(error)
    return {"ok": True, "result": result}


def bridge_help(subject: str) -> dict[str, Any]:
    """Describe an effect profile, property, API operation, or bridge method."""

    if subject in METHOD_DESCRIPTIONS:
        return {
            "kind": "method",
            "name": subject,
            "description": METHOD_DESCRIPTIONS[subject],
            "required_host_flag": required_host_flag(subject),
        }
    try:
        return dict(LiveProject.describe_property(subject))
    except KeyError:
        pass
    try:
        return dict(LiveProject.describe_api(subject))
    except KeyError:
        pass
    try:
        return dict(describe_effect_profile(subject))
    except ValueError:
        return error_payload(
            "HELP_NOT_FOUND",
            f"no help entry for {subject!r}",
            effect_profiles=list(available_effect_profiles()),
            method_categories=sorted(grouped_methods()),
        )


def bridge_find(
    query: str,
    catalogs: list[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Search effect/font/palette/module catalogs by substring."""

    wanted = query.casefold()
    allowed = set(catalogs) if catalogs else {"effect", "font", "palette", "module"}
    matches: dict[str, list[Any]] = {}
    try:
        if "effect" in allowed:
            hits: list[dict[str, Any]] = []
            for effect in _iter_effect_catalog():
                if wanted in effect.name.casefold():
                    hits.append(
                        {
                            "name": effect.name,
                            "type": effect.type,
                            "type_code": effect.type_code,
                        }
                    )
                if len(hits) >= limit:
                    break
            matches["effect"] = hits
        for kind in ("font", "palette", "module"):
            if kind not in allowed:
                continue
            entries: list[Any] = []
            for page in _iter_catalog_pages(kind):
                for entry in page:
                    name = entry.get("name") if isinstance(entry, dict) else entry
                    if isinstance(name, str) and wanted in name.casefold():
                        entries.append(entry)
                    if len(entries) >= limit:
                        break
                if len(entries) >= limit:
                    break
            matches[kind] = entries
    except BridgeRemoteError as error:
        return _remote_error(error)
    except (ConnectionError, FileNotFoundError, OSError) as error:
        return _connection_error(error)
    return {"ok": True, "query": query, "matches": matches}


def bridge_snapshot(
    layer_start: int | None = None,
    layer_end: int | None = None,
    include_alias: bool = True,
    offset: int = 0,
    count: int = 4096,
) -> dict[str, Any]:
    """Return the revision-scoped object snapshot of the current scene."""

    try:
        client = _CONNECTION.client()
        snapshot = client.get_snapshot(
            offset=offset,
            count=count,
            layer_start=layer_start,
            layer_end=layer_end,
            include_alias=include_alias,
        )
    except BridgeRemoteError as error:
        return _remote_error(error)
    except (ConnectionError, FileNotFoundError, OSError) as error:
        return _connection_error(error)
    return {
        "revision": snapshot.revision,
        "scene_id": snapshot.scene_id,
        "total": snapshot.total,
        "offset": snapshot.offset,
        "next_offset": snapshot.next_offset,
        "objects": [asdict(obj) for obj in snapshot.objects],
    }


def _resolve_target(
    client: LiveClient,
    object_id: str,
    expected_revision: int,
) -> SnapshotObject:
    """Resolve one object reference and enforce the expected revision."""

    snapshot = client.get_snapshot(
        object_ids=[object_id],
        include_alias=False,
    )
    if snapshot.revision != expected_revision:
        raise _StaleRevisionError(
            f"host revision {snapshot.revision} does not match "
            f"expected_revision {expected_revision}"
        )
    for obj in snapshot.objects:
        if obj.object_id == object_id:
            return obj
    raise LookupError(f"object not found in current scene: {object_id!r}")


class _StaleRevisionError(ValueError):
    """The host revision does not match the caller's expected_revision."""


def bridge_render_object(
    object_id: str,
    expected_revision: int,
    frame: int,
    output_path: str | None = None,
    apply_effect: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Render one object to a revision-bound PNG and save it to a file."""

    path = Path(output_path) if output_path else _default_render_name("object", ".png")
    try:
        client = _CONNECTION.client()
        target = _resolve_target(client, object_id, expected_revision)
        rendered = client.render_object_frame(
            target,
            frame=frame,
            apply_effect=apply_effect,
            output_path=path,
            overwrite=overwrite,
            timeout=DEFAULT_TIMEOUT,
        )
    except BridgeRemoteError as error:
        return _remote_error(error)
    except _StaleRevisionError as error:
        return error_payload("STALE_PROJECT_STATE", str(error))
    except LookupError as error:
        return error_payload("OBJECT_NOT_FOUND", str(error))
    except (ConnectionError, FileNotFoundError, OSError) as error:
        return _connection_error(error)
    return {
        "ok": True,
        "png_path": str(path),
        "width": rendered.width,
        "height": rendered.height,
        "frame": rendered.frame,
        "revision": rendered.revision,
        "sha256": rendered.sha256,
    }


def bridge_render_object_audio(
    object_id: str,
    expected_revision: int,
    frame_start: int,
    frame_end: int,
    output_path: str | None = None,
    apply_effect: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Render one object to stereo f32le PCM and save it to a file."""

    path = Path(output_path) if output_path else _default_render_name("object", ".pcm")
    try:
        client = _CONNECTION.client()
        target = _resolve_target(client, object_id, expected_revision)
        rendered = client.render_object_audio(
            target,
            frame_start=frame_start,
            frame_end=frame_end,
            apply_effect=apply_effect,
            output_path=path,
            overwrite=overwrite,
            timeout=120.0,
        )
    except BridgeRemoteError as error:
        return _remote_error(error)
    except _StaleRevisionError as error:
        return error_payload("STALE_PROJECT_STATE", str(error))
    except LookupError as error:
        return error_payload("OBJECT_NOT_FOUND", str(error))
    except (ConnectionError, FileNotFoundError, OSError) as error:
        return _connection_error(error)
    return {
        "ok": True,
        "pcm_path": str(path),
        "sample_rate": rendered.sample_rate,
        "sample_count": rendered.sample_count,
        "duration_seconds": rendered.duration_seconds,
        "sha256": rendered.sha256,
    }


def bridge_watch_events(
    after_sequence: int = 0,
    timeout_ms: int = 30000,
    types: list[str] | None = None,
) -> dict[str, Any]:
    """Long-poll sequenced host events (edit_state_changed and friends)."""

    try:
        client = _CONNECTION.client()
        watched = client.watch_events(
            after_sequence=after_sequence,
            timeout_ms=timeout_ms,
            types=types,
        )
    except BridgeRemoteError as error:
        return _remote_error(error)
    except (ConnectionError, FileNotFoundError, OSError) as error:
        return _connection_error(error)
    return {
        "events": [asdict(event) for event in watched.events],
        "latest_sequence": watched.latest_sequence,
        "resync_required": watched.resync_required,
        "timed_out": watched.timed_out,
    }


TOOL_EXPORTS = (
    bridge_card,
    bridge_status,
    bridge_call,
    bridge_help,
    bridge_find,
    bridge_snapshot,
    bridge_render_object,
    bridge_render_object_audio,
    bridge_watch_events,
)


def build_server() -> Any:
    """Assemble the FastMCP server with every bridge tool registered."""

    from mcp.server.fastmcp import FastMCP

    app = FastMCP(
        SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
    )
    for tool in TOOL_EXPORTS:
        app.add_tool(
            tool,
            description=tool.__doc__,
        )
    return app


def main() -> None:
    """Run the Windows-local stdio MCP server until EOF."""

    app = build_server()
    try:
        app.run(transport="stdio")
    finally:
        _CONNECTION.close()


__all__ = [
    "DEFAULT_RENDER_DIR",
    "DEFAULT_TIMEOUT",
    "TOOL_EXPORTS",
    "BridgeConnection",
    "bridge_call",
    "bridge_card",
    "bridge_find",
    "bridge_help",
    "bridge_render_object",
    "bridge_render_object_audio",
    "bridge_snapshot",
    "bridge_status",
    "bridge_watch_events",
    "build_server",
    "main",
]
