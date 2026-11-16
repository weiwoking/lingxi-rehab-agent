from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from feature_extractors.lateral_transfer_profile import DERIVED_ROOT, LF_VERSION
from tools.lateral_transfer_dataset import source_csv_for_session
from tools.lateral_transfer_segmentation_v1 import segment_lateral_transfer_file
from tools.lateral_transfer_segmentation_gate import SegmentationNotReadyError, assert_lateral_segmentation_ready, write_gate_failure_marker


AXES = ("roll", "pitch", "yaw")
MODES = ("positive", "negative", "absolute_direction_persistence_separated")
THRESHOLDS_DEG = (5.0, 7.5, 10.0, 12.5, 15.0, 17.5, 20.0)
DURATIONS_S = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
NEUTRAL_WINDOWS_S = (0.5, 0.75, 1.0)
MEDIAN_WINDOWS_S = (0.3, 0.5, 0.7)
MAX_GAP_FACTOR = 3.0


def _series(frame: pd.DataFrame, column: str) -> np.ndarray:
    if column not in frame.columns:
        return np.full(len(frame), np.nan, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)


def _median_dt_ms(times: np.ndarray) -> float | None:
    diffs = np.diff(times[np.isfinite(times)])
    positive = diffs[np.isfinite(diffs) & (diffs > 0)]
    if len(positive) == 0:
        return None
    return float(np.median(positive))


def _duration_s(times: np.ndarray, mask: np.ndarray, max_gap_ms: float | None) -> float:
    best = 0.0
    start: int | None = None
    prev: int | None = None
    for idx, active in enumerate(mask):
        now = float(times[idx])
        valid = bool(active) and math.isfinite(now)
        if valid and prev is not None:
            dt = now - float(times[prev])
            if dt <= 0 or (max_gap_ms is not None and dt > max_gap_ms):
                if start is not None and prev > start:
                    best = max(best, float(times[prev] - times[start]))
                start = idx
        if valid:
            if start is None:
                start = idx
            prev = idx
        else:
            if start is not None and prev is not None and prev > start:
                best = max(best, float(times[prev] - times[start]))
            start = None
            prev = None
    if start is not None and prev is not None and prev > start:
        best = max(best, float(times[prev] - times[start]))
    return best / 1000.0


def evaluate_lf_rule(
    frame: pd.DataFrame,
    *,
    axis: str,
    mode: str,
    threshold_deg: float,
    neutral_window_s: float,
    median_window_s: float,
    max_gap_factor: float = MAX_GAP_FACTOR,
) -> dict:
    times = _series(frame, "time_ms")
    values = _series(frame, f"imu2_{axis}")
    valid = np.isfinite(times) & np.isfinite(values)
    if int(np.sum(valid)) < 3:
        return {"status": "insufficient_valid_axis", "duration_s": 0.0, "positive_duration_s": 0.0, "negative_duration_s": 0.0}
    median_dt = _median_dt_ms(times[valid])
    if median_dt is None:
        return {"status": "insufficient_time", "duration_s": 0.0, "positive_duration_s": 0.0, "negative_duration_s": 0.0}
    rate_hz = 1000.0 / median_dt
    window = max(1, int(round(rate_hz * median_window_s)))
    if window % 2 == 0:
        window += 1
    smoothed = pd.Series(np.rad2deg(np.unwrap(np.deg2rad(values)))).rolling(window=window, center=True, min_periods=1).median().to_numpy(dtype=float)
    first_time = float(times[np.where(valid)[0][0]])
    neutral_mask = valid & np.isfinite(smoothed) & (times <= first_time + neutral_window_s * 1000.0)
    if int(np.sum(neutral_mask)) < 3:
        return {"status": "insufficient_neutral", "duration_s": 0.0, "positive_duration_s": 0.0, "negative_duration_s": 0.0}
    neutral = float(np.median(smoothed[neutral_mask]))
    relative = smoothed - neutral
    max_gap = median_dt * max_gap_factor
    positive_duration = _duration_s(times, valid & np.isfinite(relative) & (relative >= threshold_deg), max_gap)
    negative_duration = _duration_s(times, valid & np.isfinite(relative) & (relative <= -threshold_deg), max_gap)
    if mode == "positive":
        duration = positive_duration
    elif mode == "negative":
        duration = negative_duration
    else:
        duration = max(positive_duration, negative_duration)
    return {
        "status": "ok",
        "duration_s": duration,
        "positive_duration_s": positive_duration,
        "negative_duration_s": negative_duration,
        "neutral_deg": neutral,
        "peak_abs_deg": float(np.nanmax(np.abs(relative))) if np.any(np.isfinite(relative)) else None,
        "median_dt_ms": median_dt,
        "max_allowed_gap_ms": max_gap,
    }


