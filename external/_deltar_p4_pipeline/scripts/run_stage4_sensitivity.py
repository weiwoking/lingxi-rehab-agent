from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np

from deltar_p4_pipeline.synthetic_geometry import evaluate_synthetic, rectangle_depth, rotation_xyz, synthetic_calibration

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs_stage4" / "sensitivity_report.json"
METRICS = ("distance_m", "object_height_m", "bottom_height_m")


def percentile(values: list[float], q: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=float), q))


def run() -> dict:
    profile = json.loads((ROOT / "geometry_profile.json").read_text(encoding="utf-8"))
    base_cal = synthetic_calibration()
    source, mask, truth = rectangle_depth(base_cal, distance_m=2.5, bottom_m=0.1, height_m=1.0)
    base = evaluate_synthetic(source, mask, base_cal, profile, truth)["geometry"]
    cases: list[dict] = []

    def evaluate_case(category: str, parameter: str, delta, cal=None, depth=None):
        geometry = evaluate_synthetic(source if depth is None else depth, mask, base_cal if cal is None else cal, profile, truth)["geometry"]
        changes = {metric: {"absolute_change_m": abs(geometry[metric] - base[metric]), "relative_change": abs(geometry[metric] - base[metric]) / max(abs(base[metric]), 1e-9)} for metric in METRICS}
        cases.append({"category": category, "parameter": parameter, "delta": delta, "changes": changes})

    for pct in (-0.10, -0.05, -0.02, 0.02, 0.05, 0.10):
        changed = source.copy(); changed[mask] *= 1.0 + pct
        evaluate_case("depth", "scale", pct, depth=changed)
    for bias in (-0.05, -0.02, 0.02, 0.05):
        changed = source.copy(); changed[mask] += bias
        evaluate_case("depth", "bias_m", bias, depth=changed)
    for pct in (-0.05, -0.03, -0.01, 0.01, 0.03, 0.05):
        cal = copy.deepcopy(base_cal); cal["camera_model"]["fx"] *= 1 + pct; cal["camera_model"]["fy"] *= 1 + pct
        evaluate_case("intrinsics", "fx_fy_scale", pct, cal=cal)
    for shift in (-5, -2, 2, 5):
        cal = copy.deepcopy(base_cal); cal["camera_model"]["cx"] += shift; cal["camera_model"]["cy"] += shift
        evaluate_case("intrinsics", "cx_cy_px", shift, cal=cal)
    for shift in (-0.05, -0.02, -0.01, 0.01, 0.02, 0.05):
        cal = copy.deepcopy(base_cal); cal["transforms"]["T_ground_from_camera"][2][3] += shift
        evaluate_case("camera_ground", "camera_height_m", shift, cal=cal)
    for axis, values in (("pitch_deg", (-2, -1, -0.5, 0.5, 1, 2)), ("yaw_deg", (-3, -1, 1, 3)), ("roll_deg", (-3, -1, 1, 3))):
        for value in values:
            cal = copy.deepcopy(base_cal)
            kwargs = {"roll_deg": 0.0, "pitch_deg": 0.0, "yaw_deg": 0.0}; kwargs[axis] = value
            rotation = rotation_xyz(**kwargs)
            current = np.asarray(cal["transforms"]["T_ground_from_camera"], dtype=float)
            current[:3, :3] = rotation @ current[:3, :3]
            cal["transforms"]["T_ground_from_camera"] = current.tolist()
            evaluate_case("camera_ground", axis, value, cal=cal)
    summaries = {}
    for category in sorted(set(case["category"] for case in cases)):
        subset = [case for case in cases if case["category"] == category]
        summaries[category] = {}
        for metric in METRICS:
            absolute = [case["changes"][metric]["absolute_change_m"] for case in subset]
            relative = [case["changes"][metric]["relative_change"] for case in subset]
            summaries[category][metric] = {"median_absolute_change_m": percentile(absolute, 0.5), "p90_absolute_change_m": percentile(absolute, 0.9), "p95_absolute_change_m": percentile(absolute, 0.95), "median_relative_change": percentile(relative, 0.5), "p90_relative_change": percentile(relative, 0.9), "p95_relative_change": percentile(relative, 0.95)}
    ranking = []
    for case in cases:
        ranking.append({"factor": f"{case['parameter']}={case['delta']}", "max_absolute_change_m": max(case["changes"][metric]["absolute_change_m"] for metric in METRICS)})
    ranking.sort(key=lambda row: row["max_absolute_change_m"], reverse=True)
    payload = {
        "schema_version": "deltar_stage4_sensitivity.v1",
        "status": "pass",
        "baseline": {metric: base[metric] for metric in METRICS},
        "case_count": len(cases),
        "cases": cases,
        "summary": summaries,
        "most_sensitive": ranking[:10],
        "camera_tof": {"status": "upstream_alignment_sensitivity_not_fully_evaluated", "reason": "Stage 2 has no real Camera-ToF fr/zone projector. T_camera_from_tof is intentionally not re-applied during camera-aligned dense-depth backprojection.", "qualitative_error_sources": ["baseline error shifts projected zone footprints", "ToF plane rotation changes zone orientation", "translation error changes parallax, strongest near the camera and at depth discontinuities"]},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    result = run()
    print(json.dumps({"case_count": result["case_count"], "most_sensitive": result["most_sensitive"][:3]}, indent=2))
