"""Rebuild Drink Water V2.1.1 baseline from exported repetitions.

This is an offline-only derivation tool. It reads V2.1.1 segmentation outputs
and extracted repetition CSV files, writes a new baseline under
REHAB_SCORING_ASSETS_ROOT/derived/drink_water_v1/baseline_v2_1_1, and never modifies the
original 22 CSV files, legacy bootstrap artifacts, formal templates, service
routes, or online scoring behavior.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from cloud_interface.cloud_score_api import score_trial_full_json
from cloud_interface.raw_log_to_trial import build_trial_from_raw_log
from tools import drink_water_v1_pipeline as pipeline


BASELINE_VERSION = "drink_water_v2.1.1"
OUTPUT_DIR = pipeline.DERIVED_ROOT / "baseline_v2_1_1"
SOURCE_PER_FILE_DIR = pipeline.SEGMENTATION_DIR / "per_file"
SOURCE_REP_DIR = pipeline.EXTRACTED_REPS_DIR
SCORING_TEMPLATE_PATH = OUTPUT_DIR / "drink_water_baseline_v2_1_1_scoring_template.json"
BASELINE_PATH = OUTPUT_DIR / "drink_water_baseline_v2_1_1.json"
SOURCE_MANIFEST_PATH = OUTPUT_DIR / "baseline_source_manifest.json"
VALIDATION_PATH = OUTPUT_DIR / "session_score_validation_v2_1_1.json"
REPORT_PATH = OUTPUT_DIR / "V2_1_1_baseline_rebuild_report.md"

NORMAL_REFERENCE_LABELS = {
    "normal_single",
    "normal_three_continuous",
    "normal_five_continuous",
}

SESSION_TYPE_BY_LABEL = {
    "normal_single": "normal",
    "normal_three_continuous": "normal",
    "normal_five_continuous": "normal",
    "forward_compensation_single": "compensation",
    "normal_compensation_normal": "mixed",
}

TEMPLATE_FEATURES = {
    "speed": ("WristMeanSpeed_mm_s", "speed_mean", "speed_std"),
    "num_speed_peaks": ("NumSpeedPeaks", "num_speed_peaks_mean", "num_speed_peaks_std"),
    "pause_count": ("PauseCount", "pause_count_mean", "pause_count_std"),
    "forearm_rotation": ("ForearmRotationRange_deg", "forearm_rotation_mean", "forearm_rotation_std"),
    "forearm_angular_speed": (
        "ForearmMeanAngularSpeed_deg_s",
        "forearm_angular_speed_mean",
        "forearm_angular_speed_std",
    ),
    "ROM": ("UpperArmROM_mm", "ROM_mean", "ROM_std"),
    "trunk_compensation": (
        "TrunkCompensationRatio",
        "trunk_compensation_mean",
        "trunk_compensation_std",
    ),
    "Extensor_digitorum_RMS": (
        "Extensor_digitorum_RMS",
        "Extensor_digitorum_RMS_mean",
        "Extensor_digitorum_RMS_std",
    ),
    "Extensor_digitorum_MAV": (
        "Extensor_digitorum_MAV",
        "Extensor_digitorum_MAV_mean",
        "Extensor_digitorum_MAV_std",
    ),
    "Extensor_digitorum_WL": (
        "Extensor_digitorum_WL_mean",
        "Extensor_digitorum_WL_mean",
        "Extensor_digitorum_WL_std",
    ),
    "Biceps_brachii_RMS": ("Biceps_brachii_RMS", "Biceps_brachii_RMS_mean", "Biceps_brachii_RMS_std"),
    "Biceps_brachii_MAV": ("Biceps_brachii_MAV", "Biceps_brachii_MAV_mean", "Biceps_brachii_MAV_std"),
    "Biceps_brachii_WL": (
        "Biceps_brachii_WL_mean",
        "Biceps_brachii_WL_mean",
        "Biceps_brachii_WL_std",
    ),
    "Triceps_brachii_RMS": ("Triceps_brachii_RMS", "Triceps_brachii_RMS_mean", "Triceps_brachii_RMS_std"),
    "Triceps_brachii_MAV": ("Triceps_brachii_MAV", "Triceps_brachii_MAV_mean", "Triceps_brachii_MAV_std"),
    "Triceps_brachii_WL": (
        "Triceps_brachii_WL_mean",
        "Triceps_brachii_WL_mean",
        "Triceps_brachii_WL_std",
    ),
    "Extensor_carpi_ulnaris_RMS": (
        "Extensor_carpi_ulnaris_RMS",
        "Extensor_carpi_ulnaris_RMS_mean",
        "Extensor_carpi_ulnaris_RMS_std",
    ),
    "Extensor_carpi_ulnaris_MAV": (
        "Extensor_carpi_ulnaris_MAV",
        "Extensor_carpi_ulnaris_MAV_mean",
        "Extensor_carpi_ulnaris_MAV_std",
    ),
    "Extensor_carpi_ulnaris_WL": (
        "Extensor_carpi_ulnaris_WL_mean",
        "Extensor_carpi_ulnaris_WL_mean",
        "Extensor_carpi_ulnaris_WL_std",
    ),
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def numeric_stats(values: list[float]) -> dict[str, Any]:
    arr = np.asarray([float(v) for v in values if v is not None and np.isfinite(v)], dtype=float)
    if len(arr) == 0:
        return {"count": 0, "mean": None, "std": None, "median": None, "min": None, "max": None}
    return {
        "count": int(len(arr)),
        "mean": round(float(np.mean(arr)), 9),
        "std": round(float(np.std(arr, ddof=1)) if len(arr) > 1 else 1.0, 9),
        "median": round(float(np.median(arr)), 9),
        "min": round(float(np.min(arr)), 9),
        "max": round(float(np.max(arr)), 9),
    }


def load_per_file_records() -> list[dict[str, Any]]:
    records = []
    for path in sorted(SOURCE_PER_FILE_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        source = payload["source"]
        stem = Path(source["filename"]).stem
        for rep in payload.get("repetitions", []):
            rep_index = int(rep["rep_index"])
            records.append(
                {
                    "per_file_json": path,
                    "rep_csv": SOURCE_REP_DIR / stem / f"rep_{rep_index:02d}.csv",
                    "rep_metadata": SOURCE_REP_DIR / stem / f"rep_{rep_index:02d}.metadata.json",
                    "source": source,
                    "expected_label": source.get("expected_label"),
                    "session_type": SESSION_TYPE_BY_LABEL.get(source.get("expected_label"), "unknown"),
                    "rep": rep,
                }
            )
    return records


def build_trial(record: dict[str, Any]) -> dict[str, Any]:
    rep = record["rep"]
    return build_trial_from_raw_log(
        record["rep_csv"],
        patient_id="drink_water_v2_1_1_offline",
        session_id=Path(record["source"]["filename"]).stem,
        task_number=12,
        trial_id=int(rep["rep_index"]),
    )


def split_sources(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    included = []
    excluded = []
    for record in records:
        item = {
            "source_csv": record["source"]["relative_path"],
            "source_filename": record["source"]["filename"],
            "expected_label": record["expected_label"],
            "rep_index": int(record["rep"]["rep_index"]),
            "rep_csv": str(record["rep_csv"].relative_to(pipeline.DERIVED_ROOT)),
            "duration_ms": record["rep"].get("duration_ms"),
            "quality_score": record["rep"].get("full_cycle_quality", {}).get("full_cycle_score"),
            "valid": bool(record["rep"].get("valid")),
        }
        if record["expected_label"] in NORMAL_REFERENCE_LABELS and item["valid"]:
            included.append(item)
        else:
            item["exclude_reason"] = (
                "compensation_or_mixed_label"
                if record["expected_label"] not in NORMAL_REFERENCE_LABELS
                else "invalid_repetition"
            )
            excluded.append(item)
    return included, excluded


def build_scoring_template(reference_trials: list[dict[str, Any]]) -> dict[str, Any]:
    task_template: dict[str, Any] = {}
    for _, source_field, mean_key, std_key in (
        (name, *mapping) for name, mapping in TEMPLATE_FEATURES.items()
    ):
        values = [trial.get(source_field) for trial in reference_trials]
        stats = numeric_stats(values)
        task_template[mean_key] = stats["mean"]
        task_template[std_key] = stats["std"] if stats["std"] not in (None, 0) else 1.0
    return {"12": task_template}


def baseline_profiles(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    profiles = []
    for record in records:
        df = pd.read_csv(record["rep_csv"], encoding=pipeline.CSV_ENCODING)
        rep = {
            "start_index": 0,
            "end_index": len(df) - 1,
            "start_time_ms": float(df["time_ms"].iloc[0]) if "time_ms" in df.columns and len(df) else None,
        }
        profiles.append(
            {
                "source_filename": record["source"]["filename"],
                "rep_index": int(record["rep"]["rep_index"]),
                **pipeline.baseline_profile(df, rep),
            }
        )
    return profiles


def trajectory_features(records: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_by_field: dict[str, list[list[float | None]]] = {field: [] for field in pipeline.BOOTSTRAP_FIELDS}
    for record in records:
        df = pd.read_csv(record["rep_csv"], encoding=pipeline.CSV_ENCODING)
        normalized = pipeline.normalize_frame(df, pipeline.BOOTSTRAP_FIELDS, pipeline.CONFIG.bootstrap_normalized_length)
        for field, values in normalized.items():
            normalized_by_field[field].append(values)

    median_template: dict[str, list[float | None]] = {}
    for field, samples in normalized_by_field.items():
        matrix = np.asarray(
            [[np.nan if value is None else float(value) for value in sample] for sample in samples],
            dtype=float,
        )
        if matrix.size == 0 or np.all(np.isnan(matrix)):
            median_template[field] = []
        else:
            median_template[field] = [
                None if np.isnan(value) else round(float(value), 6)
                for value in np.nanmedian(matrix, axis=0)
            ]
    return {
        "normalized_length": pipeline.CONFIG.bootstrap_normalized_length,
        "field_count": len(median_template),
        "median_trajectory": median_template,
    }


def quality_statistics(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "full_cycle_score": numeric_stats(
            [record["rep"].get("full_cycle_quality", {}).get("full_cycle_score") for record in records]
        ),
        "duration_ms": numeric_stats([record["rep"].get("duration_ms") for record in records]),
        "confidence": numeric_stats([record["rep"].get("confidence") for record in records]),
    }


def score_records(records: list[dict[str, Any]], template_path: Path) -> list[dict[str, Any]]:
    sessions = []
    for record in records:
        trial = build_trial(record)
        result = score_trial_full_json(trial, template_path=template_path)
        scores = result.get("scores", {})
        compensation = result.get("compensation", {}) or {}
        sessions.append(
            {
                "source": record["source"]["relative_path"],
                "filename": record["source"]["filename"],
                "type": record["session_type"],
                "expected_label": record["expected_label"],
                "rep_index": int(record["rep"]["rep_index"]),
                "duration_ms": record["rep"].get("duration_ms"),
                "score": {
                    "valid": result.get("scores") is not None,
                    "final_score": scores.get("final_score"),
                    "total_score": scores.get("total_score"),
                },
                "subscores": {
                    "speed_score": scores.get("speed_score"),
                    "ROM_score": scores.get("ROM_score"),
                    "smoothness_score": scores.get("smoothness_score"),
                    "coordination_score": scores.get("coordination_score"),
                    "muscle_score": scores.get("muscle_score"),
                },
                "compensation": {
                    "detected": bool(
                        compensation.get("detected", compensation.get("has_compensation", False))
                    ),
                    "raw": compensation,
                },
                "fatigue": result.get("fatigue"),
            }
        )
    return sessions


def aggregate_sessions(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for session_type in ("normal", "compensation", "mixed"):
        rows = [item for item in sessions if item["type"] == session_type]
        scores = [item["score"]["final_score"] for item in rows if item["score"].get("final_score") is not None]
        result[session_type] = {
            "count": len(rows),
            "final_score": numeric_stats(scores),
            "compensation_detected_count": sum(1 for item in rows if item["compensation"]["detected"]),
        }
    return result


def decision_from_validation(aggregates: dict[str, Any]) -> str:
    normal_mean = aggregates.get("normal", {}).get("final_score", {}).get("mean")
    compensation_mean = aggregates.get("compensation", {}).get("final_score", {}).get("mean")
    comp_detected = aggregates.get("compensation", {}).get("compensation_detected_count", 0)
    if normal_mean is None or compensation_mean is None:
        return "NEED_MORE_CALIBRATION"
    if normal_mean >= 70 and (comp_detected > 0 or compensation_mean <= normal_mean - 8):
        return "READY_FOR_BASELINE_SWITCH"
    return "NEED_MORE_CALIBRATION"


def write_report(
    manifest: dict[str, Any],
    validation: dict[str, Any],
    decision: str,
) -> None:
    aggregates = validation["aggregates"]
    normal = aggregates["normal"]
    compensation = aggregates["compensation"]
    mixed = aggregates["mixed"]
    lines = [
        "# V2.1.1 Baseline Rebuild Report",
        "",
        "## 1. 数据来源",
        "",
        f"- 40 rep总数: {manifest['total_source_count']}",
        f"- 29 normal reference reps: {manifest['used_count']}",
        f"- excluded reps: {len(manifest['excluded_reps'])}",
        "",
        "## 2. baseline变化",
        "",
        "- 旧baseline来源: template/MHH_healthy_template.json",
        "- 旧baseline版本: legacy healthy-template JSON",
        f"- 新baseline: {BASELINE_VERSION}",
        f"- 新baseline目录: {OUTPUT_DIR}",
        "- 正式模板未覆盖，线上baseline未切换。",
        "",
        "## 3. Session评分结果",
        "",
        "| 类别 | 数量 | 平均分 | 代偿检测 |",
        "| --- | ---: | ---: | --- |",
        f"| 正常 | {normal['count']} | {normal['final_score']['mean']} | {normal['compensation_detected_count']} |",
        f"| 前倾代偿 | {compensation['count']} | {compensation['final_score']['mean']} | {compensation['compensation_detected_count']} |",
        f"| 混合 | {mixed['count']} | {mixed['final_score']['mean']} | {mixed['compensation_detected_count']} |",
        "",
        "## 4. 建议",
        "",
        decision,
        "",
        "## 5. 说明",
        "",
        "- 本报告由离线函数生成，未启动HTTP服务。",
        "- 冻结接口路径和返回合同未修改。",
        "- 原始CSV、旧bootstrap、正式模板和线上评分行为未修改。",
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    records = load_per_file_records()
    if len(records) != 40:
        raise RuntimeError(f"expected_40_repetitions_found_{len(records)}")

    included_manifest, excluded_manifest = split_sources(records)
    included_keys = {
        (item["source_filename"], int(item["rep_index"]))
        for item in included_manifest
    }
    included_records = [
        record
        for record in records
        if (record["source"]["filename"], int(record["rep"]["rep_index"])) in included_keys
    ]
    excluded_records = [
        record
        for record in records
        if (record["source"]["filename"], int(record["rep"]["rep_index"])) not in included_keys
    ]

    if len(included_records) != 29:
        raise RuntimeError(f"expected_29_reference_reps_found_{len(included_records)}")
    if len(excluded_records) != 11:
        raise RuntimeError(f"expected_11_excluded_reps_found_{len(excluded_records)}")

    reference_trials = [build_trial(record) for record in included_records]
    scoring_template = build_scoring_template(reference_trials)
    write_json(SCORING_TEMPLATE_PATH, scoring_template)

    duration_values = [record["rep"].get("duration_ms") for record in included_records]
    baseline_payload = {
        "baseline_version": BASELINE_VERSION,
        "generated_at": utc_now_iso(),
        "source": {
            "segmentation_version": "v2.1.1",
            "source_rep_count": len(included_records),
            "source": "derived/drink_water_v1/extracted_repetitions",
        },
        "duration_statistics": numeric_stats(duration_values),
        "imu_baseline": {
            "segment_mapping": pipeline.DRINK_WATER_V1_IMU_SEGMENTS,
            "baseline_profiles": baseline_profiles(included_records),
        },
        "emg_baseline": {
            "muscle_mapping": {
                "EMG1": "Extensor_digitorum",
                "EMG2": "Biceps_brachii",
                "EMG3": "Triceps_brachii",
                "EMG4": "Extensor_carpi_ulnaris",
            },
            "template_features": {
                key: value
                for key, value in scoring_template["12"].items()
                if any(marker in key for marker in ("RMS", "MAV", "WL"))
            },
        },
        "trajectory_features": trajectory_features(included_records),
        "quality_statistics": quality_statistics(included_records),
        "reference_reps": included_manifest,
        "excluded_rep_count": len(excluded_records),
        "formal_template_replacement": False,
        "online_scoring_behavior_changed": False,
    }
    write_json(BASELINE_PATH, baseline_payload)

    source_manifest = {
        "baseline_version": BASELINE_VERSION,
        "generated_at": utc_now_iso(),
        "included_reps": included_manifest,
        "excluded_reps": excluded_manifest,
        "total_source_count": len(records),
        "used_count": len(included_records),
    }
    write_json(SOURCE_MANIFEST_PATH, source_manifest)

    sessions = score_records(records, SCORING_TEMPLATE_PATH)
    validation = {
        "baseline_version": BASELINE_VERSION,
        "generated_at": utc_now_iso(),
        "baseline_path": str(BASELINE_PATH),
        "scoring_template_path": str(SCORING_TEMPLATE_PATH),
        "sessions": sessions,
        "aggregates": aggregate_sessions(sessions),
    }
    validation["decision"] = decision_from_validation(validation["aggregates"])
    write_json(VALIDATION_PATH, validation)
    write_report(source_manifest, validation, validation["decision"])

    print(json.dumps({
        "baseline_version": BASELINE_VERSION,
        "output_dir": str(OUTPUT_DIR),
        "source_rep_count": len(records),
        "used_count": len(included_records),
        "excluded_count": len(excluded_records),
        "decision": validation["decision"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
