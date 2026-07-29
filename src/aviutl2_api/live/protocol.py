"""Wire protocol primitives for AviUtl2 Live Bridge."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
MAX_PAYLOAD_BYTES = 1024 * 1024


class ProtocolError(ValueError):
    """Raised when a peer sends an invalid protocol frame or envelope."""


class BridgeRemoteError(RuntimeError):
    """A structured error returned by the AviUtl2 plugin."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
        request_id: str = "",
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details or {}
        self.retryable = retryable
        self.request_id = request_id


@dataclass(frozen=True, slots=True)
class Response:
    """Validated response envelope."""

    request_id: str
    result: dict[str, Any]


def encode_request(
    request_id: str,
    method: str,
    params: dict[str, Any] | None = None,
) -> bytes:
    """Serialize a request envelope as compact UTF-8 JSON."""
    if not request_id or len(request_id.encode("utf-8")) > 128:
        raise ValueError("request_id must be a non-empty UTF-8 string up to 128 bytes")
    if not method or len(method.encode("utf-8")) > 128:
        raise ValueError("method must be a non-empty UTF-8 string up to 128 bytes")
    document = {
        "id": request_id,
        "protocol_version": PROTOCOL_VERSION,
        "method": method,
        "params": params or {},
    }
    payload = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    validate_payload_size(payload)
    return payload


def decode_response(payload: bytes) -> Response:
    """Decode and validate a response, raising structured remote errors."""
    validate_payload_size(payload)
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as decode_error:
        raise ProtocolError("response is not valid UTF-8 JSON") from decode_error
    if not isinstance(document, dict):
        raise ProtocolError("response root must be an object")

    request_id = document.get("id")
    ok = document.get("ok")
    if not isinstance(request_id, str) or not isinstance(ok, bool):
        raise ProtocolError("response envelope is missing id or ok")
    if ok:
        result = document.get("result")
        if not isinstance(result, dict):
            raise ProtocolError("successful response result must be an object")
        return Response(request_id=request_id, result=result)

    error_object = document.get("error")
    if not isinstance(error_object, dict):
        raise ProtocolError("error response is missing the error object")
    code = error_object.get("code")
    message = error_object.get("message")
    details = error_object.get("details", {})
    retryable = error_object.get("retryable", False)
    if (
        not isinstance(code, str)
        or not isinstance(message, str)
        or not isinstance(details, dict)
        or not isinstance(retryable, bool)
    ):
        raise ProtocolError("error response contains invalid fields")
    raise BridgeRemoteError(
        code,
        message,
        details=details,
        retryable=retryable,
        request_id=request_id,
    )


def encode_frame(payload: bytes) -> bytes:
    """Prefix a payload with its 32-bit little-endian byte length."""
    validate_payload_size(payload)
    return struct.pack("<I", len(payload)) + payload


def decode_frame_header(header: bytes) -> int:
    """Validate and decode a four-byte frame header."""
    if len(header) != 4:
        raise ProtocolError("frame header must contain exactly four bytes")
    length = int(struct.unpack("<I", header)[0])
    if length == 0 or length > MAX_PAYLOAD_BYTES:
        raise ProtocolError("frame payload length is outside the allowed range")
    return length


def validate_payload_size(payload: bytes) -> None:
    """Reject empty and oversized payloads."""
    if not payload:
        raise ProtocolError("payload must not be empty")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ProtocolError("payload exceeds the 1 MiB limit")
