"""Build the small, device-facing result from the full PC scoring result."""

import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, List


COMPACT_RESULT_MAX_BYTES = 64 * 1024
REHAB_SCORE_MAX_ACTIONS = 64
P4_RESULT_FIELDS = (
    "schema_version",
    "session_id",
    "task_name",
    "task_number",
    "valid",
    "invalid_reason",
    "average_score",
    "max_score",
    "min_score",
    "repetition_count",
    "target_repetition_count",
    "consistency_score",
    "data_quality_level",
    "warnings",
    "compensation_detected",
    "compensation_action_count",
    "score_version",
    "model_version",
    "completed_at",
    "pc_job_id",
)
DEVICE_SCORE_VERSION_ALIASES = {
    "lateral_transfer_session_scoring_v1_1_reasonable_speed":
        "lateral_transfer_v1_1_reasonable_speed",
}
COMPACT_ACTION_FORBIDDEN_KEYS = {
    "trajectory",
    "trajectories",
    "curve_data",
    "comparisons",
}
COMPACT_SUBSCORE_FIELDS = (
    "speed_score",
    "ROM_score",
    "smoothness_score",
    "coordination_score",
    "muscle_score",
)
REQUIRED_FULL_RESULT_FIELDS = {
    "session_id",
    "task_name",
    "task_number",
    "valid",
    "invalid_reason",
    "repetition_count",
    "target_repetition_count",
    "summary",
    "data_quality",
    "score_version",
    "model_version",
}


def _completed_at(value: Any = None) -> str:
    if isinstance(value, str) and value:
        return value
    return datetime.now(timezone.utc).isoformat()


def _finite_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _round_score(value: float) -> float:
    return round(float(value), 2)


def _utf8_len(value: str) -> int:
    return len(value.encode("utf-8"))


def _nonempty_p4_string(value: Any, max_bytes: int, field_name: str) -> str:
    if value is None:
        raise ValueError(f"compact {field_name} must be non-empty UTF-8 bytes < {max_bytes}")
    text = str(value)
    if not text or _utf8_len(text) >= max_bytes:
        raise ValueError(f"compact {field_name} must be non-empty UTF-8 bytes < {max_bytes}")
    return text


def _device_score_version(value: Any) -> str:
    text = str(value)
    alias = DEVICE_SCORE_VERSION_ALIASES.get(text)
    if alias is not None:
        return _nonempty_p4_string(alias, 48, "score_version")
    return _nonempty_p4_string(text, 48, "score_version")


def _forbid_heavy_action_content(value: Any, path: str = "actions") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if key_lower in COMPACT_ACTION_FORBIDDEN_KEYS or "base64" in key_lower:
                raise ValueError(f"compact action contains forbidden field: {path}.{key_text}")
            _forbid_heavy_action_content(item, f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _forbid_heavy_action_content(item, f"{path}[{index}]")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"compact action contains non-finite value: {path}")


def _string_list(value: Any, *, max_items: int = 16, max_chars: int = 512) -> List[str]:
    if value is None:
        return []
    raw = value if isinstance(value, list) else [value]
    result: List[str] = []
    for item in raw:
        text = str(item).strip()
        if text:
            result.append(text[:max_chars])
        if len(result) >= max_items:
            break
    return result


def _project_subscores(value: Any) -> tuple[Dict[str, Any], Dict[str, bool]]:
    source = value if isinstance(value, dict) else {}
    subscores: Dict[str, Any] = {}
    availability: Dict[str, bool] = {}
    for field in COMPACT_SUBSCORE_FIELDS:
        parsed = _finite_number(source.get(field))
        available = parsed is not None
        subscores[field] = _round_score(parsed) if available else None
        availability[field] = available
    return subscores, availability


def _project_compensation(value: Any) -> Dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    raw_types = source.get("types")
    if raw_types is None:
        raw_types = source.get("compensation_types")
    types = _string_list(raw_types, max_items=8, max_chars=64)
    detected = bool(source.get("detected", source.get("has_compensation", False))) or bool(types)
    return {
        "detected": detected,
        "types": types,
    }


