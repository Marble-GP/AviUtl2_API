"""Backend-neutral editing plans used by file and live project adapters."""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Literal, Protocol, TypeAlias

PlanSequence = Literal["parallel", "serial"]
FramePosition = int | Literal["end"] | None
MediaFit = Literal["contain", "cover"]


@dataclass(frozen=True, slots=True)
class LinearMotion:
    """A high-level value that moves linearly from ``start`` to ``end``."""

    start: float
    end: float

    def __post_init__(self) -> None:
        for name, value in (("start", self.start), ("end", self.end)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"linear motion {name} must be numeric")
            if not math.isfinite(value):
                raise ValueError(f"linear motion {name} must be finite")
            object.__setattr__(self, name, float(value))


TransformValue = float | LinearMotion


def linear(start: float, end: float) -> LinearMotion:
    """Return a localized-name-free linear animation value."""

    return LinearMotion(start, end)


EffectScope = Literal["primary", "video", "audio"]
EffectParameterValue: TypeAlias = (
    str | int | float | bool | LinearMotion | PathLike[str]
)
# Public spelling used by the backend-neutral Effect API specification.
EffectValue: TypeAlias = EffectParameterValue
EFFECT_PROFILES: tuple[str, ...] = (
    "color_adjustment",
    "monochrome",
    "gradient",
    "crop",
    "mask",
    "resize",
    "mosaic",
    "blur",
    "directional_blur",
    "motion_blur",
    "glow",
    "emission",
    "outline",
    "drop_shadow",
    "chroma_key",
    "luminance_key",
    "fade",
    "wipe",
    "audio_gain",
    "audio_fade",
)


@dataclass(frozen=True, slots=True)
class EffectSpec:
    """Localized-name-free description of one curated AviUtl2 effect."""

    profile: str
    parameters: Mapping[str, EffectParameterValue]
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.profile not in EFFECT_PROFILES:
            raise ValueError(f"unknown effect profile: {self.profile!r}")
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be bool")
        object.__setattr__(
            self,
            "parameters",
            MappingProxyType(dict(self.parameters)),
        )


@dataclass(frozen=True, slots=True)
class NativeEffectSpec:
    """Explicit native effect escape hatch with no semantic guessing."""

    name: str
    values: Mapping[str, EffectParameterValue]
    enabled: bool = True
    scope: EffectScope = "primary"

    def __post_init__(self) -> None:
        if not self.name or any(char in self.name for char in "\x00\r\n"):
            raise ValueError("native effect name must be a non-empty line")
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be bool")
        if self.scope not in {"primary", "video", "audio"}:
            raise ValueError("scope must be primary, video, or audio")
        for name in self.values:
            if not name or any(char in name for char in "\x00\r\n"):
                raise ValueError("native effect item names must be non-empty lines")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


EffectDefinition: TypeAlias = EffectSpec | NativeEffectSpec


def effect(
    profile: str,
    *,
    enabled: bool = True,
    **parameters: EffectParameterValue,
) -> EffectSpec:
    """Build one curated high-level effect specification."""

    return EffectSpec(profile, parameters, enabled)


def native_effect(
    name: str,
    values: Mapping[str, EffectParameterValue],
    *,
    enabled: bool = True,
    scope: EffectScope = "primary",
) -> NativeEffectSpec:
    """Build an explicit catalog-native effect specification."""

    return NativeEffectSpec(name, values, enabled, scope)


