import numpy as np
import pytest

from deltar_p4_pipeline.calibration import validate_calibration
from deltar_p4_pipeline.camera_geometry import backproject_pixels, pixel_rays, project_points
from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.synthetic_geometry import semantics_roundtrip_error, synthetic_calibration


def test_pinhole_axial_backprojection():
    camera = synthetic_calibration()["camera_model"]
    p = backproject_pixels(np.array([[camera["cx"], camera["cy"]]]), np.array([2.0]), camera, "axial_z")
    assert np.allclose(p, [[0, 0, 2]])


def test_radial_norm_is_input_range():
    camera = synthetic_calibration()["camera_model"]
    points = backproject_pixels(np.array([[20, 30], [600, 300]]), np.array([2.0, 3.0]), camera, "radial_range")
    assert np.allclose(np.linalg.norm(points, axis=1), [2, 3])


@pytest.mark.parametrize("semantics", ["axial_z", "radial_range"])
def test_semantics_roundtrip_and_wrong_path_bias(semantics):
    result = semantics_roundtrip_error(semantics)
    assert result["correct_max_error_m"] < 1e-12
    assert result["wrong_semantics_median_point_error_m"] > 0.02


def test_zero_brown_distortion_equals_pinhole():
    pinhole = synthetic_calibration()["camera_model"]
    brown = dict(pinhole, model="opencv_brown", distortion=[0, 0, 0, 0, 0])
    uv = np.array([[10.0, 20.0], [319.5, 179.5], [620.0, 340.0]])
    assert np.allclose(pixel_rays(uv, pinhole), pixel_rays(uv, brown), atol=1e-10)


def test_nonzero_brown_projection_roundtrip():
    camera = dict(synthetic_calibration()["camera_model"], model="opencv_brown", distortion=[0.05, -0.02, 0.001, -0.001, 0.0])
    points = np.array([[0.2, 0.1, 2.0], [-0.3, 0.2, 2.5]])
    uv = project_points(points, camera)
    recovered = backproject_pixels(uv, points[:, 2], camera, "axial_z")
    assert np.allclose(recovered, points, atol=1e-7)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda camera: camera.pop("fx"),
        lambda camera: camera.update(fx=float("nan")),
        lambda camera: camera.update(model="opencv_brown", distortion=[0.1, 0.2, 0.3]),
        lambda camera: camera.update(model="opencv_brown", distortion=[0.1, 0.2, 0.0, 0.0, float("inf")]),
    ],
    ids=("malformed_K", "nan_K", "malformed_dist_coeffs", "nonfinite_dist_coeffs"),
)
def test_malformed_calibration_contract_still_fails(mutation):
    calibration = synthetic_calibration()
    mutation(calibration["camera_model"])
    with pytest.raises(PipelineError):
        validate_calibration(calibration)


@pytest.mark.parametrize(
    "points",
    [
        np.array([1.0, 2.0]),
        np.array([[1.0, 2.0, 3.0]]),
        np.array([[float("nan"), 2.0]]),
        np.array([[float("inf"), 2.0]]),
    ],
    ids=("one_dimensional", "three_columns", "nan", "inf"),
)
def test_truly_invalid_points_contract_still_fails(points):
    camera = dict(
        synthetic_calibration()["camera_model"],
        model="opencv_brown",
        distortion=[0.0, 0.0, 0.0, 0.0, 0.0],
    )
    with pytest.raises(PipelineError):
        pixel_rays(points, camera)
