"""Typed results for AviUtl2-native media operations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .protocol import ProtocolError
from .snapshot import SnapshotObject


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class MediaProbe:
    """AviUtl2's own support check and decoded media metadata."""

    exists: bool
    regular_file: bool
    extension_supported: bool
    readable: bool
    has_media_info: bool
    kind: str
    video_track_count: int
    audio_track_count: int
    duration_seconds: float
    width: int
    height: int

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> MediaProbe:
        boolean_fields = (
            "exists",
            "regular_file",
            "extension_supported",
            "readable",
            "has_media_info",
        )
        integer_fields = (
            "video_track_count",
            "audio_track_count",
            "width",
            "height",
        )
        if (
            any(not isinstance(result.get(name), bool) for name in boolean_fields)
            or any(not _integer(result.get(name)) for name in integer_fields)
            or not isinstance(result.get("kind"), str)
            or not isinstance(result.get("duration_seconds"), (int, float))
            or isinstance(result.get("duration_seconds"), bool)
        ):
            raise ProtocolError("Live Bridge returned invalid media information")
        return cls(
            exists=result["exists"],
            regular_file=result["regular_file"],
            extension_supported=result["extension_supported"],
            readable=result["readable"],
            has_media_info=result["has_media_info"],
            kind=result["kind"],
            video_track_count=result["video_track_count"],
            audio_track_count=result["audio_track_count"],
            duration_seconds=float(result["duration_seconds"]),
            width=result["width"],
            height=result["height"],
        )


@dataclass(frozen=True, slots=True)
class CreatedMediaObject:
    """Actual timeline range chosen by AviUtl2 for a media object."""

    layer: int
    frame_start: int
    frame_end: int
    snapshot_required: bool
    revision: int | None = None
    objects: tuple[SnapshotObject, ...] = ()
    undo_grouped: bool = True
    warnings: tuple[str, ...] = ()

    @property
    def duration_frames(self) -> int:
        return self.frame_end - self.frame_start + 1

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> CreatedMediaObject:
        created = result.get("created")
        if not isinstance(created, dict):
            raise ProtocolError("Live Bridge returned an invalid created object")
        layer = created.get("layer")
        frame_start = created.get("frame_start")
        frame_end = created.get("frame_end")
        snapshot_required = result.get("snapshot_required")
        revision = result.get("revision")
        created_objects = result.get("created_objects", [])
        undo_grouped = result.get("undo_grouped", True)
        warnings = result.get("warnings", [])
        if (
            not _integer(layer)
            or not _integer(frame_start)
            or not _integer(frame_end)
            or not isinstance(snapshot_required, bool)
            or (revision is not None and not _integer(revision))
            or not isinstance(created_objects, list)
            or not isinstance(undo_grouped, bool)
            or not isinstance(warnings, list)
            or any(not isinstance(value, str) for value in warnings)
        ):
            raise ProtocolError("Live Bridge returned an invalid created object")
        assert isinstance(layer, int)
        assert isinstance(frame_start, int)
        assert isinstance(frame_end, int)
        assert revision is None or isinstance(revision, int)
        if layer < 0 or frame_start < 0 or frame_end < frame_start:
            raise ProtocolError("Live Bridge returned an invalid created object")
        objects: list[SnapshotObject] = []
        if created_objects:
            if revision is None or revision <= 0:
                raise ProtocolError("Live Bridge omitted the created-object revision")
            for value in created_objects:
                if not isinstance(value, dict):
                    raise ProtocolError(
                        "Live Bridge returned an invalid created object group"
                    )
                object_id = value.get("object_id")
                object_layer = value.get("layer")
                object_start = value.get("frame_start")
                object_end = value.get("frame_end")
                name = value.get("name")
                api_locked = value.get("api_locked")
                if (
                    not isinstance(object_id, str)
                    or not object_id.startswith(f"obj-{revision}-")
                    or not _integer(object_layer)
                    or not _integer(object_start)
                    or not _integer(object_end)
                    or (name is not None and not isinstance(name, str))
                    or not isinstance(api_locked, bool)
                ):
                    raise ProtocolError(
                        "Live Bridge returned an invalid created object group"
                    )
                assert isinstance(object_layer, int)
                assert isinstance(object_start, int)
                assert isinstance(object_end, int)
                if object_end < object_start:
                    raise ProtocolError(
                        "Live Bridge returned an invalid created object group"
                    )
                objects.append(
                    SnapshotObject(
                        object_id=object_id,
                        revision=revision,
                        layer=object_layer,
                        frame_start=object_start,
                        frame_end=object_end,
                        name=name,
                        alias=None,
                        api_locked=api_locked,
                    )
                )
        return cls(
            layer,
            frame_start,
            frame_end,
            snapshot_required,
            revision,
            tuple(objects),
            undo_grouped,
            tuple(warnings),
        )


