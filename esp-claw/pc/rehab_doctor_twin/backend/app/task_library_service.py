from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .config import DATA_DIR, PATIENT_ID
from .task_registry_adapter import load_task_registry


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _motion_label(value: str) -> str:
    if value == "drink_water":
        return "水杯拿取与饮水训练"
    if value == "lateral_transfer":
        return "横向取放训练"
    return value or "未知动作"


def _object_label(value: str) -> str:
    if value == "cup":
        return "水杯"
    if value == "ball":
        return "球体"
    return value or "未知道具"


def _supported_tasks_from_registry() -> dict[str, dict[str, Any]]:
    registry = load_task_registry()
    registry_version = registry.get("registry_version")
    tasks: dict[str, dict[str, Any]] = {}
    for index, task in enumerate(registry.get("tasks", []), start=1):
        if not isinstance(task, dict):
            continue
        task_name = str(task.get("task_name") or "")
        if not task_name:
            continue
        motion_family = str(task.get("motion_family") or "")
        object_type = str(task.get("object_type") or "")
        tasks[task_name] = {
            "task_id": task_name,
            "task_name": task_name,
            "display_name": str(task.get("display_name") or task_name),
            "registry_version": registry_version,
            "motion_family": motion_family,
            "object_type": object_type,
            "score_profile": task.get("score_profile"),
            "twin_profile": task.get("twin_profile"),
            "level": task.get("level"),
            "ui_enabled": True,
            "formal_score_supported": True,
            "showcase_only": False,
            "internal_score_task_name": task_name,
            "score_mode": task.get("score_profile"),
            "description": f"{_motion_label(motion_family)}，道具：{_object_label(object_type)}。",
            "sort_order": index,
        }
    return tasks


SUPPORTED_TASKS: dict[str, dict[str, Any]] = _supported_tasks_from_registry()


def _training_eligible(task: dict[str, Any], enabled: bool) -> bool:
    return (
        enabled
        and task.get("formal_score_supported") is True
        and task.get("showcase_only") is not True
    )