def _project_action(raw_action: Any, position: int) -> Dict[str, Any]:
    if not isinstance(raw_action, dict):
        raise ValueError("compact action must be a JSON object")
    _forbid_heavy_action_content(raw_action)
    try:
        action_index = int(raw_action.get("index", raw_action.get("rep_index", position)))
    except (TypeError, ValueError) as exc:
        raise ValueError("compact action index must be an integer") from exc
    if action_index <= 0:
        raise ValueError("compact action index must be positive")

    valid = bool(raw_action.get("valid", False))
    final_score = _finite_number(raw_action.get("final_score"))
    final_score_available = bool(raw_action.get("final_score_available", final_score is not None))
    if valid and (not final_score_available or final_score is None):
        raise ValueError("valid compact action must have a finite final_score")
    if not final_score_available:
        final_score = None

    subscores, availability = _project_subscores(raw_action.get("subscores"))
    return {
        "index": action_index,
        "valid": valid,
        "invalid_reason": raw_action.get("invalid_reason"),
        "final_score": _round_score(final_score) if final_score is not None else None,
        "final_score_available": final_score is not None,
        "subscores": subscores,
        "subscore_availability": availability,
        "compensation": _project_compensation(raw_action.get("compensation")),
        "feedback": _string_list(raw_action.get("feedback")),
    }


def project_score_actions(
    raw_actions: Any,
    *,
    repetition_count: int,
    average_score: Any,
    compensation_action_count: int,
) -> List[Dict[str, Any]]:
    if not isinstance(raw_actions, list):
        raise ValueError("full score result actions must be an array")
    if len(raw_actions) > REHAB_SCORE_MAX_ACTIONS:
        raise ValueError("full score result actions exceeds maximum supported count")
    if len(raw_actions) != repetition_count:
        raise ValueError("full score result action count does not match repetition_count")

    actions = [_project_action(action, index) for index, action in enumerate(raw_actions, start=1)]
    indexes = [action["index"] for action in actions]
    if len(indexes) != len(set(indexes)):
        raise ValueError("full score result action indexes must be unique")

    valid_scores = [
        float(action["final_score"])
        for action in actions
        if action["valid"] is True and action["final_score_available"] is True
    ]
    if len(valid_scores) != repetition_count:
        raise ValueError("full score result valid action score count does not match repetition_count")
    expected_average = _finite_number(average_score)
    if valid_scores and expected_average is not None:
        action_average = _round_score(sum(valid_scores) / len(valid_scores))
        if action_average != _round_score(expected_average):
            raise ValueError("full score result action average does not match summary average_score")

    detected_count = sum(1 for action in actions if action["compensation"]["detected"])
    if detected_count != compensation_action_count:
        raise ValueError("full score result action compensation count does not match summary")
    return actions


def build_compact_score_result(
    full_result: Dict[str, Any],
    job_id: str,
    completed_at: Any = None,
) -> Dict[str, Any]:
    """Validate and project a full result without copying heavy arrays/artifacts."""
    if not isinstance(full_result, dict):
        raise ValueError("full score result must be a JSON object")
    missing = sorted(REQUIRED_FULL_RESULT_FIELDS - set(full_result))
    if missing:
        raise ValueError(f"full score result is missing required fields: {missing}")

    summary = full_result["summary"]
    quality = full_result["data_quality"]
    if not isinstance(summary, dict) or not isinstance(quality, dict):
        raise ValueError("full score result summary/data_quality must be objects")
    warnings = quality.get("warnings", []) or []
    if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
        raise ValueError("full score result data_quality.warnings must be a string array")
    compensation_count = int(summary.get("compensation_action_count", 0) or 0)
    repetition_count = int(full_result["repetition_count"])
    compact = {
        "schema_version": "rehab_score_result_v1",
        "session_id": str(full_result["session_id"]),
        "task_name": str(full_result["task_name"]),
        "task_number": int(full_result["task_number"]),
        "valid": bool(full_result["valid"]),
        "invalid_reason": full_result["invalid_reason"],
        "average_score": summary.get("average_score"),
        "max_score": summary.get("max_score"),
        "min_score": summary.get("min_score"),
        "repetition_count": repetition_count,
        "target_repetition_count": int(full_result["target_repetition_count"]),
        "consistency_score": summary.get("consistency_score"),
        "data_quality_level": quality.get("level", "unknown"),
        "warnings": list(warnings),
        "compensation_detected": compensation_count > 0,
        "compensation_action_count": compensation_count,
        "score_version": _device_score_version(full_result["score_version"]),
        "model_version": _nonempty_p4_string(full_result["model_version"], 96, "model_version"),
        "completed_at": _completed_at(completed_at),
        "pc_job_id": job_id,
    }
    if tuple(compact.keys()) != P4_RESULT_FIELDS:
        raise ValueError("compact score result field set does not match current P4 contract")
    _nonempty_p4_string(compact["data_quality_level"], 48, "data_quality_level")
    encoded = json.dumps(
        compact, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(encoded) > COMPACT_RESULT_MAX_BYTES:
        raise ValueError("compact score result exceeds 64 KiB")
    return compact
