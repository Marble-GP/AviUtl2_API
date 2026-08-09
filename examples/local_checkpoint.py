"""Apply a short local edit and create a new, guarded checkpoint."""

from __future__ import annotations

import argparse

from aviutl2_api import LocalProject, effect


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", help="Source .aup2; it will not be overwritten")
    args = parser.parse_args()

    local = LocalProject.load(args.project)
    title = local.add_text(
        "第一章",
        duration=90,
        y=-200,
        size=72,
        effects=[effect("glow", strength=50)],
    )
    receipt = local.checkpoint()

    print(f"Added {title.primary.object_id} in memory.")
    print(f"Checkpoint: {receipt.path}")
    print(f"SHA-256: {receipt.sha256}")


if __name__ == "__main__":
    main()
