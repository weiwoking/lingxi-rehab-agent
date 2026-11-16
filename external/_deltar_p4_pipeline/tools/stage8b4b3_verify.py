from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from deltar_p4_pipeline.scan_service import ServiceConfig, create_app
from deltar_p4_pipeline.stage8b4a_contracts import P4_PACKAGE_FILENAME


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--pipeline-root", type=Path, required=True)
    parser.add_argument("--worker-python", type=Path, required=True)
    parser.add_argument("--scan-id", required=True)
    args = parser.parse_args()
    config = ServiceConfig(
        data_root=args.data_root.resolve(),
        pipeline_root=args.pipeline_root.resolve(),
        worker_python=args.worker_python.resolve(),
        device="cuda:0",
        auto_open_visualization=False,
    )
    result_dir = config.data_root / "results" / args.scan_id
    package_path = result_dir / P4_PACKAGE_FILENAME
    package_raw = package_path.read_bytes()
    service_phases = [
        json.loads(line)
        for line in (result_dir / "logs" / "service_phases.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    pipeline_phases = [
        json.loads(line)
        for line in (result_dir / "logs" / "pipeline_phases.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    manifest = json.loads(
        (result_dir / "stage8b1_yolo_batch_manifest.json").read_text(
            encoding="utf-8"
        )
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
    record = json.loads(
        (result_dir / "service_job.json").read_text(encoding="utf-8")
    )
    summary = {
        "schema_version": "stage8b4b3_read_only_verification.v1",
        "scan_id": args.scan_id,
        "decision_status": decision,
        "final_record": record,
        "package_http_statuses": [first.status_code, second.status_code],
        "package_length": len(package_raw),
        "package_sha256": hashlib.sha256(package_raw).hexdigest(),
        "package_file_matches_get": package_raw == first.content,
        "package_get_bytes_identical": first.content == second.content,
        "shared_inference_process_count": sum(
            item.get("phase") == "shared_inference_geometry"
            for item in service_phases
        ),
        "yolo_phase_count": sum(
            item.get("phase") == "yolo_detect_segment"
            for item in pipeline_phases
        ),
        "yolo_worker_invocations": manifest["worker_invocations"],
        "full_visualization_process_count": sum(
            item.get("phase") == "stage8b3_full_visualization"
            for item in service_phases
        ),
        "visualization_open_attempts": record.get(
            "visualization_open_attempts"
        ),
    }
    output = config.data_root / "stage8b4b3_read_only_verification.json"
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if (
        decision.get("status") == "decision_ready"
        and decision.get("package_ready") is True
        and decision.get("package_length") == len(package_raw)
        and decision.get("package_sha256")
        == hashlib.sha256(package_raw).hexdigest()
        and first.status_code == second.status_code == 200
        and package_raw == first.content == second.content
        and summary["shared_inference_process_count"] == 1
        and summary["yolo_phase_count"] == 1
        and summary["full_visualization_process_count"] == 1
        and summary["visualization_open_attempts"] == 1
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
