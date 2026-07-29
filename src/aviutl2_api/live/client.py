"""High-level Python client for the AviUtl2 Live Bridge."""

from __future__ import annotations

import base64
import hashlib
import itertools
import math
from collections.abc import Mapping, Sequence
from os import PathLike
from pathlib import Path
from typing import Any, Literal

from aviutl2_api.models import AnimatedValue, StaticValue, TimelineObject

from .audio import RenderedAudio
from .catalog import EffectCatalogPage
from .commands import (
    CreateFromAliasCommand,
    ItemUpdate,
    make_text_object,
)
from .discovery import (
    AmbiguousInstanceError,
    InstanceInfo,
    discover_instances,
)
from .effects import (
    EffectApplication,
    EffectSemantic,
    EffectValue,
    apply_common_effect,
)
from .events import EventWatchResult, SessionInfo
from .frame import (
    ContactSheet,
    RenderedFrame,
    make_contact_sheet,
    review_sample_frames,
)
from .inspection import ItemInspection, ObjectInspection
from .layers import LayerPage
from .media import (
    CreatedMediaObject,
    MediaInventory,
    MediaProbe,
    MediaRelinkReceipt,
    MediaSplit,
)
from .protocol import BridgeRemoteError, Response, decode_response, encode_request
from .scene import SceneInfo
from .sections import ObjectSections
from .snapshot import ProjectSnapshot, SnapshotObject
from .subtitles import (
    SubtitleBatchResult,
    SubtitleCue,
    SubtitleLayerPolicy,
    SubtitleStyle,
    assign_subtitle_layers,
    load_subtitles,
)
from .timeline import (
    ObjectGroup,
    TimelineTransactionCommand,
    TransactionReceipt,
)
from .transport import FramedTransport, connect_named_pipe

_MUTATION_METHODS = frozenset(
    {
        "batch.apply",
        "layer.update",
        "media.relink",
        "media.trim",
        "object.create_from_alias",
        "object.create_from_media_file",
        "object.delete",
        "object.effect.add",
        "object.effect.delete",
        "object.effect.reorder",
        "object.effect.set_enabled",
        "object.move",
        "object.section.create",
        "object.section.delete",
        "object.section.move",
        "object.set_duration",
        "object.set_item",
        "object.set_items",
        "object.set_name",
        "object.split_media",
        "scene.update_current",
        "timeline.close_gap",
        "timeline.ripple_delete",
        "timeline.ripple_insert",
        "timeline.shift_after",
        "timeline.transaction.apply",
    }
)


class _Unchanged:
    pass


_UNCHANGED = _Unchanged()


