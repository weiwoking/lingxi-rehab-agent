"""Narrow class-family router; all unlisted classes remain generic."""
from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np


UPRIGHT_CONTAINER_CLASSES = {"bottle", "cup"}
SPORTS_BALL_CLASSES = {"sports ball", "sports_ball", "ball"}


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
    normalized = class_name.strip().lower()
    evidence = {
        **features,
        "class_family_scope": normalized,
        "support_confidence_input": float(support_confidence),
        "depth_iqr_mm": None if depth_iqr_mm is None else float(depth_iqr_mm),
    }
    if normalized in UPRIGHT_CONTAINER_CLASSES:
        return {"selected_family": "upright_container", "family_confidence": 1.0, "evidence": evidence, "fallback_reason": None}
    if normalized in SPORTS_BALL_CLASSES:
        return {"selected_family": "sports_ball", "family_confidence": 1.0, "evidence": evidence, "fallback_reason": None}
    return {"selected_family": "generic", "family_confidence": 1.0, "evidence": evidence, "fallback_reason": "class_not_in_specialized_extent_scope"}

