"""Explicitly apply one plan to Local memory and an open AviUtl2 window."""

from __future__ import annotations

import argparse

from aviutl2_api import EditPlan, LiveProject, LocalProject, SyncSession, effect


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", help=".aup2 matching the scene open in AviUtl2")
    parser.add_argument("--pid", type=int, required=True, help="AviUtl2 process ID")
    parser.add_argument(
        "--checkpoint",
        action="store_true",
        help="Create a numbered checkpoint after successful synchronization",
    )
    args = parser.parse_args()

    local = LocalProject.load(args.project)
    plan = EditPlan().add_text(
        "第一章",
        key="title",
        duration=90,
        y=-200,
        size=72,
        effects=[effect("outline", size_px=4, color="#202040")],
    )

    with LiveProject.connect(pid=args.pid) as live:
        sync = SyncSession.bind(local, live)
        status = sync.status()
        if not status.clean:
            raise RuntimeError(
                f"synchronization state is {status.state}: {sync.diff()}"
            )
        result = sync.apply(plan)
        title = result.objects["title"].primary
        rendered = live.render(title.midpoint)
        print(
            f"Applied {result.operation_id}; revision={result.live_revision}; "
            f"native PNG sha256={rendered.sha256}"
        )

    if args.checkpoint:
        receipt = local.checkpoint()
        print(f"Checkpoint: {receipt.path}")
    else:
        print("No .aup2 file was written. Pass --checkpoint to create a new one.")


if __name__ == "__main__":
    main()