def collect_lf_features(output_dir: Path) -> dict:
    try:
        gate = assert_lateral_segmentation_ready()
    except SegmentationNotReadyError as exc:
        write_gate_failure_marker(output_dir, exc)
        raise
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    rep_frames: list[tuple[dict, pd.DataFrame]] = []
    for approved_session in gate["approved_sessions"]:
        session_id = str(approved_session["session_id"])
        labels = list(approved_session["expected_labels"])
        csv_path = source_csv_for_session(session_id)
        reps, _diagnostic, df = segment_lateral_transfer_file(csv_path)
        for rep in reps:
            index = int(rep["rep_index"])
            expected_label = labels[index - 1] if index <= len(labels) else "unexpected"
            rep_df = df.iloc[int(rep["start_index"]) : int(rep["end_index"]) + 1].copy()
            result = evaluate_lf_rule(
                rep_df,
                axis="roll",
                mode="absolute_direction_persistence_separated",
                threshold_deg=12.0,
                neutral_window_s=0.75,
                median_window_s=0.50,
            )
            current_detected = float(result.get("duration_s") or 0.0) >= 1.0
            rows.append(
                {
                    "session_id": session_id,
                    "rep_index": index,
                    "expected_label": expected_label,
                    "is_lf_positive": expected_label == "lf",
                    "detected_by_current_profile": current_detected,
                    "axis": "roll",
                    "neutral_deg": result.get("neutral_deg"),
                    "positive_duration_s": result.get("positive_duration_s"),
                    "negative_duration_s": result.get("negative_duration_s"),
                    "max_duration_s": result.get("duration_s"),
                    "peak_abs_deg": result.get("peak_abs_deg"),
                    "status": result["status"],
                }
            )
            rep_frames.append((rows[-1], rep_df))
    frame = pd.DataFrame(rows)
    csv_path = output_dir / "lateral_transfer_lf_calibration.csv"
    frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
    candidates = []
    for axis in AXES:
        for mode in MODES:
            for threshold in THRESHOLDS_DEG:
                for duration in DURATIONS_S:
                    for neutral_window in NEUTRAL_WINDOWS_S:
                        for median_window in MEDIAN_WINDOWS_S:
                            detections = []
                            for _row, rep_df in rep_frames:
                                result = evaluate_lf_rule(
                                    rep_df,
                                    axis=axis,
                                    mode=mode,
                                    threshold_deg=threshold,
                                    neutral_window_s=neutral_window,
                                    median_window_s=median_window,
                                )
                                detections.append(float(result.get("duration_s") or 0.0) >= duration)
                            detected = pd.Series(detections, index=frame.index)
                            tp = int(((frame["is_lf_positive"]) & detected).sum())
                            fn = int(((frame["is_lf_positive"]) & ~detected).sum())
                            fp = int(((~frame["is_lf_positive"]) & detected).sum())
                            tn = int(((~frame["is_lf_positive"]) & ~detected).sum())
                            candidates.append(
                                {
                                    "rule": f"imu2_{axis}:{mode}:threshold>={threshold}:duration>={duration}",
                                    "axis": axis,
                                    "mode": mode,
                                    "threshold_deg": threshold,
                                    "duration_s": duration,
                                    "neutral_window_s": neutral_window,
                                    "median_window_s": median_window,
                                    "TP": tp,
                                    "FN": fn,
                                    "FP": fp,
                                    "TN": tn,
                                    "recall": None if tp + fn == 0 else tp / (tp + fn),
                                    "precision": None if tp + fp == 0 else tp / (tp + fp),
                                }
                            )
    candidates_path = output_dir / "lateral_transfer_lf_candidates.csv"
    pd.DataFrame(candidates).to_csv(candidates_path, index=False, encoding="utf-8-sig")
    ranked = sorted(candidates, key=lambda item: (item["FN"], item["FP"], item["threshold_deg"], item["duration_s"], item["axis"], item["mode"]))
    recommendation = {
        "version": LF_VERSION,
        "segmentation_gate": gate,
        "provisional": True,
        "manual_decision_required": True,
        "positive_count": int((frame["expected_label"] == "lf").sum()),
        "negative_count": int((frame["expected_label"] != "lf").sum()),
        "best_candidate": ranked[0] if ranked else None,
        "candidates": ranked[:20],
        "note": "Tremor-labelled reps are treated as LF negative only for this labeled dataset contract, not as a medical mutual-exclusion claim.",
    }
    json_path = output_dir / "lateral_transfer_lf_threshold_recommendation.json"
    json_path.write_text(json.dumps(recommendation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return recommendation


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate lateral_transfer LF provisional threshold candidates.")
    parser.add_argument("--output-dir", type=Path, default=DERIVED_ROOT / "lf_calibration_v1")
    args = parser.parse_args()
    result = collect_lf_features(args.output_dir)
    print(json.dumps({"output_dir": str(args.output_dir), "positive_count": result["positive_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
