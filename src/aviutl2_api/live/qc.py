"""Read-only edit preflight over native snapshots, catalogs, and renders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .audio import AudioAnalysis
from .media import MediaInventory
from .snapshot import ProjectSnapshot

if TYPE_CHECKING:
    from .client import LiveClient


@dataclass(frozen=True, slots=True)
class PreflightIssue:
    severity: Literal["error", "warning"]
    code: str
    message: str
    object_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreflightReport:
    revision: int
    scene_id: int
    snapshot: ProjectSnapshot
    media: MediaInventory
    issues: tuple[PreflightIssue, ...]
    audio: AudioAnalysis | None = None

    @property
    def ready(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def errors(self) -> tuple[PreflightIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[PreflightIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")


def _catalog_strings(
    client: LiveClient,
    method: Literal["font", "module"],
) -> set[str]:
    values: set[str] = set()
    start = 0
    while True:
        page = (
            client.get_font_catalog(start=start, count=256)
            if method == "font"
            else client.get_module_catalog(start=start, count=256)
        )
        entries = page.get("entries")
        next_start = page.get("next_start")
        if not isinstance(entries, list):
            raise ConnectionError(f"Live Bridge returned an invalid {method} catalog")
        for entry in entries:
            if method == "font" and isinstance(entry, str):
                values.add(entry)
            elif (
                method == "module"
                and isinstance(entry, dict)
                and isinstance(entry.get("name"), str)
            ):
                values.add(entry["name"])
        if next_start is None:
            return values
        if not isinstance(next_start, int) or isinstance(next_start, bool):
            raise ConnectionError(f"Live Bridge returned invalid {method} paging")
        start = next_start


def _effect_catalog_names(client: LiveClient) -> set[str]:
    names: set[str] = set()
    start = 0
    while True:
        page = client.get_effect_catalog(start=start, count=128)
        names.update(effect.name for effect in page.effects)
        if page.next_start is None:
            return names
        start = page.next_start


def _timeline_continuity_issues(
    snapshot: ProjectSnapshot,
) -> tuple[PreflightIssue, ...]:
    """Report timeline continuity without rejecting valid transition layouts."""
    issues: list[PreflightIssue] = []
    by_layer: dict[int, list[tuple[int, int, str]]] = {}
    for obj in snapshot.objects:
        by_layer.setdefault(obj.layer, []).append(
            (obj.frame_start, obj.frame_end, obj.object_id)
        )
    for layer, ranges in by_layer.items():
        ranges.sort()
        for previous, current in zip(ranges, ranges[1:]):
            if current[0] <= previous[1]:
                issues.append(
                    PreflightIssue(
                        "warning",
                        "TIMELINE_COLLISION",
                        (
                            f"Objects overlap on layer {layer}; this may be "
                            "an intentional transition."
                        ),
                        (previous[2], current[2]),
                    )
                )
            elif current[0] > previous[1] + 1:
                issues.append(
                    PreflightIssue(
                        "warning",
                        "TIMELINE_GAP",
                        (
                            f"Layer {layer} has a gap from frame "
                            f"{previous[1] + 1} to {current[0] - 1}."
                        ),
                        (previous[2], current[2]),
                    )
                )
    return tuple(issues)


def run_preflight(
    client: LiveClient,
    *,
    subtitle_layers: tuple[int, ...] | None = None,
    subtitle_overlap: Literal["allow", "warn", "error"] = "allow",
    minimum_subtitle_frames: int = 6,
    audio_range: tuple[int, int] | None = None,
    clipping_threshold: float = 1.0,
) -> PreflightReport:
    """Inspect the current project without writing files or changing playback."""
    if minimum_subtitle_frames < 1:
        raise ValueError("minimum_subtitle_frames must be positive")
    if subtitle_overlap not in {"allow", "warn", "error"}:
        raise ValueError("subtitle_overlap must be allow, warn, or error")
    snapshot = client.get_snapshot(include_alias=False)
    media = client.get_media_inventory()
    if media.revision != snapshot.revision:
        raise ConnectionError(
            "the project changed while preflight captured its inventory"
        )
    issues: list[PreflightIssue] = []
    if media.missing_count:
        issues.append(
            PreflightIssue(
                "error",
                "MISSING_MEDIA",
                f"{media.missing_count} referenced media item(s) are missing.",
                tuple(item.object_id for item in media.files if not item.exists),
            )
        )
    if media.unreadable_count:
        issues.append(
            PreflightIssue(
                "error",
                "UNREADABLE_MEDIA",
                f"{media.unreadable_count} media item(s) are unreadable.",
                tuple(
                    item.object_id
                    for item in media.files
                    if item.exists and not item.readable
                ),
            )
        )

    project = client.get_project_info()
    layer_max = project.get("layer_max")
    if not isinstance(layer_max, int) or isinstance(layer_max, bool):
        raise ConnectionError("Live Bridge returned invalid project layer data")
    locked_layers: set[int] = set()
    start = 0
    while start <= layer_max:
        page = client.get_layers(
            start=start,
            count=min(256, layer_max - start + 1),
        )
        if page.revision != snapshot.revision:
            raise ConnectionError(
                "the project changed while preflight inspected layers"
            )
        locked_layers.update(layer.layer for layer in page.layers if layer.locked)
        if not page.layers:
            break
        start += len(page.layers)
    if locked_layers:
        issues.append(
            PreflightIssue(
                "warning",
                "LOCKED_LAYERS",
                "Locked layers will be rejected by external mutations: "
                + ", ".join(str(value) for value in sorted(locked_layers)),
            )
        )
    locked_objects = tuple(obj.object_id for obj in snapshot.objects if obj.api_locked)
    if locked_objects:
        issues.append(
            PreflightIssue(
                "warning",
                "API_LOCKED_OBJECTS",
                f"{len(locked_objects)} object(s) reject external edits.",
                locked_objects,
            )
        )

    issues.extend(_timeline_continuity_issues(snapshot))

    fonts = _catalog_strings(client, "font")
    modules = _catalog_strings(client, "module")
    catalog_effects = _effect_catalog_names(client)
    del modules  # The SDK does not expose an effect-to-module ownership map.
    subtitle_ranges: list[tuple[int, int, str]] = []
    for obj in snapshot.objects:
        inspection = client.inspect_object(obj)
        if inspection.revision != snapshot.revision:
            raise ConnectionError(
                "the project changed while preflight inspected objects"
            )
        locked_effects = tuple(
            effect.selector for effect in inspection.effects if effect.locked
        )
        if locked_effects:
            issues.append(
                PreflightIssue(
                    "warning",
                    "LOCKED_EFFECTS",
                    "Locked effects reject external edits: "
                    + ", ".join(locked_effects),
                    (obj.object_id,),
                )
            )
        missing_effects = tuple(
            effect.name
            for effect in inspection.effects
            if effect.name not in catalog_effects
        )
        if missing_effects:
            issues.append(
                PreflightIssue(
                    "error",
                    "EFFECT_OR_MODULE_MISSING",
                    "Effect is absent from the current host catalog: "
                    + ", ".join(missing_effects),
                    (obj.object_id,),
                )
            )
        is_text = False
        for effect in inspection.effects:
            for item in effect.items:
                if item.type == "font" and item.raw_value:
                    if item.raw_value not in fonts:
                        issues.append(
                            PreflightIssue(
                                "error",
                                "FONT_MISSING",
                                f"Font is not installed: {item.raw_value}",
                                (obj.object_id,),
                            )
                        )
                if item.type == "text":
                    is_text = True
        if is_text and subtitle_layers is not None and obj.layer in subtitle_layers:
            if obj.duration_frames < minimum_subtitle_frames:
                issues.append(
                    PreflightIssue(
                        "warning",
                        "SUBTITLE_TOO_SHORT",
                        (
                            f"Text object is shown for only "
                            f"{obj.duration_frames} frame(s)."
                        ),
                        (obj.object_id,),
                    )
                )
            subtitle_ranges.append((obj.frame_start, obj.frame_end, obj.object_id))
    subtitle_ranges.sort()
    if subtitle_overlap != "allow":
        for previous, current in zip(
            subtitle_ranges,
            subtitle_ranges[1:],
        ):
            if current[0] <= previous[1]:
                issues.append(
                    PreflightIssue(
                        "warning" if subtitle_overlap == "warn" else "error",
                        "SUBTITLE_OVERLAP",
                        "Subtitle display times overlap.",
                        (previous[2], current[2]),
                    )
                )

    audio: AudioAnalysis | None = None
    if audio_range is not None:
        frame_start, frame_end = audio_range
        capture = client.render_audio(
            frame_start=frame_start,
            frame_end=frame_end,
            expected_revision=snapshot.revision,
        )
        audio = capture.analyze(clipping_threshold=clipping_threshold)
        if audio.non_finite_samples:
            issues.append(
                PreflightIssue(
                    "error",
                    "AUDIO_NON_FINITE",
                    (
                        f"Audio contains {audio.non_finite_samples} "
                        "non-finite sample(s)."
                    ),
                )
            )
        if audio.clipping_samples:
            issues.append(
                PreflightIssue(
                    "error",
                    "AUDIO_CLIPPING",
                    (f"Audio contains {audio.clipping_samples} clipping sample(s)."),
                )
            )
        if audio.silence_ratio >= 0.999:
            issues.append(
                PreflightIssue(
                    "warning",
                    "AUDIO_SILENCE",
                    "The reviewed audio range is effectively silent.",
                )
            )
    return PreflightReport(
        revision=snapshot.revision,
        scene_id=snapshot.scene_id,
        snapshot=snapshot,
        media=media,
        issues=tuple(issues),
        audio=audio,
    )


__all__ = [
    "PreflightIssue",
    "PreflightReport",
    "run_preflight",
]
