from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from deltar_p4_pipeline.scan_service import ServiceConfig, create_app
from deltar_p4_pipeline.scan_service_contracts import utc_now
from deltar_p4_pipeline.scan_service_storage import ServiceStorage
from deltar_p4_pipeline.scan_service_worker import run_pipeline
from deltar_p4_pipeline.stage8b4a_contracts import P4_PACKAGE_FILENAME


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay one validated Stage 8B-4B-3 scan in isolated storage."
    )
    parser.add_argument("--source-scan", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--pipeline-root", type=Path, required=True)
    parser.add_argument("--worker-python", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source_scan.resolve()
    data_root = args.data_root.resolve()
    if data_root.exists():
        raise SystemExit(f"refusing to reuse replay data root: {data_root}")
    scan = json.loads((source / "scan.json").read_text(encoding="utf-8"))
    scan_id = str(scan["scan_id"])
    config = ServiceConfig(
        data_root=data_root,
        pipeline_root=args.pipeline_root.resolve(),
        worker_python=args.worker_python.resolve(),
        device=args.device,
        auto_open_visualization=False,
    )
    storage = ServiceStorage(data_root)
    validated = storage.validated_dir(scan_id)
    shutil.copytree(source, validated)
    service_job_id = "svc_replay_" + uuid.uuid4().hex
    record: dict[str, Any] = {
        "schema_version": "deltar_scan_service_job_internal.v1",
        "service_job_id": service_job_id,
        "pipeline_job_id": None,
        "scan_id": scan_id,
        "status": "running",
        "pipeline_result_status": None,
        "created_at": utc_now(),
        "started_at": utc_now(),
        "finished_at": None,
        "error": None,
        "mode": "full_pipeline",
        "frame_count": int(scan["frame_count"]),
        "source_identity": "isolated_replay_of_existing_validated_scan",
        "upload_bytes": sum(
            path.stat().st_size
            for path in validated.rglob("*")
            if path.is_file() and path.name != "service_job.json"
        ),
        "validate_seconds": None,
        "package_ready": False,
        "full_scene_status": "pending",
        "full_scene_ready": False,
        "full_scene_error_code": None,
        "full_scene_error_message": None,
    }
    storage.atomic_json(validated / "service_job.json", record)
    transitions: list[dict[str, Any]] = []

    def transition(updated: dict[str, Any]) -> None:
        snapshot = deepcopy(updated)
        transitions.append(snapshot)
        storage.atomic_json(validated / "service_job.json", snapshot)
        if storage.result_dir(scan_id).is_dir():
            storage.atomic_json(
                storage.result_dir(scan_id) / "service_job.json", snapshot
            )

    started = time.perf_counter()
    run_pipeline(config.worker(), storage, record, transition)
    elapsed = time.perf_counter() - started
    result_dir = storage.result_dir(scan_id)
    package_path = result_dir / P4_PACKAGE_FILENAME
    package_raw = package_path.read_bytes() if package_path.is_file() else b""

    with TestClient(create_app(config)) as client:
        decision = client.get(
            f"/api/v1/scans/{scan_id}/decision-status"
        ).json()
        first = client.get(f"/api/v1/scans/{scan_id}/decision-package")
        second = client.get(f"/api/v1/scans/{scan_id}/decision-package")
    final_record = json.loads(
        (result_dir / "service_job.json").read_text(encoding="utf-8")
    )
    summary = {
        "schema_version": "stage8b4b3_replay_summary.v1",
        "scan_id": scan_id,
        "source_scan": str(source),
        "data_root": str(data_root),
        "pipeline_root": str(args.pipeline_root.resolve()),
        "service_python": sys.executable,
        "worker_python": str(args.worker_python.resolve()),
        "device": args.device,
        "elapsed_seconds": elapsed,
        "transition_statuses": [item.get("status") for item in transitions],
        "final_record": final_record,
        "decision_status": decision,
        "package_http_statuses": [first.status_code, second.status_code],
        "package_get_bytes_identical": first.content == second.content,
        "package_file_matches_get": package_raw == first.content,
        "package_length": len(package_raw),
        "package_sha256": hashlib.sha256(package_raw).hexdigest()
        if package_raw
        else None,
    }
    write_json(data_root / "stage8b4b3_replay_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if (
        decision.get("status") == "decision_ready"
        and decision.get("package_ready") is True
        and final_record.get("full_scene_ready") is True
        and first.status_code == second.status_code == 200
        and first.content == second.content == package_raw
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
