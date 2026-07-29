"""Typed multi-object timeline transactions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .commands import ItemUpdate
from .protocol import ProtocolError
from .snapshot import SnapshotObject


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class ObjectGroup:
    """Explicit A/V/subtitle objects that must move/delete together."""

    objects: tuple[SnapshotObject, ...]

    def __post_init__(self) -> None:
        if not self.objects:
            raise ValueError("an object group must not be empty")
        revisions = {value.revision for value in self.objects}
        identifiers = {value.object_id for value in self.objects}
        if len(revisions) != 1 or len(identifiers) != len(self.objects):
            raise ValueError("group objects must be unique and from one snapshot")

    @property
    def revision(self) -> int:
        return self.objects[0].revision

    @property
    def object_ids(self) -> tuple[str, ...]:
        return tuple(value.object_id for value in self.objects)


@dataclass(frozen=True, slots=True)
class TimelineTransactionCommand:
    value: dict[str, Any]

    @classmethod
    def move(
        cls,
        obj: SnapshotObject,
        *,
        layer: int,
        frame: int,
    ) -> TimelineTransactionCommand:
        return cls(
            {
                "op": "move",
                "target": {"object_id": obj.object_id},
                "layer": layer,
                "frame": frame,
            }
        )

    @classmethod
    def delete(cls, obj: SnapshotObject) -> TimelineTransactionCommand:
        return cls(
            {
                "op": "delete",
                "target": {"object_id": obj.object_id},
            }
        )

    @classmethod
    def set_items(
        cls,
        obj: SnapshotObject,
        updates: tuple[ItemUpdate, ...],
    ) -> TimelineTransactionCommand:
        if not updates:
            raise ValueError("updates must not be empty")
        return cls(
            {
                "op": "set_items",
                "target": {"object_id": obj.object_id},
                "items": [value.to_wire() for value in updates],
            }
        )

    @classmethod
    def set_name(
        cls,
        obj: SnapshotObject,
        name: str | None,
    ) -> TimelineTransactionCommand:
        return cls(
            {
                "op": "set_name",
                "target": {"object_id": obj.object_id},
                "name": name,
            }
        )

    @classmethod
    def set_effect_enabled(
        cls,
        obj: SnapshotObject,
        selector: str,
        enabled: bool,
    ) -> TimelineTransactionCommand:
        return cls(
            {
                "op": "effect.set_enabled",
                "target": {"object_id": obj.object_id},
                "selector": selector,
                "enabled": enabled,
            }
        )


@dataclass(frozen=True, slots=True)
class TransactionReceipt:
    valid: bool
    applied_count: int
    revision: int | None
    undo_grouped: bool
    snapshot_required: bool
    warnings: tuple[str, ...]

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> TransactionReceipt:
        revision = result.get("revision")
        warnings = result.get("warnings", [])
        if (
            not isinstance(result.get("valid"), bool)
            or not _integer(result.get("applied_count"))
            or (revision is not None and not _integer(revision))
            or not isinstance(result.get("undo_grouped"), bool)
            or not isinstance(result.get("snapshot_required"), bool)
            or not isinstance(warnings, list)
            or any(not isinstance(value, str) for value in warnings)
        ):
            raise ProtocolError("Live Bridge returned an invalid transaction receipt")
        applied_count = result["applied_count"]
        assert isinstance(applied_count, int)
        assert revision is None or isinstance(revision, int)
        return cls(
            valid=result["valid"],
            applied_count=applied_count,
            revision=revision,
            undo_grouped=result["undo_grouped"],
            snapshot_required=result["snapshot_required"],
            warnings=tuple(warnings),
        )


__all__ = [
    "ObjectGroup",
    "TimelineTransactionCommand",
    "TransactionReceipt",
]
