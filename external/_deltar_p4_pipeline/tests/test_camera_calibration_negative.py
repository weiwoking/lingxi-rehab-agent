import copy
from types import SimpleNamespace

import numpy as np
import pytest

from deltar_p4_pipeline.camera_calibration import calibrate_camera_charuco, degeneracy_warnings, deterministic_split
from deltar_p4_pipeline.camera_calibration_contracts import CameraCalibrationError


def synthetic_observations():
    values = []
    base = np.array([[x, y] for y in (10, 120, 240, 350) for x in (10, 160, 320, 480, 630)], np.float32)
    for index in range(30):
        values.append(SimpleNamespace(
            filename=f"capture_{index:03d}.png", image_points=base,
            centroid=(50.0 + (index % 6) * 100.0, 40.0 + (index // 6) * 65.0),
        ))
    return values


def test_heldout_ratio_out_of_range():
    with pytest.raises(CameraCalibrationError):
        deterministic_split(synthetic_observations(), seed=1, heldout_ratio=0.0)


def test_few_images_rejected_before_solver(tmp_path):
    with pytest.raises(CameraCalibrationError):
        deterministic_split([], seed=1, heldout_ratio=0.2)


def test_pose_similarity_and_center_coverage_detected():
    observations = synthetic_observations()
    same = [copy.copy(observations[0]) for _ in observations]
    fake_solution = {
        "fit": {"per_image": [{"distance_m": 0.5, "tilt_deg": 0.2}] * 24},
        "heldout": {"per_image": [{"distance_m": 0.5, "tilt_deg": 0.2}] * 6, "rms_px": 0.2},
        "intrinsics": {"fx": 520, "fy": 515, "cx": 319.5, "cy": 179.5},
    }
    warnings = degeneracy_warnings(same, fake_solution, min_valid_images=25)
    assert "POSE_CENTERS_TOO_SIMILAR" in warnings
    assert "BOARD_DISTANCES_TOO_SIMILAR" in warnings
    assert "BOARD_POSES_TOO_FRONTO_PARALLEL" in warnings


def test_implausible_intrinsics_and_rms_detected():
    observations = synthetic_observations()
    fake_solution = {
        "fit": {"per_image": []},
        "heldout": {"per_image": [], "rms_px": 3.0},
        "intrinsics": {"fx": 50, "fy": 50, "cx": 900, "cy": 500},
    }
    fake_solution["fit"]["per_image"] = [{"distance_m": 0.4 + i*0.02, "tilt_deg": i} for i in range(30)]
    warnings = degeneracy_warnings(observations, fake_solution, min_valid_images=25)
    assert "FOCAL_LENGTH_IMPLAUSIBLE" in warnings
    assert "PRINCIPAL_POINT_OUTSIDE_IMAGE" in warnings
    assert "HELDOUT_RMS_EXCEEDS_DEMO_CEILING" in warnings
