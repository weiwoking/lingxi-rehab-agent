import hashlib
import json
import sys
import time
import traceback
from io import BytesIO
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image


ROOT = Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128")
EVIDENCE = Path(r"C:\ec\_deltar_formal\evidence")
OUTPUT = Path(r"C:\ec\_deltar_formal\outputs\smoke")
RESULT_PATH = EVIDENCE / "smoke_perf.json"
sys.path.insert(0, str(ROOT))
sys.argv = ["run_smoke_perf.py", str(ROOT / "configs" / "test_demo_compat.txt")]

from src.config import args
from src.dataloader.zjuL5 import ZJUL5
from src.models.deltar import Deltar


def gpu_memory():
    return {
        "allocated_bytes": torch.cuda.memory_allocated(),
        "reserved_bytes": torch.cuda.memory_reserved(),
        "max_allocated_bytes": torch.cuda.max_memory_allocated(),
        "max_reserved_bytes": torch.cuda.max_memory_reserved(),
    }


def shape_dtype(value):
    return {"shape": list(value.shape), "dtype": str(value.dtype), "device": str(value.device)}


def percentile_summary(values):
    array = np.asarray(values, dtype=np.float64)
    return {
        "count": int(array.size),
        "mean_ms": float(array.mean()),
        "median_ms": float(np.median(array)),
        "p95_ms": float(np.percentile(array, 95)),
        "min_ms": float(array.min()),
        "max_ms": float(array.max()),
    }


def official_postprocess(prediction):
    clipped = np.clip(prediction.detach().cpu().numpy(), args.min_depth, args.max_depth)
    resized = F.interpolate(
        torch.tensor(clipped),
        (args.input_height, args.input_width),
        mode="bilinear",
        align_corners=True,
    )
    return resized.squeeze().numpy().astype(np.float32, copy=False)


def png_sha(array_mm):
    buffer = BytesIO()
    Image.fromarray(array_mm).save(buffer, format="PNG")
    return hashlib.sha256(buffer.getvalue()).hexdigest()


result = {
    "status": "started",
    "worktree": str(ROOT),
    "config": str(ROOT / "configs" / "test_demo_compat.txt"),
    "precision": "float32 (no autocast)",
    "warmup_iterations": 3,
    "measured_iterations": 10,
}

try:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable")
    device = torch.device("cuda:0")
    torch.manual_seed(20260717)
    np.random.seed(20260717)
    torch.backends.cudnn.benchmark = False
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()

    build_start = time.perf_counter()
    model = Deltar(n_bins=args.n_bins, min_val=args.min_depth, max_val=args.max_depth, norm="linear")
    result["model_build_seconds"] = time.perf_counter() - build_start
    result["parameters"] = {
        "total": sum(parameter.numel() for parameter in model.parameters()),
        "trainable": sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad),
    }

    weight_path = ROOT / args.weight_path
    load_start = time.perf_counter()
    state_dict = torch.load(weight_path, map_location="cpu", weights_only=True)
    if not isinstance(state_dict, dict):
        raise TypeError(f"Expected state dict, got {type(state_dict).__name__}")
    incompat = model.load_state_dict(state_dict, strict=False)
    result["checkpoint"] = {
        "path": str(weight_path),
        "bytes": weight_path.stat().st_size,
        "top_level_type": type(state_dict).__name__,
        "tensor_key_count": sum(torch.is_tensor(value) for value in state_dict.values()),
        "missing_keys": list(incompat.missing_keys),
        "unexpected_keys": list(incompat.unexpected_keys),
        "load_seconds": time.perf_counter() - load_start,
        "safe_load": "torch.load(map_location='cpu', weights_only=True)",
    }
    if incompat.missing_keys or incompat.unexpected_keys:
        raise RuntimeError("Checkpoint keys do not exactly match model")

    model = model.to(device).eval()
    result["gpu_after_model"] = gpu_memory()

    loader = ZJUL5(args, "online_eval").data
    result["dataset_sample_count"] = len(loader.dataset)
    batch = next(iter(loader))
    additional = {
        "hist_data": batch["additional"]["hist_data"].to(device),
        "rect_data": batch["additional"]["rect_data"].to(device),
        "mask": batch["additional"]["mask"].to(device),
        "patch_info": batch["additional"]["patch_info"],
    }
    input_data = {"rgb": batch["image"].to(device), "additional": additional}
    result["first_sample"] = {
        "image_path": batch["image_path"][0],
        "image_folder": batch["image_folder"][0],
        "input_rgb": shape_dtype(input_data["rgb"]),
        "input_hist_data": shape_dtype(additional["hist_data"]),
        "input_rect_data": shape_dtype(additional["rect_data"]),
        "input_mask": shape_dtype(additional["mask"]),
        "ground_truth": shape_dtype(batch["depth"]),
    }

    with torch.inference_mode():
        for _ in range(result["warmup_iterations"]):
            bin_edges, raw_prediction = model(input_data)
        torch.cuda.synchronize()

        forward_ms = []
        end_to_end_ms = []
        processed = []
        for _ in range(result["measured_iterations"]):
            start = time.perf_counter()
            bin_edges, raw_prediction = model(input_data)
            torch.cuda.synchronize()
            forward_end = time.perf_counter()
            dense_depth_m = official_postprocess(raw_prediction)
            end = time.perf_counter()
            forward_ms.append((forward_end - start) * 1000.0)
            end_to_end_ms.append((end - start) * 1000.0)
            if len(processed) < 2:
                processed.append(dense_depth_m.copy())

    first, second = processed
    depth_mm = np.clip(first * 1000.0, 0, np.iinfo(np.uint16).max).astype(np.uint16)
    output_dir = OUTPUT / batch["image_folder"][0]
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(batch["image_path"][0]).stem
    float_path = output_dir / f"{stem}_depth_m.npy"
    png_path = output_dir / f"{stem}_depth_mm.png"
    np.save(float_path, first)
    Image.fromarray(depth_mm).save(png_path)

    difference = np.abs(first - second)
    second_mm = np.clip(second * 1000.0, 0, np.iinfo(np.uint16).max).astype(np.uint16)
    result.update(
        {
            "raw_outputs": {
                "bin_edges": shape_dtype(bin_edges),
                "prediction": shape_dtype(raw_prediction),
            },
            "dense_depth_m": {
                "path": str(float_path),
                "shape": list(first.shape),
                "dtype": str(first.dtype),
                "finite_ratio": float(np.isfinite(first).mean()),
                "positive_ratio": float((first > 0).mean()),
                "min": float(np.min(first)),
                "median": float(np.median(first)),
                "max": float(np.max(first)),
                "sha256_raw": hashlib.sha256(first.tobytes()).hexdigest(),
            },
            "depth_mm_png": {
                "path": str(png_path),
                "shape": list(depth_mm.shape),
                "dtype": str(depth_mm.dtype),
                "sha256_file": hashlib.sha256(png_path.read_bytes()).hexdigest(),
            },
            "repeatability_first_two_measured": {
                "float_exact_equal": bool(np.array_equal(first, second)),
                "float_max_abs_diff_m": float(difference.max()),
                "float_mean_abs_diff_m": float(difference.mean()),
                "float_sha256_first": hashlib.sha256(first.tobytes()).hexdigest(),
                "float_sha256_second": hashlib.sha256(second.tobytes()).hexdigest(),
                "uint16_exact_equal": bool(np.array_equal(depth_mm, second_mm)),
                "png_sha256_first": png_sha(depth_mm),
                "png_sha256_second": png_sha(second_mm),
            },
            "timing": {
                "pure_model_forward": percentile_summary(forward_ms),
                "model_plus_official_cpu_postprocess": percentile_summary(end_to_end_ms),
                "raw_forward_ms": forward_ms,
                "raw_end_to_end_ms": end_to_end_ms,
            },
            "gpu_peak": gpu_memory(),
            "status": "success",
        }
    )
except Exception as exc:
    result.update(
        {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    )
finally:
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))

if result["status"] != "success":
    raise SystemExit(1)
