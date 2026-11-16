from __future__ import annotations

import time

_PROCESS_STARTED = time.perf_counter()
_IMPORT_STARTED = time.perf_counter()

import argparse
import hashlib
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.frame_pipeline import FramePipelineError
from deltar_p4_pipeline.frozen_runtime_contracts import (
    ScanJobCancelled,
    ScanJobRequest,
    identity_hints_unchanged,
)
from deltar_p4_pipeline.frozen_runtime_resources import create_shared_runtime_resources
from deltar_p4_pipeline.scan_contracts import ScanContractError
from deltar_p4_pipeline.scan_job import run_scan_job
from deltar_p4_pipeline.stage8b4a_contracts import MAX_PACKAGE_BYTES, validate_p4_package
from deltar_p4_pipeline.stage8b4a_contracts import P4_PACKAGE_FILENAME

_IMPORTS_MS = (time.perf_counter() - _IMPORT_STARTED) * 1000.0

READY_SCHEMA = "deltar_frozen_scan_runtime_ready.v1"
COMMAND_SCHEMA = "deltar_frozen_scan_runtime_command.v1"
RESPONSE_SCHEMA = "deltar_frozen_scan_runtime_response.v1"


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


def error_payload(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, ScanContractError):
        return {"error_code": exc.code, "message": exc.message, "details": exc.as_dict(), "fatal": False}
    if isinstance(exc, FramePipelineError):
        return {
            "error_code": exc.code,
            "message": exc.message,
            "details": {"phase": exc.phase, "error_type": exc.error_type, **exc.details},
            "fatal": False,
        }
    return {
        "error_code": "E_FROZEN_RUNTIME_JOB",
        "message": f"{type(exc).__name__}: {exc}",
        "details": {"error_type": type(exc).__name__},
        "fatal": True,
    }


