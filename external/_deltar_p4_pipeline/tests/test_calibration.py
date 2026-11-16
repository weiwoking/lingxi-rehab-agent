import copy

import numpy as np
import pytest

from deltar_p4_pipeline.calibration import invert_transform, transform_points, validate_calibration, validate_transform
from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.synthetic_geometry import synthetic_calibration


def test_synthetic_calibration_valid():
    assert validate_calibration(synthetic_calibration())["calibration_status"] == "synthetic"


def test_transform_roundtrip_nonzero_rotation():
    cal = synthetic_calibration(roll_deg=4, pitch_deg=-7, yaw_deg=9)
    transform = cal["transforms"]["T_ground_from_camera"]
    points = np.array([[0.2, -0.1, 2.0], [-0.4, 0.3, 1.5]])
    assert np.allclose(transform_points(transform_points(points, transform), invert_transform(transform)), points)


@pytest.mark.parametrize("field,value", [("fx", 0), ("fy", -1)])
def test_invalid_intrinsics(field, value):
    cal = synthetic_calibration(); cal["camera_model"][field] = value
    with pytest.raises(PipelineError, match="positive"):
        validate_calibration(cal)


def test_reflection_rotation_rejected():
    matrix = np.eye(4); matrix[0, 0] = -1
    with pytest.raises(PipelineError):
        validate_transform(matrix, "T")


def test_nonorthogonal_rotation_rejected():
    matrix = np.eye(4); matrix[0, 1] = 0.2
    with pytest.raises(PipelineError):
        validate_transform(matrix, "T")


def test_bad_semantics_rejected():
    cal = synthetic_calibration(); cal["depth"]["semantics"] = "guess"
    with pytest.raises(PipelineError):
        validate_calibration(cal)


def test_unverified_calibration_is_valid_contract_state():
    cal = synthetic_calibration(status="unverified")
    assert validate_calibration(cal)["depth"]["semantics_status"] == "PLAUSIBLE_BUT_UNPROVEN"
