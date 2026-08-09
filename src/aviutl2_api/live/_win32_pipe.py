"""Small ctypes wrapper for timeout-aware overlapped named-pipe I/O."""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from threading import Lock
from typing import Any

if sys.platform != "win32":
    raise ImportError("Windows named-pipe transport is available only on Windows")

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_FILE_FLAG_OVERLAPPED = 0x40000000
_ERROR_FILE_NOT_FOUND = 2
_ERROR_BROKEN_PIPE = 109
_ERROR_PIPE_BUSY = 231
_ERROR_NO_DATA = 232
_ERROR_PIPE_NOT_CONNECTED = 233
_ERROR_IO_PENDING = 997
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_INFINITE = 0xFFFFFFFF
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_ULONG_PTR = (
    ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
)


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", _ULONG_PTR),
        ("InternalHigh", _ULONG_PTR),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


_kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)

_CreateFileW = _kernel32.CreateFileW
_CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
_CreateFileW.restype = wintypes.HANDLE

_WaitNamedPipeW = _kernel32.WaitNamedPipeW
_WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
_WaitNamedPipeW.restype = wintypes.BOOL

_CreateEventW = _kernel32.CreateEventW
_CreateEventW.argtypes = [
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.BOOL,
    wintypes.LPCWSTR,
]
_CreateEventW.restype = wintypes.HANDLE

_ReadFile = _kernel32.ReadFile
_ReadFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(_Overlapped),
]
_ReadFile.restype = wintypes.BOOL

_WriteFile = _kernel32.WriteFile
_WriteFile.argtypes = _ReadFile.argtypes
_WriteFile.restype = wintypes.BOOL

_WaitForSingleObject = _kernel32.WaitForSingleObject
_WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_WaitForSingleObject.restype = wintypes.DWORD

_GetOverlappedResult = _kernel32.GetOverlappedResult
_GetOverlappedResult.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_Overlapped),
    ctypes.POINTER(wintypes.DWORD),
    wintypes.BOOL,
]
_GetOverlappedResult.restype = wintypes.BOOL

_CancelIoEx = _kernel32.CancelIoEx
_CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped)]
_CancelIoEx.restype = wintypes.BOOL

_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = [wintypes.HANDLE]
_CloseHandle.restype = wintypes.BOOL


class Win32NamedPipeStream:
    """A byte stream backed by one overlapped Windows named-pipe handle."""

    def __init__(self, handle: int) -> None:
        self._handle = handle
        self._closed = False
        self._lock = Lock()

    @classmethod
    def connect(cls, pipe_name: str, timeout: float) -> Win32NamedPipeStream:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if not pipe_name.startswith(r"\\.\pipe\AviUtl2.LiveBridge."):
            raise ValueError("pipe_name is not an AviUtl2 Live Bridge pipe")
        deadline = time.monotonic() + timeout
        while True:
            handle = _CreateFileW(
                pipe_name,
                _GENERIC_READ | _GENERIC_WRITE,
                0,
                None,
                _OPEN_EXISTING,
                _FILE_FLAG_OVERLAPPED,
                None,
            )
            handle_value = ctypes.cast(handle, ctypes.c_void_p).value
            if handle_value != _INVALID_HANDLE_VALUE:
                if handle_value is None:
                    raise OSError("CreateFileW returned a null handle")
                return cls(handle_value)

            error = ctypes.get_last_error()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timed out connecting to {pipe_name}")
            if error not in {_ERROR_PIPE_BUSY, _ERROR_FILE_NOT_FOUND}:
                raise ctypes.WinError(error)
            wait_ms = max(1, min(int(remaining * 1000), 100))
            _WaitNamedPipeW(pipe_name, wait_ms)

    def read(self, size: int, timeout: float) -> bytes:
        if size <= 0:
            return b""
        with self._lock:
            self._ensure_open()
            buffer = ctypes.create_string_buffer(size)
            transferred = self._operate(
                _ReadFile,
                ctypes.cast(buffer, ctypes.c_void_p),
                size,
                timeout,
            )
            return bytes(buffer.raw[:transferred])

    def write(self, data: bytes, timeout: float) -> int:
        if not data:
            return 0
        with self._lock:
            self._ensure_open()
            buffer = ctypes.create_string_buffer(data, len(data))
            return self._operate(
                _WriteFile,
                ctypes.cast(buffer, ctypes.c_void_p),
                len(data),
                timeout,
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            _CloseHandle(wintypes.HANDLE(self._handle))
            self._closed = True

    def _operate(
        self,
        function: Any,
        buffer: ctypes.c_void_p,
        size: int,
        timeout: float,
    ) -> int:
        if timeout <= 0:
            raise TimeoutError("named-pipe I/O timed out")
        event = _CreateEventW(None, True, False, None)
        event_value = ctypes.cast(event, ctypes.c_void_p).value
        if not event_value:
            raise ctypes.WinError(ctypes.get_last_error())
        overlapped = _Overlapped()
        overlapped.hEvent = event
        transferred = wintypes.DWORD()
        try:
            completed = function(
                wintypes.HANDLE(self._handle),
                buffer,
                size,
                ctypes.byref(transferred),
                ctypes.byref(overlapped),
            )
            if not completed:
                error = ctypes.get_last_error()
                if error != _ERROR_IO_PENDING:
                    self._raise_io_error(error)
                timeout_ms = max(1, min(int(timeout * 1000), 0xFFFFFFFE))
                wait_result = _WaitForSingleObject(event, timeout_ms)
                if wait_result == _WAIT_TIMEOUT:
                    _CancelIoEx(
                        wintypes.HANDLE(self._handle),
                        ctypes.byref(overlapped),
                    )
                    _WaitForSingleObject(event, _INFINITE)
                    raise TimeoutError("named-pipe I/O timed out")
                if wait_result != _WAIT_OBJECT_0:
                    raise OSError(f"WaitForSingleObject failed: {wait_result}")
                if not _GetOverlappedResult(
                    wintypes.HANDLE(self._handle),
                    ctypes.byref(overlapped),
                    ctypes.byref(transferred),
                    False,
                ):
                    self._raise_io_error(ctypes.get_last_error())
            return int(transferred.value)
        finally:
            _CloseHandle(event)

    def _ensure_open(self) -> None:
        if self._closed:
            raise ConnectionError("named pipe is closed")

    @staticmethod
    def _raise_io_error(error: int) -> None:
        if error in {
            _ERROR_BROKEN_PIPE,
            _ERROR_NO_DATA,
            _ERROR_PIPE_NOT_CONNECTED,
        }:
            raise BrokenPipeError(error, "named pipe disconnected")
        raise ctypes.WinError(error)
