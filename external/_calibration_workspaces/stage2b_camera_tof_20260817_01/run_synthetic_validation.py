from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)
    return module


s2b = load("s2b_tool", ROOT / "stage2b_camera_tof.py")
tests = load("s2b_tests", ROOT / "test_stage2b_toolchain.py")


def main() -> int:
    obs, truth = tests.synthetic_observations()
    grid = s2b.candidate_grid(obs, "strict")
    selected = s2b.enrich_selected(dict(grid[0]), obs, "strict")
    estimate = np.asarray(selected["params"], float)
    r_true, _ = cv2.Rodrigues(truth[:3]); r_est, _ = cv2.Rodrigues(estimate[:3])
    rotation_error = math.degrees(math.acos(np.clip((np.trace(r_est @ r_true.T) - 1) / 2, -1, 1)))
    translation_error = float(np.linalg.norm(estimate[3:6] - truth[3:6]))
    ray_true = s2b.tof_rays(truth); ray_est = s2b.tof_rays(estimate)
    ray_error = np.degrees(np.arccos(np.clip(np.sum(ray_true * ray_est, axis=1), -1, 1)))
    orientation = {}
    for row in grid: orientation.setdefault(row["orientation"], row)
    orientation_ranking = sorted(orientation.values(), key=lambda x: x["score"])
    semantics = {}
    for row in grid: semantics.setdefault(row["range_semantics"], row)
    semantics_ranking = sorted(semantics.values(), key=lambda x: x["score"])
    ids = [p["pose_id"] for p in obs["poses"]]
    lopo = []
    for held in ids:
        train = s2b.observation_rows(obs, "strict", set(ids) - {held}); test = s2b.observation_rows(obs, "strict", {held})
        fit = s2b.robust_lm(train, selected["orientation"], selected["range_semantics"], selected["model"], initial=estimate, max_iterations=18)
        _, metric = s2b.evaluate_params(fit["params"], test, selected["orientation"], selected["range_semantics"], selected["model"])
        lopo.append({"heldout_pose_id": held, "metrics": metric})
    broad = s2b.observation_rows(obs, "broad"); strict = s2b.observation_rows(obs, "strict")
    broad_fit = s2b.robust_lm(broad, "flip_horizontal", "radial_range", "B_low_dimensional", max_iterations=30)
    strict_fit = s2b.robust_lm(strict, "flip_horizontal", "radial_range", "B_low_dimensional", max_iterations=30)
    model_c = s2b.fit_model_c_refinement(estimate.tolist(), strict, selected["orientation"], selected["range_semantics"])
    result = {
        "schema_version": "stage2b_synthetic_validation.v1",
        "truth": {"orientation": "flip_horizontal", "range_semantics": "radial_range", "params": truth.tolist()},
        "recovery": {
            "rank_1_orientation": orientation_ranking[0]["orientation"],
            "rank_1_range_semantics": semantics_ranking[0]["range_semantics"],
            "orientation_objective_margin_mm": orientation_ranking[1]["score"] - orientation_ranking[0]["score"],
            "orientation_data_only_margin_mm": sorted(x["data_only_score"] for x in orientation_ranking)[1] - sorted(x["data_only_score"] for x in orientation_ranking)[0],
            "rotation_error_deg": rotation_error,
            "translation_error_mm": translation_error,
            "ray_rms_error_deg": float(np.sqrt(np.mean(ray_error ** 2))),
            "residual_rms_mm": selected["metrics_detail"]["all"]["rms_mm"],
        },
        "orientation_ranking": [{k: x[k] for k in ("orientation", "range_semantics", "model", "score", "data_only_score", "gauge_prior_penalty_mm", "train", "heldout")} for x in orientation_ranking],
        "range_semantics_ranking": [{k: x[k] for k in ("range_semantics", "orientation", "model", "score", "train", "heldout")} for x in semantics_ranking],
        "model_comparison": {"selected_A_or_B": selected["model"], "model_C": model_c},
        "broad_vs_strict": {"broad_count": len(broad), "strict_count": len(strict), "broad_metrics": broad_fit["metrics"], "strict_metrics": strict_fit["metrics"]},
        "lopo": lopo,
        "lopo_summary": s2b.metrics([x["metrics"]["rms_mm"] for x in lopo]),
        "dynamic_registry_counts_exercised": [10, 12, 20, 30],
        "noise_and_outliers": {"distance_noise_sigma_mm": 2.0, "broad_only_outliers": True, "missing_zones": True},
    }
    out = ROOT / "synthetic"; out.mkdir(exist_ok=True)
    s2b.write_json(out / "synthetic_validation_results.json", result)
    rotation, translation = s2b.params_to_transform(estimate.tolist()); transform = np.eye(4); transform[:3, :3] = rotation; transform[:3, 3] = translation
    example = {
        "schema_version": "camera_tof.stage2b_candidate.v1", "status": "CANDIDATE_NOT_PROMOTED",
        "synthetic_example": True, "camera_intrinsics_ref": str(s2b.CAMERA_CANDIDATE),
        "camera_intrinsics_sha256": s2b.CAMERA_CANDIDATE_SHA256,
        "transform": {"direction": "T_camera_from_tof", "units": "mm", "convention": "column_vector", "R": rotation.tolist(), "t": translation.tolist(), "matrix": transform.tolist()},
        "raw_zone_orientation": {"name": selected["orientation"], "raw_index_for_canonical_index": s2b.d4_map(selected["orientation"]).tolist()},
        "range_semantics": selected["range_semantics"],
        "tof_ray_model": {"model_type": selected["model"], "zone_center_rays": s2b.tof_rays(estimate).tolist(), "zone_boundaries": {"kind": "model-derived", "corner_rays": s2b.ray_boundaries(estimate.tolist())}},
        "metrics": result["recovery"], "source_pose_ids": ids, "source_scan_ids": [p["scan_id"] for p in obs["poses"]],
        "promotion": False,
    }
    s2b.write_json(out / "camera_tof.stage2b_candidate.synthetic_example.v1.json", example)
    print(json.dumps(result["recovery"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
