"""Instance discovery tests."""

from __future__ import annotations

import json
from pathlib import Path

from aviutl2_api.live.discovery import discover_instances


def write_instance(directory: Path, pid: int, **changes: object) -> None:
    document: dict[str, object] = {
        "pid": pid,
        "pipe": rf"\\.\pipe\AviUtl2.LiveBridge.{pid}",
        "protocol_version": 1,
        "plugin_version": "0.4.1",
        "sdk_baseline": "mirror-2026-07-25",
        "project_path": None,
        "scene_id": 0,
        "started_at": "2026-07-25T00:00:00.000Z",
    }
    document.update(changes)
    (directory / f"{pid}.json").write_text(
        json.dumps(document),
        encoding="utf-8",
    )


def test_discovery_ignores_stale_and_malformed_entries(tmp_path: Path) -> None:
    write_instance(tmp_path, 100)
    write_instance(tmp_path, 200)
    (tmp_path / "300.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "not-a-pid.json").write_text("{}", encoding="utf-8")

    instances = discover_instances(
        tmp_path,
        pid_is_alive=lambda pid: pid == 100,
    )
    assert [instance.pid for instance in instances] == [100]
    assert instances[0].project_path is None


def test_discovery_rejects_spoofed_pipe_name(tmp_path: Path) -> None:
    write_instance(tmp_path, 100, pipe=r"\\.\pipe\Other")
    assert discover_instances(tmp_path, pid_is_alive=lambda _pid: True) == []


def test_discovery_rejects_filename_pid_mismatch(tmp_path: Path) -> None:
    write_instance(tmp_path, 100)
    (tmp_path / "100.json").rename(tmp_path / "101.json")
    assert discover_instances(tmp_path, pid_is_alive=lambda _pid: True) == []
