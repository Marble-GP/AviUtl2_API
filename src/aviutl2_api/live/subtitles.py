"""SRT/WebVTT parsing and deterministic subtitle placement models."""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Literal

from .protocol import ProtocolError
from .snapshot import SnapshotObject

_TIMING = re.compile(
    r"^\s*(?P<start>(?:\d{1,3}:)?\d{2}:\d{2}[,.]\d{3})"
    r"\s*-->\s*"
    r"(?P<end>(?:\d{1,3}:)?\d{2}:\d{2}[,.]\d{3})"
    r"(?:\s+.*)?$"
)
_VOICE = re.compile(r"^\s*<v(?:\.[^ >]+)*\s+([^>]+)>(.*)$", re.DOTALL)
_TAG = re.compile(r"</?[^>]+>")


def _timestamp_seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        hours = 0
        minutes_text, seconds_text = parts
    elif len(parts) == 3:
        hours = int(parts[0])
        minutes_text, seconds_text = parts[1:]
    else:
        raise ValueError(f"invalid subtitle timestamp: {value!r}")
    minutes = int(minutes_text)
    seconds = float(seconds_text)
    if minutes >= 60 or seconds >= 60.0:
        raise ValueError(f"invalid subtitle timestamp: {value!r}")
    return hours * 3600.0 + minutes * 60.0 + seconds


@dataclass(frozen=True, slots=True)
class SubtitleCue:
    start_seconds: float
    end_seconds: float
    text: str
    speaker: str | None = None
    language: str | None = None

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.start_seconds)
            or not math.isfinite(self.end_seconds)
            or self.start_seconds < 0.0
            or self.end_seconds <= self.start_seconds
            or not self.text
        ):
            raise ValueError("subtitle cue timing/text is invalid")


@dataclass(frozen=True, slots=True)
class SubtitleStyle:
    x: float = 0.0
    y: float = 400.0
    size: float = 42.0
    color: str = "ffffff"
    speaker_colors: dict[str, str] = field(default_factory=dict)
    include_speaker_label: bool = False

    def color_for(self, cue: SubtitleCue) -> str:
        if cue.speaker is not None:
            return self.speaker_colors.get(cue.speaker, self.color)
        return self.color

    def text_for(self, cue: SubtitleCue) -> str:
        if self.include_speaker_label and cue.speaker:
            return f"{cue.speaker}: {cue.text}"
        return cue.text


@dataclass(frozen=True, slots=True)
class SubtitleLayerPolicy:
    base_layer: int
    max_layers: int = 4
    overlap: Literal["stack", "reject"] = "stack"

    def __post_init__(self) -> None:
        if self.base_layer < 0 or self.max_layers < 1:
            raise ValueError("subtitle layer policy is invalid")
        if self.overlap == "reject" and self.max_layers != 1:
            raise ValueError("reject policy requires max_layers=1")


@dataclass(frozen=True, slots=True)
class SubtitlePlacement:
    cue: SubtitleCue
    layer: int
    frame_start: int
    frame_end: int
    client_id: str


@dataclass(frozen=True, slots=True)
class SubtitleBatchResult:
    previous_revision: int
    revision: int
    placements: tuple[SubtitlePlacement, ...]
    objects: tuple[SnapshotObject, ...]
    undo_grouped: bool

    def __post_init__(self) -> None:
        if len(self.placements) != len(self.objects):
            raise ProtocolError(
                "subtitle placements do not match created objects"
            )


