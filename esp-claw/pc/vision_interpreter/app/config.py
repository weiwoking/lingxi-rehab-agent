import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "vision_config.json"
TARGET_CLASS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def _normalize_person_follow(config: dict[str, Any]) -> None:
    raw = config.get("person_follow")
    if raw is None:
        # Old configuration files remain readable. The new route stays unavailable
        # until it is explicitly enabled by a person_follow section.
        raw = {"enabled": False}
        config["person_follow"] = raw
    if not isinstance(raw, dict):
        raise ValueError("person_follow must be a JSON object")

    defaults = {
        "enabled": False,
        "logical_model_slot": "person_follow",
        "model_id": "coco-yolo11n",
        "model_path": config["model"]["path"],
        "target_class": "person",
        "confidence_threshold": 0.35,
        "image_size": 640,
        "device": "cpu",
        "half": False,
        "max_image_bytes": 131072,
        "warmup": True,
    }
    for key, value in defaults.items():
        raw.setdefault(key, value)

    for key in ("enabled", "half", "warmup"):
        if type(raw[key]) is not bool:
            raise ValueError(f"person_follow.{key} must be a boolean")
    for key in ("logical_model_slot", "model_id", "device"):
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise ValueError(f"person_follow.{key} must be a non-empty string")
    target_class = raw["target_class"]
    if not isinstance(target_class, str) or not TARGET_CLASS_PATTERN.fullmatch(target_class):
        raise ValueError("person_follow.target_class contains unsupported characters")
    threshold = raw["confidence_threshold"]
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0.0 <= float(threshold) <= 1.0:
        raise ValueError("person_follow.confidence_threshold must be in [0, 1]")
    for key in ("image_size", "max_image_bytes"):
        if type(raw[key]) is not int or raw[key] <= 0:
            raise ValueError(f"person_follow.{key} must be a positive integer")
    if raw["device"].strip().lower() == "cpu" and raw["half"]:
        raise ValueError("person_follow.half must be false for CPU")
    if not isinstance(raw["model_path"], str) or not raw["model_path"].strip():
        raise ValueError("person_follow.model_path must be a non-empty string")

    raw["logical_model_slot"] = raw["logical_model_slot"].strip()
    raw["model_id"] = raw["model_id"].strip()
    raw["device"] = raw["device"].strip()
    raw["model_path"] = str(_project_path(raw["model_path"]))
    raw["confidence_threshold"] = float(raw["confidence_threshold"])


@lru_cache(maxsize=1)
def get_config() -> dict[str, Any]:
    path = _project_path(os.getenv("VISION_CONFIG_PATH", str(DEFAULT_CONFIG)))
    with path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    override = os.getenv("VISION_MODEL_PATH", "").strip()
    model_path = override or str(config["model"]["path"])
    config["model"]["path"] = str(_project_path(model_path))

    identity = config.get("identity")
    if isinstance(identity, dict) and identity.get("model_path"):
        identity["model_path"] = str(_project_path(str(identity["model_path"])))
    _normalize_person_follow(config)
    return config
