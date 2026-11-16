"""Bridge persistent P4 score jobs to the formal dedicated session scorers."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Iterable

from cloud_interface.compact_score_result import project_score_actions
from cloud_interface.task_registry import load_task_registry, require_resolved_task
from tools.drink_water_session_scoring_v1 import orchestrate_session as orchestrate_drink_water_session
from tools.lateral_transfer_session_scoring_v1 import orchestrate_session as orchestrate_lateral_transfer_session
from scoring_assets import TEMPLATE_ROOT


SERVICE_ROOT = Path(__file__).resolve().parents[1]
DRINK_BASELINE_TEMPLATE_PATH = TEMPLATE_ROOT / "MHH_healthy_template.json"
LATERAL_TEMPLATE_PATH = TEMPLATE_ROOT / "lateral_transfer_healthy_template_v1.json"
SUPPORTED_SCORE_PROFILES = {"drink_water", "lateral_transfer"}
QUALITY_REQUIRED_COLUMNS = (
    "log_seq",
    "time_ms",
    "imu1_valid",
    "imu2_valid",
    "imu3_valid",
    "imu4_valid",
    "emg_valid",
)
QUALITY_VALID_FLAG_COLUMNS = (
    "imu1_valid",
    "imu2_valid",
    "imu3_valid",
    "imu4_valid",
    "emg_valid",
)
QUALITY_WARNING_LABELS = {
    "LF": "\u8eaf\u5e72\u524d\u503e",
    "TREMOR": "\u624b\u90e8\u6296\u52a8",
}


def process_dedicated_job_directory(
    task_directory: str,
    output_dir: str | None = None,
    patient_id: str = "S001",
    session_id: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Run the registry-selected dedicated scorer and write P4-compatible full result."""
    task_dir = Path(task_directory)
    result_dir = Path(output_dir) if output_dir else task_dir / "result.tmp"
    task_payload = _load_task_payload(task_dir)
    task_name = _require_text(task_payload.get("task_name"), "task_json_missing_task_name")
    csv_path = task_dir / _require_text(task_payload.get("csv_file"), "task_json_missing_csv_file")
    if not csv_path.is_file():
        raise FileNotFoundError(f"task_csv_missing:{csv_path}")

    resolved = require_resolved_task(task_name)
    score_profile = _require_text(resolved.get("score_profile"), "resolved_task_missing_score_profile")
    if score_profile not in SUPPORTED_SCORE_PROFILES:
        raise ValueError(f"unsupported_score_profile:{score_profile}")
    task_number = _registry_task_number(resolved)
    if task_number <= 0:
        raise ValueError("registry_task_number_unresolved")

    result_dir.mkdir(parents=True, exist_ok=True)
    session_id = session_id or task_dir.name
    dedicated_root = result_dir / "dedicated_scoring"
    dedicated_session_dir = _run_dedicated_scorer(
        score_profile=score_profile,
        csv_path=csv_path,
        dedicated_root=dedicated_root,
        session_id=session_id,
    )
    full_result = _build_full_result(
        session_id=session_id,
        task_name=task_name,
        task_number=task_number,
        score_profile=score_profile,
        patient_id=patient_id,
        csv_path=csv_path,
        dedicated_session_dir=dedicated_session_dir,
        resolved_task=resolved,
    )
    _validate_full_result_contract(full_result)
    (result_dir / "score_result.json").write_text(
        json.dumps(full_result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return full_result


def _load_task_payload(task_dir: Path) -> dict[str, Any]:
    task_path = task_dir / "task.json"
    try:
        payload = json.loads(task_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"task_json_missing:{task_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"task_json_invalid:{task_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("task_json_not_object")
    return payload


def _require_text(value: Any, reason: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(reason)
    return text


def _registry_task_number(resolved: dict[str, Any]) -> int:
    registry = load_task_registry()
    canonical = str(resolved.get("task_name") or "")
    for index, item in enumerate(registry["tasks"], start=1):
        if isinstance(item, dict) and str(item.get("task_name") or "") == canonical:
            return index
    return 0


def _run_dedicated_scorer(
    *,
    score_profile: str,
    csv_path: Path,
    dedicated_root: Path,
    session_id: str,
) -> Path:
    if score_profile == "drink_water":
        if not DRINK_BASELINE_TEMPLATE_PATH.is_file():
            raise FileNotFoundError(f"drink_baseline_template_missing:{DRINK_BASELINE_TEMPLATE_PATH}")
        orchestrate_drink_water_session(
            source_session_path=csv_path,
            baseline_template=DRINK_BASELINE_TEMPLATE_PATH,
            output_root=dedicated_root,
            session_id=session_id,
            overwrite=True,
        )
    elif score_profile == "lateral_transfer":
        if not LATERAL_TEMPLATE_PATH.is_file():
            raise FileNotFoundError(f"lateral_template_missing:{LATERAL_TEMPLATE_PATH}")
        orchestrate_lateral_transfer_session(
            source_session=csv_path,
            output_root=dedicated_root,
            template_path=LATERAL_TEMPLATE_PATH,
            session_id=session_id,
            overwrite=True,
        )
    else:
        raise ValueError(f"unsupported_score_profile:{score_profile}")
    dedicated_session_dir = dedicated_root / session_id
    if not (dedicated_session_dir / "session_summary.json").is_file():
        raise ValueError("dedicated_scorer_missing_session_summary")
    if not (dedicated_session_dir / "per_rep_scores").is_dir():
        raise ValueError("dedicated_scorer_missing_per_rep_scores")
    return dedicated_session_dir


def _build_full_result(
    *,
    session_id: str,
    task_name: str,
    task_number: int,
    score_profile: str,
    patient_id: str,
    csv_path: Path,
    dedicated_session_dir: Path,
    resolved_task: dict[str, Any],
) -> dict[str, Any]:
    summary = _read_json_object(dedicated_session_dir / "session_summary.json")
    rep_scores = _read_rep_scores(dedicated_session_dir / "per_rep_scores")
    valid_rep_scores = [
        score
        for score in rep_scores
        if score.get("valid") is True and _finite_number(score.get("final_score")) is not None
    ]
    score_values = [_finite_number(score.get("final_score")) for score in valid_rep_scores]
    score_values = [value for value in score_values if value is not None]
    repetition_count = len(score_values)
    average_score = _round_score(sum(score_values) / repetition_count) if repetition_count else None
    max_score = _round_score(max(score_values)) if repetition_count else None
    min_score = _round_score(min(score_values)) if repetition_count else None
    consistency_score = _consistency_score(score_values)
    compensation_action_count = _compensation_action_count(valid_rep_scores)
    quality = _protocol_quality(csv_path)
    warnings = _dedup_strings([*quality["warnings"], *_compensation_warnings(valid_rep_scores)])
    quality["warnings"] = warnings
    valid = repetition_count >= 1
    score_version = _score_version(summary, rep_scores, score_profile)
    model_version = _model_version(summary, score_version, score_profile)
    actions = _compact_actions_from_rep_scores(
        rep_scores,
        repetition_count=repetition_count,
        average_score=average_score,
        compensation_action_count=compensation_action_count,
    )
    return {
        "schema_version": "p4_dedicated_score_full_result_v1",
        "session_id": session_id,
        "patient_id": patient_id,
        "task_name": task_name,
        "task_number": task_number,
        "task_registry": {
            "registry_version": resolved_task.get("registry_version"),
            "task_registry_status": resolved_task.get("task_registry_status"),
            "score_profile": score_profile,
            "task_number_rule": "registry_1_based_index",
        },
        "score_semantic_source": "DEDICATED_ONLY",
        "valid": valid,
        "invalid_reason": None if valid else _invalid_reason(summary, rep_scores),
        "repetition_count": repetition_count,
        "target_repetition_count": max(1, repetition_count),
        "summary": {
            "average_score": average_score,
            "max_score": max_score,
            "min_score": min_score,
            "consistency_score": consistency_score,
            "compensation_action_count": compensation_action_count,
            "valid_repetition_count": repetition_count,
            "score_source": "dedicated_per_rep_scores",
        },
        "actions": actions,
        "data_quality": quality,
        "score_version": score_version,
        "model_version": model_version,
        "dedicated_scoring": {
            "score_profile": score_profile,
            "session_summary_path": str(dedicated_session_dir / "session_summary.json"),
            "per_rep_scores_dir": str(dedicated_session_dir / "per_rep_scores"),
        },
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"json_missing:{path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"json_invalid:{path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"json_not_object:{path}")
    return payload


def _read_rep_scores(score_dir: Path) -> list[dict[str, Any]]:
    scores: list[dict[str, Any]] = []
    for path in sorted(score_dir.glob("rep_*_score.json")):
        payload = _read_json_object(path)
        payload["_score_path"] = str(path)
        scores.append(payload)
    return scores


def _compact_actions_from_rep_scores(
    rep_scores: list[dict[str, Any]],
    *,
    repetition_count: int,
    average_score: Any,
    compensation_action_count: int,
) -> list[dict[str, Any]]:
    raw_actions: list[dict[str, Any]] = []
    for index, score in enumerate(rep_scores, start=1):
        raw_actions.append(
            {
                "index": index,
                "valid": bool(score.get("valid", False)),
                "invalid_reason": score.get("invalid_reason"),
                "final_score": score.get("final_score"),
                "final_score_available": _finite_number(score.get("final_score")) is not None,
                "subscores": score.get("subscores", {}),
                "compensation": score.get("compensation", {}),
                "feedback": score.get("feedback", []),
            }
        )
    return project_score_actions(
        raw_actions,
        repetition_count=repetition_count,
        average_score=average_score,
        compensation_action_count=compensation_action_count,
    )


def attach_compact_actions_from_dedicated_result(
    full_result: dict[str, Any],
    result_dir: Path,
) -> dict[str, Any]:
    """Attach compact action facts for persisted dedicated results created before actions[] existed."""
    if not isinstance(full_result, dict) or "actions" in full_result:
        return full_result
    if full_result.get("schema_version") != "p4_dedicated_score_full_result_v1":
        return full_result
    summary = full_result.get("summary")
    if not isinstance(summary, dict):
        return full_result
    session_id = str(full_result.get("session_id") or "").strip()
    if not session_id:
        return full_result
    score_dir = result_dir / "dedicated_scoring" / session_id / "per_rep_scores"
    if not score_dir.is_dir():
        return full_result
    rep_scores = _read_rep_scores(score_dir)
    repetition_count = int(full_result.get("repetition_count") or 0)
    compensation_action_count = int(summary.get("compensation_action_count") or 0)
    enriched = dict(full_result)
    enriched["actions"] = _compact_actions_from_rep_scores(
        rep_scores,
        repetition_count=repetition_count,
        average_score=summary.get("average_score"),
        compensation_action_count=compensation_action_count,
    )
    return enriched


def _finite_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _round_score(value: float) -> float:
    return round(float(value), 2)


def _consistency_score(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return 100.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return round(max(0.0, min(100.0, 100.0 - math.sqrt(variance))), 2)


def _score_version(summary: dict[str, Any], rep_scores: list[dict[str, Any]], score_profile: str) -> str:
    for score in rep_scores:
        value = score.get("score_version")
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("score_version", "session_version"):
        value = summary.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return score_profile


def _model_version(summary: dict[str, Any], score_version: str, score_profile: str) -> str:
    for key in ("session_version", "baseline_version", "template_version", "segmentation_version"):
        value = summary.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return score_version or score_profile


def _compensation_payload(score: dict[str, Any]) -> dict[str, Any]:
    compensation = score.get("compensation")
    return compensation if isinstance(compensation, dict) else {}


def _compensation_types(score: dict[str, Any]) -> list[str]:
    compensation = _compensation_payload(score)
    raw = compensation.get("types")
    if raw is None:
        raw = compensation.get("compensation_types")
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _compensation_action_count(rep_scores: list[dict[str, Any]]) -> int:
    count = 0
    for score in rep_scores:
        compensation = _compensation_payload(score)
        if compensation.get("detected") is True or _compensation_types(score):
            count += 1
    return count


def _compensation_warnings(rep_scores: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for score in rep_scores:
        for code in _compensation_types(score):
            label = QUALITY_WARNING_LABELS.get(code)
            if label:
                warnings.append(label)
    return warnings


def _dedup_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def _invalid_reason(summary: dict[str, Any], rep_scores: list[dict[str, Any]]) -> str:
    for value in _walk_reason_sources(summary):
        if value:
            return value
    for score in rep_scores:
        for key in ("invalid_reason", "reason", "error"):
            value = score.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "no_valid_repetition"


def _walk_reason_sources(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key in ("invalid_reason", "reason", "error", "fallback_rejected_reason"):
            reason = value.get(key)
            if isinstance(reason, str) and reason.strip():
                yield reason.strip()
        for nested in value.values():
            yield from _walk_reason_sources(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_reason_sources(item)


def _protocol_quality(csv_path: Path) -> dict[str, Any]:
    warnings: list[str] = []
    valid_flag_counts = {
        field: {"valid_samples": 0, "invalid_samples": 0}
        for field in QUALITY_VALID_FLAG_COLUMNS
    }
    all_sensor_valid_samples = 0
    valid_flag_row_count = 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        missing = [field for field in QUALITY_REQUIRED_COLUMNS if field not in header]
        if missing:
            warnings.append("required columns missing: " + ",".join(missing))
        valid_flag_fields_present = all(field in header for field in QUALITY_VALID_FLAG_COLUMNS)
        time_values: list[float] = []
        log_values: list[int] = []
        for row in reader:
            time_value = _finite_number(row.get("time_ms"))
            if time_value is not None:
                time_values.append(time_value)
            log_value = _finite_number(row.get("log_seq"))
            if log_value is not None:
                log_values.append(int(log_value))
            row_flags: list[bool] = []
            for field in QUALITY_VALID_FLAG_COLUMNS:
                if field not in header:
                    continue
                flag_value = _parse_valid_flag(row.get(field))
                if flag_value is True:
                    valid_flag_counts[field]["valid_samples"] += 1
                else:
                    valid_flag_counts[field]["invalid_samples"] += 1
                row_flags.append(flag_value is True)
            if valid_flag_fields_present:
                valid_flag_row_count += 1
                if row_flags and all(row_flags):
                    all_sensor_valid_samples += 1
    time_diffs = [right - left for left, right in zip(time_values, time_values[1:])]
    positive_diffs = [diff for diff in time_diffs if diff > 0]
    if positive_diffs:
        median_dt = sorted(positive_diffs)[len(positive_diffs) // 2]
        estimated_rate_hz = 1000.0 / median_dt if median_dt > 0 else None
        if estimated_rate_hz is not None and estimated_rate_hz < 40.0:
            warnings.append("estimated sampling rate is below 40 Hz")
    else:
        estimated_rate_hz = None
    if any(diff > 100.0 for diff in time_diffs):
        warnings.append("large sampling gaps over 100 ms detected")
    log_diffs = [right - left for left, right in zip(log_values, log_values[1:])]
    if any(diff > 1 for diff in log_diffs):
        warnings.append("log sequence gaps detected")
    valid_flags = _valid_flag_stats(valid_flag_counts)
    for field, stats in valid_flags.items():
        if stats["invalid_samples"] > 0:
            warnings.append(f"{field} contains invalid samples")
    return {
        "level": "good" if not warnings else "use_with_caution",
        "warnings": warnings,
        "checks": {
            "required_columns": list(QUALITY_REQUIRED_COLUMNS),
            "estimated_sampling_rate_hz": None if estimated_rate_hz is None else round(estimated_rate_hz, 2),
            "sampling_gap_threshold_ms": 100.0,
            "log_seq_gap_checked": True,
            "valid_flags": valid_flags,
            "all_sensor_valid_ratio": (
                None
                if valid_flag_row_count == 0
                else round(all_sensor_valid_samples / valid_flag_row_count, 6)
            ),
        },
    }


def _parse_valid_flag(value: Any) -> bool | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in {"1", "1.0"} or text == "true" or text == "True" or text == "TRUE":
        return True
    if text in {"0", "0.0"} or text == "false" or text == "False" or text == "FALSE":
        return False
    return None


def _valid_flag_stats(counts: dict[str, dict[str, int]]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for field in QUALITY_VALID_FLAG_COLUMNS:
        valid_samples = int(counts.get(field, {}).get("valid_samples", 0))
        invalid_samples = int(counts.get(field, {}).get("invalid_samples", 0))
        total = valid_samples + invalid_samples
        stats[field] = {
            "valid_samples": valid_samples,
            "invalid_samples": invalid_samples,
            "valid_ratio": None if total == 0 else round(valid_samples / total, 6),
        }
    return stats


def _validate_full_result_contract(result: dict[str, Any]) -> None:
    task_number = int(result.get("task_number") or 0)
    repetition_count = int(result.get("repetition_count") or 0)
    target_repetition_count = int(result.get("target_repetition_count") or 0)
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    compensation_count = int(summary.get("compensation_action_count") or 0)
    if not isinstance(result.get("actions"), list):
        raise ValueError("p4_contract_actions_missing")
    project_score_actions(
        result["actions"],
        repetition_count=repetition_count,
        average_score=summary.get("average_score"),
        compensation_action_count=compensation_count,
    )
    if task_number <= 0:
        raise ValueError("p4_contract_task_number_not_positive")
    if target_repetition_count <= 0:
        raise ValueError("p4_contract_target_repetition_count_not_positive")
    if compensation_count > repetition_count:
        raise ValueError("p4_contract_compensation_count_exceeds_repetition_count")
    if result.get("valid") is True:
        if result.get("invalid_reason") is not None:
            raise ValueError("p4_contract_valid_result_has_invalid_reason")
        values = [
            summary.get("average_score"),
            summary.get("max_score"),
            summary.get("min_score"),
            summary.get("consistency_score"),
        ]
        if any(_finite_number(value) is None for value in values):
            raise ValueError("p4_contract_valid_result_missing_numeric_score")
        average = float(summary["average_score"])
        maximum = float(summary["max_score"])
        minimum = float(summary["min_score"])
        if not (minimum <= average <= maximum):
            raise ValueError("p4_contract_score_order_invalid")
    else:
        if not str(result.get("invalid_reason") or "").strip():
            raise ValueError("p4_contract_invalid_result_missing_reason")
