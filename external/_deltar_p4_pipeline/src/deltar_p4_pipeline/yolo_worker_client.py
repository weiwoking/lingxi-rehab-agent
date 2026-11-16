from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .errors import ErrorCode, PipelineError
from .yolo_contracts import load_yolo_output


COMBINED_REQUEST_SCHEMA = "deltar_yolo_combined_request.v1"
COMBINED_RESPONSE_SCHEMA = "deltar_yolo_combined_response.v1"
RUNTIME_ENDPOINT_ENV = "DELTAR_YOLO_RUNTIME_ENDPOINT"
RUNTIME_TOKEN_ENV = "DELTAR_YOLO_RUNTIME_TOKEN"
RUNTIME_GENERATION_ENV = "DELTAR_YOLO_RUNTIME_GENERATION"
MAX_RUNTIME_RESPONSE_BYTES = 1024 * 1024


def _runtime_round_trip(request: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    endpoint = os.environ.get(RUNTIME_ENDPOINT_ENV)
    if not endpoint:
        raise PipelineError(ErrorCode.YOLO_ENV, "persistent YOLO runtime endpoint is missing", RUNTIME_ENDPOINT_ENV)
    try:
        host, port_text = endpoint.rsplit(":", 1)
        port = int(port_text)
    except (ValueError, TypeError) as exc:
        raise PipelineError(ErrorCode.YOLO_ENV, "persistent YOLO runtime endpoint is invalid", endpoint) from exc
    envelope = {
        "schema_version": "deltar_yolo_runtime_broker_request.v1",
        "token": os.environ.get(RUNTIME_TOKEN_ENV, ""),
        "command": "job",
        "request": request,
    }
    try:
        with socket.create_connection((host, port), timeout=min(timeout_s, 30.0)) as connection:
            connection.settimeout(timeout_s)
            connection.sendall(json.dumps(envelope, ensure_ascii=False).encode("utf-8") + b"\n")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_RUNTIME_RESPONSE_BYTES:
                    raise PipelineError(ErrorCode.YOLO_WORKER, "YOLO runtime response is too large", endpoint)
                if b"\n" in chunk:
                    break
    except (OSError, TimeoutError) as exc:
        raise PipelineError(ErrorCode.YOLO_WORKER, f"YOLO runtime request failed: {exc}", endpoint) from exc
    raw = b"".join(chunks).split(b"\n", 1)[0]
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PipelineError(ErrorCode.YOLO_WORKER, "YOLO runtime response is not JSON", endpoint) from exc


def _load_combined_task(
    task: str,
    task_response: dict[str, Any],
    images: list[Path],
    *,
    invocation_wall_ms: float,
    runtime: dict[str, Any],
) -> list[dict[str, Any]]:
    metadata = task_response.get("metadata")
    timing = task_response.get("timing")
    response_outputs = task_response.get("outputs")
    if type(metadata) is not dict or type(timing) is not dict or type(response_outputs) is not list:
        raise PipelineError(ErrorCode.YOLO_WORKER, f"combined {task} response is incomplete", task)
    reload_started = time.perf_counter()
    outputs: list[dict[str, Any]] = []
    for expected_image, item in zip(images, response_outputs, strict=False):
        json_path = Path(item["json"])
        npz_path = Path(item["npz"]) if item.get("npz") else None
        document, masks = load_yolo_output(json_path, npz_path)
        actual_source = Path(document["image"]["path"]).resolve()
        if str(actual_source).casefold() != str(expected_image.resolve()).casefold():
            raise PipelineError(
                ErrorCode.YOLO_WORKER,
                "YOLO combined output order/path mismatch",
                task,
                {"expected": str(expected_image.resolve()), "actual": str(actual_source)},
            )
        outputs.append(
            {
                "document": document,
                "masks": masks,
                "json_path": json_path,
                "npz_path": npz_path,
                "worker_batch_timing_ms": timing.get("predict_and_postprocess_ms"),
                "worker_timing": timing,
                "worker_metadata": metadata,
                "actual_sub_batches": task_response.get("actual_sub_batches"),
                "worker_invocation_wall_ms": invocation_wall_ms,
                "runtime": runtime,
            }
        )
    parent_reload_ms = (time.perf_counter() - reload_started) * 1000.0
    for output in outputs:
        output["parent_reload_ms"] = parent_reload_ms
    if len(outputs) != len(images):
        raise PipelineError(
            ErrorCode.YOLO_WORKER,
            f"combined {task} output count mismatch",
            task,
            {"expected": len(images), "actual": len(outputs)},
        )
    return outputs


def run_yolo_pair(
    python: Path,
    worker: Path,
    detect_model: Path,
    segment_model: Path,
    images: list[Path],
    output_dir: Path,
    *,
    detect_sha256: str,
    segment_sha256: str,
    scan_id: str,
    device: str = "cuda:0",
    confidence: float = 0.25,
    timeout_s: float = 600.0,
    imgsz: int = 640,
    batch_size: int | None = None,
    half: bool = False,
    require_cuda: bool = True,
    expected_gpu_name: str | None = None,
    required_class_ids: tuple[int, ...] = (),
    test_injection: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    for path, label in ((python, "YOLO Python"), (worker, "combined YOLO worker"),
                        (detect_model, "detect model"), (segment_model, "segment model")):
        if not path.is_file():
            raise PipelineError(ErrorCode.YOLO_ENV, f"{label} is missing", str(path))
    if not images:
        raise PipelineError(ErrorCode.YOLO_WORKER, "YOLO image batch must not be empty", "images")
    detect_output = output_dir / "detect"
    segment_output = output_dir / "segment"
    request = {
        "schema_version": COMBINED_REQUEST_SCHEMA,
        "job_id": f"yolo_{scan_id}_{time.time_ns()}",
        "scan_id": scan_id,
        "generation": int(os.environ.get(RUNTIME_GENERATION_ENV, "0")),
        "models": {
            "detect": {"path": str(detect_model.resolve()), "sha256": detect_sha256.lower()},
            "segment": {"path": str(segment_model.resolve()), "sha256": segment_sha256.lower()},
        },
        "images": [str(path.resolve()) for path in images],
        "output_dirs": {"detect": str(detect_output.resolve()), "segment": str(segment_output.resolve())},
        "output_root": str(output_dir.resolve()),
        "device": device,
        "confidence": confidence,
        "imgsz": int(imgsz),
        "batch_size": int(batch_size or len(images)),
        "half": bool(half),
        "require_cuda": bool(require_cuda),
        "expected_gpu_name": expected_gpu_name,
        "required_class_ids": list(required_class_ids),
        "test_injection": test_injection,
    }
    invocation_started = time.perf_counter()
    if os.environ.get(RUNTIME_ENDPOINT_ENV):
        response = _runtime_round_trip(request, timeout_s)
        mode = "persistent"
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="deltar_yolo_pair_") as folder:
            request_path = Path(folder) / "request.json"
            request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            try:
                completed = subprocess.run(
                    [str(python), str(worker), "--request", str(request_path)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_s,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise PipelineError(ErrorCode.YOLO_WORKER, f"combined YOLO launch failed: {exc}", str(worker)) from exc
        if completed.returncode != 0:
            worker_error: dict[str, Any] = {}
            try:
                worker_error = json.loads(completed.stderr.strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                pass
            raise PipelineError(
                worker_error.get("error_code", str(ErrorCode.YOLO_WORKER)),
                worker_error.get("message", "combined YOLO worker returned non-zero"),
                str(worker),
                {"exit_code": completed.returncode, "stderr": completed.stderr[-4000:]},
            )
        try:
            response = json.loads(completed.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as exc:
            raise PipelineError(ErrorCode.YOLO_WORKER, "combined YOLO response is not JSON", str(worker)) from exc
        mode = "combined_one_shot"
    invocation_wall_ms = (time.perf_counter() - invocation_started) * 1000.0
    if response.get("status") != "success" or response.get("schema_version") != COMBINED_RESPONSE_SCHEMA:
        raise PipelineError(
            response.get("error_code", str(ErrorCode.YOLO_WORKER)),
            response.get("message", "combined YOLO runtime failed"),
            mode,
            response,
        )
    tasks = response.get("tasks") or {}
    runtime = {**(response.get("runtime") or {}), "mode": mode}
    detect_outputs = _load_combined_task("detect", tasks.get("detect") or {}, images,
                                         invocation_wall_ms=invocation_wall_ms, runtime=runtime)
    segment_outputs = _load_combined_task("segment", tasks.get("segment") or {}, images,
                                          invocation_wall_ms=invocation_wall_ms, runtime=runtime)
    return detect_outputs, segment_outputs, {
        "mode": mode,
        "invocation_wall_ms": invocation_wall_ms,
        "runtime": runtime,
    }


def run_yolo_worker(python: Path, worker: Path, model: Path, task: str, images: list[Path], output_dir: Path,
                    *, expected_sha256: str, device: str = "cpu", confidence: float = 0.25,
                    timeout_s: float = 300.0, imgsz: int = 640, batch_size: int | None = None,
                    half: bool = False, require_cuda: bool = False,
                    expected_gpu_name: str | None = None,
                    required_class_ids: tuple[int, ...] = ()) -> list[dict[str, Any]]:
    if not python.is_file():
        raise PipelineError(ErrorCode.YOLO_ENV, "YOLO Python is missing", str(python))
    if not model.is_file():
        raise PipelineError(ErrorCode.YOLO_MODEL_MISSING, "YOLO model is missing", str(model))
    if not images:
        raise PipelineError(ErrorCode.YOLO_WORKER, "YOLO image batch must not be empty", "images")
    if require_cuda and device != "cuda:0":
        raise PipelineError(ErrorCode.YOLO_ENV, "Stage 8B-1 requires device=cuda:0", "device")
    if half:
        raise PipelineError(ErrorCode.YOLO_ENV, "Stage 8B-1 FP16 is forbidden", "half")
    output_dir.mkdir(parents=True, exist_ok=True)
    if len(expected_sha256) != 64:
        raise PipelineError(ErrorCode.YOLO_MODEL_HASH, "expected YOLO SHA-256 must contain 64 hex characters", "expected_sha256")
    request = {"schema_version": "deltar_stage3_1_yolo_worker_request.v1", "model_path": str(model.resolve()),
               "expected_sha256": expected_sha256.lower(), "expected_task": task,
               "device": device, "confidence": confidence, "images": [str(path.resolve()) for path in images],
               "output_dir": str(output_dir.resolve()), "imgsz": int(imgsz),
               "batch_size": int(batch_size or len(images)), "half": bool(half),
               "require_cuda": bool(require_cuda), "expected_gpu_name": expected_gpu_name,
               "required_class_ids": list(required_class_ids)}
    with tempfile.TemporaryDirectory(prefix="deltar_stage3_yolo_") as folder:
        request_path = Path(folder) / "request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        invocation_started = time.perf_counter()
        try:
            completed = subprocess.run([str(python), str(worker), "--request", str(request_path)], capture_output=True,
                                       text=True, encoding="utf-8", errors="replace", timeout=timeout_s, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PipelineError(ErrorCode.YOLO_WORKER, f"YOLO worker launch failed: {exc}", str(worker)) from exc
        invocation_wall_ms = (time.perf_counter() - invocation_started) * 1000.0
    if completed.returncode != 0:
        worker_error: dict[str, Any] = {}
        try:
            worker_error = json.loads(completed.stderr.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            pass
        code = worker_error.get("error_code", str(ErrorCode.YOLO_WORKER))
        raise PipelineError(code, worker_error.get("message", "YOLO worker returned non-zero"), str(worker),
                            {"exit_code": completed.returncode, "stderr": completed.stderr[-4000:], "stdout": completed.stdout[-4000:]})
    try:
        response = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise PipelineError(ErrorCode.YOLO_WORKER, "YOLO worker response is not JSON", str(worker), {"stdout": completed.stdout[-4000:]}) from exc
    if response.get("schema_version") != "deltar_stage8b1_yolo_worker_response.v1":
        raise PipelineError(ErrorCode.YOLO_WORKER, "YOLO worker response schema mismatch", str(worker))
    metadata = response.get("metadata")
    timing = response.get("timing")
    if type(metadata) is not dict or type(timing) is not dict:
        raise PipelineError(ErrorCode.YOLO_WORKER, "YOLO worker metadata/timing missing", str(worker))
    if require_cuda:
        if metadata.get("device") != "cuda:0" or metadata.get("cuda_available") is not True:
            raise PipelineError(ErrorCode.YOLO_ENV, "YOLO worker did not confirm cuda:0", str(worker), metadata)
        gpu_name = str(metadata.get("gpu_name", ""))
        if expected_gpu_name and expected_gpu_name.casefold() not in gpu_name.casefold():
            raise PipelineError(ErrorCode.YOLO_ENV, "YOLO worker GPU identity mismatch", str(worker), metadata)
        if metadata.get("precision") != "FP32" or metadata.get("half") is not False:
            raise PipelineError(ErrorCode.YOLO_ENV, "YOLO worker precision contract mismatch", str(worker), metadata)
    reload_started = time.perf_counter()
    outputs = []
    response_outputs = response.get("outputs", [])
    if type(response_outputs) is not list:
        raise PipelineError(ErrorCode.YOLO_WORKER, "YOLO worker outputs must be a list", str(worker))
    for expected_image, item in zip(images, response_outputs, strict=False):
        json_path = Path(item["json"]); npz_path = Path(item["npz"]) if item.get("npz") else None
        document, masks = load_yolo_output(json_path, npz_path)
        actual_source = Path(document["image"]["path"]).resolve()
        if str(actual_source).casefold() != str(expected_image.resolve()).casefold():
            raise PipelineError(
                ErrorCode.YOLO_WORKER,
                "YOLO worker output order/path mismatch",
                str(worker),
                {"expected": str(expected_image.resolve()), "actual": str(actual_source)},
            )
        outputs.append({"document": document, "masks": masks, "json_path": json_path, "npz_path": npz_path,
                        "worker_batch_timing_ms": timing.get("predict_and_postprocess_ms"),
                        "worker_timing": timing, "worker_metadata": metadata,
                        "actual_sub_batches": response.get("actual_sub_batches"),
                        "worker_invocation_wall_ms": invocation_wall_ms})
    parent_reload_ms = (time.perf_counter() - reload_started) * 1000.0
    for output in outputs:
        output["parent_reload_ms"] = parent_reload_ms
    if len(outputs) != len(images):
        raise PipelineError(
            ErrorCode.YOLO_WORKER,
            "YOLO worker output count mismatch",
            str(worker),
            {"expected": len(images), "actual": len(outputs)},
        )
    return outputs
