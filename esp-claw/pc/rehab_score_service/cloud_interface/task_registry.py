from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


SERVICE_ROOT = Path(__file__).resolve().parents[1]
TASK_REGISTRY_PATH = SERVICE_ROOT / "config" / "rehab_task_registry_v1.json"
TASK_REGISTRY_VERSION = "rehab_task_registry_v1"


def load_task_registry(path: Path = TASK_REGISTRY_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("task_registry_not_object")
    if payload.get("registry_version") != TASK_REGISTRY_VERSION:
        raise ValueError("task_registry_version_mismatch")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("task_registry_tasks_not_list")
    return payload


def resolve_task(task_name: Any, path: Path = TASK_REGISTRY_PATH) -> dict[str, Any]:
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


def require_resolved_task(task_name: Any, path: Path = TASK_REGISTRY_PATH) -> dict[str, Any]:
    resolved = resolve_task(task_name, path)
    if resolved.get("task_registry_status") != "resolved":
        raise ValueError(f"UNKNOWN_TASK:{resolved.get('original_task_name')}")
    return resolved
