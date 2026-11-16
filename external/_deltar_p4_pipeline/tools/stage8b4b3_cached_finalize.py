from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from deltar_p4_pipeline.scan_contracts import atomic_write_json
from deltar_p4_pipeline.geometry_contracts import ARTIFACT_FILENAME
from deltar_p4_pipeline.scan_job import _job_report
from deltar_p4_pipeline.scan_profiles import load_profile_bundle
from deltar_p4_pipeline.scan_service import ServiceConfig, create_app
from deltar_p4_pipeline.scan_service_contracts import utc_now
from deltar_p4_pipeline.scan_service_storage import ServiceStorage
from deltar_p4_pipeline.scan_service_worker import (
    _finish_full_scene,
    validate_decision_package,
)
from deltar_p4_pipeline.stage8b4a_contracts import P4_PACKAGE_FILENAME


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Finish package and visualization from one existing replay cache."
    )
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--pipeline-root", type=Path, required=True)
    parser.add_argument("--worker-python", type=Path, required=True)
    parser.add_argument("--scan-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = ServiceConfig(
        data_root=args.data_root.resolve(),
        pipeline_root=args.pipeline_root.resolve(),
        worker_python=args.worker_python.resolve(),
        device="cuda:0",
        auto_open_visualization=False,
    )
    storage = ServiceStorage(config.data_root)
    result_dir = storage.result_dir(args.scan_id)
    validated_dir = storage.validated_dir(args.scan_id)
    result = json.loads((result_dir / "result.json").read_text(encoding="utf-8"))
    geometry_started = time.perf_counter()
    geometry_path = result_dir / ARTIFACT_FILENAME
    if not geometry_path.exists():
        raise FileNotFoundError(
            f"cached finalize requires the existing all-object authority: {geometry_path}"
        )
    cached_geometry_seconds = time.perf_counter() - geometry_started
    frames = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((result_dir / "frame_results").glob("frame_*.json"))
    ]
    rejected = [
        {
            "frame_id": frame["frame_id"],
            "sequence": frame["sequence"],
            "error": (
                frame["errors"][0]
                if frame.get("errors")
                else {
                    "error_code": "E_CACHED_REJECTED_FRAME_WITHOUT_ERROR",
                    "message": "cached frame is rejected but has no error entry",
                }
            ),
        }
        for frame in frames
        if frame["status"] == "rejected"
    ]
    phase_lines = [
        json.loads(line)
        for line in (result_dir / "logs" / "pipeline_phases.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    started_at = phase_lines[0]["phase_start"]
    bundle = load_profile_bundle(config.pipeline_root / "profiles")
    report = _job_report(
        result["job_id"],
        args.scan_id,
        "partial_success",
        started_at,
        result_status=result["status"],
        errors=[],
        warnings=result["warnings"],
        frame_count=len(frames),
        processed_frames=[
            frame["frame_id"] for frame in frames if frame["status"] != "rejected"
        ],
        rejected_frames=rejected,
        bundle=bundle,
        exit_code=0,
        result_written=True,
        total_ms=float(result["processing"]["total_ms"]),
    )
    atomic_write_json(result_dir / "job_report.json", report)

    package_command = [
        str(config.worker_python),
        str(config.pipeline_root / "tools" / "stage8b4a_build_decision_package.py"),
        str(result_dir),
    ]
    package_started = time.perf_counter()
    package_completed = subprocess.run(
        package_command,
        cwd=config.pipeline_root,
        capture_output=True,
        check=False,
    )
    (result_dir / "logs" / "cached_finalize_package.stdout.log").write_bytes(
        package_completed.stdout
    )
    (result_dir / "logs" / "cached_finalize_package.stderr.log").write_bytes(
        package_completed.stderr
    )
    if package_completed.returncode != 0:
        raise SystemExit(package_completed.returncode)
    _, package_raw, package_sha = validate_decision_package(
        config.worker(), storage, args.scan_id
    )
    record = json.loads(
        (result_dir / "service_job.json").read_text(encoding="utf-8")
    )
    record.update({
        "status": "decision_ready",
        "pipeline_job_id": result["job_id"],
        "pipeline_result_status": result["status"],
        "finished_at": None,
        "error": None,
        "package_length": len(package_raw),
        "package_sha256": package_sha,
        "package_ready": True,
        "decision_ready_at": utc_now(),
        "full_scene_status": "pending",
        "full_scene_ready": False,
        "full_scene_error_code": None,
        "full_scene_error_message": None,
        "visualization_open_attempts": 0,
        "cached_finalize": True,
        "cached_finalize_package_seconds": time.perf_counter() - package_started,
        "cached_all_object_geometry_seconds": cached_geometry_seconds,
    })
    transitions: list[dict[str, Any]] = []

    def transition(updated: dict[str, Any]) -> None:
        snapshot = deepcopy(updated)
        transitions.append(snapshot)
        storage.atomic_json(result_dir / "service_job.json", snapshot)
        storage.atomic_json(validated_dir / "service_job.json", snapshot)

    transition(record)
    _finish_full_scene(
        config.worker(),
        storage,
        record,
        transition,
        allow_auto_open=True,
    )
    with TestClient(create_app(config)) as client:
        decision = client.get(
            f"/api/v1/scans/{args.scan_id}/decision-status"
        ).json()
        first = client.get(
            f"/api/v1/scans/{args.scan_id}/decision-package"
        )
        second = client.get(
            f"/api/v1/scans/{args.scan_id}/decision-package"
        )
    package_path = result_dir / P4_PACKAGE_FILENAME
    summary = {
        "schema_version": "stage8b4b3_cached_finalize_summary.v1",
        "scan_id": args.scan_id,
        "used_existing_inference_cache": True,
        "new_inference_invocations": 0,
        "transition_statuses": [item["status"] for item in transitions],
        "decision_status": decision,
        "final_record": json.loads(
            (result_dir / "service_job.json").read_text(encoding="utf-8")
        ),
        "package_length": len(package_raw),
        "package_sha256": package_sha,
        "package_get_bytes_identical": first.content == second.content,
        "package_file_matches_get": package_path.read_bytes() == first.content,
        "package_http_statuses": [first.status_code, second.status_code],
        "package_file_sha256": hashlib.sha256(
            package_path.read_bytes()
        ).hexdigest(),
    }
    summary_path = config.data_root / "stage8b4b3_cached_finalize_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if (
        decision.get("status") == "decision_ready"
        and decision.get("package_ready") is True
        and summary["final_record"].get("full_scene_ready") is True
        and first.status_code == second.status_code == 200
        and first.content == second.content == package_raw
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
