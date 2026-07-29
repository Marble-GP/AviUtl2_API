"""Synchronous framed transport for the Live Bridge named pipe."""

from __future__ import annotations

import sys
import time
from threading import Lock
from typing import Protocol

from .protocol import decode_frame_header, encode_frame


class BinaryStream(Protocol):
    """Minimal timeout-aware stream used by the framed transport."""

    def read(self, size: int, timeout: float) -> bytes:
        """Read up to size bytes."""

    def write(self, data: bytes, timeout: float) -> int:
        """Write bytes and return the number written."""

    def close(self) -> None:
        """Close and cancel pending I/O."""


class TransportClosedError(ConnectionError):
    """Raised when an operation is attempted after closing the transport."""


class FramedTransport:
    """Serial request/response transport with a shared timeout budget."""

    def __init__(self, stream: BinaryStream) -> None:
        self._stream = stream
        self._lock = Lock()
        self._closed = False

    def exchange(self, payload: bytes, *, timeout: float = 5.0) -> bytes:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        frame = encode_frame(payload)
        deadline = time.monotonic() + timeout
        with self._lock:
            if self._closed:
                raise TransportClosedError("transport is closed")
            self._write_all(frame, deadline)
            header = self._read_exact(4, deadline)
            length = decode_frame_header(header)
            return self._read_exact(length, deadline)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._stream.close()

    def __enter__(self) -> FramedTransport:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Live Bridge request timed out")
        return remaining

    def _write_all(self, data: bytes, deadline: float) -> None:
        offset = 0
        while offset < len(data):
            written = self._stream.write(
                data[offset:],
                self._remaining(deadline),
            )
            if written <= 0:
                raise ConnectionError("named pipe closed while writing")
            offset += written

    def _read_exact(self, size: int, deadline: float) -> bytes:
        chunks: list[bytes] = []
        remaining_bytes = size
        while remaining_bytes:
            chunk = self._stream.read(
                remaining_bytes,
                self._remaining(deadline),
            )
            if not chunk:
                raise ConnectionError("named pipe closed while reading")
            chunks.append(chunk)
            remaining_bytes -= len(chunk)
        return b"".join(chunks)


def connect_named_pipe(pipe_name: str, *, timeout: float = 5.0) -> FramedTransport:
    """Connect to a Windows Live Bridge pipe."""
    if sys.platform != "win32":
        raise OSError("AviUtl2 Live Bridge is supported on Windows only")
    from ._win32_pipe import Win32NamedPipeStream

    return FramedTransport(Win32NamedPipeStream.connect(pipe_name, timeout))
