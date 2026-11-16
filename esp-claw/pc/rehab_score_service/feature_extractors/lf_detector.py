from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


DRINK_WATER_LF_VERSION = "drink_water_lf_v2"
LF_ROLL_THRESHOLD_DEG = 10.0
LF_MIN_DURATION_S = 1.0
LF_NEUTRAL_WINDOW_S = 0.75
LF_ROLL_MEDIAN_WINDOW_S = 0.50
LF_MIN_NEUTRAL_SAMPLE_COUNT = 3
LF_MIN_VALID_SAMPLE_COUNT = 3


def _base_result(status: str = "not_evaluated") -> dict[str, Any]:
    return {
        "version": DRINK_WATER_LF_VERSION,
        "detected": False,
        "status": status,
        "neutral_roll_deg": None,
        "peak_abs_roll_deg": None,
        "max_duration_over_threshold_s": 0.0,
        "positive_duration_over_threshold_s": 0.0,
        "negative_duration_over_threshold_s": 0.0,
        "roll_threshold_deg": LF_ROLL_THRESHOLD_DEG,
        "min_duration_s": LF_MIN_DURATION_S,
        "median_window_s": LF_ROLL_MEDIAN_WINDOW_S,
        "neutral_window_s": LF_NEUTRAL_WINDOW_S,
        "valid_sample_count": 0,
        "estimated_rate_hz": None,
        "median_dt_ms": None,
        "max_allowed_gap_ms": None,
        "insufficient_evidence": True,
        "uses_interpolation": False,
        "uses_zero_fill": False,
        "uses_repeat_last": False,
    }


def _finite_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([math.nan] * len(frame), index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _valid_flag_series(frame: pd.DataFrame, column: str) -> pd.Series | None:
    if column not in frame.columns:
        return None
    return pd.to_numeric(frame[column], errors="coerce")


def _contiguous_true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(mask):
        if value and start is None:
            start = index
        elif not value and start is not None:
            runs.append((start, index - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask) - 1))
    return runs


def _estimate_median_dt_ms(times_ms: np.ndarray) -> float | None:
    if len(times_ms) < 2:
        return None
    diffs = np.diff(times_ms)
    positive = diffs[np.isfinite(diffs) & (diffs > 0)]
    if len(positive) == 0:
        return None
    median_dt_ms = float(np.median(positive))
    if median_dt_ms <= 0:
        return None
    return median_dt_ms


def _estimate_rate_hz(times_ms: np.ndarray) -> float | None:
    median_dt_ms = _estimate_median_dt_ms(times_ms)
    if median_dt_ms is None:
        return None
    return 1000.0 / median_dt_ms


def _median_window_samples(rate_hz: float | None) -> int:
    if rate_hz is None or not math.isfinite(rate_hz) or rate_hz <= 0:
        return 1
    samples = max(1, int(round(rate_hz * LF_ROLL_MEDIAN_WINDOW_S)))
    if samples % 2 == 0:
        samples += 1
    return samples


def _smooth_valid_roll(
    time_ms: pd.Series,
    roll_deg: pd.Series,
    valid_mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float | None]:
    smoothed = np.full(len(roll_deg), np.nan, dtype=float)
    all_valid_times = time_ms.to_numpy(dtype=float)[valid_mask]
    rate_hz = _estimate_rate_hz(all_valid_times)
    window = _median_window_samples(rate_hz)

    for start, end in _contiguous_true_runs(valid_mask):
        segment = roll_deg.iloc[start : end + 1].to_numpy(dtype=float)
        if len(segment) == 0:
            continue
        unwrapped = np.rad2deg(np.unwrap(np.deg2rad(segment)))
        segment_smoothed = (
            pd.Series(unwrapped)
            .rolling(window=window, center=True, min_periods=1)
            .median()
            .to_numpy(dtype=float)
        )
        smoothed[start : end + 1] = segment_smoothed

    return time_ms.to_numpy(dtype=float), smoothed, rate_hz


