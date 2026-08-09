"""Explicit, conflict-aware synchronization between local and Live projects.

Nothing in this module watches files or mutates either backend in the
background.  A project changes only when :meth:`SyncSession.apply` is called.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, cast, overload
from uuid import uuid4

from aviutl2_api.editing import (
    EditInstruction,
    EditPlan,
    PlanApplyError,
    PlannedPlacement,
    PlanResult,
    PlanValidation,
    ProjectChangedError,
    RollbackReceipt,
    ValidationIssue,
)
from aviutl2_api.live.project import LiveObject, LiveProject
from aviutl2_api.live.snapshot import ProjectSnapshot, SnapshotObject
from aviutl2_api.local import LocalObject, LocalProject, LocalSnapshot

SyncState = Literal[
    "clean",
    "local_dirty",
    "live_dirty",
    "diverged",
    "incompatible",
]


class SyncConflictError(RuntimeError):
    """The two backends no longer share the state captured at bind time."""

    def __init__(
        self,
        message: str,
        *,
        status: SyncStatus | None = None,
        diff: SyncDiff | None = None,
        code: str = "SYNC_CONFLICT",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.diff = diff if diff is not None else (status.diff if status else None)
        self.details: Mapping[str, object] = {
            "state": status.state if status is not None else None,
            "local_revision": status.local_revision if status is not None else None,
            "live_revision": status.live_revision if status is not None else None,
        }
        self.retryable = True
        self.required_action = "refresh_diff_and_rebind"


class SyncCapabilityUnavailableError(RuntimeError):
    """A requested edit cannot be represented safely by both backends."""

    def __init__(
        self,
        message: str,
        *,
        missing: Sequence[str] = (),
        code: str = "SYNC_CAPABILITY_UNAVAILABLE",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.missing = tuple(missing)
        self.details: Mapping[str, object] = {"missing": self.missing}
        self.retryable = False
        self.required_action = "change_operation_or_upgrade_plugin"


class SyncValidationError(ValueError):
    """A clean binding rejected the requested edit plan."""

    def __init__(self, validation: SyncValidation) -> None:
        message = "; ".join(validation.errors) or "synchronization plan is invalid"
        super().__init__(message)
        self.code = "SYNC_VALIDATION_FAILED"
        self.validation = validation
        self.details: Mapping[str, object] = {
            "issues": tuple(issue.code for issue in validation.issues),
            "local_revision": validation.local_revision,
            "live_revision": validation.live_revision,
        }
        self.retryable = False
        self.required_action = "fix_plan"


@dataclass(frozen=True, slots=True)
class SyncedObject:
    """One object whose local and Live representations have been verified."""

    local: LocalObject
    live: LiveObject

    @property
    def object_id(self) -> str:
        return self.live.object_id

    @property
    def revision(self) -> int:
        return self.live.revision

    @property
    def layer(self) -> int:
        return self.live.layer

    @property
    def frame_start(self) -> int:
        return self.live.frame_start

    @property
    def frame_end(self) -> int:
        return self.live.frame_end

    @property
    def duration(self) -> int:
        return self.live.duration

    @property
    def midpoint(self) -> int:
        return self.live.midpoint

    @property
    def name(self) -> str | None:
        return self.live.name

    @property
    def api_locked(self) -> bool:
        return self.live.api_locked


@dataclass(frozen=True, slots=True)
class SyncedObjectGroup(Sequence[SyncedObject]):
    objects: tuple[SyncedObject, ...]

    def __post_init__(self) -> None:
        if not self.objects:
            raise ValueError("a synchronized object group must not be empty")

    @property
    def primary(self) -> SyncedObject:
        return self.objects[0]

    def __iter__(self) -> Iterator[SyncedObject]:
        return iter(self.objects)

    def __len__(self) -> int:
        return len(self.objects)

    @overload
    def __getitem__(self, index: int) -> SyncedObject: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[SyncedObject]: ...

    def __getitem__(self, index: int | slice) -> SyncedObject | Sequence[SyncedObject]:
        return self.objects[index]


class SyncedObjectSelection(Sequence[SyncedObject]):
    """Search result with explicit cardinality helpers for agent-written code."""

    def __init__(self, objects: Sequence[SyncedObject]) -> None:
        self._objects = tuple(objects)

    def __iter__(self) -> Iterator[SyncedObject]:
        return iter(self._objects)

    def __len__(self) -> int:
        return len(self._objects)

    @overload
    def __getitem__(self, index: int) -> SyncedObject: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[SyncedObject]: ...

    def __getitem__(self, index: int | slice) -> SyncedObject | Sequence[SyncedObject]:
        return self._objects[index]

    def first(self) -> SyncedObject | None:
        return self._objects[0] if self._objects else None

    def one(self) -> SyncedObject:
        count = len(self._objects)
        if count == 0:
            raise LookupError("expected one synchronized object, found none")
        if count > 1:
            raise LookupError(f"expected one synchronized object, found {count}")
        return self._objects[0]


@dataclass(frozen=True, slots=True)
class SyncDiff:
    synced: tuple[SyncedObject, ...] = ()
    local_only: tuple[LocalObject, ...] = ()
    live_only: tuple[LiveObject, ...] = ()
    changed: tuple[tuple[LocalObject, LiveObject], ...] = ()
    diagnostics: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not (self.local_only or self.live_only or self.changed)


@dataclass(frozen=True, slots=True)
class SyncStatus:
    state: SyncState
    local_revision: int
    live_revision: int
    local_file_dirty: bool
    scene_id: int | None
    project_file_path: str | None
    diff: SyncDiff
    diagnostics: tuple[str, ...] = ()
    _local_changed_since_bind: bool = False
    _live_changed_since_bind: bool = False

    @property
    def clean(self) -> bool:
        return self.state == "clean"

    @property
    def local_changed_since_bind(self) -> bool:
        return self._local_changed_since_bind

    @property
    def live_changed_since_bind(self) -> bool:
        return self._live_changed_since_bind

    @property
    def file_unsaved(self) -> bool:
        return self.local_file_dirty


@dataclass(frozen=True, slots=True)
class SyncValidation:
    valid: bool
    status: SyncStatus
    local: PlanValidation | None = None
    live: PlanValidation | None = None
    placements: tuple[PlannedPlacement, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def errors(self) -> tuple[str, ...]:
        local_errors = self.local.errors if self.local is not None else ()
        live_errors = self.live.errors if self.live is not None else ()
        issue_errors = tuple(issue.message for issue in self.issues)
        return tuple(dict.fromkeys((*live_errors, *local_errors, *issue_errors)))

    @property
    def warnings(self) -> tuple[str, ...]:
        local_warnings = self.local.warnings if self.local is not None else ()
        live_warnings = self.live.warnings if self.live is not None else ()
        return tuple(dict.fromkeys((*local_warnings, *live_warnings)))

    @property
    def local_revision(self) -> int:
        return self.status.local_revision

    @property
    def live_revision(self) -> int:
        return self.status.live_revision


@dataclass(frozen=True, slots=True)
class SyncRecoveryReceipt:
    operation_id: str
    live_applied: bool
    local_committed: bool
    recovery_required: bool
    message: str = ""
    code: str = "SYNC_PARTIAL_APPLY"
    local_revision: int | None = None
    live_revision: int | None = None
    rollback: RollbackReceipt = RollbackReceipt()
    warnings: tuple[str, ...] = ()

    @property
    def gui_undo_required(self) -> bool:
        return self.rollback.gui_undo_required


class SyncPartialApplyError(RuntimeError):
    """Live changed, but the matching local in-memory commit did not finish."""

    def __init__(self, message: str, *, receipt: SyncRecoveryReceipt) -> None:
        super().__init__(message)
        self.receipt = receipt
        self.code = receipt.code
        self.details: Mapping[str, object] = {
            "operation_id": receipt.operation_id,
            "live_applied": receipt.live_applied,
            "local_committed": receipt.local_committed,
            "recovery_required": receipt.recovery_required,
            "gui_undo_required": receipt.gui_undo_required,
        }
        self.retryable = receipt.recovery_required
        self.required_action = (
            "recover" if receipt.recovery_required else "inspect_and_rebind"
        )


@dataclass(frozen=True, slots=True)
class SyncResult:
    operation_id: str
    local_revision_before: int
    local_revision: int
    live_revision_before: int
    live_revision: int
    local_simulation_result: PlanResult
    local_snapshot: LocalSnapshot
    live_result: PlanResult
    objects: Mapping[str, SyncedObjectGroup]
    undo_grouped: bool = False
    atomic: bool = False
    disk_written: bool = False
    rollback: RollbackReceipt = RollbackReceipt()
    gui_undo_required: bool = False
    warnings: tuple[str, ...] = ()

    @property
    def local_result(self) -> PlanResult:
        """Compatibility alias for the explicitly named simulation receipt."""

        return self.local_simulation_result


@dataclass(slots=True)
class _PendingRecovery:
    receipt: SyncRecoveryReceipt
    local_revision: int
    commands: tuple[object, ...]
    sequence: str
    live_snapshot: ProjectSnapshot
    live_to_local_id: Mapping[str, int]


_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_TEXTUAL_ALIAS_KEYS = frozenset({"テキスト", "ファイル", "フォント"})


def _normalized_atom(value: str) -> str:
    stripped = value.strip()
    if not _NUMBER.fullmatch(stripped):
        return value
    try:
        number = Decimal(stripped)
    except InvalidOperation:
        return value
    if not number.is_finite():
        return value
    normalized = format(number.normalize(), "f")
    return "0" if Decimal(normalized) == 0 else normalized


def _normalized_value(value: str) -> str:
    parts = value.split(",")
    if len(parts) > 1:
        return ",".join(
            _normalized_atom(part) if _NUMBER.fullmatch(part.strip()) else part
            for part in parts
        )
    return _normalized_atom(value)


def _canonical_alias(alias: str) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    effects: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    properties: list[tuple[str, str]] | None = None
    for raw_line in alias.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[Object.") and line.endswith("]"):
            if properties is not None:
                name = next(
                    (value for key, value in properties if key == "effect.name"), ""
                )
                payload = tuple(
                    sorted(value for value in properties if value[0] != "effect.name")
                )
                effects.append((name, payload))
            properties = []
            continue
        if line.startswith("["):
            continue
        if properties is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"Group2", "Group3"}:
            continue
        normalized = (
            value
            if key in _TEXTUAL_ALIAS_KEYS or key.endswith("色")
            else _normalized_value(value)
        )
        properties.append((key, normalized))
    if properties is not None:
        name = next((value for key, value in properties if key == "effect.name"), "")
        payload = tuple(
            sorted(value for value in properties if value[0] != "effect.name")
        )
        effects.append((name, payload))
    return tuple(effects)


def _plan_copy(
    plan: EditPlan,
    placements: Sequence[PlannedPlacement],
    *,
    native_media_readback: bool = False,
) -> EditPlan:
    by_index = {value.command_index: value for value in placements}
    copied = EditPlan(sequence=plan.sequence)
    for index, command in enumerate(plan.commands):
        placement = by_index.get(index)
        replacements: dict[str, object] = {}
        if placement is not None and all(
            hasattr(command, name) for name in ("at", "layer", "duration")
        ):
            replacements.update(
                at=placement.frame,
                layer=placement.layer,
                duration=placement.duration,
            )
        if native_media_readback and command.op == "add_media":
            # AviUtl2 may create a linked A/V group and route audio Effects to
            # an object that does not exist in a standalone local simulation.
            # The authoritative post-apply Alias stack is committed below.
            replacements["effects"] = ()
        if replacements:
            command = cast(
                EditInstruction,
                replace(
                    cast(Any, command),
                    **replacements,
                ),
            )
        copied._append(command)
    return copied


def _plan_from_commands(sequence: str, commands: Sequence[object]) -> EditPlan:
    plan = EditPlan(sequence=sequence)  # type: ignore[arg-type]
    for command in commands:
        plan._append(command)  # type: ignore[arg-type]
    return plan


def _command_digest(plan: EditPlan) -> str:
    serializable = [
        {
            "op": command.op,
            "key": command.key,
            "values": {key: repr(value) for key, value in command.values.items()},
        }
        for command in plan.commands
    ]
    encoded = json.dumps(serializable, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


class SyncSession:
    """Explicitly apply new plans to matching local and Live projects."""

    def __init__(self, local: LocalProject, live: LiveProject) -> None:
        self.local = local
        self.live = live
        self._closed = False
        self._base_local_revision = local.revision
        self._base_live_revision = 0
        self._last_status: SyncStatus | None = None
        self._pending: dict[str, _PendingRecovery] = {}
        self._journal: list[dict[str, object]] = []
        self._event_sequence = 0
        self._binding_invalidated: str | None = None
        self._poll_lifecycle(accept_baseline=True)
        initial = self.refresh()
        self._base_live_revision = initial.live_revision

    @classmethod
    def bind(cls, local: LocalProject, live: LiveProject) -> SyncSession:
        try:
            local._editable()
        except (RuntimeError, ValueError) as error:
            raise SyncCapabilityUnavailableError(str(error)) from error
        capabilities = getattr(live, "capabilities", None)
        if isinstance(capabilities, Mapping):
            required_flags = {
                "explicit_plan_sync",
                "project_lifecycle_notifications",
                "project_path_observation",
            }
            missing_flags = sorted(
                name for name in required_flags if capabilities.get(name) is not True
            )
            if missing_flags:
                raise SyncCapabilityUnavailableError(
                    "running plugin lacks explicit synchronization capabilities: "
                    + ", ".join(missing_flags),
                    missing=missing_flags,
                )
            methods = capabilities.get("methods", ())
            available = (
                set(methods)
                if isinstance(methods, Sequence)
                and not isinstance(methods, (str, bytes))
                else set()
            )
            required = {
                "edit.plan.apply",
                "edit.plan.validate",
                "project.get_snapshot",
                "scene.get_current",
            }
            missing = sorted(required - available)
            if missing:
                raise SyncCapabilityUnavailableError(
                    "running plugin lacks explicit synchronization methods: "
                    + ", ".join(missing),
                    missing=missing,
                )
        session = cls(local, live)
        status = session.status(refresh=False)
        if status.state == "incompatible":
            raise SyncCapabilityUnavailableError(
                "local and Live projects are incompatible: "
                + "; ".join(status.diagnostics)
            )
        return session

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("the synchronization session is closed")

    def _poll_lifecycle(self, *, accept_baseline: bool = False) -> None:
        watcher = getattr(self.live.client, "watch_events", None)
        if not callable(watcher):
            return
        try:
            watched = watcher(
                after_sequence=self._event_sequence,
                timeout_ms=0,
                types=("project_loaded", "edit_scene_changed"),
            )
        except (ConnectionError, RuntimeError, ValueError):
            return
        self._event_sequence = watched.latest_sequence
        if accept_baseline:
            return
        if watched.resync_required:
            self._binding_invalidated = "event journal overflow requires rebind"
        elif watched.events:
            self._binding_invalidated = (
                f"{watched.events[-1].type} invalidated the synchronization binding"
            )

    def _compare(
        self,
        local_objects: Sequence[LocalObject],
        live_objects: Sequence[SnapshotObject],
    ) -> SyncDiff:
        local_by_range: dict[tuple[int, int, int], list[LocalObject]] = defaultdict(
            list
        )
        live_by_range: dict[tuple[int, int, int], list[LiveObject]] = defaultdict(list)
        for local_value in local_objects:
            local_by_range[
                (local_value.layer, local_value.frame_start, local_value.frame_end)
            ].append(local_value)
        for snapshot_value in live_objects:
            live_by_range[
                (
                    snapshot_value.layer,
                    snapshot_value.frame_start,
                    snapshot_value.frame_end,
                )
            ].append(LiveObject(snapshot_value))

        synced: list[SyncedObject] = []
        local_only: list[LocalObject] = []
        live_only: list[LiveObject] = []
        changed: list[tuple[LocalObject, LiveObject]] = []
        diagnostics: list[str] = []
        for key in sorted(set(local_by_range) | set(live_by_range)):
            local_values = local_by_range.get(key, [])
            live_values = live_by_range.get(key, [])
            if len(local_values) != 1 or len(live_values) != 1:
                local_only.extend(local_values)
                live_only.extend(live_values)
                diagnostics.append(
                    f"timeline range {key} is not a one-to-one object match"
                )
                continue
            local_value = local_values[0]
            live_value = live_values[0]
            live_alias = live_value.snapshot_object.alias
            if not live_alias:
                live_only.append(live_value)
                local_only.append(local_value)
                diagnostics.append(f"Live object {live_value.object_id} omitted Alias")
            elif _canonical_alias(local_value.alias) == _canonical_alias(live_alias):
                synced.append(SyncedObject(local_value, live_value))
            else:
                changed.append((local_value, live_value))
        return SyncDiff(
            tuple(synced),
            tuple(local_only),
            tuple(live_only),
            tuple(changed),
            tuple(diagnostics),
        )

    def refresh(self) -> SyncStatus:
        self._ensure_open()
        self._poll_lifecycle()
        snapshot = self.live.get_snapshot(include_alias=True)
        project_info = self.live.client.get_project_info()
        diagnostics: list[str] = []
        try:
            scene = self.live.client.get_current_scene()
        except Exception as error:
            scene = None
            diagnostics.append(f"current scene information is unavailable: {error}")

        local_summary = self.local.summary()
        if scene is not None:
            if self.local.display_scene_id != scene.scene_id:
                diagnostics.append(
                    "display.scene does not match the Live current scene"
                )
            expected = (
                local_summary["width"],
                local_summary["height"],
                local_summary["frame_rate"]["rate"],  # type: ignore[index]
                local_summary["frame_rate"]["scale"],  # type: ignore[index]
                local_summary["sample_rate"],
            )
            actual = (
                scene.width,
                scene.height,
                scene.rate,
                scene.scale,
                scene.sample_rate,
            )
            if expected != actual:
                diagnostics.append(
                    f"scene settings differ: local={expected!r}, live={actual!r}"
                )

        diff = self._compare(self.local.get_snapshot().objects, snapshot.objects)
        incompatible = (
            scene is None
            or any(
                "does not match" in value or "settings differ" in value
                for value in diagnostics
            )
            or any("omitted Alias" in value for value in diff.diagnostics)
        )
        if self._binding_invalidated is not None:
            diagnostics.append(self._binding_invalidated)
            incompatible = True
        local_changed = self.local.revision != self._base_local_revision
        live_changed = (
            self._base_live_revision > 0
            and snapshot.revision != self._base_live_revision
        )
        if incompatible:
            state: SyncState = "incompatible"
        elif local_changed and live_changed:
            state = "diverged"
        elif not diff.clean:
            state = "diverged"
        elif local_changed:
            state = "local_dirty"
        elif live_changed:
            state = "live_dirty"
        else:
            state = "clean"
        path = project_info.get("project_file_path")
        status = SyncStatus(
            state,
            self.local.revision,
            snapshot.revision,
            self.local.dirty,
            snapshot.scene_id,
            path if isinstance(path, str) else None,
            diff,
            tuple((*diagnostics, *diff.diagnostics)),
            local_changed,
            live_changed,
        )
        self._last_status = status
        return status

    def status(self, *, refresh: bool = True) -> SyncStatus:
        if refresh or self._last_status is None:
            return self.refresh()
        return self._last_status

    def diff(self, *, refresh: bool = True) -> SyncDiff:
        return self.status(refresh=refresh).diff

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
    ) -> SyncedObjectSelection:
        self._ensure_open()
        status = self.refresh()
        local_matches = {
            value.local_id
            for value in self.local.find(
                text=text,
                text_contains=text_contains,
                file=file,
                file_contains=file_contains,
                effect=effect,
                layer=layer,
                at=at,
                overlap=overlap,
            )
        }
        return SyncedObjectSelection(
            tuple(
                value
                for value in status.diff.synced
                if value.local.local_id in local_matches
                and (name is None or value.name == name)
                and (
                    name_contains is None
                    or (
                        value.name is not None
                        and name_contains.casefold() in value.name.casefold()
                    )
                )
                and (api_locked is None or value.api_locked is api_locked)
            )
        )

    @staticmethod
    def _issue(code: str, message: str) -> ValidationIssue:
        return ValidationIssue(code, message)

    def _require_clean(self, status: SyncStatus) -> None:
        if not status.clean:
            raise SyncConflictError(
                "explicit synchronization requires clean state; "
                f"current state is {status.state}",
                status=status,
                code="SYNC_NOT_CLEAN",
            )

    def validate(self, plan: EditPlan) -> SyncValidation:
        self._ensure_open()
        status = self.refresh()
        return self._validate_from_status(plan, status)

    def _validate_from_status(
        self,
        plan: EditPlan,
        status: SyncStatus,
    ) -> SyncValidation:
        if not status.clean:
            return SyncValidation(
                False,
                status,
                issues=(
                    self._issue(
                        "SYNC_NOT_CLEAN",
                        f"synchronization state is {status.state}",
                    ),
                ),
            )
        live_validation = self.live.validate(plan)
        if not live_validation.valid:
            return SyncValidation(
                False,
                status,
                live=live_validation,
                placements=live_validation.placements,
                issues=live_validation.issues,
            )
        local_plan = _plan_copy(
            plan,
            live_validation.placements,
            native_media_readback=True,
        )
        local_validation = self.local.validate(local_plan)
        issues = (*live_validation.issues, *local_validation.issues)
        return SyncValidation(
            live_validation.valid and local_validation.valid,
            status,
            local_validation,
            live_validation,
            live_validation.placements,
            issues,
        )

    def _synced_groups(
        self,
        result: PlanResult,
        status: SyncStatus,
    ) -> Mapping[str, SyncedObjectGroup]:
        by_live = {value.live.object_id: value for value in status.diff.synced}
        groups: dict[str, SyncedObjectGroup] = {}
        for key, group in result.objects.items():
            values = tuple(
                by_live[value.object_id]
                for value in group.objects
                if value.object_id in by_live
            )
            if values:
                groups[key] = SyncedObjectGroup(values)
        return groups

    def apply(
        self,
        plan: EditPlan,
        *,
        operation_id: str | None = None,
    ) -> SyncResult:
        self._ensure_open()
        status_before = self.refresh()
        self._require_clean(status_before)
        validation = self._validate_from_status(plan, status_before)
        if not validation.valid or validation.live is None:
            raise SyncValidationError(validation)

        local_plan = _plan_copy(
            plan,
            validation.placements,
            native_media_readback=True,
        )
        live_plan = _plan_copy(plan, validation.placements)
        commands = local_plan.commands
        live_to_local_id = {
            value.live.object_id: value.local.local_id
            for value in status_before.diff.synced
        }
        staged = self.local._fork()
        local_result = staged.apply(local_plan)
        operation_id = operation_id or f"sync-{uuid4().hex}"
        digest = _command_digest(plan)
        try:
            live_result = self.live.apply(live_plan, operation_id=operation_id)
        except PlanApplyError as error:
            partial = bool(
                error.result is not None
                and (
                    not error.result.rollback.complete
                    or error.result.rollback.gui_undo_required
                )
            )
            if partial and not plan.consumed:
                plan._mark_consumed()
            raise
        except Exception as error:
            # A transport/decode failure leaves the host result ambiguous.  If
            # a fresh revision proves no host change, retain the caller's plan;
            # otherwise consume it to prevent an accidental second apply.
            current: ProjectSnapshot | None
            try:
                current = self.live.get_snapshot(include_alias=True)
            except Exception:
                current = None
            if current is not None and current.revision == status_before.live_revision:
                raise
            if not plan.consumed:
                plan._mark_consumed()
            message = (
                "Live changed but no complete apply receipt was received"
                if current is not None
                else "Live apply result is unknown because readback also failed"
            )
            uncertain = SyncRecoveryReceipt(
                operation_id=operation_id,
                live_applied=current is not None,
                local_committed=False,
                recovery_required=False,
                message=message,
                code="SYNC_LIVE_RESULT_UNKNOWN",
                local_revision=status_before.local_revision,
                live_revision=current.revision if current is not None else None,
            )
            self._journal.append(
                {
                    "operation_id": operation_id,
                    "command_digest": digest,
                    "local_revision": status_before.local_revision,
                    "live_revision": (
                        current.revision if current is not None else None
                    ),
                    "status": "live_result_unknown",
                }
            )
            raise SyncPartialApplyError(message, receipt=uncertain) from error
        plan._mark_consumed()

        live_snapshot = self.live.get_snapshot(include_alias=True)
        receipt = SyncRecoveryReceipt(
            operation_id=operation_id,
            live_applied=True,
            local_committed=False,
            recovery_required=True,
            local_revision=status_before.local_revision,
            live_revision=live_result.revision,
            rollback=live_result.rollback,
            warnings=live_result.warnings,
        )
        self._poll_lifecycle()
        if (
            live_snapshot.revision != live_result.revision
            or self._binding_invalidated is not None
        ):
            reason = self._binding_invalidated or (
                "the Live project changed before Alias readback"
            )
            unrecoverable = replace(
                receipt,
                recovery_required=False,
                message=reason,
            )
            raise SyncPartialApplyError(reason, receipt=unrecoverable)
        try:
            staged._replace_scene_from_live(
                live_snapshot.objects,
                expected_revision=staged.revision,
                live_to_local_id=live_to_local_id,
            )
            self.local._adopt(staged, expected_revision=status_before.local_revision)
        except Exception as error:
            self._pending[operation_id] = _PendingRecovery(
                receipt,
                status_before.local_revision,
                commands,
                local_plan.sequence,
                live_snapshot,
                live_to_local_id,
            )
            self._journal.append(
                {
                    "operation_id": operation_id,
                    "command_digest": digest,
                    "local_revision": status_before.local_revision,
                    "live_revision": live_result.revision,
                    "status": "recovery_required",
                }
            )
            raise SyncPartialApplyError(
                f"Live applied operation {operation_id}, but local commit "
                f"failed: {error}",
                receipt=receipt,
            ) from error

        self._base_local_revision = self.local.revision
        self._base_live_revision = live_snapshot.revision
        local_snapshot = self.local.get_snapshot()
        post_diff = self._compare(local_snapshot.objects, live_snapshot.objects)
        status_after = SyncStatus(
            "clean" if post_diff.clean else "diverged",
            self.local.revision,
            live_snapshot.revision,
            self.local.dirty,
            live_snapshot.scene_id,
            status_before.project_file_path,
            post_diff,
            post_diff.diagnostics,
            False,
            False,
        )
        self._last_status = status_after
        if not status_after.diff.clean:
            recovery = SyncRecoveryReceipt(
                operation_id=operation_id,
                live_applied=True,
                local_committed=True,
                recovery_required=False,
                message="post-apply Local/Live semantic comparison failed",
                code="SYNC_POST_APPLY_MISMATCH",
                local_revision=self.local.revision,
                live_revision=live_snapshot.revision,
                rollback=live_result.rollback,
                warnings=live_result.warnings,
            )
            raise SyncPartialApplyError(recovery.message, receipt=recovery)
        self._journal.append(
            {
                "operation_id": operation_id,
                "command_digest": digest,
                "local_revision": self.local.revision,
                "live_revision": live_snapshot.revision,
                "status": "applied",
            }
        )
        return SyncResult(
            operation_id=operation_id,
            local_revision_before=status_before.local_revision,
            local_revision=self.local.revision,
            live_revision_before=status_before.live_revision,
            live_revision=live_snapshot.revision,
            local_simulation_result=local_result,
            local_snapshot=local_snapshot,
            live_result=live_result,
            objects=self._synced_groups(live_result, status_after),
            undo_grouped=live_result.undo_grouped,
            rollback=live_result.rollback,
            gui_undo_required=live_result.rollback.gui_undo_required,
            warnings=tuple((*local_result.warnings, *live_result.warnings)),
        )

    def recover(self, receipt: SyncRecoveryReceipt) -> SyncRecoveryReceipt:
        self._ensure_open()
        pending = self._pending.get(receipt.operation_id)
        if pending is None:
            raise LookupError("no pending recovery matches this operation ID")
        if self.local.revision != pending.local_revision:
            raise ProjectChangedError("the local project changed after partial apply")
        current = self.live.get_snapshot(include_alias=True)
        if current.revision != pending.live_snapshot.revision:
            status = self.status(refresh=False)
            raise SyncConflictError(
                "the Live project changed after partial apply",
                status=status,
                code="SYNC_RECOVERY_CONFLICT",
            )
        staged = self.local._fork()
        local_plan = _plan_from_commands(pending.sequence, pending.commands)
        staged.apply(local_plan)
        staged._replace_scene_from_live(
            current.objects,
            expected_revision=staged.revision,
            live_to_local_id=pending.live_to_local_id,
        )
        self.local._adopt(staged, expected_revision=pending.local_revision)
        self._base_local_revision = self.local.revision
        self._base_live_revision = current.revision
        del self._pending[receipt.operation_id]
        recovered = SyncRecoveryReceipt(
            operation_id=receipt.operation_id,
            live_applied=True,
            local_committed=True,
            recovery_required=False,
            message="local commit recovered from Live Alias readback",
            code="SYNC_RECOVERED",
            local_revision=self.local.revision,
            live_revision=current.revision,
            rollback=receipt.rollback,
            warnings=receipt.warnings,
        )
        self._journal.append(
            {
                "operation_id": receipt.operation_id,
                "local_revision": self.local.revision,
                "live_revision": current.revision,
                "status": "recovered",
            }
        )
        self.refresh()
        return recovered

    @property
    def journal(self) -> tuple[Mapping[str, object], ...]:
        return tuple(dict(value) for value in self._journal)

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> SyncSession:
        self._ensure_open()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


__all__ = [
    "SyncCapabilityUnavailableError",
    "SyncConflictError",
    "SyncDiff",
    "SyncPartialApplyError",
    "SyncRecoveryReceipt",
    "SyncResult",
    "SyncSession",
    "SyncState",
    "SyncStatus",
    "SyncValidation",
    "SyncValidationError",
    "SyncedObject",
    "SyncedObjectGroup",
    "SyncedObjectSelection",
]
