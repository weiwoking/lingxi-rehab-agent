"""Drink-water wrist tremor detector.

The detector uses only IMU4 wrist gyroscope rows from one already-segmented
repetition. It never resamples, interpolates, zero-fills, or creates frames.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


TREMOR_DETECTOR_VERSION = "drink_water_tremor_v1_1"
TREMOR_BAND_HZ = (3.5, 6.0)
TOTAL_MOTION_BAND_HZ = (0.5, 20.0)
TREMOR_WINDOW_SECONDS = 2.0
TREMOR_STEP_SECONDS = 0.5
TREMOR_BAND_RATIO_THRESHOLD = 0.18
TREMOR_BAND_RMS_THRESHOLD = 6.0
TREMOR_FRACTION_THRESHOLD = 0.40


@dataclass(frozen=True)
class TremorWindowResult:
    start_index: int
    end_index: int
    axis: str | None
    positive: bool
    band_ratio: float | None
    band_rms: float | None


def detect_drink_water_tremor_from_rows(df: pd.DataFrame) -> dict:
    time_ms = _finite_series(df, "time_ms")
    if time_ms is None or len(time_ms) < 3:
        return _not_evaluated("insufficient_time_ms")

    diffs = np.diff(time_ms)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    if len(diffs) == 0:
        return _not_evaluated("invalid_time_ms")

    median_step_ms = float(np.median(diffs))
    if median_step_ms <= 0:
        return _not_evaluated("invalid_time_ms")
    fs = 1000.0 / median_step_ms
    window_samples = int(round(TREMOR_WINDOW_SECONDS * fs))
    step_samples = max(1, int(round(TREMOR_STEP_SECONDS * fs)))
    if window_samples < 8 or len(df) < window_samples:
        return _not_evaluated("insufficient_reliable_window", fs=fs)

    axis_values = {}
    for axis in ("gx", "gy", "gz"):
        values = _finite_series(df, f"imu4_{axis}")
        if values is None:
            axis_values[axis] = None
        else:
            axis_values[axis] = values

    if not any(values is not None for values in axis_values.values()):
        return _not_evaluated("imu4_gyro_unavailable", fs=fs)

    windows: list[TremorWindowResult] = []
    for start in range(0, len(df) - window_samples + 1, step_samples):
        end = start + window_samples
        best = _best_positive_axis(axis_values, start, end, fs)
        if best is not None:
            windows.append(best)

    valid_windows = [item for item in windows if item.axis is not None]
    if not valid_windows:
        return _not_evaluated("no_valid_tremor_windows", fs=fs)

    positive_count = sum(1 for item in valid_windows if item.positive)
    fraction = positive_count / len(valid_windows)
    detected = tremor_fraction_detected(fraction)
    return {
        "status": "evaluated",
        "detected": bool(detected),
        "version": TREMOR_DETECTOR_VERSION,
        "frequency_band_hz": list(TREMOR_BAND_HZ),
        "total_motion_band_hz": list(TOTAL_MOTION_BAND_HZ),
        "fs_hz": float(fs),
        "window_seconds": TREMOR_WINDOW_SECONDS,
        "step_seconds": TREMOR_STEP_SECONDS,
        "band_ratio_threshold": TREMOR_BAND_RATIO_THRESHOLD,
        "band_rms_threshold": TREMOR_BAND_RMS_THRESHOLD,
        "tremor_fraction_threshold": TREMOR_FRACTION_THRESHOLD,
        "valid_window_count": len(valid_windows),
        "positive_window_count": int(positive_count),
        "tremor_fraction": float(fraction),
        "windows": [_window_to_dict(item) for item in valid_windows],
    }


def _finite_series(df: pd.DataFrame, column: str) -> np.ndarray | None:
    if column not in df.columns:
        return None
    values = pd.to_numeric(df[column], errors="coerce").to_numpy(dtype=float)
    if len(values) == 0 or not bool(np.any(np.isfinite(values))):
        return None
    return values


def tremor_fraction_detected(fraction: float) -> bool:
    return float(fraction) >= TREMOR_FRACTION_THRESHOLD


def _best_positive_axis(axis_values: dict[str, np.ndarray | None], start: int, end: int, fs: float) -> TremorWindowResult | None:
    best: TremorWindowResult | None = None
    for axis, values in axis_values.items():
        if values is None:
            continue
        segment = values[start:end]
        if len(segment) != end - start or not bool(np.all(np.isfinite(segment))):
            continue
        band_ratio, band_rms = _axis_band_features(segment, fs)
        positive = band_ratio >= TREMOR_BAND_RATIO_THRESHOLD and band_rms >= TREMOR_BAND_RMS_THRESHOLD
        current = TremorWindowResult(
            start_index=start,
            end_index=end - 1,
            axis=axis,
            positive=bool(positive),
            band_ratio=float(band_ratio),
            band_rms=float(band_rms),
        )
        if best is None:
            best = current
        elif current.positive and not best.positive:
            best = current
        elif current.positive == best.positive and (current.band_ratio or 0) > (best.band_ratio or 0):
            best = current
    return best


def _axis_band_features(values: Iterable[float], fs: float) -> tuple[float, float]:
    x = np.asarray(list(values), dtype=float)
    if len(x) < 2 or fs <= 0:
        return 0.0, 0.0
    x = x - np.mean(x)
    window = np.hanning(len(x))
    windowed = x * window
    fft = np.fft.rfft(windowed)
    freqs = np.fft.rfftfreq(len(windowed), d=1.0 / fs)
    scale = fs * float(np.sum(window ** 2))
    if scale <= 0:
        return 0.0, 0.0
    psd = (np.abs(fft) ** 2) / scale
    if len(psd) > 2:
        psd[1:-1] *= 2.0
    band_power = _band_power(freqs, psd, TREMOR_BAND_HZ)
    total_power = _band_power(freqs, psd, TOTAL_MOTION_BAND_HZ)
    ratio = 0.0 if total_power <= 0 else band_power / total_power
    return float(ratio), float(math.sqrt(max(0.0, band_power)))


def _band_power(freqs: np.ndarray, psd: np.ndarray, band: tuple[float, float]) -> float:
    mask = (freqs >= band[0]) & (freqs <= band[1])
    if not bool(np.any(mask)):
        return 0.0
    return float(np.sum(psd[mask]))


def _not_evaluated(reason: str, fs: float | None = None) -> dict:
    return {
        "status": "not_evaluated",
        "detected": False,
        "reason": reason,
        "version": TREMOR_DETECTOR_VERSION,
        "frequency_band_hz": list(TREMOR_BAND_HZ),
        "window_seconds": TREMOR_WINDOW_SECONDS,
        "step_seconds": TREMOR_STEP_SECONDS,
        "band_ratio_threshold": TREMOR_BAND_RATIO_THRESHOLD,
        "band_rms_threshold": TREMOR_BAND_RMS_THRESHOLD,
        "tremor_fraction_threshold": TREMOR_FRACTION_THRESHOLD,
        "fs_hz": fs,
        "valid_window_count": 0,
        "positive_window_count": 0,
        "tremor_fraction": None,
        "windows": [],
    }


def _window_to_dict(item: TremorWindowResult) -> dict:
    return {
        "start_index": item.start_index,
        "end_index": item.end_index,
        "axis": item.axis,
        "positive": item.positive,
        "band_ratio": item.band_ratio,
        "band_rms": item.band_rms,
    }
