from __future__ import annotations

import io
import math
import struct

from PIL import Image

from aviutl2_api.live.audio import RenderedAudio
from aviutl2_api.live.catalog import (
    CatalogEffect,
    EffectCatalogPage,
    EffectFlags,
)
from aviutl2_api.live.frame import RenderedFrame, make_contact_sheet, make_preview
from aviutl2_api.live.inspection import (
    EffectInspection,
    ItemInspection,
    ObjectInspection,
)
from aviutl2_api.live.layers import LayerInfo, LayerPage
from aviutl2_api.live.media import CreatedMediaObject, MediaInventory
from aviutl2_api.live.qc import _timeline_continuity_issues, run_preflight
from aviutl2_api.live.snapshot import ProjectSnapshot, SnapshotObject
from aviutl2_api.live.subtitles import (
    SubtitleLayerPolicy,
    assign_subtitle_layers,
    parse_srt,
    parse_webvtt,
)


def _png(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (16, 9), color).save(output, format="PNG")
    return output.getvalue()


def test_native_audio_analysis() -> None:
    sample_rate = 48_000
    samples: list[float] = []
    for index in range(sample_rate):
        value = 0.25 * math.sin(2.0 * math.pi * 1000.0 * index / sample_rate)
        samples.extend((value, value))
    pcm = struct.pack(f"<{len(samples)}f", *samples)
    capture = RenderedAudio(
        frame_start=0,
        frame_end=29,
        sample_rate=sample_rate,
        sample_count=sample_rate,
        scene_id=1,
        revision=10,
        sha256="0" * 64,
        pcm_f32le=pcm,
    )
    analysis = capture.analyze()
    assert 0.249 < analysis.peak < 0.251
    assert 0.17 < analysis.rms < 0.18
    assert analysis.clipping_samples == 0
    assert analysis.non_finite_samples == 0
    assert analysis.integrated_lufs is not None


def test_subtitle_parsing_and_layer_assignment() -> None:
    srt = """1
00:00:00,500 --> 00:00:02,000
Hello

2
00:00:01,500 --> 00:00:03,000
World
"""
    cues = parse_srt(srt, language="en")
    assert len(cues) == 2
    placements = assign_subtitle_layers(
        cues,
        rate=30,
        scale=1,
        policy=SubtitleLayerPolicy(base_layer=5, max_layers=2),
    )
    assert placements[0].layer == 5
    assert placements[0].frame_start == 15
    assert placements[0].frame_end == 59
    assert placements[1].layer == 6

    webvtt = """WEBVTT

00:00:01.000 --> 00:00:02.000 align:center
<v Alice>Hello &amp; welcome</v>
"""
    cue = parse_webvtt(webvtt)[0]
    assert cue.speaker == "Alice"
    assert cue.text == "Hello & welcome"


def test_contact_sheet_is_in_memory_png() -> None:
    frames = tuple(
        RenderedFrame(
            frame=index,
            width=16,
            height=9,
            scene_id=1,
            revision=22,
            sha256="0" * 64,
            png=_png(color),
        )
        for index, color in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255)))
    )
    sheet = make_contact_sheet(
        frames,
        columns=2,
        thumbnail_width=160,
    )
    assert sheet.revision == 22
    assert sheet.frames == (0, 1, 2)
    assert sheet.png.startswith(b"\x89PNG\r\n\x1a\n")
    assert sheet.width == 320


def test_rendered_preview_is_small_and_agent_transport_ready() -> None:
    frame = RenderedFrame(
        frame=42,
        width=1600,
        height=900,
        scene_id=1,
        revision=22,
        sha256="0" * 64,
        png=_png((12, 34, 56)),
    )

    preview = make_preview(frame, max_width=8, format="jpg", quality=80)

    assert preview.frame == 42
    assert (preview.width, preview.height) == (8, 5)
    assert preview.mime_type == "image/jpeg"
    assert preview.data.startswith(b"\xff\xd8")
    assert preview.to_data_url().startswith("data:image/jpeg;base64,")
    assert preview.sha256 != frame.sha256


def test_media_creation_returns_sdk_generated_group() -> None:
    result = CreatedMediaObject.from_wire(
        {
            "created": {
                "layer": 2,
                "frame_start": 10,
                "frame_end": 39,
            },
            "snapshot_required": False,
            "revision": 124,
            "undo_grouped": True,
            "warnings": ["sdk_generated_object_group"],
            "created_objects": [
                {
                    "object_id": "obj-124-0",
                    "layer": 2,
                    "frame_start": 10,
                    "frame_end": 39,
                    "name": None,
                    "api_locked": False,
                },
                {
                    "object_id": "obj-124-1",
                    "layer": 3,
                    "frame_start": 10,
                    "frame_end": 39,
                    "name": "Audio",
                    "api_locked": False,
                },
            ],
        }
    )
    assert result.revision == 124
    assert len(result.objects) == 2
    assert result.objects[1].layer == 3


