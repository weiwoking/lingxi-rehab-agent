from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


DOCTOR_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PC_ROOT = DOCTOR_PROJECT_ROOT.parent
DEFAULT_SCORE_SERVICE_ROOT = DEFAULT_PC_ROOT / "rehab_score_service"
SCORE_SERVICE_ROOT = Path(
    os.getenv("REHAB_SCORE_ROOT")
    or os.getenv("SCORE_SERVICE_ROOT")
    or DEFAULT_SCORE_SERVICE_ROOT
).resolve()
TASK_REGISTRY_PATH = Path(
    os.getenv("REHAB_TASK_REGISTRY_PATH")
    or SCORE_SERVICE_ROOT / "config" / "rehab_task_registry_v1.json"
).resolve()
TASK_REGISTRY_VERSION = "rehab_task_registry_v1"
LEGACY_TASK_HEURISTIC_ROLE = "COMPATIBILITY_FALLBACK_ONLY"


def load_task_registry(path: Path = TASK_REGISTRY_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("task_registry_not_object")
    if payload.get("registry_version") != TASK_REGISTRY_VERSION:
        raise ValueError("task_registry_version_mismatch")
    if not isinstance(payload.get("tasks"), list):
        raise ValueError("task_registry_tasks_not_list")
    return payload


def resolve_task_name(task_name: Any, path: Path = TASK_REGISTRY_PATH) -> dict[str, Any]:
    raw_name = str(task_name or "").strip()
    registry = load_task_registry(path)
    for item in registry["tasks"]:
        if not isinstance(item, dict):
            continue
        names = [item.get("task_name"), *(item.get("aliases") or [])]
        if raw_name in {str(name) for name in names if name}:
            resolved = deepcopy(item)
            resolved["registry_version"] = registry["registry_version"]
            resolved["task_registry_status"] = "resolved"
            resolved["original_task_name"] = raw_name
            resolved["task_level"] = resolved.get("level")
            return resolved
    return {
        "registry_version": registry["registry_version"],
        "task_registry_status": "unknown",
        "original_task_name": raw_name,
        "unsupported_task_reason": "UNKNOWN_TASK",
        "motion_family": None,
        "object_type": None,
        "score_profile": None,
        "twin_profile": None,
        "task_level": None,
    }


def resolved_task_fields(task_name: Any) -> dict[str, Any]:
    resolved = resolve_task_name(task_name)
    if resolved.get("task_registry_status") != "resolved":
        return {
            "original_task_name": str(task_name or "").strip(),
            "task_registry_version": resolved.get("registry_version"),
            "task_registry_status": "unknown",
            "unsupported_task_reason": "UNKNOWN_TASK",
            "motion_family": None,
            "object_type": None,
            "score_profile": None,
            "twin_profile": None,
            "task_level": None,
        }
    return {
        "original_task_name": resolved["original_task_name"],
        "task_name": resolved["display_name"],
        "task": resolved["display_name"],
        "task_registry_version": resolved["registry_version"],
        "task_registry_status": "resolved",
        "motion_family": resolved["motion_family"],
        "object_type": resolved["object_type"],
        "score_profile": resolved["score_profile"],
        "twin_profile": resolved["twin_profile"],
        "task_level": resolved["level"],
    }


def legacy_task_fields(session: dict[str, Any]) -> dict[str, Any]:
    if session.get("task_registry_status") in {"resolved", "unknown"}:
        return {}
    if session.get("motion_family") and session.get("object_type"):
        return {}
    text = " ".join(
        str(session.get(key) or "")
        for key in ("session_id", "scenario_id", "task", "task_name", "internal_score_task_name")
    ).lower()
    if "lateral" in text or "volleyball" in text or "横向" in text or "排球" in text or "取放" in text:
        return {
            "motion_family": "lateral_transfer",
            "score_profile": "lateral_transfer",
            "twin_profile": "lateral_transfer",
            "object_type": "ball" if "球" in text or "volleyball" in text else "cup",
            "task_registry_status": session.get("task_registry_status") or "legacy_fallback",
        }
    if "水" in text or "杯" in text or "drink" in text:
        return {
            "motion_family": "drink_water",
            "score_profile": "drink_water",
            "twin_profile": "drink_water",
            "object_type": "cup",
            "task_registry_status": session.get("task_registry_status") or "legacy_fallback",
        }
    return {}