class ObjectReference(Protocol):
    """Minimum object-reference surface returned by an editing backend."""

    @property
    def object_id(self) -> str: ...

    @property
    def revision(self) -> int: ...

    @property
    def layer(self) -> int: ...

    @property
    def frame_start(self) -> int: ...

    @property
    def frame_end(self) -> int: ...

    @property
    def duration(self) -> int: ...

    @property
    def midpoint(self) -> int: ...

    @property
    def name(self) -> str | None: ...

    @property
    def api_locked(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class Transform:
    """Common visual transform independent of localized AviUtl2 item names."""

    x: TransformValue | None = None
    y: TransformValue | None = None
    z: TransformValue | None = None
    scale: TransformValue | None = None
    rotation: TransformValue | None = None
    rotation_x: TransformValue | None = None
    rotation_y: TransformValue | None = None
    rotation_z: TransformValue | None = None
    opacity: TransformValue | None = None

    def __post_init__(self) -> None:
        scale_values = (
            (self.scale.start, self.scale.end)
            if isinstance(self.scale, LinearMotion)
            else (self.scale,)
        )
        if any(value is not None and value <= 0.0 for value in scale_values):
            raise ValueError("scale must be positive (100 is native size)")
        opacity_values = (
            (self.opacity.start, self.opacity.end)
            if isinstance(self.opacity, LinearMotion)
            else (self.opacity,)
        )
        if any(
            value is not None and not 0.0 <= value <= 1.0 for value in opacity_values
        ):
            raise ValueError("opacity must be between 0.0 and 1.0")
        if self.rotation is not None and self.rotation_z is not None:
            raise ValueError("use rotation or rotation_z, not both")

    @property
    def effective_rotation_z(self) -> TransformValue | None:
        return self.rotation if self.rotation_z is None else self.rotation_z

    @property
    def empty(self) -> bool:
        return all(
            value is None
            for value in (
                self.x,
                self.y,
                self.z,
                self.scale,
                self.rotation,
                self.rotation_x,
                self.rotation_y,
                self.rotation_z,
                self.opacity,
            )
        )


@dataclass(frozen=True, slots=True)
class EditCommand:
    """One backend-neutral edit intent stored in an :class:`EditPlan`."""

    op: str
    key: str | None = None
    target: object | None = None
    values: Mapping[str, object] = field(default_factory=dict)


class EditInstruction(Protocol):
    """Structural interface shared by every backend-neutral instruction."""

    @property
    def op(self) -> str: ...

    @property
    def key(self) -> str | None: ...

    @property
    def target(self) -> object | None: ...

    @property
    def values(self) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class AddTextInstruction:
    text: str
    key: str | None
    at: FramePosition
    layer: int | None
    duration: int | None
    transform: Transform
    size: float
    color: str
    font: str | None
    effects: tuple[EffectDefinition, ...]
    op: ClassVar[str] = "add_text"
    target: ClassVar[None] = None

    @property
    def values(self) -> Mapping[str, object]:
        return {
            "text": self.text,
            "at": self.at,
            "layer": self.layer,
            "duration": self.duration,
            "transform": self.transform,
            "size": self.size,
            "color": self.color,
            "font": self.font,
            "effects": self.effects,
        }


@dataclass(frozen=True, slots=True)
class AddMediaInstruction:
    file: Path
    kind: Literal["auto", "image", "video", "audio"]
    key: str | None
    at: FramePosition
    layer: int | None
    duration: int | None
    transform: Transform
    fit: MediaFit | None
    apply_exif_orientation: bool
    effects: tuple[EffectDefinition, ...]
    op: ClassVar[str] = "add_media"
    target: ClassVar[None] = None

    @property
    def values(self) -> Mapping[str, object]:
        return {
            "file": self.file,
            "kind": self.kind,
            "at": self.at,
            "layer": self.layer,
            "duration": self.duration,
            "transform": self.transform,
            "fit": self.fit,
            "apply_exif_orientation": self.apply_exif_orientation,
            "effects": self.effects,
        }


@dataclass(frozen=True, slots=True)
class AddShapeInstruction:
    shape: str
    key: str | None
    at: FramePosition
    layer: int | None
    duration: int | None
    transform: Transform
    color: str
    width: float
    height: float
    effects: tuple[EffectDefinition, ...]
    op: ClassVar[str] = "add_shape"
    target: ClassVar[None] = None

    @property
    def values(self) -> Mapping[str, object]:
        return {
            "shape": self.shape,
            "at": self.at,
            "layer": self.layer,
            "duration": self.duration,
            "transform": self.transform,
            "color": self.color,
            "width": self.width,
            "height": self.height,
            "effects": self.effects,
        }


@dataclass(frozen=True, slots=True)
class UpdateObjectInstruction:
    target: object
    key: str | None
    text: str | None
    name: str | None
    transform: Transform
    op: ClassVar[str] = "update"

    @property
    def values(self) -> Mapping[str, object]:
        return {"text": self.text, "name": self.name, "transform": self.transform}


@dataclass(frozen=True, slots=True)
class MoveObjectInstruction:
    target: object
    key: str | None
    at: int
    layer: int
    op: ClassVar[str] = "move"

    @property
    def values(self) -> Mapping[str, object]:
        return {"at": self.at, "layer": self.layer}


@dataclass(frozen=True, slots=True)
class DeleteObjectInstruction:
    target: object
    key: str | None
    op: ClassVar[str] = "delete"

    @property
    def values(self) -> Mapping[str, object]:
        return {}


@dataclass(frozen=True, slots=True)
class AddEffectInstruction:
    target: object
    effect: str
    items: Mapping[str, object]
    enabled: bool
    key: str | None
    op: ClassVar[str] = "add_effect"

    @property
    def values(self) -> Mapping[str, object]:
        return {"effect": self.effect, "items": self.items, "enabled": self.enabled}


@dataclass(frozen=True, slots=True)
class ApplyEffectInstruction:
    """Apply one backend-neutral semantic or explicit native Effect."""

    target: object
    spec: EffectDefinition
    key: str | None
    op: ClassVar[str] = "apply_effect"

    @property
    def values(self) -> Mapping[str, object]:
        return {"spec": self.spec}


@dataclass(frozen=True, slots=True)
class SetEffectEnabledInstruction:
    target: object
    selector: str
    enabled: bool
    key: str | None
    op: ClassVar[str] = "set_effect_enabled"

    @property
    def values(self) -> Mapping[str, object]:
        return {"selector": self.selector, "enabled": self.enabled}


@dataclass(frozen=True, slots=True)
class DeleteEffectInstruction:
    target: object
    selector: str
    key: str | None
    op: ClassVar[str] = "delete_effect"

    @property
    def values(self) -> Mapping[str, object]:
        return {"selector": self.selector}


@dataclass(frozen=True, slots=True)
class PlannedPlacement:
    command_index: int
    key: str | None
    layer: int
    frame: int
    duration: int


@dataclass(frozen=True, slots=True)
class PlanValidation:
    valid: bool
    revision: int
    placements: tuple[PlannedPlacement, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """Machine-readable validation failure for agent recovery."""

    code: str
    message: str
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", dict(self.details))


@dataclass(frozen=True, slots=True)
class PlanCommandResult:
    command_index: int
    key: str | None
    status: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RollbackReceipt:
    attempted: bool = False
    complete: bool = True
    restored_count: int = 0
    gui_undo_required: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ObjectGroup:
    """Objects created by one logical command, including linked A/V objects."""

    objects: tuple[ObjectReference, ...]

    def __post_init__(self) -> None:
        if not self.objects:
            raise ValueError("an object group must not be empty")

    @property
    def primary(self) -> ObjectReference:
        return self.objects[0]

    def __iter__(self) -> Iterator[ObjectReference]:
        return iter(self.objects)

    def __len__(self) -> int:
        return len(self.objects)


@dataclass(frozen=True, slots=True)
class AppliedEffect:
    """Revision-scoped receipt for one high-level effect application."""

    profile: str | None
    native_name: str
    selector: str
    index: int
    enabled: bool
    values: Mapping[str, str]
    object_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class PlanResult:
    revision_before: int
    revision: int
    applied_count: int
    undo_grouped: bool
    atomic: bool
    commands: tuple[PlanCommandResult, ...]
    objects: Mapping[str, ObjectGroup]
    effects: Mapping[str, tuple[AppliedEffect, ...]] = field(default_factory=dict)
    rollback: RollbackReceipt = RollbackReceipt()
    warnings: tuple[str, ...] = ()


class PlanValidationError(ValueError):
    """Raised when a plan cannot be applied without changing the project."""

    def __init__(self, validation: PlanValidation) -> None:
        super().__init__("edit plan validation failed: " + "; ".join(validation.errors))
        self.validation = validation
        self.code = "INVALID_EDIT_PLAN"
        self.details: Mapping[str, object] = {
            "revision": validation.revision,
            "issues": tuple(issue.code for issue in validation.issues),
        }
        self.retryable = False
        self.required_action = "fix_plan"


class PlanApplyError(RuntimeError):
    """Raised when a preflighted plan fails while the host is editing."""

    def __init__(self, message: str, *, result: PlanResult | None = None) -> None:
        super().__init__(message)
        self.result = result
        self.code = "PLAN_APPLY_FAILED"
        self.details: Mapping[str, object] = {
            "revision": result.revision if result is not None else None,
            "gui_undo_required": (
                result.rollback.gui_undo_required if result is not None else None
            ),
        }
        self.retryable = result is None
        self.required_action = (
            "gui_undo" if result and result.rollback.gui_undo_required else "refresh"
        )


class ProjectChangedError(RuntimeError):
    """Raised rather than guessing after a GUI or competing-agent edit."""

    code = "PROJECT_CHANGED"
    retryable = True
    required_action = "refresh"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.details: Mapping[str, object] = {}


class InvalidMediaArgumentsError(ValueError):
    """A media-specific argument cannot be represented by the selected domain."""

    code = "INVALID_MEDIA_ARGUMENTS"
    retryable = False
    required_action = "fix_arguments"

    def __init__(self, message: str, *, arguments: Sequence[str]) -> None:
        super().__init__(message)
        self.details: Mapping[str, object] = {"arguments": tuple(arguments)}


class PlacementConflictError(ValueError):
    """An explicit timeline placement overlaps one or more objects."""

    code = "TIMELINE_PLACEMENT_CONFLICT"
    retryable = False
    required_action = "choose_free_range"

    def __init__(
        self,
        *,
        layer: int,
        frame_start: int,
        frame_end: int,
        conflicting_object_ids: tuple[str, ...],
        suggested_layer: int | None,
    ) -> None:
        suggestion = (
            f"; try layer={suggested_layer}" if suggested_layer is not None else ""
        )
        conflicts = ", ".join(conflicting_object_ids) or "unknown object"
        super().__init__(
            "requested timeline placement is occupied: "
            f"layer {layer} frames {frame_start}..{frame_end} overlap "
            f"{conflicts}{suggestion}"
        )
        self.layer = layer
        self.frame_start = frame_start
        self.frame_end = frame_end
        self.conflicting_object_ids = conflicting_object_ids
        self.suggested_layer = suggested_layer

    @property
    def details(self) -> Mapping[str, object]:
        return {
            "layer": self.layer,
            "frame_start": self.frame_start,
            "frame_end": self.frame_end,
            "conflicting_object_ids": self.conflicting_object_ids,
            "suggested_layer": self.suggested_layer,
        }


class EditPlan:
    """A reusable-until-success collection of deterministic editing intents."""

    def __init__(self, *, sequence: PlanSequence = "parallel") -> None:
        if sequence not in {"parallel", "serial"}:
            raise ValueError("sequence must be 'parallel' or 'serial'")
        self.sequence: PlanSequence = sequence
        self._commands: list[EditInstruction] = []
        self._keys: set[str] = set()
        self._consumed = False

    @property
    def commands(self) -> tuple[EditInstruction, ...]:
        return tuple(self._commands)

    @property
    def consumed(self) -> bool:
        return self._consumed

    def _append(self, command: EditInstruction) -> EditPlan:
        if self._consumed:
            raise RuntimeError("a successfully applied EditPlan cannot be reused")
        if command.key is not None:
            if not command.key or command.key in self._keys:
                raise ValueError("plan command keys must be non-empty and unique")
            self._keys.add(command.key)
        self._commands.append(command)
        return self

    @staticmethod
    def _placement(
        *, at: FramePosition, layer: int | None, duration: int | None
    ) -> dict[str, object]:
        if isinstance(at, int) and (isinstance(at, bool) or at < 0):
            raise ValueError("at must be a non-negative frame, 'end', or None")
        if layer is not None and (isinstance(layer, bool) or layer < 0):
            raise ValueError("layer must be non-negative or None")
        if duration is not None and (isinstance(duration, bool) or duration < 1):
            raise ValueError("duration must be positive or None")
        return {"at": at, "layer": layer, "duration": duration}

    @staticmethod
    def _effect_stack(
        effects: tuple[EffectDefinition, ...] | list[EffectDefinition] | None,
    ) -> tuple[EffectDefinition, ...]:
        stack = tuple(effects or ())
        if len(stack) > 32:
            raise ValueError("one object supports at most 32 initial effects")
        if any(
            not isinstance(value, (EffectSpec, NativeEffectSpec)) for value in stack
        ):
            raise TypeError(
                "effects must contain EffectSpec or NativeEffectSpec values"
            )
        return stack

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
        effects: tuple[EffectDefinition, ...] | list[EffectDefinition] | None = None,
    ) -> EditPlan:
        if not text:
            raise ValueError("text must not be empty")
        self._placement(at=at, layer=layer, duration=duration)
        return self._append(
            AddTextInstruction(
                text=text,
                key=key,
                at=at,
                layer=layer,
                duration=duration,
                transform=Transform(
                    x=x,
                    y=y,
                    z=z,
                    scale=scale,
                    rotation=rotation,
                    rotation_x=rotation_x,
                    rotation_y=rotation_y,
                    rotation_z=rotation_z,
                    opacity=opacity,
                ),
                size=size,
                color=color,
                font=font,
                effects=self._effect_stack(effects),
            )
        )

    def add_media(
        self,
        file: str | PathLike[str],
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
        effects: tuple[EffectDefinition, ...] | list[EffectDefinition] | None = None,
    ) -> EditPlan:
        if kind not in {"auto", "image", "video", "audio"}:
            raise ValueError("unsupported media kind")
        if fit not in {None, "contain", "cover"}:
            raise ValueError("fit must be 'contain', 'cover', or None")
        if fit is not None and scale is not None:
            raise ValueError("fit and scale cannot be supplied together")
        if not isinstance(apply_exif_orientation, bool):
            raise TypeError("apply_exif_orientation must be bool")
        transform_arguments = {
            "x": x,
            "y": y,
            "z": z,
            "scale": scale,
            "rotation": rotation,
            "rotation_x": rotation_x,
            "rotation_y": rotation_y,
            "rotation_z": rotation_z,
            "opacity": opacity,
        }
        if kind == "audio":
            invalid = [
                name for name, value in transform_arguments.items() if value is not None
            ]
            if fit is not None:
                invalid.append("fit")
            if apply_exif_orientation:
                invalid.append("apply_exif_orientation")
            if invalid:
                raise InvalidMediaArgumentsError(
                    "audio media does not support visual transform arguments",
                    arguments=invalid,
                )
        self._placement(at=at, layer=layer, duration=duration)
        return self._append(
            AddMediaInstruction(
                file=Path(file),
                kind=kind,
                key=key,
                at=at,
                layer=layer,
                duration=duration,
                transform=Transform(
                    x=x,
                    y=y,
                    z=z,
                    scale=scale,
                    rotation=rotation,
                    rotation_x=rotation_x,
                    rotation_y=rotation_y,
                    rotation_z=rotation_z,
                    opacity=opacity,
                ),
                fit=fit,
                apply_exif_orientation=apply_exif_orientation,
                effects=self._effect_stack(effects),
            )
        )

    def _add_typed_media(
        self,
        file: str | PathLike[str],
        kind: Literal["image", "video", "audio"],
        *,
        key: str | None,
        at: FramePosition,
        layer: int | None,
        duration: int | None,
        x: TransformValue | None,
        y: TransformValue | None,
        z: TransformValue | None,
        scale: TransformValue | None,
        rotation: TransformValue | None,
        rotation_x: TransformValue | None,
        rotation_y: TransformValue | None,
        rotation_z: TransformValue | None,
        opacity: TransformValue | None,
        fit: MediaFit | None,
        apply_exif_orientation: bool,
        effects: tuple[EffectDefinition, ...] | list[EffectDefinition] | None,
    ) -> EditPlan:
        return self.add_media(
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
            effects=effects,
        )

    def add_image(
        self,
        file: str | PathLike[str],
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
        effects: tuple[EffectDefinition, ...] | list[EffectDefinition] | None = None,
    ) -> EditPlan:
        return self._add_typed_media(
            file,
            "image",
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
        file: str | PathLike[str],
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
        effects: tuple[EffectDefinition, ...] | list[EffectDefinition] | None = None,
    ) -> EditPlan:
        return self._add_typed_media(
            file,
            "video",
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
            apply_exif_orientation=False,
            effects=effects,
        )

    def add_audio(
        self,
        file: str | PathLike[str],
        *,
        key: str | None = None,
        at: FramePosition = None,
        layer: int | None = None,
        duration: int | None = None,
        effects: tuple[EffectDefinition, ...] | list[EffectDefinition] | None = None,
    ) -> EditPlan:
        return self._add_typed_media(
            file,
            "audio",
            key=key,
            at=at,
            layer=layer,
            duration=duration,
            x=None,
            y=None,
            z=None,
            scale=None,
            rotation=None,
            rotation_x=None,
            rotation_y=None,
            rotation_z=None,
            opacity=None,
            fit=None,
            apply_exif_orientation=False,
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
        effects: tuple[EffectDefinition, ...] | list[EffectDefinition] | None = None,
    ) -> EditPlan:
        if not shape:
            raise ValueError("shape must not be empty")
        self._placement(at=at, layer=layer, duration=duration)
        return self._append(
            AddShapeInstruction(
                shape=shape,
                key=key,
                at=at,
                layer=layer,
                duration=duration,
                transform=Transform(
                    x=x,
                    y=y,
                    z=z,
                    scale=scale,
                    rotation=rotation,
                    rotation_x=rotation_x,
                    rotation_y=rotation_y,
                    rotation_z=rotation_z,
                    opacity=opacity,
                ),
                color=color,
                width=width,
                height=height,
                effects=self._effect_stack(effects),
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
    ) -> EditPlan:
        transform = Transform(
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
        if text is None and name is None and transform.empty:
            raise ValueError("update requires at least one changed value")
        return self._append(
            UpdateObjectInstruction(
                target=target,
                key=key,
                text=text,
                name=name,
                transform=transform,
            )
        )

    def move(
        self,
        target: object,
        *,
        at: int,
        layer: int,
        key: str | None = None,
    ) -> EditPlan:
        if isinstance(at, bool) or at < 0 or isinstance(layer, bool) or layer < 0:
            raise ValueError("move frame/layer must be non-negative")
        return self._append(
            MoveObjectInstruction(
                target=target,
                key=key,
                at=at,
                layer=layer,
            )
        )

    def delete(self, target: object, *, key: str | None = None) -> EditPlan:
        return self._append(DeleteObjectInstruction(target=target, key=key))

    def add_effect(
        self,
        target: object,
        effect: str,
        *,
        values: Mapping[str, object] | None = None,
        enabled: bool = True,
        key: str | None = None,
    ) -> EditPlan:
        if not effect:
            raise ValueError("effect must not be empty")
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be bool")
        return self._append(
            AddEffectInstruction(
                key=key,
                target=target,
                effect=effect,
                items=dict(values or {}),
                enabled=enabled,
            )
        )

    def apply_effect(
        self,
        target: object,
        spec: EffectDefinition,
        *,
        key: str | None = None,
    ) -> EditPlan:
        """Append one semantic/native Effect using the shared profile schema."""

        if not isinstance(spec, (EffectSpec, NativeEffectSpec)):
            raise TypeError("spec must be EffectSpec or NativeEffectSpec")
        return self._append(ApplyEffectInstruction(target, spec, key))

    def set_effect_enabled(
        self,
        target: object,
        selector: str,
        enabled: bool,
        *,
        key: str | None = None,
    ) -> EditPlan:
        if not selector or not isinstance(enabled, bool):
            raise ValueError("a selector and bool enabled value are required")
        return self._append(
            SetEffectEnabledInstruction(
                key=key,
                target=target,
                selector=selector,
                enabled=enabled,
            )
        )

    def delete_effect(
        self,
        target: object,
        selector: str,
        *,
        key: str | None = None,
    ) -> EditPlan:
        if not selector:
            raise ValueError("an effect selector is required")
        return self._append(DeleteEffectInstruction(target, selector, key))

    def _mark_consumed(self) -> None:
        self._consumed = True


__all__ = [
    "AddEffectInstruction",
    "AddMediaInstruction",
    "AddShapeInstruction",
    "AddTextInstruction",
    "ApplyEffectInstruction",
    "AppliedEffect",
    "DeleteObjectInstruction",
    "DeleteEffectInstruction",
    "EFFECT_PROFILES",
    "EditCommand",
    "EffectDefinition",
    "EffectParameterValue",
    "EffectScope",
    "EffectSpec",
    "EffectValue",
    "EditInstruction",
    "EditPlan",
    "FramePosition",
    "InvalidMediaArgumentsError",
    "LinearMotion",
    "MediaFit",
    "MoveObjectInstruction",
    "NativeEffectSpec",
    "ObjectGroup",
    "ObjectReference",
    "PlanApplyError",
    "PlanCommandResult",
    "PlanResult",
    "PlanSequence",
    "PlanValidation",
    "PlanValidationError",
    "PlacementConflictError",
    "PlannedPlacement",
    "ProjectChangedError",
    "RollbackReceipt",
    "SetEffectEnabledInstruction",
    "Transform",
    "TransformValue",
    "UpdateObjectInstruction",
    "ValidationIssue",
    "linear",
    "effect",
    "native_effect",
]
