from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import test_stage2c_toolchain as tests

ROOT = Path(__file__).resolve().parent
s2c = tests.s2c


def run() -> dict:
    perfect = tests.synthetic_observations(3)
    noisy = tests.synthetic_observations(6, noise_sigma=4.0)
    outside = tests.synthetic_observations(8, noise_sigma=3.0, outside=True)
    perfect_records = s2c.observation_records(perfect); noisy_records = s2c.observation_records(noisy); outside_records = s2c.observation_records(outside)
    normal = tests.rotation_y(20) @ np.array([0, 0, -1.0])
    rays, rotation, translation, measured = tests.synthetic_measurements(normal, 700)
    perturbed_rotation = tests.rotation_y(2) @ rotation
    _, perturbed, _ = s2c.geometry_residuals(normal, 700, measured, rays, perturbed_rotation, translation + np.array([15.0, 0, 0]))
    result = {
        "schema_version": "stage2c_synthetic_validation.v1",
        "perfect_frozen_geometry": s2c.metric(r["point_to_plane_residual_mm"] for r in perfect_records if r["likely_reference_plane"]),
        "measurement_noise_4mm_axial": s2c.metric(r["axial_depth_residual_mm"] for r in noisy_records if r["likely_reference_plane"]),
        "perturbed_candidate_tx15mm_yaw2deg": s2c.metric(perturbed),
        "outside_background": {
            "all_broad": s2c.metric(r["point_to_plane_residual_mm"] for r in outside_records if r["broad_valid"]),
            "core": s2c.metric(r["point_to_plane_residual_mm"] for r in outside_records if r["likely_reference_plane"]),
            "automatic_blacklist": False,
        },
        "dynamic_pose_counts_exercised": [3, 6, 8, 12],
        "pose_capacity": "dynamic_unbounded",
        "parameter_recovery_attempted": False,
    }
    path = ROOT / "synthetic" / "synthetic_validation_results.json"
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    example = s2c.validate_document(noisy, persist=False)
    (ROOT / "synthetic" / "example_validation_output.json").write_text(json.dumps(example, indent=2) + "\n", encoding="utf-8")
    s2c.create_plots(noisy_records, ROOT / "synthetic" / "plots")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    run()
