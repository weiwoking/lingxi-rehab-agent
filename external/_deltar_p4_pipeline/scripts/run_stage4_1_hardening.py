from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import numpy as np

try:
    from scripts.run_stage4_official_matrix import run as run_official
    from scripts.run_stage4_synthetic_matrix import run as run_synthetic
except ModuleNotFoundError:  # direct script execution
    from run_stage4_official_matrix import run as run_official
    from run_stage4_synthetic_matrix import run as run_synthetic

from deltar_p4_pipeline.object_geometry import summarize_geometry
from deltar_p4_pipeline.point_cloud import build_point_cloud
from deltar_p4_pipeline.synthetic_geometry import rectangle_depth, synthetic_calibration

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs_stage4_1" / "hardening_summary.json"
CASES = {"meters": 1.0, "millimeters": 0.001, "centimeters": 0.01}


def run_unit_equivalence(profile: dict[str, Any]) -> dict[str, Any]:
    base_calibration = synthetic_calibration()
    depth_m, mask, _ = rectangle_depth(base_calibration, distance_m=2.5, bottom_m=0.1, height_m=0.9)
    results: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for name, scale in CASES.items():
        calibration = copy.deepcopy(base_calibration)
        calibration["depth"]["scale_to_meter"] = scale
        raw_depth = depth_m / scale
        cloud = build_point_cloud(raw_depth, mask, calibration, profile)
        results[name] = (cloud, summarize_geometry(cloud, calibration, profile))

    reference_cloud, reference_geometry = results["meters"]
    comparisons: dict[str, Any] = {}
    for name, (cloud, geometry) in results.items():
        point_delta = float(np.max(np.abs(cloud["points_camera"] - reference_cloud["points_camera"])))
        metric_deltas = {
            metric: abs(float(geometry[metric]) - float(reference_geometry[metric]))
            for metric in ("distance_m", "object_height_m", "bottom_height_m")
        }
        comparisons[name] = {
            "scale_to_meter": CASES[name],
            "retained_pixel_mask_equal": bool(np.array_equal(cloud["pixel_uv"], reference_cloud["pixel_uv"])),
            "depth_mad_delta_m": abs(cloud["filter_metrics"]["depth_mad_m"] - reference_cloud["filter_metrics"]["depth_mad_m"]),
            "depth_iqr_delta_m": abs(cloud["filter_metrics"]["depth_iqr_m"] - reference_cloud["filter_metrics"]["depth_iqr_m"]),
            "dominant_peak_support_delta": abs(cloud["filter_metrics"]["dominant_peak_support"] - reference_cloud["filter_metrics"]["dominant_peak_support"]),
            "points_camera_max_delta_m": point_delta,
            **{f"{key}_delta_m": value for key, value in metric_deltas.items()},
        }
    tolerance = 1e-12
    passed = all(
        row["retained_pixel_mask_equal"]
        and max(value for key, value in row.items() if key.endswith("_delta_m") or key.endswith("support_delta")) <= tolerance
        for row in comparisons.values()
    )
    return {"passed": passed, "tolerance": tolerance, "cases": comparisons}


def run() -> dict[str, Any]:
    profile = json.loads((ROOT / "geometry_profile.json").read_text(encoding="utf-8"))
    synthetic = run_synthetic()
    official = run_official()
    unit_tests = run_unit_equivalence(profile)
    rows = official["objects"]
    payload = {
        "schema_version": "deltar_stage4_1_hardening_summary.v1",
        "canvas_selection_counts": official["canvas_selection_counts"],
        "absolute_geometry_valid_true_count": sum(row["absolute_geometry_valid"] for row in rows),
        "unit_equivalence_tests": unit_tests,
        "synthetic_matrix_passed": synthetic["summary"]["passed"] == synthetic["summary"]["scene_count"] == 10,
        "official_object_count": len(rows),
        "contract_checks": {
            "disagreed_selected_geometry_null": all(
                row["canvas_selection_status"] != "disagreed" or row.get("distance_m") is None for row in rows
            ),
            "single_canvas_explicitly_marked": all(
                row["canvas_selection_status"] not in {"direct_only", "letterbox_only"} or row["single_canvas_only"]
                for row in rows
            ),
            "official_absolute_geometry_all_false": all(not row["absolute_geometry_valid"] for row in rows),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, ensure_ascii=False))
