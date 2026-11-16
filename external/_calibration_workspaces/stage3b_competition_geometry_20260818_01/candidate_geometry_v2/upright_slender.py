"""Gravity-only upright/slender silhouette enhancement without class gating."""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from generic_support import bottom_contour


def _rays(uv: np.ndarray, camera: dict[str, Any]) -> np.ndarray:
    k = np.asarray([[camera["fx"], 0, camera["cx"]], [0, camera["fy"], camera["cy"]], [0, 0, 1]], dtype=np.float64)
    xy = cv2.undistortPoints(np.asarray(uv, dtype=np.float64).reshape(-1, 1, 2), k, np.asarray(camera["distortion"], dtype=np.float64)).reshape(-1, 2)
    return np.column_stack((xy, np.ones(xy.shape[0])))


def _project(points: np.ndarray, camera: dict[str, Any]) -> np.ndarray:
    k = np.asarray([[camera["fx"], 0, camera["cx"]], [0, camera["fy"], camera["cy"]], [0, 0, 1]], dtype=np.float64)
    uv, _ = cv2.projectPoints(points.reshape(-1, 1, 3), np.zeros(3), np.zeros(3), k, np.asarray(camera["distortion"], dtype=np.float64))
    return uv.reshape(-1, 2)


def estimate_upright_slender(mask: np.ndarray, support_mm: float, camera: dict[str, Any], gravity: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    silhouette = np.asarray(mask, dtype=bool)
    contour = bottom_contour(silhouette)
    minimum = int(cfg["minimum_anchor_count"])
    if contour.shape[0] < minimum:
        return {"available": False, "reason": "insufficient_bottom_contour"}
    trim = float(cfg["base_anchor_edge_trim_fraction"])
    lo, hi = np.quantile(contour[:, 0], [trim, 1 - trim])
    usable = contour[(contour[:, 0] >= lo) & (contour[:, 0] <= hi)]
    count = min(int(cfg["maximum_anchor_count"]), usable.shape[0])
    anchors = usable[np.unique(np.rint(np.linspace(0, usable.shape[0] - 1, count)).astype(int))]
    up = np.asarray(gravity["gravity_authority"]["u_up_camera"], dtype=np.float64)
    camera_height = float(gravity["camera_height_mm"])
    rays = _rays(anchors, camera)
    denominators = rays @ up
    scales = (float(support_mm) - camera_height) / denominators
    valid = np.isfinite(scales) & (scales > 0) & (np.abs(denominators) > 1e-9)
    bases = rays[valid] * scales[valid, None]
    if bases.shape[0] < minimum:
        return {"available": False, "reason": "support_ray_intersection_failed", "anchor_count": int(anchors.shape[0])}
    heights = np.arange(float(cfg["height_min_mm"]), float(cfg["height_max_mm"]) + 0.5 * float(cfg["height_step_mm"]), float(cfg["height_step_mm"]))
    tolerance = int(cfg["mask_hole_tolerance_px"])
    test_mask = cv2.dilate(silhouette.astype(np.uint8), np.ones((2 * tolerance + 1, 2 * tolerance + 1), np.uint8)).astype(bool) if tolerance else silhouette
    candidates = []
    rejection = CounterLike()
    for base in bases:
        uv = _project(base[None, :] + heights[:, None] * up[None, :], camera)
        xy = np.rint(uv).astype(int)
        inside_image = (xy[:, 0] >= 0) & (xy[:, 0] < 640) & (xy[:, 1] >= 0) & (xy[:, 1] < 360)
        inside = np.zeros(heights.size, dtype=bool)
        q = xy[inside_image]
        inside[inside_image] = test_mask[q[:, 1], q[:, 0]]
        indices = np.flatnonzero(inside)
        if indices.size < 3 or indices[0] > 2:
            rejection.add("unstable_mask_run")
            continue
        first, last = int(indices[0]), int(indices[-1])
        if np.count_nonzero(~inside[first:last + 1]) > max(1, tolerance):
            rejection.add("mask_run_has_gaps")
            continue
        candidates.append(float(heights[last]))
    if len(candidates) < minimum:
        return {"available": False, "reason": "insufficient_valid_anchors", "anchor_count": int(anchors.shape[0]), "valid_anchor_count": len(candidates), "rejections": rejection.values}
    values = np.asarray(candidates)
    q25, median, q75 = (float(v) for v in np.percentile(values, [25, 50, 75]))
    if q75 - q25 > float(cfg["maximum_candidate_iqr_mm"]):
        return {"available": False, "reason": "unstable_anchor_heights", "candidate_iqr_mm": q75 - q25, "valid_anchor_count": int(values.size)}
    return {"available": True, "reason": None, "height_mm": median, "top_mm": float(support_mm) + median, "method": "upright_slender_gravity_silhouette", "confidence": float(min(0.95, 0.55 + 0.4 * values.size / max(anchors.shape[0], 1) - 0.2 * (q75 - q25) / max(float(cfg["maximum_candidate_iqr_mm"]), 1))), "anchor_count": int(anchors.shape[0]), "valid_anchor_count": int(values.size), "candidate_p25_mm": q25, "candidate_median_mm": median, "candidate_p75_mm": q75, "candidate_min_mm": float(values.min()), "candidate_max_mm": float(values.max())}


class CounterLike:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
    def add(self, key: str) -> None:
        self.values[key] = self.values.get(key, 0) + 1

