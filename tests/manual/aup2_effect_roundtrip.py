"""Generate and compare the manual AviUtl2 Open/Save Effect fixture.

Usage:
    python tests/manual/aup2_effect_roundtrip.py generate before.aup2
    # Open before.aup2 in AviUtl2, then Save As after.aup2.
    python tests/manual/aup2_effect_roundtrip.py compare before.aup2 after.aup2
"""

from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

from aviutl2_api import (
    apply_effects,
    compare_aup2_roundtrip,
    parse_file,
    serialize_to_file,
    validate_standard_effects,
)
from aviutl2_api.editing import (
    EFFECT_PROFILES,
    EffectParameterValue,
    EffectSpec,
)
from aviutl2_api.models import Effect, Project, Scene, StaticValue, TimelineObject


def _draw_effect() -> Effect:
    return Effect(
        1,
        "標準描画",
        {
            "X": StaticValue(0.0),
            "Y": StaticValue(0.0),
            "Z": StaticValue(0.0),
            "Group": StaticValue(1.0),
            "中心X": StaticValue(0.0),
            "中心Y": StaticValue(0.0),
            "中心Z": StaticValue(0.0),
            "X軸回転": StaticValue(0.0),
            "Y軸回転": StaticValue(0.0),
            "Z軸回転": StaticValue(0.0),
            "拡大率": StaticValue(100.0),
            "縦横比": StaticValue(0.0),
            "透明度": StaticValue(0.0),
            "合成モード": "通常",
        },
    )


def _shape(object_id: int, frame: int) -> TimelineObject:
    return TimelineObject(
        object_id,
        0,
        frame,
        frame + 29,
        [
            Effect(
                0,
                "図形",
                {
                    "図形の種類": "円",
                    "サイズ": StaticValue(200.0),
                    "縦横比": StaticValue(0.0),
                    "ライン幅": StaticValue(4000.0),
                    "色": "ffd966",
                    "角を丸くする": StaticValue(0.0),
                },
            ),
            _draw_effect(),
        ],
    )


def _audio(object_id: int, frame: int, file: Path) -> TimelineObject:
    return TimelineObject(
        object_id,
        1,
        frame,
        frame + 29,
        [
            Effect(
                0,
                "音声ファイル",
                {
                    "再生位置": StaticValue(0.0),
                    "再生速度": StaticValue(100.0),
                    "ファイル": str(file.resolve()),
                    "トラック": StaticValue(0.0),
                    "ループ再生": StaticValue(0.0),
                },
            ),
            Effect(
                1,
                "音声再生",
                {"音量": StaticValue(100.0), "左右": StaticValue(0.0)},
            ),
        ],
    )


_NON_DEFAULTS: dict[str, dict[str, EffectParameterValue]] = {
    "color_adjustment": {"brightness": 110, "clamp": False},
    "monochrome": {"strength": 75, "color": "#FFD966"},
    "gradient": {"angle_degrees": 30, "start_color": "#FF8040"},
    "crop": {"top_px": 8, "left_px": 12},
    "mask": {"kind": "rectangle", "size_px": 160, "invert": True},
    "resize": {"scale": 80, "nearest": True},
    "mosaic": {"size_px": 20, "tile": True},
    "blur": {"radius_px": 8, "fixed_size": True},
    "directional_blur": {"radius_px": 16, "angle_degrees": 25},
    "motion_blur": {"radius_px": 18, "center_x_px": 20},
    "glow": {"strength": 50, "color": "#FFD966"},
    "emission": {"strength": 65, "color": "#80C0FF"},
    "outline": {"size_px": 4, "color": "#202040"},
    "drop_shadow": {"x_px": 8, "y_px": 8, "opacity": 0.6},
    "chroma_key": {"hue_range": 30, "color": "#00FF00"},
    "luminance_key": {"threshold": 35, "range": 12},
    "fade": {"in_seconds": 0.25, "out_seconds": 0.5},
    "wipe": {"in_seconds": 0.3, "kind": "circle", "blur_px": 4},
    "audio_gain": {"volume": 80, "pan": -10},
    "audio_fade": {"in_seconds": 0.2, "out_seconds": 0.4},
}


def _write_silent_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48_000)
        output.writeframes(bytes(48_000 * 2 * 2))


def generate(output: Path, *, overwrite: bool) -> None:
    audio_file = output.with_suffix(".wav")
    for path in (output, audio_file):
        if path.exists() and not overwrite:
            raise FileExistsError(f"refusing to overwrite {path}")
    _write_silent_wav(audio_file)
    project = Project(scenes=[Scene(scene_id=0)])
    scene = project.scenes[0]
    object_id = 0
    for profile in EFFECT_PROFILES:
        for parameters in ({}, _NON_DEFAULTS[profile]):
            frame = object_id * 35
            obj = (
                _audio(object_id, frame, audio_file)
                if profile.startswith("audio_")
                else _shape(object_id, frame)
            )
            scene.objects.append(obj)
            apply_effects(project, obj, EffectSpec(profile, parameters))
            object_id += 1
    validation = validate_standard_effects(project)
    if not validation.valid:
        raise RuntimeError(validation.errors)
    serialize_to_file(project, output)
    print(f"generated {output} with {object_id} Effect test objects")


def compare(before_path: Path, after_path: Path) -> int:
    before = parse_file(before_path)
    after = parse_file(after_path)
    # AviUtl2 may upgrade the project format version while saving. Validate the
    # generated source against its manifest, then use semantic comparison for
    # the host output instead of claiming an unverified newer manifest.
    validation = validate_standard_effects(before)
    report = compare_aup2_roundtrip(before, after)
    payload = {
        "compatible": report.compatible and validation.valid,
        "normalizations": report.normalizations,
        "differences": [
            {"code": value.code, "message": value.message}
            for value in report.differences
        ],
        "validation_errors": [
            {"code": value.code, "message": value.message}
            for value in validation.errors
        ],
        "unverified": [value.message for value in validation.unverified],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["compatible"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("output", type=Path)
    generate_parser.add_argument("--overwrite", action="store_true")
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("before", type=Path)
    compare_parser.add_argument("after", type=Path)
    args = parser.parse_args()
    if args.command == "generate":
        generate(args.output, overwrite=args.overwrite)
        return 0
    return compare(args.before, args.after)


if __name__ == "__main__":
    raise SystemExit(main())
