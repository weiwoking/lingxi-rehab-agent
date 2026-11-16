from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


DISPLAY_NAMES_ZH = {
    "cup": "水杯",
    "bottle": "瓶子",
    "book": "书",
    "sports ball": "运动球",
}


def _bbox_metrics(a: list[float], b: list[float]) -> tuple[float, float, float, float]:
    ax1, ay1, ax2, ay2 = map(float, a)
    bx1, by1, bx2, by2 = map(float, b)
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = iw * ih
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - intersection
    iou = intersection / union if union > 0.0 else 0.0
    aw, ah = ax2 - ax1, ay2 - ay1
    bw, bh = bx2 - bx1, by2 - by1
    center_distance = math.hypot(
        ((ax1 + ax2) - (bx1 + bx2)) / 2.0,
        ((ay1 + ay2) - (by1 + by2)) / 2.0,
    )
    normalizer = max(math.hypot(aw, ah), math.hypot(bw, bh), 1.0)
    width_ratio = min(aw, bw) / max(aw, bw, 1.0)
    height_ratio = min(ah, bh) / max(ah, bh, 1.0)
    return iou, center_distance / normalizer, width_ratio, height_ratio


def _correlation(a: np.ndarray, b: np.ndarray) -> float:
    left = a.reshape(-1).astype(np.float64)
    right = b.reshape(-1).astype(np.float64)
    left -= left.mean()
    right -= right.mean()
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(np.dot(left, right) / denominator) if denominator > 1e-12 else 0.0


