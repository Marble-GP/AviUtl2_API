"""Opt-in cross-language transport test using the native test server."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SERVER_ENV = "AVIUTL2_NATIVE_TEST_SERVER"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named pipe test")
def test_python_transport_with_native_server() -> None:
    from aviutl2_api.live._win32_pipe import Win32NamedPipeStream
    from aviutl2_api.live.transport import FramedTransport

    executable_value = os.environ.get(SERVER_ENV)
    if not executable_value:
        pytest.skip(f"{SERVER_ENV} is not configured")
    executable = Path(executable_value)
    if not executable.is_file():
        pytest.fail(f"native test server does not exist: {executable}")

    pipe_name = rf"\\.\pipe\AviUtl2.LiveBridge.PythonTest.{os.getpid()}"
    process = subprocess.Popen(
        [str(executable), "--echo-pipe", pipe_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "READY"
        stream = Win32NamedPipeStream.connect(pipe_name, 3.0)
        with FramedTransport(stream) as transport:
            payload = '{"cross_language":"日本語"}'.encode()
            assert transport.exchange(payload, timeout=3.0) == payload
        assert process.wait(timeout=5.0) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5.0)
