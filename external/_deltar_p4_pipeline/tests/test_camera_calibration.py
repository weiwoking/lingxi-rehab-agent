import math

import cv2
import numpy as np

from deltar_p4_pipeline.camera_calibration import CharucoObservation, calibrate_observations, coverage_statistics, deterministic_split


TRUE_MATRIX = np.array([[520.0, 0, 319.5], [0, 515.0, 179.5], [0, 0, 1.0]], np.float64)
TRUE_DISTORTION = np.array([0.08, -0.04, 0.001, -0.001, 0.01], np.float64)


def synthetic_observations(count=30, noise_px=0.08):
    rng = np.random.default_rng(713)
    grid = np.array([[x * 0.025, y * 0.025, 0.0] for y in range(1, 5) for x in range(1, 7)], np.float32)
    values = []
    for index in range(count):
        row, col = divmod(index, 6)
        rvec = np.radians([(-14 + row * 6), (-18 + col * 7), (-12 + (index % 5) * 6)]).astype(np.float64)
        z = 0.48 + 0.10 * (index % 6)
        tvec = np.array([[-0.25 + 0.065 * col], [-0.14 + 0.055 * row], [z]], np.float64)
        projected, _ = cv2.projectPoints(grid, rvec, tvec, TRUE_MATRIX, TRUE_DISTORTION)
        points = projected.reshape(-1, 2) + rng.normal(0, noise_px, (len(grid), 2))
        x0, y0 = points.min(axis=0); x1, y1 = points.max(axis=0)
        values.append(CharucoObservation(
            filename=f"capture_{index:03d}.png", sha256=f"{index:064x}", width=640, height=360,
            image_points=points.astype(np.float32), object_points=grid.copy(), marker_count=17,
            corner_count=len(grid), coverage=float((x1-x0)*(y1-y0)/(640*360)), sharpness=500.0,
            centroid=(float(points[:,0].mean()), float(points[:,1].mean())), board_angle_deg=float(index),
        ))
    return values


def test_synthetic_brown_calibration_recovers_intrinsics_with_heldout():
    observations = synthetic_observations()
    fit, heldout = deterministic_split(observations, seed=7, heldout_ratio=0.2)
    result = calibrate_observations(fit, heldout)
    assert len(fit) == 24 and len(heldout) == 6
    assert result["heldout"]["rms_px"] < 0.5
    assert abs(result["intrinsics"]["fx"] - TRUE_MATRIX[0,0]) / TRUE_MATRIX[0,0] < 0.04
    assert abs(result["intrinsics"]["fy"] - TRUE_MATRIX[1,1]) / TRUE_MATRIX[1,1] < 0.04
    assert abs(result["intrinsics"]["distortion"]["k1"]) > 0.001


def test_split_is_deterministic_and_disjoint():
    observations = synthetic_observations()
    first = deterministic_split(observations, seed=11, heldout_ratio=0.2)
    second = deterministic_split(list(reversed(observations)), seed=11, heldout_ratio=0.2)
    assert [[item.filename for item in part] for part in first] == [[item.filename for item in part] for part in second]
    assert not ({x.filename for x in first[0]} & {x.filename for x in first[1]})


def test_coverage_heatmap_reports_edges():
    stats = coverage_statistics(synthetic_observations())
    assert stats["covered_cells"] >= 8
    assert stats["outer_cells_covered"] >= 5
