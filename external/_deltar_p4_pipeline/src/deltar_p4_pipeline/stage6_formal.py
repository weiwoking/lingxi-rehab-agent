from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from .depth_statistics import compute_statistics
from .errors import ErrorCode, PipelineError
from .object_depth import evaluate_object_depth
from .object_matching import greedy_match
from .object_regions import bbox_mask, binary_erosion
from .partial_object import border_flags, visibility_contract
from .point_cloud import erode_mask
from .stage3_1 import frozen_candidate
from .stage4_validation import process_object_group

CANVASES = ("direct_640x360", "letterbox_640x480")
SEGMENT_METHOD = {"region_method": "segment_eroded_3px", "statistic_method": "mad_median"}
DETECTION_METHOD = {"region_method": "bbox_full", "statistic_method": "median"}
CORE_CLASSES = ("person", "chair", "dining table", "cup", "bottle", "book", "tv")
SOURCE_SHAPE = (360, 640)


def _mask_hash(mask: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(mask, dtype=np.uint8).tobytes(order="C")).hexdigest()


def _unavailable(source_type: str) -> dict[str, Any]:
    return {
        "available": False, "source_type": source_type, "object_id": None, "instance_id": None,
        "class_id": None, "class_name": None, "confidence": None, "bbox_xyxy": [],
        "mask_npz_path": None, "mask_key": None, "region_method": None, "statistic_method": None,
        "model_sha256": None, "canvas_candidates": {},
    }


def _required_candidate(
    depth: np.ndarray,
    region_mask: np.ndarray,
    method: dict[str, str],
    *,
    segment: bool,
) -> tuple[dict[str, Any], np.ndarray]:
    if depth.shape != SOURCE_SHAPE:
        raise PipelineError(
            ErrorCode.YOLO_MASK_SHAPE,
            "source depth must have shape 360x640",
            "depth",
        )
    if region_mask.shape != SOURCE_SHAPE:
        raise PipelineError(
            ErrorCode.YOLO_MASK_SHAPE,
            "segment mask must have shape 360x640",
            "segment_mask",
        )
    raw_count = int(region_mask.sum())
    if raw_count == 0:
        error = PipelineError(
            ErrorCode.OBJECT_REGION_EMPTY, "object region is empty", "region"
        )
        return {
            "status": "rejected",
            "region_method": method["region_method"],
            "statistic_method": method["statistic_method"],
            "rejection_reason": error.message,
        }, np.zeros(SOURCE_SHAPE, dtype=bool)
    valid_region = region_mask & np.isfinite(depth) & (depth > 0)
    valid_count = int(valid_region.sum())
    if valid_count < 8:
        error = PipelineError(
            ErrorCode.OBJECT_REGION_TOO_SMALL,
            "object region has fewer than 8 valid pixels",
            "region",
        )
        return {
            "status": "rejected",
            "region_method": method["region_method"],
            "statistic_method": method["statistic_method"],
            "rejection_reason": error.message,
        }, np.zeros(SOURCE_SHAPE, dtype=bool)
    try:
        stats = compute_statistics(depth[valid_region], min_pixels=8)
    except PipelineError as exc:
        return {
            "status": "rejected",
            "region_method": method["region_method"],
            "statistic_method": method["statistic_method"],
            "rejection_reason": exc.message,
        }, valid_region
    statistic = method["statistic_method"]
    statistic_field = "mad_filtered_median" if segment else "median"
    candidate = {
        "status": "valid",
        "surface_depth_m": float(stats[statistic_field]),
        "pixel_count": raw_count,
        "valid_pixel_count": valid_count,
        "valid_ratio": float(valid_count / raw_count),
        "median_m": stats["median"],
        "mad_m": stats["mad_m"],
        "iqr_m": stats["iqr_m"],
        "dominant_peak_m": stats["dominant_peak_m"],
        "dominant_peak_support": stats["dominant_peak_support"],
        "secondary_peak_m": stats["secondary_peak_m"],
        "peak_separation_m": stats["peak_separation_m"],
        "multimodal": stats["multimodal"],
        "geometry_confidence_inputs": {
            "region_support": float(valid_count / raw_count),
            "depth_compactness": float(1.0 / (1.0 + stats["iqr_m"])),
            "peak_dominance": stats["dominant_peak_support"],
            "edge_stability": None,
            "canvas_agreement": None,
        },
        "warnings": list(stats["warnings"]),
        "rejection_reason": None,
    }
    return {
        "region_method": method["region_method"],
        "statistic_method": statistic,
        **candidate,
        "selection_source": "deltar_stage3_1_method_selection.v1",
    }, valid_region