def _parse_blocks(
    text: str,
    *,
    webvtt: bool,
    language: str | None,
) -> tuple[SubtitleCue, ...]:
    normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace(
        "\r", "\n"
    )
    if webvtt:
        lines = normalized.splitlines()
        if not lines or not lines[0].strip().startswith("WEBVTT"):
            raise ValueError("WebVTT input must start with WEBVTT")
        normalized = "\n".join(lines[1:])
    blocks = re.split(r"\n[ \t]*\n", normalized.strip())
    cues: list[SubtitleCue] = []
    for block in blocks:
        lines = block.splitlines()
        if not lines:
            continue
        if webvtt and lines[0].lstrip().startswith(
            ("NOTE", "STYLE", "REGION")
        ):
            continue
        timing_index = next(
            (
                index
                for index, line in enumerate(lines[:2])
                if _TIMING.match(line)
            ),
            -1,
        )
        if timing_index < 0:
            if all(not line.strip() for line in lines):
                continue
            raise ValueError("subtitle block has no valid timing line")
        timing = _TIMING.match(lines[timing_index])
        assert timing is not None
        body = "\n".join(lines[timing_index + 1 :]).strip()
        if not body:
            raise ValueError("subtitle cue text must not be empty")
        speaker: str | None = None
        if webvtt:
            voice = _VOICE.match(body)
            if voice is not None:
                speaker = html.unescape(voice.group(1).strip())
                body = voice.group(2)
            body = html.unescape(_TAG.sub("", body))
        cues.append(
            SubtitleCue(
                start_seconds=_timestamp_seconds(timing.group("start")),
                end_seconds=_timestamp_seconds(timing.group("end")),
                text=body,
                speaker=speaker,
                language=language,
            )
        )
    return tuple(cues)


def parse_srt(
    text: str,
    *,
    language: str | None = None,
) -> tuple[SubtitleCue, ...]:
    return _parse_blocks(text, webvtt=False, language=language)


def parse_webvtt(
    text: str,
    *,
    language: str | None = None,
) -> tuple[SubtitleCue, ...]:
    return _parse_blocks(text, webvtt=True, language=language)


def load_subtitles(
    path: str | PathLike[str],
    *,
    language: str | None = None,
    encoding: str = "utf-8-sig",
) -> tuple[SubtitleCue, ...]:
    source = Path(path).expanduser().resolve()
    text = source.read_text(encoding=encoding)
    suffix = source.suffix.lower()
    if suffix == ".srt":
        return parse_srt(text, language=language)
    if suffix in {".vtt", ".webvtt"}:
        return parse_webvtt(text, language=language)
    raise ValueError("subtitle file must use .srt, .vtt, or .webvtt")


def cue_frame_range(
    cue: SubtitleCue,
    *,
    rate: int,
    scale: int,
) -> tuple[int, int]:
    if rate <= 0 or scale <= 0:
        raise ValueError("scene frame rate must be positive")
    frames_per_second = rate / scale
    start = max(0, math.floor(cue.start_seconds * frames_per_second))
    end_exclusive = max(
        start + 1,
        math.ceil(cue.end_seconds * frames_per_second),
    )
    return start, end_exclusive - 1


def assign_subtitle_layers(
    cues: tuple[SubtitleCue, ...],
    *,
    rate: int,
    scale: int,
    policy: SubtitleLayerPolicy,
    occupied: tuple[SnapshotObject, ...] = (),
) -> tuple[SubtitlePlacement, ...]:
    intervals: dict[int, list[tuple[int, int]]] = {
        layer: [
            (obj.frame_start, obj.frame_end)
            for obj in occupied
            if obj.layer == layer
        ]
        for layer in range(
            policy.base_layer,
            policy.base_layer + policy.max_layers,
        )
    }
    placements: list[SubtitlePlacement] = []
    for index, cue in enumerate(cues):
        frame_start, frame_end = cue_frame_range(
            cue,
            rate=rate,
            scale=scale,
        )
        selected: int | None = None
        for layer, ranges in intervals.items():
            collision = any(
                frame_start <= other_end
                and other_start <= frame_end
                for other_start, other_end in ranges
            )
            if not collision:
                selected = layer
                break
            if policy.overlap == "reject":
                break
        if selected is None:
            raise ValueError(
                f"subtitle cue {index} cannot be placed without overlap"
            )
        intervals[selected].append((frame_start, frame_end))
        language = cue.language or "und"
        client_id = f"subtitle-{index:04d}-{language}"[:128]
        placements.append(
            SubtitlePlacement(
                cue=cue,
                layer=selected,
                frame_start=frame_start,
                frame_end=frame_end,
                client_id=client_id,
            )
        )
    return tuple(placements)


__all__ = [
    "SubtitleBatchResult",
    "SubtitleCue",
    "SubtitleLayerPolicy",
    "SubtitlePlacement",
    "SubtitleStyle",
    "assign_subtitle_layers",
    "cue_frame_range",
    "load_subtitles",
    "parse_srt",
    "parse_webvtt",
]