def _max_continuous_duration_s(
    times_ms: np.ndarray,
    over_threshold: np.ndarray,
    median_dt_ms: float | None,
) -> float:
    max_allowed_gap_ms = None if median_dt_ms is None else 3.0 * median_dt_ms
    max_duration_ms = 0.0

    start_index: int | None = None
    previous_index: int | None = None

    for index, value in enumerate(over_threshold):
        current_time = float(times_ms[index])
        current_valid = bool(value) and math.isfinite(current_time)

        if current_valid and previous_index is not None:
            previous_time = float(times_ms[previous_index])
            dt_ms = current_time - previous_time
            if (
                not math.isfinite(dt_ms)
                or dt_ms <= 0
                or (
                    max_allowed_gap_ms is not None
                    and dt_ms > max_allowed_gap_ms
                )
            ):
                if start_index is not None and previous_index > start_index:
                    max_duration_ms = max(
                        max_duration_ms,
                        max(0.0, float(times_ms[previous_index] - times_ms[start_index])),
                    )
                start_index = index

        if current_valid:
            if start_index is None:
                start_index = index
            previous_index = index
            continue

        if start_index is not None and previous_index is not None and previous_index > start_index:
            max_duration_ms = max(
                max_duration_ms,
                max(0.0, float(times_ms[previous_index] - times_ms[start_index])),
            )
        start_index = None
        previous_index = None

    if start_index is not None and previous_index is not None and previous_index > start_index:
        max_duration_ms = max(
            max_duration_ms,
            max(0.0, float(times_ms[previous_index] - times_ms[start_index])),
        )

    return max_duration_ms / 1000.0


def detect_drink_water_lf_from_rows(frame: pd.DataFrame) -> dict[str, Any]:
    result = _base_result()
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return result

    time_ms = _finite_series(frame, "time_ms")
    roll_deg = _finite_series(frame, "imu2_roll")
    imu2_valid = _valid_flag_series(frame, "imu2_valid")
    if imu2_valid is None:
        result["status"] = "missing_imu2_valid"
        return result

    imu2_valid_values = imu2_valid.to_numpy(dtype=float)
    valid_mask = (
        np.isfinite(time_ms.to_numpy(dtype=float))
        & np.isfinite(roll_deg.to_numpy(dtype=float))
        & np.isfinite(imu2_valid_values)
        & (imu2_valid_values == 1.0)
    )
    valid_count = int(np.sum(valid_mask))
    result["valid_sample_count"] = valid_count
    if valid_count < LF_MIN_VALID_SAMPLE_COUNT:
        result["status"] = "insufficient_valid_roll"
        return result

    times, smoothed_roll, rate_hz = _smooth_valid_roll(time_ms, roll_deg, valid_mask)
    result["estimated_rate_hz"] = None if rate_hz is None else float(rate_hz)
    valid_times = times[valid_mask]
    median_dt_ms = _estimate_median_dt_ms(valid_times)
    max_allowed_gap_ms = None if median_dt_ms is None else 3.0 * median_dt_ms
    result["median_dt_ms"] = median_dt_ms
    result["max_allowed_gap_ms"] = max_allowed_gap_ms

    first_valid_time = float(times[np.where(valid_mask)[0][0]])
    neutral_mask = (
        valid_mask
        & np.isfinite(smoothed_roll)
        & (times >= first_valid_time)
        & (times <= first_valid_time + LF_NEUTRAL_WINDOW_S * 1000.0)
    )
    neutral_values = smoothed_roll[neutral_mask]
    neutral_values = neutral_values[np.isfinite(neutral_values)]
    if len(neutral_values) < LF_MIN_NEUTRAL_SAMPLE_COUNT:
        result["status"] = "insufficient_neutral_roll"
        return result

    neutral_roll = float(np.median(neutral_values))
    relative_roll = smoothed_roll - neutral_roll
    lean_magnitude = np.abs(relative_roll)
    peak_abs = float(np.nanmax(lean_magnitude)) if np.any(np.isfinite(lean_magnitude)) else 0.0
    positive_over_threshold = (
        valid_mask
        & np.isfinite(relative_roll)
        & (relative_roll >= LF_ROLL_THRESHOLD_DEG)
    )
    negative_over_threshold = (
        valid_mask
        & np.isfinite(relative_roll)
        & (relative_roll <= -LF_ROLL_THRESHOLD_DEG)
    )
    positive_duration_s = _max_continuous_duration_s(
        times,
        positive_over_threshold,
        median_dt_ms=median_dt_ms,
    )
    negative_duration_s = _max_continuous_duration_s(
        times,
        negative_over_threshold,
        median_dt_ms=median_dt_ms,
    )
    max_duration_s = max(positive_duration_s, negative_duration_s)

    detected = bool(max_duration_s >= LF_MIN_DURATION_S)
    result.update(
        {
            "detected": detected,
            "status": "detected" if detected else "not_detected",
            "neutral_roll_deg": neutral_roll,
            "peak_abs_roll_deg": peak_abs,
            "max_duration_over_threshold_s": max_duration_s,
            "positive_duration_over_threshold_s": positive_duration_s,
            "negative_duration_over_threshold_s": negative_duration_s,
            "insufficient_evidence": False,
        }
    )
    return result
