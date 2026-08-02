"""Practical high-level workflow over the additive Live Bridge protocol."""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .audio import AudioAnalysis, RenderedAudio
from .client import LiveClient
from .frame import ContactSheet, make_contact_sheet
from .qc import PreflightReport, run_preflight
from .snapshot import ProjectSnapshot
from .timeline import TimelineTransactionCommand, TransactionReceipt


class CapabilityUnavailableError(RuntimeError):
    """The running plugin/SDK does not truthfully expose a required feature."""


@dataclass(frozen=True, slots=True)
class UndoReceipt:
    operation_id: str
    revision_before: int
    revision_after: int
    grouped: bool
    executable_by_bridge: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EditingTransactionResult:
    receipt: TransactionReceipt
    snapshot: ProjectSnapshot
    undo: UndoReceipt


@dataclass(frozen=True, slots=True)
class ReviewBundle:
    revision: int
    contact_sheet: ContactSheet
    audio: RenderedAudio | None
    audio_analysis: AudioAnalysis | None


class EditingSession:
    """Preflight, mutate, refresh, and native-review one open project."""

    def __init__(self, client: LiveClient) -> None:
        self.client = client
        self.capabilities = client.get_capabilities()
        methods = self.capabilities.get("methods")
        if not isinstance(methods, list) or any(
            not isinstance(value, str) for value in methods
        ):
            raise ConnectionError("Live Bridge returned an invalid capability manifest")
        self._methods = frozenset(methods)
        self._operation_ids = itertools.count(1)
        self._snapshot: ProjectSnapshot | None = None

    @property
    def snapshot(self) -> ProjectSnapshot | None:
        return self._snapshot

    @property
    def ready_for_1_0(self) -> bool:
        release_gate = self.capabilities.get("release_gate")
        return (
            isinstance(release_gate, dict) and release_gate.get("ready_for_1_0") is True
        )

    def require(self, *methods: str) -> None:
        missing = [method for method in methods if method not in self._methods]
        if missing:
            raise CapabilityUnavailableError(
                "running AviUtl2/SDK does not expose: " + ", ".join(missing)
            )

    def refresh(self, *, include_alias: bool = False) -> ProjectSnapshot:
        self.require("project.get_snapshot")
        self._snapshot = self.client.get_snapshot(include_alias=include_alias)
        return self._snapshot

    def preflight(
        self,
        *,
        subtitle_layers: tuple[int, ...] | None = None,
        subtitle_overlap: Literal["allow", "warn", "error"] = "allow",
        minimum_subtitle_frames: int = 6,
        audio_range: tuple[int, int] | None = None,
        clipping_threshold: float = 1.0,
    ) -> PreflightReport:
        self.require(
            "project.get_snapshot",
            "project.get_layers",
            "media.inventory",
            "object.inspect",
            "effect.catalog",
            "font.catalog",
            "module.catalog",
        )
        if audio_range is not None:
            self.require(
                "audio.render",
                "audio.read_chunk",
                "audio.release",
            )
        report = run_preflight(
            self.client,
            subtitle_layers=subtitle_layers,
            subtitle_overlap=subtitle_overlap,
            minimum_subtitle_frames=minimum_subtitle_frames,
            audio_range=audio_range,
            clipping_threshold=clipping_threshold,
        )
        self._snapshot = report.snapshot
        return report

    def apply_transaction(
        self,
        commands: Sequence[TimelineTransactionCommand],
        *,
        expected_revision: int | None = None,
        validate_first: bool = True,
    ) -> EditingTransactionResult:
        self.require(
            "timeline.transaction.validate",
            "timeline.transaction.apply",
            "project.get_snapshot",
        )
        current = self._snapshot or self.refresh()
        revision = current.revision if expected_revision is None else expected_revision
        if current.revision != revision:
            raise ValueError("expected_revision does not match the session snapshot")
        if validate_first:
            validation = self.client.validate_transaction(
                expected_revision=revision,
                commands=commands,
            )
            if not validation.valid:
                raise ConnectionError("transaction validation was not valid")
        operation_id = f"editing-session-{next(self._operation_ids):016d}"
        receipt = self.client.apply_transaction(
            expected_revision=revision,
            commands=commands,
            operation_id=operation_id,
        )
        fresh = self.client.get_snapshot(include_alias=False)
        if receipt.revision is not None and fresh.revision != receipt.revision:
            raise ConnectionError(
                "fresh snapshot does not match the transaction receipt"
            )
        self._snapshot = fresh
        history = self.capabilities.get("history")
        history_executable = (
            isinstance(history, dict) and history.get("bridge_owned_undo") is True
        )
        return EditingTransactionResult(
            receipt=receipt,
            snapshot=fresh,
            undo=UndoReceipt(
                operation_id=operation_id,
                revision_before=revision,
                revision_after=fresh.revision,
                grouped=receipt.undo_grouped,
                executable_by_bridge=history_executable,
                warnings=receipt.warnings,
            ),
        )

    def review(
        self,
        *,
        frames: Sequence[int] | None = None,
        audio_range: tuple[int, int] | None = None,
        columns: int = 4,
        thumbnail_width: int = 320,
    ) -> ReviewBundle:
        self.require(
            "frame.render",
            "frame.read_chunk",
            "frame.release",
        )
        current = self._snapshot or self.refresh()
        if frames is None:
            sheet = self.client.render_review_contact_sheet(
                snapshot=current,
                columns=columns,
                thumbnail_width=thumbnail_width,
            )
        else:
            sheet = make_contact_sheet(
                self.client.render_frames(
                    frames,
                    expected_revision=current.revision,
                ),
                columns=columns,
                thumbnail_width=thumbnail_width,
            )
        audio: RenderedAudio | None = None
        analysis: AudioAnalysis | None = None
        if audio_range is not None:
            self.require(
                "audio.render",
                "audio.read_chunk",
                "audio.release",
            )
            audio = self.client.render_audio(
                frame_start=audio_range[0],
                frame_end=audio_range[1],
                expected_revision=current.revision,
            )
            analysis = audio.analyze()
        return ReviewBundle(
            revision=current.revision,
            contact_sheet=sheet,
            audio=audio,
            audio_analysis=analysis,
        )

    def undo(self) -> dict[str, Any]:
        history = self.capabilities.get("history")
        if (
            "history.undo" not in self._methods
            or not isinstance(history, dict)
            or history.get("bridge_owned_undo") is not True
        ):
            raise CapabilityUnavailableError(
                "official SDK Undo execution is unavailable"
            )
        return self.client.history_undo()

    def redo(self) -> dict[str, Any]:
        history = self.capabilities.get("history")
        if (
            "history.redo" not in self._methods
            or not isinstance(history, dict)
            or history.get("bridge_owned_redo") is not True
        ):
            raise CapabilityUnavailableError(
                "official SDK Redo execution is unavailable"
            )
        return self.client.history_redo()


__all__ = [
    "CapabilityUnavailableError",
    "EditingSession",
    "EditingTransactionResult",
    "ReviewBundle",
    "UndoReceipt",
]
