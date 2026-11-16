"""Offline Drink Water V2.1.2 session scoring orchestrator.

This module composes existing V2.1.1 segmentation and the existing single-action
score core without changing service routes, production templates, scoring
formulas, production baselines, or fatigue ownership.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from cloud_interface.cloud_score_api import score_trial_json
from cloud_interface.raw_log_to_trial import (
    build_trial_from_raw_log,
    read_fused_csv,
    validate_fused_scoring_input,
)
from tools import drink_water_v1_pipeline as pipeline


SESSION_VERSION = "drink_water_session_v1_4_reasonable_speed"
BASELINE_VERSION = "drink_water_v2.1.4_reasonable_speed"
SEGMENTATION_VERSION = "drink_hybrid_segmentation_v2_1"
SENSOR_MAPPING_VERSION = "rehab_sensor_mapping_20260806"
SOURCE_MODE = "offline_validation"
SOURCE_ORIGIN = "v2_1_2_session_orchestrator"
DEFAULT_OUTPUT_ROOT = pipeline.DERIVED_ROOT / "session_scoring_v2_1_2"
FROZEN_SCORE_FIELDS = [
    "score_version",
    "valid",
    "final_score",
    "subscores",
    "compensation",
    "fatigue",
    "brs",
    "feedback",
]
FROZEN_SUBSCORE_FIELDS = [
    "speed_score",
    "ROM_score",
    "smoothness_score",
    "coordination_score",
    "muscle_score",
]
WINDOWS_RENAME_RETRY_DELAYS_SECONDS = (0.10, 0.20, 0.40, 0.80, 1.00)


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
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rename_directory_with_retry(source: Path, target: Path) -> None:
    """Rename a directory, retrying only transient Windows access-denied errors."""

    try:
        source.rename(target)
        return
    except PermissionError as exc:
        if getattr(exc, "winerror", None) != 5:
            raise
        last_error = exc

    for delay in WINDOWS_RENAME_RETRY_DELAYS_SECONDS:
        time.sleep(delay)
        try:
            source.rename(target)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) != 5:
                raise
            last_error = exc
    raise last_error


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_baseline_template(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"baseline_template_missing:{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"baseline_template_invalid_json:{path}") from exc
    task_template = payload.get("12") if isinstance(payload, dict) else None
    if not isinstance(task_template, dict):
        raise ValueError("baseline_template_missing_task_12")
    expected_mapping = {
        "imu1": "upper_arm",
        "imu2": "trunk_lower_back",
        "imu3": "forearm",
        "imu4": "wrist",
        "emg1": "extensor_digitorum",
        "emg2": "biceps_brachii",
        "emg3": "triceps_brachii",
        "emg4": "extensor_carpi_ulnaris",
    }
    if task_template.get("baseline_version") != BASELINE_VERSION:
        raise ValueError("baseline_template_version_mismatch")
    if task_template.get("sensor_mapping_version") != SENSOR_MAPPING_VERSION:
        raise ValueError("baseline_template_sensor_mapping_version_mismatch")
    if task_template.get("sensor_mapping") != expected_mapping:
        raise ValueError("baseline_template_sensor_mapping_mismatch")
    required = [
        "speed_mean",
        "speed_std",
        "num_speed_peaks_mean",
        "num_speed_peaks_std",
        "pause_count_mean",
        "pause_count_std",
        "ROM_mean",
        "ROM_std",
        "forearm_rotation_mean",
        "forearm_rotation_std",
        "forearm_angular_speed_mean",
        "forearm_angular_speed_std",
        "Extensor_digitorum_RMS_mean",
        "Extensor_digitorum_RMS_std",
        "Extensor_digitorum_MAV_mean",
        "Extensor_digitorum_MAV_std",
        "Extensor_digitorum_WL_mean",
        "Extensor_digitorum_WL_std",
        "Biceps_brachii_RMS_mean",
        "Biceps_brachii_RMS_std",
        "Biceps_brachii_MAV_mean",
        "Biceps_brachii_MAV_std",
        "Biceps_brachii_WL_mean",
        "Biceps_brachii_WL_std",
        "Triceps_brachii_RMS_mean",
        "Triceps_brachii_RMS_std",
        "Triceps_brachii_MAV_mean",
        "Triceps_brachii_MAV_std",
        "Triceps_brachii_WL_mean",
        "Triceps_brachii_WL_std",
        "Extensor_carpi_ulnaris_RMS_mean",
        "Extensor_carpi_ulnaris_RMS_std",
        "Extensor_carpi_ulnaris_MAV_mean",
        "Extensor_carpi_ulnaris_MAV_std",
        "Extensor_carpi_ulnaris_WL_mean",
        "Extensor_carpi_ulnaris_WL_std",
    ]
    missing = [field for field in required if field not in task_template]
    if missing:
        raise ValueError("baseline_template_contract_incompatible:" + ",".join(missing))


def validate_frozen_score_result(result: dict[str, Any]) -> dict[str, Any]:
    """Validate the public single-action score contract without filling fields."""

    if not isinstance(result, dict):
        raise ValueError("score_result_not_object")
    fields = set(result.keys())
    expected_fields = set(FROZEN_SCORE_FIELDS)
    if fields != expected_fields:
        missing = sorted(expected_fields - fields)
        extra = sorted(fields - expected_fields)
        raise ValueError(f"score_result_contract_mismatch:missing={missing};extra={extra}")
    subscores = result.get("subscores")
    if not isinstance(subscores, dict):
        raise ValueError("score_result_subscores_not_object")
    subscore_fields = set(subscores.keys())
    expected_subscores = set(FROZEN_SUBSCORE_FIELDS)
    if subscore_fields != expected_subscores:
        missing = sorted(expected_subscores - subscore_fields)
        extra = sorted(subscore_fields - expected_subscores)
        raise ValueError(f"score_result_subscores_contract_mismatch:missing={missing};extra={extra}")
    return {field: result[field] for field in FROZEN_SCORE_FIELDS}


def validate_score_core_result(result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(result, dict):
        raise ValueError("score_core_result_not_object")
    if result.get("valid") is True:
        return "valid_score", validate_frozen_score_result(result)
    if result.get("valid") is False:
        required = {"score_version", "valid", "invalid_reason"}
        missing = sorted(required - set(result.keys()))
        if missing:
            raise ValueError("score_core_invalid_contract_missing:" + ",".join(missing))
        return "invalid_score", {
            "score_version": result["score_version"],
            "valid": False,
            "invalid_reason": result["invalid_reason"],
        }
    raise ValueError("score_core_result_missing_boolean_valid")


def validate_session_id(session_id: str) -> str:
    if not isinstance(session_id, str):
        raise ValueError("session_id_not_string")
    candidate = session_id.strip()
    if not candidate:
        raise ValueError("session_id_empty")
    if candidate in {".", ".."}:
        raise ValueError("session_id_reserved")
    if any(ord(ch) < 32 for ch in candidate):
        raise ValueError("session_id_contains_control_character")
    if "/" in candidate or "\\" in candidate:
        raise ValueError("session_id_contains_path_separator")
    if ":" in candidate:
        raise ValueError("session_id_contains_drive_or_scheme_separator")
    if Path(candidate).is_absolute():
        raise ValueError("session_id_absolute_path")
    if Path(os.path.normpath(candidate)).name != candidate:
        raise ValueError("session_id_not_single_directory_name")
    return candidate


def ensure_child_path(root: Path, child_name: str) -> Path:
    root_resolved = root.resolve()
    child = (root / child_name).resolve()
    if child.parent != root_resolved:
        raise ValueError("session_output_path_escapes_output_root")
    return child


def compensation_detected(compensation: dict[str, Any]) -> bool:
    if not isinstance(compensation, dict):
        return False
    return bool(compensation.get("detected", compensation.get("has_compensation", False)))


def compensation_types(compensation: dict[str, Any]) -> list[str]:
    if not isinstance(compensation, dict):
        return []
    raw = compensation.get("types", compensation.get("compensation_types", []))
    return [str(item) for item in raw] if isinstance(raw, list) else []


def score_values(score_files: list[Path]) -> list[dict[str, Any]]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in score_files]


def summarize_scores(
    session_id: str,
    source_session_path: Path,
    repetitions: list[dict[str, Any]],
    rejected_repetitions: list[dict[str, Any]],
    scoring_rejections: list[dict[str, Any]],
    score_files: list[Path],
    source_sha256: str,
    source_row_count: int,
    source_column_count: int,
    baseline_template_path: Path,
    baseline_template_sha256: str,
    session_started_at_ms: Any,
    session_ended_at_ms: Any,
    segmented_candidate_count: int,
    segmentation_valid_repetition_count: int,
) -> dict[str, Any]:
    scores = score_values(score_files)
    valid_scores = [score for score in scores if score.get("valid") and score.get("final_score") is not None]
    final_values = [float(score["final_score"]) for score in valid_scores]
    subscore_averages: dict[str, float | None] = {}
    for field in FROZEN_SUBSCORE_FIELDS:
        values = [
            float(score["subscores"][field])
            for score in valid_scores
            if isinstance(score.get("subscores"), dict) and score["subscores"].get(field) is not None
        ]
        subscore_averages[field] = round(float(np.mean(values)), 6) if values else None
    comp_items = [score.get("compensation", {}) for score in scores]
    comp_hits = [item for item in comp_items if compensation_detected(item)]
    comp_type_set = sorted({item for comp in comp_hits for item in compensation_types(comp)})
    started = repetitions[0].get("start_time_ms") if repetitions else None
    ended = repetitions[-1].get("end_time_ms") if repetitions else None
    return {
        "session_version": SESSION_VERSION,
        "session_id": session_id,
        "action_type": "drink_water",
        "baseline_version": BASELINE_VERSION,
        "segmentation_version": SEGMENTATION_VERSION,
        "sensor_mapping_version": SENSOR_MAPPING_VERSION,
        "source_mode": SOURCE_MODE,
        "source_origin": SOURCE_ORIGIN,
        "source_session_path": str(source_session_path),
        "source_sha256": source_sha256,
        "source_row_count": source_row_count,
        "source_column_count": source_column_count,
        "baseline_template_path": str(baseline_template_path),
        "baseline_template_sha256": baseline_template_sha256,
        "session_started_at_ms": session_started_at_ms,
        "session_ended_at_ms": session_ended_at_ms,
        "session_duration_ms": round(float(session_ended_at_ms) - float(session_started_at_ms), 3)
        if session_started_at_ms is not None and session_ended_at_ms is not None
        else None,
        "activity_started_at_ms": started,
        "activity_ended_at_ms": ended,
        "activity_duration_ms": round(float(ended) - float(started), 3) if started is not None and ended is not None else None,
        "segmented_candidate_count": segmented_candidate_count,
        "segmentation_valid_repetition_count": segmentation_valid_repetition_count,
        "rejected_repetition_count": len(rejected_repetitions),
        "scored_repetition_count": len(scores) + len(scoring_rejections),
        "score_valid_repetition_count": len(valid_scores),
        "score_invalid_repetition_count": len(scoring_rejections),
        "average_score": round(float(np.mean(final_values)), 6) if final_values else None,
        "median_score": round(float(median(final_values)), 6) if final_values else None,
        "best_score": round(float(max(final_values)), 6) if final_values else None,
        "worst_score": round(float(min(final_values)), 6) if final_values else None,
        "score_trend": [
            {"rep_index": rep["rep_index"], "final_score": score.get("final_score"), "valid": score.get("valid")}
            for rep, score in zip(repetitions, scores)
        ],
        "subscore_averages": subscore_averages,
        "compensation_summary": {
            "detected_repetition_count": len(comp_hits),
            "types": comp_type_set,
            "max_severity": None,
        },
        "repetitions": repetitions,
        "rejected_repetitions": rejected_repetitions,
        "scoring_rejections": scoring_rejections,
    }


def write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Drink Water Session Score Report",
        "",
        f"- session_id: {summary['session_id']}",
        f"- baseline_version: {summary['baseline_version']}",
        f"- segmentation_version: {summary['segmentation_version']}",
        f"- sensor_mapping_version: {summary['sensor_mapping_version']}",
        f"- segmented_candidate_count: {summary['segmented_candidate_count']}",
        f"- segmentation_valid_repetition_count: {summary['segmentation_valid_repetition_count']}",
        f"- rejected_repetition_count: {summary['rejected_repetition_count']}",
        f"- scored_repetition_count: {summary['scored_repetition_count']}",
        f"- score_valid_repetition_count: {summary['score_valid_repetition_count']}",
        f"- score_invalid_repetition_count: {summary['score_invalid_repetition_count']}",
        f"- average_score: {summary['average_score']}",
        f"- compensation_detector_detected: {summary['compensation_summary']['detected_repetition_count']}",
        "",
        "| rep | valid | final_score | compensation_detected |",
        "| ---: | --- | ---: | --- |",
    ]
    for rep in summary["repetitions"]:
        lines.append(
            f"| {rep['rep_index']} | {rep['valid']} | {rep.get('final_score')} | "
            f"{rep.get('compensation_detected')} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rep_metadata_base(session_id: str, source_session_path: Path, rep_index: int, rep: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "rep_index": rep_index,
        "source_session_path": str(source_session_path),
        "source_mode": SOURCE_MODE,
        "source_origin": SOURCE_ORIGIN,
        "segmentation_version": SEGMENTATION_VERSION,
        "start_index": rep.get("start_index"),
        "end_index": rep.get("end_index"),
        "start_time_ms": rep.get("start_time_ms"),
        "end_time_ms": rep.get("end_time_ms"),
        "duration_ms": rep.get("duration_ms"),
        "start_boundary": rep.get("start_boundary"),
        "end_boundary": rep.get("end_boundary"),
    }


def orchestrate_session(
    source_session_path: Path,
    baseline_template: Path,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    session_id: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    validate_baseline_template(baseline_template)
    baseline_template_sha256 = sha256_file(baseline_template)
    source_sha256 = sha256_file(source_session_path)
    session_id = validate_session_id(session_id or source_session_path.stem)
    source_df_for_contract = read_fused_csv(source_session_path)
    validate_fused_scoring_input(source_df_for_contract, source_label=str(source_session_path))
    output_root.mkdir(parents=True, exist_ok=True)
    session_dir = ensure_child_path(output_root, session_id)
    if session_dir.exists() and not overwrite:
        raise FileExistsError(f"session_output_exists:{session_dir}")
    tmp_dir = ensure_child_path(output_root, f"{session_id}.tmp_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}")
    if tmp_dir.exists():
        raise FileExistsError(f"temporary_session_output_exists:{tmp_dir}")

    try:
        repetitions_dir = tmp_dir / "repetitions"
        rejected_dir = tmp_dir / "rejected_repetitions"
        scoring_rejection_dir = tmp_dir / "scoring_rejections"
        score_dir = tmp_dir / "per_rep_scores"
        repetitions_dir.mkdir(parents=True, exist_ok=False)
        rejected_dir.mkdir(parents=True, exist_ok=False)
        scoring_rejection_dir.mkdir(parents=True, exist_ok=False)
        score_dir.mkdir(parents=True, exist_ok=False)

        reps, diagnostic, df = pipeline.segment_file(source_session_path)
        reps = sorted(reps, key=lambda item: (item.get("start_time_ms") is None, item.get("start_time_ms", 0), item["rep_index"]))
        session_started_at_ms = df.iloc[0]["time_ms"] if len(df) > 0 and "time_ms" in df.columns else None
        session_ended_at_ms = df.iloc[-1]["time_ms"] if len(df) > 0 and "time_ms" in df.columns else None

        score_files: list[Path] = []
        summary_reps: list[dict[str, Any]] = []
        rejected_reps: list[dict[str, Any]] = []
        scoring_rejections: list[dict[str, Any]] = []
        valid_output_index = 0
        rejected_output_index = 0
        scoring_rejection_index = 0
        for source_index, rep in enumerate(reps, start=1):
            rep_df = df.iloc[int(rep["start_index"]) : int(rep["end_index"]) + 1].copy()
            if rep.get("valid") is not True:
                rejected_output_index += 1
                rejected_name = f"rejected_{rejected_output_index:02d}"
                rejected_csv = rejected_dir / f"{rejected_name}.csv"
                rejected_metadata = rejected_dir / f"{rejected_name}.metadata.json"
                rep_df.to_csv(rejected_csv, index=False, encoding=pipeline.CSV_ENCODING)
                metadata = _rep_metadata_base(session_id, source_session_path, source_index, rep)
                metadata.update(
                    {
                        "invalid_reason": rep.get("invalid_reason"),
                        "merged_candidate": rep.get("merged_candidate"),
                        "incomplete_candidate": rep.get("incomplete_candidate"),
                        "rejected_csv_file": rejected_csv.name,
                    }
                )
                write_json(rejected_metadata, metadata)
                rejected_reps.append(
                    {
                        **metadata,
                        "rejected_csv": str(rejected_csv.relative_to(tmp_dir)),
                        "rejected_metadata_file": str(rejected_metadata.relative_to(tmp_dir)),
                    }
                )
                continue

            valid_output_index += 1
            rep_name = f"rep_{valid_output_index:02d}"
            rep_csv = repetitions_dir / f"{rep_name}.csv"
            rep_metadata = repetitions_dir / f"{rep_name}.metadata.json"
            validate_fused_scoring_input(
                rep_df,
                source_label=f"{source_session_path}#rep_{valid_output_index:02d}",
            )
            rep_df.to_csv(rep_csv, index=False, encoding=pipeline.CSV_ENCODING)

            trial_json = build_trial_from_raw_log(
                rep_csv,
                patient_id="drink_water_session_offline",
                session_id=session_id,
                task_number=12,
                trial_id=valid_output_index,
            )
            score_status, score_payload = validate_score_core_result(score_trial_json(trial_json, template_path=baseline_template))
            if score_status == "invalid_score":
                scoring_rejection_index += 1
                invalid_score_file = scoring_rejection_dir / f"{rep_name}_score_invalid.json"
                invalid_metadata_file = scoring_rejection_dir / f"{rep_name}_score_invalid_metadata.json"
                write_json(invalid_score_file, score_payload)
                invalid_metadata = _rep_metadata_base(session_id, source_session_path, valid_output_index, rep)
                invalid_metadata.update(
                    {
                        "baseline_version": BASELINE_VERSION,
                        "baseline_template_path": str(baseline_template),
                        "baseline_template_sha256": baseline_template_sha256,
                        "sensor_mapping_version": SENSOR_MAPPING_VERSION,
                        "rep_csv_file": rep_csv.name,
                        "score_invalid_file": invalid_score_file.name,
                        "score_invalid_metadata_file": invalid_metadata_file.name,
                        "invalid_reason": score_payload["invalid_reason"],
                    }
                )
                write_json(invalid_metadata_file, invalid_metadata)
                scoring_rejections.append(
                    {
                        **invalid_metadata,
                        "score_invalid_file": str(invalid_score_file.relative_to(tmp_dir)),
                        "score_invalid_metadata_file": str(invalid_metadata_file.relative_to(tmp_dir)),
                    }
                )
                write_json(
                    rep_metadata,
                    {
                        "session_id": session_id,
                        "rep_index": valid_output_index,
                        "source_session_path": str(source_session_path),
                        "rep": rep,
                        "source_mode": SOURCE_MODE,
                        "source_origin": SOURCE_ORIGIN,
                        "segmentation_version": SEGMENTATION_VERSION,
                        "score_status": "invalid_score",
                    },
                )
                continue
            frozen_score = score_payload

            score_file = score_dir / f"{rep_name}_score.json"
            score_metadata_file = score_dir / f"{rep_name}_score_metadata.json"
            write_json(score_file, frozen_score)
            score_metadata = _rep_metadata_base(session_id, source_session_path, valid_output_index, rep)
            score_metadata.update(
                {
                    "baseline_version": BASELINE_VERSION,
                    "baseline_template_path": str(baseline_template),
                    "baseline_template_sha256": baseline_template_sha256,
                    "sensor_mapping_version": SENSOR_MAPPING_VERSION,
                    "rep_csv_file": rep_csv.name,
                    "score_file": score_file.name,
                }
            )
            write_json(score_metadata_file, score_metadata)
            write_json(
                rep_metadata,
                {
                    "session_id": session_id,
                    "rep_index": valid_output_index,
                    "source_session_path": str(source_session_path),
                    "rep": rep,
                    "source_mode": SOURCE_MODE,
                    "source_origin": SOURCE_ORIGIN,
                    "segmentation_version": SEGMENTATION_VERSION,
                },
            )
            score_files.append(score_file)
            summary_reps.append(
                {
                    "rep_index": valid_output_index,
                    "valid": frozen_score["valid"],
                    "final_score": frozen_score["final_score"],
                    "score_file": str(score_file.relative_to(tmp_dir)),
                    "score_metadata_file": str(score_metadata_file.relative_to(tmp_dir)),
                    "rep_csv": str(rep_csv.relative_to(tmp_dir)),
                    "start_index": rep["start_index"],
                    "end_index": rep["end_index"],
                    "start_time_ms": rep["start_time_ms"],
                    "end_time_ms": rep["end_time_ms"],
                    "duration_ms": rep["duration_ms"],
                    "compensation_detected": compensation_detected(frozen_score["compensation"]),
                }
            )

        segmentation_valid_count = sum(1 for rep in reps if rep.get("valid") is True)
        session_manifest = {
            "session_id": session_id,
            "generated_at": utc_now_iso(),
            "source_session_path": str(source_session_path),
            "source_sha256": source_sha256,
            "source_row_count": int(len(df)),
            "source_column_count": int(len(df.columns)),
            "source_mode": SOURCE_MODE,
            "source_origin": SOURCE_ORIGIN,
            "baseline_version": BASELINE_VERSION,
            "baseline_template_path": str(baseline_template),
            "baseline_template_sha256": baseline_template_sha256,
            "segmentation_version": SEGMENTATION_VERSION,
            "sensor_mapping_version": SENSOR_MAPPING_VERSION,
            "segmentation_method": diagnostic.get("method"),
            "segmented_candidate_count": len(reps),
            "segmentation_valid_repetition_count": segmentation_valid_count,
            "rejected_repetition_count": len(rejected_reps),
            "scoring_rejection_count": len(scoring_rejections),
            "segmentation_diagnostic": diagnostic,
        }
        write_json(tmp_dir / "session_manifest.json", session_manifest)
        summary = summarize_scores(
            session_id=session_id,
            source_session_path=source_session_path,
            repetitions=summary_reps,
            rejected_repetitions=rejected_reps,
            scoring_rejections=scoring_rejections,
            score_files=score_files,
            source_sha256=source_sha256,
            source_row_count=int(len(df)),
            source_column_count=int(len(df.columns)),
            baseline_template_path=baseline_template,
            baseline_template_sha256=baseline_template_sha256,
            session_started_at_ms=session_started_at_ms,
            session_ended_at_ms=session_ended_at_ms,
            segmented_candidate_count=len(reps),
            segmentation_valid_repetition_count=segmentation_valid_count,
        )
        write_json(tmp_dir / "session_summary.json", summary)
        write_report(tmp_dir / "session_score_report.md", summary)
        if session_dir.exists():
            rollback_dir = ensure_child_path(output_root, f"{session_id}.rollback_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}")
            rename_directory_with_retry(session_dir, rollback_dir)
            try:
                rename_directory_with_retry(tmp_dir, session_dir)
            except Exception:
                if not session_dir.exists() and rollback_dir.exists():
                    rename_directory_with_retry(rollback_dir, session_dir)
                raise
            shutil.rmtree(rollback_dir)
        else:
            rename_directory_with_retry(tmp_dir, session_dir)
        return summary
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline Drink Water V2.1.2 session scoring orchestrator.")
    parser.add_argument("--source-session", required=True, type=Path, help="Complete 86-column session CSV path.")
    parser.add_argument("--baseline-template", required=True, type=Path, help="Explicit V2.1.2 scoring template JSON path.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT, help="Derived output root.")
    parser.add_argument("--session-id", default=None, help="Optional output session id; defaults to source CSV stem.")
    parser.add_argument("--overwrite", action="store_true", help="Replace only the selected output session directory.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary = orchestrate_session(
        source_session_path=args.source_session,
        baseline_template=args.baseline_template,
        output_root=args.output_root,
        session_id=args.session_id,
        overwrite=args.overwrite,
    )
    print(json.dumps({"session_id": summary["session_id"], "score_valid_repetition_count": summary["score_valid_repetition_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
