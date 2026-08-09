"""Native stereo audio capture and quality-control helpers."""

from __future__ import annotations

import math
import sys
from array import array
from dataclasses import dataclass
from os import PathLike
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AudioAnalysis:
    """Deterministic measurements over interleaved stereo float PCM."""

    peak: float
    peak_dbfs: float
    rms: float
    rms_dbfs: float
    clipping_samples: int
    non_finite_samples: int
    silence_ratio: float
    integrated_lufs: float | None


@dataclass(frozen=True, slots=True)
class RenderedAudio:
    """Revision-bound audio rendered by the running AviUtl2 process."""

    frame_start: int
    frame_end: int
    sample_rate: int
    sample_count: int
    scene_id: int
    revision: int
    sha256: str
    pcm_f32le: bytes

    @property
    def duration_seconds(self) -> float:
        return self.sample_count / self.sample_rate

    def analyze(
        self,
        *,
        clipping_threshold: float = 1.0,
        silence_threshold_dbfs: float = -60.0,
    ) -> AudioAnalysis:
        return analyze_pcm_f32le(
            self.pcm_f32le,
            sample_rate=self.sample_rate,
            channels=2,
            clipping_threshold=clipping_threshold,
            silence_threshold_dbfs=silence_threshold_dbfs,
        )

    def save_pcm(
        self,
        path: str | PathLike[str],
        *,
        overwrite: bool = False,
    ) -> Path:
        destination = Path(path).expanduser().resolve()
        if destination.exists() and not overwrite:
            raise FileExistsError(f"refusing to overwrite existing PCM: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.pcm_f32le)
        return destination


def _dbfs(value: float) -> float:
    return -math.inf if value <= 0.0 else 20.0 * math.log10(value)


def _biquad(
    samples: list[float],
    *,
    b0: float,
    b1: float,
    b2: float,
    a1: float,
    a2: float,
) -> list[float]:
    output: list[float] = []
    x1 = 0.0
    x2 = 0.0
    y1 = 0.0
    y2 = 0.0
    for sample in samples:
        value = b0 * sample + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        output.append(value)
        x2, x1 = x1, sample
        y2, y1 = y1, value
    return output


def _high_pass_coefficients(
    sample_rate: int,
    frequency: float,
    quality: float,
) -> tuple[float, float, float, float, float]:
    omega = 2.0 * math.pi * frequency / sample_rate
    cosine = math.cos(omega)
    alpha = math.sin(omega) / (2.0 * quality)
    a0 = 1.0 + alpha
    return (
        (1.0 + cosine) / (2.0 * a0),
        -(1.0 + cosine) / a0,
        (1.0 + cosine) / (2.0 * a0),
        -2.0 * cosine / a0,
        (1.0 - alpha) / a0,
    )


def _high_shelf_coefficients(
    sample_rate: int,
    frequency: float,
    gain_db: float,
    quality: float,
) -> tuple[float, float, float, float, float]:
    amplitude = 10.0 ** (gain_db / 40.0)
    omega = 2.0 * math.pi * frequency / sample_rate
    cosine = math.cos(omega)
    sine = math.sin(omega)
    alpha = sine / (2.0 * quality)
    root = 2.0 * math.sqrt(amplitude) * alpha
    a0 = amplitude + 1.0 - (amplitude - 1.0) * cosine + root
    return (
        amplitude * (amplitude + 1.0 + (amplitude - 1.0) * cosine + root) / a0,
        -2.0 * amplitude * (amplitude - 1.0 + (amplitude + 1.0) * cosine) / a0,
        amplitude * (amplitude + 1.0 + (amplitude - 1.0) * cosine - root) / a0,
        2.0 * (amplitude - 1.0 - (amplitude + 1.0) * cosine) / a0,
        (amplitude + 1.0 - (amplitude - 1.0) * cosine - root) / a0,
    )


def _integrated_loudness(
    left: list[float],
    right: list[float],
    sample_rate: int,
) -> float | None:
    """Measure gated stereo loudness using the BS.1770/EBU R128 method."""
    if len(left) < max(1, round(sample_rate * 0.4)):
        return None
    shelf = _high_shelf_coefficients(
        sample_rate,
        1681.974450955533,
        4.0,
        0.7071752369554196,
    )
    high_pass = _high_pass_coefficients(
        sample_rate,
        38.13547087602444,
        0.5003270373238773,
    )
    filtered_channels = []
    for channel in (left, right):
        filtered = _biquad(
            channel,
            b0=shelf[0],
            b1=shelf[1],
            b2=shelf[2],
            a1=shelf[3],
            a2=shelf[4],
        )
        filtered_channels.append(
            _biquad(
                filtered,
                b0=high_pass[0],
                b1=high_pass[1],
                b2=high_pass[2],
                a1=high_pass[3],
                a2=high_pass[4],
            )
        )

    block_size = max(1, round(sample_rate * 0.4))
    step = max(1, round(block_size * 0.25))
    energies: list[float] = []
    for start in range(0, len(left) - block_size + 1, step):
        stop = start + block_size
        energy = (
            sum(
                sample * sample
                for channel in filtered_channels
                for sample in channel[start:stop]
            )
            / block_size
        )
        energies.append(energy)
    absolute = [
        energy
        for energy in energies
        if energy > 0.0 and -0.691 + 10.0 * math.log10(energy) >= -70.0
    ]
    if not absolute:
        return None
    preliminary_energy = sum(absolute) / len(absolute)
    relative_gate = -0.691 + 10.0 * math.log10(preliminary_energy) - 10.0
    gated = [
        energy
        for energy in absolute
        if -0.691 + 10.0 * math.log10(energy) >= relative_gate
    ]
    if not gated:
        return None
    return -0.691 + 10.0 * math.log10(sum(gated) / len(gated))


def analyze_pcm_f32le(
    pcm: bytes,
    *,
    sample_rate: int,
    channels: int = 2,
    clipping_threshold: float = 1.0,
    silence_threshold_dbfs: float = -60.0,
) -> AudioAnalysis:
    if sample_rate <= 0 or channels != 2:
        raise ValueError("a positive sample rate and stereo PCM are required")
    if len(pcm) == 0 or len(pcm) % (4 * channels) != 0:
        raise ValueError("PCM must contain complete interleaved float32 frames")
    if (
        not math.isfinite(clipping_threshold)
        or clipping_threshold <= 0.0
        or not math.isfinite(silence_threshold_dbfs)
    ):
        raise ValueError("analysis thresholds must be finite and valid")

    values = array("f")
    values.frombytes(pcm)
    if sys.byteorder != "little":
        values.byteswap()
    finite = [float(value) for value in values if math.isfinite(value)]
    non_finite = len(values) - len(finite)
    if not finite:
        return AudioAnalysis(
            peak=0.0,
            peak_dbfs=-math.inf,
            rms=0.0,
            rms_dbfs=-math.inf,
            clipping_samples=0,
            non_finite_samples=non_finite,
            silence_ratio=1.0,
            integrated_lufs=None,
        )
    peak = max(abs(value) for value in finite)
    rms = math.sqrt(sum(value * value for value in finite) / len(finite))
    silence_threshold = 10.0 ** (silence_threshold_dbfs / 20.0)
    left = [
        float(values[index]) if math.isfinite(values[index]) else 0.0
        for index in range(0, len(values), 2)
    ]
    right = [
        float(values[index]) if math.isfinite(values[index]) else 0.0
        for index in range(1, len(values), 2)
    ]
    return AudioAnalysis(
        peak=peak,
        peak_dbfs=_dbfs(peak),
        rms=rms,
        rms_dbfs=_dbfs(rms),
        clipping_samples=sum(abs(value) >= clipping_threshold for value in finite),
        non_finite_samples=non_finite,
        silence_ratio=sum(abs(value) < silence_threshold for value in finite)
        / len(finite),
        integrated_lufs=_integrated_loudness(
            left,
            right,
            sample_rate,
        ),
    )


__all__ = [
    "AudioAnalysis",
    "RenderedAudio",
    "analyze_pcm_f32le",
]
