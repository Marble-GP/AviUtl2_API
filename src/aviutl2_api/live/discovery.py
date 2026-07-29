"""Discovery of locally running AviUtl2 Live Bridge instances."""

from __future__ import annotations

import ctypes
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class InstanceInfo:
    """Validated instance metadata published by an AviUtl2 plugin."""

    pid: int
    pipe: str
    protocol_version: int
    plugin_version: str
    sdk_baseline: str
    project_path: str | None
    scene_id: int
    started_at: str


class AmbiguousInstanceError(RuntimeError):
    """Raised when discovery finds multiple instances without a selection."""

    def __init__(self, instances: list[InstanceInfo]) -> None:
        pids = ", ".join(str(instance.pid) for instance in instances)
        super().__init__(f"multiple AviUtl2 Live Bridge instances found: {pids}")
        self.instances = instances


def default_instance_directory() -> Path:
    """Return the per-user instance directory used by the plugin."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise OSError("LOCALAPPDATA is not defined")
    return Path(local_app_data) / "AviUtl2LiveBridge" / "instances"


def discover_instances(
    directory: Path | None = None,
    *,
    pid_is_alive: Callable[[int], bool] | None = None,
) -> list[InstanceInfo]:
    """Return valid live entries, ignoring malformed and stale files."""
    instance_directory = directory or default_instance_directory()
    if not instance_directory.is_dir():
        return []
    alive = pid_is_alive or _pid_is_alive
    instances: list[InstanceInfo] = []
    for path in sorted(instance_directory.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            instance = _parse_instance(document)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
            continue
        if path.stem != str(instance.pid) or not alive(instance.pid):
            continue
        instances.append(instance)
    return sorted(instances, key=lambda item: (item.started_at, item.pid))


def _parse_instance(document: Any) -> InstanceInfo:
    if not isinstance(document, dict):
        raise ValueError("instance document must be an object")
    pid = document.get("pid")
    pipe = document.get("pipe")
    protocol_version = document.get("protocol_version")
    plugin_version = document.get("plugin_version")
    sdk_baseline = document.get("sdk_baseline")
    project_path = document.get("project_path")
    scene_id = document.get("scene_id")
    started_at = document.get("started_at")
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or not isinstance(pipe, str)
        or pipe != rf"\\.\pipe\AviUtl2.LiveBridge.{pid}"
        or not isinstance(protocol_version, int)
        or isinstance(protocol_version, bool)
        or not isinstance(plugin_version, str)
        or not isinstance(sdk_baseline, str)
        or (project_path is not None and not isinstance(project_path, str))
        or not isinstance(scene_id, int)
        or isinstance(scene_id, bool)
        or not isinstance(started_at, str)
    ):
        raise ValueError("instance document contains invalid fields")
    return InstanceInfo(
        pid=pid,
        pipe=pipe,
        protocol_version=protocol_version,
        plugin_version=plugin_version,
        sdk_baseline=sdk_baseline,
        project_path=project_path,
        scene_id=scene_id,
        started_at=started_at,
    )


def _pid_is_alive(pid: int) -> bool:
    if sys.platform == "win32":
        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        open_process.restype = ctypes.c_void_p
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        handle = open_process(process_query_limited_information, 0, pid)
        if not handle:
            return False
        close_handle(handle)
        return True
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True
