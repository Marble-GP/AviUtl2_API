"""Add one title with the high-level Live Bridge API and render it natively."""

from aviutl2_api.editing import effect
from aviutl2_api.live import LiveProject


def main() -> None:
    # With one enabled AviUtl2 window, no PID, frame, layer, or revision is needed.
    with LiveProject.connect() as project:
        title = project.add_text(
            "AviUtl2 Live Bridge",
            duration=90,
            y=-180,
            size=72,
            color="#FFFFFF",
            effects=[
                effect("glow", strength=45, color="#FFD966"),
                effect("outline", size_px=4, color="#202040"),
            ],
        )
        rendered = project.render(title.primary)

    print(
        f"Added {title.primary.object_id} on Layer {title.primary.layer} "
        f"at frames {title.primary.frame_start}-{title.primary.frame_end}."
    )
    print(
        f"AviUtl2 native render: frame={rendered.frame}, "
        f"size={rendered.width}x{rendered.height}, sha256={rendered.sha256}"
    )


if __name__ == "__main__":
    main()
