from __future__ import annotations

import copy
import unittest

import numpy as np

from deltar_p4_pipeline.camera_world_calibration import (
    R_WORLD_FROM_CAMERA_LEVEL,
    make_transform,
    orientation_diagnostics,
)
from deltar_p4_pipeline.camera_world_contracts import (
    CAMERA_HEIGHT_MM,
    CAMERA_HEIGHT_UNCERTAINTY_MM,
    FROZEN_CAMERA_TOF_PROFILE,
    FROZEN_CAMERA_TOF_PROFILE_ID,
    FROZEN_CAMERA_TOF_SHA256,
    MATRIX_CONVENTION,
    PROFILE_SCHEMA,
    WORLD_FRAME,
    CameraWorldError,
    load_frozen_camera_tof_profile,
    validate_profile,
)


def valid_profile() -> dict:
    stage7b = load_frozen_camera_tof_profile(FROZEN_CAMERA_TOF_PROFILE)
    transform_wc = make_transform(
        R_WORLD_FROM_CAMERA_LEVEL, [0, 0, CAMERA_HEIGHT_MM]
    )
    transform_wt = transform_wc @ np.asarray(
        stage7b["T_camera_from_tof"], dtype=np.float64
    )
    return {
        "schema_version": PROFILE_SCHEMA,
        "profile_id": "camera_world_pose_0123456789abcdef",
        "created_at": "2026-07-24T00:00:00Z",
        "validation_status": "PASS",
        "camera_profile_id": "sc2336_intrinsics_afe9ca2170a81dfa",
        "camera_tof_profile_id": FROZEN_CAMERA_TOF_PROFILE_ID,
        "world_frame": copy.deepcopy(WORLD_FRAME),
        "matrix_convention": MATRIX_CONVENTION,
        "T_world_from_camera": transform_wc.tolist(),
        "rotation_world_from_camera": R_WORLD_FROM_CAMERA_LEVEL.tolist(),
        "translation_world_from_camera_mm": [0.0, 0.0, 969.0],
        "T_world_from_tof": transform_wt.tolist(),
        "rotation_world_from_tof": transform_wt[:3, :3].tolist(),
        "translation_world_from_tof_mm": transform_wt[:3, 3].tolist(),
        "camera_height_mm": CAMERA_HEIGHT_MM,
        "camera_height_uncertainty_mm": CAMERA_HEIGHT_UNCERTAINTY_MM,
        "orientation_diagnostics": orientation_diagnostics(
            R_WORLD_FROM_CAMERA_LEVEL
        ),
        "capture_summary": {
            "pose_count": 3,
            "frame_count": 15,
            "valid_frame_count": 15,
            "rejected_frame_count": 0,
        },
        "residual_summary": {
            "camera_orientation": {},
            "tof_vertical_plane": {},
        },
        "source_data_hashes": {
            str(FROZEN_CAMERA_TOF_PROFILE.resolve()): FROZEN_CAMERA_TOF_SHA256,
            "C:\\synthetic\\camera.json": "1" * 64,
            "C:\\synthetic\\board.json": "2" * 64,
            "C:\\synthetic\\capture.json": "3" * 64,
        },
        "warnings": [],
        "unresolved_fields": ["absolute_geometry_validation"],
        "world_pose": {"status": "measured"},
        "world_pose_resolved": True,
        "absolute_geometry_valid": False,
    }


class CameraWorldProfileContractTests(unittest.TestCase):
    def test_frozen_stage7b_binding(self) -> None:
        profile = load_frozen_camera_tof_profile(FROZEN_CAMERA_TOF_PROFILE)
        self.assertEqual(profile["profile_id"], FROZEN_CAMERA_TOF_PROFILE_ID)
        self.assertEqual(profile["validation_status"], "PASS")
        self.assertEqual(profile["camera_tof_extrinsics"]["status"], "measured")
        self.assertFalse(profile["world_pose_resolved"])
        self.assertFalse(profile["absolute_geometry_valid"])

    def test_valid_closed_world_profile(self) -> None:
        value = valid_profile()
        self.assertIs(validate_profile(value), value)

    def test_single_orientation_outlier_is_valid(self) -> None:
        value = valid_profile()
        value["capture_summary"]["valid_frame_count"] = 14
        value["capture_summary"]["rejected_frame_count"] = 1
        self.assertIs(validate_profile(value), value)

    def test_fewer_than_fourteen_orientation_inliers_is_rejected(self) -> None:
        value = valid_profile()
        value["capture_summary"]["valid_frame_count"] = 13
        value["capture_summary"]["rejected_frame_count"] = 2
        with self.assertRaises(CameraWorldError):
            validate_profile(value)

    def test_unknown_transform_direction_is_rejected(self) -> None:
        value = valid_profile()
        value["transform_direction"] = "T_camera_from_world"
        with self.assertRaises(CameraWorldError):
            validate_profile(value)

    def test_axis_text_contradiction_is_rejected(self) -> None:
        value = valid_profile()
        value["world_frame"]["y_axis"] = "vehicle_backward"
        with self.assertRaises(CameraWorldError):
            validate_profile(value)

    def test_height_cannot_be_overridden_by_board_translation(self) -> None:
        value = valid_profile()
        value["translation_world_from_camera_mm"] = [0.0, 0.0, 950.0]
        value["T_world_from_camera"][2][3] = 950.0
        with self.assertRaises(CameraWorldError):
            validate_profile(value)

    def test_uncertainty_is_exactly_five(self) -> None:
        value = valid_profile()
        value["camera_height_uncertainty_mm"] = 6.0
        with self.assertRaises(CameraWorldError):
            validate_profile(value)

    def test_composition_mismatch_is_rejected(self) -> None:
        value = valid_profile()
        value["T_world_from_tof"][0][3] += 1.0
        with self.assertRaises(CameraWorldError):
            validate_profile(value)

    def test_absolute_geometry_must_remain_false(self) -> None:
        value = valid_profile()
        value["absolute_geometry_valid"] = True
        with self.assertRaises(CameraWorldError):
            validate_profile(value)


if __name__ == "__main__":
    unittest.main()
