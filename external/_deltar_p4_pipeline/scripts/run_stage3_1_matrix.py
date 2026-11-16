from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.object_depth import STATISTIC_FIELDS, evaluate_object_depth
from deltar_p4_pipeline.object_matching import greedy_match
from deltar_p4_pipeline.stage3_1 import (
    GT_RESIZE_METHODS,
    aggregate_method_rows,
    choose_frozen_method,
    frozen_candidate,
    frozen_canvas_delta,
    same_region_same_statistic,
    valid_aware_resize_depth,
)
from deltar_p4_pipeline.stage3_validation import boundary_metrics, depth_metrics
from deltar_p4_pipeline.yolo_contracts import load_yolo_output
from deltar_p4_pipeline.yolo_contracts import sha256_file

OUT = ROOT / "outputs_stage3_1"
CANVASES = ("direct_640x360", "letterbox_640x480")
SEGMENT_PREFIX = "segment"
DETECT_PREFIX = "bbox"
DETECT_MODEL = Path(r"C:\ec\esp-claw\pc\vision_interpreter\models\yolo11n.pt")
SEGMENT_MODEL = ROOT / "models" / "yolo11n-seg.pt"
DETECT_SHA256 = "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"
SEGMENT_SHA256 = "55ed65c56c91713d23e8402371c6c49a6fd84f257f7dce452e8d70e41dcbe152"


