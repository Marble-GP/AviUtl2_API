"""Connection sessions and sequenced AviUtl2 host events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocol import ProtocolError


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class SessionInfo:
    session_id: str
    connection_id: int
    client_name: str
    max_cached_operations: int

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> SessionInfo:
        if (
            not isinstance(result.get("session_id"), str)
            or not _integer(result.get("connection_id"))
            or not isinstance(result.get("client_name"), str)
            or not _integer(result.get("max_cached_operations"))
        ):
            raise ProtocolError("Live Bridge returned an invalid session")
        return cls(
            session_id=result["session_id"],
            connection_id=result["connection_id"],
            client_name=result["client_name"],
            max_cached_operations=result["max_cached_operations"],
        )


@dataclass(frozen=True, slots=True)
class BridgeEvent:
    sequence: int
    timestamp_ms: int
    type: str

    @classmethod
    def from_wire(cls, value: object) -> BridgeEvent:
        if (
            not isinstance(value, dict)
            or not _integer(value.get("sequence"))
            or not _integer(value.get("timestamp_ms"))
            or not isinstance(value.get("type"), str)
        ):
            raise ProtocolError("Live Bridge returned an invalid event")
        return cls(
            sequence=value["sequence"],
            timestamp_ms=value["timestamp_ms"],
            type=value["type"],
        )


@dataclass(frozen=True, slots=True)
class EventWatchResult:
    events: tuple[BridgeEvent, ...]
    latest_sequence: int
    resync_required: bool
    timed_out: bool

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> EventWatchResult:
        events = result.get("events")
        if (
            not isinstance(events, list)
            or not _integer(result.get("latest_sequence"))
            or not isinstance(result.get("resync_required"), bool)
            or not isinstance(result.get("timed_out"), bool)
        ):
            raise ProtocolError("Live Bridge returned an invalid event watch result")
        parsed = tuple(BridgeEvent.from_wire(value) for value in events)
        if any(
            left.sequence >= right.sequence for left, right in zip(parsed, parsed[1:])
        ):
            raise ProtocolError("Live Bridge returned out-of-order events")
        return cls(
            events=parsed,
            latest_sequence=result["latest_sequence"],
            resync_required=result["resync_required"],
            timed_out=result["timed_out"],
        )


__all__ = ["BridgeEvent", "EventWatchResult", "SessionInfo"]
