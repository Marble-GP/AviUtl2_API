"""Prepare and run the AviUtl2 Live Bridge 0.9.5 manual acceptance test.

The run command intentionally leaves its objects in the GUI. Confirm their
Effect stacks, then press Ctrl+Z once and verify that every test object vanishes.
"""

from __future__ import annotations

import argparse
import json
import math
import wave
from array import array
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aviutl2_api.editing import EditPlan, PlanResult, effect
from aviutl2_api.live import AudioAnalysis, LiveProject

_PLUGIN_VERSION = "0.9.5"
_REQUIRED_CAPABILITIES = (
    "semantic_effect_profiles",
    "edit_plan_create_effect_stack",
    "media_group_effect_routing",
    "linear_effect_values",
)


def prepare_tone(path: Path, *, overwrite: bool) -> Path:
    """Write a three-second stereo tone used by native PCM review."""

    destination = path.expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 48_000
    frames = array("h")
    for index in range(sample_rate * 3):
        time = index / sample_rate
        envelope = min(1.0, time * 8.0, (3.0 - time) * 8.0)
        frames.append(round(math.sin(2.0 * math.pi * 440.0 * time) * 8192 * envelope))
        frames.append(round(math.sin(2.0 * math.pi * 660.0 * time) * 6144 * envelope))
    with wave.open(str(destination), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(frames.tobytes())
    return destination


def _build_plan(
    tone: Path,
    *,
    at: int | None,
    duration: int,
    video_with_audio: Path | None,
) -> tuple[EditPlan, dict[str, list[str]]]:
    plan = EditPlan(sequence="parallel")
    plan.add_shape(
        "circle",
        key="shape",
        at=at,
        duration=duration,
        width=420,
        height=420,
        x=-360,
        color="#40C8FF",
        effects=[
            effect(
                "gradient",
                angle_degrees=35,
                start_color="#40C8FF",
                end_color="#8040FF",
            ),
            effect("mosaic", size_px=10, tile=True),
            effect("drop_shadow", x_px=12, y_px=12, opacity=0.55),
        ],
    )
    plan.add_text(
        "Live Bridge 0.9.5",
        key="title",
        at=at,
        duration=duration,
        x=260,
        size=76,
        color="#FFFFFF",
        effects=[
            effect("glow", strength=55, color="#FFD966"),
            effect("glow", enabled=False, strength=20, color="#80C0FF"),
            effect("outline", size_px=4, color="#202040"),
        ],
    )
    plan.add_audio(
        tone,
        key="tone",
        at=at,
        duration=duration,
        effects=[
            effect("audio_gain", volume=80, pan=-10),
            effect("audio_fade", in_seconds=0.2, out_seconds=0.4),
        ],
    )
    expected = {
        "shape": ["gradient", "mosaic", "drop_shadow"],
        "title": ["glow", "glow", "outline"],
        "tone": ["audio_gain", "audio_fade"],
    }
    if video_with_audio is not None:
        plan.add_video(
            video_with_audio,
            key="av-media",
            at=at,
            duration=duration,
            y=300,
            scale=45,
            effects=[
                effect("glow", strength=25, color="#FFFFFF"),
                effect("audio_gain", volume=65, pan=15),
            ],
        )
        expected["av-media"] = ["glow", "audio_gain"]
    return plan, expected


def _verify_result(result: PlanResult, expected: dict[str, list[str]]) -> None:
    if not result.undo_grouped:
        raise RuntimeError("host did not report a single grouped Undo unit")
    for key, profiles in expected.items():
        actual = [receipt.profile for receipt in result.effects.get(key, ())]
        if actual != profiles:
            raise RuntimeError(
                f"{key} Effect order mismatch: {actual!r} != {profiles!r}"
            )
    title_enabled = [receipt.enabled for receipt in result.effects["title"]]
    if title_enabled != [True, False, True]:
        raise RuntimeError(f"title enabled-state mismatch: {title_enabled!r}")
    if "av-media" in expected:
        group = result.objects["av-media"]
        group_ids = {item.object_id for item in group}
        routed = {
            receipt.profile: receipt.object_id for receipt in result.effects["av-media"]
        }
        if not set(routed.values()).issubset(group_ids):
            raise RuntimeError(
                "video/audio Effects were routed outside the created media group"
            )
        if len(group) > 1 and routed["glow"] == routed["audio_gain"]:
            raise RuntimeError("a dedicated audio object was not selected")


def _result_payload(
    *,
    hello: dict[str, Any],
    capabilities: dict[str, Any],
    validation_warnings: tuple[str, ...],
    result: PlanResult,
    frames: tuple[int, ...],
    contact_sheet: Path,
    pcm: Path,
    audio_analysis: AudioAnalysis,
) -> dict[str, Any]:
    return {
        "plugin_version": hello.get("plugin_version"),
        "protocol_version": hello.get("protocol_version"),
        "capabilities": {
            name: capabilities.get(name) for name in _REQUIRED_CAPABILITIES
        },
        "aup2_effect_manifest_version": capabilities.get(
            "aup2_effect_manifest_version"
        ),
        "revision_before": result.revision_before,
        "revision_after": result.revision,
        "applied_count": result.applied_count,
        "undo_grouped": result.undo_grouped,
        "atomic": result.atomic,
        "warnings": list(result.warnings),
        "validation_warnings": list(validation_warnings),
        "objects": {
            key: [
                {
                    "object_id": value.object_id,
                    "layer": value.layer,
                    "frame_start": value.frame_start,
                    "frame_end": value.frame_end,
                }
                for value in group
            ]
            for key, group in result.objects.items()
        },
        "effects": {
            key: [
                {
                    "profile": value.profile,
                    "native_name": value.native_name,
                    "selector": value.selector,
                    "enabled": value.enabled,
                    "object_id": value.object_id,
                    "values": dict(value.values),
                }
                for value in receipts
            ]
            for key, receipts in result.effects.items()
        },
        "native_review": {
            "frames": list(frames),
            "contact_sheet": str(contact_sheet),
            "pcm_f32le": str(pcm),
            "audio": asdict(audio_analysis),
        },
        "manual_next_step": (
            "Inspect the stacks in AviUtl2, then press Ctrl+Z exactly once and "
            "confirm that every object listed above disappears."
        ),
    }


def run_acceptance(
    *,
    pid: int | None,
    tone: Path,
    output_dir: Path,
    at: int | None,
    duration: int,
    video_with_audio: Path | None,
    overwrite_results: bool,
) -> Path:
    tone = tone.expanduser().resolve()
    if not tone.is_file():
        raise FileNotFoundError(f"test tone does not exist: {tone}")
    if video_with_audio is not None:
        video_with_audio = video_with_audio.expanduser().resolve()
        if not video_with_audio.is_file():
            raise FileNotFoundError(f"video does not exist: {video_with_audio}")
    output_dir = output_dir.expanduser().resolve()
    contact_path = output_dir / "02_live_contact_sheet.png"
    pcm_path = output_dir / "02_live_audio.f32le"
    result_path = output_dir / "02_live_result.json"
    if not overwrite_results:
        existing = [
            path for path in (contact_path, pcm_path, result_path) if path.exists()
        ]
        if existing:
            raise FileExistsError(f"refusing to overwrite result file(s): {existing}")
    output_dir.mkdir(parents=True, exist_ok=True)

    plan, expected = _build_plan(
        tone,
        at=at,
        duration=duration,
        video_with_audio=video_with_audio,
    )
    with LiveProject.connect(pid=pid) as project:
        hello = project.client.hello()
        capabilities = project.client.get_capabilities()
        if hello.get("plugin_version") != _PLUGIN_VERSION:
            actual_version = hello.get("plugin_version")
            raise RuntimeError(
                f"expected plugin {_PLUGIN_VERSION}, got {actual_version!r}"
            )
        missing = [
            name
            for name in _REQUIRED_CAPABILITIES
            if capabilities.get(name) is not True
        ]
        if missing:
            raise RuntimeError(f"required capability is unavailable: {missing}")
        if capabilities.get("aup2_effect_manifest_version") != 2_001_901:
            raise RuntimeError("unexpected .aup2 Effect manifest version")
        validation = project.validate(plan)
        if not validation.valid:
            raise RuntimeError(f"plan validation failed: {validation.errors}")
        result = project.apply(plan)
        _verify_result(result, expected)
        title = result.objects["title"].primary
        frames = (title.frame_start, title.midpoint, title.frame_end)
        contact_sheet = project.contact_sheet(frames, columns=3)
        audio, analysis = project.audio_review(title.frame_start, title.frame_end)
        contact_sheet.save(contact_path, overwrite=overwrite_results)
        audio.save_pcm(pcm_path, overwrite=overwrite_results)

    payload = _result_payload(
        hello=hello,
        capabilities=capabilities,
        validation_warnings=validation.warnings,
        result=result,
        frames=frames,
        contact_sheet=contact_path,
        pcm=pcm_path,
        audio_analysis=analysis,
    )
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("\n次はAviUtl2でEffect stackを確認し、Ctrl+Zを1回だけ実行してください。")
    return result_path


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("tone", type=Path)
    prepare.add_argument("--overwrite", action="store_true")
    run = subparsers.add_parser("run")
    run.add_argument("--pid", type=int)
    run.add_argument(
        "--tone",
        type=Path,
        default=Path(__file__).with_name("02_live_effect_tone.wav"),
    )
    run.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).with_name("results"),
    )
    run.add_argument("--at", type=int)
    run.add_argument("--duration", type=int, default=90)
    run.add_argument("--video-with-audio", type=Path)
    run.add_argument("--overwrite-results", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        print(prepare_tone(args.tone, overwrite=args.overwrite))
        return 0
    if args.duration <= 0:
        parser.error("--duration must be positive")
    run_acceptance(
        pid=args.pid,
        tone=args.tone,
        output_dir=args.output_dir,
        at=args.at,
        duration=args.duration,
        video_with_audio=args.video_with_audio,
        overwrite_results=args.overwrite_results,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
