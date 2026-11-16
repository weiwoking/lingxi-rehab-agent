from __future__ import annotations

import json
from pathlib import Path

try:
    from scripts.build_stage4_inputs import build as build_inputs
except ModuleNotFoundError:  # direct script execution
    from build_stage4_inputs import build as build_inputs
from deltar_p4_pipeline.stage4_validation import load_geometry_profile, run_manifest
from deltar_p4_pipeline.synthetic_geometry import synthetic_calibration

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs_stage4" / "official_matrix.json"


def concise(obj: dict) -> dict:
    geometry = obj.get("selected_geometry") or {}
    agreement = obj.get("canvas_agreement") or {}
    return {
        "logical_object_id": obj["logical_object_id"],
        "class_name": obj["class_name"],
        "status": obj["status"],
        "selected_source": obj.get("selected_source"),
        "fallback_used": obj.get("fallback_used"),
        "point_count": geometry.get("point_count"),
        "mask_support": geometry.get("quality_inputs", {}).get("mask_support"),
        "distance_m": geometry.get("distance_m"),
        "object_height_m": geometry.get("object_height_m"),
        "bottom_height_m": geometry.get("bottom_height_m"),
        "absolute_geometry_valid": obj.get("absolute_geometry_valid", False),
        "automatic_geometry_valid": obj.get("automatic_geometry_valid", False),
        "canvas_selection_status": obj.get("canvas_selection_status"),
        "selected_canvas": obj.get("selected_canvas"),
        "selection_is_provisional": obj.get("selection_is_provisional", False),
        "single_canvas_only": obj.get("single_canvas_only", False),
        "canvas_agreement": agreement.get("agreement_status"),
        "distance_delta_m": agreement.get("distance_delta_m"),
        "height_delta_m": agreement.get("height_delta_m"),
        "bottom_height_delta_m": agreement.get("bottom_height_delta_m"),
        "rejection_reason": obj.get("rejection_reason"),
    }


def run() -> dict:
    manifest = build_inputs()
    profile = load_geometry_profile(ROOT / "geometry_profile.json")
    calibration = synthetic_calibration(status="unverified", semantics="axial_z")
    calibration["profile_id"] = "synthetic_test_calibration_for_internal_consistency_only"
    result = run_manifest(manifest, calibration, profile, ROOT)
    rows = [concise(obj) for obj in result["objects"]]
    success = [row for row in rows if row["status"] == "success"]
    rejected = [row for row in rows if row["status"] != "success"]
    selection_states = ("agreed", "disagreed", "direct_only", "letterbox_only", "both_rejected")
    canvas_selection_counts = {state: sum(row["canvas_selection_status"] == state for row in rows) for state in selection_states}
    by_support = sorted(success, key=lambda row: row.get("mask_support") or 0)
    representatives = [by_support[0], by_support[-1]] if len(by_support) >= 2 else by_support
    deltas = [row["distance_delta_m"] for row in success if row["distance_delta_m"] is not None]
    payload = {
        "schema_version": "deltar_stage4_official_matrix.v1",
        "status": "internal_consistency_only",
        "official_absolute_geometry_not_evaluated": True,
        "calibration_status": "unverified",
        "absolute_geometry_valid": False,
        "absolute_geometry_valid_true_count": sum(row["absolute_geometry_valid"] for row in rows),
        "canvas_selection_counts": canvas_selection_counts,
        "object_count": len(rows),
        "success_count": len(success),
        "rejected_count": len(rejected),
        "fallback_count": sum(bool(row["fallback_used"]) for row in success),
        "direct_letterbox_distance_delta_m": {"count": len(deltas), "median": None if not deltas else sorted(deltas)[len(deltas)//2], "max": None if not deltas else max(deltas)},
        "objects": rows,
        "representative_objects": representatives,
        "warnings": ["No traceable measured intrinsics or Camera-to-Ground transform for these demo samples; numeric heights are not real-world accuracy claims."],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    result = run()
    print(json.dumps({key: result[key] for key in ("object_count", "success_count", "rejected_count", "fallback_count", "canvas_selection_counts", "absolute_geometry_valid_true_count")}, indent=2))
