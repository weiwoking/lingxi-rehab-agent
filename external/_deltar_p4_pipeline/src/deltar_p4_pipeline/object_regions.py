from __future__ import annotations

from typing import Any

import numpy as np

from .depth_statistics import select_peak_mask
from .errors import ErrorCode, PipelineError
from .yolo_contracts import SOURCE_SHAPE, bbox_to_slices


def bbox_mask(bbox: list[float], shape: tuple[int, int] = SOURCE_SHAPE) -> np.ndarray:
    output = np.zeros(shape, dtype=bool)
    ys, xs = bbox_to_slices(bbox, shape[1], shape[0])
    output[ys, xs] = True
    return output


def bbox_center_mask(bbox: list[float], fraction: float = 0.5, shape: tuple[int, int] = SOURCE_SHAPE) -> np.ndarray:
    if not 0 < fraction <= 1:
        raise ValueError("center fraction must be in (0,1]")
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    width, height = (x2 - x1) * fraction, (y2 - y1) * fraction
    return bbox_mask([cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2], shape)


def binary_erosion(mask: np.ndarray, iterations: int = 1) -> np.ndarray:
    output = np.asarray(mask, dtype=bool).copy()
    for _ in range(iterations):
        padded = np.pad(output, 1, constant_values=False)
        neighbors = [padded[dy:dy + output.shape[0], dx:dx + output.shape[1]] for dy in range(3) for dx in range(3)]
        output = np.logical_and.reduce(neighbors)
    return output


def boundary_band(mask: np.ndarray, pixels: int = 1) -> np.ndarray:
    arr = np.asarray(mask, dtype=bool)
    return arr & ~binary_erosion(arr, pixels)


def _record(mask: np.ndarray, valid_depth: np.ndarray, *, min_pixels: int) -> dict[str, Any]:
    raw_count = int(mask.sum())
    if raw_count == 0:
        raise PipelineError(ErrorCode.OBJECT_REGION_EMPTY, "object region is empty", "region")
    valid = mask & valid_depth
    valid_count = int(valid.sum())
    if valid_count < min_pixels:
        raise PipelineError(ErrorCode.OBJECT_REGION_TOO_SMALL, f"object region has fewer than {min_pixels} valid pixels", "region")
    return {"mask": valid, "pixel_count": raw_count, "valid_pixel_count": valid_count, "valid_ratio": float(valid_count / raw_count)}


def build_regions(bbox: list[float], depth: np.ndarray, valid_mask: np.ndarray | None = None,
                  segment_mask: np.ndarray | None = None, *, center_fraction: float = 0.5,
                  erosion_pixels: tuple[int, ...] = (1, 2, 3), min_pixels: int = 8,
                  peak_bin_width_m: float = 0.10, peak_min_support: float = 0.08) -> dict[str, dict[str, Any]]:
    if depth.shape != SOURCE_SHAPE:
        raise PipelineError(ErrorCode.YOLO_MASK_SHAPE, "source depth must have shape 360x640", "depth")
    valid = np.isfinite(depth) & (depth > 0)
    if valid_mask is not None:
        if valid_mask.shape != SOURCE_SHAPE:
            raise PipelineError(ErrorCode.YOLO_MASK_SHAPE, "source valid mask must have shape 360x640", "valid_mask")
        valid &= valid_mask
    base: dict[str, np.ndarray] = {"bbox_full": bbox_mask(bbox), "bbox_center": bbox_center_mask(bbox, center_fraction)}
    if segment_mask is not None:
        if segment_mask.shape != SOURCE_SHAPE:
            raise PipelineError(ErrorCode.YOLO_MASK_SHAPE, "segment mask must have shape 360x640", "segment_mask")
        base["segment_mask"] = segment_mask.astype(bool, copy=False)
        for pixels in erosion_pixels:
            base[f"segment_eroded_{pixels}px"] = binary_erosion(segment_mask, pixels)
    output: dict[str, dict[str, Any]] = {}
    for name, mask in base.items():
        try:
            output[name] = _record(mask, valid, min_pixels=min_pixels)
        except PipelineError as exc:
            output[name] = {"mask": np.zeros(SOURCE_SHAPE, dtype=bool), "pixel_count": int(mask.sum()),
                            "valid_pixel_count": 0, "valid_ratio": 0.0, "status": "rejected", "error": exc.as_dict()}
    cluster_inputs = ["bbox_full", "segment_mask"] + [f"segment_eroded_{p}px" for p in erosion_pixels]
    for parent in cluster_inputs:
        if parent not in output or output[parent].get("status") == "rejected":
            continue
        name = "bbox_depth_cluster" if parent == "bbox_full" else ("segment_depth_cluster" if parent == "segment_mask" else f"{parent}_depth_cluster")
        try:
            selected, cluster = select_peak_mask(depth, output[parent]["mask"], bin_width_m=peak_bin_width_m,
                                                  min_support=peak_min_support, min_pixels=min_pixels)
            output[name] = {**_record(selected, valid, min_pixels=min_pixels), "cluster": cluster, "parent_region": parent}
        except PipelineError as exc:
            output[name] = {"mask": np.zeros(SOURCE_SHAPE, dtype=bool), "pixel_count": 0, "valid_pixel_count": 0,
                            "valid_ratio": 0.0, "status": "rejected", "error": exc.as_dict(), "parent_region": parent}
    return output
