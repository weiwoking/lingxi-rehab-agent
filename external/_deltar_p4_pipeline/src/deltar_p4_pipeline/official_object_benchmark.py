from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import h5py
import numpy as np

from .depth_statistics import compute_statistics
from .object_matching import bbox_iou
from .object_regions import binary_erosion

OFFICIAL_ROOT = Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128\data\demo")
OFFICIAL_INDEX = OFFICIAL_ROOT / "data.json"
SOURCE_SHAPE = (480, 640)
MIN_DEPTH_M = 1e-3
MAX_DEPTH_M = 10.0


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def official_samples(root: Path = OFFICIAL_ROOT, index: Path = OFFICIAL_INDEX) -> list[dict[str, Any]]:
    rows = read_json(index)["test"]
    return [
        {
            "sample_index": number,
            "sample_id": Path(row["filename"]).stem,
            "relative_path": row["filename"],
            "hdf5_path": str((root / row["filename"]).resolve()),
        }
        for number, row in enumerate(rows)
    ]


def load_hdf5(sample: dict[str, Any]) -> dict[str, np.ndarray]:
    with h5py.File(sample["hdf5_path"], "r") as handle:
        return {name: handle[name][:] for name in ("rgb", "depth", "hist_data", "fr", "mask")}


def pack_mask(mask: np.ndarray) -> dict[str, Any]:
    arr = np.asarray(mask, dtype=np.uint8)
    packed = np.packbits(arr.reshape(-1), bitorder="little")
    return {
        "encoding": "numpy_packbits_little_base64",
        "shape": list(arr.shape),
        "true_count": int(arr.sum()),
        "data": base64.b64encode(packed.tobytes()).decode("ascii"),
    }


def unpack_mask(value: dict[str, Any]) -> np.ndarray:
    shape = tuple(int(item) for item in value["shape"])
    raw = np.frombuffer(base64.b64decode(value["data"]), dtype=np.uint8)
    return np.unpackbits(raw, bitorder="little", count=int(np.prod(shape))).reshape(shape).astype(bool)


def bbox_mask(bbox: list[float], shape: tuple[int, int] = SOURCE_SHAPE) -> np.ndarray:
    height, width = shape
    x1, y1, x2, y2 = bbox
    xa, ya = max(0, int(np.floor(x1))), max(0, int(np.floor(y1)))
    xb, yb = min(width, int(np.ceil(x2))), min(height, int(np.ceil(y2)))
    output = np.zeros(shape, dtype=bool)
    output[ya:yb, xa:xb] = True
    return output


def valid_depth(depth: np.ndarray) -> np.ndarray:
    return np.isfinite(depth) & (depth > MIN_DEPTH_M) & (depth < MAX_DEPTH_M)


def region_statistics(depth: np.ndarray, region: np.ndarray, *, min_pixels: int = 8) -> dict[str, Any]:
    region = np.asarray(region, dtype=bool)
    valid = region & valid_depth(depth)
    record: dict[str, Any] = {
        "pixel_count": int(region.sum()),
        "valid_count": int(valid.sum()),
        "valid_ratio": float(valid.sum() / max(region.sum(), 1)),
    }
    if int(valid.sum()) < min_pixels:
        return {**record, "status": "rejected", "rejection_reason": "too_few_valid_pixels", "warnings": []}
    try:
        stats = compute_statistics(np.asarray(depth)[valid], min_pixels=min_pixels)
    except Exception as exc:
        return {**record, "status": "rejected", "rejection_reason": type(exc).__name__, "warnings": [str(exc)]}
    return {
        **record,
        "status": "valid",
        "median_m": stats["median"],
        "mad_filtered_median_m": stats["mad_filtered_median"],
        "trimmed_mean_m": stats["trimmed_mean"],
        "dominant_peak_m": stats["dominant_peak_m"],
        "nearest_stable_surface_m": stats["nearest_stable_surface"],
        "mad_m": stats["mad_m"],
        "iqr_m": stats["iqr_m"],
        "p10_m": stats["p10"],
        "p25_m": stats["p25"],
        "p50_m": stats["p50"],
        "p75_m": stats["p75"],
        "p90_m": stats["p90"],
        "multimodal": stats["multimodal"],
        "primary_peak_m": stats["dominant_peak_m"],
        "secondary_peak_m": stats["secondary_peak_m"],
        "peak_separation_m": stats["peak_separation_m"],
        "warnings": stats["warnings"],
        "rejection_reason": None,
    }


def gt_reference(depth: np.ndarray, segment_mask: np.ndarray) -> dict[str, Any]:
    masks = {"segment_mask": segment_mask}
    for pixels in (1, 2, 3):
        masks[f"segment_eroded_{pixels}px"] = binary_erosion(segment_mask, pixels)
    regions = {name: region_statistics(depth, mask) for name, mask in masks.items()}
    primary = regions["segment_eroded_3px"]
    return {
        "primary_region": "segment_eroded_3px",
        "primary_statistic": "mad_filtered_median_m",
        "gt_distance_m": primary.get("mad_filtered_median_m") if primary.get("status") == "valid" else None,
        "regions": regions,
        "status": primary["status"],
        "rejection_reason": primary.get("rejection_reason"),
    }


def match_detect_segment(
    detections: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    masks: dict[str, np.ndarray],
    *,
    threshold: float = 0.5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[tuple[float, int, int]] = []
    for di, detect in enumerate(detections):
        for si, segment in enumerate(segments):
            if detect.get("class_name") == segment.get("class_name"):
                candidates.append((bbox_iou(detect["bbox_xyxy"], segment["bbox_xyxy"]), di, si))
    used_d: set[int] = set()
    used_s: set[int] = set()
    matched: list[dict[str, Any]] = []
    for iou, di, si in sorted(candidates, reverse=True):
        if iou < threshold or di in used_d or si in used_s:
            continue
        mask_key = segments[si].get("mask_key")
        if not mask_key or mask_key not in masks or not masks[mask_key].any():
            continue
        used_d.add(di)
        used_s.add(si)
        matched.append({"detect_index": di, "segment_index": si, "match_iou": float(iou)})
    diagnostics: list[dict[str, Any]] = []
    diagnostics.extend({"reason": "unmatched_detect", "detect_index": i, "segment_index": None} for i in range(len(detections)) if i not in used_d)
    diagnostics.extend({"reason": "unmatched_segment", "detect_index": None, "segment_index": i} for i in range(len(segments)) if i not in used_s)
    return matched, diagnostics


def percentile_summary(values: Iterable[float]) -> dict[str, float | None]:
    arr = np.asarray(list(values), dtype=np.float64)
    if arr.size == 0:
        return {name: None for name in ("mean", "median", "p75", "p90", "p95", "max")}
    return {
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "max": float(arr.max()),
    }
