from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from feature_extractors import lf_detector, tremor_detector
from feature_extractors.lateral_transfer_profile import DERIVED_ROOT, EXPECTED_SESSIONS, SEGMENTATION_CORE_VERSION, SEGMENTATION_VERSION
from tools.lateral_transfer_dataset import source_csv_for_session
from tools.lateral_transfer_segmentation_v1 import segment_lateral_transfer_file


TOOL_VERSION = "lateral_transfer_thin_threshold_calibration_v1"

TREMOR_RATIO_CANDIDATES = (0.18, 0.16, 0.14)
TREMOR_RMS_CANDIDATES = (6.0, 5.5, 5.0)
TREMOR_FRACTION_CANDIDATES = (0.40, 0.35, 0.30, 0.25)

LF_ANGLE_CANDIDATES_DEG = (10.0, 11.0, 12.0, 13.0, 14.0, 15.0)
LF_PERSISTENCE_CANDIDATES_S = (1.0, 1.1, 1.2, 1.3, 1.4, 1.5)


def _write_dict_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _safe_rate(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def _confusion(rows: list[dict[str, Any]], positive_label: str, detected_field: str) -> dict[str, Any]:
    tp = sum(1 for row in rows if row["expected_label"] == positive_label and row[detected_field])
    fn = sum(1 for row in rows if row["expected_label"] == positive_label and not row[detected_field])
    fp = sum(1 for row in rows if row["expected_label"] != positive_label and row[detected_field])
    tn = sum(1 for row in rows if row["expected_label"] != positive_label and not row[detected_field])
    return {
        "TP": tp,
        "FN": fn,
        "FP": fp,
        "TN": tn,
        "recall": _safe_rate(tp, tp + fn),
        "precision": _safe_rate(tp, tp + fp),
    }


def _collect_matched_reps() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rep_rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for session_id, labels in EXPECTED_SESSIONS.items():
        csv_path = source_csv_for_session(session_id)
        reps, diagnostic, df = segment_lateral_transfer_file(csv_path)
        valid_reps = [rep for rep in reps if rep.get("valid") is True]
        if len(valid_reps) != len(labels):
            skipped.append(
                {
                    "session_id": session_id,
                    "expected_rep_count": len(labels),
                    "actual_valid_rep_count": len(valid_reps),
                    "reason": "SEGMENTATION_SKIPPED",
                    "segmentation_version": diagnostic.get("segmentation_version"),
                    "segmentation_core_version": diagnostic.get("segmentation_core_version"),
                }
            )
            continue
        for rep, expected_label in zip(valid_reps, labels):
            start = int(rep["start_index"])
            end = int(rep["end_index"])
            rep_rows.append(
                {
                    "session_id": session_id,
                    "rep_index": int(rep["rep_index"]),
                    "expected_label": expected_label,
                    "start_index": start,
                    "end_index": end,
                    "duration_ms": rep.get("duration_ms"),
                    "frame": df.iloc[start : end + 1].copy(),
                }
            )
    return rep_rows, skipped


def _tremor_window_features(frame: pd.DataFrame) -> dict[str, Any]:
    authority = tremor_detector.detect_drink_water_tremor_from_rows(frame)
    time_ms = tremor_detector._finite_series(frame, "time_ms")
    if time_ms is None or len(time_ms) < 3:
        return {"authority": authority, "windows": [], "status": "insufficient_time_ms"}
    diffs = np.diff(time_ms)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    if len(diffs) == 0:
        return {"authority": authority, "windows": [], "status": "invalid_time_ms"}
    median_step_ms = float(np.median(diffs))
    if median_step_ms <= 0:
        return {"authority": authority, "windows": [], "status": "invalid_time_ms"}
    fs = 1000.0 / median_step_ms
    window_samples = int(round(tremor_detector.TREMOR_WINDOW_SECONDS * fs))
    step_samples = max(1, int(round(tremor_detector.TREMOR_STEP_SECONDS * fs)))
    if window_samples < 8 or len(frame) < window_samples:
        return {"authority": authority, "windows": [], "status": "insufficient_reliable_window"}
    axis_values = {axis: tremor_detector._finite_series(frame, f"imu4_{axis}") for axis in ("gx", "gy", "gz")}
    windows: list[dict[str, Any]] = []
    for start in range(0, len(frame) - window_samples + 1, step_samples):
        end = start + window_samples
        axes: list[dict[str, Any]] = []
        for axis, values in axis_values.items():
            if values is None:
                continue
            segment = values[start:end]
            if len(segment) != end - start or not bool(np.all(np.isfinite(segment))):
                continue
            ratio, rms = tremor_detector._axis_band_features(segment, fs)
            axes.append({"axis": axis, "band_ratio": float(ratio), "band_rms": float(rms)})
        if axes:
            windows.append({"start_index": start, "end_index": end - 1, "axes": axes})
    return {"authority": authority, "windows": windows, "status": authority.get("status"), "fs_hz": fs}


def _tremor_detect(features: dict[str, Any], ratio_threshold: float, rms_threshold: float, fraction_threshold: float) -> tuple[bool, int, int, float | None]:
    windows = list(features.get("windows") or [])
    valid_count = len(windows)
    if valid_count == 0:
        return False, 0, 0, None
    positive = []
    for item in windows:
        axes = list(item.get("axes") or [])
        if any(
            float(axis.get("band_ratio") or 0.0) >= ratio_threshold
            and float(axis.get("band_rms") or 0.0) >= rms_threshold
            for axis in axes
        ):
            positive.append(item)
    fraction = len(positive) / valid_count
    return fraction >= fraction_threshold, len(positive), valid_count, fraction


def _lf_features(frame: pd.DataFrame) -> dict[str, Any]:
    authority = lf_detector.detect_drink_water_lf_from_rows(frame)
    time_ms = lf_detector._finite_series(frame, "time_ms")
    roll_deg = lf_detector._finite_series(frame, "imu2_roll")
    valid_mask = np.isfinite(time_ms.to_numpy(dtype=float)) & np.isfinite(roll_deg.to_numpy(dtype=float))
    if int(np.sum(valid_mask)) < lf_detector.LF_MIN_VALID_SAMPLE_COUNT:
        return {"authority": authority, "status": "insufficient_valid_roll"}
    times, smoothed_roll, rate_hz = lf_detector._smooth_valid_roll(time_ms, roll_deg, valid_mask)
    valid_times = times[valid_mask]
    median_dt_ms = lf_detector._estimate_median_dt_ms(valid_times)
    first_valid_time = float(times[np.where(valid_mask)[0][0]])
    neutral_mask = (
        valid_mask
        & np.isfinite(smoothed_roll)
        & (times >= first_valid_time)
        & (times <= first_valid_time + lf_detector.LF_NEUTRAL_WINDOW_S * 1000.0)
    )
    neutral_values = smoothed_roll[neutral_mask]
    neutral_values = neutral_values[np.isfinite(neutral_values)]
    if len(neutral_values) < lf_detector.LF_MIN_NEUTRAL_SAMPLE_COUNT:
        return {"authority": authority, "status": "insufficient_neutral_roll"}
    neutral_roll = float(np.median(neutral_values))
    relative_roll = smoothed_roll - neutral_roll
    return {
        "authority": authority,
        "status": "ok",
        "times": times,
        "valid_mask": valid_mask,
        "relative_roll": relative_roll,
        "neutral_roll_deg": neutral_roll,
        "median_dt_ms": median_dt_ms,
        "rate_hz": rate_hz,
        "peak_abs_roll_deg": float(np.nanmax(np.abs(relative_roll))) if np.any(np.isfinite(relative_roll)) else None,
    }


def _lf_detect(features: dict[str, Any], angle_threshold_deg: float, persistence_seconds: float) -> tuple[bool, float, float, float]:
    if features.get("status") != "ok":
        return False, 0.0, 0.0, 0.0
    times = features["times"]
    valid_mask = features["valid_mask"]
    relative_roll = features["relative_roll"]
    median_dt_ms = features["median_dt_ms"]
    positive_mask = valid_mask & np.isfinite(relative_roll) & (relative_roll >= angle_threshold_deg)
    negative_mask = valid_mask & np.isfinite(relative_roll) & (relative_roll <= -angle_threshold_deg)
    positive_s = lf_detector._max_continuous_duration_s(times, positive_mask, median_dt_ms)
    negative_s = lf_detector._max_continuous_duration_s(times, negative_mask, median_dt_ms)
    max_s = max(positive_s, negative_s)
    return max_s >= persistence_seconds, positive_s, negative_s, max_s


def _scan_tremor(rep_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    baseline_rows: list[dict[str, Any]] = []
    for row in rep_rows:
        features = _tremor_window_features(row["frame"])
        detected = bool(features["authority"].get("detected") is True)
        baseline_rows.append({**row, "tremor_features": features, "tremor_baseline_detected": detected})
    scan_rows: list[dict[str, Any]] = []
    stage_a = _scan_tremor_stage(baseline_rows, "FRACTION_ONLY", ratios=(0.18,), rms_values=(6.0,), fractions=TREMOR_FRACTION_CANDIDATES)
    scan_rows.extend(stage_a)
    selected = _select_tremor_candidate(stage_a)
    if not _tremor_candidate_is_acceptable(selected):
        stage_b = _scan_tremor_stage(
            baseline_rows,
            "RATIO_PLUS_FRACTION",
            ratios=TREMOR_RATIO_CANDIDATES,
            rms_values=(6.0,),
            fractions=TREMOR_FRACTION_CANDIDATES,
        )
        scan_rows.extend(stage_b)
        selected = _select_tremor_candidate(stage_b)
    if not _tremor_candidate_is_acceptable(selected):
        stage_c = _scan_tremor_stage(
            baseline_rows,
            "RMS_PLUS_FRACTION",
            ratios=(0.18,),
            rms_values=TREMOR_RMS_CANDIDATES,
            fractions=TREMOR_FRACTION_CANDIDATES,
        )
        scan_rows.extend(stage_c)
        selected = _select_tremor_candidate(stage_c)
    for row in scan_rows:
        row["recommended"] = row is selected
    per_rep_margin = _tremor_margin_rows(baseline_rows, selected)
    return scan_rows, per_rep_margin, selected


def _scan_tremor_stage(
    baseline_rows: list[dict[str, Any]],
    search_stage: str,
    *,
    ratios: tuple[float, ...],
    rms_values: tuple[float, ...],
    fractions: tuple[float, ...],
) -> list[dict[str, Any]]:
    rows_out: list[dict[str, Any]] = []
    for ratio in ratios:
        for rms in rms_values:
            for fraction in fractions:
                rows = []
                for item in baseline_rows:
                    detected, positive_count, valid_count, positive_fraction = _tremor_detect(item["tremor_features"], ratio, rms, fraction)
                    rows.append({**item, "tremor_detected": detected, "positive_window_count": positive_count, "valid_window_count": valid_count, "positive_fraction": positive_fraction})
                conf = _confusion(rows, "tremor", "tremor_detected")
                rows_out.append(
                    {
                        "search_stage": search_stage,
                        "ratio_threshold": ratio,
                        "rms_threshold": rms,
                        "fraction_threshold": fraction,
                        **conf,
                        "changed_parameter_count": int(ratio != 0.18) + int(rms != 6.0) + int(fraction != 0.40),
                        "distance_from_drink_baseline": round(abs(ratio - 0.18) + abs(rms - 6.0) / 10.0 + abs(fraction - 0.40), 6),
                        "recommended": False,
                    }
                )
    return rows_out


def _tremor_candidate_is_acceptable(candidate: dict[str, Any]) -> bool:
    return bool(candidate["TP"] == 5 and candidate["FN"] == 0 and candidate["FP"] <= 1)


def _select_tremor_candidate(scan_rows: list[dict[str, Any]]) -> dict[str, Any]:
    acceptable = [row for row in scan_rows if _tremor_candidate_is_acceptable(row)]
    if not acceptable:
        return sorted(scan_rows, key=lambda r: (r["FN"], r["FP"], r["changed_parameter_count"], r["distance_from_drink_baseline"]))[0]
    return sorted(acceptable, key=lambda r: (r["FP"], r["changed_parameter_count"], r["distance_from_drink_baseline"]))[0]


def _find_tremor_baseline_scan(scan_rows: list[dict[str, Any]]) -> dict[str, Any]:
    for row in scan_rows:
        if (
            row["ratio_threshold"] == 0.18
            and row["rms_threshold"] == 6.0
            and row["fraction_threshold"] == 0.40
        ):
            return row
    raise RuntimeError("TREMOR_BASELINE_SCAN_ROW_MISSING")


def _scan_lf(rep_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    feature_rows = []
    for row in rep_rows:
        features = _lf_features(row["frame"])
        feature_rows.append({**row, "lf_features": features, "lf_baseline_detected": bool(features["authority"].get("detected") is True)})
    scan_rows: list[dict[str, Any]] = []
    angle_only_success = False
    for angle in LF_ANGLE_CANDIDATES_DEG:
        rows = []
        for item in feature_rows:
            detected, positive_s, negative_s, max_s = _lf_detect(item["lf_features"], angle, 1.0)
            rows.append({**item, "lf_detected": detected, "lf_positive_duration_s": positive_s, "lf_negative_duration_s": negative_s, "lf_max_duration_s": max_s})
        conf = _confusion(rows, "lf", "lf_detected")
        angle_only_success = angle_only_success or (conf["TP"] == 6 and conf["FN"] == 0 and conf["FP"] <= 1)
        scan_rows.append(
            {
                "angle_threshold_deg": angle,
                "persistence_seconds": 1.0,
                **conf,
                "distance_from_drink_baseline": round(abs(angle - 10.0), 6),
                "recommended": False,
            }
        )
    if not angle_only_success:
        for persistence in LF_PERSISTENCE_CANDIDATES_S:
            if persistence == 1.0:
                continue
            rows = []
            for item in feature_rows:
                detected, positive_s, negative_s, max_s = _lf_detect(item["lf_features"], 10.0, persistence)
                rows.append({**item, "lf_detected": detected, "lf_positive_duration_s": positive_s, "lf_negative_duration_s": negative_s, "lf_max_duration_s": max_s})
            conf = _confusion(rows, "lf", "lf_detected")
            scan_rows.append(
                {
                    "angle_threshold_deg": 10.0,
                    "persistence_seconds": persistence,
                    **conf,
                    "distance_from_drink_baseline": round(abs(persistence - 1.0), 6),
                    "recommended": False,
                }
            )
    selected = _select_lf_candidate(scan_rows)
    for row in scan_rows:
        row["recommended"] = row is selected
    margin_rows = _lf_margin_rows(feature_rows, selected)
    return scan_rows, margin_rows, selected


def _select_lf_candidate(scan_rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [row for row in scan_rows if row["TP"] == 6 and row["FN"] == 0 and row["FP"] <= 1]
    if not candidates:
        candidates = [row for row in scan_rows if row["TP"] == 6 and row["FN"] == 0]
    if not candidates:
        candidates = scan_rows
    return sorted(candidates, key=lambda r: (r["FN"], r["FP"], r["distance_from_drink_baseline"]))[0]


def _stability(scan_rows: list[dict[str, Any]], selected: dict[str, Any], keys: tuple[str, ...]) -> str:
    equivalent = [
        row for row in scan_rows
        if row["TP"] == selected["TP"] and row["FN"] == selected["FN"] and row["FP"] == selected["FP"] and row["TN"] == selected["TN"]
    ]
    unique_points = {tuple(row[key] for key in keys) for row in equivalent}
    if len(unique_points) >= 3:
        return "HIGH"
    if len(unique_points) == 2:
        return "MEDIUM"
    return "LOW"


def _tremor_margin_rows(rep_rows: list[dict[str, Any]], selected: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in rep_rows:
        detected, positive_count, valid_count, fraction = _tremor_detect(
            item["tremor_features"],
            selected["ratio_threshold"],
            selected["rms_threshold"],
            selected["fraction_threshold"],
        )
        windows = item["tremor_features"].get("windows") or []
        axes = [axis for window in windows for axis in list(window.get("axes") or [])]
        max_ratio = max([float(axis["band_ratio"]) for axis in axes], default=None)
        max_rms = max([float(axis["band_rms"]) for axis in axes], default=None)
        out.append(
            {
                "session_id": item["session_id"],
                "rep_index": item["rep_index"],
                "expected_label": item["expected_label"],
                "detected": detected,
                "valid_window_count": valid_count,
                "positive_window_count": positive_count,
                "positive_fraction": fraction,
                "fraction_margin": None if fraction is None else fraction - selected["fraction_threshold"],
                "max_band_ratio": max_ratio,
                "ratio_margin": None if max_ratio is None else max_ratio - selected["ratio_threshold"],
                "max_band_rms": max_rms,
                "rms_margin": None if max_rms is None else max_rms - selected["rms_threshold"],
            }
        )
    return out


def _lf_margin_rows(rep_rows: list[dict[str, Any]], selected: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in rep_rows:
        detected, positive_s, negative_s, max_s = _lf_detect(
            item["lf_features"],
            selected["angle_threshold_deg"],
            selected["persistence_seconds"],
        )
        features = item["lf_features"]
        peak = features.get("peak_abs_roll_deg")
        direction = "positive" if positive_s >= negative_s else "negative"
        out.append(
            {
                "session_id": item["session_id"],
                "rep_index": item["rep_index"],
                "expected_label": item["expected_label"],
                "detected": detected,
                "neutral_roll_deg": features.get("neutral_roll_deg"),
                "signed_excursion_direction": direction,
                "excursion_magnitude_deg": peak,
                "max_persistent_duration_s": max_s,
                "duration_margin_s": max_s - selected["persistence_seconds"],
                "angle_margin_deg": None if peak is None else peak - selected["angle_threshold_deg"],
                "positive_duration_s": positive_s,
                "negative_duration_s": negative_s,
            }
        )
    return out


def _error_cases(per_rep: list[dict[str, Any]], selected: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    out = []
    for row in per_rep:
        expected = row["expected_label"]
        detected = bool(row["detected"])
        if kind == "tremor":
            if expected == "tremor" and not detected:
                failure = "FN"
            elif expected != "tremor" and detected:
                failure = "FP"
            else:
                continue
            gates = []
            if row.get("positive_fraction") is None or row.get("positive_fraction") < selected["fraction_threshold"]:
                gates.append("FRACTION_GATE")
            if row.get("max_band_ratio") is None or row.get("max_band_ratio") < selected["ratio_threshold"]:
                gates.append("RATIO_GATE")
            if row.get("max_band_rms") is None or row.get("max_band_rms") < selected["rms_threshold"]:
                gates.append("RMS_GATE")
            out.append({**row, "failure_type": failure, "likely_blocking_gate": "MULTIPLE_GATES" if len(gates) > 1 else (gates[0] if gates else "NONE")})
        else:
            if expected == "lf" and not detected:
                failure = "FN"
            elif expected != "lf" and detected:
                failure = "FP"
            else:
                continue
            angle_block = row.get("angle_margin_deg") is None or row.get("angle_margin_deg") < 0
            persistence_block = row.get("duration_margin_s") is None or row.get("duration_margin_s") < 0
            if angle_block and persistence_block:
                likely = "BOTH"
            elif angle_block:
                likely = "ANGLE"
            elif persistence_block:
                likely = "PERSISTENCE"
            else:
                likely = "NONE"
            out.append({**row, "failure_type": failure, "likely_differentiator": likely})
    return out


def _separation(rows: list[dict[str, Any]], positive_label: str, margin_key: str) -> str:
    pos = [float(row[margin_key]) for row in rows if row["expected_label"] == positive_label and row.get(margin_key) is not None]
    neg = [float(row[margin_key]) for row in rows if row["expected_label"] != positive_label and row.get(margin_key) is not None]
    if not pos or not neg:
        return "UNKNOWN"
    return "POSITIVE" if min(pos) > max(neg) else "OVERLAP"


def calibrate(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rep_rows, skipped = _collect_matched_reps()
    baseline_tremor_rows = []
    baseline_lf_rows = []
    for item in rep_rows:
        tremor = tremor_detector.detect_drink_water_tremor_from_rows(item["frame"])
        lf = lf_detector.detect_drink_water_lf_from_rows(item["frame"])
        baseline_tremor_rows.append({**item, "tremor_detected": bool(tremor.get("detected") is True)})
        baseline_lf_rows.append({**item, "lf_detected": bool(lf.get("detected") is True)})
    baseline_tremor = _confusion(baseline_tremor_rows, "tremor", "tremor_detected")
    baseline_lf = _confusion(baseline_lf_rows, "lf", "lf_detected")

    tremor_scan, tremor_margin, tremor_selected = _scan_tremor(rep_rows)
    baseline_tremor_scan = _find_tremor_baseline_scan(tremor_scan)
    tremor_authority_equivalent = all(
        baseline_tremor_scan[key] == baseline_tremor[key]
        for key in ("TP", "FN", "FP", "TN")
    )
    if not tremor_authority_equivalent:
        raise RuntimeError(
            "TREMOR_AUTHORITY_BASELINE_NOT_EQUIVALENT:"
            f"authority=TP{baseline_tremor['TP']}/FN{baseline_tremor['FN']}/FP{baseline_tremor['FP']}/TN{baseline_tremor['TN']}:"
            f"calibration=TP{baseline_tremor_scan['TP']}/FN{baseline_tremor_scan['FN']}/FP{baseline_tremor_scan['FP']}/TN{baseline_tremor_scan['TN']}"
        )
    lf_scan, lf_margin, lf_selected = _scan_lf(rep_rows)
    baseline_tremor_candidate = {"ratio_threshold": 0.18, "rms_threshold": 6.0, "fraction_threshold": 0.40}
    baseline_lf_candidate = {"angle_threshold_deg": 10.0, "persistence_seconds": 1.0}
    baseline_tremor_margin = _tremor_margin_rows(
        [
            {**row, "tremor_features": _tremor_window_features(row["frame"])}
            for row in rep_rows
        ],
        baseline_tremor_candidate,
    )
    baseline_lf_margin = _lf_margin_rows(
        [
            {**row, "lf_features": _lf_features(row["frame"])}
            for row in rep_rows
        ],
        baseline_lf_candidate,
    )
    tremor_errors = _error_cases(baseline_tremor_margin, baseline_tremor_candidate, "tremor")
    lf_errors = _error_cases(baseline_lf_margin, baseline_lf_candidate, "lf")

    _write_dict_csv(output_dir / "segmentation_skipped_sessions.csv", skipped)
    _write_dict_csv(output_dir / "tremor_threshold_scan.csv", tremor_scan)
    _write_dict_csv(output_dir / "lf_threshold_scan.csv", lf_scan)
    _write_dict_csv(output_dir / "tremor_per_rep_margin.csv", tremor_margin)
    _write_dict_csv(output_dir / "lf_per_rep_margin.csv", lf_margin)
    _write_dict_csv(output_dir / "tremor_error_cases.csv", tremor_errors)
    _write_dict_csv(output_dir / "lf_error_cases.csv", lf_errors)

    summary = {
        "tool_version": TOOL_VERSION,
        "segmentation_version": SEGMENTATION_VERSION,
        "segmentation_core_version": SEGMENTATION_CORE_VERSION,
        "drink_lf_version": lf_detector.DRINK_WATER_LF_VERSION,
        "drink_tremor_version": tremor_detector.TREMOR_DETECTOR_VERSION,
        "profile_written_to_production": False,
        "parameter_search_bounded": True,
        "emg_tremor_implemented": False,
        "evaluated_session_count": len({row["session_id"] for row in rep_rows}),
        "evaluated_rep_total": len(rep_rows),
        "segmentation_skipped_session_count": len(skipped),
        "baseline_tremor": baseline_tremor,
        "tremor_authority_baseline_equivalent": tremor_authority_equivalent,
        "baseline_tremor_error_cases": tremor_errors,
        "recommended_tremor": tremor_selected,
        "recommended_tremor_search_stage": tremor_selected["search_stage"],
        "tremor_stability": _stability(tremor_scan, tremor_selected, ("ratio_threshold", "rms_threshold", "fraction_threshold")),
        "tremor_separation_margin": _separation(tremor_margin, "tremor", "fraction_margin"),
        "baseline_lf": baseline_lf,
        "baseline_lf_error_cases": lf_errors,
        "recommended_lf": lf_selected,
        "lf_stability": _stability(lf_scan, lf_selected, ("angle_threshold_deg", "persistence_seconds")),
        "lf_separation_margin": _separation(lf_margin, "lf", "duration_margin_s"),
    }
    (output_dir / "calibration_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run bounded thin-threshold calibration for lateral_transfer LF/TREMOR.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DERIVED_ROOT.parent / "lateral_transfer_thin_threshold_calibration",
    )
    args = parser.parse_args()
    output_dir = args.output_dir
    if output_dir.name == "lateral_transfer_thin_threshold_calibration":
        import datetime as _datetime

        output_dir = output_dir.with_name(output_dir.name + "_" + _datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    summary = calibrate(output_dir)
    bt = summary["baseline_tremor"]
    rt = summary["recommended_tremor"]
    bl = summary["baseline_lf"]
    rl = summary["recommended_lf"]
    print(f"BASELINE_TREMOR=TP{bt['TP']}/FN{bt['FN']}/FP{bt['FP']}/TN{bt['TN']}")
    print(f"TREMOR_AUTHORITY_BASELINE_EQUIVALENT={'YES' if summary['tremor_authority_baseline_equivalent'] else 'NO'}")
    tremor_fn = [
        f"{row['session_id']}:rep_{int(row['rep_index']):02d}"
        for row in summary["baseline_tremor_error_cases"]
        if row.get("failure_type") == "FN"
    ]
    print(f"TREMOR_FN_SESSION_REPS={','.join(tremor_fn)}")
    print(f"RECOMMENDED_TREMOR_RATIO={rt['ratio_threshold']}")
    print(f"RECOMMENDED_TREMOR_RMS={rt['rms_threshold']}")
    print(f"RECOMMENDED_TREMOR_FRACTION={rt['fraction_threshold']}")
    print(f"RECOMMENDED_TREMOR_SEARCH_STAGE={summary['recommended_tremor_search_stage']}")
    print(f"RECOMMENDED_TREMOR_TP={rt['TP']}")
    print(f"RECOMMENDED_TREMOR_FN={rt['FN']}")
    print(f"RECOMMENDED_TREMOR_FP={rt['FP']}")
    print(f"RECOMMENDED_TREMOR_TN={rt['TN']}")
    print(f"TREMOR_STABILITY={summary['tremor_stability']}")
    print(f"TREMOR_SEPARATION_MARGIN={summary['tremor_separation_margin']}")
    print(f"BASELINE_LF=TP{bl['TP']}/FN{bl['FN']}/FP{bl['FP']}/TN{bl['TN']}")
    lf_fp = [
        f"{row['session_id']}:rep_{int(row['rep_index']):02d}"
        for row in summary["baseline_lf_error_cases"]
        if row.get("failure_type") == "FP"
    ]
    print(f"LF_FP_SESSION_REPS={','.join(lf_fp)}")
    print(f"RECOMMENDED_LF_ANGLE={rl['angle_threshold_deg']}")
    print(f"RECOMMENDED_LF_PERSISTENCE={rl['persistence_seconds']}")
    print(f"RECOMMENDED_LF_TP={rl['TP']}")
    print(f"RECOMMENDED_LF_FN={rl['FN']}")
    print(f"RECOMMENDED_LF_FP={rl['FP']}")
    print(f"RECOMMENDED_LF_TN={rl['TN']}")
    print(f"LF_STABILITY={summary['lf_stability']}")
    print(f"LF_SEPARATION_MARGIN={summary['lf_separation_margin']}")
    print(f"PROFILE_WRITTEN_TO_PRODUCTION={summary['profile_written_to_production']}")
    print(f"OUTPUT_DIR={output_dir}")


if __name__ == "__main__":
    main()