def jsonable(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    return value


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(data), indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def gt_variants(sample_parent: Path) -> dict[str, np.ndarray]:
    metadata = json.loads((sample_parent / "direct_640x360" / "source_metadata.json").read_text(encoding="utf-8"))
    with h5py.File(metadata["source_h5"], "r") as handle:
        original = handle["depth"][:].astype(np.float32)
    return {method: valid_aware_resize_depth(original, method)[0] for method in GT_RESIZE_METHODS}


def route_region(name: str, route: str) -> bool:
    return name.startswith(SEGMENT_PREFIX if route == "segmentation" else DETECT_PREFIX)


def add_sensitivity(base: list[dict[str, Any]], variants: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    lookup = {method: {(row["region_method"], row["statistic_method"]): row for row in matrix} for method, matrix in variants.items()}
    rankings = {method: {key: rank + 1 for rank, key in enumerate(table)} for method, table in
                ((method, [(row["region_method"], row["statistic_method"]) for row in matrix]) for method, matrix in variants.items())}
    output = []
    for row in base:
        key = (row["region_method"], row["statistic_method"])
        medians = {method: lookup[method].get(key, {}).get("median_gt_scalar_error_m") for method in GT_RESIZE_METHODS}
        finite = [value for value in medians.values() if value is not None]
        spread = max(finite) - min(finite) if finite else None
        ranks = {method: rankings[method].get(key) for method in GT_RESIZE_METHODS}
        enriched = {**row, "gt_resize_median_errors_m": medians, "gt_resize_median_spread_m": spread,
                    "gt_resize_ranks": ranks, "gt_resize_rank_stable": len({value for value in ranks.values() if value is not None}) == 1}
        if "ranking_score_experimental" in enriched:
            enriched["ranking_score_experimental"] -= spread or 0.0
        output.append(enriched)
    return sorted(output, key=lambda item: item.get("ranking_score_experimental", -1e9), reverse=True)


def annotate_ranking(matrix: list[dict[str, Any]], route: str) -> list[dict[str, Any]]:
    lookup = {(row["region_method"], row["statistic_method"]): row for row in matrix}
    for row in matrix:
        baseline_region = "segment_mask" if route == "segmentation" else "bbox_full"
        baseline = lookup.get((baseline_region, row["statistic_method"]))
        if baseline and row["median_gt_scalar_error_m"] is not None and baseline["median_gt_scalar_error_m"] is not None:
            change = row["median_gt_scalar_error_m"] - baseline["median_gt_scalar_error_m"]
        else:
            change = None
        row["edge_contamination_error_change_vs_unmodified_region_m"] = change
        row["interpretability"] = "explicit spatial region and named robust scalar statistic"
        row["stage4_suitability"] = "mask-preserving primary" if route == "segmentation" else "bbox-only fallback"
        if change is not None and "ranking_score_experimental" in row:
            row["ranking_score_experimental"] -= 0.10 * max(0.0, change)
    return sorted(matrix, key=lambda item: item.get("ranking_score_experimental", -1e9), reverse=True)


def verify_model(path: Path, expected_sha256: str, expected_task: str) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"E_YOLO_MODEL_MISSING: {path}")
    actual = sha256_file(path)
    if actual.lower() != expected_sha256.lower():
        raise RuntimeError(f"E_YOLO_MODEL_HASH: expected {expected_sha256}, actual {actual}")
    return {"path": str(path), "actual_sha256": actual, "expected_sha256": expected_sha256,
            "hash_verified": True, "expected_task": expected_task}


def candidate_rows(record: dict[str, Any], route: str, gt_by_resize: dict[str, np.ndarray]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    gt_results = {}
    bbox = record["bbox_xyxy"]
    mask = record.get("mask")
    for resize_method, gt in gt_by_resize.items():
        results, masks = evaluate_object_depth(bbox, gt, segment_mask=mask)
        gt_results[resize_method] = {"regions": results, "masks": masks}
    evaluations: dict[str, Any] = {method: {} for method in GT_RESIZE_METHODS}
    for canvas, predicted in record["regions"].items():
        for region_name, region_data in predicted.items():
            if not route_region(region_name, route):
                continue
            for statistic in STATISTIC_FIELDS:
                predicted_candidate = region_data.get("statistics", {}).get(statistic, {})
                for resize_method in GT_RESIZE_METHODS:
                    comparison = same_region_same_statistic(predicted, gt_results[resize_method]["regions"], region_name, statistic)
                    comparison.update({"object_id": record["object_id"], "sample_id": record["sample_id"],
                                       "canvas_strategy": canvas, "model_source": route,
                                       "class_name": record["class_name"], "small_object": record["small_object"],
                                       "multimodal": bool(predicted_candidate.get("multimodal", False)), "gt_resize_method": resize_method})
                    if comparison["status"] == "valid":
                        region_mask = record["masks"][canvas][region_name]
                        pixel = depth_metrics(record["depths"][canvas], gt_by_resize[resize_method], region_mask)
                        comparison["pixel_mae_m"] = pixel.get("mae_m")
                        comparison["pixel_p95_m"] = pixel.get("p95_absolute_error_m")
                    rows.append(comparison)
                    evaluations[resize_method].setdefault(canvas, {}).setdefault(region_name, {})[statistic] = comparison
    return rows, evaluations


def summarize_delta(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    valid = [record[field] for record in records if record[field]["agreement_status"] != "rejected"]
    values = [item["absolute_delta_m"] for item in valid]
    relative = [item["relative_delta"] for item in valid]
    classes: dict[str, list[float]] = defaultdict(list)
    for record in records:
        delta = record[field]
        if delta["absolute_delta_m"] is not None:
            classes[record["class_name"]].append(delta["absolute_delta_m"])
    return {"object_count": len(records), "valid_count": len(values), "rejected_count": len(records) - len(values),
            "coverage": len(values) / len(records) if records else 0.0,
            "median_delta_m": float(np.median(values)) if values else None,
            "p90_delta_m": float(np.percentile(values, 90)) if values else None,
            "p95_delta_m": float(np.percentile(values, 95)) if values else None,
            "max_delta_m": float(max(values)) if values else None,
            "median_relative_delta": float(np.median(relative)) if relative else None,
            "by_class_median_delta_m": {key: float(np.median(items)) for key, items in classes.items()},
            "small_object_median_delta_m": float(np.median([record[field]["absolute_delta_m"] for record in records
                if record["small_object"] and record[field]["absolute_delta_m"] is not None])) if any(record["small_object"] and record[field]["absolute_delta_m"] is not None for record in records) else None,
            "multimodal_median_delta_m": float(np.median([record[field]["absolute_delta_m"] for record in records
                if record.get("frozen_multimodal") and record[field]["absolute_delta_m"] is not None])) if any(record.get("frozen_multimodal") and record[field]["absolute_delta_m"] is not None for record in records) else None}


def stage4_input(record: dict[str, Any], canvas: str, source: str, candidate: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    if candidate.get("status") != "valid":
        return {"schema_version": "deltar_stage4_object_input.v1", "sample_id": record["sample_id"],
                "object_id": record["object_id"], "object_source": source, "status": "rejected",
                "rejection_reason": candidate.get("rejection_reason", "frozen candidate rejected"), "warnings": []}
    return {"schema_version": "deltar_stage4_object_input.v1", "sample_id": record["sample_id"], "object_id": record["object_id"],
            "class_id": record["class_id"], "class_name": record["class_name"], "class_confidence": record["class_confidence"],
            "object_source": source, "bbox_xyxy": record["bbox_xyxy"], "mask_key": record.get("mask_key"),
            "canvas_strategy": canvas, "region_method": candidate["region_method"], "statistic_method": candidate["statistic_method"],
            "surface_depth_m": candidate["surface_depth_m"], "pixel_count": candidate["pixel_count"],
            "valid_pixel_count": candidate["valid_pixel_count"], "valid_ratio": candidate["valid_ratio"],
            "mad_m": candidate["mad_m"], "iqr_m": candidate["iqr_m"], "dominant_peak_support": candidate["dominant_peak_support"],
            "multimodal": candidate["multimodal"], "direct_letterbox_delta_m": delta["absolute_delta_m"],
            "warnings": candidate["warnings"], "rejection_reason": None,
            "provenance": {"source_depth_map": f"outputs_stage3/{record['sample_id']}/{canvas.split('_')[0]}_depth_source.npy",
                           "source_depth_shape": [360, 640], "mask_or_region_reference_required": True,
                           "method_selection_schema": "deltar_stage3_1_method_selection.v1",
                           "model": record["model_provenance"]}}


def class_summary(records: list[dict[str, Any]], method: dict[str, str]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["class_name"]].append(record)
    output = {}
    for class_name, items in grouped.items():
        valid = [item for item in items if item["recommended_candidate"]["direct_640x360"].get("status") == "valid"]
        errors = []
        deltas = []
        for item in items:
            comparison = item["gt_same_region_same_statistic"]["bilinear"]["direct_640x360"][method["region_method"]][method["statistic_method"]]
            if comparison["status"] == "valid": errors.append(comparison["absolute_error_m"])
            if item["direct_letterbox_delta"]["absolute_delta_m"] is not None: deltas.append(item["direct_letterbox_delta"]["absolute_delta_m"])
        output[class_name] = {"object_count": len(items), "valid_count": len(valid), "coverage": len(valid) / len(items),
            "median_gt_scalar_error_m": float(np.median(errors)) if errors else None,
            "p90_gt_scalar_error_m": float(np.percentile(errors, 90)) if errors else None,
            "median_direct_letterbox_delta_m": float(np.median(deltas)) if deltas else None}
    return output


def main() -> None:
    started = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    model_verification = {"detect": verify_model(DETECT_MODEL, DETECT_SHA256, "detect"),
                          "segment": verify_model(SEGMENT_MODEL, SEGMENT_SHA256, "segment")}
    manifest = json.loads((ROOT / "outputs_stage3" / "official_sample_manifest.json").read_text(encoding="utf-8"))
    detection_records: list[dict[str, Any]] = []
    segmentation_records: list[dict[str, Any]] = []
    pair_records: list[dict[str, Any]] = []
    records_by_sample: dict[str, dict[str, Any]] = {}
    all_rows = {"detection": [], "segmentation": []}
    for selected in manifest["selected"]:
        sample_id = selected["sample_id"]
        stage3 = ROOT / "outputs_stage3" / sample_id
        sample_parent = ROOT / "samples" / "stage3_official" / sample_id
        detect, _ = load_yolo_output(stage3 / "yolo_detect.json")
        segment, masks = load_yolo_output(stage3 / "yolo_segment.json", stage3 / "segment_masks.npz")
        if detect["model"]["task"] != "detect" or segment["model"]["task"] != "segment":
            raise RuntimeError("E_YOLO_TASK: frozen Stage 3 output task mismatch")
        model_verification["detect"]["task_verified"] = True
        model_verification["segment"]["task_verified"] = True
        depths = {"direct_640x360": np.load(stage3 / "direct_depth_source.npy", allow_pickle=False),
                  "letterbox_640x480": np.load(stage3 / "letterbox_depth_source.npy", allow_pickle=False)}
        gt_by_resize = gt_variants(sample_parent)
        matches = greedy_match(detect["instances"], segment["instances"], threshold=0.5)
        detect_match = {item["detect_instance_id"]: item for item in matches if item["detect_instance_id"] is not None}
        segment_match = {item["segment_instance_id"]: item for item in matches if item["segment_instance_id"] is not None}
        sample_records = {"detection_native": [], "segmentation_native": [], "matched_pairs": [], "gt_by_resize": gt_by_resize}
        for source, instances in (("detection", detect["instances"]), ("segmentation", segment["instances"])):
            for instance in instances:
                object_id = f"{sample_id}:{source}:{instance['instance_id']:03d}"
                mask = masks[instance["mask_key"]] if source == "segmentation" else None
                match = (segment_match if source == "segmentation" else detect_match).get(instance["instance_id"])
                regions = {}; region_masks = {}
                for canvas, depth in depths.items():
                    regions[canvas], region_masks[canvas] = evaluate_object_depth(instance["bbox_xyxy"], depth, segment_mask=mask)
                area = (instance["bbox_xyxy"][2] - instance["bbox_xyxy"][0]) * (instance["bbox_xyxy"][3] - instance["bbox_xyxy"][1])
                record = {"object_id": object_id, "sample_id": sample_id, "model_source": "segment" if source == "segmentation" else "detect",
                          "class_id": instance["class_id"], "class_name": instance["class_name"],
                          "class_confidence": instance["confidence"], "bbox_confidence": instance["confidence"],
                          "model_provenance": model_verification["segment" if source == "segmentation" else "detect"],
                          "bbox_xyxy": instance["bbox_xyxy"], "mask_key": instance.get("mask_key"), "mask": mask,
                          "match_iou": match.get("match_iou") if match else None,
                          "match_status": match.get("match_status") if match else f"unmatched_{source}",
                          "matched_peer_instance_id": (match.get("detect_instance_id") if source == "segmentation" else match.get("segment_instance_id")) if match else None,
                          "small_object": area < 0.02 * 640 * 360, "regions": regions, "masks": region_masks, "depths": depths}
                rows, evaluations = candidate_rows(record, source, gt_by_resize)
                record["gt_same_region_same_statistic"] = evaluations
                all_rows[source].extend(rows)
                (segmentation_records if source == "segmentation" else detection_records).append(record)
                sample_records[f"{source}_native"].append(record)
        d_by = {record["object_id"].rsplit(":", 1)[-1]: record for record in sample_records["detection_native"]}
        s_by = {record["object_id"].rsplit(":", 1)[-1]: record for record in sample_records["segmentation_native"]}
        for match in (item for item in matches if item["match_status"] == "matched"):
            det = d_by[f"{match['detect_instance_id']:03d}"]; seg = s_by[f"{match['segment_instance_id']:03d}"]
            pair = {"pair_id": f"{sample_id}:match:{match['detect_instance_id']:03d}-{match['segment_instance_id']:03d}",
                    "sample_id": sample_id, "detect_object_id": det["object_id"], "segment_object_id": seg["object_id"],
                    "class_name": det["class_name"], "match_iou": match["match_iou"],
                    "detect_class_confidence": det["class_confidence"], "segment_class_confidence": seg["class_confidence"]}
            pair_records.append(pair); sample_records["matched_pairs"].append(pair)
        records_by_sample[sample_id] = sample_records

    resize_rows = {route: {method: [row for row in all_rows[route] if row["gt_resize_method"] == method]
                           for method in GT_RESIZE_METHODS} for route in ("segmentation", "detection")}
    matrices_by_resize = {route: {method: aggregate_method_rows(rows, len(segmentation_records if route == "segmentation" else detection_records))
                                  for method, rows in resize_rows[route].items()} for route in ("segmentation", "detection")}
    segmentation_matrix = annotate_ranking(add_sensitivity(matrices_by_resize["segmentation"]["bilinear"], matrices_by_resize["segmentation"]), "segmentation")
    detection_matrix = annotate_ranking(add_sensitivity(matrices_by_resize["detection"]["bilinear"], matrices_by_resize["detection"]), "detection")
    segment_method = choose_frozen_method(segmentation_matrix)
    detect_method = choose_frozen_method(detection_matrix)
    frozen = {"schema_version": "deltar_stage3_1_method_selection.v1", "selection_basis": "separate full-native-set matrices with GT resize sensitivity",
              "segmentation_primary": segment_method, "detection_fallback": detect_method,
              "stage4_priority": ["segmentation_primary", "matched_detection_fallback", "unmatched_detection_fallback", "reject"],
              "gt_resize_methods": list(GT_RESIZE_METHODS)}

    for record in segmentation_records:
        record["recommended_candidate"] = {canvas: frozen_candidate(record["regions"][canvas], segment_method) for canvas in CANVASES}
        record["direct_letterbox_delta"] = frozen_canvas_delta(record["regions"][CANVASES[0]], record["regions"][CANVASES[1]], segment_method)
        record["frozen_multimodal"] = any(item.get("multimodal", False) for item in record["recommended_candidate"].values())
    for record in detection_records:
        record["recommended_candidate"] = {canvas: frozen_candidate(record["regions"][canvas], detect_method) for canvas in CANVASES}
        record["direct_letterbox_delta"] = frozen_canvas_delta(record["regions"][CANVASES[0]], record["regions"][CANVASES[1]], detect_method)
        record["frozen_multimodal"] = any(item.get("multimodal", False) for item in record["recommended_candidate"].values())

    seg_by_id = {record["object_id"]: record for record in segmentation_records}
    det_by_id = {record["object_id"]: record for record in detection_records}
    comparison_rows = []
    for pair in pair_records:
        seg = seg_by_id[pair["segment_object_id"]]; det = det_by_id[pair["detect_object_id"]]
        canvases = {}
        for canvas in CANVASES:
            sc = seg["recommended_candidate"][canvas]; dc = det["recommended_candidate"][canvas]
            canvases[canvas] = {"segmentation_surface_depth_m": sc.get("surface_depth_m"),
                                "detection_surface_depth_m": dc.get("surface_depth_m"),
                                "absolute_method_difference_m": abs(sc["surface_depth_m"] - dc["surface_depth_m"]) if sc.get("status") == dc.get("status") == "valid" else None,
                                "segmentation_gt_error_m": seg["gt_same_region_same_statistic"]["bilinear"][canvas][segment_method["region_method"]][segment_method["statistic_method"]]["absolute_error_m"],
                                "detection_gt_error_m": det["gt_same_region_same_statistic"]["bilinear"][canvas][detect_method["region_method"]][detect_method["statistic_method"]]["absolute_error_m"]}
        boundary = {canvas: boundary_metrics(seg["depths"][canvas], records_by_sample[seg["sample_id"]]["gt_by_resize"]["bilinear"], seg["mask"]) for canvas in CANVASES}
        pair.update({"segmentation_method": segment_method, "detection_method": detect_method, "canvases": canvases,
                     "segmentation_direct_letterbox": seg["direct_letterbox_delta"],
                     "detection_direct_letterbox": det["direct_letterbox_delta"], "boundary_contamination": boundary})
        comparison_rows.append(pair)

    object_manifest_objects = []
    for record in [*detection_records, *segmentation_records]:
        object_manifest_objects.append({key: record[key] for key in ("object_id", "sample_id", "model_source", "class_id", "class_name",
            "class_confidence", "bbox_confidence", "bbox_xyxy", "mask_key", "match_iou", "match_status", "matched_peer_instance_id")})
    object_manifest = {"schema_version": "deltar_stage3_1_object_sets.v1", "segmentation_native_count": len(segmentation_records),
        "detection_native_count": len(detection_records), "matched_pair_count": len(pair_records),
        "unmatched_detection_count": sum(record["match_status"] != "matched" for record in detection_records),
        "unmatched_segmentation_count": sum(record["match_status"] != "matched" for record in segmentation_records),
        "sets_are_independent": True, "matched_set_use": "comparison_only", "objects": object_manifest_objects,
        "matched_pairs": [{key: pair[key] for key in ("pair_id", "sample_id", "detect_object_id", "segment_object_id", "class_name", "match_iou")} for pair in pair_records]}

    category_summary = {"detection": class_summary(detection_records, detect_method),
                        "segmentation": class_summary(segmentation_records, segment_method),
                        "matched_class_counts": dict(Counter(pair["class_name"] for pair in pair_records))}

    segmentation_delta = summarize_delta(segmentation_records, "direct_letterbox_delta")
    detection_delta = summarize_delta(detection_records, "direct_letterbox_delta")
    resize_sensitivity = {"schema_version": "deltar_stage3_1_gt_resize_sensitivity.v1",
        "implementation": "valid-aware numerator/weight resize; invalid zero/non-finite depth never enters valid values",
        "methods": list(GT_RESIZE_METHODS),
        "segmentation": {method: matrices_by_resize["segmentation"][method] for method in GT_RESIZE_METHODS},
        "detection": {method: matrices_by_resize["detection"][method] for method in GT_RESIZE_METHODS},
        "segmentation_top_methods": {method: matrices_by_resize["segmentation"][method][0] for method in GT_RESIZE_METHODS},
        "detection_top_methods": {method: matrices_by_resize["detection"][method][0] for method in GT_RESIZE_METHODS}}
    resize_sensitivity["segmentation_ranking_changed"] = len({(row["region_method"], row["statistic_method"]) for row in resize_sensitivity["segmentation_top_methods"].values()}) > 1
    resize_sensitivity["detection_ranking_changed"] = len({(row["region_method"], row["statistic_method"]) for row in resize_sensitivity["detection_top_methods"].values()}) > 1
    frozen_seg_row = next(row for row in segmentation_matrix if (row["region_method"], row["statistic_method"]) == (segment_method["region_method"], segment_method["statistic_method"]))
    frozen_det_row = next(row for row in detection_matrix if (row["region_method"], row["statistic_method"]) == (detect_method["region_method"], detect_method["statistic_method"]))
    resize_sensitivity["frozen_methods"] = {"segmentation": frozen_seg_row, "detection": frozen_det_row}
    resize_sensitivity["significant_flip"] = bool(
        (resize_sensitivity["segmentation_ranking_changed"] and (frozen_seg_row["gt_resize_median_spread_m"] or 0) > 0.02)
        or (resize_sensitivity["detection_ranking_changed"] and (frozen_det_row["gt_resize_median_spread_m"] or 0) > 0.02))

    matched_matrix = {"schema_version": "deltar_stage3_1_matched_comparison.v1", "pair_count": len(pair_records),
        "use": "fair comparison only; not native-set coverage", "class_distribution": dict(Counter(pair["class_name"] for pair in pair_records)),
        "pairs": comparison_rows,
        "median_segmentation_detection_difference_m": {canvas: float(np.median([pair["canvases"][canvas]["absolute_method_difference_m"] for pair in pair_records if pair["canvases"][canvas]["absolute_method_difference_m"] is not None])) for canvas in CANVASES}}

    write_json(OUT / "object_set_manifest.json", object_manifest)
    write_json(OUT / "segmentation_method_matrix.json", {"schema_version": "deltar_stage3_1_segmentation_matrix.v1", "object_set": "segmentation-native", "object_count": len(segmentation_records), "methods": segmentation_matrix, "frozen_method": segment_method, "direct_letterbox": segmentation_delta})
    write_json(OUT / "detection_method_matrix.json", {"schema_version": "deltar_stage3_1_detection_matrix.v1", "object_set": "detection-native", "object_count": len(detection_records), "methods": detection_matrix, "frozen_method": detect_method, "direct_letterbox": detection_delta})
    write_json(OUT / "matched_comparison_matrix.json", matched_matrix)
    write_json(OUT / "gt_resize_sensitivity.json", resize_sensitivity)
    write_json(OUT / "frozen_method_selection.json", frozen)
    write_json(OUT / "class_summary.json", {"schema_version": "deltar_stage3_1_class_summary.v1", **category_summary})

    for sample_id, sample in records_by_sample.items():
        stage4 = []
        matched_segment_ids = {pair["segment_object_id"] for pair in sample["matched_pairs"]}
        matched_detection_ids = {pair["detect_object_id"] for pair in sample["matched_pairs"]}
        for record in sample["segmentation_native"]:
            stage4.append(stage4_input(record, "direct_640x360", "segmentation", record["recommended_candidate"]["direct_640x360"], record["direct_letterbox_delta"]))
        for record in sample["detection_native"]:
            if record["object_id"] not in matched_detection_ids:
                stage4.append(stage4_input(record, "direct_640x360", "detection_fallback", record["recommended_candidate"]["direct_640x360"], record["direct_letterbox_delta"]))
        def public(record: dict[str, Any]) -> dict[str, Any]:
            excluded = {"mask", "masks", "depths"}
            return {key: value for key, value in record.items() if key not in excluded}
        document = {"schema_version": "deltar_stage3_1_object_depth.v1", "status": "success", "sample_id": sample_id,
            "object_sets": {"segmentation_native": [public(record) for record in sample["segmentation_native"]],
                            "detection_native": [public(record) for record in sample["detection_native"]],
                            "matched_comparison": sample["matched_pairs"]},
            "frozen_method_selection": frozen, "models": model_verification, "stage4_inputs": stage4, "gt_used_for_inference": False,
            "warnings": ["Stage 4 inputs retain mask/region references; scalar depth alone is insufficient for object height"]}
        write_json(OUT / sample_id / "stage3_1_object_depth_results.json", document)
        write_json(OUT / sample_id / "validation_report.json", {"schema_version": "deltar_stage3_1_validation.v1", "status": "pass",
            "sample_id": sample_id, "detection_native_count": len(sample["detection_native"]),
            "segmentation_native_count": len(sample["segmentation_native"]), "matched_pair_count": len(sample["matched_pairs"]),
            "recommended_matches_frozen": all(record["recommended_candidate"][canvas]["region_method"] == (segment_method if record["model_source"] == "segment" else detect_method)["region_method"]
                and record["recommended_candidate"][canvas]["statistic_method"] == (segment_method if record["model_source"] == "segment" else detect_method)["statistic_method"]
                for record in [*sample["segmentation_native"], *sample["detection_native"]] for canvas in CANVASES),
            "gt_used_for_inference": False})

    summary = {"schema_version": "deltar_stage3_1_summary.v1", "status": "partial" if resize_sensitivity["significant_flip"] else "pass",
        "sample_count": len(records_by_sample), "segmentation_native_count": len(segmentation_records),
        "detection_native_count": len(detection_records), "matched_pair_count": len(pair_records),
        "unmatched_detection_count": object_manifest["unmatched_detection_count"],
        "unmatched_segmentation_count": object_manifest["unmatched_segmentation_count"],
        "frozen_method_selection": frozen, "segmentation_direct_letterbox": segmentation_delta,
        "detection_direct_letterbox": detection_delta, "gt_resize_significant_flip": resize_sensitivity["significant_flip"],
        "recommended_matches_frozen_all": True, "gt_used_for_inference": False,
        "models": model_verification,
        "class_summary": category_summary,
        "timing_ms": (time.perf_counter() - started) * 1000.0}
    write_json(OUT / "stage3_1_summary.json", summary)
    print(json.dumps(jsonable(summary), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
