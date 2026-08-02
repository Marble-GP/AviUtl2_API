"""Versioned semantic effect profiles shared by file and live backends."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from types import MappingProxyType
from typing import Literal, TypeAlias

from aviutl2_api.editing import (
    EFFECT_PROFILES,
    EffectDefinition,
    EffectParameterValue,
    EffectScope,
    EffectSpec,
    LinearMotion,
    NativeEffectSpec,
)
from aviutl2_api.models import AnimatedValue, AnimationParams, StaticValue

AUP2_EFFECT_MANIFEST_VERSION = 2001901
# AviUtl2 currently upgrades a generated 2001901 project to 2010200 when the
# user opens and saves it.  Manual round-trip verification confirmed that the
# curated Effect names, item schemas, enums, and units remain compatible.
# Keep this allow-list explicit so an unknown future project format still
# fails closed instead of being interpreted using a guessed schema.
AUP2_EFFECT_COMPATIBLE_PROJECT_VERSIONS = frozenset({2001901, 2010200})
NativeValue: TypeAlias = str | int | float | bool | StaticValue | AnimatedValue
ItemKind = Literal["number", "check", "color", "select", "mask", "combo", "file"]
BindingKind = Literal["number", "check", "color", "select", "file", "opacity"]


class EffectProfileUnavailableError(ValueError):
    """Raised when a curated profile cannot be represented without guessing."""


@dataclass(frozen=True, slots=True)
class ItemTemplate:
    name: str
    kind: ItemKind
    default: NativeValue


@dataclass(frozen=True, slots=True)
class ParameterBinding:
    item: str
    kind: BindingKind


@dataclass(frozen=True, slots=True)
class EffectProfileDefinition:
    profile: str
    native_name: str
    scope: EffectScope
    items: tuple[ItemTemplate, ...]
    parameters: Mapping[str, ParameterBinding]
    enums: Mapping[str, Mapping[str, str]] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @property
    def schema(self) -> tuple[tuple[str, str], ...]:
        return tuple((item.name, item.kind) for item in self.items)


@dataclass(frozen=True, slots=True)
class ResolvedEffect:
    profile: str | None
    native_name: str
    scope: EffectScope
    items: tuple[tuple[str, NativeValue], ...]
    item_types: Mapping[str, str]
    enabled: bool
    verified: bool


def _items(*values: tuple[str, ItemKind, NativeValue]) -> tuple[ItemTemplate, ...]:
    return tuple(ItemTemplate(*value) for value in values)


def _bindings(**values: tuple[str, BindingKind]) -> Mapping[str, ParameterBinding]:
    return MappingProxyType(
        {name: ParameterBinding(item, kind) for name, (item, kind) in values.items()}
    )


def _enums(
    **values: Mapping[str, str],
) -> Mapping[str, Mapping[str, str]]:
    return MappingProxyType(
        {name: MappingProxyType(dict(options)) for name, options in values.items()}
    )


_VIDEO: EffectScope = "video"
_AUDIO: EffectScope = "audio"

_PROFILES: dict[str, EffectProfileDefinition] = {
    "color_adjustment": EffectProfileDefinition(
        "color_adjustment",
        "色調補正",
        _VIDEO,
        _items(
            ("明るさ", "number", StaticValue(100.0)),
            ("コントラスト", "number", StaticValue(100.0)),
            ("色相", "number", StaticValue(0.0)),
            ("輝度", "number", StaticValue(100.0)),
            ("彩度", "number", StaticValue(100.0)),
            ("飽和する", "check", True),
        ),
        _bindings(
            brightness=("明るさ", "number"),
            contrast=("コントラスト", "number"),
            hue_degrees=("色相", "number"),
            luminance=("輝度", "number"),
            saturation=("彩度", "number"),
            clamp=("飽和する", "check"),
        ),
    ),
    "monochrome": EffectProfileDefinition(
        "monochrome",
        "単色化",
        _VIDEO,
        _items(
            ("強さ", "number", StaticValue(100.0)),
            ("色", "color", "ffffff"),
            ("輝度を保持する", "check", True),
        ),
        _bindings(
            strength=("強さ", "number"),
            color=("色", "color"),
            preserve_luminance=("輝度を保持する", "check"),
        ),
    ),
    "gradient": EffectProfileDefinition(
        "gradient",
        "グラデーション",
        _VIDEO,
        _items(
            ("強さ", "number", StaticValue(100.0)),
            ("中心X", "number", StaticValue(0.0)),
            ("中心Y", "number", StaticValue(0.0)),
            ("角度", "number", StaticValue(0.0)),
            ("幅", "number", StaticValue(100.0)),
            ("合成モード", "select", "通常"),
            ("形状", "select", "線形"),
            ("開始色", "color", "ffffff"),
            ("終了色", "color", ""),
        ),
        _bindings(
            strength=("強さ", "number"),
            center_x_px=("中心X", "number"),
            center_y_px=("中心Y", "number"),
            angle_degrees=("角度", "number"),
            width_px=("幅", "number"),
            blend_mode=("合成モード", "select"),
            shape=("形状", "select"),
            start_color=("開始色", "color"),
            end_color=("終了色", "color"),
        ),
        _enums(
            blend_mode={"normal": "通常", "add": "加算", "multiply": "乗算"},
            shape={"linear": "線形", "radial": "円形"},
        ),
    ),
    "crop": EffectProfileDefinition(
        "crop",
        "クリッピング",
        _VIDEO,
        _items(
            ("上", "number", StaticValue(0.0)),
            ("下", "number", StaticValue(0.0)),
            ("左", "number", StaticValue(0.0)),
            ("右", "number", StaticValue(0.0)),
            ("中心の位置を変更", "check", False),
        ),
        _bindings(
            top_px=("上", "number"),
            bottom_px=("下", "number"),
            left_px=("左", "number"),
            right_px=("右", "number"),
            move_center=("中心の位置を変更", "check"),
        ),
    ),
    "mask": EffectProfileDefinition(
        "mask",
        "マスク",
        _VIDEO,
        _items(
            ("X", "number", StaticValue(0.0)),
            ("Y", "number", StaticValue(0.0)),
            ("回転", "number", StaticValue(0.0)),
            ("サイズ", "number", StaticValue(100.0)),
            ("縦横比", "number", StaticValue(0.0)),
            ("ぼかし", "number", StaticValue(0.0)),
            ("マスクの種類", "mask", "円"),
            ("シーンの長さを合わせる", "check", False),
            ("マスクの反転", "check", False),
            ("元のサイズに合わせる", "check", False),
        ),
        _bindings(
            x_px=("X", "number"),
            y_px=("Y", "number"),
            rotation_degrees=("回転", "number"),
            size_px=("サイズ", "number"),
            aspect=("縦横比", "number"),
            blur_px=("ぼかし", "number"),
            kind=("マスクの種類", "select"),
            fit_scene=("シーンの長さを合わせる", "check"),
            invert=("マスクの反転", "check"),
            fit_source=("元のサイズに合わせる", "check"),
        ),
        _enums(
            kind={
                "circle": "円",
                "rectangle": "四角形",
                "triangle": "三角形",
                "star": "星型",
            }
        ),
    ),
    "resize": EffectProfileDefinition(
        "resize",
        "リサイズ",
        _VIDEO,
        _items(
            ("拡大率", "number", StaticValue(100.0)),
            ("X", "number", StaticValue(100.0)),
            ("Y", "number", StaticValue(100.0)),
            ("補間なし", "check", False),
            ("ピクセル数でサイズ指定", "check", False),
        ),
        _bindings(
            scale=("拡大率", "number"),
            width_px=("X", "number"),
            height_px=("Y", "number"),
            nearest=("補間なし", "check"),
        ),
    ),
    "mosaic": EffectProfileDefinition(
        "mosaic",
        "モザイク",
        _VIDEO,
        _items(("サイズ", "number", StaticValue(12.0)), ("タイル風", "check", False)),
        _bindings(size_px=("サイズ", "number"), tile=("タイル風", "check")),
    ),
    "blur": EffectProfileDefinition(
        "blur",
        "ぼかし",
        _VIDEO,
        _items(
            ("範囲", "number", StaticValue(5.0)),
            ("縦横比", "number", StaticValue(0.0)),
            ("光の強さ", "number", StaticValue(0.0)),
            ("サイズ固定", "check", False),
        ),
        _bindings(
            radius_px=("範囲", "number"),
            aspect=("縦横比", "number"),
            light_strength=("光の強さ", "number"),
            fixed_size=("サイズ固定", "check"),
        ),
    ),
    "directional_blur": EffectProfileDefinition(
        "directional_blur",
        "方向ブラー",
        _VIDEO,
        _items(
            ("範囲", "number", StaticValue(20.0)),
            ("角度", "number", StaticValue(50.0)),
            ("サイズ固定", "check", False),
        ),
        _bindings(
            radius_px=("範囲", "number"),
            angle_degrees=("角度", "number"),
            fixed_size=("サイズ固定", "check"),
        ),
    ),
    "motion_blur": EffectProfileDefinition(
        "motion_blur",
        "放射ブラー",
        _VIDEO,
        _items(
            ("範囲", "number", StaticValue(20.0)),
            ("X", "number", StaticValue(0.0)),
            ("Y", "number", StaticValue(0.0)),
            ("サイズ固定", "check", False),
        ),
        _bindings(
            radius_px=("範囲", "number"),
            center_x_px=("X", "number"),
            center_y_px=("Y", "number"),
            fixed_size=("サイズ固定", "check"),
        ),
    ),
    "glow": EffectProfileDefinition(
        "glow",
        "グロー",
        _VIDEO,
        _items(
            ("強さ", "number", StaticValue(40.0)),
            ("拡散", "number", StaticValue(50.0)),
            ("角度", "number", StaticValue(25.0)),
            ("しきい値", "number", StaticValue(80.0)),
            ("比率", "number", StaticValue(100.0)),
            ("ぼかし", "number", StaticValue(1.0)),
            ("形状", "select", "クロス(4本)"),
            ("光色", "color", ""),
            ("光成分のみ", "check", False),
            ("サイズ固定", "check", False),
        ),
        _bindings(
            strength=("強さ", "number"),
            diffusion=("拡散", "number"),
            angle_degrees=("角度", "number"),
            threshold=("しきい値", "number"),
            ratio=("比率", "number"),
            blur=("ぼかし", "number"),
            shape=("形状", "select"),
            color=("光色", "color"),
            light_only=("光成分のみ", "check"),
            fixed_size=("サイズ固定", "check"),
        ),
        _enums(
            shape={"cross4": "クロス(4本)", "cross6": "クロス(6本)", "circle": "円"}
        ),
    ),
    "emission": EffectProfileDefinition(
        "emission",
        "発光",
        _VIDEO,
        _items(
            ("強さ", "number", StaticValue(100.0)),
            ("拡散", "number", StaticValue(250.0)),
            ("しきい値", "number", StaticValue(80.0)),
            ("拡散速度", "number", StaticValue(0.0)),
            ("光色", "color", ""),
            ("サイズ固定", "check", False),
        ),
        _bindings(
            strength=("強さ", "number"),
            diffusion=("拡散", "number"),
            threshold=("しきい値", "number"),
            diffusion_speed=("拡散速度", "number"),
            color=("光色", "color"),
            fixed_size=("サイズ固定", "check"),
        ),
    ),
    "outline": EffectProfileDefinition(
        "outline",
        "縁取り",
        _VIDEO,
        _items(
            ("サイズ", "number", StaticValue(5.0)),
            ("ぼかし", "number", StaticValue(5.0)),
            ("縁色", "color", "ffffff"),
            ("パターン画像", "combo", ""),
        ),
        _bindings(
            size_px=("サイズ", "number"),
            blur_px=("ぼかし", "number"),
            color=("縁色", "color"),
            pattern_file=("パターン画像", "file"),
        ),
    ),
    "drop_shadow": EffectProfileDefinition(
        "drop_shadow",
        "ドロップシャドウ",
        _VIDEO,
        _items(
            ("X", "number", StaticValue(-40.0)),
            ("Y", "number", StaticValue(24.0)),
            ("濃さ", "number", StaticValue(40.0)),
            ("拡散", "number", StaticValue(10.0)),
            ("影色", "color", "000000"),
            ("影を別オブジェクトで描画", "check", False),
        ),
        _bindings(
            x_px=("X", "number"),
            y_px=("Y", "number"),
            opacity=("濃さ", "opacity"),
            diffusion_px=("拡散", "number"),
            color=("影色", "color"),
            separate_object=("影を別オブジェクトで描画", "check"),
        ),
    ),
    "chroma_key": EffectProfileDefinition(
        "chroma_key",
        "クロマキー",
        _VIDEO,
        _items(
            ("色相範囲", "number", StaticValue(16.0)),
            ("彩度範囲", "number", StaticValue(96.0)),
            ("境界補正", "number", StaticValue(1.0)),
            ("基準色", "color", ""),
            ("色彩補正", "check", False),
            ("透過補正", "check", False),
        ),
        _bindings(
            hue_range=("色相範囲", "number"),
            saturation_range=("彩度範囲", "number"),
            boundary=("境界補正", "number"),
            color=("基準色", "color"),
            color_correction=("色彩補正", "check"),
            transparency_correction=("透過補正", "check"),
        ),
    ),
    "luminance_key": EffectProfileDefinition(
        "luminance_key",
        "ルミナンスキー",
        _VIDEO,
        _items(
            ("基準輝度", "number", StaticValue(2048.0)),
            ("輝度範囲", "number", StaticValue(512.0)),
            ("モード", "select", "暗い部分を透過"),
        ),
        _bindings(
            threshold=("基準輝度", "number"),
            range=("輝度範囲", "number"),
            mode=("モード", "select"),
        ),
        _enums(mode={"dark": "暗い部分を透過", "light": "明るい部分を透過"}),
    ),
    "fade": EffectProfileDefinition(
        "fade",
        "フェード",
        _VIDEO,
        _items(
            ("イン", "number", StaticValue(0.5)), ("アウト", "number", StaticValue(0.5))
        ),
        _bindings(in_seconds=("イン", "number"), out_seconds=("アウト", "number")),
    ),
    "wipe": EffectProfileDefinition(
        "wipe",
        "ワイプ",
        _VIDEO,
        _items(
            ("イン", "number", StaticValue(0.5)),
            ("アウト", "number", StaticValue(0.5)),
            ("ぼかし", "number", StaticValue(2.0)),
            ("ワイプの種類", "select", "ワイプ(円)"),
            ("縦横比固定", "check", True),
            ("反転(イン)", "check", False),
            ("反転(アウト)", "check", False),
        ),
        _bindings(
            in_seconds=("イン", "number"),
            out_seconds=("アウト", "number"),
            blur_px=("ぼかし", "number"),
            kind=("ワイプの種類", "select"),
            preserve_aspect=("縦横比固定", "check"),
            invert_in=("反転(イン)", "check"),
            invert_out=("反転(アウト)", "check"),
        ),
        _enums(
            kind={"circle": "ワイプ(円)", "square": "ワイプ(四角)", "clock": "時計"}
        ),
    ),
    "audio_gain": EffectProfileDefinition(
        "audio_gain",
        "音量調整",
        _AUDIO,
        _items(
            ("音量", "number", StaticValue(100.0)), ("左右", "number", StaticValue(0.0))
        ),
        _bindings(volume=("音量", "number"), pan=("左右", "number")),
    ),
    "audio_fade": EffectProfileDefinition(
        "audio_fade",
        "音量フェード",
        _AUDIO,
        _items(
            ("イン", "number", StaticValue(0.5)), ("アウト", "number", StaticValue(0.5))
        ),
        _bindings(in_seconds=("イン", "number"), out_seconds=("アウト", "number")),
    ),
}


def available_effect_profiles() -> tuple[str, ...]:
    """Return the stable ordered set of curated profile identifiers."""

    return EFFECT_PROFILES


def get_effect_profile(profile: str) -> EffectProfileDefinition:
    try:
        return _PROFILES[profile]
    except KeyError as error:
        raise EffectProfileUnavailableError(
            f"unknown effect profile: {profile!r}"
        ) from error


def _number(value: EffectParameterValue, name: str) -> NativeValue:
    if isinstance(value, LinearMotion):
        return AnimatedValue(
            value.start,
            value.end,
            AnimationParams("直線移動", "0"),
        )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric or linear(start, end)")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return StaticValue(number)


def _check(value: EffectParameterValue, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be bool")
    return value


def _color(value: EffectParameterValue, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be #RRGGBB")
    if value == "":
        return ""
    normalized = value.removeprefix("#").lower()
    if len(normalized) != 6 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{name} must be #RRGGBB")
    return normalized


def _file(value: EffectParameterValue, name: str) -> str:
    if not isinstance(value, (str, PathLike)):
        raise TypeError(f"{name} must be a file path")
    if str(value) == "":
        return ""
    return str(Path(value).expanduser().resolve())


def _select(
    definition: EffectProfileDefinition,
    parameter: str,
    value: EffectParameterValue,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{parameter} must be a named option")
    options = definition.enums.get(parameter)
    if options is None or value not in options:
        allowed = ", ".join(options or ())
        raise ValueError(f"{parameter} must be one of: {allowed}")
    return options[value]


def _convert(
    definition: EffectProfileDefinition,
    parameter: str,
    value: EffectParameterValue,
    binding: ParameterBinding,
) -> NativeValue:
    if binding.kind == "number":
        return _number(value, parameter)
    if binding.kind == "check":
        return _check(value, parameter)
    if binding.kind == "color":
        return _color(value, parameter)
    if binding.kind == "file":
        return _file(value, parameter)
    if binding.kind == "select":
        return _select(definition, parameter, value)
    if binding.kind == "opacity":
        converted = _number(value, parameter)
        if isinstance(converted, AnimatedValue):
            for number in (converted.start, converted.end):
                if not 0.0 <= number <= 1.0:
                    raise ValueError(f"{parameter} must be between 0.0 and 1.0")
            return AnimatedValue(
                converted.start * 100.0,
                converted.end * 100.0,
                converted.animation,
            )
        assert isinstance(converted, StaticValue)
        number = float(converted.value)
        if not 0.0 <= number <= 1.0:
            raise ValueError(f"{parameter} must be between 0.0 and 1.0")
        return StaticValue(number * 100.0)
    raise AssertionError(f"unsupported binding kind: {binding.kind}")


def _resolve_profile(spec: EffectSpec) -> ResolvedEffect:
    definition = get_effect_profile(spec.profile)
    missing = sorted(set(spec.parameters) - set(definition.parameters))
    if missing:
        raise TypeError(
            f"unsupported {spec.profile} parameter(s): " + ", ".join(missing)
        )
    if spec.profile == "resize":
        has_width = "width_px" in spec.parameters
        has_height = "height_px" in spec.parameters
        if has_width != has_height:
            raise ValueError("resize width_px and height_px must be supplied together")
        if has_width and "scale" in spec.parameters:
            raise ValueError("resize uses scale or width_px/height_px, not both")
    resolved = {item.name: item.default for item in definition.items}
    for name, value in spec.parameters.items():
        binding = definition.parameters[name]
        resolved[binding.item] = _convert(definition, name, value, binding)
    if spec.profile == "resize" and "width_px" in spec.parameters:
        resolved["ピクセル数でサイズ指定"] = True
    return ResolvedEffect(
        profile=spec.profile,
        native_name=definition.native_name,
        scope=definition.scope,
        items=tuple((item.name, resolved[item.name]) for item in definition.items),
        item_types=MappingProxyType(
            {item.name: item.kind for item in definition.items}
        ),
        enabled=spec.enabled,
        verified=True,
    )


def _resolve_native(spec: NativeEffectSpec) -> ResolvedEffect:
    values: list[tuple[str, NativeValue]] = []
    for name, value in spec.values.items():
        if isinstance(value, LinearMotion):
            converted: NativeValue = _number(value, name)
        elif isinstance(value, PathLike):
            converted = _file(value, name)
        else:
            converted = value
        values.append((name, converted))
    return ResolvedEffect(
        profile=None,
        native_name=spec.name,
        scope=spec.scope,
        items=tuple(values),
        item_types=MappingProxyType({}),
        enabled=spec.enabled,
        verified=False,
    )


def resolve_effect(
    spec: EffectDefinition,
    *,
    project_version: int = AUP2_EFFECT_MANIFEST_VERSION,
) -> ResolvedEffect:
    """Resolve a semantic/native spec to ordered AviUtl2 item values."""

    if project_version not in AUP2_EFFECT_COMPATIBLE_PROJECT_VERSIONS:
        raise EffectProfileUnavailableError(
            f"no effect manifest is available for project version {project_version}"
        )
    if isinstance(spec, EffectSpec):
        return _resolve_profile(spec)
    if isinstance(spec, NativeEffectSpec):
        return _resolve_native(spec)
    raise TypeError("effect spec must be EffectSpec or NativeEffectSpec")


def legacy_compatibility_effect(
    profile: Literal["sharpen", "shake"],
    *,
    strength: float | None = None,
) -> NativeEffectSpec:
    """Return complete 2001901 templates retained for legacy CLI names."""

    if profile == "sharpen":
        return NativeEffectSpec(
            "シャープ",
            MappingProxyType(
                {
                    "強さ": 50.0 if strength is None else strength,
                    "範囲": 5.0,
                }
            ),
            True,
            "video",
        )
    if profile == "shake":
        amount = 10.0 if strength is None else strength
        return NativeEffectSpec(
            "振動",
            MappingProxyType(
                {
                    "X": amount,
                    "Y": amount,
                    "Z": 0.0,
                    "周期": 1.0,
                    "ランダムに強さを変える": True,
                    "複雑に振動": False,
                }
            ),
            True,
            "video",
        )
    raise AssertionError(f"unsupported compatibility profile: {profile}")


__all__ = [
    "AUP2_EFFECT_COMPATIBLE_PROJECT_VERSIONS",
    "AUP2_EFFECT_MANIFEST_VERSION",
    "EffectProfileDefinition",
    "EffectProfileUnavailableError",
    "ItemTemplate",
    "NativeValue",
    "ParameterBinding",
    "ResolvedEffect",
    "available_effect_profiles",
    "get_effect_profile",
    "legacy_compatibility_effect",
    "resolve_effect",
]
