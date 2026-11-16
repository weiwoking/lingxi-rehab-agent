from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .object_depth import evaluate_object_depth
from .object_matching import greedy_match
from .stage3_1 import frozen_candidate, frozen_canvas_delta
from .stage4_validation import process_object_group

CANVASES = ("direct_640x360", "letterbox_640x480")
SEGMENT_METHOD = {"region_method": "segment_eroded_3px", "statistic_method": "mad_median"}
DETECTION_METHOD = {"region_method": "bbox_full", "statistic_method": "median"}


def unavailable(source_type: str) -> dict[str, Any]:
    return {"available": False, "source_type": source_type, "object_id": None, "confidence": None,
            "bbox_xyxy": [], "mask_npz_path": None, "mask_key": None, "region_method": None,
            "statistic_method": None, "canvas_candidates": {}}


def _candidate(record: dict[str, Any], source_type: str, mask_npz_path: str | None) -> dict[str, Any]:
    method = SEGMENT_METHOD if source_type == "segmentation" else DETECTION_METHOD
    return {"available": True, "source_type": source_type, "object_id": record["object_id"],
            "confidence": record["class_confidence"], "bbox_xyxy": record["bbox_xyxy"],
            "mask_npz_path": mask_npz_path if source_type == "segmentation" else None,
            "mask_key": record.get("mask_key"), **method,
            "canvas_candidates": {canvas: {key: value for key, value in record["recommended_candidate"][canvas].items()
                                            if key in {"status", "surface_depth_m", "pixel_count", "valid_ratio", "multimodal"}}
                                  for canvas in CANVASES}}


def _record(sample_id: str, source: str, instance: dict[str, Any], depths: dict[str, np.ndarray], mask: np.ndarray | None) -> dict[str, Any]:
    regions = {canvas: evaluate_object_depth(instance["bbox_xyxy"], depth, segment_mask=mask)[0]
               for canvas, depth in depths.items()}
    method = SEGMENT_METHOD if source == "segmentation" else DETECTION_METHOD
    return {"object_id": f"{sample_id}:{source}:{instance['instance_id']:03d}", "sample_id": sample_id,
            "model_source": source, "class_id": instance["class_id"], "class_name": instance["class_name"],
            "class_confidence": instance["confidence"], "bbox_xyxy": instance["bbox_xyxy"],
            "mask_key": instance.get("mask_key"), "regions": regions,
            "recommended_candidate": {canvas: frozen_candidate(regions[canvas], method) for canvas in CANVASES},
            "direct_letterbox_delta": frozen_canvas_delta(regions[CANVASES[0]], regions[CANVASES[1]], method)}


