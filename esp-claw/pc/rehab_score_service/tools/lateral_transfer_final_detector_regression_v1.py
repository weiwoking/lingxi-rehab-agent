from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from feature_extractors.lateral_transfer_detectors import detect_lateral_transfer_lf, detect_lateral_transfer_tremor
from feature_extractors.lateral_transfer_profile import (
    DERIVED_ROOT,
    LF,
    LF_VERSION,
    SEGMENTATION_CORE_VERSION,
    SEGMENTATION_VERSION,
    TEMPLATE_VERSION,
    TREMOR,
    TREMOR_VERSION,
)
from tools.lateral_transfer_segmentation_gate import assert_lateral_segmentation_ready
from tools.lateral_transfer_segmentation_v1 import segment_lateral_transfer_file


REGRESSION_VERSION = "lateral_transfer_final_detector_regression_v1"


def _confusion(rows: list[dict[str, Any]], positive_label: str, detected_field: str) -> dict[str, Any]:
    tp = sum(1 for row in rows if row["manual_label"] == positive_label and row[detected_field])
    fn = sum(1 for row in rows if row["manual_label"] == positive_label and not row[detected_field])
    fp = sum(1 for row in rows if row["manual_label"] != positive_label and row[detected_field])
    tn = sum(1 for row in rows if row["manual_label"] != positive_label and not row[detected_field])
    return {"TP": tp, "FN": fn, "FP": fp, "TN": tn}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
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
    gate = assert_lateral_segmentation_ready()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for approved_session in gate["approved_sessions"]:
        session_id = str(approved_session["session_id"])
        labels = list(approved_session["expected_labels"])
        reps, _diagnostic, df = segment_lateral_transfer_file(Path(str(approved_session["source_csv"])))
        valid_reps = [rep for rep in reps if rep.get("valid") is True]
        for rep, manual_label in zip(valid_reps, labels):
            start = int(rep["start_index"])
            end = int(rep["end_index"])
            rep_df = df.iloc[start : end + 1].copy()
            lf = detect_lateral_transfer_lf(rep_df)
            tremor = detect_lateral_transfer_tremor(rep_df)
            rows.append(
                {
                    "session_id": session_id,
                    "rep_index": int(rep["rep_index"]),
                    "manual_label": manual_label,
                    "lf_detected": bool(lf.get("detected") is True),
                    "tremor_detected": bool(tremor.get("detected") is True),
                    "lf_version": lf.get("version"),
                    "tremor_version": tremor.get("version"),
                    "lf_threshold_deg": LF.threshold_deg,
                    "lf_persistence_seconds": LF.min_duration_s,
                    "tremor_ratio_threshold": TREMOR.band_ratio_threshold,
                    "tremor_rms_threshold": TREMOR.band_rms_threshold,
                    "tremor_fraction_threshold": TREMOR.fraction_threshold,
                    "start_index": start,
                    "end_index": end,
                }
            )
    tremor_confusion = _confusion(rows, "tremor", "tremor_detected")
    lf_confusion = _confusion(rows, "lf", "lf_detected")
    summary = {
        "regression_version": REGRESSION_VERSION,
        "approved_session_count": gate["approved_session_count"],
        "approved_rep_count": gate["approved_rep_total"],
        "healthy_normal_rep_count": gate["healthy_normal_rep_count"],
        "segmentation_wrapper_version": SEGMENTATION_VERSION,
        "segmentation_core_version": SEGMENTATION_CORE_VERSION,
        "template_version": TEMPLATE_VERSION,
        "lf_algorithm_version": LF_VERSION,
        "tremor_algorithm_version": TREMOR_VERSION,
        "lateral_lf_parameters": {"angle_threshold_deg": LF.threshold_deg, "persistence_seconds": LF.min_duration_s},
        "lateral_tremor_parameters": {
            "band_ratio_threshold": TREMOR.band_ratio_threshold,
            "band_rms_threshold": TREMOR.band_rms_threshold,
            "positive_window_fraction": TREMOR.fraction_threshold,
        },
        "tremor_confusion": tremor_confusion,
        "lf_confusion": lf_confusion,
        "expected_tremor_confusion": {"TP": 5, "FN": 0, "FP": 0, "TN": 54},
        "expected_lf_confusion": {"TP": 6, "FN": 0, "FP": 2, "TN": 51},
        "final_detector_regression": (
            "PASS"
            if tremor_confusion == {"TP": 5, "FN": 0, "FP": 0, "TN": 54}
            and lf_confusion == {"TP": 6, "FN": 0, "FP": 2, "TN": 51}
            else "FAIL"
        ),
    }
    (output_dir / "final_detector_regression_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(output_dir / "final_detector_regression_per_rep.csv", rows)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run final lateral_transfer LF/TREMOR detector regression on approved reps.")
    parser.add_argument("--output-dir", type=Path, default=DERIVED_ROOT / "final_detector_regression_v1")
    args = parser.parse_args()
    summary = evaluate(args.output_dir)
    print(f"APPROVED_SESSION_COUNT={summary['approved_session_count']}")
    print(f"APPROVED_REP_COUNT={summary['approved_rep_count']}")
    print(f"HEALTHY_NORMAL_REP_COUNT={summary['healthy_normal_rep_count']}")
    tremor = summary["tremor_confusion"]
    print(f"TREMOR_TP={tremor['TP']}")
    print(f"TREMOR_FN={tremor['FN']}")
    print(f"TREMOR_FP={tremor['FP']}")
    print(f"TREMOR_TN={tremor['TN']}")
    lf = summary["lf_confusion"]
    print(f"LF_TP={lf['TP']}")
    print(f"LF_FN={lf['FN']}")
    print(f"LF_FP={lf['FP']}")
    print(f"LF_TN={lf['TN']}")
    print(f"FINAL_DETECTOR_REGRESSION={summary['final_detector_regression']}")
    print(f"OUTPUT_DIR={args.output_dir}")
    if summary["final_detector_regression"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
