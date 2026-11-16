"""Lightweight contracts for the canonical all-object Geometry artifact.

This module is safe to import in the HTTP/orchestration interpreter.  Keep
numerical estimators, NumPy arrays, SciPy, Torch, CUDA, and model loading in
worker-owned implementation modules.
"""

from __future__ import annotations

import math
from typing import Any


SCHEMA_VERSION = "stage8b_all_object_geometry.v1"
ARTIFACT_FILENAME = "stage8b_all_object_geometry.v1.json"
CONFIDENCE_THRESHOLD = 0.5


def validate_geometry_document(document: dict[str, Any]) -> dict[str, Any]:
    """Validate the published Geometry document without importing estimators."""

    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"expected schema_version {SCHEMA_VERSION}")
    if int(document.get("frame_count", -1)) != 11:
        raise ValueError("all-object geometry is restricted to 11-frame scans")
    frames = document.get("frames")
    if not isinstance(frames, list):
        raise ValueError("frames must be an array")
    identities: set[tuple[str, str]] = set()
    for frame in frames:
        frame_id = str(frame.get("frame_id", ""))
        for item in frame.get("objects", []):
            key = (frame_id, str(item.get("logical_object_id", "")))
            if not all(key) or key in identities:
                raise ValueError("(frame_id, logical_object_id) must be unique")
            identities.add(key)
            status = item.get("geometry_status")
            if status not in {"ok", "degraded", "unavailable"}:
                raise ValueError("geometry_status must be ok/degraded/unavailable")
            if type(item.get("numeric_available")) is not bool:
                raise ValueError("numeric_available must be boolean")
            if item["numeric_available"] != (status in {"ok", "degraded"}):
                raise ValueError("numeric_available and geometry_status disagree")
            if status in {"ok", "degraded"}:
                geometry = item.get("geometry") or {}
                values = [
                    geometry.get(name)
                    for name in (
                        "distance_mm",
                        "object_height_mm",
                        "support_height_mm",
                        "top_height_above_floor_mm",
                    )
                ]
                if not all(
                    isinstance(value, (int, float)) and math.isfinite(float(value))
                    for value in values
                ):
                    raise ValueError(
                        "numeric geometry must contain four finite fields"
                    )
                residual = (
                    float(geometry["support_height_mm"])
                    + float(geometry["object_height_mm"])
                    - float(geometry["top_height_above_floor_mm"])
                )
                if abs(residual) > 1e-9:
                    raise ValueError("top must equal support + object height")
                confidence = float(item["geometry_confidence"])
                if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                    raise ValueError("geometry confidence must be finite in [0,1]")
                source = item.get("diagnostics", {}).get("region_source")
                if source == "bbox" and not confidence < CONFIDENCE_THRESHOLD:
                    raise ValueError("bbox-only geometry confidence must be below 0.5")
                if confidence < CONFIDENCE_THRESHOLD and status != "degraded":
                    raise ValueError("low-confidence numeric geometry must be degraded")
                if status == "ok" and item.get("degradation_reason") is not None:
                    raise ValueError("ok geometry must not carry a degradation reason")
                if status == "degraded" and not isinstance(item.get("degradation_reason"), str):
                    raise ValueError("degraded geometry requires a degradation reason")
            else:
                geometry = item.get("geometry") or {}
                if any(geometry.get(name) is not None for name in (
                    "distance_mm", "object_height_mm", "support_height_mm",
                    "top_height_above_floor_mm",
                )):
                    raise ValueError("unavailable geometry must not carry numeric fields")
    return document