def _descriptor(path: Path, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    with Image.open(path) as image:
        gray = np.asarray(
            image.convert("L").resize((width, height), Image.Resampling.BILINEAR),
            dtype=np.float64,
        ) / 255.0
    dx = np.zeros_like(gray)
    dy = np.zeros_like(gray)
    dx[:, 1:] = np.abs(np.diff(gray, axis=1))
    dy[1:, :] = np.abs(np.diff(gray, axis=0))
    return gray, dx + dy


def compare_frame_views(
    first_path: Path,
    second_path: Path,
    profile: dict[str, Any],
) -> dict[str, Any]:
    image_profile = profile["image_size"]
    width = int(image_profile["descriptor_width"])
    height = int(image_profile["descriptor_height"])
    first, first_gradient = _descriptor(first_path, width, height)
    second, second_gradient = _descriptor(second_path, width, height)
    window = np.outer(np.hanning(height), np.hanning(width))
    fa = np.fft.fft2((first_gradient - first_gradient.mean()) * window)
    fb = np.fft.fft2((second_gradient - second_gradient.mean()) * window)
    cross_power = fa * np.conj(fb)
    cross_power /= np.maximum(np.abs(cross_power), 1e-12)
    phase = np.abs(np.fft.ifft2(cross_power))
    peak_y, peak_x = np.unravel_index(int(np.argmax(phase)), phase.shape)
    shift_y = int(peak_y if peak_y <= height // 2 else peak_y - height)
    shift_x = int(peak_x if peak_x <= width // 2 else peak_x - width)
    phase_peak_ratio = float(phase[peak_y, peak_x] / max(float(phase.mean()), 1e-12))

    y0, y1 = max(0, shift_y), min(height, height + shift_y)
    x0, x1 = max(0, shift_x), min(width, width + shift_x)
    first_crop = first[y0:y1, x0:x1]
    second_crop = second[y0 - shift_y : y1 - shift_y, x0 - shift_x : x1 - shift_x]
    first_gradient_crop = first_gradient[y0:y1, x0:x1]
    second_gradient_crop = second_gradient[
        y0 - shift_y : y1 - shift_y, x0 - shift_x : x1 - shift_x
    ]
    overlap_ratio = float(first_crop.size / (width * height))
    intensity_correlation = _correlation(first_crop, second_crop)
    gradient_correlation = _correlation(first_gradient_crop, second_gradient_crop)
    same_view = profile["same_view"]
    trusted = (
        phase_peak_ratio >= float(same_view["minimum_phase_peak_ratio"])
        and intensity_correlation >= float(same_view["minimum_intensity_correlation"])
        and gradient_correlation >= float(same_view["minimum_gradient_correlation"])
        and overlap_ratio >= float(same_view["minimum_overlap_ratio"])
    )
    scale_x = float(image_profile["width"]) / width
    scale_y = float(image_profile["height"]) / height
    return {
        "trusted_same_view": trusted,
        "alignment_method": "grayscale_gradient_phase_correlation_v1",
        "second_to_first_translation_xy": [
            float(shift_x * scale_x),
            float(shift_y * scale_y),
        ],
        "phase_peak_ratio": phase_peak_ratio,
        "intensity_correlation": intensity_correlation,
        "gradient_correlation": gradient_correlation,
        "overlap_ratio": overlap_ratio,
    }


def build_frame_relationships(
    job_output: Path,
    candidates: list[dict[str, Any]],
    profile: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    frames_by_class: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        frames_by_class[str(candidate["class_name"])].add(str(candidate["source_frame_id"]))
    relevant_pairs: set[tuple[str, str]] = set()
    for frame_ids in frames_by_class.values():
        ordered = sorted(frame_ids)
        for index, first in enumerate(ordered):
            for second in ordered[index + 1 :]:
                relevant_pairs.add((first, second))
    relationships: dict[tuple[str, str], dict[str, Any]] = {}
    for first, second in sorted(relevant_pairs):
        first_path = job_output / "logs" / first / "source_rgb.png"
        second_path = job_output / "logs" / second / "source_rgb.png"
        if not first_path.is_file() or not second_path.is_file():
            relationships[(first, second)] = {
                "trusted_same_view": False,
                "reason": "source_rgb_unavailable",
            }
            continue
        relationships[(first, second)] = compare_frame_views(
            first_path, second_path, profile
        )
    return relationships


def _geometry_conflict(
    first: dict[str, Any], second: dict[str, Any], profile: dict[str, Any]
) -> tuple[bool, list[str]]:
    first_geometry = first["geometry"]
    second_geometry = second["geometry"]
    reasons: list[str] = []
    if not first_geometry["valid"] or not second_geometry["valid"]:
        return False, reasons
    match = profile["object_match"]
    first_height = first_geometry.get("object_height_mm")
    second_height = second_geometry.get("object_height_mm")
    if first_height is not None and second_height is not None:
        relative = abs(float(first_height) - float(second_height)) / max(
            abs(float(first_height)), abs(float(second_height)), 1.0
        )
        if relative > float(match["maximum_object_height_relative_difference"]):
            reasons.append("object_height_conflict")
    first_support = first_geometry.get("support_height_mm")
    second_support = second_geometry.get("support_height_mm")
    if (
        first_support is not None
        and second_support is not None
        and abs(float(first_support) - float(second_support))
        > float(match["maximum_support_height_difference_mm"])
    ):
        reasons.append("support_height_conflict")
    return bool(reasons), reasons


def evaluate_observation_pair(
    first: dict[str, Any],
    second: dict[str, Any],
    relationship: dict[str, Any] | None,
    profile: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "observation_a": first["candidate_id"],
        "observation_b": second["candidate_id"],
        "class_name": first["class_name"],
        "decision": "keep_separate",
        "reasons": [],
        "metrics": {},
    }
    if first["class_name"] != second["class_name"]:
        result["reasons"].append("different_class")
        return result
    if first["source_frame_id"] == second["source_frame_id"]:
        result["reasons"].append("same_frame_merge_forbidden")
        return result
    if not relationship or not relationship.get("trusted_same_view", False):
        result["reasons"].append("no_trusted_same_view_evidence")
        return result
    shift_x, shift_y = relationship["second_to_first_translation_xy"]
    shifted_second = [
        float(second["bbox_xyxy"][0]) + float(shift_x),
        float(second["bbox_xyxy"][1]) + float(shift_y),
        float(second["bbox_xyxy"][2]) + float(shift_x),
        float(second["bbox_xyxy"][3]) + float(shift_y),
    ]
    iou, center_distance, width_ratio, height_ratio = _bbox_metrics(
        first["bbox_xyxy"], shifted_second
    )
    result["metrics"] = {
        "aligned_iou": iou,
        "normalized_center_distance": center_distance,
        "width_ratio": width_ratio,
        "height_ratio": height_ratio,
    }
    match = profile["object_match"]
    compatible = (
        iou >= float(match["minimum_aligned_iou"])
        and center_distance <= float(match["maximum_normalized_center_distance"])
        and width_ratio >= float(match["minimum_width_ratio"])
        and height_ratio >= float(match["minimum_height_ratio"])
    )
    if not compatible:
        result["reasons"].append("aligned_bbox_or_size_incompatible")
        return result
    conflict, conflict_reasons = _geometry_conflict(first, second, profile)
    if conflict:
        result["reasons"].extend(conflict_reasons)
        return result
    result["decision"] = "merge"
    result["reasons"] = [
        "same_canonical_class",
        "different_frames",
        "trusted_same_view_alignment",
        "aligned_bbox_compatible",
        "geometry_not_conflicting",
    ]
    result["metrics"]["same_view_phase_peak_ratio"] = relationship["phase_peak_ratio"]
    return result


def _representative_rank(candidate: dict[str, Any]) -> tuple[Any, ...]:
    mask = candidate["mask_summary"]
    return (
        int(candidate["visibility"]["full_extent_visible"]),
        int(not candidate["touches_border"]),
        int(candidate["detect_segment_agreed"]),
        int(mask["available"]),
        float(mask["bbox_fill_ratio"] or 0.0),
        float(candidate["confidence"]),
        -int(candidate["source_frame_sequence"]),
        candidate["candidate_id"],
    )


def _geometry_rank(candidate: dict[str, Any]) -> tuple[Any, ...]:
    geometry = candidate["geometry"]
    return (
        int(geometry["valid"]),
        int(candidate["support_surface_available"]),
        int(geometry.get("object_height_mm") is not None),
        int(candidate["visibility"]["full_extent_visible"]),
        int(not candidate["touches_border"]),
        float(candidate["confidence"]),
        -int(candidate["source_frame_sequence"]),
        candidate["candidate_id"],
    )


def _scene_object(cluster: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    ordered = sorted(cluster, key=lambda item: item["candidate_id"])
    representative = max(ordered, key=_representative_rank)
    geometry_source = max(ordered, key=_geometry_rank)
    confidence_source = max(
        ordered, key=lambda item: (float(item["confidence"]), item["candidate_id"])
    )
    observation_ids = [str(item["candidate_id"]) for item in ordered]
    identity_material = (
        str(ordered[0]["class_name"]) + "\n" + "\n".join(observation_ids)
    ).encode("utf-8")
    suffix = hashlib.sha256(identity_material).hexdigest()[:16]
    class_name = str(ordered[0]["class_name"])
    all_warnings = sorted(
        {str(warning) for item in ordered for warning in item.get("warnings", [])}
    )
    if len(ordered) == 1:
        all_warnings.append("W_CONSERVATIVE_SINGLETON_NO_SUFFICIENT_CROSS_FRAME_EVIDENCE")
    recognition_floor = float(profile["recognition_confidence_floor"])
    recognition_usable = any(
        float(item["confidence"]) >= recognition_floor for item in ordered
    )
    return {
        "object_id": f"scene_object_{class_name}_{suffix}",
        "class_name": class_name,
        "display_name_zh": DISPLAY_NAMES_ZH[class_name],
        "observation_ids": observation_ids,
        "observation_count": len(ordered),
        "confidence": float(confidence_source["confidence"]),
        "confidence_method": "maximum_reliable_observation_v1",
        "decision_usable": recognition_usable,
        "representative_observation_id": representative["candidate_id"],
        "source_frame_id": representative["source_frame_id"],
        "source_frame_sequence": representative["source_frame_sequence"],
        "bbox_xyxy": [float(value) for value in representative["bbox_xyxy"]],
        "geometry": dict(geometry_source["geometry"]),
        "visibility": dict(representative["visibility"]),
        "support_surface_available": bool(
            geometry_source["support_surface_available"]
        ),
        "field_sources": {
            "confidence": confidence_source["candidate_id"],
            "geometry": geometry_source["candidate_id"],
            "representative_image": representative["candidate_id"],
            "bbox_xyxy": representative["candidate_id"],
            "visibility": representative["candidate_id"],
        },
        "fusion_status": "fused" if len(ordered) > 1 else "singleton_conservative",
        "fusion_quality": "high_same_view" if len(ordered) > 1 else "not_fused",
        "fusion_reasons": (
            [
                "same_class_cross_frame",
                "trusted_same_view_alignment",
                "aligned_bbox_and_size_compatible",
                "geometry_not_conflicting",
            ]
            if len(ordered) > 1
            else ["insufficient_cross_frame_identity_evidence"]
        ),
        "warnings": sorted(set(all_warnings)),
    }


def fuse_scene_observations(
    candidates: list[dict[str, Any]],
    frame_relationships: dict[tuple[str, str], dict[str, Any]],
    profile: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ordered = sorted(candidates, key=lambda item: item["candidate_id"])
    parent = list(range(len(ordered)))
    cluster_frames = [{str(item["source_frame_id"])} for item in ordered]

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    evaluations: list[dict[str, Any]] = []
    merge_edges: list[tuple[float, int, int, dict[str, Any]]] = []
    for first_index, first in enumerate(ordered):
        for second_index in range(first_index + 1, len(ordered)):
            second = ordered[second_index]
            if first["class_name"] != second["class_name"]:
                continue
            frame_pair = tuple(
                sorted((str(first["source_frame_id"]), str(second["source_frame_id"])))
            )
            relationship = frame_relationships.get(frame_pair)
            evaluation = evaluate_observation_pair(
                first, second, relationship, profile
            )
            evaluations.append(evaluation)
            if evaluation["decision"] == "merge":
                score = float(evaluation["metrics"]["aligned_iou"])
                merge_edges.append((-score, first_index, second_index, evaluation))

    rejected_transitive: list[dict[str, Any]] = []
    for _, first_index, second_index, evaluation in sorted(
        merge_edges, key=lambda item: (item[0], item[1], item[2])
    ):
        first_root, second_root = find(first_index), find(second_index)
        if first_root == second_root:
            continue
        if cluster_frames[first_root] & cluster_frames[second_root]:
            evaluation["decision"] = "keep_separate"
            evaluation["reasons"].append("cluster_same_frame_collision_forbidden")
            rejected_transitive.append(
                {
                    "observation_a": evaluation["observation_a"],
                    "observation_b": evaluation["observation_b"],
                    "reason": "cluster_same_frame_collision_forbidden",
                }
            )
            continue
        parent[second_root] = first_root
        cluster_frames[first_root] |= cluster_frames[second_root]

    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, candidate in enumerate(ordered):
        groups[find(index)].append(candidate)
    objects = [_scene_object(group, profile) for _, group in sorted(groups.items())]
    objects.sort(key=lambda item: (item["class_name"], item["object_id"]))
    diagnostics = {
        "schema_version": "stage8b4a_fusion_diagnostics.v1",
        "observation_count": len(ordered),
        "scene_object_count": len(objects),
        "frame_relationships": [
            {
                "frame_a": first,
                "frame_b": second,
                **relationship,
            }
            for (first, second), relationship in sorted(frame_relationships.items())
        ],
        "pair_evaluations": evaluations,
        "rejected_transitive_merges": rejected_transitive,
        "policy_assertions": {
            "same_class_only": True,
            "same_frame_merge_forbidden": True,
            "untrusted_thin_object_distance_used_as_strong_evidence": False,
            "insufficient_evidence_keeps_multiple_objects": True,
        },
    }
    return objects, diagnostics
