"""Safe, stateful editing of local AviUtl2 project files."""

from __future__ import annotations

import copy
import hashlib
import math
import os
import shutil
import wave
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast, overload
from uuid import uuid4

from PIL import Image

from aviutl2_api.aup2_document import Aup2Document
from aviutl2_api.aup2_effects import apply_effects, validate_standard_effects
from aviutl2_api.editing import (
    AppliedEffect,
    EditPlan,
    EffectDefinition,
    EffectSpec,
    FramePosition,
    MediaFit,
    NativeEffectSpec,
    ObjectGroup,
    PlacementConflictError,
    PlanCommandResult,
    PlannedPlacement,
    PlanResult,
    PlanValidation,
    PlanValidationError,
    ProjectChangedError,
    RollbackReceipt,
    Transform,
    TransformValue,
    ValidationIssue,
)
from aviutl2_api.editing_resolver import (
    TimelineRange,
)
from aviutl2_api.editing_resolver import (
    conflicts as _range_conflicts,
)
from aviutl2_api.editing_resolver import (
    draw_properties as _draw_properties,
)
from aviutl2_api.editing_resolver import (
    image_exif_orientation as _image_exif_orientation,
)
from aviutl2_api.editing_resolver import (
    offset_transform_value as _offset_transform_value,
)
from aviutl2_api.editing_resolver import (
    resolve_frame as _resolve_frame,
)
from aviutl2_api.editing_resolver import (
    shape_object as _shape_object,
)
from aviutl2_api.editing_resolver import (
    suggested_layer as _suggested_timeline_layer,
)
from aviutl2_api.editing_resolver import (
    text_object as make_text_object,
)
from aviutl2_api.editing_resolver import (
    track_value as _track_value,
)
from aviutl2_api.effect_profiles import (
    AUP2_EFFECT_COMPATIBLE_PROJECT_VERSIONS,
    available_effect_profiles,
    get_effect_profile,
)
from aviutl2_api.live.alias import serialize_object_alias
from aviutl2_api.models import Effect, Project, Scene, StaticValue, TimelineObject
from aviutl2_api.parser import Aup2ParseError, parse_string


class LocalFileChangedError(RuntimeError):
    """The file changed since it was loaded or inspected."""

    def __init__(
        self,
        message: str,
        *,
        path: Path | None = None,
        expected_sha256: str | None = None,
        observed_sha256: str | None = None,
        code: str = "LOCAL_FILE_CHANGED",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path
        self.expected_sha256 = expected_sha256
        self.observed_sha256 = observed_sha256
        self.details: Mapping[str, object] = {
            "path": str(path) if path is not None else None,
            "expected_sha256": expected_sha256,
            "observed_sha256": observed_sha256,
        }
        self.retryable = True
        self.required_action = "reload_or_choose_another_path"


class LocalOverwriteRequiredError(PermissionError):
    """Source replacement was requested without an explicit overwrite grant."""

    code = "LOCAL_OVERWRITE_REQUIRED"
    retryable = False
    required_action = "obtain_user_authorization_and_pass_overwrite_true"

    def __init__(self, path: Path | None) -> None:
        super().__init__("save_source requires explicit overwrite=True")
        self.path = path
        self.details: Mapping[str, object] = {
            "path": str(path) if path is not None else None
        }


class LocalProjectFormatError(ValueError):
    """The project cannot be edited without guessing about its structure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "LOCAL_PROJECT_FORMAT_ERROR",
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})
        self.retryable = False
        self.required_action = "use_a_verified_project_format"


class LocalCapabilityUnavailableError(RuntimeError):
    """The local backend cannot safely represent a requested operation."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "LOCAL_CAPABILITY_UNAVAILABLE",
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})
        self.retryable = False
        self.required_action = "change_operation_or_use_live_backend"


@dataclass(frozen=True, slots=True)
class SaveReceipt:
    path: Path
    sha256: str
    bytes_written: int
    local_revision: int
    replaced: bool = False
    rebound: bool = False
    backup_path: Path | None = None


@dataclass(frozen=True, slots=True)
class LocalObject:
    """Revision-scoped local object reference."""

    local_id: int
    scene_id: int
    revision: int
    layer: int
    frame_start: int
    frame_end: int
    alias: str
    name: str | None = None
    api_locked: bool = False

    @property
    def object_id(self) -> str:
        return f"local-{self.revision}-{self.scene_id}-{self.local_id}"

    @property
    def duration(self) -> int:
        return self.frame_end - self.frame_start + 1

    @property
    def duration_frames(self) -> int:
        return self.duration

    @property
    def midpoint(self) -> int:
        return self.frame_start + (self.duration - 1) // 2


class LocalObjectSelection(Sequence[LocalObject]):
    def __init__(self, objects: Sequence[LocalObject]) -> None:
        self._objects = tuple(objects)

    def __iter__(self) -> Iterator[LocalObject]:
        return iter(self._objects)

    def __len__(self) -> int:
        return len(self._objects)

    @overload
    def __getitem__(self, index: int) -> LocalObject: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[LocalObject]: ...

    def __getitem__(self, index: int | slice) -> LocalObject | Sequence[LocalObject]:
        return self._objects[index]

    def one(self) -> LocalObject:
        if len(self._objects) != 1:
            raise LookupError(
                f"expected exactly one local object, found {len(self._objects)}"
            )
        return self._objects[0]

    def first(self) -> LocalObject | None:
        return self._objects[0] if self._objects else None


@dataclass(frozen=True, slots=True)
class LocalSnapshot:
    revision: int
    scene_id: int
    objects: tuple[LocalObject, ...]


@dataclass(frozen=True, slots=True)
class _Simulation:
    project: Project
    dirty_sections: frozenset[str]
    deleted_sections: frozenset[str]
    validation: PlanValidation
    result: PlanResult


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _object_sections(obj: TimelineObject) -> set[str]:
    return {
        str(obj.object_id),
        *(f"{obj.object_id}.{e.effect_id}" for e in obj.effects),
    }


def _document_sections_for_object(
    document: Aup2Document,
    object_id: int,
) -> set[str]:
    base = str(object_id)
    prefix = f"{base}."
    return {
        name
        for name in document.section_names
        if name == base or name.startswith(prefix)
    }


def _maximum_document_object_id(document: Aup2Document) -> int:
    return max(
        (
            int(name.split(".", 1)[0])
            for name in document.section_names
            if name.split(".", 1)[0].isdigit()
            and ("." not in name or name.split(".", 1)[1].isdigit())
        ),
        default=-1,
    )


