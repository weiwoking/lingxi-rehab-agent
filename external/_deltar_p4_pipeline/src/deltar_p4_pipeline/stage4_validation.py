from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np

from .calibration import validate_calibration
from .errors import ErrorCode, PipelineError
from .geometry_quality import compute_absolute_geometry_valid, experimental_ranking_score
from .object_geometry import compare_canvases, summarize_geometry
from .point_cloud import bbox_mask, build_point_cloud, erode_mask
from .stage4_contracts import OUTPUT_SCHEMA

CRITICAL_GEOMETRY_WARNINGS = {
    "W_DETECTION_BBOX_BACKGROUND_CONTAMINATION",
    "W_OBJECT_TOUCHES_IMAGE_BORDER",
}


def load_geometry_profile(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_mask(
    candidate: dict[str, Any],
    root: Path,
    segment_masks: dict[str, np.ndarray] | None = None,
) -> np.ndarray:
    key = candidate.get("mask_key")
    if segment_masks is not None and key in segment_masks:
        mask = segment_masks[key]
        if mask.shape != (360, 640):
            raise PipelineError(ErrorCode.STAGE4_MASK_SHAPE, "mask must be [360,640]", details=list(mask.shape))
        return erode_mask(mask, 3)
    path = root / candidate["mask_npz_path"]
    if not path.is_file():
        raise PipelineError(ErrorCode.STAGE4_MASK_KEY, "mask NPZ does not exist", str(path))
    with np.load(path) as archive:
        if key not in archive.files:
            raise PipelineError(ErrorCode.STAGE4_MASK_KEY, "mask key not found", str(path), key)
        mask = archive[key]
    if mask.shape != (360, 640):
        raise PipelineError(ErrorCode.STAGE4_MASK_SHAPE, "mask must be [360,640]", details=list(mask.shape))
    return erode_mask(mask, 3)


def select_canvas_geometry(canvas_results: dict[str, Any], agreement: dict[str, Any]) -> dict[str, Any]:
    direct_ok = canvas_results.get("direct_640x360", {}).get("status") == "success"
    letterbox_ok = canvas_results.get("letterbox_640x480", {}).get("status") == "success"
    if direct_ok and letterbox_ok:
        if agreement.get("agreement_status") == "agree":
            selected_canvas = "direct_640x360"
            geometry = deepcopy(canvas_results[selected_canvas]["geometry"])
            critical = [warning for warning in geometry.get("warnings", []) if warning in CRITICAL_GEOMETRY_WARNINGS]
            return {
                "canvas_selection_status": "agreed",
                "selected_canvas": selected_canvas,
                "selected_geometry": geometry,
                "selection_is_provisional": True,
                "single_canvas_only": False,
                "automatic_geometry_valid": not critical,
                "critical_geometry_rejections": critical,
                "selection_error": None,
            }
        error = PipelineError(
            ErrorCode.GEOMETRY_CANVAS_DISAGREEMENT,
            "direct and letterbox geometry disagree beyond tolerance",
            details=agreement,
        ).as_dict()
        return {
            "canvas_selection_status": "disagreed",
            "selected_canvas": None,
            "selected_geometry": None,
            "selection_is_provisional": False,
            "single_canvas_only": False,
            "automatic_geometry_valid": False,
            "critical_geometry_rejections": [ErrorCode.GEOMETRY_CANVAS_DISAGREEMENT.value],
            "selection_error": error,
        }
    if direct_ok or letterbox_ok:
        selected_canvas = "direct_640x360" if direct_ok else "letterbox_640x480"
        rejected_canvas = "letterbox_640x480" if direct_ok else "direct_640x360"
        rejection = canvas_results.get(rejected_canvas, {}).get("error", {})
        rejection_code = rejection.get("error_code", "E_GEOMETRY_CANVAS_CANDIDATE_REJECTED")
        return {
            "canvas_selection_status": "direct_only" if direct_ok else "letterbox_only",
            "selected_canvas": selected_canvas,
            "selected_geometry": deepcopy(canvas_results[selected_canvas]["geometry"]),
            "selection_is_provisional": True,
            "single_canvas_only": True,
            "automatic_geometry_valid": False,
            "critical_geometry_rejections": [rejection_code],
            "selection_error": None,
        }
    return {
        "canvas_selection_status": "both_rejected",
        "selected_canvas": None,
        "selected_geometry": None,
        "selection_is_provisional": False,
        "single_canvas_only": False,
        "automatic_geometry_valid": False,
        "critical_geometry_rejections": [
            item.get("error", {}).get("error_code", "E_GEOMETRY_CANVAS_CANDIDATE_REJECTED")
            for item in canvas_results.values()
            if item.get("status") != "success"
        ],
        "selection_error": None,
    }


def evaluate_candidate(
    candidate: dict[str, Any],
    group: dict[str, Any],
    calibration: dict[str, Any],
    profile: dict[str, Any],
    root: str | Path,
    *,
    depth_arrays: dict[str, np.ndarray] | None = None,
    segment_masks: dict[str, np.ndarray] | None = None,
) -> dict[str, Any]:
    root = Path(root)
    canvas_results: dict[str, Any] = {}
    for canvas, depth_ref in group["source_depth_refs"].items():
        try:
            depth = (
                depth_arrays[canvas]
                if depth_arrays is not None and canvas in depth_arrays
                else np.load(root / depth_ref)
            )
            if candidate["source_type"] == "segmentation":
                mask = _load_mask(candidate, root, segment_masks)
            else:
                mask = bbox_mask((360, 640), candidate["bbox_xyxy"])
            stage3 = candidate.get("canvas_candidates", {}).get(canvas, {}).get("surface_depth_m")
            cloud = build_point_cloud(depth, mask, calibration, profile, stage3_surface_depth_m=stage3, source_type=candidate["source_type"])
            geometry = summarize_geometry(cloud, calibration, profile)
            geometry["quality_inputs"].update({"source_type": candidate["source_type"], "mask_support": int(mask.sum()), "fallback_used": candidate["source_type"] != "segmentation"})
            geometry["geometry_ranking_score_experimental"] = experimental_ranking_score(geometry["quality_inputs"])
            canvas_results[canvas] = {"status": "success", "geometry": geometry}
        except (PipelineError, OSError, ValueError) as exc:
            error = exc.as_dict() if isinstance(exc, PipelineError) else PipelineError(ErrorCode.STAGE4_OUTPUT_WRITE, str(exc)).as_dict()
            canvas_results[canvas] = {"status": "rejected", "error": error}
    direct = canvas_results.get("direct_640x360", {}).get("geometry")
    letterbox = canvas_results.get("letterbox_640x480", {}).get("geometry")
    agreement = compare_canvases(direct, letterbox, profile)
    selection = select_canvas_geometry(canvas_results, agreement)
    return {
        "canvas_results": canvas_results,
        "canvas_agreement": agreement,
        **selection,
        "success": selection["selected_geometry"] is not None,
    }


def process_object_group(
    group: dict[str, Any],
    calibration: dict[str, Any],
    profile: dict[str, Any],
    root: str | Path,
    *,
    depth_arrays: dict[str, np.ndarray] | None = None,
    segment_masks: dict[str, np.ndarray] | None = None,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    seg = group["segmentation_primary"]
    det = group["detection_fallback"]
    selected: tuple[str, dict[str, Any], dict[str, Any]] | None = None
    if seg.get("available"):
        result = evaluate_candidate(
            seg,
            group,
            calibration,
            profile,
            root,
            depth_arrays=depth_arrays,
            segment_masks=segment_masks,
        )
        attempts.append({"source": "segmentation", **result})
        if result["success"]:
            selected = ("segmentation", seg, result)
    if selected is None and det.get("available"):
        result = evaluate_candidate(
            det,
            group,
            calibration,
            profile,
            root,
            depth_arrays=depth_arrays,
            segment_masks=segment_masks,
        )
        source = "matched_detection_fallback" if group["match"]["status"] == "matched" else "detection_fallback"
        attempts.append({"source": source, **result})
        if result["success"]:
            selected = (source, det, result)
    if selected is None:
        last = attempts[-1] if attempts else {}
        selection_status = last.get("canvas_selection_status", "both_rejected")
        selection_error = last.get("selection_error")
        return {
            "logical_object_id": group["logical_object_id"],
            "class_name": group["class_name"],
            "status": "rejected",
            "selected_source": None,
            "fallback_used": bool(attempts and len(attempts) > 1),
            "canvas_results": last.get("canvas_results", {}),
            "canvas_agreement": last.get("canvas_agreement"),
            "canvas_selection_status": selection_status,
            "selected_canvas": None,
            "selected_geometry": None,
            "selection_is_provisional": False,
            "single_canvas_only": False,
            "automatic_geometry_valid": False,
            "absolute_geometry_valid": False,
            "critical_geometry_rejections": last.get("critical_geometry_rejections", []),
            "errors": [] if selection_error is None else [selection_error],
            "warnings": [],
            "attempts": attempts,
            "rejection_reason": (selection_error or {}).get("error_code", "no valid segmentation or detection geometry"),
            "provenance": group["provenance"],
        }
    source, candidate, result = selected
    preferred_canvas = result["selected_canvas"]
    selected_geometry = result["selected_geometry"]
    selected_geometry["quality_inputs"]["canvas_agreement"] = result["canvas_agreement"]["agreement_status"]
    selected_geometry["automatic_geometry_valid"] = result["automatic_geometry_valid"]
    absolute_valid = compute_absolute_geometry_valid(
        calibration_status=calibration["calibration_status"],
        depth_semantics_status=calibration["depth"]["semantics_status"],
        selected_geometry_status="success",
        canvas_selection_status=result["canvas_selection_status"],
        automatic_geometry_valid=result["automatic_geometry_valid"],
        critical_geometry_rejections=result["critical_geometry_rejections"],
    )
    selected_geometry["absolute_geometry_valid"] = absolute_valid
    return {
        "logical_object_id": group["logical_object_id"],
        "class_name": group["class_name"],
        "class_confidence": candidate.get("confidence"),
        "status": "success",
        "selected_source": source,
        "fallback_used": source != "segmentation",
        "fallback_reason": "segmentation unavailable or rejected" if source != "segmentation" else None,
        "segmentation_status": attempts[0]["success"] if seg.get("available") and attempts else None,
        "detection_status": next((a["success"] for a in attempts if "detection" in a["source"]), None),
        "canvas_results": result["canvas_results"],
        "canvas_agreement": result["canvas_agreement"],
        "canvas_selection_status": result["canvas_selection_status"],
        "selected_canvas": preferred_canvas,
        "selection_is_provisional": result["selection_is_provisional"],
        "single_canvas_only": result["single_canvas_only"],
        "automatic_geometry_valid": result["automatic_geometry_valid"],
        "absolute_geometry_valid": absolute_valid,
        "critical_geometry_rejections": result["critical_geometry_rejections"],
        "preferred_canvas_candidate_experimental": preferred_canvas,
        "selected_geometry": selected_geometry,
        "errors": [],
        "warnings": ["W_SINGLE_CANVAS_ONLY"] if result["single_canvas_only"] else [],
        "attempts": attempts,
        "rejection_reason": None,
        "provenance": group["provenance"],
    }


def run_manifest(manifest: dict[str, Any], calibration: dict[str, Any], profile: dict[str, Any], root: str | Path) -> dict[str, Any]:
    validate_calibration(calibration)
    objects = [process_object_group(group, calibration, profile, root) for group in manifest["object_groups"]]
    successes = sum(obj["status"] == "success" for obj in objects)
    status = "success" if successes == len(objects) else ("partial" if successes else "rejected")
    absolute_valid = bool(objects) and all(obj["status"] == "success" and obj["absolute_geometry_valid"] for obj in objects)
    warnings = []
    if calibration["calibration_status"] != "measured":
        warnings.append("W_ABSOLUTE_GEOMETRY_UNVERIFIED")
    if calibration["depth"]["semantics_status"] != "PROVEN":
        warnings.append("W_DEPTH_SEMANTICS_UNPROVEN")
    return {
        "schema_version": OUTPUT_SCHEMA,
        "status": status,
        "calibration": {"profile_id": calibration["profile_id"], "status": calibration["calibration_status"]},
        "depth_semantics": {"value": calibration["depth"]["semantics"], "status": calibration["depth"]["semantics_status"]},
        "absolute_geometry_valid": absolute_valid,
        "objects": objects,
        "warnings": warnings,
    }
