"""Geometry-first shape-family router; class is a bounded weak prior only."""
from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np


UPRIGHT_WEAK_PRIORS = {"bottle", "cup", "vase", "can"}
SPHERICAL_WEAK_PRIORS = {"sports ball", "ball"}


def _mask_features(mask: np.ndarray | None, bbox: list[float]) -> dict[str, Any]:
    x1, y1, x2, y2 = (float(v) for v in bbox)
    width, height = max(1.0, x2 - x1), max(1.0, y2 - y1)
    result = {"aspect_height_over_width": height / width, "mask_available": False, "fill_ratio": None, "roundness": None, "touches_border": x1 <= 1 or y1 <= 1 or x2 >= 639 or y2 >= 359}
    if mask is None or np.asarray(mask).shape != (360, 640) or not np.any(mask):
        return result
    source = np.asarray(mask, dtype=np.uint8)
    contours, _ = cv2.findContours(source, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return result
    contour = max(contours, key=cv2.contourArea)
    area, perimeter = float(cv2.contourArea(contour)), float(cv2.arcLength(contour, True))
    result.update({"mask_available": True, "fill_ratio": float(np.count_nonzero(source)) / (width * height), "roundness": 0.0 if perimeter <= 0 else float(4 * math.pi * area / (perimeter * perimeter))})
    return result


def route_shape(class_name: str, mask: np.ndarray | None, bbox: list[float], support_confidence: float, depth_iqr_mm: float | None, cfg: dict[str, Any]) -> dict[str, Any]:
    features = _mask_features(mask, bbox)
    aspect = float(features["aspect_height_over_width"])
    prior_weight = float(cfg["semantic_prior_weight"])
    upright_prior = prior_weight if class_name.strip().lower() in UPRIGHT_WEAK_PRIORS else 0.0
    spherical_prior = prior_weight if class_name.strip().lower() in SPHERICAL_WEAK_PRIORS else 0.0
    upright_geometry = min(1.0, max(0.0, (aspect - 1.0) / 1.5))
    upright_score = 0.42 * upright_geometry + 0.25 * float(features["mask_available"]) + 0.18 * min(1.0, support_confidence) + 0.05 * float(not features["touches_border"]) + upright_prior
    low, high = (float(v) for v in cfg["spherical_aspect_range"])
    aspect_round = max(0.0, 1.0 - abs(math.log(max(aspect, 1e-6))) / max(abs(math.log(low)), abs(math.log(high)))) if low <= aspect <= high else 0.0
    measured_roundness = features["roundness"] if features["roundness"] is not None else aspect_round * 0.7
    depth_compact = 0.5 if depth_iqr_mm is None else max(0.0, 1.0 - float(depth_iqr_mm) / 250.0)
    spherical_score = 0.42 * aspect_round + 0.25 * float(measured_roundness) + 0.13 * depth_compact + 0.10 * min(1.0, support_confidence) + spherical_prior
    evidence = {**features, "upright_geometry_score": upright_geometry, "spherical_aspect_score": aspect_round, "depth_compactness": depth_compact, "upright_semantic_prior": upright_prior, "spherical_semantic_prior": spherical_prior}
    if features["mask_available"] and aspect >= float(cfg["upright_min_aspect"]) and upright_score >= float(cfg["upright_min_confidence"]):
        return {"selected_family": "upright_slender", "family_confidence": float(upright_score), "evidence": evidence, "fallback_reason": None}
    spherical_confirmed = aspect_round > 0.55 and (features["mask_available"] or depth_compact > 0.35)
    if spherical_confirmed and measured_roundness >= float(cfg["spherical_min_roundness"]) and spherical_score >= float(cfg["spherical_min_confidence"]):
        return {"selected_family": "roughly_spherical", "family_confidence": float(spherical_score), "evidence": evidence, "fallback_reason": None}
    return {"selected_family": "generic", "family_confidence": float(max(0.0, 1.0 - max(upright_score, spherical_score))), "evidence": evidence, "fallback_reason": "no_shape_family_passed_geometry_confidence_gate"}