class TaskLibraryService:
    def __init__(self) -> None:
        self._path = DATA_DIR / "patients" / PATIENT_ID / "task_library.json"

    def list_tasks(self) -> list[dict[str, Any]]:
        return deepcopy(self._load()["tasks"])

    def active_task(self) -> dict[str, Any] | None:
        active = [
            task
            for task in self._load()["tasks"]
            if task.get("enabled") is True
            and task.get("active") is True
            and task.get("formal_score_supported") is True
            and task.get("showcase_only") is not True
        ]
        return deepcopy(active[0]) if len(active) == 1 else None

    def add(self, task_id: str) -> dict[str, Any]:
        if task_id not in SUPPORTED_TASKS:
            raise ValueError("Unsupported task_id.")
        library = self._load()
        if any(item["task_id"] == task_id for item in library["tasks"]):
            raise ValueError("Task already exists in the patient library.")

        now = _now()
        registry_task = SUPPORTED_TASKS[task_id]
        enabled = registry_task["ui_enabled"] is True
        task = {
            **registry_task,
            "enabled": enabled,
            "training_eligible": _training_eligible(registry_task, enabled),
            "active": not any(item.get("active") for item in library["tasks"]),
            "sort_order": registry_task["sort_order"],
            "created_at": now,
            "updated_at": now,
        }
        library["tasks"].append(task)
        self._save(library)
        return deepcopy(task)

    def update(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        library = self._load()
        task = next(
            (item for item in library["tasks"] if item["task_id"] == task_id),
            None,
        )
        if task is None:
            raise KeyError(task_id)
        allowed = {"enabled", "sort_order", "active"}
        if set(payload) - allowed:
            raise ValueError("Only enabled, active, and sort_order can be changed.")
        if "enabled" in payload:
            task["enabled"] = bool(payload["enabled"])
            task["training_eligible"] = _training_eligible(
                task, bool(payload["enabled"])
            )
            if not task["enabled"]:
                task["active"] = False
        if "active" in payload:
            should_activate = bool(payload["active"])
            if should_activate:
                if not _training_eligible(task, bool(task.get("enabled"))):
                    raise ValueError("Task is not training eligible.")
                for other in library["tasks"]:
                    other["active"] = False
                task["active"] = True
            else:
                task["active"] = False
        elif task.get("enabled") and task.get("formal_score_supported") is True and not any(
            item is not task and item.get("active") for item in library["tasks"]
        ):
            task["active"] = True
        if "sort_order" in payload:
            value = int(payload["sort_order"])
            if value < 1:
                raise ValueError("sort_order must be positive.")
            task["sort_order"] = value
        task["updated_at"] = _now()
        library["tasks"].sort(key=lambda item: (item["sort_order"], item["task_id"]))
        self._save(library)
        return deepcopy(task)

    def remove(self, task_id: str) -> None:
        library = self._load()
        remaining = [
            item for item in library["tasks"] if item["task_id"] != task_id
        ]
        if len(remaining) == len(library["tasks"]):
            raise KeyError(task_id)
        library["tasks"] = remaining
        self._save(library)

    def supported_registry(self) -> list[dict[str, Any]]:
        return deepcopy(
            sorted(SUPPORTED_TASKS.values(), key=lambda item: item["sort_order"])
        )

    def _load(self) -> dict[str, Any]:
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            value = self._default()
            self._save(value)
        tasks = value.get("tasks") if isinstance(value, dict) else None
        if not isinstance(tasks, list):
            value = self._default()
        value["tasks"] = self._normalize_tasks(value["tasks"])
        return value

    def _normalize_tasks(self, tasks: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        now = _now()
        for item in tasks:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id") or item.get("task_name") or "")
            registry_task = SUPPORTED_TASKS.get(task_id)
            if registry_task is None or task_id in seen:
                continue
            enabled = item.get("enabled", registry_task["ui_enabled"]) is True
            training_eligible = _training_eligible(registry_task, enabled)
            active = (
                enabled
                and item.get("active", not any(existing.get("active") for existing in normalized)) is True
                and not any(existing.get("active") for existing in normalized)
            )
            normalized.append(
                {
                    **registry_task,
                    "enabled": enabled,
                    "training_eligible": training_eligible,
                    "active": active,
                    "sort_order": int(item.get("sort_order") or registry_task["sort_order"]),
                    "created_at": item.get("created_at") or now,
                    "updated_at": item.get("updated_at") or now,
                }
            )
            seen.add(task_id)
        for task_id, registry_task in SUPPORTED_TASKS.items():
            if task_id in seen:
                continue
            enabled = registry_task["ui_enabled"] is True
            normalized.append(
                {
                    **registry_task,
                    "enabled": enabled,
                    "training_eligible": _training_eligible(registry_task, enabled),
                    "active": enabled and not any(existing.get("active") for existing in normalized),
                    "sort_order": registry_task["sort_order"],
                    "created_at": now,
                    "updated_at": now,
                }
            )
        normalized.sort(key=lambda item: (item["sort_order"], item["task_id"]))
        return normalized

    def _default(self) -> dict[str, Any]:
        now = _now()
        tasks = [
            {
                **task,
                "enabled": task["ui_enabled"] is True,
                "training_eligible": _training_eligible(
                    task, task["ui_enabled"] is True
                ),
                "active": index == 0,
                "sort_order": task["sort_order"],
                "created_at": now,
                "updated_at": now,
            }
            for index, task in enumerate(
                sorted(SUPPORTED_TASKS.values(), key=lambda item: item["sort_order"])
            )
        ]
        return {
            "schema_version": "rehab_task_library_v1",
            "patient_id": PATIENT_ID,
            "tasks": tasks,
        }

    def _save(self, value: dict[str, Any]) -> None:
        value = deepcopy(value)
        value["schema_version"] = "rehab_task_library_v1"
        value["patient_id"] = PATIENT_ID
        value["tasks"] = self._normalize_tasks(value.get("tasks", []))
        value["updated_at"] = _now()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self._path)


task_library_service = TaskLibraryService()
