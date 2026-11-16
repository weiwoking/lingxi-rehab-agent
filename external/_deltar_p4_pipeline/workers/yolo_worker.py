from __future__ import annotations

import time

SCRIPT_STARTED = time.perf_counter()

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path
from typing import Any

SCHEMA = "deltar_stage3_yolo.v1"
RESPONSE_SCHEMA = "deltar_stage8b1_yolo_worker_response.v1"


class WorkerError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resize_nearest(mask: Any, shape: tuple[int, int], np: Any, Image: Any) -> Any:
    if mask.shape != shape:
        mask = np.asarray(
            Image.fromarray(mask.astype(np.uint8), mode="L").resize(
                (shape[1], shape[0]), Image.Resampling.NEAREST
            )
        )
    return mask.astype(bool)


def is_cuda_oom(exc: BaseException, torch: Any) -> bool:
    oom_type = getattr(getattr(torch, "cuda", None), "OutOfMemoryError", ())
    return isinstance(exc, oom_type) or "out of memory" in str(exc).casefold()


def predict_batch(
    model: Any,
    image_paths: list[Path],
    request: dict[str, Any],
    torch: Any,
) -> tuple[list[Any], list[int], str | None]:
    requested_batch_size = int(request.get("batch_size", len(image_paths)))
    kwargs = {
        "imgsz": int(request.get("imgsz", 640)),
        "conf": float(request.get("confidence", 0.25)),
        "device": str(request.get("device", "cpu")),
        "half": bool(request.get("half", False)),
        "verbose": False,
        "stream": False,
    }
    try:
        results = model.predict(
            source=[str(path) for path in image_paths],
            batch=requested_batch_size,
            **kwargs,
        )
        return list(results), [len(image_paths)], None
    except BaseException as exc:
        if (
            not is_cuda_oom(exc, torch)
            or not bool(request.get("require_cuda", False))
            or len(image_paths) != 11
        ):
            raise
        torch.cuda.empty_cache()
        results: list[Any] = []
        try:
            for start, size in ((0, 6), (6, 5)):
                chunk = image_paths[start : start + size]
                results.extend(
                    model.predict(
                        source=[str(path) for path in chunk],
                        batch=size,
                        **kwargs,
                    )
                )
        except BaseException as fallback_exc:
            if is_cuda_oom(fallback_exc, torch):
                raise WorkerError(
                    "E_YOLO_OOM",
                    "CUDA OOM for batch=11 and fallback sub-batches 6+5",
                ) from fallback_exc
            raise
        return results, [6, 5], f"batch=11 CUDA OOM: {type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    try:
        process_start_ms = (time.perf_counter() - SCRIPT_STARTED) * 1000.0
        request = json.loads(args.request.read_text(encoding="utf-8"))
        if request.get("schema_version") != "deltar_stage3_1_yolo_worker_request.v1":
            raise ValueError("unsupported request schema")
        model_path = Path(request["model_path"]).resolve()
        task = request["expected_task"]
        if task not in {"detect", "segment"}:
            raise WorkerError("E_YOLO_TASK", "expected_task must be detect or segment")
        if not model_path.is_file():
            raise WorkerError(
                "E_YOLO_MODEL_MISSING", f"YOLO model is missing: {model_path}"
            )
        hash_started = time.perf_counter()
        actual_sha256 = sha256(model_path)
        model_hash_ms = (time.perf_counter() - hash_started) * 1000.0
        expected_sha256 = str(request["expected_sha256"]).lower()
        if actual_sha256.lower() != expected_sha256:
            raise WorkerError(
                "E_YOLO_MODEL_HASH",
                "YOLO model SHA-256 mismatch: "
                f"expected {expected_sha256}, actual {actual_sha256}",
            )

        imports_started = time.perf_counter()
        import numpy as np
        from PIL import Image
        import torch
        import torchvision
        import ultralytics
        from ultralytics import YOLO

        imports_ms = (time.perf_counter() - imports_started) * 1000.0
        device = str(request.get("device", "cpu"))
        require_cuda = bool(request.get("require_cuda", False))
        half = bool(request.get("half", False))
        if require_cuda and device != "cuda:0":
            raise WorkerError(
                "E_YOLO_DEVICE", "Stage 8B-1 requires exactly device=cuda:0"
            )
        if require_cuda and half:
            raise WorkerError("E_YOLO_PRECISION", "Stage 8B-1 requires FP32")
        cuda_available = bool(torch.cuda.is_available())
        if require_cuda and not cuda_available:
            raise WorkerError("E_YOLO_CUDA_UNAVAILABLE", "CUDA is unavailable")
        gpu_name = torch.cuda.get_device_name(0) if cuda_available else None
        expected_gpu_name = request.get("expected_gpu_name")
        if (
            require_cuda
            and expected_gpu_name
            and str(expected_gpu_name).casefold() not in str(gpu_name).casefold()
        ):
            raise WorkerError(
                "E_YOLO_GPU_MISMATCH",
                f"GPU mismatch: expected {expected_gpu_name!r}, actual {gpu_name!r}",
            )
        if require_cuda:
            torch.cuda.set_device(0)
            torch.cuda.reset_peak_memory_stats(0)

        model_load_started = time.perf_counter()
        model = YOLO(str(model_path), task=task)
        if str(model.task) != task:
            raise WorkerError(
                "E_YOLO_TASK", f"loaded task {model.task!r}, expected {task!r}"
            )
        if require_cuda:
            model.to(device)
            model.model.float()
        model_load_ms = (time.perf_counter() - model_load_started) * 1000.0
        required_class_ids = [int(value) for value in request.get("required_class_ids", [])]
        missing_class_ids = [
            class_id for class_id in required_class_ids if class_id not in model.names
        ]
        if missing_class_ids:
            raise WorkerError(
                "E_YOLO_CLASS_METADATA",
                f"required class ids missing: {missing_class_ids}",
            )

        image_paths = [Path(value).resolve() for value in request["images"]]
        if not image_paths:
            raise WorkerError("E_YOLO_INPUT", "images must not be empty")
        missing_images = [str(path) for path in image_paths if not path.is_file()]
        if missing_images:
            raise WorkerError(
                "E_YOLO_INPUT", f"source images missing: {missing_images}"
            )
        output_dir = Path(request["output_dir"]).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        predict_started = time.perf_counter()
        results, actual_sub_batches, oom_fallback_reason = predict_batch(
            model, image_paths, request, torch
        )
        if require_cuda:
            torch.cuda.synchronize(0)
        predict_and_postprocess_ms = (time.perf_counter() - predict_started) * 1000.0
        actual_model_device = str(next(model.model.parameters()).device)
        if require_cuda and actual_model_device != "cuda:0":
            raise WorkerError(
                "E_YOLO_DEVICE",
                f"model parameters are on {actual_model_device}, expected cuda:0",
            )
        if len(results) != len(image_paths):
            raise WorkerError(
                "E_YOLO_OUTPUT_COUNT",
                f"output count mismatch: expected {len(image_paths)}, actual {len(results)}",
            )

        model_meta = {
            "path": str(model_path),
            "sha256": actual_sha256,
            "expected_sha256": expected_sha256,
            "hash_verified": True,
            "size_bytes": model_path.stat().st_size,
            "task": task,
            "expected_task": task,
            "task_verified": True,
            "device": device,
            "actual_model_device": actual_model_device,
            "precision": "FP16" if half else "FP32",
            "half": half,
            "ultralytics_version": ultralytics.__version__,
            "torch_version": torch.__version__,
            "class_names": {str(k): v for k, v in model.names.items()},
        }
        serialize_started = time.perf_counter()
        outputs = []
        for batch_index, (source_path, result) in enumerate(
            zip(image_paths, results, strict=True)
        ):
            rgb = np.asarray(Image.open(source_path).convert("RGB"))
            if rgb.ndim != 3 or rgb.shape[2] != 3:
                raise ValueError(f"source must be RGB, got {rgb.shape}")
            height, width = rgb.shape[:2]
            instances = []
            masks = {}
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
                for index, (bbox, class_id, confidence) in enumerate(
                    zip(xyxy, classes, confs, strict=True)
                ):
                    clipped = [
                        float(np.clip(bbox[0], 0, width)),
                        float(np.clip(bbox[1], 0, height)),
                        float(np.clip(bbox[2], 0, width)),
                        float(np.clip(bbox[3], 0, height)),
                    ]
                    key = f"instance_{index:03d}" if task == "segment" else None
                    if key is not None:
                        if mask_data is None:
                            raise WorkerError(
                                "E_YOLO_MASK_MISSING",
                                "segment result has boxes but no masks",
                            )
                        mask = resize_nearest(
                            mask_data[index] > 0.5, (height, width), np, Image
                        )
                        x1, y1, x2, y2 = (
                            int(np.floor(clipped[0])),
                            int(np.floor(clipped[1])),
                            int(np.ceil(clipped[2])),
                            int(np.ceil(clipped[3])),
                        )
                        bounded = np.zeros((height, width), dtype=bool)
                        bounded[
                            max(0, y1) : min(height, y2),
                            max(0, x1) : min(width, x2),
                        ] = mask[
                            max(0, y1) : min(height, y2),
                            max(0, x1) : min(width, x2),
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
            npz_path = (
                output_dir / f"{stem}_{task}_masks.npz"
                if task == "segment"
                else None
            )
            document = {
                "schema_version": SCHEMA,
                "model": model_meta,
                "image": {
                    "path": str(source_path),
                    "width": width,
                    "height": height,
                },
                "instances": instances,
                "warnings": [],
            }
            json_path.write_text(
                json.dumps(
                    document, indent=2, ensure_ascii=False, allow_nan=False
                )
                + "\n",
                encoding="utf-8",
            )
            if npz_path is not None:
                np.savez_compressed(npz_path, **masks)
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
            float(torch.cuda.max_memory_allocated(0)) / (1024.0 * 1024.0)
            if require_cuda
            else 0.0
        )
        timing = {
            "worker_process_start_ms": process_start_ms,
            "imports_ms": imports_ms,
            "model_hash_ms": model_hash_ms,
            "model_load_ms": model_load_ms,
            "warmup_ms": 0.0,
            "warmup_mode": "implicit_first_predict_included",
            "predict_ms": None,
            "postprocess_nms_ms": None,
            "predict_and_postprocess_ms": predict_and_postprocess_ms,
            "serialize_ms": serialize_ms,
            "gpu_peak_memory_mb": gpu_peak_memory_mb,
        }
        metadata = {
            "python_version": platform.python_version(),
            "python_executable": sys.executable,
            "torch_version": torch.__version__,
            "torchvision_version": torchvision.__version__,
            "ultralytics_version": ultralytics.__version__,
            "numpy_version": np.__version__,
            "pillow_version": Image.__version__,
            "cuda_runtime": torch.version.cuda,
            "cuda_available": cuda_available,
            "gpu_name": gpu_name,
            "device": device,
            "actual_model_device": actual_model_device,
            "precision": "FP16" if half else "FP32",
            "half": half,
            "imgsz": int(request.get("imgsz", 640)),
            "requested_batch_size": int(
                request.get("batch_size", len(image_paths))
            ),
            "input_count": len(image_paths),
            "output_count": len(outputs),
            "model_load_count": 1,
            "predict_call_count": len(actual_sub_batches),
            "required_class_ids": required_class_ids,
            "required_class_ids_verified": not missing_class_ids,
        }
        print(
            json.dumps(
                {
                    "schema_version": RESPONSE_SCHEMA,
                    "outputs": outputs,
                    "metadata": metadata,
                    "timing": timing,
                    "actual_sub_batches": actual_sub_batches,
                    "oom_fallback_reason": oom_fallback_reason,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except WorkerError as exc:
        print(
            json.dumps(
                {
                    "status": "rejected",
                    "error_code": exc.code,
                    "message": exc.message,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "rejected",
                    "error_code": "E_YOLO_WORKER",
                    "message": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
