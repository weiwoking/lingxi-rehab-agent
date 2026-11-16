from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from feature_extractors.lateral_transfer_profile import DERIVED_ROOT, TREMOR, TREMOR_VERSION
from tools.lateral_transfer_dataset import source_csv_for_session
from tools.lateral_transfer_segmentation_v1 import segment_lateral_transfer_file
from tools.lateral_transfer_segmentation_gate import SegmentationNotReadyError, assert_lateral_segmentation_ready, write_gate_failure_marker


BANDS_HZ = ((3.0, 5.0), (3.5, 5.5), (3.5, 6.0), (4.0, 6.0), (4.0, 7.0))
WINDOW_SECONDS = (1.5, 2.0, 2.5)
STEP_SECONDS = (0.5, 1.0)
BAND_RATIO_THRESHOLDS = (0.10, 0.15, 0.20, 0.25, 0.30)
FRACTION_THRESHOLDS = (0.20, 0.30, 0.40, 0.50, 0.60)


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


def _band_power(freqs: np.ndarray, psd: np.ndarray, band: tuple[float, float]) -> float:
    mask = (freqs >= band[0]) & (freqs <= band[1])
    if not np.any(mask):
        return 0.0
    return float(np.trapz(psd[mask], freqs[mask]))


def _window_feature_rows(frame: pd.DataFrame, rep_key: dict[str, Any]) -> list[dict[str, Any]]:
    times = _series(frame, "time_ms")
    valid_time = np.isfinite(times)
    median_dt = _median_dt_ms(times[valid_time])
    if median_dt is None:
        return []
    fs = 1000.0 / median_dt
    max_gap_ms = median_dt * TREMOR.max_gap_factor
    axes = {axis: _series(frame, f"{TREMOR.gyro_source}_{axis}") for axis in ("gx", "gy", "gz")}
    rows: list[dict[str, Any]] = []
    for window_seconds in WINDOW_SECONDS:
        window_samples = int(round(window_seconds * fs))
        if window_samples < 8:
            continue
        for step_seconds in STEP_SECONDS:
            step_samples = max(1, int(round(step_seconds * fs)))
            for start in range(0, len(frame) - window_samples + 1, step_samples):
                end = start + window_samples
                window_time = times[start:end]
                if not np.all(np.isfinite(window_time)):
                    continue
                diffs = np.diff(window_time)
                if np.any(diffs <= 0) or np.max(diffs) > max_gap_ms:
                    continue
                for axis, values in axes.items():
                    segment = values[start:end]
                    if not np.all(np.isfinite(segment)):
                        continue
                    centered = segment - float(np.mean(segment))
                    freqs = np.fft.rfftfreq(len(centered), d=1.0 / fs)
                    psd = (np.abs(np.fft.rfft(centered)) ** 2) / max(len(centered), 1)
                    total_power = float(np.trapz(psd, freqs))
                    band_rms = float(np.sqrt(np.mean(centered ** 2)))
                    for band in BANDS_HZ:
                        band_power = _band_power(freqs, psd, band)
                        band_ratio = 0.0 if total_power <= 0 else band_power / total_power
                        rows.append(
                            {
                                **rep_key,
                                "axis": axis,
                                "window_start_index": start,
                                "window_end_index": end - 1,
                                "window_start_time_ms": float(window_time[0]),
                                "window_end_time_ms": float(window_time[-1]),
                                "window_seconds": window_seconds,
                                "step_seconds": step_seconds,
                                "band_low_hz": band[0],
                                "band_high_hz": band[1],
                                "band_ratio": band_ratio,
                                "band_rms": band_rms,
                                "median_dt_ms": median_dt,
                                "max_allowed_gap_ms": max_gap_ms,
                            }
                        )
    return rows


def _candidate_rms_thresholds(values: pd.Series) -> list[float]:
    finite = pd.to_numeric(values, errors="coerce").dropna()
    if finite.empty:
        return [6.0]
    quantiles = [0.50, 0.60, 0.70, 0.80, 0.90, 0.95]
    thresholds = {round(float(finite.quantile(q)), 6) for q in quantiles}
    thresholds.add(6.0)
    return sorted(value for value in thresholds if value >= 0.0)