def _selector_effect(obj: TimelineObject, selector: str) -> Effect:
    name, separator, raw_index = selector.rpartition(":")
    if separator and raw_index.isdigit():
        matches = [value for value in obj.effects if value.name == name]
        index = int(raw_index)
        if index >= len(matches):
            raise ValueError(f"effect selector is absent: {selector!r}")
        return matches[index]
    matches = [value for value in obj.effects if value.name == selector]
    if len(matches) != 1:
        raise ValueError(f"effect selector must resolve exactly once: {selector!r}")
    return matches[0]


def _property_text(value: object) -> str:
    if isinstance(value, (StaticValue,)):
        return value.to_aup2()
    to_aup2 = getattr(value, "to_aup2", None)
    if callable(to_aup2):
        return str(to_aup2())
    return str(value)


def _local_media_kind(path: Path, requested: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    if requested != "auto":
        return requested
    try:
        with Image.open(path) as image:
            image.verify()
        return "image"
    except (OSError, ValueError):
        pass
    if path.suffix.lower() in {
        ".wav",
        ".wave",
        ".mp3",
        ".aac",
        ".m4a",
        ".flac",
        ".ogg",
    }:
        return "audio"
    return "video"


def _local_media_duration(path: Path, kind: str, fps: float) -> int | None:
    if kind == "image":
        return 60
    if kind == "audio" and path.suffix.lower() in {".wav", ".wave"}:
        try:
            with wave.open(str(path), "rb") as source:
                rate = source.getframerate()
                frames = source.getnframes()
            if rate > 0 and frames > 0:
                return max(1, math.ceil(frames / rate * fps))
        except (wave.Error, OSError):
            return None
    if kind == "video":
        try:
            import cv2

            capture = cv2.VideoCapture(str(path))
            try:
                count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
                video_rate = capture.get(cv2.CAP_PROP_FPS)
            finally:
                capture.release()
            if count > 0.0 and video_rate > 0.0 and math.isfinite(count / video_rate):
                return max(1, math.ceil(count / video_rate * fps))
        except (ImportError, OSError, ValueError):
            return None
    return None


def _local_media_size(path: Path, kind: str) -> tuple[int, int] | None:
    if kind == "image":
        with Image.open(path) as image:
            return image.size
    if kind == "video":
        try:
            import cv2

            capture = cv2.VideoCapture(str(path))
            try:
                width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            finally:
                capture.release()
            if width > 0 and height > 0:
                return width, height
        except (ImportError, OSError, ValueError):
            return None
    return None


def _media_object(
    path: Path,
    *,
    kind: str,
    object_id: int,
    layer: int,
    frame: int,
    duration: int,
    transform: Transform,
) -> TimelineObject:
    absolute = str(path.expanduser().resolve())
    if kind == "image":
        effects = [
            Effect(0, "画像ファイル", {"ファイル": absolute}),
            Effect(1, "標準描画", _draw_properties(transform)),
        ]
    elif kind == "video":
        effects = [
            Effect(
                0,
                "動画ファイル",
                {
                    "再生位置": StaticValue(0.0),
                    "再生速度": StaticValue(100.0),
                    "ループ再生": StaticValue(0.0),
                    "ファイル": absolute,
                },
            ),
            Effect(1, "映像再生", _draw_properties(transform)),
        ]
    elif kind == "audio":
        if not transform.empty:
            raise ValueError("visual transforms are unavailable for audio media")
        effects = [
            Effect(
                0,
                "音声ファイル",
                {
                    "再生位置": StaticValue(0.0),
                    "再生速度": StaticValue(100.0),
                    "ファイル": absolute,
                    "トラック": StaticValue(0.0),
                    "ループ再生": StaticValue(0.0),
                },
            ),
            Effect(
                1,
                "音声再生",
                {"音量": StaticValue(100.0), "左右": StaticValue(0.0)},
            ),
        ]
    else:
        raise ValueError(f"unsupported media kind: {kind}")
    return TimelineObject(
        object_id=object_id,
        layer=layer,
        frame_start=frame,
        frame_end=frame + duration - 1,
        effects=effects,
    )


def _object_from_alias(
    alias: str,
    *,
    object_id: int,
    layer: int,
    frame_start: int,
    frame_end: int,
) -> TimelineObject:
    """Parse one host Alias through the established `.aup2` value parser."""

    converted: list[str] = []
    for line in alias.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line:
            continue
        if line == "[Object]":
            continue
        if line.startswith("[Object.") and line.endswith("]"):
            converted.append(f"[{object_id}.{line[8:-1]}]")
        else:
            converted.append(line)
    text = "\n".join(
        [
            "[project]",
            "version=2010200",
            "display.scene=0",
            "[scene.0]",
            "scene=0",
            "name=Root",
            "video.width=1920",
            "video.height=1080",
            "video.rate=30",
            "video.scale=1",
            "audio.rate=44100",
            f"[{object_id}]",
            f"layer={layer}",
            f"frame={frame_start},{frame_end}",
            *converted,
        ]
    )
    project = parse_string(text)
    matches = [
        value for value in project.scenes[0].objects if value.object_id == object_id
    ]
    if len(matches) != 1 or not matches[0].effects:
        raise LocalProjectFormatError(
            "Live Bridge returned an Alias that cannot be represented locally",
            code="LOCAL_ALIAS_PARSE_FAILED",
        )
    return matches[0]


class LocalProject:
    """Stateful, explicitly saved local `.aup2` editing backend."""

    def __init__(
        self,
        project: Project,
        document: Aup2Document,
        *,
        path: Path | None,
        source_sha256: str | None,
        revision: int = 1,
        dirty: bool = False,
        dirty_sections: set[str] | None = None,
        deleted_sections: set[str] | None = None,
    ) -> None:
        self._project = project
        self._document = document
        self._path = path
        self._source_sha256 = source_sha256
        self._revision = revision
        self._dirty = dirty
        self._dirty_sections = set(dirty_sections or ())
        self._deleted_sections = set(deleted_sections or ())

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> LocalProject:
        source = Path(path).expanduser().resolve()
        payload = source.read_bytes()
        try:
            document = Aup2Document.parse_bytes(payload)
            text = payload.removeprefix(b"\xef\xbb\xbf").decode("utf-8")
            project = parse_string(text)
        except (Aup2ParseError, UnicodeDecodeError, ValueError) as error:
            raise LocalProjectFormatError(str(error)) from error
        return cls(
            project,
            document,
            path=source,
            source_sha256=_sha256(payload),
        )

    @classmethod
    def create(
        cls,
        *,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        path: str | os.PathLike[str] | None = None,
    ) -> LocalProject:
        bound = Path(path).expanduser().resolve() if path is not None else None
        project = Project.create_empty(
            width=width,
            height=height,
            fps=fps,
            file_path=str(bound) if bound is not None else "",
        )
        document = Aup2Document.from_project(project)
        return cls(
            project,
            document,
            path=bound,
            source_sha256=None,
            dirty=True,
            dirty_sections=set(document.section_names),
        )

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def source_sha256(self) -> str | None:
        return self._source_sha256

    @property
    def display_scene_id(self) -> int:
        return self._project.display_scene

    @property
    def model(self) -> Project:
        """Return a detached model snapshot; edits must go through this facade."""

        return copy.deepcopy(self._project)

    @property
    def _scene(self) -> Scene:
        scene = self._project.get_scene(self._project.display_scene)
        if scene is None:
            raise LocalProjectFormatError(
                "display.scene does not identify an existing scene",
                code="LOCAL_DISPLAY_SCENE_MISSING",
            )
        return scene

    def _editable(self) -> None:
        if self._project.version not in AUP2_EFFECT_COMPATIBLE_PROJECT_VERSIONS:
            raise LocalCapabilityUnavailableError(
                f"project version {self._project.version} has no editing manifest",
                code="LOCAL_PROJECT_VERSION_UNAVAILABLE",
            )
        if len(self._project.scenes) > 1:
            raise LocalProjectFormatError(
                "multi-scene object ownership is not verified for this file",
                code="LOCAL_SCENE_OWNERSHIP_UNVERIFIED",
            )

    def _reference(
        self, obj: TimelineObject, *, revision: int | None = None
    ) -> LocalObject:
        return LocalObject(
            local_id=obj.object_id,
            scene_id=self._scene.scene_id,
            revision=self._revision if revision is None else revision,
            layer=obj.layer,
            frame_start=obj.frame_start,
            frame_end=obj.frame_end,
            alias=serialize_object_alias(obj),
        )

    def get_snapshot(self, *, include_alias: bool = True) -> LocalSnapshot:
        del include_alias  # Local aliases are inexpensive and always retained.
        return LocalSnapshot(
            self._revision,
            self._scene.scene_id,
            tuple(self._reference(obj) for obj in self._scene.objects),
        )

    @property
    def objects(self) -> LocalObjectSelection:
        return LocalObjectSelection(self.get_snapshot().objects)

    def summary(self) -> dict[str, object]:
        scene = self._scene
        return {
            "revision": self._revision,
            "dirty": self._dirty,
            "path": str(self._path) if self._path is not None else None,
            "source_sha256": self._source_sha256,
            "scene_id": scene.scene_id,
            "width": scene.width,
            "height": scene.height,
            "frame_rate": {"rate": scene.fps, "scale": scene.video_scale},
            "sample_rate": scene.audio_rate,
            "cursor": {"frame": scene.cursor_frame, "layer": scene.cursor_layer},
            "object_count": len(scene.objects),
            "frame_max": scene.max_frame,
            "layer_max": scene.max_layer,
        }

    def find(
        self,
        *,
        name: str | None = None,
        name_contains: str | None = None,
        text: str | None = None,
        text_contains: str | None = None,
        file: str | Path | None = None,
        file_contains: str | None = None,
        effect: str | None = None,
        layer: int | None = None,
        at: int | None = None,
        overlap: tuple[int, int] | None = None,
        api_locked: bool | None = None,
    ) -> LocalObjectSelection:
        unavailable = tuple(
            filter_name
            for filter_name, value in (
                ("name", name),
                ("name_contains", name_contains),
                ("api_locked", api_locked),
            )
            if value is not None
        )
        if unavailable:
            raise LocalCapabilityUnavailableError(
                "the .aup2 backend cannot safely inspect these filters: "
                + ", ".join(unavailable),
                code="LOCAL_QUERY_FILTER_UNAVAILABLE",
                details={"filters": unavailable},
            )
        if overlap is not None:
            overlap_start, overlap_end = overlap
            if (
                isinstance(overlap_start, bool)
                or isinstance(overlap_end, bool)
                or overlap_start < 0
                or overlap_end < overlap_start
            ):
                raise ValueError("overlap must be a non-negative (start, end) range")
        requested_file = (
            str(Path(file).expanduser().resolve()).casefold()
            if file is not None
            else None
        )
        requested_file_contains = (
            file_contains.casefold() if file_contains is not None else None
        )
        requested_effect = effect
        if effect in available_effect_profiles():
            assert effect is not None
            requested_effect = get_effect_profile(effect).native_name
        results: list[LocalObject] = []
        for obj in self._scene.objects:
            if layer is not None and obj.layer != layer:
                continue
            if at is not None and not obj.frame_start <= at <= obj.frame_end:
                continue
            if overlap is not None and not (
                obj.frame_start <= overlap[1] and overlap[0] <= obj.frame_end
            ):
                continue
            if requested_effect is not None and not any(
                value.name == requested_effect for value in obj.effects
            ):
                continue
            if text is not None or text_contains is not None:
                text_effect = obj.get_effect("テキスト")
                raw_text = (
                    str(text_effect.properties.get("テキスト", ""))
                    if text_effect is not None
                    else ""
                )
                if text is not None and raw_text != text:
                    continue
                if (
                    text_contains is not None
                    and text_contains.casefold() not in raw_text.casefold()
                ):
                    continue
            if requested_file is not None or requested_file_contains is not None:
                paths = [
                    str(value)
                    for candidate in obj.effects
                    for key, value in candidate.properties.items()
                    if key == "ファイル"
                ]
                normalized_paths = [
                    str(Path(value).expanduser().resolve()).casefold()
                    for value in paths
                ]
                if (
                    requested_file is not None
                    and requested_file not in normalized_paths
                ):
                    continue
                if requested_file_contains is not None and not any(
                    requested_file_contains in value for value in normalized_paths
                ):
                    continue
            results.append(self._reference(obj))
        return LocalObjectSelection(results)

    def _current(
        self, target: object, project: Project | None = None
    ) -> TimelineObject:
        candidate = getattr(target, "local", target)
        if not isinstance(candidate, LocalObject):
            raise TypeError("local plans require LocalObject or SyncedObject targets")
        if candidate.revision != self._revision:
            raise ProjectChangedError("the local object reference is stale")
        active = self._project if project is None else project
        scene = active.get_scene(candidate.scene_id)
        if scene is None:
            raise ProjectChangedError("the local scene is unavailable")
        matches = [
            value for value in scene.objects if value.object_id == candidate.local_id
        ]
        if len(matches) != 1:
            raise ProjectChangedError("the local object is unavailable")
        return matches[0]

    @staticmethod
    def _occupied(
        ranges: Sequence[TimelineRange],
        layer: int,
        frame: int,
        duration: int,
    ) -> tuple[str, ...]:
        return _range_conflicts(
            ranges,
            layer=layer,
            frame=frame,
            duration=duration,
        )

    def _simulate(self, plan: EditPlan) -> _Simulation:
        self._editable()
        if plan.consumed:
            raise RuntimeError("a successfully applied EditPlan cannot be reused")
        if not plan.commands:
            raise ValueError("an EditPlan must contain at least one command")
        project = copy.deepcopy(self._project)
        scene = project.get_scene(project.display_scene)
        assert scene is not None
        ranges = [
            TimelineRange(
                obj.layer,
                obj.frame_start,
                obj.frame_end,
                f"local:{obj.object_id}",
            )
            for obj in scene.objects
        ]
        serial_frame = scene.cursor_frame
        next_object_id = (
            max(
                [
                    _maximum_document_object_id(self._document),
                    *(value.object_id for value in scene.objects),
                ]
            )
            + 1
        )
        dirty: set[str] = set()
        deleted: set[str] = set()
        placements: list[PlannedPlacement] = []
        commands: list[PlanCommandResult] = []
        result_ids: dict[str, tuple[int, ...]] = {}
        effect_records: list[tuple[str, int, int, str | None]] = []

        for index, command in enumerate(plan.commands):
            key = command.key or f"command-{index}"
            if command.op in {"add_text", "add_shape", "add_media"}:
                at = command.values["at"]
                if at is not None and at != "end" and not isinstance(at, int):
                    raise TypeError("at must be a frame number, 'end', or None")
                frame = _resolve_frame(
                    at,
                    sequence=plan.sequence,
                    cursor_frame=scene.cursor_frame,
                    serial_frame=serial_frame,
                    ranges=ranges,
                )
                raw_duration = command.values["duration"]
                kind: str | None = None
                media_path: Path | None = None
                if command.op == "add_media":
                    media_file = command.values["file"]
                    if not isinstance(media_file, (str, os.PathLike)):
                        raise TypeError("media file must be a filesystem path")
                    media_path = Path(media_file).expanduser().resolve()
                    kind = _local_media_kind(media_path, str(command.values["kind"]))
                if raw_duration is None:
                    if media_path is None or kind is None:
                        duration = 60
                    else:
                        detected = _local_media_duration(
                            media_path,
                            kind,
                            scene.fps / scene.video_scale,
                        )
                        if detected is None:
                            raise LocalCapabilityUnavailableError(
                                "local media duration is unavailable; specify duration",
                                code="LOCAL_MEDIA_DURATION_UNAVAILABLE",
                            )
                        duration = detected
                else:
                    assert isinstance(raw_duration, int)
                    duration = raw_duration
                raw_layer = command.values["layer"]
                if raw_layer is None:
                    suggested = _suggested_timeline_layer(
                        ranges,
                        frame=frame,
                        duration=duration,
                    )
                    if suggested is None:
                        raise ValueError("no collision-free local layer is available")
                    layer = suggested
                else:
                    assert isinstance(raw_layer, int)
                    layer = raw_layer
                    conflicts = self._occupied(ranges, layer, frame, duration)
                    if conflicts:
                        suggestion = _suggested_timeline_layer(
                            ranges,
                            frame=frame,
                            duration=duration,
                        )
                        raise PlacementConflictError(
                            layer=layer,
                            frame_start=frame,
                            frame_end=frame + duration - 1,
                            conflicting_object_ids=conflicts,
                            suggested_layer=suggestion,
                        )
                transform = command.values["transform"]
                assert isinstance(transform, Transform)
                if command.op == "add_text":
                    obj = make_text_object(
                        str(command.values["text"]),
                        layer=layer,
                        frame=frame,
                        length=duration,
                        size=float(cast(float, command.values["size"])),
                        color=str(command.values["color"]),
                    )
                    obj.effects[1].properties = _draw_properties(transform)
                    font = command.values["font"]
                    if font is not None:
                        obj.effects[0].properties["フォント"] = str(font)
                elif command.op == "add_shape":
                    obj = _shape_object(
                        str(command.values["shape"]),
                        layer=layer,
                        frame=frame,
                        duration=duration,
                        color=str(command.values["color"]),
                        width=float(cast(float, command.values["width"])),
                        height=float(cast(float, command.values["height"])),
                        transform=transform,
                    )
                else:
                    assert media_path is not None and kind is not None
                    fit = command.values.get("fit")
                    apply_orientation = (
                        command.values.get("apply_exif_orientation") is True
                    )
                    orientation = 1
                    if apply_orientation:
                        if kind != "image":
                            raise ValueError(
                                "EXIF orientation is supported only for images"
                            )
                        orientation = _image_exif_orientation(media_path)
                        if orientation in {2, 4, 5, 7}:
                            raise ValueError(
                                "mirrored EXIF orientation must be normalized first"
                            )
                        offset = {1: 0.0, 3: 180.0, 6: 90.0, 8: -90.0}[orientation]
                        if offset:
                            transform = replace(
                                transform,
                                rotation=None,
                                rotation_z=_offset_transform_value(
                                    transform.effective_rotation_z,
                                    offset,
                                ),
                            )
                    if fit is not None:
                        if kind == "audio":
                            raise ValueError("fit is unavailable for audio")
                        media_size = _local_media_size(media_path, kind)
                        if media_size is None:
                            raise ValueError(
                                "media dimensions are unavailable; omit fit"
                            )
                        width, height = media_size
                        if apply_orientation and orientation in {5, 6, 7, 8}:
                            width, height = height, width
                        ratios = (scene.width / width, scene.height / height)
                        scale = (
                            min(ratios) if fit == "contain" else max(ratios)
                        ) * 100.0
                        transform = replace(transform, scale=scale)
                    obj = _media_object(
                        media_path,
                        kind=kind,
                        object_id=next_object_id,
                        layer=layer,
                        frame=frame,
                        duration=duration,
                        transform=transform,
                    )
                obj.object_id = next_object_id
                scene.objects.append(obj)
                effects = command.values.get("effects", ())
                if effects:
                    specs = cast(Sequence[EffectDefinition], effects)
                    added_effects = apply_effects(
                        project,
                        obj,
                        *specs,
                    )
                    for initial_spec, initial_effect in zip(specs, added_effects):
                        profile = (
                            initial_spec.profile
                            if isinstance(initial_spec, EffectSpec)
                            else None
                        )
                        effect_records.append(
                            (key, obj.object_id, initial_effect.effect_id, profile)
                        )
                dirty.update(_object_sections(obj))
                result_ids[key] = (obj.object_id,)
                next_object_id += 1
                ranges.append(
                    TimelineRange(
                        layer,
                        frame,
                        frame + duration - 1,
                        f"plan:{key}",
                    )
                )
                if plan.sequence == "serial" and at is None:
                    serial_frame = frame + duration
                placements.append(PlannedPlacement(index, key, layer, frame, duration))
                commands.append(PlanCommandResult(index, key, "applied"))
                continue

            obj = self._current(command.target, project)
            old_sections = _object_sections(obj) | _document_sections_for_object(
                self._document,
                obj.object_id,
            )
            if command.op == "update":
                if command.values["name"] is not None:
                    raise LocalCapabilityUnavailableError(
                        "object names have no verified .aup2 representation",
                        code="LOCAL_PROPERTY_UNAVAILABLE",
                    )
                text = command.values["text"]
                if text is not None:
                    effect = obj.get_effect("テキスト")
                    if effect is None:
                        raise ValueError("target is not a text object")
                    effect.properties["テキスト"] = str(text)
                    dirty.add(f"{obj.object_id}.{effect.effect_id}")
                transform = command.values["transform"]
                assert isinstance(transform, Transform)
                if not transform.empty:
                    effect = obj.get_effect("標準描画") or obj.get_effect("映像再生")
                    if effect is None:
                        raise ValueError("target has no verified transform effect")
                    updates = {
                        "X": transform.x,
                        "Y": transform.y,
                        "Z": transform.z,
                        "X軸回転": transform.rotation_x,
                        "Y軸回転": transform.rotation_y,
                        "Z軸回転": transform.effective_rotation_z,
                        "拡大率": transform.scale,
                    }
                    for name, value in updates.items():
                        if value is not None:
                            effect.properties[name] = _track_value(value)
                    if transform.opacity is not None:
                        effect.properties["透明度"] = _track_value(
                            transform.opacity,
                            invert_opacity=True,
                        )
                    dirty.add(f"{obj.object_id}.{effect.effect_id}")
            elif command.op == "move":
                ranges = [
                    value
                    for value in ranges
                    if value.reference != f"local:{obj.object_id}"
                ]
                layer_value = command.values["layer"]
                frame_value = command.values["at"]
                if not isinstance(layer_value, int) or not isinstance(frame_value, int):
                    raise TypeError("move layer and frame must be integers")
                layer = layer_value
                frame = frame_value
                conflicts = self._occupied(ranges, layer, frame, obj.duration_frames)
                if conflicts:
                    raise PlacementConflictError(
                        layer=layer,
                        frame_start=frame,
                        frame_end=frame + obj.duration_frames - 1,
                        conflicting_object_ids=conflicts,
                        suggested_layer=_suggested_timeline_layer(
                            ranges,
                            frame=frame,
                            duration=obj.duration_frames,
                        ),
                    )
                obj.layer = layer
                obj.frame_end = frame + obj.duration_frames - 1
                obj.frame_start = frame
                ranges.append(
                    TimelineRange(
                        obj.layer,
                        obj.frame_start,
                        obj.frame_end,
                        f"local:{obj.object_id}",
                    )
                )
                dirty.add(str(obj.object_id))
            elif command.op == "delete":
                scene.objects.remove(obj)
                deleted.update(old_sections)
                ranges = [
                    value
                    for value in ranges
                    if value.reference != f"local:{obj.object_id}"
                ]
            elif command.op == "add_effect":
                item_values = command.values["items"]
                if not isinstance(item_values, Mapping):
                    raise TypeError("effect items must be a mapping")
                properties = dict(item_values)
                if command.values["enabled"] is False:
                    properties = {"effect.disable": StaticValue(1.0), **properties}
                effect = Effect(
                    max((value.effect_id for value in obj.effects), default=-1) + 1,
                    str(command.values["effect"]),
                    properties,
                )
                obj.effects.append(effect)
                dirty.add(f"{obj.object_id}.{effect.effect_id}")
                effect_records.append((key, obj.object_id, effect.effect_id, None))
            elif command.op == "apply_effect":
                effect_spec = command.values["spec"]
                if not isinstance(effect_spec, (EffectSpec, NativeEffectSpec)):
                    raise TypeError("invalid Effect specification")
                semantic_effects = apply_effects(project, obj, effect_spec)
                dirty.update(
                    f"{obj.object_id}.{semantic_effect.effect_id}"
                    for semantic_effect in semantic_effects
                )
                for semantic_effect in semantic_effects:
                    profile = (
                        effect_spec.profile
                        if isinstance(effect_spec, EffectSpec)
                        else None
                    )
                    effect_records.append(
                        (key, obj.object_id, semantic_effect.effect_id, profile)
                    )
            elif command.op == "set_effect_enabled":
                effect = _selector_effect(obj, str(command.values["selector"]))
                if command.values["enabled"] is True:
                    effect.properties.pop("effect.disable", None)
                else:
                    effect.properties["effect.disable"] = StaticValue(1.0)
                dirty.add(f"{obj.object_id}.{effect.effect_id}")
            elif command.op == "delete_effect":
                effect = _selector_effect(obj, str(command.values["selector"]))
                obj.effects.remove(effect)
                deleted.add(f"{obj.object_id}.{effect.effect_id}")
            else:
                raise LocalCapabilityUnavailableError(
                    f"local EditPlan operation is unavailable: {command.op}"
                )
            if command.op != "delete":
                result_ids[key] = (obj.object_id,)
            commands.append(PlanCommandResult(index, key, "applied"))

        validation_report = validate_standard_effects(project)
        if validation_report.errors:
            raise ValueError(validation_report.errors[0].message)
        next_revision = self._revision + 1
        groups = {
            key: ObjectGroup(
                tuple(
                    self._reference(
                        next(
                            value
                            for value in scene.objects
                            if value.object_id == object_id
                        ),
                        revision=next_revision,
                    )
                    for object_id in object_ids
                )
            )
            for key, object_ids in result_ids.items()
        }
        applied_effects: dict[str, list[AppliedEffect]] = {}
        for key, object_id, effect_id, profile in effect_records:
            obj = next(value for value in scene.objects if value.object_id == object_id)
            selected = next(
                value for value in obj.effects if value.effect_id == effect_id
            )
            same_name = [value for value in obj.effects if value.name == selected.name]
            selector_index = same_name.index(selected)
            applied_effects.setdefault(key, []).append(
                AppliedEffect(
                    profile,
                    selected.name,
                    f"{selected.name}:{selector_index}",
                    obj.effects.index(selected),
                    "effect.disable" not in selected.properties,
                    {
                        name: _property_text(value)
                        for name, value in selected.properties.items()
                        if name != "effect.disable"
                    },
                    f"local-{next_revision}-{scene.scene_id}-{object_id}",
                    next_revision,
                )
            )
        validation = PlanValidation(True, self._revision, tuple(placements))
        result = PlanResult(
            revision_before=self._revision,
            revision=next_revision,
            applied_count=len(commands),
            undo_grouped=False,
            atomic=True,
            commands=tuple(commands),
            objects=groups,
            effects={key: tuple(value) for key, value in applied_effects.items()},
            rollback=RollbackReceipt(),
            warnings=("LOCAL_IN_MEMORY_ONLY",),
        )
        return _Simulation(
            project,
            frozenset(dirty),
            frozenset(deleted),
            validation,
            result,
        )

    @staticmethod
    def _issue(error: Exception) -> ValidationIssue:
        return ValidationIssue(
            getattr(error, "code", "INVALID_EDIT_PLAN"),
            str(error),
            getattr(error, "details", {}),
        )

    def validate(self, plan: EditPlan) -> PlanValidation:
        try:
            return self._simulate(plan).validation
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            return PlanValidation(
                False,
                self._revision,
                errors=(str(error),),
                issues=(self._issue(error),),
            )

    def apply(self, plan: EditPlan) -> PlanResult:
        try:
            simulation = self._simulate(plan)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            validation = PlanValidation(
                False,
                self._revision,
                errors=(str(error),),
                issues=(self._issue(error),),
            )
            raise PlanValidationError(validation) from error
        self._project = simulation.project
        self._dirty_sections.update(simulation.dirty_sections)
        self._deleted_sections.update(simulation.deleted_sections)
        self._revision = simulation.result.revision
        self._dirty = True
        plan._mark_consumed()
        return simulation.result

    def _single_group(self, plan: EditPlan) -> ObjectGroup:
        result = self.apply(plan)
        group = next(iter(result.objects.values()), None)
        if group is None:
            raise RuntimeError("the local edit did not return an updated object")
        return group

    def add_text(
        self,
        text: str,
        *,
        key: str | None = None,
        at: FramePosition = None,
        layer: int | None = None,
        duration: int | None = None,
        x: TransformValue | None = None,
        y: TransformValue | None = None,
        z: TransformValue | None = None,
        scale: TransformValue | None = None,
        rotation: TransformValue | None = None,
        rotation_x: TransformValue | None = None,
        rotation_y: TransformValue | None = None,
        rotation_z: TransformValue | None = None,
        opacity: TransformValue | None = None,
        size: float = 34.0,
        color: str = "ffffff",
        font: str | None = None,
        effects: Sequence[EffectDefinition] | None = None,
    ) -> ObjectGroup:
        return self._single_group(
            EditPlan().add_text(
                text,
                key=key,
                at=at,
                layer=layer,
                duration=duration,
                x=x,
                y=y,
                z=z,
                scale=scale,
                rotation=rotation,
                rotation_x=rotation_x,
                rotation_y=rotation_y,
                rotation_z=rotation_z,
                opacity=opacity,
                size=size,
                color=color,
                font=font,
                effects=list(effects) if effects is not None else None,
            )
        )

    def add_media(
        self,
        file: str | os.PathLike[str],
        *,
        kind: Literal["auto", "image", "video", "audio"] = "auto",
        key: str | None = None,
        at: FramePosition = None,
        layer: int | None = None,
        duration: int | None = None,
        x: TransformValue | None = None,
        y: TransformValue | None = None,
        z: TransformValue | None = None,
        scale: TransformValue | None = None,
        rotation: TransformValue | None = None,
        rotation_x: TransformValue | None = None,
        rotation_y: TransformValue | None = None,
        rotation_z: TransformValue | None = None,
        opacity: TransformValue | None = None,
        fit: MediaFit | None = None,
        apply_exif_orientation: bool = False,
        effects: Sequence[EffectDefinition] | None = None,
    ) -> ObjectGroup:
        return self._single_group(
            EditPlan().add_media(
                file,
                kind=kind,
                key=key,
                at=at,
                layer=layer,
                duration=duration,
                x=x,
                y=y,
                z=z,
                scale=scale,
                rotation=rotation,
                rotation_x=rotation_x,
                rotation_y=rotation_y,
                rotation_z=rotation_z,
                opacity=opacity,
                fit=fit,
                apply_exif_orientation=apply_exif_orientation,
                effects=list(effects) if effects is not None else None,
            )
        )

    def add_image(
        self,
        file: str | os.PathLike[str],
        *,
        key: str | None = None,
        at: FramePosition = None,
        layer: int | None = None,
        duration: int | None = None,
        x: TransformValue | None = None,
        y: TransformValue | None = None,
        z: TransformValue | None = None,
        scale: TransformValue | None = None,
        rotation: TransformValue | None = None,
        rotation_x: TransformValue | None = None,
        rotation_y: TransformValue | None = None,
        rotation_z: TransformValue | None = None,
        opacity: TransformValue | None = None,
        fit: MediaFit | None = None,
        apply_exif_orientation: bool = False,
        effects: Sequence[EffectDefinition] | None = None,
    ) -> ObjectGroup:
        return self.add_media(
            file,
            kind="image",
            key=key,
            at=at,
            layer=layer,
            duration=duration,
            x=x,
            y=y,
            z=z,
            scale=scale,
            rotation=rotation,
            rotation_x=rotation_x,
            rotation_y=rotation_y,
            rotation_z=rotation_z,
            opacity=opacity,
            fit=fit,
            apply_exif_orientation=apply_exif_orientation,
            effects=effects,
        )

    def add_video(
        self,
        file: str | os.PathLike[str],
        *,
        key: str | None = None,
        at: FramePosition = None,
        layer: int | None = None,
        duration: int | None = None,
        x: TransformValue | None = None,
        y: TransformValue | None = None,
        z: TransformValue | None = None,
        scale: TransformValue | None = None,
        rotation: TransformValue | None = None,
        rotation_x: TransformValue | None = None,
        rotation_y: TransformValue | None = None,
        rotation_z: TransformValue | None = None,
        opacity: TransformValue | None = None,
        fit: MediaFit | None = None,
        effects: Sequence[EffectDefinition] | None = None,
    ) -> ObjectGroup:
        return self.add_media(
            file,
            kind="video",
            key=key,
            at=at,
            layer=layer,
            duration=duration,
            x=x,
            y=y,
            z=z,
            scale=scale,
            rotation=rotation,
            rotation_x=rotation_x,
            rotation_y=rotation_y,
            rotation_z=rotation_z,
            opacity=opacity,
            fit=fit,
            effects=effects,
        )

    def add_audio(
        self,
        file: str | os.PathLike[str],
        *,
        key: str | None = None,
        at: FramePosition = None,
        layer: int | None = None,
        duration: int | None = None,
        effects: Sequence[EffectDefinition] | None = None,
    ) -> ObjectGroup:
        return self.add_media(
            file,
            kind="audio",
            key=key,
            at=at,
            layer=layer,
            duration=duration,
            effects=effects,
        )

    def add_shape(
        self,
        shape: str,
        *,
        key: str | None = None,
        at: FramePosition = None,
        layer: int | None = None,
        duration: int | None = None,
        color: str = "ffffff",
        width: float = 200.0,
        height: float = 200.0,
        x: TransformValue | None = None,
        y: TransformValue | None = None,
        z: TransformValue | None = None,
        scale: TransformValue | None = None,
        rotation: TransformValue | None = None,
        rotation_x: TransformValue | None = None,
        rotation_y: TransformValue | None = None,
        rotation_z: TransformValue | None = None,
        opacity: TransformValue | None = None,
        effects: Sequence[EffectDefinition] | None = None,
    ) -> ObjectGroup:
        return self._single_group(
            EditPlan().add_shape(
                shape,
                key=key,
                at=at,
                layer=layer,
                duration=duration,
                color=color,
                width=width,
                height=height,
                x=x,
                y=y,
                z=z,
                scale=scale,
                rotation=rotation,
                rotation_x=rotation_x,
                rotation_y=rotation_y,
                rotation_z=rotation_z,
                opacity=opacity,
                effects=list(effects) if effects is not None else None,
            )
        )

    def update(
        self,
        target: object,
        *,
        key: str | None = None,
        text: str | None = None,
        name: str | None = None,
        x: TransformValue | None = None,
        y: TransformValue | None = None,
        z: TransformValue | None = None,
        scale: TransformValue | None = None,
        rotation: TransformValue | None = None,
        rotation_x: TransformValue | None = None,
        rotation_y: TransformValue | None = None,
        rotation_z: TransformValue | None = None,
        opacity: TransformValue | None = None,
    ) -> ObjectGroup:
        return self._single_group(
            EditPlan().update(
                target,
                key=key,
                text=text,
                name=name,
                x=x,
                y=y,
                z=z,
                scale=scale,
                rotation=rotation,
                rotation_x=rotation_x,
                rotation_y=rotation_y,
                rotation_z=rotation_z,
                opacity=opacity,
            )
        )

    def move(self, target: object, *, at: int, layer: int) -> ObjectGroup:
        return self._single_group(EditPlan().move(target, at=at, layer=layer))

    def delete(self, target: object) -> PlanResult:
        return self.apply(EditPlan().delete(target))

    def add_effect(
        self,
        target: object,
        effect: str,
        *,
        values: Mapping[str, object] | None = None,
        enabled: bool = True,
    ) -> ObjectGroup:
        return self._single_group(
            EditPlan().add_effect(
                target,
                effect,
                values=values,
                enabled=enabled,
            )
        )

    def apply_effect(
        self,
        target: object,
        spec: EffectDefinition,
    ) -> AppliedEffect:
        result = self.apply(EditPlan().apply_effect(target, spec, key="effect"))
        effects = result.effects.get("effect", ())
        if len(effects) != 1:
            raise RuntimeError("the local edit did not return one applied effect")
        return effects[0]

    def set_effect_enabled(
        self,
        target: object,
        selector: str,
        enabled: bool,
    ) -> ObjectGroup:
        return self._single_group(
            EditPlan().set_effect_enabled(target, selector, enabled)
        )

    def delete_effect(self, target: object, selector: str) -> ObjectGroup:
        return self._single_group(EditPlan().delete_effect(target, selector))

    @staticmethod
    def available_effect_profiles() -> tuple[str, ...]:
        return available_effect_profiles()

    def _fork(self) -> LocalProject:
        return LocalProject(
            copy.deepcopy(self._project),
            self._document,
            path=self._path,
            source_sha256=self._source_sha256,
            revision=self._revision,
            dirty=self._dirty,
            dirty_sections=set(self._dirty_sections),
            deleted_sections=set(self._deleted_sections),
        )

    def _adopt(self, other: LocalProject, *, expected_revision: int) -> None:
        if self._revision != expected_revision:
            raise ProjectChangedError(
                "the local project changed during synchronization"
            )
        self._project = other._project
        self._dirty_sections = set(other._dirty_sections)
        self._deleted_sections = set(other._deleted_sections)
        self._revision = other._revision
        self._dirty = other._dirty

    def _replace_scene_from_live(
        self,
        live_objects: Sequence[object],
        *,
        expected_revision: int,
        live_to_local_id: Mapping[str, int] | None = None,
    ) -> None:
        """Canonicalize the edited scene from a post-plan host snapshot."""

        if self._revision != expected_revision:
            raise ProjectChangedError("the local project changed before Alias readback")
        scene = self._scene
        old_by_id = {obj.object_id: obj for obj in scene.objects}
        old_by_range = {
            (obj.layer, obj.frame_start, obj.frame_end): obj for obj in scene.objects
        }
        used_ids: set[int] = set()
        next_id = (
            max(
                [
                    _maximum_document_object_id(self._document),
                    *(obj.object_id for obj in scene.objects),
                ]
            )
            + 1
        )
        rebuilt: list[TimelineObject] = []
        changed: set[str] = set()
        for live in live_objects:
            alias = getattr(live, "alias", None)
            if not isinstance(alias, str) or not alias:
                raise LocalProjectFormatError(
                    "post-plan Live snapshot omitted an object Alias",
                    code="LOCAL_ALIAS_READBACK_MISSING",
                )
            layer = int(getattr(live, "layer"))
            frame_start = int(getattr(live, "frame_start"))
            frame_end = int(getattr(live, "frame_end"))
            live_id = str(getattr(live, "object_id", ""))
            mapped_id = (
                live_to_local_id.get(live_id) if live_to_local_id is not None else None
            )
            old = old_by_id.get(mapped_id) if mapped_id is not None else None
            if old is None:
                old = old_by_range.get((layer, frame_start, frame_end))
            if old is not None and old.object_id not in used_ids:
                object_id = old.object_id
            else:
                object_id = next_id
                next_id += 1
            used_ids.add(object_id)
            obj = _object_from_alias(
                alias,
                object_id=object_id,
                layer=layer,
                frame_start=frame_start,
                frame_end=frame_end,
            )
            rebuilt.append(obj)
            if old is None or serialize_object_alias(old) != serialize_object_alias(
                obj
            ):
                changed.update(_object_sections(obj))
                if old is not None:
                    self._deleted_sections.update(
                        (
                            _object_sections(old)
                            | _document_sections_for_object(
                                self._document,
                                old.object_id,
                            )
                        )
                        - _object_sections(obj)
                    )
        removed = [obj for obj in scene.objects if obj.object_id not in used_ids]
        for obj in removed:
            self._deleted_sections.update(
                _object_sections(obj)
                | _document_sections_for_object(self._document, obj.object_id)
            )
        scene.objects = rebuilt
        self._dirty_sections.update(changed)
        self._dirty = True

    def reload(self, *, discard_changes: bool = False) -> LocalProject:
        if self._path is None:
            raise ValueError("an unbound local project cannot be reloaded")
        if self._dirty and not discard_changes:
            raise LocalFileChangedError(
                "local changes exist; pass discard_changes=True to reload"
            )
        loaded = type(self).load(self._path)
        self.__dict__.update(loaded.__dict__)
        return self

    def _render(self, target: Path) -> bytes:
        return self._document.render(
            self._project,
            dirty_sections=self._dirty_sections,
            deleted_sections=self._deleted_sections,
            project_path=target,
        ).encode("utf-8")

    @staticmethod
    def _write_new(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _replace(
        path: Path,
        payload: bytes,
        *,
        expected_sha256: str,
    ) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            observed = _sha256(path.read_bytes())
            if observed != expected_sha256:
                raise LocalFileChangedError(
                    "save target changed immediately before replacement",
                    path=path,
                    expected_sha256=expected_sha256,
                    observed_sha256=observed,
                )
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _backup_path(path: Path) -> Path:
        candidate = path.with_suffix(path.suffix + ".bak")
        index = 1
        while candidate.exists():
            candidate = path.with_suffix(path.suffix + f".bak.{index}")
            index += 1
        return candidate

    def checkpoint(
        self,
        path: str | os.PathLike[str] | None = None,
    ) -> SaveReceipt:
        if path is None:
            if self._path is None:
                raise ValueError("checkpoint path is required for an unbound project")
            index = 1
            while True:
                candidate = self._path.with_name(
                    f"{self._path.stem}.ai-{index:04d}{self._path.suffix}"
                )
                if not candidate.exists():
                    target = candidate
                    break
                index += 1
        else:
            target = Path(path).expanduser().resolve()
        payload = self._render(target)
        self._write_new(target, payload)
        return SaveReceipt(
            target,
            _sha256(payload),
            len(payload),
            self._revision,
        )

    def save_as(
        self,
        path: str | os.PathLike[str],
        *,
        overwrite: bool = False,
        expected_sha256: str | None = None,
    ) -> SaveReceipt:
        if self._project.version not in AUP2_EFFECT_COMPATIBLE_PROJECT_VERSIONS:
            raise LocalCapabilityUnavailableError(
                "unknown project versions support checkpoint-only output",
                code="LOCAL_PROJECT_VERSION_UNAVAILABLE",
            )
        target = Path(path).expanduser().resolve()
        replaced = target.exists()
        if replaced:
            if not overwrite or expected_sha256 is None:
                raise FileExistsError(
                    "existing save_as targets require overwrite=True and "
                    "expected_sha256"
                )
            actual = _sha256(target.read_bytes())
            if actual != expected_sha256:
                raise LocalFileChangedError(
                    "save_as target hash does not match",
                    path=target,
                    expected_sha256=expected_sha256,
                    observed_sha256=actual,
                )
        payload = self._render(target)
        if replaced:
            assert expected_sha256 is not None
            self._replace(
                target,
                payload,
                expected_sha256=expected_sha256,
            )
        else:
            self._write_new(target, payload)
        self._rebind(target, payload)
        return SaveReceipt(
            target,
            self._source_sha256 or _sha256(payload),
            len(payload),
            self._revision,
            replaced=replaced,
            rebound=True,
        )

    def save_source(
        self,
        *,
        overwrite: bool = False,
        backup: bool = True,
    ) -> SaveReceipt:
        if self._project.version not in AUP2_EFFECT_COMPATIBLE_PROJECT_VERSIONS:
            raise LocalCapabilityUnavailableError(
                "unknown project versions support checkpoint-only output",
                code="LOCAL_PROJECT_VERSION_UNAVAILABLE",
            )
        if not overwrite:
            raise LocalOverwriteRequiredError(self._path)
        if self._path is None or self._source_sha256 is None:
            raise ValueError("the project is not bound to an existing source file")
        actual = _sha256(self._path.read_bytes())
        if actual != self._source_sha256:
            raise LocalFileChangedError(
                "source file changed since it was loaded",
                path=self._path,
                expected_sha256=self._source_sha256,
                observed_sha256=actual,
            )
        payload = self._render(self._path)
        backup_path = None
        if backup:
            backup_path = self._backup_path(self._path)
            shutil.copy2(self._path, backup_path)
        self._replace(
            self._path,
            payload,
            expected_sha256=self._source_sha256,
        )
        self._rebind(self._path, payload)
        return SaveReceipt(
            self._path,
            self._source_sha256 or _sha256(payload),
            len(payload),
            self._revision,
            replaced=True,
            rebound=True,
            backup_path=backup_path,
        )

    def _rebind(self, path: Path, payload: bytes) -> None:
        self._path = path
        self._source_sha256 = _sha256(payload)
        self._document = Aup2Document.parse_bytes(payload)
        self._project.file_path = str(path)
        self._dirty_sections.clear()
        self._deleted_sections.clear()
        self._dirty = False


__all__ = [
    "LocalCapabilityUnavailableError",
    "LocalFileChangedError",
    "LocalObject",
    "LocalObjectSelection",
    "LocalOverwriteRequiredError",
    "LocalProject",
    "LocalProjectFormatError",
    "LocalSnapshot",
    "SaveReceipt",
]