@dataclass(frozen=True, slots=True)
class MediaSplitRange:
    layer: int
    frame_start: int
    frame_end: int

    @classmethod
    def from_wire(cls, value: Any) -> MediaSplitRange:
        if not isinstance(value, dict):
            raise ProtocolError("Live Bridge returned an invalid split range")
        layer = value.get("layer")
        frame_start = value.get("frame_start")
        frame_end = value.get("frame_end")
        if not _integer(layer) or not _integer(frame_start) or not _integer(frame_end):
            raise ProtocolError("Live Bridge returned an invalid split range")
        assert isinstance(layer, int)
        assert isinstance(frame_start, int)
        assert isinstance(frame_end, int)
        if layer < 0 or frame_start < 0 or frame_end < frame_start:
            raise ProtocolError("Live Bridge returned an invalid split range")
        return cls(layer, frame_start, frame_end)


@dataclass(frozen=True, slots=True)
class MediaSplit:
    left: MediaSplitRange
    right: MediaSplitRange
    source_position_left: float
    source_position_right: float
    playback_rate: float
    revision: int | None
    snapshot_required: bool

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> MediaSplit:
        left = MediaSplitRange.from_wire(result.get("left"))
        right = MediaSplitRange.from_wire(result.get("right"))
        source_position = result.get("source_position")
        playback_rate = result.get("playback_rate")
        revision = result.get("revision")
        snapshot_required = result.get("snapshot_required")
        if (
            not isinstance(source_position, dict)
            or not isinstance(source_position.get("left"), (int, float))
            or isinstance(source_position.get("left"), bool)
            or not isinstance(source_position.get("right"), (int, float))
            or isinstance(source_position.get("right"), bool)
            or not isinstance(playback_rate, (int, float))
            or isinstance(playback_rate, bool)
            or (revision is not None and (not _integer(revision) or revision <= 0))
            or not isinstance(snapshot_required, bool)
        ):
            raise ProtocolError("Live Bridge returned an invalid media split")
        source_left = float(source_position["left"])
        source_right = float(source_position["right"])
        rate = float(playback_rate)
        if (
            not math.isfinite(source_left)
            or not math.isfinite(source_right)
            or not math.isfinite(rate)
            or rate <= 0
            or left.layer != right.layer
            or left.frame_end + 1 != right.frame_start
        ):
            raise ProtocolError("Live Bridge returned an invalid media split")
        return cls(
            left=left,
            right=right,
            source_position_left=source_left,
            source_position_right=source_right,
            playback_rate=rate,
            revision=revision,
            snapshot_required=snapshot_required,
        )


