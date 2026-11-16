from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

from yolo_worker import WorkerError, predict_batch, resize_nearest, sha256


SCRIPT_STARTED = time.perf_counter()
PROTOCOL_STDOUT = sys.stdout
REQUEST_SCHEMA = "deltar_yolo_combined_request.v1"
RESPONSE_SCHEMA = "deltar_yolo_combined_response.v1"
READY_SCHEMA = "deltar_yolo_runtime_ready.v1"
COMMAND_SCHEMA = "deltar_yolo_runtime_command.v1"
SCHEMA = "deltar_stage3_yolo.v1"


class Runtime:
    def __init__(self, startup: dict[str, Any], *, warmup: bool) -> None:
        imports_started = time.perf_counter()
        import numpy as np
        from PIL import Image
        import torch
        import torchvision
        import ultralytics
        from ultralytics import YOLO

        self.np = np
        self.Image = Image
        self.torch = torch
        self.torchvision = torchvision
        self.ultralytics = ultralytics
        self.YOLO = YOLO
        self.imports_ms = (time.perf_counter() - imports_started) * 1000.0
        self.device = str(startup.get("device", "cuda:0"))
        self.half = bool(startup.get("half", False))
        self.imgsz = int(startup.get("imgsz", 640))
        self.batch_size = int(startup.get("batch_size", 11))
        self.confidence = float(startup.get("confidence", 0.25))
        self.require_cuda = bool(startup.get("require_cuda", True))
        self.expected_gpu_name = startup.get("expected_gpu_name")
        self.required_class_ids = [int(value) for value in startup.get("required_class_ids", [])]
        if self.require_cuda and self.device != "cuda:0":
            raise WorkerError("E_YOLO_DEVICE", "runtime requires exactly device=cuda:0")
        if self.require_cuda and self.half:
            raise WorkerError("E_YOLO_PRECISION", "runtime requires FP32")
        self.cuda_available = bool(torch.cuda.is_available())
        if self.require_cuda and not self.cuda_available:
            raise WorkerError("E_YOLO_CUDA_UNAVAILABLE", "CUDA is unavailable")
        self.gpu_name = torch.cuda.get_device_name(0) if self.cuda_available else None
        if (
            self.require_cuda
            and self.expected_gpu_name
            and str(self.expected_gpu_name).casefold() not in str(self.gpu_name).casefold()
        ):
            raise WorkerError(
                "E_YOLO_GPU_MISMATCH",
                f"GPU mismatch: expected {self.expected_gpu_name!r}, actual {self.gpu_name!r}",
            )
        if self.require_cuda:
            torch.cuda.set_device(0)
            torch.cuda.reset_peak_memory_stats(0)

        models = startup.get("models")
        if type(models) is not dict or set(models) != {"detect", "segment"}:
            raise WorkerError("E_YOLO_CONFIG", "startup models must contain detect and segment")
        self.models: dict[str, Any] = {}
        self.model_meta: dict[str, dict[str, Any]] = {}
        self.model_identity: dict[str, dict[str, Any]] = {}
        self.model_timing: dict[str, dict[str, float]] = {}
        for task in ("detect", "segment"):
            self._load_model(task, models[task])

        warmup_started = time.perf_counter()
        self.warmup_mode = "disabled_combined_one_shot"
        self.warmup_sub_batches: dict[str, list[int]] = {}
        if warmup:
            self._warmup()
            self.warmup_mode = "startup_full_batch_11_black_rgb"
        self.warmup_ms = (time.perf_counter() - warmup_started) * 1000.0 if warmup else 0.0
        self.jobs_completed = 0

    def _load_model(self, task: str, config: dict[str, Any]) -> None:
        model_path = Path(config["path"]).resolve()
        if not model_path.is_file():
            raise WorkerError("E_YOLO_MODEL_MISSING", f"YOLO model is missing: {model_path}")
        hash_started = time.perf_counter()
        actual_sha256 = sha256(model_path)
        hash_ms = (time.perf_counter() - hash_started) * 1000.0
        expected_sha256 = str(config["sha256"]).lower()
        if actual_sha256.lower() != expected_sha256:
            raise WorkerError(
                "E_YOLO_MODEL_HASH",
                f"YOLO model SHA-256 mismatch: expected {expected_sha256}, actual {actual_sha256}",
            )
        load_started = time.perf_counter()
        model = self.YOLO(str(model_path), task=task)
        if str(model.task) != task:
            raise WorkerError("E_YOLO_TASK", f"loaded task {model.task!r}, expected {task!r}")
        if self.require_cuda:
            model.to(self.device)
            model.model.float()
        load_ms = (time.perf_counter() - load_started) * 1000.0
        missing_class_ids = [value for value in self.required_class_ids if value not in model.names]
        if missing_class_ids:
            raise WorkerError("E_YOLO_CLASS_METADATA", f"required class ids missing: {missing_class_ids}")
        stat = model_path.stat()
        actual_model_device = str(next(model.model.parameters()).device)
        self.models[task] = model
        self.model_identity[task] = {
            "path": str(model_path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": actual_sha256,
        }
        self.model_timing[task] = {"model_hash_ms": hash_ms, "model_load_ms": load_ms}
        self.model_meta[task] = {
            "path": str(model_path),
            "sha256": actual_sha256,
            "expected_sha256": expected_sha256,
            "hash_verified": True,
            "size_bytes": stat.st_size,
            "task": task,
            "expected_task": task,
            "task_verified": True,
            "device": self.device,
            "actual_model_device": actual_model_device,
            "precision": "FP16" if self.half else "FP32",
            "half": self.half,
            "ultralytics_version": self.ultralytics.__version__,
            "torch_version": self.torch.__version__,
            "class_names": {str(k): v for k, v in model.names.items()},
        }

    def _base_request(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "confidence": self.confidence,
            "imgsz": self.imgsz,
            "batch_size": self.batch_size,
            "half": self.half,
            "require_cuda": self.require_cuda,
        }

    def _warmup(self) -> None:
        dummy = self.np.zeros((360, 640, 3), dtype=self.np.uint8)
        request = self._base_request()
        for task in ("detect", "segment"):
            results = self.models[task].predict(
                source=[dummy] * self.batch_size,
                batch=self.batch_size,
                imgsz=self.imgsz,
                conf=self.confidence,
                device=self.device,
                half=self.half,
                verbose=False,
                stream=False,
            )
            if len(results) != self.batch_size:
                raise WorkerError("E_YOLO_WARMUP", f"{task} warmup output count mismatch")
            self.warmup_sub_batches[task] = [self.batch_size]
        if self.require_cuda:
            self.torch.cuda.synchronize(0)

    def _validate_job(self, request: dict[str, Any]) -> list[Path]:
        if request.get("schema_version") != REQUEST_SCHEMA:
            raise WorkerError("E_YOLO_PROTOCOL", "unsupported combined request schema")
        images = [Path(value).resolve() for value in request.get("images", [])]
        if not images:
            raise WorkerError("E_YOLO_INPUT", "images must not be empty")
        missing = [str(path) for path in images if not path.is_file()]
        if missing:
            raise WorkerError("E_YOLO_INPUT", f"source images missing: {missing}")
        for key, expected in (
            ("device", self.device),
            ("half", self.half),
            ("imgsz", self.imgsz),
            ("batch_size", len(images)),
        ):
            if request.get(key) != expected:
                raise WorkerError("E_YOLO_CONFIG", f"job {key} differs from resident runtime")
        if float(request.get("confidence")) != self.confidence:
            raise WorkerError("E_YOLO_CONFIG", "job confidence differs from resident runtime")
        requested_models = request.get("models") or {}
        for task in ("detect", "segment"):
            identity = self.model_identity[task]
            supplied = requested_models.get(task) or {}
            if str(Path(supplied.get("path", "")).resolve()).casefold() != str(identity["path"]).casefold():
                raise WorkerError("E_YOLO_MODEL_IDENTITY", f"{task} model path differs from runtime")
            if str(supplied.get("sha256", "")).lower() != identity["sha256"]:
                raise WorkerError("E_YOLO_MODEL_IDENTITY", f"{task} model SHA differs from runtime")
        return images

    def _run_task(
        self,
        task: str,
        images: list[Path],
        output_dir: Path,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        if self.require_cuda:
            self.torch.cuda.reset_peak_memory_stats(0)
        predict_started = time.perf_counter()
        results, actual_sub_batches, oom_fallback_reason = predict_batch(
            self.models[task], images, request, self.torch
        )
        if self.require_cuda:
            self.torch.cuda.synchronize(0)
        predict_ms = (time.perf_counter() - predict_started) * 1000.0
        if len(results) != len(images):
            raise WorkerError(
                "E_YOLO_OUTPUT_COUNT",
                f"output count mismatch: expected {len(images)}, actual {len(results)}",
            )
        serialize_started = time.perf_counter()
        outputs: list[dict[str, Any]] = []
        model = self.models[task]
        for batch_index, (source_path, result) in enumerate(zip(images, results, strict=True)):
            rgb = self.np.asarray(self.Image.open(source_path).convert("RGB"))
            if rgb.ndim != 3 or rgb.shape[2] != 3:
                raise ValueError(f"source must be RGB, got {rgb.shape}")
            height, width = rgb.shape[:2]
            instances: list[dict[str, Any]] = []
            masks: dict[str, Any] = {}
            boxes = result.boxes
            mask_data = (
                result.masks.data.detach().cpu().numpy()
                if task == "segment" and result.masks is not None
                else None
            )
            if boxes is not None:
                xyxy = boxes.xyxy.detach().cpu().numpy()
                classes = boxes.cls.detach().cpu().numpy().astype(int)
                confs = boxes.conf.detach().cpu().numpy()
                for index, (bbox, class_id, confidence) in enumerate(zip(xyxy, classes, confs, strict=True)):
                    clipped = [
                        float(self.np.clip(bbox[0], 0, width)),
                        float(self.np.clip(bbox[1], 0, height)),
                        float(self.np.clip(bbox[2], 0, width)),
                        float(self.np.clip(bbox[3], 0, height)),
                    ]
                    key = f"instance_{index:03d}" if task == "segment" else None
                    if key is not None:
                        if mask_data is None:
                            raise WorkerError("E_YOLO_MASK_MISSING", "segment result has boxes but no masks")
                        mask = resize_nearest(mask_data[index] > 0.5, (height, width), self.np, self.Image)
                        x1, y1, x2, y2 = (
                            int(self.np.floor(clipped[0])),
                            int(self.np.floor(clipped[1])),
                            int(self.np.ceil(clipped[2])),
                            int(self.np.ceil(clipped[3])),
                        )
                        bounded = self.np.zeros((height, width), dtype=bool)
                        bounded[max(0, y1):min(height, y2), max(0, x1):min(width, x2)] = mask[
                            max(0, y1):min(height, y2), max(0, x1):min(width, x2)
                        ]
                        if not bounded.any():
                            continue
                        masks[key] = bounded
                    instances.append(
                        {
                            "instance_id": len(instances),
                            "class_id": int(class_id),
                            "class_name": model.names[int(class_id)],
                            "confidence": float(confidence),
                            "bbox_xyxy": clipped,
                            "bbox_normalized": [
                                clipped[0] / width,
                                clipped[1] / height,
                                clipped[2] / width,
                                clipped[3] / height,
                            ],
                            "mask_key": key,
                        }
                    )
            stem = f"{batch_index:03d}_{source_path.stem}"
            json_path = output_dir / f"{stem}_{task}.json"
            npz_path = output_dir / f"{stem}_{task}_masks.npz" if task == "segment" else None
            document = {
                "schema_version": SCHEMA,
                "model": self.model_meta[task],
                "image": {"path": str(source_path), "width": width, "height": height},
                "instances": instances,
                "warnings": [],
            }
            json_path.write_text(
                json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            if npz_path is not None:
                self.np.savez_compressed(npz_path, **masks)
            outputs.append(
                {
                    "source_path": str(source_path),
                    "batch_index": batch_index,
                    "json": str(json_path),
                    "npz": str(npz_path) if npz_path else None,
                }
            )
        serialize_ms = (time.perf_counter() - serialize_started) * 1000.0
        gpu_peak_memory_mb = (
            float(self.torch.cuda.max_memory_allocated(0)) / (1024.0 * 1024.0)
            if self.require_cuda
            else 0.0
        )
        timing = {
            "worker_process_start_ms": (time.perf_counter() - SCRIPT_STARTED) * 1000.0,
            "imports_ms": self.imports_ms if task == "detect" else 0.0,
            "model_hash_ms": self.model_timing[task]["model_hash_ms"],
            "model_load_ms": self.model_timing[task]["model_load_ms"],
            "warmup_ms": self.warmup_ms if task == "detect" else 0.0,
            "warmup_mode": self.warmup_mode,
            "predict_ms": None,
            "postprocess_nms_ms": None,
            "predict_and_postprocess_ms": predict_ms,
            "serialize_ms": serialize_ms,
            "gpu_peak_memory_mb": gpu_peak_memory_mb,
        }
        metadata = {
            "python_version": platform.python_version(),
            "python_executable": sys.executable,
            "torch_version": self.torch.__version__,
            "torchvision_version": self.torchvision.__version__,
            "ultralytics_version": self.ultralytics.__version__,
            "numpy_version": self.np.__version__,
            "pillow_version": self.Image.__version__,
            "cuda_runtime": self.torch.version.cuda,
            "cuda_available": self.cuda_available,
            "gpu_name": self.gpu_name,
            "device": self.device,
            "actual_model_device": str(next(model.model.parameters()).device),
            "precision": "FP16" if self.half else "FP32",
            "half": self.half,
            "imgsz": self.imgsz,
            "requested_batch_size": len(images),
            "input_count": len(images),
            "output_count": len(outputs),
            "model_load_count": 1,
            "predict_call_count": len(actual_sub_batches),
            "required_class_ids": self.required_class_ids,
            "required_class_ids_verified": True,
        }
        return {
            "outputs": outputs,
            "metadata": metadata,
            "timing": timing,
            "actual_sub_batches": actual_sub_batches,
            "oom_fallback_reason": oom_fallback_reason,
        }

    def run_job(self, request: dict[str, Any]) -> dict[str, Any]:
        images = self._validate_job(request)
        injection = request.get("test_injection")
        job_started = time.perf_counter()
        task_results: dict[str, dict[str, Any]] = {}
        for task in ("detect", "segment"):
            if injection == f"crash_{task}":
                os._exit(97)
            task_results[task] = self._run_task(
                task,
                images,
                Path(request["output_dirs"][task]).resolve(),
                {**self._base_request(), "batch_size": len(images)},
            )
        self.jobs_completed += 1
        return {
            "schema_version": RESPONSE_SCHEMA,
            "status": "success",
            "job_id": request.get("job_id"),
            "scan_id": request.get("scan_id"),
            "tasks": task_results,
            "runtime": {
                "pid": os.getpid(),
                "jobs_completed": self.jobs_completed,
                "model_identity": self.model_identity,
                "job_total_ms": (time.perf_counter() - job_started) * 1000.0,
            },
        }


def load_startup(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "models": request["models"],
        "device": request.get("device", "cuda:0"),
        "confidence": request.get("confidence", 0.25),
        "imgsz": request.get("imgsz", 640),
        "batch_size": request.get("batch_size", len(request.get("images", [])) or 11),
        "half": request.get("half", False),
        "require_cuda": request.get("require_cuda", True),
        "expected_gpu_name": request.get("expected_gpu_name"),
        "required_class_ids": request.get("required_class_ids", []),
    }


def error_payload(exc: BaseException) -> dict[str, Any]:
    return {
        "status": "rejected",
        "error_code": exc.code if isinstance(exc, WorkerError) else "E_YOLO_WORKER",
        "message": exc.message if isinstance(exc, WorkerError) else f"{type(exc).__name__}: {exc}",
    }


def one_shot(path: Path) -> int:
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
        runtime = Runtime(load_startup(request), warmup=False)
        print(json.dumps(runtime.run_job(request), ensure_ascii=False), file=PROTOCOL_STDOUT, flush=True)
        return 0
    except BaseException as exc:
        print(json.dumps(error_payload(exc), ensure_ascii=False), file=sys.stderr, flush=True)
        return 2


def serve(path: Path) -> int:
    try:
        startup = json.loads(path.read_text(encoding="utf-8"))
        runtime = Runtime(startup, warmup=True)
        ready_total_ms = (time.perf_counter() - SCRIPT_STARTED) * 1000.0
        print(
            json.dumps(
                {
                    "schema_version": READY_SCHEMA,
                    "status": "READY",
                    "pid": os.getpid(),
                    "timing": {
                        "process_start_ms": ready_total_ms,
                        "imports_ms": runtime.imports_ms,
                        "cuda_init_ms": None,
                        "detect_model_load_ms": runtime.model_timing["detect"]["model_load_ms"],
                        "segment_model_load_ms": runtime.model_timing["segment"]["model_load_ms"],
                        "warmup_ms": runtime.warmup_ms,
                        "runtime_ready_total_ms": ready_total_ms,
                    },
                    "model_identity": runtime.model_identity,
                },
                ensure_ascii=False,
            ),
            file=PROTOCOL_STDOUT,
            flush=True,
        )
        for line in sys.stdin:
            try:
                command = json.loads(line)
                if command.get("schema_version") != COMMAND_SCHEMA:
                    raise WorkerError("E_YOLO_PROTOCOL", "unsupported runtime command schema")
                if command.get("command") == "ping":
                    response = {"schema_version": RESPONSE_SCHEMA, "status": "pong", "pid": os.getpid()}
                elif command.get("command") == "shutdown":
                    response = {"schema_version": RESPONSE_SCHEMA, "status": "stopped", "pid": os.getpid()}
                    print(json.dumps(response), file=PROTOCOL_STDOUT, flush=True)
                    return 0
                elif command.get("command") == "job":
                    response = runtime.run_job(command["request"])
                else:
                    raise WorkerError("E_YOLO_PROTOCOL", "unknown runtime command")
            except BaseException as exc:
                response = error_payload(exc)
            print(json.dumps(response, ensure_ascii=False), file=PROTOCOL_STDOUT, flush=True)
        return 0
    except BaseException as exc:
        print(json.dumps(error_payload(exc), ensure_ascii=False), file=sys.stderr, flush=True)
        return 2


def main() -> int:
    sys.stdout = sys.stderr
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--request", type=Path)
    group.add_argument("--serve", type=Path)
    args = parser.parse_args()
    return one_shot(args.request) if args.request is not None else serve(args.serve)


if __name__ == "__main__":
    raise SystemExit(main())
