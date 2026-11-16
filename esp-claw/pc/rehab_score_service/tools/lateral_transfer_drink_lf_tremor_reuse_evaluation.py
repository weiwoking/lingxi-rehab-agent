from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from feature_extractors.lateral_transfer_profile import (
    DERIVED_ROOT,
    EXPECTED_SESSIONS,
    SEGMENTATION_CORE_VERSION,
    SEGMENTATION_VERSION,
)
from feature_extractors.lf_detector import DRINK_WATER_LF_VERSION, detect_drink_water_lf_from_rows
from feature_extractors.tremor_detector import TREMOR_DETECTOR_VERSION, detect_drink_water_tremor_from_rows
from tools.lateral_transfer_dataset import source_csv_for_session
from tools.lateral_transfer_segmentation_v1 import segment_lateral_transfer_file


EVALUATOR_VERSION = "lateral_transfer_drink_lf_tremor_exact_reuse_v1"


def _bool(value: Any) -> bool:
    return bool(value is True)


def _rate(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def _confusion(rows: list[dict[str, Any]], positive_label: str, detected_field: str) -> dict[str, Any]:
    tp = sum(1 for row in rows if row["expected_label"] == positive_label and row[detected_field])
    fn = sum(1 for row in rows if row["expected_label"] == positive_label and not row[detected_field])
    fp = sum(1 for row in rows if row["expected_label"] != positive_label and row[detected_field])
    tn = sum(1 for row in rows if row["expected_label"] != positive_label and not row[detected_field])
    return {
        "positive_label": positive_label,
        "TP": tp,
        "FN": fn,
        "FP": fp,
        "TN": tn,
        "evaluated_positive": tp + fn,
        "evaluated_negative": fp + tn,
        "recall": _rate(tp, tp + fn),
        "precision": _rate(tp, tp + fp),
    }


def _write_dict_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def evaluate(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    per_rep_rows: list[dict[str, Any]] = []
    skipped_sessions: list[dict[str, Any]] = []
    evaluated_session_count = 0

    for session_id, labels in EXPECTED_SESSIONS.items():
        csv_path = source_csv_for_session(session_id)
        reps, diagnostic, df = segment_lateral_transfer_file(csv_path)
        valid_reps = [rep for rep in reps if rep.get("valid") is True]
        if len(valid_reps) != len(labels):
            skipped_sessions.append(
                {
                    "session_id": session_id,
                    "expected_rep_count": len(labels),
                    "actual_valid_rep_count": len(valid_reps),
                    "reason": "SEGMENTATION_EXCLUDED_FROM_CLASSIFICATION",
                    "source_csv": str(csv_path),
                    "segmentation_version": diagnostic.get("segmentation_version"),
                    "segmentation_core_version": diagnostic.get("segmentation_core_version"),
                }
            )
            continue
        evaluated_session_count += 1
        for rep, expected_label in zip(valid_reps, labels):
            start = int(rep["start_index"])
            end = int(rep["end_index"])
            rep_df = df.iloc[start : end + 1].copy()
            lf = detect_drink_water_lf_from_rows(rep_df)
            tremor = detect_drink_water_tremor_from_rows(rep_df)
            per_rep_rows.append(
                {
                    "session_id": session_id,
                    "rep_index": int(rep["rep_index"]),
                    "expected_label": expected_label,
                    "start_index": start,
                    "end_index": end,
                    "duration_ms": rep.get("duration_ms"),
                    "lf_detected": _bool(lf.get("detected")),
                    "lf_version": lf.get("version"),
                    "lf_status": lf.get("status"),
                    "lf_neutral_roll_deg": lf.get("neutral_roll_deg"),
                    "lf_peak_abs_roll_deg": lf.get("peak_abs_roll_deg"),
                    "lf_max_duration_over_threshold_s": lf.get("max_duration_over_threshold_s"),
                    "lf_positive_duration_over_threshold_s": lf.get("positive_duration_over_threshold_s"),
                    "lf_negative_duration_over_threshold_s": lf.get("negative_duration_over_threshold_s"),
                    "tremor_detected": _bool(tremor.get("detected")),
                    "tremor_version": tremor.get("version"),
                    "tremor_status": tremor.get("status"),
                    "tremor_valid_window_count": tremor.get("valid_window_count"),
                    "tremor_positive_window_count": tremor.get("positive_window_count"),
                    "tremor_fraction": tremor.get("tremor_fraction"),
                    "tremor_frequency_band_hz": json.dumps(tremor.get("frequency_band_hz"), ensure_ascii=False),
                    "tremor_band_ratio_threshold": tremor.get("band_ratio_threshold"),
                    "tremor_band_rms_threshold": tremor.get("band_rms_threshold"),
                }
            )

    lf_confusion = _confusion(per_rep_rows, "lf", "lf_detected")
    tremor_confusion = _confusion(per_rep_rows, "tremor", "tremor_detected")
    summary = {
        "evaluator_version": EVALUATOR_VERSION,
        "lateral_wrapper_version": SEGMENTATION_VERSION,
        "segmentation_core_version": SEGMENTATION_CORE_VERSION,
        "drink_lf_version": DRINK_WATER_LF_VERSION,
        "drink_tremor_version": TREMOR_DETECTOR_VERSION,
        "exact_reuse": True,
        "parameter_sweep": False,
        "calibration_run": False,
        "evaluated_session_count": evaluated_session_count,
        "evaluated_rep_total": len(per_rep_rows),
        "segmentation_skipped_session_count": len(skipped_sessions),
        "segmentation_skipped_sessions": skipped_sessions,
        "lf_confusion": lf_confusion,
        "tremor_confusion": tremor_confusion,
    }

    (output_dir / "reuse_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_dict_csv(output_dir / "reuse_per_rep.csv", per_rep_rows)
    _write_dict_csv(output_dir / "segmentation_skipped_sessions.csv", skipped_sessions)
    _write_dict_csv(output_dir / "reuse_lf_confusion.csv", [lf_confusion])
    _write_dict_csv(output_dir / "reuse_tremor_confusion.csv", [tremor_confusion])
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate frozen drink-water LF/TREMOR detectors on lateral_transfer reps.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DERIVED_ROOT.parent / "lateral_transfer_drink_detector_reuse_evaluation",
    )
    args = parser.parse_args()
    output_dir = args.output_dir
    if output_dir.name == "lateral_transfer_drink_detector_reuse_evaluation":
        import datetime as _datetime

        output_dir = output_dir.with_name(output_dir.name + "_" + _datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    summary = evaluate(output_dir)
    print(f"DRINK_LF_VERSION={summary['drink_lf_version']}")
    print(f"DRINK_TREMOR_VERSION={summary['drink_tremor_version']}")
    print(f"EVALUATED_SESSION_COUNT={summary['evaluated_session_count']}")
    print(f"EVALUATED_REP_TOTAL={summary['evaluated_rep_total']}")
    print(f"SEGMENTATION_SKIPPED_SESSION_COUNT={summary['segmentation_skipped_session_count']}")
    tremor = summary["tremor_confusion"]
    print(f"TREMOR_TP={tremor['TP']}")
    print(f"TREMOR_FN={tremor['FN']}")
    print(f"TREMOR_FP={tremor['FP']}")
    print(f"TREMOR_TN={tremor['TN']}")
    print(f"TREMOR_RECALL={tremor['recall']}")
    print(f"TREMOR_PRECISION={tremor['precision']}")
    lf = summary["lf_confusion"]
    print(f"LF_TP={lf['TP']}")
    print(f"LF_FN={lf['FN']}")
    print(f"LF_FP={lf['FP']}")
    print(f"LF_TN={lf['TN']}")
    print(f"LF_RECALL={lf['recall']}")
    print(f"LF_PRECISION={lf['precision']}")
    print(f"OUTPUT_DIR={output_dir}")


if __name__ == "__main__":
    main()
