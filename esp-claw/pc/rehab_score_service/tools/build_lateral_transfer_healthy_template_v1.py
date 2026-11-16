from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from statistics import mean, stdev
from typing import Any

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from cloud_interface.raw_log_to_trial import build_trial_from_raw_log
from feature_extractors.lateral_transfer_profile import (
    DERIVED_ROOT,
    LF,
    LF_VERSION,
    MOTION_FAMILY,
    SEGMENTATION_CORE_VERSION,
    SEGMENTATION_VERSION,
    SENSOR_MAPPING_VERSION,
    TEMPLATE_PATH,
    TEMPLATE_VERSION,
    TREMOR,
    TREMOR_VERSION,
)
from tools.lateral_transfer_dataset import sha256_file, source_csv_for_session, write_dataset_manifest
from tools.lateral_transfer_segmentation_v1 import segment_lateral_transfer_file
from tools.lateral_transfer_segmentation_gate import SegmentationNotReadyError, assert_lateral_segmentation_ready, write_gate_failure_marker


FEATURE_FIELDS = [
    "WristMeanSpeed_mm_s",
    "NumSpeedPeaks",
    "PauseCount",
    "UpperArmROM_mm",
    "ForearmRotationRange_deg",
    "ForearmMeanAngularSpeed_deg_s",
    "Extensor_digitorum_RMS",
    "Extensor_digitorum_MAV",
    "Extensor_digitorum_WL_mean",
    "Biceps_brachii_RMS",
    "Biceps_brachii_MAV",
    "Biceps_brachii_WL_mean",
    "Triceps_brachii_RMS",
    "Triceps_brachii_MAV",
    "Triceps_brachii_WL_mean",
    "Extensor_carpi_ulnaris_RMS",
    "Extensor_carpi_ulnaris_MAV",
    "Extensor_carpi_ulnaris_WL_mean",
]


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "std": 1.0}
    return {"mean": float(mean(values)), "std": float(stdev(values)) if len(values) > 1 else 1.0}


