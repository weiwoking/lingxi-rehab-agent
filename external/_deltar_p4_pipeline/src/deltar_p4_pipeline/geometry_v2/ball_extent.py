"""Strongly gated sports-ball extent with immutable environment support."""
from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np

from .roughly_spherical import ellipse_proxy, estimate_roughly_spherical


def _silhouette_gates(mask: np.ndarray | None, bbox: list[float], shape: tuple[int, int], cfg: dict[str, Any]) -> dict[str, Any]:
    real_mask = bool(mask is not None and np.asarray(mask).shape == shape and np.any(mask))
    silhouette = np.asarray(mask, dtype=np.uint8) if real_mask else ellipse_proxy(bbox, shape).astype(np.uint8)
    contours, _ = cv2.findContours(silhouette, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return {"passed": False, "reason": "no_ball_contour", "real_mask": real_mask}
    contour = max(contours, key=cv2.contourArea)
    area = float(cv2.contourArea(contour))
    perimeter = float(cv2.arcLength(contour, True))
    x, y, width, height = cv2.boundingRect(contour)
    aspect = float(width / max(height, 1))
    roundness = 0.0 if perimeter <= 0.0 else float(4.0 * math.pi * area / (perimeter * perimeter))
    (cx, cy), radius = cv2.minEnclosingCircle(contour)
    points = contour.reshape(-1, 2).astype(np.float64)
    angles = np.mod(np.arctan2(points[:, 1] - cy, points[:, 0] - cx), 2.0 * math.pi)
    angles.sort()
    gaps = np.diff(np.r_[angles, angles[0] + 2.0 * math.pi])
    visible_arc = float(1.0 - np.max(gaps) / (2.0 * math.pi)) if angles.size > 2 else 0.0
    low, high = (float(value) for value in cfg["silhouette_aspect_range"])
    passed = bool(low <= aspect <= high and roundness >= float(cfg["minimum_roundness"]) and visible_arc >= float(cfg["minimum_visible_arc_fraction"]))
    return {
        "passed": passed,
        "reason": None if passed else "ball_silhouette_quality_gate",
        "real_mask": real_mask,
        "aspect": aspect,
        "roundness": roundness,
        "visible_arc_fraction": visible_arc,
        "enclosing_radius_px": float(radius),
        "contour_point_count": int(points.shape[0]),
    }


def estimate_ball_extent(
    mask: np.ndarray | None,
    bbox: list[float],
    support_mm: float,
    axial: np.ndarray,
    world: np.ndarray,
    camera: dict[str, Any],
    gravity: dict[str, Any],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    gates = _silhouette_gates(mask, bbox, axial.shape, cfg)
    if not gates["passed"]:
        return {"available": False, "reason": gates["reason"], "silhouette_gates": gates}
    estimate = estimate_roughly_spherical(mask, bbox, support_mm, axial, world, camera, gravity, cfg)
    if not estimate.get("available"):
        return {**estimate, "silhouette_gates": gates}
    sphere = estimate.get("sphere_fit") or {}
    if sphere.get("available"):
        implied_delta = abs(float(sphere["support_mm"]) - float(support_mm))
        if sphere.get("surface_direction_condition", float("inf")) > float(cfg["sphere_fit_max_surface_direction_condition"]):
            return {"available": False, "reason": "sphere_visible_surface_condition_gate", "sphere_fit": sphere, "silhouette_gates": gates}
        if implied_delta > float(cfg["maximum_implied_support_disagreement_mm"]):
            return {"available": False, "reason": "sphere_support_consistency_gate", "sphere_fit": sphere, "silhouette_gates": gates, "implied_support_disagreement_mm": implied_delta}
    elif not gates["real_mask"]:
        return {"available": False, "reason": "bbox_proxy_requires_reliable_depth_sphere", "sphere_fit": sphere, "silhouette_gates": gates}
    return {
        **estimate,
        "method": "support_constrained_ball_extent",
        "support_authority": "read_only_environment_support",
        "silhouette_gates": gates,
    }
