from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def depth_stats(depth: np.ndarray) -> dict[str, Any]:
    finite = np.isfinite(depth)
    return {
        "shape": list(depth.shape),
        "dtype": str(depth.dtype),
        "unit": "m",
        "finite_ratio": float(finite.mean()),
        "positive_ratio": float((depth[finite] > 0).mean()) if finite.any() else 0.0,
        "min": float(depth[finite].min()) if finite.any() else None,
        "median": float(np.median(depth[finite])) if finite.any() else None,
        "max": float(depth[finite].max()) if finite.any() else None,
    }


def quantize_depth_mm(depth: np.ndarray, valid: np.ndarray | None = None) -> np.ndarray:
    finite = np.isfinite(depth) & (depth > 0)
    if valid is not None:
        finite &= valid
    values = np.zeros(depth.shape, dtype=np.uint16)
    values[finite] = np.clip(np.rint(depth[finite] * 1000.0), 1, 65535).astype(np.uint16)
    return values


def validate_depth_png(depth: np.ndarray, png_path: Path, valid: np.ndarray | None = None) -> dict[str, Any]:
    expected = quantize_depth_mm(depth, valid)
    with Image.open(png_path) as image:
        png_mode = image.mode
        actual = np.asarray(image).copy()
    delta = np.abs(actual.astype(np.int64) - expected.astype(np.int64))
    return {
        "png_mode": png_mode,
        "png_dtype": str(actual.dtype),
        "exact_quantized_match": bool(np.array_equal(expected, actual)),
        "max_quantization_difference_mm": int(delta.max()),
        "rule": "round(depth_m*1000), clip valid positive finite to 1..65535; invalid=0",
    }


def compare_depth(reference: np.ndarray, candidate: np.ndarray) -> dict[str, Any]:
    if reference.shape != candidate.shape:
        return {"comparable": False, "reason": "shape mismatch", "reference_shape": list(reference.shape), "candidate_shape": list(candidate.shape)}
    valid = np.isfinite(reference) & np.isfinite(candidate) & (reference > 0) & (candidate > 0)
    if not valid.any():
        return {"comparable": False, "reason": "no valid overlap", "valid_overlap": 0.0}
    delta = np.abs(reference[valid].astype(np.float64) - candidate[valid].astype(np.float64))
    return {
        "comparable": True,
        "interpretation": "MODEL_SENSITIVITY_NOT_REAL_WORLD_ACCURACY",
        "valid_overlap": float(valid.mean()),
        "mae_m": float(delta.mean()),
        "median_absolute_difference_m": float(np.median(delta)),
        "p90_m": float(np.percentile(delta, 90)),
        "p95_m": float(np.percentile(delta, 95)),
        "max_m": float(delta.max()),
        "reference_range_m": [float(reference[valid].min()), float(reference[valid].max())],
        "candidate_range_m": [float(candidate[valid].min()), float(candidate[valid].max())],
    }