def serve(config_path: Path) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema_version") != "deltar_frozen_scan_runtime_startup.v1":
        raise ValueError("unsupported frozen runtime startup schema")
    calibration_paths = [Path(value) for value in config.get("calibration_paths", [])]
    resources = create_shared_runtime_resources(
        Path(config["profile_bundle"]),
        device=str(config["device"]),
        generation=int(config["generation"]),
        calibration_paths=calibration_paths,
        worktree=Path(config["worktree"]),
        checkpoint=Path(config["checkpoint"]),
        process_start=_PROCESS_STARTED,
        imports_ms=_IMPORTS_MS,
    )
    emit({
        "schema_version": READY_SCHEMA,
        "status": "READY",
        "pid": os.getpid(),
        "generation": resources.generation,
        "timing": resources.startup_timing,
        "runtime_identity": resources.identity,
        "identity_hints": resources.identity_hints,
        "gpu": {
            "allocated_bytes": int(torch.cuda.memory_allocated()) if torch.cuda.is_available() else 0,
            "reserved_bytes": int(torch.cuda.memory_reserved()) if torch.cuda.is_available() else 0,
        },
    })
    for raw in sys.stdin:
        try:
            command = json.loads(raw)
            if command.get("schema_version") != COMMAND_SCHEMA:
                raise ValueError("unsupported frozen runtime command schema")
            operation = command.get("command")
            if operation == "shutdown":
                emit({"schema_version": RESPONSE_SCHEMA, "status": "stopped", "generation": resources.generation})
                return 0
            if operation == "ping":
                emit({
                    "schema_version": RESPONSE_SCHEMA,
                    "status": "pong",
                    "pid": os.getpid(),
                    "generation": resources.generation,
                    "identity_unchanged": identity_hints_unchanged(
                        resources.identity_hints
                    ),
                    "gpu": {
                        "allocated_bytes": int(torch.cuda.memory_allocated()) if torch.cuda.is_available() else 0,
                        "reserved_bytes": int(torch.cuda.memory_reserved()) if torch.cuda.is_available() else 0,
                    },
                })
                continue
            if operation != "job":
                raise ValueError("unknown frozen runtime command")
            if not identity_hints_unchanged(resources.identity_hints):
                emit({
                    "schema_version": RESPONSE_SCHEMA,
                    "status": "stale",
                    "error_code": "E_FROZEN_RUNTIME_STALE",
                    "message": "runtime identity hint changed",
                    "generation": resources.generation,
                })
                return 75
            injection = command.get("test_injection")
            if injection and os.environ.get("DELTAR_FROZEN_RUNTIME_TESTING") != "1":
                raise ValueError("test injection is disabled")
            if injection == "crash":
                os._exit(86)
            request = ScanJobRequest.from_dict(command["request"])
            if request.profile_dir.resolve() != resources.bundle.root or request.device != resources.bridge.device:
                raise ValueError("job static identity does not match runtime generation")
            started = time.perf_counter()
            try:
                result, report = run_scan_job(request, resources)
                package_path = request.output_dir / P4_PACKAGE_FILENAME
                package_size = package_path.stat().st_size
                if package_size < 1 or package_size > MAX_PACKAGE_BYTES:
                    raise ValueError("canonical package size is invalid")
                with package_path.open("rb") as handle:
                    package_raw = handle.read(MAX_PACKAGE_BYTES + 1)
                if len(package_raw) != package_size:
                    raise ValueError("canonical package changed during bounded read")
                package_sha256 = hashlib.sha256(package_raw).hexdigest()
                sidecar = package_path.with_name(package_path.name + ".sha256").read_text(encoding="ascii").strip()
                if sidecar != package_sha256:
                    raise ValueError("canonical package SHA sidecar mismatch")
                package = json.loads(package_raw.decode("utf-8", errors="strict"))
                validate_p4_package(package)
                if package.get("scan_id") != report["scan_id"]:
                    raise ValueError("canonical package scan identity mismatch")
            except ScanJobCancelled:
                emit({
                    "schema_version": RESPONSE_SCHEMA,
                    "status": "aborted",
                    "reason": "ABORTED_BY_REAL_TRAFFIC",
                    "generation": resources.generation,
                })
                continue
            except BaseException as exc:
                payload = error_payload(exc)
                emit({
                    "schema_version": RESPONSE_SCHEMA,
                    "status": "rejected",
                    "generation": resources.generation,
                    **payload,
                })
                if payload["fatal"]:
                    traceback.print_exception(exc, file=sys.stderr)
                    return 70
                continue
            emit({
                "schema_version": RESPONSE_SCHEMA,
                "status": "success",
                "generation": resources.generation,
                "job_wall_ms": (time.perf_counter() - started) * 1000.0,
                "result_status": result["status"],
                "job_id": report["job_id"],
                "scan_id": report["scan_id"],
                "job_processing_ms": report["processing"]["total_ms"],
                "canonical_package": {
                    "schema_version": package["schema_version"],
                    "filename": P4_PACKAGE_FILENAME,
                    "scan_id": report["scan_id"],
                    "generation": resources.generation,
                    "bytes": len(package_raw),
                    "sha256": package_sha256,
                    "object_count": len(package["objects"]),
                    "written_mtime_ns": package_path.stat().st_mtime_ns,
                },
                "gpu": {
                    "allocated_bytes": int(torch.cuda.memory_allocated()) if torch.cuda.is_available() else 0,
                    "reserved_bytes": int(torch.cuda.memory_reserved()) if torch.cuda.is_available() else 0,
                    "peak_bytes": int(torch.cuda.max_memory_allocated()) if torch.cuda.is_available() else 0,
                },
            })
        except BaseException as exc:
            emit({"schema_version": RESPONSE_SCHEMA, "status": "rejected", **error_payload(exc)})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", type=Path, required=True)
    return serve(parser.parse_args().serve)


if __name__ == "__main__":
    raise SystemExit(main())
