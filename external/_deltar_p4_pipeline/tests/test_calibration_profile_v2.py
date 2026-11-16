import copy

import pytest

from deltar_p4_pipeline.camera_calibration_contracts import CameraCalibrationError, validate_calibration_v2, v2_camera_to_v1_camera_model


def profile_v2():
    hashes = {"x.png": "1" * 64}
    return {
        "schema_version": "deltar_geometry_calibration.v2", "profile_id": "camera_test",
        "measurement_status": "measured", "absolute_geometry_valid": False,
        "camera": {"resolution": {"width": 640, "height": 360},
                   "orientation": {"physical": "landscape", "software_rotation_degrees": 0, "scene_upright_confirmed": True},
                   "intrinsics": {"model": "opencv_brown", "fx": 520.0, "fy": 515.0, "cx": 319.5, "cy": 179.5,
                                  "distortion": {"k1": 0.08, "k2": -0.04, "p1": 0.001, "p2": -0.001, "k3": 0.01}}},
        "camera_tof_extrinsics": {"status": "unresolved"}, "world_pose": {"status": "unresolved"},
        "quality": {"fit_rms_px": 0.2, "heldout_rms_px": 0.3, "heldout_p95_px": 0.5,
                    "accepted_images": 30, "fit_images": 24, "heldout_images": 6, "quality_status": "PASS"},
        "provenance": {"board_manifest_sha256": "2"*64, "orientation_manifest_sha256": "3"*64,
                       "accepted_images_manifest_sha256": "4"*64, "accepted_image_sha256": hashes,
                       "python_version": "3.11", "opencv_version": "4.10", "cli_parameters": {}, "seed": 7,
                       "fit_images": ["x.png"], "heldout_images": ["y.png"], "generated_at": "2026-07-19T00:00:00Z",
                       "tool_version": "test", "source_path": "synthetic"}
    }


def test_measured_v2_and_legacy_camera_adapter():
    value = validate_calibration_v2(profile_v2())
    legacy = v2_camera_to_v1_camera_model(value)
    assert legacy["model"] == "opencv_brown" and len(legacy["distortion"]) == 5


def test_unverified_is_explicit_legal_state():
    value = profile_v2(); value["measurement_status"] = "unverified"
    assert validate_calibration_v2(value)["measurement_status"] == "unverified"


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(absolute_geometry_valid=True),
    lambda p: p["camera"]["resolution"].update(width=360, height=640),
    lambda p: p["camera"]["orientation"].update(software_rotation_degrees=90),
    lambda p: p["camera_tof_extrinsics"].update(status="measured"),
    lambda p: p["camera"]["intrinsics"].update(fx=float("nan")),
    lambda p: p.update(unknown=True),
])
def test_v2_negative_contracts(mutation):
    value = profile_v2(); mutation(value)
    with pytest.raises(CameraCalibrationError):
        validate_calibration_v2(value)