def _record(frame_id: str, source: str, instance: dict[str, Any], depths: dict[str, np.ndarray],
            mask: np.ndarray | None, *, required_region_only: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    method = SEGMENT_METHOD if source == "segmentation" else DETECTION_METHOD
    regions: dict[str, Any] = {}
    region_masks: dict[str, dict[str, np.ndarray]] = {}
    candidates: dict[str, dict[str, Any]] = {}
    frozen_region: np.ndarray | None = None
    if required_region_only:
        if source == "segmentation" and mask is None:
            candidates = {
                canvas: {
                    "status": "rejected",
                    "region_method": method["region_method"],
                    "statistic_method": method["statistic_method"],
                    "rejection_reason": "frozen method unavailable",
                }
                for canvas in CANVASES
            }
        else:
            frozen_region = (
                binary_erosion(mask, 3)
                if source == "segmentation"
                else bbox_mask(instance["bbox_xyxy"])
            )
            for canvas in CANVASES:
                candidate, valid_region = _required_candidate(
                    depths[canvas], frozen_region, method, segment=source == "segmentation"
                )
                candidates[canvas] = candidate
                region_masks[canvas] = {method["region_method"]: valid_region}
    else:
        for canvas in CANVASES:
            regions[canvas], region_masks[canvas] = evaluate_object_depth(instance["bbox_xyxy"], depths[canvas], segment_mask=mask)
        candidates = {canvas: frozen_candidate(regions[canvas], method) for canvas in CANVASES}
    record = {
        "object_id": f"{frame_id}:{source}:{int(instance['instance_id']):03d}",
        "instance_id": int(instance["instance_id"]), "class_id": int(instance["class_id"]),
        "class_name": str(instance["class_name"]), "confidence": float(instance["confidence"]),
        "bbox_xyxy": [float(value) for value in instance["bbox_xyxy"]], "mask_key": instance.get("mask_key"),
        "recommended_candidate": candidates,
    }
    audit: dict[str, Any] = {"source": source, "region_method": method["region_method"], "statistic_method": method["statistic_method"]}
    if source == "segmentation" and mask is not None:
        frozen = frozen_region if frozen_region is not None else binary_erosion(mask, 3)
        formal = erode_mask(mask, 3)
        audit.update({
            "raw_mask_pixel_count": int(mask.sum()), "frozen_region_pixel_count": int(frozen.sum()),
            "frozen_region_sha256": _mask_hash(frozen), "formal_region_sha256": _mask_hash(formal),
            "stage3_stage4_region_exact_equal": bool(np.array_equal(frozen, formal)),
            "stage3_valid_region_pixel_count": {canvas: int(region_masks[canvas]["segment_eroded_3px"].sum()) for canvas in CANVASES},
        })
        if not audit["stage3_stage4_region_exact_equal"]:
            raise ValueError("E_FROZEN_REGION_MISMATCH: Stage 3.1 and Stage 4.1 erosion differ")
    return record, audit


def _candidate(record: dict[str, Any], source_type: str, model_sha256: str, mask_path: str | None) -> dict[str, Any]:
    method = SEGMENT_METHOD if source_type == "segmentation" else DETECTION_METHOD
    return {
        "available": True, "source_type": source_type, "object_id": record["object_id"],
        "instance_id": record["instance_id"], "class_id": record["class_id"], "class_name": record["class_name"],
        "confidence": record["confidence"], "bbox_xyxy": record["bbox_xyxy"],
        "mask_npz_path": mask_path if source_type == "segmentation" else None,
        "mask_key": record.get("mask_key"), "model_sha256": model_sha256, **method,
        "canvas_candidates": {
            canvas: {key: value for key, value in record["recommended_candidate"][canvas].items()
                     if key in {"status", "surface_depth_m", "pixel_count", "valid_pixel_count", "valid_ratio", "multimodal", "rejection_reason"}}
            for canvas in CANVASES
        },
    }


def _public_candidate(candidate: dict[str, Any], kind: str) -> dict[str, Any]:
    id_key = "segment_instance_id" if kind == "segmentation" else "detect_instance_id"
    return {
        "available": bool(candidate["available"]), id_key: candidate.get("instance_id"),
        "class_id": candidate.get("class_id"), "class_name": candidate.get("class_name"),
        "confidence": candidate.get("confidence"), "bbox_xyxy": candidate.get("bbox_xyxy", []),
        "mask_key": candidate.get("mask_key") if kind == "segmentation" else None,
        "region_method": candidate.get("region_method"), "statistic_method": candidate.get("statistic_method"),
        "model_sha256": candidate.get("model_sha256"),
    }


def build_logical_groups(frame_id: str, detections: list[dict[str, Any]], segments: list[dict[str, Any]],
                         segment_masks: dict[str, np.ndarray], depths: dict[str, np.ndarray],
                         model_hashes: dict[str, str], mask_npz_path: str,
                         depth_refs: dict[str, str], match_threshold: float, *,
                         required_region_only: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    matches = greedy_match(detections, segments, match_threshold)
    det_records: dict[int, dict[str, Any]] = {}
    seg_records: dict[int, dict[str, Any]] = {}
    region_audit: list[dict[str, Any]] = []
    for item in detections:
        record, audit = _record(
            frame_id, "detection", item, depths, None,
            required_region_only=required_region_only,
        )
        det_records[item["instance_id"]] = record; region_audit.append({"object_id": record["object_id"], **audit})
    for item in segments:
        record, audit = _record(
            frame_id, "segmentation", item, depths, segment_masks[item["mask_key"]],
            required_region_only=required_region_only,
        )
        seg_records[item["instance_id"]] = record; region_audit.append({"object_id": record["object_id"], **audit})
    matched_by_segment = {item["segment_instance_id"]: item for item in matches if item["match_status"] == "matched"}
    matched_detection_ids: set[int] = set()
    groups: list[dict[str, Any]] = []
    public: list[dict[str, Any]] = []
    for segment in sorted(segments, key=lambda item: item["instance_id"]):
        pair = matched_by_segment.get(segment["instance_id"])
        det_record = None
        if pair is not None:
            det_record = det_records[pair["detect_instance_id"]]
            matched_detection_ids.add(pair["detect_instance_id"])
        seg_record = seg_records[segment["instance_id"]]
        logical_id = f"{frame_id}:logical:{len(groups):03d}"
        seg_candidate = _candidate(seg_record, "segmentation", model_hashes["segment"], mask_npz_path)
        det_candidate = _candidate(det_record, "detection_fallback", model_hashes["detect"], None) if det_record else _unavailable("detection_fallback")
        match = {"status": "matched" if det_record else "unmatched_segmentation", "iou": float(pair["match_iou"]) if det_record else None}
        provenance = {
            "frame_id": frame_id, "stage3_schema": "deltar_stage3_1_object_depth.v1",
            "stage4_group_schema": "deltar_stage4_object_group_input.v1.1",
            "stage4_function": "all_object_geometry.measure_logical_object_v2",
            "segmentation_object_id": seg_record["object_id"], "detection_object_id": det_record["object_id"] if det_record else None,
            "model_hashes": model_hashes, "gt_used_for_inference": False,
        }
        group = {"sample_id": frame_id, "logical_object_id": logical_id, "class_id": seg_record["class_id"],
                 "class_name": seg_record["class_name"], "segmentation_primary": seg_candidate,
                 "detection_fallback": det_candidate, "match": match, "source_depth_refs": depth_refs, "provenance": provenance}
        groups.append(group)
        public.append({"logical_object_id": logical_id, "class_id": group["class_id"], "class_name": group["class_name"],
                       "segmentation_primary": _public_candidate(seg_candidate, "segmentation"),
                       "detection_fallback": _public_candidate(det_candidate, "detection"), "match": match, "provenance": provenance})
    for detection in sorted(detections, key=lambda item: item["instance_id"]):
        if detection["instance_id"] in matched_detection_ids:
            continue
        det_record = det_records[detection["instance_id"]]
        logical_id = f"{frame_id}:logical:{len(groups):03d}"
        seg_candidate = _unavailable("segmentation")
        det_candidate = _candidate(det_record, "detection_fallback", model_hashes["detect"], None)
        match = {"status": "unmatched_detection", "iou": None}
        provenance = {"frame_id": frame_id, "stage3_schema": "deltar_stage3_1_object_depth.v1",
                      "stage4_group_schema": "deltar_stage4_object_group_input.v1.1",
                      "stage4_function": "all_object_geometry.measure_logical_object_v2", "segmentation_object_id": None,
                      "detection_object_id": det_record["object_id"], "model_hashes": model_hashes, "gt_used_for_inference": False}
        group = {"sample_id": frame_id, "logical_object_id": logical_id, "class_id": det_record["class_id"],
                 "class_name": det_record["class_name"], "segmentation_primary": seg_candidate,
                 "detection_fallback": det_candidate, "match": match, "source_depth_refs": depth_refs, "provenance": provenance}
        groups.append(group)
        public.append({"logical_object_id": logical_id, "class_id": group["class_id"], "class_name": group["class_name"],
                       "segmentation_primary": _public_candidate(seg_candidate, "segmentation"),
                       "detection_fallback": _public_candidate(det_candidate, "detection"), "match": match, "provenance": provenance})
    return groups, public, region_audit


def _attempt_summaries(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"source": item["source"], "success": bool(item["success"]),
             "canvas_selection_status": item["canvas_selection_status"], "selected_canvas": item["selected_canvas"],
             "single_canvas_only": bool(item["single_canvas_only"]),
             "automatic_geometry_valid": bool(item["automatic_geometry_valid"]),
             "critical_geometry_rejections": [str(value) for value in item["critical_geometry_rejections"]]}
            for item in attempts]


def formal_result_to_observation(group: dict[str, Any], result: dict[str, Any], border_tolerance_px: float) -> dict[str, Any]:
    selected_source = result.get("selected_source")
    candidate = group["segmentation_primary"] if selected_source == "segmentation" else group["detection_fallback"]
    if not candidate.get("available"):
        candidate = group["segmentation_primary"] if group["segmentation_primary"].get("available") else group["detection_fallback"]
    bbox = candidate.get("bbox_xyxy", [])
    flags = border_flags(bbox, 640, 360, border_tolerance_px) if len(bbox) == 4 else {
        "touches_top": False, "touches_bottom": False, "touches_left": False, "touches_right": False, "touches_image_border": False}
    success = result["status"] == "success" and result.get("selected_geometry") is not None
    geometry = result.get("selected_geometry") if success else None
    visibility = visibility_contract(
        mask_touches_border=flags["touches_image_border"] and candidate.get("source_type") == "segmentation",
        bbox_touches_border=flags["touches_image_border"], top_visible=not flags["touches_top"],
        bottom_visible=not flags["touches_bottom"], obvious_occlusion=False, depth_continuous=success,
        large_truncation=False, point_support_top=success, point_support_bottom=success,
        distance_valid=success, visible_mask_valid=success, ground_transform_valid=success,
        calibration_measured=bool(result.get("absolute_geometry_valid")), bottom_occluded_or_truncated=flags["touches_bottom"], border=flags)
    warnings = list(result.get("warnings", []))
    if result.get("fallback_used"):
        warnings.append("W_DETECTION_FALLBACK")
    if result.get("canvas_selection_status") == "disagreed":
        warnings.append("W_CANVAS_DISAGREEMENT")
    if visibility["visibility_status"] != "complete":
        warnings.append("W_PARTIAL_OBJECT")
    absolute = bool(result.get("absolute_geometry_valid"))
    visible_available = geometry is not None
    complete = visibility["visibility_status"] == "complete"
    physical_available = bool(visible_available and complete)
    physical_absolute = bool(absolute and physical_available)
    bottom_available = bool(visible_available and visibility["bottom_visible"] and geometry["quality_inputs"].get("bottom_support", 0) > 0) if geometry else False
    bottom_absolute = bool(absolute and bottom_available)
    selected_canvas = result.get("selected_canvas")
    stage3_candidate = candidate.get("canvas_candidates", {}).get(selected_canvas, {}) if selected_canvas else {}
    surface_distance = stage3_candidate.get("surface_depth_m") if stage3_candidate.get("status") == "valid" else (geometry.get("distance_m") if geometry else None)
    class_name = group["class_name"]
    return {
        "frame_object_id": group["logical_object_id"], "logical_object_id": group["logical_object_id"],
        "class_id": int(group["class_id"]), "class_name": class_name,
        "class_scope": "core_now" if class_name in CORE_CLASSES else "supported_but_not_core",
        "object_role": "context_person" if class_name == "person" else "operable_object",
        "object_source": selected_source or "rejected", "confidence": float(candidate.get("confidence") or 0.0),
        "bbox_xyxy": [float(value) for value in bbox],
        "distance_m": float(surface_distance) if surface_distance is not None else None,
        "distance_semantics": "visible_surface_distance" if surface_distance is not None else None,
        "centroid_3d_m": None, "geometry_point_count": int(geometry["point_count"]) if geometry else 0,
        "depth_compactness": float(1.0 / (1.0 + geometry["quality_inputs"].get("depth_iqr", 0.0))) if geometry else 0.0,
        "formal_geometry": {
            "schema_version": "deltar_stage4_geometry.v1.1", "status": result["status"],
            "canvas_selection_status": result["canvas_selection_status"], "selected_canvas": result.get("selected_canvas"),
            "selection_is_provisional": bool(result["selection_is_provisional"]), "single_canvas_only": bool(result["single_canvas_only"]),
            "automatic_geometry_valid": bool(result["automatic_geometry_valid"]), "absolute_geometry_valid": absolute,
            "selected_source": selected_source, "fallback_used": bool(result["fallback_used"]),
            "fallback_reason": result.get("fallback_reason"), "critical_geometry_rejections": [str(value) for value in result["critical_geometry_rejections"]],
            "attempts": _attempt_summaries(result["attempts"]),
            "selected_geometry": None if geometry is None else {
                "distance_m": float(geometry["distance_m"]), "object_height_m": float(geometry["object_height_m"]),
                "bottom_height_m": float(geometry["bottom_height_m"]), "point_count": int(geometry["point_count"]),
                "mask_support": int(geometry["quality_inputs"].get("mask_support", 0)),
            },
            "provenance": group["provenance"],
        },
        "visibility": visibility,
        "visible_height": {"value_m": float(geometry["object_height_m"]) if geometry else None,
                           "available": visible_available, "absolute_valid": bool(absolute and visible_available)},
        "physical_height": {"value_m": float(geometry["object_height_m"]) if physical_absolute else None,
                            "available": physical_available, "absolute_valid": physical_absolute},
        "bottom_height": {"value_m": float(geometry["bottom_height_m"]) if bottom_absolute else None,
                          "available": bottom_available, "absolute_valid": bottom_absolute},
        "placement_level": "unknown" if not bottom_absolute else ("ground" if abs(float(geometry["bottom_height_m"])) <= 0.08 else "elevated"),
        "warnings": sorted(set(warnings)), "errors": list(result.get("errors", [])),
        "rejection_reason": result.get("rejection_reason"),
        "provenance": {"frame_id": group["provenance"]["frame_id"], "model_hashes": group["provenance"]["model_hashes"],
                       "stage4_function": "all_object_geometry.measure_logical_object_v2"},
    }


def process_groups(groups: list[dict[str, Any]], calibration: dict[str, Any], geometry_profile: dict[str, Any],
                   root: Path, border_tolerance_px: float, *,
                   depth_arrays: dict[str, np.ndarray] | None = None,
                   segment_masks: dict[str, np.ndarray] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    formal_results = [
        process_object_group(
            group,
            calibration,
            geometry_profile,
            root,
            depth_arrays=depth_arrays,
            segment_masks=segment_masks,
        )
        for group in groups
    ]
    observations = [formal_result_to_observation(group, result, border_tolerance_px) for group, result in zip(groups, formal_results, strict=True)]
    return formal_results, observations
