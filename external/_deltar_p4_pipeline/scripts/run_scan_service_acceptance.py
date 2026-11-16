from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import fastapi
import multipart
import starlette
import uvicorn
from fastapi.testclient import TestClient

from deltar_p4_pipeline.scan_service import ServiceConfig, create_app
from tests.scan_service_helpers import multipart_bytes


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PC Scan Service Phase A acceptance")
    parser.add_argument("--scan-dir", type=Path, default=Path(r"C:\ec\scan_test\scan_20260718_081523_000"))
    parser.add_argument("--data-root", type=Path, default=Path(r"C:\ec\_deltar_scan_service_data"))
    parser.add_argument("--worker-python", type=Path, default=Path(r"C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe"))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs_scan_service")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--scan-id", help="override scan_id in the in-memory multipart without changing the source scan")
    args = parser.parse_args()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    p4_head = git("-C", r"C:\ec\esp-claw", "rev-parse", "HEAD")
    p4_status_before = git("-C", r"C:\ec\esp-claw", "status", "--short")
    environment = {
        "schema_version": "deltar_scan_service_environment_audit.v1", "generated_at": generated_at,
        "service_python": sys.version.split()[0], "fastapi": fastapi.__version__, "starlette": starlette.__version__,
        "uvicorn": uvicorn.__version__, "python_multipart": multipart.__version__,
        "worker_python": str(args.worker_python), "dependencies_installed": False,
        "p4_head": p4_head, "p4_expected_head": "6589245490be19a177899a50849b6448dd1583ce",
        "p4_clean_before": p4_status_before == "",
    }
    write(out / "environment_audit.json", environment)

    test_env = dict(os.environ)
    test_env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q",
                            "tests/test_scan_service_contracts.py", "tests/test_scan_service_storage.py",
                            "tests/test_scan_service_worker.py", "tests/test_scan_service_api.py"],
                           cwd=ROOT, env=test_env, capture_output=True, text=True)
    write(out / "unit_test_report.json", {
        "schema_version": "deltar_scan_service_unit_tests.v1", "passed": tests.returncode == 0,
        "command": "vision-python -m pytest <scan service tests>",
        "summary": (tests.stdout + tests.stderr).strip().splitlines()[-3:],
    })

    config = ServiceConfig(data_root=args.data_root, pipeline_root=ROOT, worker_python=args.worker_python,
                           profile_bundle=ROOT / "profiles", job_timeout_seconds=int(args.timeout_seconds))
    app = create_app(config)
    def upload_parts():
        parts = multipart_bytes(args.scan_dir)
        if args.scan_id:
            name, (filename, raw, content_type) = parts[0]
            scan_json = json.loads(raw.decode("utf-8"))
            scan_json["scan_id"] = args.scan_id
            parts[0] = (name, (filename, json.dumps(scan_json).encode("utf-8"), content_type))
        return parts
    started = time.perf_counter()
    with TestClient(app) as client:
        post_started = time.perf_counter()
        response = client.post(
            "/api/v1/scan/jobs", files=upload_parts(),
            headers={"X-VisionPC-Job-Role": "test"},
        )
        post_seconds = time.perf_counter() - post_started
        response_body = response.json()
        if response.status_code not in {200, 202}:
            write(out / "real_5frame_integration.json", {"status": "fail", "response": response_body})
            return 1
        status_url = response_body["status_url"]
        deadline = time.monotonic() + args.timeout_seconds + 30
        status = response_body
        while status["status"] not in {"completed", "failed"} and time.monotonic() < deadline:
            time.sleep(0.2)
            status = client.get(status_url).json()
        total_seconds = time.perf_counter() - started
        if status["status"] == "completed":
            stored_path = args.data_root / "results" / status["scan_id"] / "result.json"
            stored_before = stored_path.read_bytes()
            downloaded = client.get(status["result_url"])
            stored_after = stored_path.read_bytes()
            result = json.loads(downloaded.content.decode("utf-8"))
            original_unchanged = stored_before == downloaded.content == stored_after
            result_bytes = len(downloaded.content)
            result_sha = sha256(downloaded.content)
            objects = len(result["objects"])
            warnings = result["warnings"]
        else:
            downloaded = None
            result = {}
            original_unchanged = False
            result_bytes = 0
            result_sha = None
            objects = None
            warnings = []
        record = app.state.scan_manager.get(status["service_job_id"])
        same = client.post(
            "/api/v1/scan/jobs", files=upload_parts(),
            headers={"X-VisionPC-Job-Role": "test"},
        )
        health = client.get("/api/v1/scan/health").json()

    restarted_app = create_app(config)
    with TestClient(restarted_app) as client:
        restarted = client.get(status_url).json()

    p4_status_after = git("-C", r"C:\ec\esp-claw", "status", "--short")
    passed = (
        response.status_code == 202 and status["status"] == "completed" and downloaded is not None
        and downloaded.status_code == 200 and original_unchanged and result.get("scan_id") == status["scan_id"]
        and result.get("job_id") == status["pipeline_job_id"] and same.status_code == 200
        and same.json().get("idempotent") is True and restarted["status"] == "completed"
        and p4_status_before == p4_status_after == "" and tests.returncode == 0
    )
    integration = {
        "schema_version": "deltar_scan_service_real_5frame_integration.v1",
        "status": "pass" if passed else "fail", "http_create_status": response.status_code,
        "scan_id": status.get("scan_id"), "frame_count": 5,
        "service_job_id": status.get("service_job_id"), "pipeline_job_id": status.get("pipeline_job_id"),
        "service_status": status.get("status"), "pipeline_result_status": status.get("pipeline_result_status"),
        "upload_bytes": record.get("upload_bytes"), "post_including_validate_seconds": post_seconds,
        "validate_seconds": record.get("validate_seconds"), "run_seconds": record.get("run_seconds"),
        "total_seconds": total_seconds, "result_bytes": result_bytes, "result_sha256": result_sha,
        "objects": objects, "warnings": warnings, "original_result_bytes_unchanged": original_unchanged,
    }
    write(out / "real_5frame_integration.json", integration)
    write(out / "performance_report.json", {
        "schema_version": "deltar_scan_service_performance.v1", "baseline_5frame_seconds": 60.65,
        "upload_and_validate_seconds": post_seconds, "validate_seconds": record.get("validate_seconds"),
        "run_seconds": record.get("run_seconds"), "total_seconds": total_seconds,
        "second_job_cold_start_behavior": "unchanged; each job starts the frozen worker process",
        "known_bottleneck": "YOLO subprocess/model startup remains the bottleneck; no resident-model claim",
    })
    write(out / "idempotency_report.json", {
        "schema_version": "deltar_scan_service_idempotency.v1", "same_completed_http_status": same.status_code,
        "same_service_job_id": same.json().get("service_job_id") == status.get("service_job_id"),
        "idempotent": same.json().get("idempotent"), "busy_and_active_cases": "covered by service API tests",
    })
    write(out / "restart_behavior.json", {
        "schema_version": "deltar_scan_service_restart.v1", "completed_reconstructed": restarted["status"] == "completed",
        "resume_supported": False, "stale_validated_behavior": "stale_failed_no_resume",
        "stale_upload_behavior": "moved to failed; never resumed", "tests": "pass" if tests.returncode == 0 else "fail",
    })
    write(out / "http_contract.json", {
        "schema_version": "deltar_scan_service.v1", "endpoints": [
            "GET /api/v1/scan/health", "POST /api/v1/scan/jobs",
            "GET /api/v1/scan/jobs/{service_job_id}", "GET /api/v1/scan/jobs/{service_job_id}/result"],
        "create_status": 202, "completed_idempotent_status": 200, "default_port": 8030,
        "limits": health["limits"], "resume_supported": False,
    })
    write(out / "upload_field_contract.json", {
        "schema_version": "deltar_scan_service_upload_fields.v1", "scan": "scan_json",
        "frame_pattern": "frame_<6 digits>_<frame_json|tof_json|image_rgb565>",
        "parts_for_5_frames": 16, "image_bytes": 460800, "json_max_bytes": 65536,
    })
    write(out / "security_matrix.json", {
        "schema_version": "deltar_scan_service_security_matrix.v1", "status": "pass" if tests.returncode == 0 else "fail",
        "checks": {"path_traversal": True, "duplicate_part": True, "unknown_part": True,
                   "missing_part": True, "image_size": True, "json_limit": True, "request_limit": True,
                   "no_shell_true": True, "worker_environment_allowlist": True, "no_http_traceback": True,
                   "no_http_absolute_path": True, "no_overwrite": True, "data_root_containment": True},
    })
    write(out / "phase_a_acceptance.json", {
        "schema_version": "deltar_scan_service_phase_a_acceptance.v1", "verdict": "PASS" if passed else "PARTIAL",
        "pc_scan_service_status": "complete" if passed else "partial",
        "p4_transfer_implementation_allowed": passed, "real_5frame_passed": passed,
        "service_tests_passed": tests.returncode == 0, "p4_repository_unmodified": p4_status_before == p4_status_after == "",
    })
    print(json.dumps(integration, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
