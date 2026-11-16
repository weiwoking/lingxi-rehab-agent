from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


EXPECTED_BASELINE_VERSION = "drink_water_v2.1.2"
EXPECTED_SEGMENTATION_VERSION = "drink_hybrid_segmentation_v2_1"
EXPECTED_SENSOR_MAPPING_VERSION = "rehab_sensor_mapping_20260806"
TASK_ID = "12"


def utc_stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}_invalid_json:{path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label}_must_be_json_object:{path}")
    return payload


def validate_candidate_task12(candidate: dict[str, Any]) -> dict[str, Any]:
    if TASK_ID not in candidate:
        raise ValueError("candidate_missing_task_12")
    task = candidate[TASK_ID]
    if not isinstance(task, dict):
        raise ValueError("candidate_task_12_must_be_object")
    expected = {
        "baseline_version": EXPECTED_BASELINE_VERSION,
        "segmentation_version": EXPECTED_SEGMENTATION_VERSION,
        "sensor_mapping_version": EXPECTED_SENSOR_MAPPING_VERSION,
    }
    mismatches = {
        key: {"expected": expected_value, "actual": task.get(key)}
        for key, expected_value in expected.items()
        if task.get(key) != expected_value
    }
    if mismatches:
        raise ValueError(f"candidate_task_12_version_mismatch:{json.dumps(mismatches, ensure_ascii=False)}")
    return task


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def switch_task12_baseline(candidate_path: Path, formal_path: Path, rollback_dir: Path | None = None) -> dict[str, Any]:
    candidate_path = candidate_path.resolve()
    formal_path = formal_path.resolve()
    candidate_before_sha = sha256_file(candidate_path)
    formal_before_sha = sha256_file(formal_path)

    candidate = load_json_object(candidate_path, "candidate")
    formal = load_json_object(formal_path, "formal")
    candidate_task12 = validate_candidate_task12(candidate)
    if TASK_ID not in formal:
        raise ValueError("formal_missing_task_12")

    other_tasks_before = {key: value for key, value in formal.items() if key != TASK_ID}
    formal[TASK_ID] = candidate_task12

    tmp_path = formal_path.with_name(f"{formal_path.name}.tmp_{utc_stamp()}")
    rollback_path = formal_path.with_name(f"{formal_path.name}.rollback_{utc_stamp()}")
    copied_rollback_path: Path | None = None
    try:
        write_json(tmp_path, formal)
        tmp_payload = load_json_object(tmp_path, "tmp")
        if tmp_payload.get(TASK_ID) != candidate_task12:
            raise ValueError("tmp_task_12_not_equal_candidate")
        tmp_other_tasks = {key: value for key, value in tmp_payload.items() if key != TASK_ID}
        if tmp_other_tasks != other_tasks_before:
            raise ValueError("tmp_other_tasks_changed")

        formal_path.replace(rollback_path)
        try:
            tmp_path.replace(formal_path)
        except Exception:
            rollback_path.replace(formal_path)
            raise

        final_payload = load_json_object(formal_path, "formal_after")
        if final_payload.get(TASK_ID) != candidate_task12:
            raise ValueError("formal_task_12_not_equal_candidate_after_switch")
        final_other_tasks = {key: value for key, value in final_payload.items() if key != TASK_ID}
        if final_other_tasks != other_tasks_before:
            raise ValueError("formal_other_tasks_changed_after_switch")
        if sha256_file(candidate_path) != candidate_before_sha:
            raise ValueError("candidate_file_changed")

        if rollback_dir is not None:
            rollback_dir.mkdir(parents=True, exist_ok=True)
            copied_rollback_path = rollback_dir / rollback_path.name
            shutil.copy2(rollback_path, copied_rollback_path)

        return {
            "task_id": TASK_ID,
            "candidate": str(candidate_path),
            "formal": str(formal_path),
            "rollback": str(rollback_path),
            "copied_rollback": None if copied_rollback_path is None else str(copied_rollback_path),
            "candidate_sha256": candidate_before_sha,
            "formal_before_sha256": formal_before_sha,
            "formal_after_sha256": sha256_file(formal_path),
            "baseline_version": candidate_task12["baseline_version"],
            "segmentation_version": candidate_task12["segmentation_version"],
            "sensor_mapping_version": candidate_task12["sensor_mapping_version"],
            "other_tasks_unchanged": True,
            "task12_equal_candidate": True,
        }
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely switch formal Task 12 baseline to Drink Water V2.1.2.")
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--formal", required=True, type=Path)
    parser.add_argument("--rollback-dir", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = switch_task12_baseline(args.candidate, args.formal, args.rollback_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
