"""Automatic semantic target matching and robust sample-level aggregation."""
from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable

import numpy as np


TARGET_ORDER = ("upper_bottle", "lower_bottle", "lower_volleyball")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _observation(item: dict[str, Any], frame_id: str, sequence: int) -> dict[str, Any] | None:
    geometry = item.get("geometry") or {}
    if item.get("geometry_status") not in (None, "success"):
        return None
    support = _finite(geometry.get("support_height_mm"))
    height = _finite(geometry.get("object_height_mm"))
    top = _finite(geometry.get("top_height_above_floor_mm"))
    bbox = item.get("bbox_xyxy")
    if support is None or height is None or not isinstance(bbox, list) or len(bbox) != 4:
        return None
    bbox = [float(value) for value in bbox]
    return {
        "class_name": str(item.get("class_name")),
        "frame_id": frame_id,
        "sequence": int(sequence),
        "logical_object_id": str(item.get("logical_object_id") or item.get("object_id") or ""),
        "bbox_xyxy": bbox,
        "bbox_center_y": (bbox[1] + bbox[3]) / 2.0,
        "support_height_mm": support,
        "object_height_mm": height,
        "top_z_mm": top if top is not None else support + height,
        "distance_mm": _finite(geometry.get("distance_mm")),
        "confidence": _finite(item.get("geometry_confidence") or item.get("confidence")),
        "support_source": str(
            (item.get("diagnostics") or {}).get("support_source")
            or geometry.get("support_source")
            or "package_stabilized"
        ),
        "region_source": str((item.get("diagnostics") or {}).get("region_source") or "unknown"),
    }


def observations_from_raw(document: dict[str, Any]) -> list[dict[str, Any]]:
    observations = []
    for frame in document.get("frames", []):
        frame_id = str(frame.get("frame_id"))
        sequence = int(frame.get("sequence", -1))
        for item in frame.get("objects", []):
            value = _observation(item, frame_id, sequence)
            if value is not None:
                observations.append(value)
    return observations


def observations_from_package(document: dict[str, Any]) -> list[dict[str, Any]]:
    observations = []
    for item in document.get("objects", []):
        value = _observation(
            item,
            str(item.get("source_frame_id")),
            int(item.get("source_frame_sequence", -1)),
        )
        if value is not None:
            observations.append(value)
    return observations


def _summary(values: Iterable[float]) -> dict[str, Any]:
    array = np.asarray(list(values), dtype=np.float64)
    if not array.size:
        return {"count": 0, "median": None, "p05": None, "p95": None, "rms": None}
    return {
        "count": int(array.size),
        "median": float(np.median(array)),
        "p05": float(np.percentile(array, 5)),
        "p95": float(np.percentile(array, 95)),
        "rms": float(np.sqrt(np.mean(np.square(array)))),
    }


def _aggregate(
    target_name: str,
    records: list[dict[str, Any]],
    truth: dict[str, Any],
    *,
    ambiguity: str | None = None,
) -> dict[str, Any]:
    target_truth = truth["targets"][target_name]
    support_truth = float(target_truth["expected_support_mm"])
    height_truth = float(target_truth["expected_object_height_mm"])
    if not records:
        return {
            "target": target_name,
            "detected": False,
            "match_status": "UNRESOLVED_TARGET_MATCH",
            "unresolved_reason": ambiguity or "no_usable_geometry_observation",
            "candidate_count": 0,
            "truth": target_truth,
        }
    support = _summary(item["support_height_mm"] for item in records)
    height = _summary(item["object_height_mm"] for item in records)
    top = _summary(item["top_z_mm"] for item in records)
    distance = _summary(
        item["distance_mm"] for item in records if item["distance_mm"] is not None
    )
    signed_support = float(support["median"] - support_truth)
    signed_height = float(height["median"] - height_truth)
    sources = Counter(item["support_source"] for item in records)
    frames = sorted({item["frame_id"] for item in records})
    status = "UNRESOLVED_TARGET_MATCH" if ambiguity else "RESOLVED"
    return {
        "target": target_name,
        "detected": True,
        "match_status": status,
        "unresolved_reason": ambiguity,
        "candidate_count": len(records),
        "valid_frame_count": len(frames),
        "source_frames": frames,
        "logical_objects": sorted({item["logical_object_id"] for item in records}),
        "support_source": sources.most_common(1)[0][0],
        "support_source_counts": dict(sorted(sources.items())),
        "source_consistent": len(sources) == 1,
        "support_height_mm": support,
        "object_height_mm": height,
        "top_z_mm": top,
        "scene_depth_mm": distance,
        "confidence": _summary(
            item["confidence"] for item in records if item["confidence"] is not None
        ),
        "truth": target_truth,
        "support_error": {
            "signed_mm": signed_support,
            "absolute_mm": abs(signed_support),
            "percent": abs(signed_support) / support_truth * 100.0,
            "truth_uncertainty_mm": float(truth["support_truth_uncertainty_mm"]),
        },
        "object_height_error": {
            "signed_mm": signed_height,
            "absolute_mm": abs(signed_height),
            "percent": abs(signed_height) / height_truth * 100.0,
            "truth_uncertainty_mm": float(target_truth["truth_uncertainty_mm"]),
        },
    }


def match_targets(
    observations: list[dict[str, Any]], truth: dict[str, Any]
) -> dict[str, Any]:
    bottles_by_frame: dict[str, list[dict[str, Any]]] = {}
    balls = []
    for item in observations:
        if item["class_name"] == "bottle":
            bottles_by_frame.setdefault(item["frame_id"], []).append(item)
        elif item["class_name"] == "sports ball":
            balls.append(item)

    upper: list[dict[str, Any]] = []
    lower: list[dict[str, Any]] = []
    unresolved_singletons = []
    for frame_records in bottles_by_frame.values():
        ordered = sorted(frame_records, key=lambda item: item["bbox_center_y"])
        if len(ordered) >= 2:
            upper.append(ordered[0])
            lower.append(ordered[-1])
        elif ordered:
            item = ordered[0]
            upper_delta = abs(
                item["support_height_mm"]
                - float(truth["targets"]["upper_bottle"]["expected_support_mm"])
            )
            lower_delta = abs(
                item["support_height_mm"]
                - float(truth["targets"]["lower_bottle"]["expected_support_mm"])
            )
            if abs(upper_delta - lower_delta) >= 100.0:
                (upper if upper_delta < lower_delta else lower).append(item)
            else:
                unresolved_singletons.append(item)

    bottle_ambiguity = None
    if unresolved_singletons and (not upper or not lower):
        bottle_ambiguity = (
            "single-bottle observations lacked a decisive vertical/support-height assignment"
        )
    targets = {
        "upper_bottle": _aggregate(
            "upper_bottle", upper, truth, ambiguity=bottle_ambiguity if not upper else None
        ),
        "lower_bottle": _aggregate(
            "lower_bottle", lower, truth, ambiguity=bottle_ambiguity if not lower else None
        ),
        "lower_volleyball": _aggregate("lower_volleyball", balls, truth),
    }
    resolved = all(item.get("match_status") == "RESOLVED" for item in targets.values())
    return {
        "schema_version": "stage3b_target_matching.v1",
        "detector_class_authority": {"bottle": "bottle", "volleyball": "sports ball"},
        "target_order": list(TARGET_ORDER),
        "all_targets_resolved": resolved,
        "unassigned_bottle_candidates": unresolved_singletons,
        "targets": targets,
    }