def collect_tremor_features(output_dir: Path) -> dict[str, Any]:
    try:
        gate = assert_lateral_segmentation_ready()
    except SegmentationNotReadyError as exc:
        write_gate_failure_marker(output_dir, exc)
        raise
    output_dir.mkdir(parents=True, exist_ok=True)
    window_rows: list[dict[str, Any]] = []
    rep_rows: list[dict[str, Any]] = []
    for approved_session in gate["approved_sessions"]:
        session_id = str(approved_session["session_id"])
        labels = list(approved_session["expected_labels"])
        csv_path = source_csv_for_session(session_id)
        reps, _diagnostic, df = segment_lateral_transfer_file(csv_path)
        for rep in reps:
            index = int(rep["rep_index"])
            expected_label = labels[index - 1] if index <= len(labels) else "unexpected"
            key = {
                "session_id": session_id,
                "rep_index": index,
                "expected_label": expected_label,
                "is_tremor_positive": expected_label == "tremor",
            }
            rep_df = df.iloc[int(rep["start_index"]) : int(rep["end_index"]) + 1].copy()
            rows = _window_feature_rows(rep_df, key)
            window_rows.extend(rows)
            rep_rows.append({**key, "valid_window_feature_count": len(rows)})
    window_frame = pd.DataFrame(window_rows)
    rep_frame = pd.DataFrame(rep_rows)
    window_frame.to_csv(output_dir / "lateral_transfer_tremor_window_features.csv", index=False, encoding="utf-8-sig")
    rep_frame.to_csv(output_dir / "lateral_transfer_tremor_rep_features.csv", index=False, encoding="utf-8-sig")

    candidates: list[dict[str, Any]] = []
    rms_thresholds = _candidate_rms_thresholds(window_frame["band_rms"] if "band_rms" in window_frame else pd.Series(dtype=float))
    for band in BANDS_HZ:
        for window_seconds in WINDOW_SECONDS:
            for step_seconds in STEP_SECONDS:
                subset = window_frame[
                    (window_frame["band_low_hz"] == band[0])
                    & (window_frame["band_high_hz"] == band[1])
                    & (window_frame["window_seconds"] == window_seconds)
                    & (window_frame["step_seconds"] == step_seconds)
                ] if not window_frame.empty else pd.DataFrame()
                for ratio_threshold in BAND_RATIO_THRESHOLDS:
                    for rms_threshold in rms_thresholds:
                        rep_counts = subset.assign(
                            positive=(subset["band_ratio"] >= ratio_threshold) & (subset["band_rms"] >= rms_threshold)
                        ).groupby(["session_id", "rep_index", "expected_label", "is_tremor_positive"])["positive"].agg(["count", "sum"]).reset_index() if not subset.empty else pd.DataFrame()
                        for fraction_threshold in FRACTION_THRESHOLDS:
                            if rep_counts.empty:
                                detected = pd.Series([], dtype=bool)
                                positives = pd.Series([], dtype=bool)
                            else:
                                fraction = rep_counts["sum"] / rep_counts["count"].clip(lower=1)
                                detected = fraction >= fraction_threshold
                                positives = rep_counts["is_tremor_positive"].astype(bool)
                            tp = int((positives & detected).sum())
                            fn = int((positives & ~detected).sum())
                            fp = int((~positives & detected).sum())
                            tn = int((~positives & ~detected).sum())
                            candidates.append(
                                {
                                    "rule": (
                                        f"{band[0]}-{band[1]}Hz window={window_seconds}s step={step_seconds}s "
                                        f"ratio>={ratio_threshold} rms>={rms_threshold} fraction>={fraction_threshold}"
                                    ),
                                    "band_low_hz": band[0],
                                    "band_high_hz": band[1],
                                    "window_seconds": window_seconds,
                                    "step_seconds": step_seconds,
                                    "band_ratio_threshold": ratio_threshold,
                                    "band_rms_threshold": rms_threshold,
                                    "fraction_threshold": fraction_threshold,
                                    "TP": tp,
                                    "FN": fn,
                                    "FP": fp,
                                    "TN": tn,
                                    "recall": None if tp + fn == 0 else tp / (tp + fn),
                                    "precision": None if tp + fp == 0 else tp / (tp + fp),
                                }
                            )
    pd.DataFrame(candidates).to_csv(output_dir / "lateral_transfer_tremor_candidates.csv", index=False, encoding="utf-8-sig")
    ranked = sorted(candidates, key=lambda item: (item["FN"], item["FP"], item["band_low_hz"], item["band_high_hz"], item["window_seconds"], item["band_ratio_threshold"]))
    recommendation = {
        "version": TREMOR_VERSION,
        "segmentation_gate": gate,
        "provisional": True,
        "manual_decision_required": True,
        "positive_count": sum(1 for row in rep_rows if row["expected_label"] == "tremor"),
        "negative_count": sum(1 for row in rep_rows if row["expected_label"] != "tremor"),
        "best_candidate": ranked[0] if ranked else None,
        "candidates": ranked[:30],
        "window_gap_rule": "dt <= median_dt_ms * TREMOR.max_gap_factor",
    }
    (output_dir / "lateral_transfer_tremor_threshold_recommendation.json").write_text(
        json.dumps(recommendation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return recommendation


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate lateral_transfer TREMOR full provisional parameter candidates.")
    parser.add_argument("--output-dir", type=Path, default=DERIVED_ROOT / "tremor_calibration_v1")
    args = parser.parse_args()
    result = collect_tremor_features(args.output_dir)
    print(json.dumps({"output_dir": str(args.output_dir), "positive_count": result["positive_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