def process_full_new_scene(sample_id: str, depths: dict[str, np.ndarray], detect: dict[str, Any], segment: dict[str, Any],
                           segment_masks: dict[str, np.ndarray], segment_npz_path: Path, depth_refs: dict[str, str],
                           calibration: dict[str, Any], geometry_profile: dict[str, Any], root: Path) -> dict[str, Any]:
    detections = detect["instances"]; segments = segment["instances"]
    matches = greedy_match(detections, segments, threshold=.5)
    det_records = {item["instance_id"]: _record(sample_id, "detection", item, depths, None) for item in detections}
    seg_records = {item["instance_id"]: _record(sample_id, "segmentation", item, depths, segment_masks[item["mask_key"]]) for item in segments}
    mask_ref = segment_npz_path.relative_to(root).as_posix()
    groups: list[dict[str, Any]] = []; predictions: list[dict[str, Any]] = []
    paired_detections: set[int] = set()
    match_by_segment = {item["segment_instance_id"]: item for item in matches if item["segment_instance_id"] is not None}
    for seg in segments:
        pair = match_by_segment.get(seg["instance_id"]); det = None
        if pair and pair["match_status"] == "matched":
            det = det_records[pair["detect_instance_id"]]; paired_detections.add(pair["detect_instance_id"])
        record = seg_records[seg["instance_id"]]
        logical = f"{sample_id}:logical:{len(groups):03d}"
        group = {"sample_id": sample_id, "logical_object_id": logical, "class_id": record["class_id"], "class_name": record["class_name"],
                 "segmentation_primary": _candidate(record, "segmentation", mask_ref),
                 "detection_fallback": _candidate(det, "detection_fallback", None) if det else unavailable("detection_fallback"),
                 "match": {"status": "matched" if det else "unmatched_segmentation", "iou": pair.get("match_iou") if det else None},
                 "source_depth_refs": depth_refs,
                 "provenance": {"stage3_schema": "deltar_stage3_1_object_depth.v1", "stage4_group_schema": "deltar_stage4_object_group_input.v1.1",
                                "stage3_functions": ["evaluate_object_depth", "frozen_candidate", "frozen_canvas_delta"],
                                "stage4_function": "stage4_validation.process_object_group", "frozen_method": SEGMENT_METHOD,
                                "segmentation_object_id": record["object_id"], "detection_object_id": det["object_id"] if det else None,
                                "gt_used_for_inference": False}}
        groups.append(group); predictions.append({**seg, "logical_object_id": logical, "source": "segmentation"})
    for det in detections:
        if det["instance_id"] in paired_detections: continue
        record = det_records[det["instance_id"]]; logical = f"{sample_id}:logical:{len(groups):03d}"
        group = {"sample_id": sample_id, "logical_object_id": logical, "class_id": record["class_id"], "class_name": record["class_name"],
                 "segmentation_primary": unavailable("segmentation"), "detection_fallback": _candidate(record, "detection_fallback", None),
                 "match": {"status": "unmatched_detection", "iou": None}, "source_depth_refs": depth_refs,
                 "provenance": {"stage3_schema": "deltar_stage3_1_object_depth.v1", "stage4_group_schema": "deltar_stage4_object_group_input.v1.1",
                                "stage3_functions": ["evaluate_object_depth", "frozen_candidate", "frozen_canvas_delta"],
                                "stage4_function": "stage4_validation.process_object_group", "frozen_method": DETECTION_METHOD,
                                "segmentation_object_id": None, "detection_object_id": record["object_id"], "gt_used_for_inference": False}}
        groups.append(group); predictions.append({**det, "logical_object_id": logical, "source": "detection_fallback"})
    results = [process_object_group(group, calibration, geometry_profile, root) for group in groups]
    return {"schema_version": "deltar_stage5_1_full_new_scene.v1", "sample_id": sample_id,
            "frozen_methods": {"segmentation_primary": SEGMENT_METHOD, "detection_fallback": DETECTION_METHOD},
            "groups": groups, "predictions": predictions, "objects": results, "detect_segment_matches": matches,
            "gt_used_for_inference": False}


def process_mask_oracle_scene(sample_id: str, masks: dict[str, np.ndarray], truths: list[dict[str, Any]],
                              depths: dict[str, np.ndarray], mask_npz_path: Path, depth_refs: dict[str, str],
                              calibration: dict[str, Any], geometry_profile: dict[str, Any], root: Path,
                              layer: str) -> list[dict[str, Any]]:
    mask_ref = mask_npz_path.relative_to(root).as_posix(); output = []
    for truth in truths:
        mask = masks[truth["visible_mask_key"]]
        bbox = truth["bbox_xyxy"]
        regions = {canvas: evaluate_object_depth(bbox, depth, segment_mask=mask)[0] for canvas, depth in depths.items()}
        record = {"object_id": f"{sample_id}:{layer}:{truth['object_id']}", "class_confidence": 1.0, "bbox_xyxy": bbox,
                  "mask_key": truth["visible_mask_key"], "recommended_candidate": {canvas: frozen_candidate(regions[canvas], SEGMENT_METHOD) for canvas in CANVASES}}
        group = {"sample_id": sample_id, "logical_object_id": record["object_id"], "class_id": -1, "class_name": truth["class_name"],
                 "segmentation_primary": _candidate(record, "segmentation", mask_ref), "detection_fallback": unavailable("detection_fallback"),
                 "match": {"status": "oracle_mask", "iou": 1.0}, "source_depth_refs": depth_refs,
                 "provenance": {"layer": layer, "mask_source": "GT isolation variable", "geometry_function": "stage4_validation.process_object_group",
                                "gt_used_for_inference": layer == "L0"}}
        output.append(process_object_group(group, calibration, geometry_profile, root))
    return output