def test_subtitle_assignment_respects_existing_objects() -> None:
    cue = parse_srt("1\n00:00:00,000 --> 00:00:01,000\nText\n")
    occupied = (
        SnapshotObject(
            object_id="obj-1-0",
            revision=1,
            layer=4,
            frame_start=0,
            frame_end=29,
            name=None,
            alias=None,
        ),
    )
    placement = assign_subtitle_layers(
        cue,
        rate=30,
        scale=1,
        policy=SubtitleLayerPolicy(base_layer=4, max_layers=2),
        occupied=occupied,
    )
    assert placement[0].layer == 5


def test_timeline_overlap_is_advisory_for_transition_layouts() -> None:
    objects = tuple(
        SnapshotObject(
            object_id=f"obj-10-{index}",
            revision=10,
            layer=0,
            frame_start=start,
            frame_end=end,
            name=None,
            alias=None,
        )
        for index, (start, end) in enumerate(((0, 20), (15, 30), (40, 49)))
    )
    snapshot = ProjectSnapshot(
        revision=10,
        scene_id=0,
        objects=objects,
        total=len(objects),
    )

    issues = _timeline_continuity_issues(snapshot)

    assert [issue.code for issue in issues] == [
        "TIMELINE_COLLISION",
        "TIMELINE_GAP",
    ]
    assert all(issue.severity == "warning" for issue in issues)
    assert "intentional transition" in issues[0].message


def test_general_text_overlap_is_allowed_unless_subtitles_are_explicit() -> None:
    class TextPreflightClient:
        def __init__(self) -> None:
            self.snapshot = ProjectSnapshot(
                revision=10,
                scene_id=0,
                objects=tuple(
                    SnapshotObject(
                        f"obj-10-{index}",
                        10,
                        index,
                        0,
                        29,
                        f"Text {index}",
                        None,
                    )
                    for index in range(2)
                ),
                total=2,
            )

        def get_snapshot(self, *, include_alias: bool = False) -> ProjectSnapshot:
            return self.snapshot

        def get_media_inventory(self) -> MediaInventory:
            return MediaInventory(10, 0, (), 0, 0, 0)

        def get_project_info(self) -> dict[str, int]:
            return {"layer_max": 1}

        def get_layers(self, *, start: int, count: int) -> LayerPage:
            layers = tuple(
                LayerInfo(index, None, True, False, True, 1)
                for index in range(start, min(2, start + count))
            )
            return LayerPage(10, 0, 1, 0, 2, start, layers)

        def get_font_catalog(self, *, start: int, count: int) -> dict[str, object]:
            return {"entries": [], "next_start": None}

        def get_module_catalog(self, *, start: int, count: int) -> dict[str, object]:
            return {"entries": [], "next_start": None}

        def get_effect_catalog(self, *, start: int, count: int) -> EffectCatalogPage:
            effects = (
                CatalogEffect(
                    "テキスト",
                    "input",
                    1,
                    EffectFlags(True, False, False, False),
                    (),
                ),
                CatalogEffect(
                    "標準描画",
                    "output",
                    2,
                    EffectFlags(True, False, False, False),
                    (),
                ),
            )
            return EffectCatalogPage(0, 2, None, effects)

        def inspect_object(self, obj: SnapshotObject) -> ObjectInspection:
            return ObjectInspection(
                obj.object_id,
                10,
                0,
                (
                    EffectInspection(
                        0,
                        0,
                        "テキスト",
                        "テキスト#0",
                        True,
                        False,
                        (ItemInspection("テキスト", "text", 1, obj.name, None),),
                    ),
                    EffectInspection(
                        1,
                        0,
                        "標準描画",
                        "標準描画#0",
                        True,
                        False,
                        (),
                    ),
                ),
            )

    client = TextPreflightClient()
    default_report = run_preflight(client)  # type: ignore[arg-type]
    explicit_report = run_preflight(  # type: ignore[arg-type]
        client,
        subtitle_layers=(0, 1),
        subtitle_overlap="warn",
    )

    assert not any("SUBTITLE" in issue.code for issue in default_report.issues)
    assert [
        issue.code for issue in explicit_report.issues if "SUBTITLE" in issue.code
    ] == ["SUBTITLE_OVERLAP"]
