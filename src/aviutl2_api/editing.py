"""Backend-neutral editing plans used by file and live project adapters."""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Literal, Protocol, TypeAlias

PlanSequence = Literal["parallel", "serial"]
FramePosition = int | Literal["end"] | None


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


class PlanApplyError(RuntimeError):
    """Raised when a preflighted plan fails while the host is editing."""

    def __init__(self, message: str, *, result: PlanResult | None = None) -> None:
        super().__init__(message)
        self.result = result


class ProjectChangedError(RuntimeError):
    """Raised rather than guessing after a GUI or competing-agent edit."""


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
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        scale: float | None = None,
        rotation: float | None = None,
        rotation_x: float | None = None,
        rotation_y: float | None = None,
        rotation_z: float | None = None,
        opacity: float | None = None,
        effects: tuple[EffectDefinition, ...] | list[EffectDefinition] | None = None,
    ) -> EditPlan:
        if kind not in {"auto", "image", "video", "audio"}:
            raise ValueError("unsupported media kind")
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
        x: float | None,
        y: float | None,
        z: float | None,
        scale: float | None,
        rotation: float | None,
        rotation_x: float | None,
        rotation_y: float | None,
        rotation_z: float | None,
        opacity: float | None,
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
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        scale: float | None = None,
        rotation: float | None = None,
        rotation_x: float | None = None,
        rotation_y: float | None = None,
        rotation_z: float | None = None,
        opacity: float | None = None,
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
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        scale: float | None = None,
        rotation: float | None = None,
        rotation_x: float | None = None,
        rotation_y: float | None = None,
        rotation_z: float | None = None,
        opacity: float | None = None,
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
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        scale: float | None = None,
        rotation: float | None = None,
        rotation_x: float | None = None,
        rotation_y: float | None = None,
        rotation_z: float | None = None,
        opacity: float | None = None,
        effects: tuple[EffectDefinition, ...] | list[EffectDefinition] | None = None,
    ) -> EditPlan:
        return self._add_typed_media(
            file,
            "audio",
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
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        scale: float | None = None,
        rotation: float | None = None,
        rotation_x: float | None = None,
        rotation_y: float | None = None,
        rotation_z: float | None = None,
        opacity: float | None = None,
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

    def _mark_consumed(self) -> None:
        self._consumed = True


__all__ = [
    "AddEffectInstruction",
    "AddMediaInstruction",
    "AddShapeInstruction",
    "AddTextInstruction",
    "AppliedEffect",
    "DeleteObjectInstruction",
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
    "LinearMotion",
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
    "PlannedPlacement",
    "ProjectChangedError",
    "RollbackReceipt",
    "SetEffectEnabledInstruction",
    "Transform",
    "TransformValue",
    "UpdateObjectInstruction",
    "linear",
    "effect",
    "native_effect",
]
