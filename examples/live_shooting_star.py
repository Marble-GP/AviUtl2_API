"""Create a colorful shooting star using only the high-level Live API."""

from __future__ import annotations

from aviutl2_api.editing import EditPlan, effect, linear
from aviutl2_api.live import LiveProject


def shooting_star(duration: int) -> EditPlan:
    """Build a backend-neutral plan placed at the GUI cursor automatically."""

    plan = EditPlan(sequence="parallel")
    plan.add_shape(
        "star",
        key="star",
        duration=duration,
        width=120,
        height=120,
        color="#FFF4B8",
        effects=[
            effect("glow", strength=65, color="#FFF4B8"),
            effect("outline", size_px=2, color="#FFFFFF"),
        ],
        x=linear(1100, -1100),
        y=linear(-540, 540),
        rotation=linear(0, 360),
    )

    trail = (
        ("circle", 120, -60, 56, "#B9F6FF", 0.90),
        ("triangle", 230, -115, 44, "#75E6FF", 0.78),
        ("circle", 335, -168, 34, "#7EA7FF", 0.66),
        ("heart", 435, -218, 25, "#D58CFF", 0.54),
        ("star", 530, -265, 18, "#FF8FD8", 0.42),
    )
    for index, (shape, dx, dy, size, color, opacity) in enumerate(trail):
        plan.add_shape(
            shape,
            key=f"trail-{index}",
            duration=duration,
            width=size,
            height=size,
            color=color,
            effects=[effect("glow", strength=35, color=color)],
            opacity=opacity,
            x=linear(1100 + dx, -1100 + dx),
            y=linear(-540 + dy, 540 + dy),
        )

    plan.add_text(
        "Shooting Star",
        key="title",
        duration=duration,
        y=360,
        size=64,
        color="#FFFFFF",
        effects=[
            effect("outline", size_px=4, color="#202040"),
            effect("drop_shadow", x_px=6, y_px=6, opacity=0.55),
        ],
    )
    return plan


def main() -> None:
    with LiveProject.connect() as project:
        result = project.apply(shooting_star(duration=90))
        star = result.objects["star"].primary
        frames = (star.frame_start, star.midpoint, star.frame_end)
        sheet = project.contact_sheet(frames, columns=3)

    print(
        f"Created {result.applied_count} objects at frame {star.frame_start}; "
        f"undo_grouped={result.undo_grouped}, revision={result.revision}"
    )
    print(f"Native review frames: {sheet.frames} ({len(sheet.png)} PNG bytes)")


if __name__ == "__main__":
    main()
