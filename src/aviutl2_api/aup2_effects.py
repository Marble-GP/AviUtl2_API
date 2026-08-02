"""Safe materialization and semantic validation of standard .aup2 effects."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from aviutl2_api.editing import EffectDefinition
from aviutl2_api.effect_profiles import (
    AUP2_EFFECT_COMPATIBLE_PROJECT_VERSIONS,
    ResolvedEffect,
    available_effect_profiles,
    get_effect_profile,
    resolve_effect,
)
from aviutl2_api.models import (
    AnimatedValue,
    AnimationParams,
    Effect,
    Project,
    StaticValue,
    TimelineObject,
)


@dataclass(frozen=True, slots=True)
class EffectValidationIssue:
    severity: Literal["error", "unverified"]
    code: str
    message: str
    object_id: int
    effect_id: int


@dataclass(frozen=True, slots=True)
class StandardEffectValidation:
    issues: tuple[EffectValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def errors(self) -> tuple[EffectValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def unverified(self) -> tuple[EffectValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "unverified")


@dataclass(frozen=True, slots=True)
class Aup2RoundTripDifference:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class Aup2RoundTripReport:
    compatible: bool
    differences: tuple[Aup2RoundTripDifference, ...]
    normalizations: int = 0


_CORE_SCHEMAS: dict[str, frozenset[str]] = {
    "図形": frozenset(
        {"図形の種類", "サイズ", "縦横比", "ライン幅", "色", "角を丸くする"}
    ),
    "音声ファイル": frozenset(
        {"再生位置", "再生速度", "ファイル", "トラック", "ループ再生"}
    ),
    "画像ファイル": frozenset(
        {"ファイル", "表示番号", "再生速度", "ループ再生", "連番ファイル"}
    ),
}

_CORE_DEFAULTS: dict[str, Mapping[str, object]] = {
    "画像ファイル": {
        "表示番号": AnimatedValue(
            0.0,
            0.0,
            # AviUtl2 adds this when an image is opened and saved.
            AnimationParams("再生範囲", "0"),
        ),
        "再生速度": StaticValue(100.0),
        "ループ再生": StaticValue(0.0),
        "連番ファイル": StaticValue(0.0),
    },
    "図形": {"図形の種類": "円", "角を丸くする": StaticValue(0.0)},
    "音声ファイル": {"トラック": StaticValue(0.0)},
}

_INPUT_EFFECTS = frozenset(
    {
        "図形",
        "テキスト",
        "画像ファイル",
        "動画ファイル",
        "音声ファイル",
        "シーン",
    }
)


def _profile_schemas() -> dict[str, frozenset[str]]:
    result = dict(_CORE_SCHEMAS)
    for profile in available_effect_profiles():
        definition = get_effect_profile(profile)
        result[definition.native_name] = frozenset(
            item.name for item in definition.items
        )
    return result


def _profile_defaults() -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = dict(_CORE_DEFAULTS)
    for profile in available_effect_profiles():
        definition = get_effect_profile(profile)
        result[definition.native_name] = {
            item.name: item.default for item in definition.items
        }
    return result


def _object_supports_scope(obj: TimelineObject, effect: ResolvedEffect) -> bool:
    names = {value.name for value in obj.effects}
    audio_only = bool(names & {"音声ファイル", "音声再生"}) and not bool(
        names & {"動画ファイル", "画像ファイル", "図形", "テキスト", "標準描画"}
    )
    if effect.scope == "audio":
        return bool(names & {"音声ファイル", "音声再生", "映像再生"})
    if effect.scope == "video":
        return not audio_only
    return True


def apply_effects(
    project: Project,
    obj: TimelineObject,
    *specs: EffectDefinition,
) -> tuple[Effect, ...]:
    """Append verified effects to one in-project object without writing files."""

    if not specs:
        raise ValueError("at least one effect specification is required")
    if not any(
        obj is candidate for scene in project.scenes for candidate in scene.objects
    ):
        raise ValueError("the target object does not belong to the project")
    if project.version not in AUP2_EFFECT_COMPATIBLE_PROJECT_VERSIONS:
        raise ValueError(
            f"unsupported .aup2 project version for semantic effects: {project.version}"
        )
    resolved = tuple(
        resolve_effect(spec, project_version=project.version) for spec in specs
    )
    for value in resolved:
        if not _object_supports_scope(obj, value):
            raise ValueError(
                f"{value.native_name} is incompatible with the target object domain"
            )
    next_id = max((effect.effect_id for effect in obj.effects), default=-1) + 1
    # AviUtl2 places filter Effects after 標準描画/音声再生 when it opens and
    # saves a 2001901 project. Appending matches the host's canonical order and
    # avoids an Open/Save reorder.
    insert_at = len(obj.effects)
    added: list[Effect] = []
    for value in resolved:
        properties: dict[str, object] = {}
        if not value.enabled:
            # This is the canonical marker emitted by AviUtl2 Open/Save.
            properties["effect.disable"] = StaticValue(1.0)
        properties.update(value.items)
        effect = Effect(
            effect_id=next_id,
            name=value.native_name,
            properties=properties,
        )
        obj.effects.insert(insert_at, effect)
        added.append(effect)
        insert_at += 1
        next_id += 1
    # The parser orders Effect sections by their numeric IDs. Renumbering keeps
    # the host-canonical input -> drawing/playback -> filters order.
    for effect_id, existing in enumerate(obj.effects):
        existing.effect_id = effect_id
    return tuple(added)


def _is_valid_item_value(value: object, kind: str) -> bool:
    if kind == "number":
        return isinstance(
            value, (StaticValue, AnimatedValue, int, float)
        ) and not isinstance(value, bool)
    if kind == "check":
        if isinstance(value, bool):
            return True
        if isinstance(value, StaticValue):
            return value.value in {0, 0.0, 1, 1.0}
        return value in {0, 1, "0", "1"}
    if kind == "color":
        return isinstance(value, str) and (
            value == ""
            or (
                len(value) == 6
                and all(character in "0123456789abcdefABCDEF" for character in value)
            )
        )
    return isinstance(value, str)


def _allowed_enum_values(profile: str) -> dict[str, frozenset[str]]:
    definition = get_effect_profile(profile)
    result: dict[str, set[str]] = defaultdict(set)
    for parameter, options in definition.enums.items():
        binding = definition.parameters[parameter]
        result[binding.item].update(options.values())
    return {name: frozenset(values) for name, values in result.items()}


def validate_standard_effects(project: Project) -> StandardEffectValidation:
    """Validate standard schemas without rejecting third-party effects."""

    if project.version not in AUP2_EFFECT_COMPATIBLE_PROJECT_VERSIONS:
        return StandardEffectValidation(
            (
                EffectValidationIssue(
                    "error",
                    "EFFECT_MANIFEST_VERSION_UNAVAILABLE",
                    f"No standard Effect manifest exists for {project.version}.",
                    -1,
                    -1,
                ),
            )
        )
    schemas = _profile_schemas()
    profile_by_name = {
        get_effect_profile(profile).native_name: profile
        for profile in available_effect_profiles()
    }
    issues: list[EffectValidationIssue] = []
    for scene in project.scenes:
        for obj in scene.objects:
            input_indices = [
                index
                for index, effect in enumerate(obj.effects)
                if effect.name in _INPUT_EFFECTS
            ]
            if input_indices and input_indices != [0]:
                first = obj.effects[input_indices[0]]
                issues.append(
                    EffectValidationIssue(
                        "error",
                        "STANDARD_EFFECT_ORDER_INVALID",
                        "The standard input Effect must remain first and unique.",
                        obj.object_id,
                        first.effect_id,
                    )
                )
            for index, effect in enumerate(obj.effects):
                schema = schemas.get(effect.name)
                if schema is None:
                    if effect.name not in {
                        "テキスト",
                        "標準描画",
                        "画像ファイル",
                        "動画ファイル",
                        "映像再生",
                        "音声再生",
                    }:
                        issues.append(
                            EffectValidationIssue(
                                "unverified",
                                "THIRD_PARTY_EFFECT_UNVERIFIED",
                                "Effect schema is not in the "
                                f"{project.version} manifest: {effect.name}",
                                obj.object_id,
                                effect.effect_id,
                            )
                        )
                    continue
                metadata = {
                    name
                    for name in effect.properties
                    if name == "effect.disable"
                }
                if metadata and not _is_valid_item_value(
                    effect.properties["effect.disable"], "check"
                ):
                    issues.append(
                        EffectValidationIssue(
                            "error",
                            "STANDARD_EFFECT_METADATA_INVALID",
                            f"{effect.name}.effect.disable has an invalid value.",
                            obj.object_id,
                            effect.effect_id,
                        )
                    )
                actual = {
                    name
                    for name in effect.properties
                    if not re.fullmatch(r"Group\d*", name)
                    and name not in metadata
                }
                unknown = sorted(actual - schema)
                if unknown:
                    issues.append(
                        EffectValidationIssue(
                            "error",
                            "STANDARD_EFFECT_UNKNOWN_ITEM",
                            f"{effect.name} contains unsupported item(s): "
                            + ", ".join(unknown),
                            obj.object_id,
                            effect.effect_id,
                        )
                    )
                if effect.name not in _CORE_SCHEMAS:
                    missing = sorted(schema - actual)
                    if missing:
                        issues.append(
                            EffectValidationIssue(
                                "error",
                                "STANDARD_EFFECT_ITEM_MISSING",
                                f"{effect.name} is missing item(s): "
                                + ", ".join(missing),
                                obj.object_id,
                                effect.effect_id,
                            )
                        )
                profile = profile_by_name.get(effect.name)
                if profile is None:
                    continue
                definition = get_effect_profile(profile)
                resolved = ResolvedEffect(
                    profile,
                    definition.native_name,
                    definition.scope,
                    (),
                    {},
                    True,
                    True,
                )
                if not _object_supports_scope(obj, resolved):
                    issues.append(
                        EffectValidationIssue(
                            "error",
                            "STANDARD_EFFECT_DOMAIN_MISMATCH",
                            f"{effect.name} is incompatible with the object domain.",
                            obj.object_id,
                            effect.effect_id,
                        )
                    )
                expected_types = dict(definition.schema)
                for name in sorted(actual & schema):
                    if not _is_valid_item_value(
                        effect.properties[name], expected_types[name]
                    ):
                        issues.append(
                            EffectValidationIssue(
                                "error",
                                "STANDARD_EFFECT_ITEM_TYPE_INVALID",
                                f"{effect.name}.{name} has an invalid value type.",
                                obj.object_id,
                                effect.effect_id,
                            )
                        )
                enums = _allowed_enum_values(profile)
                for name, allowed in enums.items():
                    if name not in effect.properties:
                        continue
                    value = _value_string(effect.properties[name])
                    if value not in allowed:
                        issues.append(
                            EffectValidationIssue(
                                "error",
                                "STANDARD_EFFECT_ENUM_INVALID",
                                f"{effect.name}.{name} is not a supported enum value.",
                                obj.object_id,
                                effect.effect_id,
                            )
                        )
    return StandardEffectValidation(tuple(issues))


def _value_string(value: object) -> str:
    if isinstance(value, (StaticValue, AnimatedValue)):
        return value.to_aup2()
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _equivalent_value(before: object, after: object) -> bool:
    left = _value_string(before)
    right = _value_string(after)
    if left == right:
        return True
    try:
        left_number = float(left)
        right_number = float(right)
    except ValueError:
        return False
    return math.isclose(left_number, right_number, rel_tol=1e-9, abs_tol=1e-6)


def _file_value(obj: TimelineObject) -> str:
    for effect in obj.effects:
        if "ファイル" in effect.properties:
            return (
                _value_string(effect.properties["ファイル"])
                .replace("/", "\\")
                .casefold()
            )
    return ""


def _content_identity(obj: TimelineObject) -> tuple[str, ...]:
    values: list[str] = []
    for effect in obj.effects:
        for name in ("テキスト", "シーン"):
            if name in effect.properties:
                values.append(f"{name}={_value_string(effect.properties[name])}")
    return tuple(values)


def _object_signature(obj: TimelineObject) -> tuple[object, ...]:
    return (
        obj.layer,
        obj.frame_start,
        obj.frame_end,
        _file_value(obj),
        _content_identity(obj),
    )


def compare_aup2_roundtrip(
    before: Project,
    after: Project,
) -> Aup2RoundTripReport:
    """Compare AviUtl2 Open/Save results while ignoring harmless normalization."""

    differences: list[Aup2RoundTripDifference] = []
    normalizations = 0
    if len(before.scenes) != len(after.scenes):
        differences.append(
            Aup2RoundTripDifference("SCENE_COUNT_CHANGED", "Scene count changed.")
        )
        return Aup2RoundTripReport(False, tuple(differences))
    defaults = _profile_defaults()
    for before_scene, after_scene in zip(before.scenes, after.scenes):
        before_counts = Counter(_object_signature(obj) for obj in before_scene.objects)
        after_counts = Counter(_object_signature(obj) for obj in after_scene.objects)
        if before_counts != after_counts:
            differences.append(
                Aup2RoundTripDifference(
                    "OBJECT_STRUCTURE_CHANGED",
                    f"Scene {before_scene.scene_id} object ranges, sources, "
                    "or content identity changed.",
                )
            )
            continue
        grouped_before: dict[tuple[object, ...], list[TimelineObject]] = defaultdict(
            list
        )
        grouped_after: dict[tuple[object, ...], list[TimelineObject]] = defaultdict(
            list
        )
        for obj in before_scene.objects:
            grouped_before[_object_signature(obj)].append(obj)
        for obj in after_scene.objects:
            grouped_after[_object_signature(obj)].append(obj)
        for signature, before_objects in grouped_before.items():
            after_objects = grouped_after[signature]
            for before_obj, after_obj in zip(before_objects, after_objects):
                before_names = tuple(effect.name for effect in before_obj.effects)
                after_names = tuple(effect.name for effect in after_obj.effects)
                if Counter(before_names) != Counter(after_names):
                    differences.append(
                        Aup2RoundTripDifference(
                            "EFFECT_SET_CHANGED",
                            f"Object {before_obj.object_id} Effect set changed: "
                            f"{before_names!r} -> {after_names!r}.",
                        )
                    )
                    continue
                if before_names != after_names:
                    differences.append(
                        Aup2RoundTripDifference(
                            "EFFECT_ORDER_CHANGED",
                            f"Object {before_obj.object_id} Effect order changed: "
                            f"{before_names!r} -> {after_names!r}.",
                        )
                    )
                after_by_name: dict[str, list[Effect]] = defaultdict(list)
                for effect in after_obj.effects:
                    after_by_name[effect.name].append(effect)
                occurrences: Counter[str] = Counter()
                for before_effect in before_obj.effects:
                    occurrence = occurrences[before_effect.name]
                    occurrences[before_effect.name] += 1
                    after_effect = after_by_name[before_effect.name][occurrence]
                    for name, value in before_effect.properties.items():
                        if re.fullmatch(r"Group\d*", name):
                            continue
                        if name not in after_effect.properties:
                            differences.append(
                                Aup2RoundTripDifference(
                                    "EXPLICIT_EFFECT_ITEM_REMOVED",
                                    f"Object {before_obj.object_id} "
                                    f"{before_effect.name}.{name} was removed.",
                                )
                            )
                        elif not _equivalent_value(
                            value, after_effect.properties[name]
                        ):
                            differences.append(
                                Aup2RoundTripDifference(
                                    "EXPLICIT_EFFECT_VALUE_CHANGED",
                                    f"Object {before_obj.object_id} "
                                    f"{before_effect.name}.{name} changed.",
                                )
                            )
                    additions = set(after_effect.properties) - set(
                        before_effect.properties
                    )
                    for name in additions:
                        if re.fullmatch(r"Group\d*", name):
                            normalizations += 1
                            continue
                        expected = defaults.get(after_effect.name, {}).get(name)
                        if expected is not None and _equivalent_value(
                            expected, after_effect.properties[name]
                        ):
                            normalizations += 1
                        else:
                            differences.append(
                                Aup2RoundTripDifference(
                                    "UNEXPECTED_EFFECT_ITEM_ADDED",
                                    f"Object {after_obj.object_id} "
                                    f"{after_effect.name}.{name} was added "
                                    "unexpectedly.",
                                )
                            )
    return Aup2RoundTripReport(not differences, tuple(differences), normalizations)


__all__ = [
    "Aup2RoundTripDifference",
    "Aup2RoundTripReport",
    "EffectValidationIssue",
    "StandardEffectValidation",
    "apply_effects",
    "compare_aup2_roundtrip",
    "validate_standard_effects",
]
