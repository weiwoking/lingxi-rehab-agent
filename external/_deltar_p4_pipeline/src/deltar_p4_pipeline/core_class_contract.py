from __future__ import annotations

from typing import Any


CORE_NOW = ("person", "chair", "dining table", "cup", "bottle", "book", "tv")
SUPPORTED_BUT_NOT_CORE = (
    "banana", "bowl", "mouse", "remote", "sink", "spoon", "sports ball", "toothbrush",
)
FUTURE_CUSTOM_CLASS = ("block", "comb", "demo_small_object", "sandbag", "target_zone", "towel")


def classify_class(class_name: str) -> str:
    if class_name in CORE_NOW:
        return "core_now"
    if class_name in SUPPORTED_BUT_NOT_CORE:
        return "supported_but_not_core"
    if class_name in FUTURE_CUSTOM_CLASS:
        return "future_custom_class"
    return "not_needed"


def core_contract() -> dict[str, Any]:
    roles = {
        "person": "user/environment context; not a manipulable object",
        "chair": "seating and environment context",
        "dining table": "stable work surface and placement context",
        "cup": "current reach/transfer/drinking-simulation task object",
        "bottle": "current reach/transfer task object",
        "book": "current page-turn and tabletop task object",
        "tv": "display/environment context",
    }
    return {
        "schema_version": "deltar_core_class_contract.v1",
        "source": {
            "knowledge_base": "C:/ec/rehab_decision_kb_v1",
            "files_read": ["README.md", "task_library.json", "scoring_task_library.json", "yolo_label_map.json", "EVIDENCE_SUMMARY.md"],
        },
        "core_now": [{"class_name": name, "role": roles[name]} for name in CORE_NOW],
        "supported_but_not_core": list(SUPPORTED_BUT_NOT_CORE),
        "future_custom_class": list(FUTURE_CUSTOM_CLASS),
        "not_needed": {
            "policy": "All other COCO classes are outside the frozen Stage 6 launch contract unless a task requirement is approved.",
            "examples": ["car", "airplane", "zebra", "surfboard"],
        },
        "rules": [
            "Do not train a model merely to increase class count.",
            "Use an existing COCO class directly when it is task-relevant.",
            "Future custom classes require an approved rehabilitation task and are not trained in Stage 5.2B.",
            "Person supplies user/environment context and is distinct from manipulable objects.",
        ],
        "training_performed": False,
    }