@dataclass(frozen=True, slots=True)
class MediaInventoryItem:
    object_id: str
    effect: str
    item: str
    file: str
    exists: bool
    regular_file: bool
    readable: bool
    duplicate_count: int
    api_locked: bool
    probe_error: str | None

    @classmethod
    def from_wire(cls, value: object) -> MediaInventoryItem:
        if not isinstance(value, dict):
            raise ProtocolError("Live Bridge returned an invalid media item")
        string_fields = ("object_id", "effect", "item", "file")
        bool_fields = (
            "exists",
            "regular_file",
            "readable",
            "api_locked",
        )
        probe_error = value.get("probe_error")
        duplicate_count = value.get("duplicate_count")
        if (
            any(not isinstance(value.get(name), str) for name in string_fields)
            or any(not isinstance(value.get(name), bool) for name in bool_fields)
            or not _integer(duplicate_count)
            or (probe_error is not None and not isinstance(probe_error, str))
        ):
            raise ProtocolError("Live Bridge returned an invalid media item")
        assert isinstance(duplicate_count, int)
        if duplicate_count < 1:
            raise ProtocolError("Live Bridge returned an invalid media item")
        return cls(
            object_id=value["object_id"],
            effect=value["effect"],
            item=value["item"],
            file=value["file"],
            exists=value["exists"],
            regular_file=value["regular_file"],
            readable=value["readable"],
            duplicate_count=duplicate_count,
            api_locked=value["api_locked"],
            probe_error=probe_error,
        )


@dataclass(frozen=True, slots=True)
class MediaInventory:
    revision: int
    scene_id: int
    files: tuple[MediaInventoryItem, ...]
    unique_file_count: int
    missing_count: int
    unreadable_count: int

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> MediaInventory:
        files = result.get("files")
        revision = result.get("revision")
        scene_id = result.get("scene_id")
        unique_count = result.get("unique_file_count")
        missing_count = result.get("missing_count")
        unreadable_count = result.get("unreadable_count")
        file_item_count = result.get("file_item_count")
        if (
            not isinstance(files, list)
            or not _integer(revision)
            or not _integer(scene_id)
            or not _integer(unique_count)
            or not _integer(missing_count)
            or not _integer(unreadable_count)
            or not _integer(file_item_count)
            or file_item_count != len(files)
        ):
            raise ProtocolError("Live Bridge returned an invalid media inventory")
        assert isinstance(revision, int)
        assert isinstance(scene_id, int)
        assert isinstance(unique_count, int)
        assert isinstance(missing_count, int)
        assert isinstance(unreadable_count, int)
        if revision <= 0:
            raise ProtocolError("Live Bridge returned an invalid media inventory")
        parsed = tuple(MediaInventoryItem.from_wire(value) for value in files)
        return cls(
            revision=revision,
            scene_id=scene_id,
            files=parsed,
            unique_file_count=unique_count,
            missing_count=missing_count,
            unreadable_count=unreadable_count,
        )


@dataclass(frozen=True, slots=True)
class MediaRelinkReceipt:
    affected_objects: int
    matched_items: int
    revision: int | None
    undo_grouped: bool
    snapshot_required: bool
    warnings: tuple[str, ...]

    @classmethod
    def from_wire(cls, result: dict[str, Any]) -> MediaRelinkReceipt:
        affected = result.get("affected_objects")
        matched = result.get("matched_items")
        revision = result.get("revision")
        undo_grouped = result.get("undo_grouped")
        snapshot_required = result.get("snapshot_required", False)
        warnings = result.get("warnings", [])
        if (
            not _integer(affected)
            or not _integer(matched)
            or (revision is not None and not _integer(revision))
            or not isinstance(undo_grouped, bool)
            or not isinstance(snapshot_required, bool)
            or not isinstance(warnings, list)
            or any(not isinstance(value, str) for value in warnings)
        ):
            raise ProtocolError("Live Bridge returned an invalid media relink receipt")
        assert isinstance(affected, int)
        assert isinstance(matched, int)
        assert revision is None or isinstance(revision, int)
        return cls(
            affected_objects=affected,
            matched_items=matched,
            revision=revision,
            undo_grouped=undo_grouped,
            snapshot_required=snapshot_required,
            warnings=tuple(warnings),
        )


__all__ = [
    "CreatedMediaObject",
    "MediaInventory",
    "MediaInventoryItem",
    "MediaProbe",
    "MediaRelinkReceipt",
    "MediaSplit",
    "MediaSplitRange",
]
