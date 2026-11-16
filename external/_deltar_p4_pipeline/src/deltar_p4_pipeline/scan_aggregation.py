from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .frame_pipeline import CORE_CLASSES
from .object_matching import bbox_iou
from .scan_contracts import ScanContractError
from .scan_profiles import ProfileBundle


def _centroid_distance(left: list[float] | None, right: list[float] | None) -> float | None:
    if left is None or right is None:
        return None
    return float(np.linalg.norm(np.asarray(left, dtype=float) - np.asarray(right, dtype=float)))


def observation_score(observation: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    formal = observation.get("formal_geometry", {})
    if observation["object_source"] == "segmentation":
        score += 100.0
        reasons.append("segmentation_primary:+100")
    elif observation["object_source"] == "matched_detection_fallback":
        score += 60.0
        reasons.append("matched_detection_fallback:+60")
    elif observation["object_source"] == "detection_fallback":
        score += 50.0
        reasons.append("unmatched_detection_fallback:+50")
    canvas_agreed = formal.get("canvas_selection_status") == "agreed"
    if not formal and observation.get("canvas", {}).get("comparison", {}).get("agreement_status") in {"high", "moderate"}:
        canvas_agreed = True
    if canvas_agreed:
        score += 20.0
        reasons.append("canvas_agreed:+20")
    point_score = min(20.0, float(observation.get("geometry_point_count", 0)) / 100.0)
    score += point_score
    reasons.append(f"geometry_points:+{point_score:.3f}")
    if observation["visibility"]["visibility_status"] == "complete":
        score += 10.0
        reasons.append("complete:+10")
    if not observation["visibility"]["touches_image_border"]:
        score += 5.0
        reasons.append("not_border:+5")
    if observation["object_source"] == "segmentation":
        score += 3.0
        reasons.append("not_fallback:+3")
    compactness = min(2.0, max(0.0, float(observation.get("depth_compactness", 0.0))) * 2.0)
    score += compactness
    reasons.append(f"depth_compactness:+{compactness:.3f}")
    score += float(observation.get("confidence", 0.0))
    reasons.append(f"confidence:+{float(observation.get('confidence', 0.0)):.3f}")
    return score, reasons


@dataclass
class _Group:
    observations: list[tuple[str, int, dict[str, Any]]] = field(default_factory=list)
    warnings: set[str] = field(default_factory=set)

    @property
    def last(self) -> tuple[str, int, dict[str, Any]]:
        return self.observations[-1]


def _eligible(group: _Group, frame_sequence: int, observation: dict[str, Any], profile: dict[str, Any]) -> bool:
    _, last_sequence, previous = group.last
    if previous["class_name"] != observation["class_name"] or not 0 < frame_sequence - last_sequence <= int(profile["max_frame_gap"]):
        return False
    iou = bbox_iou(previous["bbox_xyxy"], observation["bbox_xyxy"])
    centroid = _centroid_distance(previous.get("centroid_3d_m"), observation.get("centroid_3d_m"))
    return iou >= float(profile["bbox_iou_threshold"]) or (centroid is not None and centroid <= float(profile["centroid_distance_m"]))


def aggregate_frames(frame_results: list[dict[str, Any]], bundle: ProfileBundle) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    profile = bundle.algorithm["multiframe_association"]
    groups: list[_Group] = []
    scan_warnings: set[str] = set()
    for frame in sorted(frame_results, key=lambda item: item["sequence"]):
        assigned: set[int] = set()
        for observation in sorted(frame["objects"], key=lambda item: item["frame_object_id"]):
            if observation.get("rejection_reason") is not None:
                continue
            if observation.get("formal_geometry") and observation["formal_geometry"].get("selected_geometry") is None:
                continue
            candidates = [index for index, group in enumerate(groups) if index not in assigned and _eligible(group, frame["sequence"], observation, profile)]
            if len(candidates) == 1:
                index = candidates[0]
                groups[index].observations.append((frame["frame_id"], frame["sequence"], observation))
                assigned.add(index)
            else:
                group = _Group([(frame["frame_id"], frame["sequence"], observation)])
                if len(candidates) > 1:
                    group.warnings.add("W_MULTIFRAME_AMBIGUOUS_ASSOCIATION")
                    scan_warnings.add("W_MULTIFRAME_AMBIGUOUS_ASSOCIATION")
                groups.append(group)
                assigned.add(len(groups) - 1)
    objects: list[dict[str, Any]] = []
    filtered_non_core = 0
    retain_non_core = bool(bundle.algorithm["retain_non_core_scan_objects"])
    for group_index, group in enumerate(groups, 1):
        scored = [(observation_score(obs), frame_id, obs) for frame_id, _, obs in group.observations]
        (best_score, reasons), best_frame_id, best = max(scored, key=lambda item: (item[0][0], item[1]))
        if best["class_scope"] != "core_now" and not retain_non_core:
            filtered_non_core += 1
            continue
        frame_ids = [frame_id for frame_id, _, _ in group.observations]
        warnings = sorted(set(best["warnings"]) | group.warnings)
        objects.append({
            "scan_object_id": f"object_{len(objects) + 1:04d}",
            "class_id": best["class_id"],
            "class_name": best["class_name"],
            "class_scope": best["class_scope"],
            "object_role": best["object_role"],
            "observation_count": len(group.observations),
            "frame_ids": frame_ids,
            "best_observation_frame_id": best_frame_id,
            "best_observation_score": best_score,
            "best_observation_reason": reasons,
            "best_observation": {"frame_id": best_frame_id, "frame_object_id": best["frame_object_id"],
                                 "selected_source": best["object_source"],
                                 "canvas_selection_status": best.get("formal_geometry", {}).get("canvas_selection_status", "legacy"),
                                 "formal_stage4_selected": bool(best.get("formal_geometry", {}).get("selected_geometry")) if best.get("formal_geometry") else True},
            "distance_observations_m": [obs["distance_m"] for _, _, obs in group.observations if obs["distance_m"] is not None],
            "visibility_observations": [obs["visibility"]["visibility_status"] for _, _, obs in group.observations],
            "visibility": {
                "status": best["visibility"]["visibility_status"],
                "top_visible": best["visibility"]["top_visible"],
                "bottom_visible": best["visibility"]["bottom_visible"],
                "full_extent_visible": best["visibility"]["full_extent_visible"],
            },
            "distance": {"value_m": best["distance_m"], "semantics": best["distance_semantics"], "valid": best["distance_m"] is not None, "source_frame_id": best_frame_id},
            "visible_height": best["visible_height"],
            "physical_height": best["physical_height"],
            "bottom_height": best["bottom_height"],
            "placement_level": best["placement_level"],
            "warnings": warnings,
            "rejection_reason": None,
            "aggregation_status": "single_observation" if len(group.observations) == 1 else "conservative_match",
            "provenance": {"source_frame_object_ids": [obs["frame_object_id"] for _, _, obs in group.observations],
                           "best_observation_only_for_geometry": True,
                           "model_hashes": best.get("provenance", {}).get("model_hashes", {}),
                           "stage4_function": best.get("provenance", {}).get("stage4_function", "legacy_stage6")},
        })
    if filtered_non_core:
        scan_warnings.add("W_NON_CORE_CLASS_FILTERED")
    metrics = {"input_observation_count": sum(len(group.observations) for group in groups), "association_group_count": len(groups),
               "output_object_count": len(objects), "filtered_non_core_count": filtered_non_core, "gt_used_for_association": False}
    return objects, sorted(scan_warnings), metrics


def determine_scan_status(frame_results: list[dict[str, Any]], objects: list[dict[str, Any]], calibration_status: str) -> str:
    valid_frames = [frame for frame in frame_results if frame["status"] == "success"]
    if not valid_frames:
        return "rejected"
    return "partial_success" if len(valid_frames) != len(frame_results) else "success"
