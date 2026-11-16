"""Geometry-confirmed roughly-spherical enhancement with fail-closed fit audit."""
from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np
from scipy.optimize import least_squares

from upright_slender import _project, _rays
from generic_support import bottom_contour


def ellipse_proxy(bbox: list[float], shape: tuple[int, int]) -> np.ndarray:
    x1, y1, x2, y2 = (float(v) for v in bbox)
    result = np.zeros(shape, dtype=np.uint8)
    cv2.ellipse(result, (int(round((x1 + x2) / 2)), int(round((y1 + y2) / 2))), (max(1, int(round((x2 - x1) / 2))), max(1, int(round((y2 - y1) / 2)))), 0, 0, 360, 1, -1)
    return result.astype(bool)


def _sphere_fit(region: np.ndarray, axial: np.ndarray, world: np.ndarray, gravity: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    finite = region & np.isfinite(axial) & (axial > 0) & np.isfinite(world).all(axis=2)
    values = axial[finite]
    minimum = int(cfg["sphere_fit_min_points"])
    if values.size < minimum:
        return {"available": False, "reason": "insufficient_points", "point_count": int(values.size)}
    lo, hi = np.percentile(values, [1, 99])
    edges = np.arange(math.floor(lo / 50) * 50, hi + 100, 50)
    counts, edges = np.histogram(values, bins=edges)
    peak = int(np.flatnonzero(counts == counts.max())[0])
    keep = finite & (axial >= edges[max(0, peak - 1)]) & (axial <= edges[min(len(edges) - 1, peak + 2)])
    points = np.column_stack((world[..., 0][keep], world[..., 1][keep], axial[keep]))
    if points.shape[0] < minimum:
        return {"available": False, "reason": "cluster_too_small", "point_count": int(points.shape[0])}
    centre = points.mean(axis=0)
    radius = float(np.median(np.linalg.norm(points - centre, axis=1)))
    def residual(p: np.ndarray) -> np.ndarray:
        return np.linalg.norm(points - p[:3], axis=1) - p[3]
    solved = least_squares(residual, np.r_[centre, max(20.0, radius)], bounds=(np.r_[[-np.inf] * 3, 10.0], np.r_[[np.inf] * 3, 1000.0]), loss="soft_l1", f_scale=10, max_nfev=250)
    res = residual(solved.x)
    rms = float(np.sqrt(np.mean(res * res)))
    singular = np.linalg.svd(solved.jac, compute_uv=False)
    condition = float(np.inf if singular[-1] <= 1e-12 else singular[0] / singular[-1])
    radius = float(solved.x[3])
    relative = rms / max(radius, 1e-9)
    passed = bool(solved.success and relative <= float(cfg["sphere_fit_max_relative_residual"]) and condition <= float(cfg["sphere_fit_max_condition"]))
    up = np.asarray(gravity["gravity_authority"]["u_up_camera"], dtype=np.float64)
    centre_z = float(solved.x[:3] @ up + float(gravity["camera_height_mm"]))
    return {"available": passed, "reason": None if passed else "quality_gate", "point_count": int(points.shape[0]), "radius_mm": radius, "residual_rms_mm": rms, "relative_residual": relative, "jacobian_condition": condition, "support_mm": centre_z - radius, "top_mm": centre_z + radius, "height_mm": 2 * radius}


def estimate_roughly_spherical(mask: np.ndarray | None, bbox: list[float], support_mm: float, axial: np.ndarray, world: np.ndarray, camera: dict[str, Any], gravity: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    silhouette = np.asarray(mask, dtype=bool) if mask is not None and np.any(mask) else ellipse_proxy(bbox, axial.shape)
    sphere = _sphere_fit(silhouette, axial, world, gravity, cfg)
    if sphere.get("available"):
        return {"available": True, "reason": None, "height_mm": sphere["height_mm"], "top_mm": sphere["top_mm"], "support_override_mm": sphere["support_mm"], "method": "robust_free_radius_sphere_fit", "confidence": 0.8, "sphere_fit": sphere}
    erosion = int(cfg["proxy_erosion_px"])
    if erosion:
        silhouette = cv2.erode(silhouette.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=erosion).astype(bool)
    contour = bottom_contour(silhouette)
    if contour.shape[0] < 6:
        return {"available": False, "reason": "insufficient_ellipse_contour", "sphere_fit": sphere}
    indices = np.unique(np.rint(np.linspace(0, contour.shape[0] - 1, min(32, contour.shape[0]))).astype(int))
    anchors = contour[indices]
    up = np.asarray(gravity["gravity_authority"]["u_up_camera"], dtype=np.float64)
    rays = _rays(anchors, camera)
    denominator = rays @ up
    scales = (float(support_mm) - float(gravity["camera_height_mm"])) / denominator
    valid = (scales > 0) & np.isfinite(scales) & (np.abs(denominator) > 1e-9)
    bases = rays[valid] * scales[valid, None]
    heights = np.arange(float(cfg["height_min_mm"]), float(cfg["height_max_mm"]) + 0.5 * float(cfg["height_step_mm"]), float(cfg["height_step_mm"]))
    candidates = []
    for base in bases:
        uv = _project(base[None, :] + heights[:, None] * up[None, :], camera)
        xy = np.rint(uv).astype(int)
        inside_image = (xy[:, 0] >= 0) & (xy[:, 0] < 640) & (xy[:, 1] >= 0) & (xy[:, 1] < 360)
        inside = np.zeros(heights.size, dtype=bool)
        q = xy[inside_image]
        inside[inside_image] = silhouette[q[:, 1], q[:, 0]]
        ids = np.flatnonzero(inside)
        if ids.size >= 3 and ids[0] <= 2:
            candidates.append(float(heights[int(ids[-1])]))
    if len(candidates) < 6:
        return {"available": False, "reason": "insufficient_valid_chords", "sphere_fit": sphere}
    values = np.asarray(candidates)
    cutoff = np.quantile(values, 1.0 - float(cfg["upper_chord_fraction"]))
    height = float(np.median(values[values >= cutoff]))
    return {"available": True, "reason": None, "height_mm": height, "top_mm": float(support_mm) + height, "method": "roughly_spherical_gravity_envelope", "confidence": float(min(0.85, 0.55 + 0.3 * len(candidates) / max(len(bases), 1))), "valid_chord_count": len(candidates), "chord_p25_mm": float(np.percentile(values, 25)), "chord_median_mm": float(np.median(values)), "chord_p75_mm": float(np.percentile(values, 75)), "sphere_fit": sphere}