class LiveClient:
    """A serial, synchronous client for one AviUtl2 process."""

    def __init__(
        self,
        transport: FramedTransport,
        *,
        default_timeout: float = 5.0,
    ) -> None:
        if default_timeout <= 0:
            raise ValueError("default_timeout must be positive")
        self._transport = transport
        self._default_timeout = default_timeout
        self._request_ids = itertools.count(1)
        self._operation_ids = itertools.count(1)
        self._session: SessionInfo | None = None

    @classmethod
    def connect(
        cls,
        *,
        pid: int | None = None,
        pipe_name: str | None = None,
        timeout: float = 5.0,
    ) -> LiveClient:
        """Discover or directly connect to an AviUtl2 Live Bridge instance."""
        selected_pipe = pipe_name
        if selected_pipe is None:
            instances = discover_instances()
            if pid is not None:
                instances = [item for item in instances if item.pid == pid]
            if not instances:
                target = f" for PID {pid}" if pid is not None else ""
                raise FileNotFoundError(
                    f"no AviUtl2 Live Bridge instance found{target}"
                )
            if len(instances) > 1:
                raise AmbiguousInstanceError(instances)
            selected_pipe = instances[0].pipe
        client = cls(
            connect_named_pipe(selected_pipe, timeout=timeout),
            default_timeout=timeout,
        )
        try:
            client.open_session(
                client_name="aviutl2-api",
                timeout=timeout,
            )
        except BridgeRemoteError as error:
            if error.code != "METHOD_NOT_FOUND":
                client.close()
                raise
        return client

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        """Call a deterministic bridge method and return its result object."""
        request_params = dict(params or {})
        if operation_id is not None:
            if not operation_id or len(operation_id.encode("utf-8")) > 128:
                raise ValueError(
                    "operation_id must be a non-empty UTF-8 string up to 128 bytes"
                )
            request_params["operation_id"] = operation_id
        elif self._session is not None and method in _MUTATION_METHODS:
            request_params["operation_id"] = (
                f"py-op-{next(self._operation_ids):016d}"
            )
        request_id = f"py-{next(self._request_ids):08d}"
        request = encode_request(request_id, method, request_params)
        response_payload = self._transport.exchange(
            request,
            timeout=self._default_timeout if timeout is None else timeout,
        )
        response: Response = decode_response(response_payload)
        if response.request_id != request_id:
            raise ConnectionError(
                "Live Bridge response id does not match the request id"
            )
        return response.result

    @property
    def session(self) -> SessionInfo | None:
        """Return the connection-bound bridge session, if supported."""
        return self._session

    def open_session(
        self,
        *,
        client_name: str = "aviutl2-api",
        timeout: float | None = None,
    ) -> SessionInfo:
        if not client_name or len(client_name.encode("utf-8")) > 128:
            raise ValueError("client_name must be a non-empty short UTF-8 string")
        session = SessionInfo.from_wire(
            self.call(
                "session.open",
                {"client_name": client_name},
                timeout=timeout,
            )
        )
        self._session = session
        return session

    def watch_events(
        self,
        *,
        after_sequence: int = 0,
        timeout_ms: int = 30_000,
        types: Sequence[str] | None = None,
    ) -> EventWatchResult:
        """Long-poll sequenced host events without occupying the SDK queue."""
        if (
            isinstance(after_sequence, bool)
            or after_sequence < 0
            or isinstance(timeout_ms, bool)
            or timeout_ms < 0
            or timeout_ms > 30_000
        ):
            raise ValueError("invalid event sequence or timeout")
        params: dict[str, Any] = {
            "after_sequence": after_sequence,
            "timeout_ms": timeout_ms,
        }
        if types is not None:
            if len(types) > 16 or any(not value for value in types):
                raise ValueError("types must contain at most 16 event names")
            params["types"] = list(types)
        return EventWatchResult.from_wire(
            self.call(
                "event.watch",
                params,
                timeout=max(
                    self._default_timeout,
                    timeout_ms / 1000.0 + 1.0,
                ),
            )
        )

    def hello(self, *, timeout: float | None = None) -> dict[str, Any]:
        return self.call("system.hello", timeout=timeout)

    def ping(self, *, timeout: float | None = None) -> bool:
        result = self.call("system.ping", timeout=timeout)
        pong = result.get("pong")
        if not isinstance(pong, bool):
            raise ConnectionError("Live Bridge returned an invalid ping result")
        return pong

    def get_capabilities(
        self,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self.call("system.get_capabilities", timeout=timeout)

    def get_project_info(
        self,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self.call("project.get_info", timeout=timeout)

    def get_current_scene(
        self,
        *,
        timeout: float | None = None,
    ) -> SceneInfo:
        return SceneInfo.from_wire(
            self.call("scene.get_current", timeout=timeout)
        )

    def update_current_scene(
        self,
        *,
        expected_revision: int,
        name: str | None = None,
        width: int | None = None,
        height: int | None = None,
        rate: int | None = None,
        scale: int | None = None,
        sample_rate: int | None = None,
        confirm_non_undoable: bool = False,
        timeout: float | None = None,
    ) -> SceneInfo:
        """Update current-scene settings that AviUtl2 cannot currently Undo."""
        if not confirm_non_undoable:
            raise ValueError("confirm_non_undoable=True is required")
        params: dict[str, Any] = {
            "expected_revision": expected_revision,
            "confirm_non_undoable": True,
        }
        for key, value in (
            ("name", name),
            ("width", width),
            ("height", height),
            ("rate", rate),
            ("scale", scale),
            ("sample_rate", sample_rate),
        ):
            if value is not None:
                params[key] = value
        return SceneInfo.from_wire(
            self.call("scene.update_current", params, timeout=timeout)
        )

    def history_undo(
        self,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Request Bridge-owned Undo when a future SDK exposes execution."""
        return self.call("history.undo", timeout=timeout)

    def history_redo(
        self,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Request Bridge-owned Redo when a future SDK exposes execution."""
        return self.call("history.redo", timeout=timeout)

    def get_effect_catalog(
        self,
        *,
        start: int = 0,
        count: int = 64,
        timeout: float | None = None,
    ) -> EffectCatalogPage:
        """Return one page of effects and setting items known by AviUtl2."""
        if (
            isinstance(start, bool)
            or isinstance(count, bool)
            or start < 0
            or count <= 0
        ):
            raise ValueError("start must be non-negative and count positive")
        return EffectCatalogPage.from_wire(
            self.call(
                "effect.catalog",
                {"start": start, "count": count},
                timeout=timeout,
            )
        )

    def get_font_catalog(
        self,
        *,
        start: int = 0,
        count: int = 128,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self.call(
            "font.catalog",
            {"start": start, "count": count},
            timeout=timeout,
        )

    def get_palette_catalog(
        self,
        *,
        start: int = 0,
        count: int = 128,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self.call(
            "palette.catalog",
            {"start": start, "count": count},
            timeout=timeout,
        )

    def get_module_catalog(
        self,
        *,
        start: int = 0,
        count: int = 128,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self.call(
            "module.catalog",
            {"start": start, "count": count},
            timeout=timeout,
        )

    def get_layers(
        self,
        *,
        start: int = 0,
        count: int = 128,
        timeout: float | None = None,
    ) -> LayerPage:
        """Return layer names, lock/enable state, and object counts."""
        if (
            isinstance(start, bool)
            or isinstance(count, bool)
            or start < 0
            or count <= 0
        ):
            raise ValueError("start must be non-negative and count positive")
        return LayerPage.from_wire(
            self.call(
                "project.get_layers",
                {"start": start, "count": count},
                timeout=timeout,
            )
        )

    def update_layer(
        self,
        *,
        expected_revision: int,
        layer: int,
        name: str | None | _Unchanged = _UNCHANGED,
        enabled: bool | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Change a layer name/visibility without ever clearing its lock."""
        if expected_revision <= 0 or layer < 0:
            raise ValueError(
                "expected_revision must be positive and layer non-negative"
            )
        params: dict[str, Any] = {
            "expected_revision": expected_revision,
            "layer": layer,
        }
        if not isinstance(name, _Unchanged):
            params["name"] = name
        if enabled is not None:
            params["enabled"] = enabled
        if len(params) == 2:
            raise ValueError("at least one layer property is required")
        return self.call("layer.update", params, timeout=timeout)

    def get_snapshot(
        self,
        *,
        offset: int = 0,
        count: int = 4096,
        layer_start: int | None = None,
        layer_end: int | None = None,
        frame_start: int | None = None,
        frame_end: int | None = None,
        object_ids: Sequence[str] | None = None,
        has_alias: bool | None = None,
        include_alias: bool = True,
        timeout: float | None = None,
    ) -> ProjectSnapshot:
        """Capture revision-scoped references for the current scene."""
        integer_values = (
            offset,
            count,
            layer_start,
            layer_end,
            frame_start,
            frame_end,
        )
        if any(
            value is not None
            and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            )
            for value in integer_values
        ) or count == 0:
            raise ValueError("snapshot ranges must be non-negative and count positive")
        params: dict[str, Any] = {
            "offset": offset,
            "count": count,
            "include_alias": include_alias,
        }
        for name, value in (
            ("layer_start", layer_start),
            ("layer_end", layer_end),
            ("frame_start", frame_start),
            ("frame_end", frame_end),
        ):
            if value is not None:
                params[name] = value
        if object_ids is not None:
            params["object_ids"] = list(object_ids)
        if has_alias is not None:
            params["has_alias"] = has_alias
        return ProjectSnapshot.from_wire(
            self.call("project.get_snapshot", params, timeout=timeout)
        )

    @staticmethod
    def _media_path(file: str | PathLike[str]) -> str:
        path = Path(file).expanduser().resolve()
        return str(path)

    def probe_media(
        self,
        file: str | PathLike[str],
        *,
        timeout: float | None = None,
    ) -> MediaProbe:
        """Ask AviUtl2 itself whether a file is readable and inspect it."""
        return MediaProbe.from_wire(
            self.call(
                "media.probe",
                {"file": self._media_path(file)},
                timeout=timeout,
            )
        )

    def get_media_inventory(
        self,
        *,
        timeout: float | None = None,
    ) -> MediaInventory:
        """Inspect every native file item and report missing/duplicate media."""
        return MediaInventory.from_wire(
            self.call("media.inventory", timeout=timeout)
        )

    def relink_media(
        self,
        *,
        expected_revision: int,
        replacements: Mapping[
            str | PathLike[str],
            str | PathLike[str],
        ],
        operation_id: str | None = None,
        timeout: float | None = None,
    ) -> MediaRelinkReceipt:
        """Replace matching native file items atomically in one Undo unit."""
        if expected_revision <= 0 or not replacements:
            raise ValueError(
                "a current revision and at least one replacement are required"
            )
        if len(replacements) > 256:
            raise ValueError("at most 256 media replacements are supported")
        values = [
            {
                "from": self._media_path(source),
                "to": self._media_path(destination),
            }
            for source, destination in replacements.items()
        ]
        return MediaRelinkReceipt.from_wire(
            self.call(
                "media.relink",
                {
                    "expected_revision": expected_revision,
                    "replacements": values,
                },
                operation_id=operation_id,
                timeout=timeout,
            )
        )

    def create_from_media_file(
        self,
        file: str | PathLike[str],
        *,
        layer: int,
        frame: int,
        length: int = 0,
        timeout: float | None = None,
    ) -> CreatedMediaObject:
        """Create media through AviUtl2's native loader.

        A length of zero delegates duration and placement adjustment to AviUtl2.
        """
        if layer < 0 or frame < 0 or length < 0:
            raise ValueError("layer, frame, and length must be non-negative")
        result = self.call(
            "object.create_from_media_file",
            {
                "file": self._media_path(file),
                "layer": layer,
                "frame": frame,
                "length": length,
            },
            timeout=timeout,
        )
        return CreatedMediaObject.from_wire(result)

    def add_image(
        self,
        file: str | PathLike[str],
        *,
        layer: int,
        frame: int,
        length: int = 0,
        timeout: float | None = None,
    ) -> CreatedMediaObject:
        """Place an image with AviUtl2's native media loader."""
        return self.create_from_media_file(
            file,
            layer=layer,
            frame=frame,
            length=length,
            timeout=timeout,
        )

    def add_video(
        self,
        file: str | PathLike[str],
        *,
        layer: int,
        frame: int,
        length: int = 0,
        timeout: float | None = None,
    ) -> CreatedMediaObject:
        """Place a video with AviUtl2's native media loader."""
        return self.create_from_media_file(
            file,
            layer=layer,
            frame=frame,
            length=length,
            timeout=timeout,
        )

    def add_audio(
        self,
        file: str | PathLike[str],
        *,
        layer: int,
        frame: int,
        length: int = 0,
        timeout: float | None = None,
    ) -> CreatedMediaObject:
        """Place audio with AviUtl2's native media loader."""
        return self.create_from_media_file(
            file,
            layer=layer,
            frame=frame,
            length=length,
            timeout=timeout,
        )

    def inspect_object(
        self,
        obj: SnapshotObject,
        *,
        sample_frame: int | None = None,
        timeout: float | None = None,
    ) -> ObjectInspection:
        """Inspect effects, typed items, locks, and native track metadata."""
        if sample_frame is not None and sample_frame < 0:
            raise ValueError("sample_frame must be non-negative")
        params = obj.target_params()
        if sample_frame is not None:
            params["sample_frame"] = sample_frame
        return ObjectInspection.from_wire(
            self.call("object.inspect", params, timeout=timeout)
        )

    def render_frame(
        self,
        frame: int,
        *,
        output_path: str | PathLike[str] | None = None,
        overwrite: bool = False,
        timeout: float = 30.0,
    ) -> RenderedFrame:
        """Render the current scene with AviUtl2 and retrieve a verified PNG."""
        if frame < 0:
            raise ValueError("frame must be non-negative")
        metadata = self.call(
            "frame.render",
            {"frame": frame},
            timeout=timeout,
        )
        capture_id = metadata.get("capture_id")
        byte_size = metadata.get("byte_size")
        chunk_count = metadata.get("chunk_count")
        width = metadata.get("width")
        height = metadata.get("height")
        scene_id = metadata.get("scene_id")
        revision = metadata.get("revision")
        digest = metadata.get("sha256")
        returned_frame = metadata.get("frame")
        native_renderer = metadata.get("native_renderer")
        integer_values = (
            byte_size,
            chunk_count,
            width,
            height,
            scene_id,
            revision,
            returned_frame,
        )
        if (
            not isinstance(capture_id, str)
            or not capture_id
            or any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in integer_values
            )
            or not isinstance(digest, str)
            or len(digest) != 64
            or native_renderer is not True
        ):
            raise ConnectionError(
                "Live Bridge returned invalid frame render metadata"
            )
        assert isinstance(byte_size, int)
        assert isinstance(chunk_count, int)
        assert isinstance(width, int)
        assert isinstance(height, int)
        assert isinstance(scene_id, int)
        assert isinstance(revision, int)
        assert isinstance(returned_frame, int)
        assert isinstance(digest, str)
        if (
            returned_frame != frame
            or byte_size <= 0
            or chunk_count <= 0
            or width <= 0
            or height <= 0
            or revision <= 0
        ):
            raise ConnectionError(
                "Live Bridge returned invalid frame render metadata"
            )

        chunks: list[bytes] = []
        received = 0
        try:
            for index in range(chunk_count):
                chunk = self.call(
                    "frame.read_chunk",
                    {"capture_id": capture_id, "index": index},
                    timeout=timeout,
                )
                encoded = chunk.get("data_base64")
                data_size = chunk.get("data_size")
                byte_offset = chunk.get("byte_offset")
                returned_index = chunk.get("index")
                eof = chunk.get("eof")
                if (
                    not isinstance(encoded, str)
                    or not isinstance(data_size, int)
                    or isinstance(data_size, bool)
                    or not isinstance(byte_offset, int)
                    or isinstance(byte_offset, bool)
                    or returned_index != index
                    or byte_offset != received
                    or not isinstance(eof, bool)
                    or eof != (index + 1 == chunk_count)
                ):
                    raise ConnectionError(
                        "Live Bridge returned invalid frame chunk metadata"
                    )
                try:
                    decoded = base64.b64decode(encoded, validate=True)
                except ValueError as error:
                    raise ConnectionError(
                        "Live Bridge returned invalid frame chunk base64"
                    ) from error
                if len(decoded) != data_size:
                    raise ConnectionError(
                        "Live Bridge frame chunk size does not match"
                    )
                chunks.append(decoded)
                received += len(decoded)
        finally:
            self.call(
                "frame.release",
                {"capture_id": capture_id},
                timeout=timeout,
            )

        png = b"".join(chunks)
        if (
            len(png) != byte_size
            or not png.startswith(b"\x89PNG\r\n\x1a\n")
            or hashlib.sha256(png).hexdigest() != digest
        ):
            raise ConnectionError(
                "Live Bridge rendered PNG failed integrity validation"
            )
        result = RenderedFrame(
            frame=frame,
            width=width,
            height=height,
            scene_id=scene_id,
            revision=revision,
            sha256=digest,
            png=png,
        )
        if output_path is not None:
            result.save(Path(output_path), overwrite=overwrite)
        return result

    def render_frames(
        self,
        frames: Sequence[int],
        *,
        expected_revision: int | None = None,
        timeout_per_frame: float = 30.0,
    ) -> tuple[RenderedFrame, ...]:
        """Render up to 64 native frames and require one stable revision."""
        selected = tuple(dict.fromkeys(frames))
        if (
            not selected
            or len(selected) > 64
            or any(
                not isinstance(frame, int)
                or isinstance(frame, bool)
                or frame < 0
                for frame in selected
            )
        ):
            raise ValueError(
                "frames must contain 1..64 unique non-negative integers"
            )
        rendered = tuple(
            self.render_frame(frame, timeout=timeout_per_frame)
            for frame in selected
        )
        revisions = {frame.revision for frame in rendered}
        if len(revisions) != 1:
            raise ConnectionError(
                "the project changed during multi-frame rendering"
            )
        revision = rendered[0].revision
        if (
            expected_revision is not None
            and revision != expected_revision
        ):
            raise ConnectionError(
                "rendered frames do not match expected_revision"
            )
        return rendered

    def render_review_contact_sheet(
        self,
        *,
        snapshot: ProjectSnapshot | None = None,
        columns: int = 4,
        thumbnail_width: int = 320,
        boundary_padding: int = 1,
        include_midpoints: bool = True,
        max_frames: int = 64,
        output_path: str | PathLike[str] | None = None,
        overwrite: bool = False,
        timeout_per_frame: float = 30.0,
    ) -> ContactSheet:
        """Auto-sample edit boundaries and build an in-memory contact sheet."""
        current = snapshot or self.get_snapshot(
            include_alias=False
        )
        frames = review_sample_frames(
            current,
            boundary_padding=boundary_padding,
            include_midpoints=include_midpoints,
            max_frames=max_frames,
        )
        sheet = make_contact_sheet(
            self.render_frames(
                frames,
                expected_revision=current.revision,
                timeout_per_frame=timeout_per_frame,
            ),
            columns=columns,
            thumbnail_width=thumbnail_width,
        )
        if output_path is not None:
            sheet.save(output_path, overwrite=overwrite)
        return sheet

    def render_audio(
        self,
        *,
        frame_start: int,
        frame_end: int,
        expected_revision: int | None = None,
        output_path: str | PathLike[str] | None = None,
        overwrite: bool = False,
        timeout: float = 120.0,
    ) -> RenderedAudio:
        """Render revision-bound native stereo float PCM from AviUtl2."""
        if frame_start < 0 or frame_end < frame_start:
            raise ValueError("frame_start/frame_end form an invalid range")
        params: dict[str, Any] = {
            "frame_start": frame_start,
            "frame_end": frame_end,
        }
        if expected_revision is not None:
            if expected_revision <= 0:
                raise ValueError("expected_revision must be positive")
            params["expected_revision"] = expected_revision
        metadata = self.call("audio.render", params, timeout=timeout)
        capture_id = metadata.get("capture_id")
        integer_names = (
            "byte_size",
            "chunk_count",
            "sample_rate",
            "sample_count",
            "scene_id",
            "revision",
            "frame_start",
            "frame_end",
        )
        integer_values = tuple(metadata.get(name) for name in integer_names)
        digest = metadata.get("sha256")
        if (
            not isinstance(capture_id, str)
            or not capture_id
            or any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in integer_values
            )
            or not isinstance(digest, str)
            or len(digest) != 64
            or metadata.get("format") != "f32le"
            or metadata.get("channels") != 2
            or metadata.get("native_renderer") is not True
        ):
            raise ConnectionError(
                "Live Bridge returned invalid audio render metadata"
            )
        (
            byte_size,
            chunk_count,
            sample_rate,
            sample_count,
            scene_id,
            revision,
            returned_start,
            returned_end,
        ) = integer_values
        assert isinstance(byte_size, int)
        assert isinstance(chunk_count, int)
        assert isinstance(sample_rate, int)
        assert isinstance(sample_count, int)
        assert isinstance(scene_id, int)
        assert isinstance(revision, int)
        assert isinstance(returned_start, int)
        assert isinstance(returned_end, int)
        if (
            byte_size <= 0
            or byte_size != sample_count * 2 * 4
            or chunk_count <= 0
            or sample_rate <= 0
            or revision <= 0
            or returned_start != frame_start
            or returned_end != frame_end
        ):
            raise ConnectionError(
                "Live Bridge returned inconsistent audio render metadata"
            )

        chunks: list[bytes] = []
        received = 0
        try:
            for index in range(chunk_count):
                chunk = self.call(
                    "audio.read_chunk",
                    {"capture_id": capture_id, "index": index},
                    timeout=timeout,
                )
                encoded = chunk.get("data_base64")
                data_size = chunk.get("data_size")
                byte_offset = chunk.get("byte_offset")
                if (
                    not isinstance(encoded, str)
                    or not isinstance(data_size, int)
                    or isinstance(data_size, bool)
                    or not isinstance(byte_offset, int)
                    or isinstance(byte_offset, bool)
                    or chunk.get("index") != index
                    or byte_offset != received
                    or chunk.get("eof") != (index + 1 == chunk_count)
                ):
                    raise ConnectionError(
                        "Live Bridge returned invalid audio chunk metadata"
                    )
                try:
                    decoded = base64.b64decode(encoded, validate=True)
                except ValueError as error:
                    raise ConnectionError(
                        "Live Bridge returned invalid audio chunk base64"
                    ) from error
                if len(decoded) != data_size:
                    raise ConnectionError(
                        "Live Bridge audio chunk size does not match"
                    )
                chunks.append(decoded)
                received += len(decoded)
        finally:
            self.call(
                "audio.release",
                {"capture_id": capture_id},
                timeout=timeout,
            )
        pcm = b"".join(chunks)
        if (
            len(pcm) != byte_size
            or hashlib.sha256(pcm).hexdigest() != digest
        ):
            raise ConnectionError(
                "Live Bridge rendered PCM failed integrity validation"
            )
        result = RenderedAudio(
            frame_start=frame_start,
            frame_end=frame_end,
            sample_rate=sample_rate,
            sample_count=sample_count,
            scene_id=scene_id,
            revision=revision,
            sha256=digest,
            pcm_f32le=pcm,
        )
        if output_path is not None:
            result.save_pcm(output_path, overwrite=overwrite)
        return result

    def _find_inspected_item(
        self,
        obj: SnapshotObject,
        *,
        effect: str,
        item: str,
        timeout: float | None,
    ) -> ItemInspection:
        inspection = self.inspect_object(obj, timeout=timeout)
        effects = [
            value for value in inspection.effects if value.selector == effect
        ]
        if len(effects) != 1:
            raise ValueError(
                f"effect selector {effect!r} did not identify one effect"
            )
        items = [value for value in effects[0].items if value.name == item]
        if len(items) != 1:
            raise ValueError(f"item {item!r} did not identify one setting")
        return items[0]

    @staticmethod
    def _validate_property_value(
        inspected: ItemInspection,
        value: str | int | float | bool | StaticValue | AnimatedValue,
    ) -> None:
        item_type = inspected.type
        if item_type == "integer" and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            raise TypeError("integer items require an int")
        if item_type == "number" and (
            not isinstance(value, (int, float, StaticValue, AnimatedValue))
            or isinstance(value, bool)
        ):
            raise TypeError(
                "number items require a number, StaticValue, or AnimatedValue"
            )
        if item_type == "check" and not isinstance(value, bool):
            raise TypeError("check items require a bool")
        if item_type in {"text", "string", "font", "figure", "folder"} and (
            not isinstance(value, str)
        ):
            raise TypeError(f"{item_type} items require a string")
        if item_type == "file" and not isinstance(value, str):
            raise TypeError("file items require an absolute path string")
        if item_type == "color":
            if not isinstance(value, str):
                raise TypeError("color items require a hexadecimal string")
            color = value.removeprefix("#")
            if len(color) not in {6, 8} or any(
                character.lower() not in "0123456789abcdef"
                for character in color
            ):
                raise ValueError(
                    "color items require six or eight hexadecimal digits"
                )

    def set_property(
        self,
        obj: SnapshotObject,
        *,
        effect: str,
        item: str,
        value: str | int | float | bool | StaticValue | AnimatedValue,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Inspect and type-check one native setting before changing it."""
        inspected = self._find_inspected_item(
            obj,
            effect=effect,
            item=item,
            timeout=timeout,
        )
        self._validate_property_value(inspected, value)
        return self.set_item(
            obj,
            effect=effect,
            item=item,
            value=value,
            timeout=timeout,
        )

    def set_animation(
        self,
        obj: SnapshotObject,
        *,
        effect: str,
        item: str,
        value: AnimatedValue,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Set native Alias animation data on a verified track item."""
        inspected = self._find_inspected_item(
            obj,
            effect=effect,
            item=item,
            timeout=timeout,
        )
        if inspected.track is None:
            raise TypeError("the requested item is not an animatable track")
        self._validate_property_value(inspected, value)
        return self.set_item(
            obj,
            effect=effect,
            item=item,
            value=value,
            timeout=timeout,
        )

    def set_media_file(
        self,
        obj: SnapshotObject,
        file: str | PathLike[str],
        *,
        effect: str | None = None,
        item: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Replace one unambiguous native file item after strict probing."""
        absolute_file = self._media_path(file)
        probe = self.probe_media(absolute_file, timeout=timeout)
        if not probe.exists or not probe.regular_file or not probe.readable:
            raise ValueError("AviUtl2 cannot read the replacement media file")
        inspection = self.inspect_object(obj, timeout=timeout)
        candidates = [
            (candidate_effect.selector, candidate_item.name)
            for candidate_effect in inspection.effects
            for candidate_item in candidate_effect.items
            if candidate_item.type == "file"
            and (effect is None or candidate_effect.selector == effect)
            and (item is None or candidate_item.name == item)
        ]
        if len(candidates) != 1:
            raise ValueError(
                "media replacement must identify exactly one native file item"
            )
        effect_selector, item_name = candidates[0]
        return self.set_item(
            obj,
            effect=effect_selector,
            item=item_name,
            value=absolute_file,
            timeout=timeout,
        )

    def set_playback_rate(
        self,
        obj: SnapshotObject,
        rate: float,
        *,
        effect: str | None = None,
        duration_mode: Literal["keep_timeline"] = "keep_timeline",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Set a fixed media playback multiplier without changing clip length.

        AviUtl2 stores playback speed as a percentage, while this API accepts
        a multiplier: ``2.0`` means 200% and ``0.5`` means 50%. Adjusting the
        timeline duration to preserve the source range requires the planned
        replacement operation and is intentionally not guessed here.
        """
        if (
            not isinstance(rate, (int, float))
            or isinstance(rate, bool)
            or not math.isfinite(rate)
            or rate <= 0
        ):
            raise ValueError("rate must be a finite positive multiplier")
        if duration_mode != "keep_timeline":
            raise NotImplementedError(
                "only duration_mode='keep_timeline' is currently supported"
            )

        inspection = self.inspect_object(obj, timeout=timeout)
        candidates = [
            (candidate_effect.selector, candidate_item)
            for candidate_effect in inspection.effects
            for candidate_item in candidate_effect.items
            if candidate_effect.name in {"動画ファイル", "音声ファイル"}
            and candidate_item.name == "再生速度"
            and (effect is None or candidate_effect.selector == effect)
        ]
        if len(candidates) != 1:
            raise ValueError(
                "playback rate must identify exactly one video or audio "
                "media effect"
            )
        effect_selector, inspected_item = candidates[0]
        raw_percent = float(rate) * 100.0
        if not math.isfinite(raw_percent):
            raise ValueError("rate is too large")
        self._validate_property_value(inspected_item, raw_percent)
        result = self.set_item(
            obj,
            effect=effect_selector,
            item="再生速度",
            value=raw_percent,
            timeout=timeout,
        )
        return {
            **result,
            "playback_rate": float(rate),
            "raw_percent": raw_percent,
            "duration_mode": duration_mode,
        }

    def validate_batch(
        self,
        commands: Sequence[CreateFromAliasCommand],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Validate command structure and requested timeline placement."""
        return self.call(
            "batch.validate",
            {"commands": [command.to_wire() for command in commands]},
            timeout=timeout,
        )

    def validate_transaction(
        self,
        *,
        expected_revision: int,
        commands: Sequence[TimelineTransactionCommand],
        timeout: float | None = None,
    ) -> TransactionReceipt:
        if expected_revision <= 0 or not commands:
            raise ValueError("a current revision and commands are required")
        return TransactionReceipt.from_wire(
            self.call(
                "timeline.transaction.validate",
                {
                    "expected_revision": expected_revision,
                    "commands": [value.value for value in commands],
                },
                timeout=timeout,
            )
        )

    def apply_transaction(
        self,
        *,
        expected_revision: int,
        commands: Sequence[TimelineTransactionCommand],
        operation_id: str | None = None,
        timeout: float | None = None,
    ) -> TransactionReceipt:
        """Apply preflighted metadata/move/delete edits in one Undo unit."""
        if expected_revision <= 0 or not commands:
            raise ValueError("a current revision and commands are required")
        return TransactionReceipt.from_wire(
            self.call(
                "timeline.transaction.apply",
                {
                    "expected_revision": expected_revision,
                    "commands": [value.value for value in commands],
                },
                operation_id=operation_id,
                timeout=timeout,
            )
        )

    def move_group(
        self,
        group: ObjectGroup,
        *,
        frame_delta: int = 0,
        layer_delta: int = 0,
        timeout: float | None = None,
    ) -> TransactionReceipt:
        if frame_delta == 0 and layer_delta == 0:
            raise ValueError("at least one group delta must be non-zero")
        commands = [
            TimelineTransactionCommand.move(
                obj,
                layer=obj.layer + layer_delta,
                frame=obj.frame_start + frame_delta,
            )
            for obj in group.objects
        ]
        if any(
            command.value["layer"] < 0 or command.value["frame"] < 0
            for command in commands
        ):
            raise ValueError("the group move would leave the timeline")
        return self.apply_transaction(
            expected_revision=group.revision,
            commands=commands,
            timeout=timeout,
        )

    def delete_group(
        self,
        group: ObjectGroup,
        *,
        timeout: float | None = None,
    ) -> TransactionReceipt:
        return self.apply_transaction(
            expected_revision=group.revision,
            commands=[
                TimelineTransactionCommand.delete(obj)
                for obj in group.objects
            ],
            timeout=timeout,
        )

    def shift_after(
        self,
        *,
        expected_revision: int,
        frame: int,
        delta: int,
        group: ObjectGroup | None = None,
        layer_start: int | None = None,
        layer_end: int | None = None,
        timeout: float | None = None,
    ) -> TransactionReceipt:
        params: dict[str, Any] = {
            "expected_revision": expected_revision,
            "frame": frame,
            "delta": delta,
        }
        if group is not None:
            if group.revision != expected_revision:
                raise ValueError("group revision does not match expected_revision")
            params["object_ids"] = list(group.object_ids)
        if layer_start is not None:
            params["layer_start"] = layer_start
        if layer_end is not None:
            params["layer_end"] = layer_end
        return TransactionReceipt.from_wire(
            self.call("timeline.shift_after", params, timeout=timeout)
        )

    def ripple_insert(
        self,
        *,
        expected_revision: int,
        frame: int,
        length: int,
        group: ObjectGroup | None = None,
        timeout: float | None = None,
    ) -> TransactionReceipt:
        params: dict[str, Any] = {
            "expected_revision": expected_revision,
            "frame": frame,
            "length": length,
        }
        if group is not None:
            if group.revision != expected_revision:
                raise ValueError("group revision does not match expected_revision")
            params["object_ids"] = list(group.object_ids)
        return TransactionReceipt.from_wire(
            self.call("timeline.ripple_insert", params, timeout=timeout)
        )

    def ripple_delete(
        self,
        *,
        expected_revision: int,
        frame_start: int,
        frame_end: int,
        group: ObjectGroup | None = None,
        timeout: float | None = None,
    ) -> TransactionReceipt:
        params: dict[str, Any] = {
            "expected_revision": expected_revision,
            "frame_start": frame_start,
            "frame_end": frame_end,
        }
        if group is not None:
            if group.revision != expected_revision:
                raise ValueError("group revision does not match expected_revision")
            params["object_ids"] = list(group.object_ids)
        return TransactionReceipt.from_wire(
            self.call("timeline.ripple_delete", params, timeout=timeout)
        )

    def close_gap(
        self,
        *,
        expected_revision: int,
        frame_start: int,
        frame_end: int,
        group: ObjectGroup | None = None,
        timeout: float | None = None,
    ) -> TransactionReceipt:
        params: dict[str, Any] = {
            "expected_revision": expected_revision,
            "frame_start": frame_start,
            "frame_end": frame_end,
        }
        if group is not None:
            if group.revision != expected_revision:
                raise ValueError("group revision does not match expected_revision")
            params["object_ids"] = list(group.object_ids)
        return TransactionReceipt.from_wire(
            self.call("timeline.close_gap", params, timeout=timeout)
        )

    def apply_batch(
        self,
        commands: Sequence[CreateFromAliasCommand],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Apply commands in one AviUtl2 edit/Undo section."""
        return self.call(
            "batch.apply",
            {"commands": [command.to_wire() for command in commands]},
            timeout=timeout,
        )

    def create_from_alias(
        self,
        alias: str,
        *,
        layer: int,
        frame: int,
        length: int,
        client_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Create one object using AviUtl2's native Alias parser."""
        command = CreateFromAliasCommand(
            alias=alias,
            layer=layer,
            frame=frame,
            length=length,
            client_id=client_id,
        )
        params = command.to_wire()
        params.pop("op")
        return self.call(
            "object.create_from_alias",
            params,
            timeout=timeout,
        )

    def duplicate_object(
        self,
        obj: SnapshotObject,
        *,
        layer: int,
        frame: int,
        timeout: float | None = None,
    ) -> SnapshotObject:
        """Clone an object through its host Alias and verify the created clip.

        The destination must be empty. The returned object belongs to a fresh
        snapshot and can immediately be used for another stale-safe operation.
        """
        if obj.api_locked:
            raise PermissionError(
                "an API-locked object cannot be duplicated externally"
            )
        if layer < 0 or frame < 0:
            raise ValueError("layer and frame must be non-negative")
        if obj.alias is None:
            raise ValueError(
                "duplicate_object requires a snapshot captured with include_alias=True"
            )
        self.create_from_alias(
            obj.alias,
            layer=layer,
            frame=frame,
            length=obj.duration_frames,
            timeout=timeout,
        )
        snapshot = self.get_snapshot(timeout=timeout)
        expected_end = frame + obj.duration_frames - 1
        candidates = [
            value
            for value in snapshot.objects
            if value.layer == layer
            and value.frame_start == frame
            and value.frame_end == expected_end
            and value.alias == obj.alias
        ]
        if len(candidates) != 1:
            raise ConnectionError(
                "AviUtl2 accepted the clone but its snapshot verification failed"
            )
        return candidates[0]

    def split_media(
        self,
        obj: SnapshotObject,
        frame: int,
        *,
        timeout: float | None = None,
    ) -> MediaSplit:
        """Split a basic video/audio clip into independent left/right clips.

        AviUtl2 performs the replacement in one Undo section. Clips with an
        animated playback position/speed or multiple sections are refused.
        """
        if obj.api_locked:
            raise PermissionError(
                "an API-locked object cannot be split externally"
            )
        if frame <= obj.frame_start or frame > obj.frame_end:
            raise ValueError("frame must be strictly inside the object")
        params = obj.target_params()
        params["frame"] = frame
        return MediaSplit.from_wire(
            self.call("object.split_media", params, timeout=timeout)
        )

    def set_duration(
        self,
        obj: SnapshotObject,
        duration: int,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Replace one safe single-section object with a verified duration."""
        if isinstance(duration, bool) or duration <= 0:
            raise ValueError("duration must be a positive frame count")
        params = obj.target_params()
        params["duration"] = duration
        return self.call("object.set_duration", params, timeout=timeout)

    def trim_media(
        self,
        obj: SnapshotObject,
        *,
        frame_start: int,
        frame_end: int,
        source_position: float | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Trim a fixed positive-speed native media clip in one Undo unit."""
        if frame_start < 0 or frame_end < frame_start:
            raise ValueError("frame_start/frame_end form an invalid range")
        params = obj.target_params()
        params.update({"frame_start": frame_start, "frame_end": frame_end})
        if source_position is not None:
            if not math.isfinite(source_position) or source_position < 0:
                raise ValueError("source_position must be finite and non-negative")
            params["source_position"] = source_position
        return self.call("media.trim", params, timeout=timeout)

    def reorder_effects(
        self,
        obj: SnapshotObject,
        selectors: Sequence[str],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Reorder every effect with verified Alias replacement."""
        if not selectors or len(set(selectors)) != len(selectors):
            raise ValueError("selectors must be a non-empty unique complete order")
        params = obj.target_params()
        params["selectors"] = list(selectors)
        return self.call("object.effect.reorder", params, timeout=timeout)

    def create_object(
        self,
        obj: TimelineObject,
        *,
        client_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Serialize an existing Python model and create it in AviUtl2."""
        command = CreateFromAliasCommand.from_object(
            obj,
            client_id=client_id,
        )
        return self.apply_batch([command], timeout=timeout)

    def add_text(
        self,
        text: str,
        *,
        layer: int,
        frame: int,
        length: int,
        x: float = 0.0,
        y: float = 0.0,
        size: float = 34.0,
        color: str = "ffffff",
        client_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Create an AviUtl2-native text object in the open project."""
        obj = make_text_object(
            text,
            layer=layer,
            frame=frame,
            length=length,
            x=x,
            y=y,
            size=size,
            color=color,
        )
        return self.create_object(
            obj,
            client_id=client_id,
            timeout=timeout,
        )

    def add_subtitles(
        self,
        source: Sequence[SubtitleCue] | str | PathLike[str],
        *,
        layer_policy: SubtitleLayerPolicy,
        style: SubtitleStyle | None = None,
        language: str | None = None,
        encoding: str = "utf-8-sig",
        timeout: float | None = None,
    ) -> SubtitleBatchResult:
        """Parse/place subtitle cues and return fresh native object references."""
        if isinstance(source, (str, PathLike)):
            cues = load_subtitles(
                source,
                language=language,
                encoding=encoding,
            )
        else:
            cues = tuple(source)
            if language is not None:
                cues = tuple(
                    SubtitleCue(
                        cue.start_seconds,
                        cue.end_seconds,
                        cue.text,
                        cue.speaker,
                        cue.language or language,
                    )
                    for cue in cues
                )
        if not cues:
            raise ValueError("at least one subtitle cue is required")
        if len(cues) > 128:
            raise ValueError(
                "one Undo-grouped subtitle batch supports at most 128 cues"
            )
        selected_style = style or SubtitleStyle()
        scene = self.get_current_scene(timeout=timeout)
        before = self.get_snapshot(
            include_alias=False,
            timeout=timeout,
        )
        if before.revision != scene.revision:
            raise ConnectionError(
                "the project changed while subtitle placement was prepared"
            )
        placements = assign_subtitle_layers(
            cues,
            rate=scene.rate,
            scale=scene.scale,
            policy=layer_policy,
            occupied=before.objects,
        )
        commands = [
            CreateFromAliasCommand.from_object(
                make_text_object(
                    selected_style.text_for(placement.cue),
                    layer=placement.layer,
                    frame=placement.frame_start,
                    length=(
                        placement.frame_end
                        - placement.frame_start
                        + 1
                    ),
                    x=selected_style.x,
                    y=selected_style.y,
                    size=selected_style.size,
                    color=selected_style.color_for(
                        placement.cue
                    ),
                ),
                client_id=placement.client_id,
            )
            for placement in placements
        ]
        receipt = self.apply_batch(commands, timeout=timeout)
        if (
            receipt.get("applied_count") != len(commands)
            or receipt.get("undo_grouped") is not True
        ):
            raise ConnectionError(
                "Live Bridge returned an invalid subtitle batch receipt"
            )
        after = self.get_snapshot(
            include_alias=False,
            timeout=timeout,
        )
        created: list[SnapshotObject] = []
        for placement in placements:
            matches = [
                obj
                for obj in after.objects
                if obj.layer == placement.layer
                and obj.frame_start == placement.frame_start
                and obj.frame_end == placement.frame_end
            ]
            if len(matches) != 1:
                raise ConnectionError(
                    "subtitle placement could not be verified in a fresh snapshot"
                )
            created.append(matches[0])
        return SubtitleBatchResult(
            previous_revision=before.revision,
            revision=after.revision,
            placements=placements,
            objects=tuple(created),
            undo_grouped=True,
        )

    def set_item(
        self,
        obj: SnapshotObject,
        *,
        effect: str,
        item: str,
        value: str | int | float | bool | StaticValue | AnimatedValue,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Update one setting using a current snapshot object reference."""
        params = obj.target_params()
        params.update(ItemUpdate(effect, item, value).to_wire())
        return self.call("object.set_item", params, timeout=timeout)

    def add_effect(
        self,
        obj: SnapshotObject,
        effect: str,
        *,
        items: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Append an effect and optional initial raw values in one Undo unit."""
        if not effect or "\r" in effect or "\n" in effect or "\x00" in effect:
            raise ValueError("effect must be a non-empty single-line name")
        params = obj.target_params()
        params["effect"] = effect
        if items is not None:
            if not items:
                raise ValueError("items must not be empty")
            if any(
                not isinstance(name, str)
                or not name
                or not isinstance(value, str)
                for name, value in items.items()
            ):
                raise TypeError("items must map non-empty names to raw strings")
            params["items"] = dict(items)
        return self.call("object.effect.add", params, timeout=timeout)

    def apply_common_effect(
        self,
        obj: SnapshotObject,
        semantic: EffectSemantic,
        values: Mapping[str, EffectValue],
        *,
        effect_name: str | None = None,
        timeout: float | None = None,
    ) -> EffectApplication:
        """Apply transition/mask/crop/chroma/audio helpers via live catalog."""
        return apply_common_effect(
            self,
            obj,
            semantic,
            values,
            effect_name=effect_name,
            timeout=timeout,
        )

    def set_effect_enabled(
        self,
        obj: SnapshotObject,
        selector: str,
        enabled: bool,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not selector or not isinstance(enabled, bool):
            raise ValueError("selector and a boolean enabled value are required")
        params = obj.target_params()
        params.update({"selector": selector, "enabled": enabled})
        return self.call("object.effect.set_enabled", params, timeout=timeout)

    def delete_effect(
        self,
        obj: SnapshotObject,
        selector: str,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Delete an effect using the selector returned by inspect_object()."""
        if (
            not selector
            or "\r" in selector
            or "\n" in selector
            or "\x00" in selector
        ):
            raise ValueError("selector must be a non-empty single-line name")
        params = obj.target_params()
        params["selector"] = selector
        return self.call("object.effect.delete", params, timeout=timeout)

    def set_items(
        self,
        obj: SnapshotObject,
        updates: Sequence[ItemUpdate],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Update several settings in one AviUtl2 Undo unit."""
        if not updates:
            raise ValueError("updates must not be empty")
        params = obj.target_params()
        params["items"] = [update.to_wire() for update in updates]
        return self.call("object.set_items", params, timeout=timeout)

    def set_object_name(
        self,
        obj: SnapshotObject,
        name: str | None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Set a timeline label; null restores AviUtl2's default label."""
        if name is not None and not isinstance(name, str):
            raise TypeError("name must be a string or None")
        params = obj.target_params()
        params["name"] = name
        return self.call("object.set_name", params, timeout=timeout)

    def list_sections(
        self,
        obj: SnapshotObject,
        *,
        timeout: float | None = None,
    ) -> ObjectSections:
        return ObjectSections.from_wire(
            self.call(
                "object.section.list",
                obj.target_params(),
                timeout=timeout,
            )
        )

    def create_section(
        self,
        obj: SnapshotObject,
        frame: int,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if frame <= obj.frame_start or frame > obj.frame_end:
            raise ValueError("frame must be a new boundary inside the object")
        params = obj.target_params()
        params["frame"] = frame
        return self.call("object.section.create", params, timeout=timeout)

    def delete_section(
        self,
        obj: SnapshotObject,
        section: int,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if section <= 0:
            raise ValueError("section must identify a middle boundary")
        params = obj.target_params()
        params["section"] = section
        return self.call("object.section.delete", params, timeout=timeout)

    def move_section(
        self,
        obj: SnapshotObject,
        section: int,
        frame: int,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if section <= 0 or frame < 0:
            raise ValueError("section must be positive and frame non-negative")
        params = obj.target_params()
        params.update({"section": section, "frame": frame})
        return self.call("object.section.move", params, timeout=timeout)

    def set_text(
        self,
        obj: SnapshotObject,
        text: str,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Change the text of an AviUtl2 text object."""
        return self.set_item(
            obj,
            effect="テキスト",
            item="テキスト",
            value=text,
            timeout=timeout,
        )

    def set_position(
        self,
        obj: SnapshotObject,
        *,
        x: float | None = None,
        y: float | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Change X/Y together in one AviUtl2 Undo unit."""
        updates: list[ItemUpdate] = []
        if x is not None:
            updates.append(ItemUpdate("標準描画", "X", x))
        if y is not None:
            updates.append(ItemUpdate("標準描画", "Y", y))
        if not updates:
            raise ValueError("at least one of x or y must be provided")
        return self.set_items(obj, updates, timeout=timeout)

    def move_object(
        self,
        obj: SnapshotObject,
        *,
        layer: int,
        frame: int,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Move an object while preserving its duration and effects."""
        if layer < 0 or frame < 0:
            raise ValueError("layer and frame must be non-negative")
        params = obj.target_params()
        params.update({"layer": layer, "frame": frame})
        return self.call("object.move", params, timeout=timeout)

    def delete_object(
        self,
        obj: SnapshotObject,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Delete one existing object in one AviUtl2 Undo unit."""
        return self.call(
            "object.delete",
            obj.target_params(),
            timeout=timeout,
        )

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> LiveClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


__all__ = ["AmbiguousInstanceError", "InstanceInfo", "LiveClient"]
