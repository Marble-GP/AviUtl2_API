"""Interactive Local/Live synchronization acceptance for AviUtl2 API 0.9.6.

The source project must already be open in the selected AviUtl2 window and
must still match its on-disk contents. The script never overwrites that source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from aviutl2_api import EditPlan, LiveProject, LocalProject, SyncSession, effect


def _new_run_directory(root: Path) -> Path:
    timestamp = datetime.now().strftime("run-%Y%m%d-%H%M%S-%f")
    destination = root.expanduser().resolve() / timestamp
    destination.mkdir(parents=True, exist_ok=False)
    return destination


def _build_plan(video: Path, *, at: int, audio_test: Path | None = None) -> EditPlan:
    plan = EditPlan(sequence="parallel")
    plan.add_video(
        video,
        key="video",
        at=at,
        fit="contain",
        effects=[
            effect("glow", strength=20),
            effect("audio_gain", volume=80, pan=0),
        ],
    )
    plan.add_shape(
        "circle",
        key="shape",
        at=at,
        duration=90,
        x=-360,
        width=360,
        height=360,
        color="#40C8FF",
        effects=[effect("glow", strength=45, color="#80D8FF")],
    )
    plan.add_text(
        "AviUtl2 API 0.9.6 Sync",
        key="title",
        at=at,
        duration=90,
        x=240,
        size=68,
        color="#FFFFFF",
        effects=[effect("outline", size_px=4, color="#202040")],
    )
    if audio_test is not None:
        plan.add_audio(
            audio_test,
            key="audio_test",
            at=at,
            duration=90,
            effects=[effect("audio_gain", volume=80, pan=0)],
        )
    return plan


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path, help=".aup2 already open in AviUtl2")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--video-with-audio", type=Path, required=True)
    parser.add_argument(
        "--audio-test",
        type=Path,
        help="optional known non-silent audio used to prove native PCM review",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("manual-sync-0.9.6-results"),
    )
    parser.add_argument("--skip-undo-check", action="store_true")
    args = parser.parse_args()

    project_path = args.project.expanduser().resolve()
    video_path = args.video_with_audio.expanduser().resolve()
    audio_test_path = (
        args.audio_test.expanduser().resolve() if args.audio_test is not None else None
    )
    if not project_path.is_file():
        raise FileNotFoundError(f"project does not exist: {project_path}")
    if not video_path.is_file():
        raise FileNotFoundError(f"video does not exist: {video_path}")
    if audio_test_path is not None and not audio_test_path.is_file():
        raise FileNotFoundError(f"audio test file does not exist: {audio_test_path}")

    run_directory = _new_run_directory(args.output_root)
    local = LocalProject.load(project_path)
    original_hash = local.source_sha256
    common_start = max((item.frame_end for item in local.objects), default=-1) + 1
    plan = _build_plan(video_path, at=common_start, audio_test=audio_test_path)

    with LiveProject.connect(pid=args.pid) as live:
        with SyncSession.bind(local, live) as sync:
            before = sync.status()
            if not before.clean:
                raise RuntimeError(
                    f"initial synchronization is {before.state}: {before}"
                )

            result = sync.apply(plan, operation_id=f"acceptance-{run_directory.name}")
            title = result.objects["title"].primary
            video_group = result.objects["video"]
            rendered = live.render(title.midpoint)
            rendered.save(run_directory / "native-frame.png")

            audio, analysis = live.audio_review(
                video_group.primary.frame_start,
                min(
                    video_group.primary.frame_end, video_group.primary.frame_start + 89
                ),
            )
            audio.save_pcm(run_directory / "native-audio-f32le.pcm")
            checkpoint = local.checkpoint(run_directory / "sync-checkpoint.aup2")
            source_hash_after = hashlib.sha256(project_path.read_bytes()).hexdigest()
            if source_hash_after != original_hash:
                raise RuntimeError("the source .aup2 changed during explicit sync")

            report: dict[str, object] = {
                "operation_id": result.operation_id,
                "source": str(project_path),
                "source_sha256_before": original_hash,
                "source_sha256_after": local.source_sha256,
                "source_disk_sha256_after": source_hash_after,
                "source_file_unsaved": local.dirty,
                "checkpoint": str(checkpoint.path),
                "local_revision": result.local_revision,
                "live_revision": result.live_revision,
                "undo_grouped": result.undo_grouped,
                "atomic": result.atomic,
                "gui_undo_required": result.gui_undo_required,
                "warnings": result.warnings,
                "objects": {
                    key: [
                        {
                            "local_id": item.local.local_id,
                            "live_id": item.live.object_id,
                            "layer": item.layer,
                            "frame_start": item.frame_start,
                            "frame_end": item.frame_end,
                            "alias": item.live.snapshot_object.alias,
                        }
                        for item in group
                    ]
                    for key, group in result.objects.items()
                },
                "native_frame_sha256": rendered.sha256,
                "native_audio_sha256": audio.sha256,
                "audio_analysis": {
                    "peak": analysis.peak,
                    "rms": analysis.rms,
                    "clipping_samples": analysis.clipping_samples,
                    "silence_ratio": analysis.silence_ratio,
                    "integrated_lufs": analysis.integrated_lufs,
                },
            }

            if not args.skip_undo_check:
                print("AviUtl2でCtrl+Zを1回押し、全テストobjectが消えたらEnter。")
                input()
                after_undo = sync.status()
                report["after_undo"] = {
                    "state": after_undo.state,
                    "local_changed_since_bind": after_undo.local_changed_since_bind,
                    "live_changed_since_bind": after_undo.live_changed_since_bind,
                    "local_only": len(after_undo.diff.local_only),
                    "live_only": len(after_undo.diff.live_only),
                    "changed": len(after_undo.diff.changed),
                }
                report_path = run_directory / "acceptance.json"
                report_path.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                if after_undo.state != "diverged":
                    raise RuntimeError(
                        "one GUI Undo did not produce the expected diverged state"
                    )
                if after_undo.diff.live_only or after_undo.diff.changed:
                    raise RuntimeError("one GUI Undo left unexpected Live changes")

            report_path = run_directory / "acceptance.json"
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"Acceptance artifacts: {run_directory}")
            print(f"Open this checkpoint manually: {checkpoint.path}")


if __name__ == "__main__":
    main()