def build_template(output_dir: Path) -> dict[str, Any]:
    try:
        gate = assert_lateral_segmentation_ready()
    except SegmentationNotReadyError as exc:
        write_gate_failure_marker(output_dir, exc)
        raise
    output_dir.mkdir(parents=True, exist_ok=True)
    write_dataset_manifest(output_dir)
    records: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    feature_values = {field: [] for field in FEATURE_FIELDS}
    for approved_session in gate["approved_sessions"]:
        session_id = str(approved_session["session_id"])
        labels = list(approved_session["expected_labels"])
        csv_path = source_csv_for_session(session_id)
        reps, _diagnostic, df = segment_lateral_transfer_file(csv_path)
        for index, expected_label in enumerate(labels, start=1):
            if expected_label != "normal":
                excluded.append({"session_id": session_id, "rep_index": index, "reason": f"label={expected_label}"})
                continue
            matching = [rep for rep in reps if rep.get("rep_index") == index and rep.get("valid") is True]
            if not matching:
                raise RuntimeError(f"normal_reference_rep_missing_or_invalid:{session_id}:rep_{index:02d}")
            rep = matching[0]
            rep_path = output_dir / "reference_reps" / session_id / f"rep_{index:02d}.csv"
            rep_path.parent.mkdir(parents=True, exist_ok=True)
            df.iloc[int(rep["start_index"]) : int(rep["end_index"]) + 1].to_csv(rep_path, index=False, encoding="utf-8-sig")
            trial = build_trial_from_raw_log(
                rep_path,
                patient_id="lateral_template_builder",
                session_id=session_id,
                task_number=24,
                trial_id=index,
            )
            for field in FEATURE_FIELDS:
                value = trial.get(field)
                if isinstance(value, (int, float)):
                    feature_values[field].append(float(value))
            records.append(
                {
                    "session_id": session_id,
                    "rep_index": index,
                    "duration_ms": rep.get("duration_ms"),
                    "source_csv": str(csv_path),
                    "source_sha256": sha256_file(csv_path),
                    "rep_csv": str(rep_path),
                    "rep_sha256": sha256_file(rep_path),
                }
            )
    if len(records) != 48:
        raise RuntimeError(f"healthy_normal_rep_count_mismatch:expected=48:actual={len(records)}")
    template = {
        "template_version": TEMPLATE_VERSION,
        "motion_family": MOTION_FAMILY,
        "sensor_mapping_version": SENSOR_MAPPING_VERSION,
        "task_family": MOTION_FAMILY,
        "segmentation_wrapper_version": SEGMENTATION_VERSION,
        "segmentation_core_version": SEGMENTATION_CORE_VERSION,
        "approved_session_count": gate["approved_session_count"],
        "approved_rep_count": gate["approved_rep_total"],
        "healthy_normal_rep_count": len(records),
        "lf_detector_version": LF_VERSION,
        "tremor_detector_version": TREMOR_VERSION,
        "lateral_lf_parameters": {
            "angle_threshold_deg": LF.threshold_deg,
            "persistence_seconds": LF.min_duration_s,
        },
        "lateral_tremor_parameters": {
            "band_ratio_threshold": TREMOR.band_ratio_threshold,
            "band_rms_threshold": TREMOR.band_rms_threshold,
            "positive_window_fraction": TREMOR.fraction_threshold,
        },
        "provisional": False,
        "source": {
            "segmentation_gate": gate,
            "normal_reference_rep_count": len(records),
            "excluded_rep_count": len(excluded),
            "records": records,
            "excluded": excluded,
        },
        "feature_statistics": {
            field: _stats(values)
            for field, values in feature_values.items()
        },
        "score_profile": {
            "speed_mean": _stats(feature_values["WristMeanSpeed_mm_s"])["mean"],
            "speed_std": _stats(feature_values["WristMeanSpeed_mm_s"])["std"],
            "num_speed_peaks_mean": _stats(feature_values["NumSpeedPeaks"])["mean"],
            "num_speed_peaks_std": _stats(feature_values["NumSpeedPeaks"])["std"],
            "pause_count_mean": _stats(feature_values["PauseCount"])["mean"],
            "pause_count_std": _stats(feature_values["PauseCount"])["std"],
            "ROM_mean": _stats(feature_values["UpperArmROM_mm"])["mean"],
            "ROM_std": _stats(feature_values["UpperArmROM_mm"])["std"],
            "forearm_rotation_mean": _stats(feature_values["ForearmRotationRange_deg"])["mean"],
            "forearm_rotation_std": _stats(feature_values["ForearmRotationRange_deg"])["std"],
            "forearm_angular_speed_mean": _stats(feature_values["ForearmMeanAngularSpeed_deg_s"])["mean"],
            "forearm_angular_speed_std": _stats(feature_values["ForearmMeanAngularSpeed_deg_s"])["std"],
        },
    }
    for muscle in ("Extensor_digitorum", "Biceps_brachii", "Triceps_brachii", "Extensor_carpi_ulnaris"):
        for feature in ("RMS", "MAV", "WL_mean"):
            stats = _stats(feature_values[f"{muscle}_{feature}"])
            template["score_profile"][f"{muscle}_{feature}_mean"] = stats["mean"]
            template["score_profile"][f"{muscle}_{feature}_std"] = stats["std"]
    output_path = output_dir / "lateral_transfer_healthy_template_v1.json"
    output_path.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return template


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the approved lateral_transfer healthy template from manual-normal reps.")
    parser.add_argument("--output-dir", type=Path, default=DERIVED_ROOT / "healthy_template_v1")
    parser.add_argument("--write-template", action="store_true", help="Copy the generated template to REHAB_SCORING_ASSETS_ROOT/templates.")
    parser.add_argument("--overwrite-formal-template", action="store_true", help="Allow replacing an existing lateral_transfer template file.")
    args = parser.parse_args()
    template = build_template(args.output_dir)
    generated_path = args.output_dir / "lateral_transfer_healthy_template_v1.json"
    formal_template_written = False
    if args.write_template:
        if TEMPLATE_PATH.exists() and not args.overwrite_formal_template:
            raise FileExistsError(f"lateral_formal_template_exists:{TEMPLATE_PATH}")
        TEMPLATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(generated_path, TEMPLATE_PATH)
        formal_template_written = True
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "generated_template": str(generated_path),
                "formal_template_path": str(TEMPLATE_PATH),
                "formal_template_written": formal_template_written,
                "normal_reference_rep_count": template["source"]["normal_reference_rep_count"],
                "template_version": template["template_version"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
