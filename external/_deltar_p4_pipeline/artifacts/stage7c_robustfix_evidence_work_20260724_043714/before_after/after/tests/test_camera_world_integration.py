from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from deltar_p4_pipeline.camera_world_calibration import (
    R_WORLD_FROM_CAMERA_LEVEL,
    FrameObservation,
    PoseObservation,
    calibrate_capture,
    manifest_template,
    rotation_from_diagnostics,
    solve_world_capture,
    validate_capture_with_profile,
)
from deltar_p4_pipeline.camera_world_contracts import (
    CAMERA_HEIGHT_MM,
    CAMERA_HEIGHT_UNCERTAINTY_MM,
    FROZEN_CAMERA_TOF_PROFILE,
    WORLD_FRAME,
    CameraWorldError,
    load_strict_json,
    prepare_output_dir,
    sha256_file,
    validate_profile,
)
from deltar_p4_pipeline.camera_world_outputs import write_overlays


ROOT = Path(__file__).resolve().parents[1]
CAMERA_PROFILE = Path(
    r"C:\calibration\result_20260719_172703\calibration_profile.v2.json"
)
BOARD_MANIFEST = ROOT / "board_assets" / "board_manifest.json"


def _synthetic_capture(
    *,
    pose_count: int = 3,
    frames_per_pose: int = 5,
    roll_deg: float = 0.0,
    orientation_outlier_counts: tuple[int, ...] = (),
    base_invalid_counts: tuple[int, ...] = (),
    tof_usable: int = 24,
    tof_rms: float = 1.0,
    reprojection_rms: float = 0.1,
) -> tuple[dict, list[PoseObservation], dict]:
    roles = [
        "world_front_near",
        "world_front_mid",
        "world_front_far",
    ][:pose_count]
    poses: list[PoseObservation] = []
    for pose_index, role in enumerate(roles):
        pose_name = role.removeprefix("world_front_")
        frames: list[FrameObservation] = []
        diagnostics: list[dict] = []
        orientation_outlier_count = (
            orientation_outlier_counts[pose_index]
            if pose_index < len(orientation_outlier_counts)
            else 0
        )
        base_invalid_count = (
            base_invalid_counts[pose_index]
            if pose_index < len(base_invalid_counts)
            else 0
        )
        for frame_index in range(frames_per_pose):
            frame_name = f"frame_{frame_index:06d}"
            base_valid = (
                reprojection_rms <= 0.5
                and frame_index < frames_per_pose - base_invalid_count
            )
            orientation_outlier = (
                base_valid
                and frame_index
                >= frames_per_pose
                - base_invalid_count
                - orientation_outlier_count
            )
            frame_rotation = rotation_from_diagnostics(
                roll_deg + (3.0 if orientation_outlier else 0.0),
                0.0,
                0.0,
            )
            frame_reprojection_rms = (
                reprojection_rms if base_valid else 0.6
            )
            diagnostic = {
                "pose_name": pose_name,
                "role": role,
                "frame_name": frame_name,
                "scan_dir": f"C:\\synthetic\\{pose_name}",
                "valid": base_valid,
                "detected_corners": 20,
                "marker_count": 12,
                "reprojection_mean_px": frame_reprojection_rms * 0.8,
                "reprojection_rms_px": frame_reprojection_rms,
                "reprojection_p95_px": frame_reprojection_rms * 1.2,
                "reprojection_max_px": frame_reprojection_rms * 1.5,
                "tvec_z_m": 0.8,
                "finite_pose": True,
                "rotation_world_from_camera": frame_rotation.tolist(),
                "translation_world_from_camera_mm": [0.0, 0.0, 969.0],
            }
            diagnostics.append(diagnostic)
            if base_valid:
                frames.append(
                    FrameObservation(
                        pose_name=pose_name,
                        role=role,
                        frame_name=frame_name,
                        scan_dir=Path(f"C:\\synthetic\\{pose_name}"),
                        rotation_world_from_camera=frame_rotation.copy(),
                        rotation_camera_from_board=np.eye(3),
                        translation_camera_from_board_m=np.asarray(
                            [0.0, 0.0, 0.8]
                        ),
                        camera_diagnostics=diagnostic,
                        board_polygon=np.asarray(
                            [[10, 10], [100, 10], [100, 100], [10, 100]],
                            dtype=np.float64,
                        ),
                        image_bgr=np.zeros(
                            (360, 640, 3), dtype=np.uint8
                        ),
                        tof={
                            "target_status": [5] * 64,
                            "nb_target_detected": [1] * 64,
                            "distance_mm": [800.0] * 64,
                            "range_sigma_mm": [1.0] * 64,
                        },
                        source_paths=(),
                    )
                )
        tof_pass = (
            tof_usable >= 16
            and tof_rms <= 15.0
        )
        tof_diag = {
            "status": "PASS" if tof_pass else "FAIL",
            "expected_normal_contract": "+Y_world",
            "plane_normal_world": [0.0, 1.0, 0.0],
            "plane_offset_world_mm": -800.0,
            "usable_overlap_zone_count": tof_usable,
            "normal_angular_error_deg": 0.1,
            "plane_rms_mm": tof_rms,
            "plane_p95_mm": tof_rms * 1.2,
            "plane_max_mm": tof_rms * 1.5,
            "rejected_zones": [],
        }
        poses.append(
            PoseObservation(
                pose_name=pose_name,
                role=role,
                approximate_distance_mm=650.0 + pose_index * 200.0,
                total_frame_count=frames_per_pose,
                frames=frames,
                frame_diagnostics=diagnostics,
                tof_diagnostics=tof_diag,
                overlay=None,
            )
        )
    manifest = {
        "capture_id": "synthetic_world_capture",
        "camera_height_mm": CAMERA_HEIGHT_MM,
        "camera_height_uncertainty_mm": CAMERA_HEIGHT_UNCERTAINTY_MM,
        "board_origin_height_above_floor_mm": None,
        "_camera_profile_path": CAMERA_PROFILE.resolve(),
        "_camera_tof_profile_path": FROZEN_CAMERA_TOF_PROFILE.resolve(),
        "_board_manifest_path": BOARD_MANIFEST.resolve(),
    }
    metadata = {
        "camera_profile_id": "sc2336_intrinsics_afe9ca2170a81dfa",
        "camera_tof_profile_id": "camera_tof_extrinsics_6698a99489d42fb2",
        "source_hashes": {
            str(CAMERA_PROFILE.resolve()): sha256_file(CAMERA_PROFILE),
            str(FROZEN_CAMERA_TOF_PROFILE.resolve()): sha256_file(
                FROZEN_CAMERA_TOF_PROFILE
            ),
            str(BOARD_MANIFEST.resolve()): sha256_file(BOARD_MANIFEST),
            "C:\\synthetic\\capture.json": "a" * 64,
        },
        "matrix": np.asarray(
            [[881.0, 0, 337.0], [0, 881.0, 188.0], [0, 0, 1.0]]
        ),
        "distortion": np.zeros(5),
        "board_contract": {},
    }
    return manifest, poses, metadata


class CameraWorldIntegrationTests(unittest.TestCase):
    def test_manifest_template_smoke_and_three_schema_parse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "camera_world_capture.v1.json"
            value = manifest_template(
                output,
                camera_profile=CAMERA_PROFILE,
                camera_tof_profile=FROZEN_CAMERA_TOF_PROFILE,
                board_manifest=BOARD_MANIFEST,
            )
            self.assertEqual(value["camera_height_mm"], 969)
            self.assertEqual(value["camera_height_uncertainty_mm"], 5)
            self.assertEqual(len(value["poses"]), 3)
            self.assertEqual(load_strict_json(output)["world_frame"], WORLD_FRAME)
        for name in (
            "camera_world_capture.v1.schema.json",
            "camera_world_pose.v1.schema.json",
            "camera_world_calibration_report.v1.schema.json",
        ):
            parsed = json.loads((ROOT / "schemas" / name).read_text("utf-8"))
            self.assertFalse(parsed["additionalProperties"])

    def test_output_dir_refuses_nonempty_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            output.mkdir()
            (output / "owned.txt").write_text("do not overwrite", "utf-8")
            with self.assertRaises(CameraWorldError):
                prepare_output_dir(output)

    def test_overlay_contains_finite_world_and_tof_diagnostics(self) -> None:
        manifest, poses, metadata = _synthetic_capture()
        pose = poses[0]
        frame = pose.frames[0]
        zones = np.arange(16, 32, dtype=np.int64)
        points_camera = np.column_stack(
            (
                np.linspace(-120.0, 120.0, len(zones)),
                np.linspace(-60.0, 60.0, len(zones)),
                np.full(len(zones), 800.0),
            )
        )
        pose.overlay = {
            "frame": frame,
            "tof": {
                "zones": zones,
                "raw_map": np.arange(64, dtype=np.int64),
                "points_camera": points_camera,
                "points_world": (
                    R_WORLD_FROM_CAMERA_LEVEL @ points_camera.T
                ).T + [0.0, 0.0, 969.0],
                "absolute_plane_residual_mm": np.zeros(len(zones)),
                "inlier_mask": np.ones(len(zones), dtype=bool),
                "overlap_mask": np.ones(64, dtype=bool),
                "plane_normal_world": np.asarray([0.0, 1.0, 0.0]),
                "plane_offset_world_mm": -800.0,
            },
            "matrix": metadata["matrix"],
            "distortion": metadata["distortion"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "overlays"
            write_overlays(output, [pose])
            image = output / "near_camera_world_overlay.png"
            self.assertTrue(image.is_file())
            self.assertGreater(image.stat().st_size, 1000)

    def test_synthetic_pass_profile_and_independent_validate(self) -> None:
        manifest, poses, metadata = _synthetic_capture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calibration_output = root / "calibration"
            with patch(
                "deltar_p4_pipeline.camera_world_calibration.load_capture_observations",
                return_value=(manifest, poses, metadata),
            ):
                report = calibrate_capture(
                    root / "synthetic_capture.json",
                    CAMERA_PROFILE,
                    FROZEN_CAMERA_TOF_PROFILE,
                    BOARD_MANIFEST,
                    calibration_output,
                    profile_bundle_path=ROOT / "profiles",
                )
            self.assertEqual(report["status"], "PASS")
            formal = calibration_output / "camera_world_pose.v1.json"
            self.assertTrue(formal.is_file())
            profile = validate_profile(load_strict_json(formal))
            self.assertTrue(profile["world_pose_resolved"])
            self.assertFalse(profile["absolute_geometry_valid"])

            # The validation command consumes an existing formal profile and
            # must not emit a second formal profile.
            capture_stub = root / "capture_stub.json"
            capture_stub.write_text(
                json.dumps(
                    {
                        "camera_profile_path": str(CAMERA_PROFILE),
                        "camera_tof_profile_path": str(
                            FROZEN_CAMERA_TOF_PROFILE
                        ),
                        "board_manifest_path": str(BOARD_MANIFEST),
                    }
                ),
                "utf-8",
            )
            validation_output = root / "validation"
            with patch(
                "deltar_p4_pipeline.camera_world_calibration.load_capture_observations",
                return_value=(manifest, poses, metadata),
            ):
                validation = validate_capture_with_profile(
                    formal,
                    capture_stub,
                    validation_output,
                    profile_bundle_path=ROOT / "profiles",
                )
            self.assertEqual(validation["status"], "PASS")
            self.assertFalse(validation["formal_profile_generated"])
            self.assertFalse(
                (validation_output / "camera_world_pose.v1.json").exists()
            )

    def test_single_orientation_outlier_passes_and_remains_diagnostic(self) -> None:
        manifest, poses, metadata = _synthetic_capture(
            orientation_outlier_counts=(0, 0, 1)
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            with patch(
                "deltar_p4_pipeline.camera_world_calibration.load_capture_observations",
                return_value=(manifest, poses, metadata),
            ):
                report = calibrate_capture(
                    Path(temporary) / "capture.json",
                    CAMERA_PROFILE,
                    FROZEN_CAMERA_TOF_PROFILE,
                    BOARD_MANIFEST,
                    output,
                    profile_bundle_path=ROOT / "profiles",
                )
            self.assertEqual(report["status"], "PASS")
            self.assertTrue((output / "camera_world_pose.v1.json").is_file())
            self.assertEqual(report["capture_summary"]["frame_count"], 15)
            self.assertEqual(
                report["gates"]["frame_quality"]["actual"][
                    "base_valid_frame_count"
                ],
                15,
            )
            self.assertEqual(report["capture_summary"]["valid_frame_count"], 14)
            self.assertEqual(report["capture_summary"]["rejected_frame_count"], 1)
            self.assertEqual(
                report["gates"]["per_pose_orientation_inlier_count"]["actual"],
                {"near": 5, "mid": 5, "far": 4},
            )
            diagnostics = load_strict_json(
                output / "frame_diagnostics.json"
            )["frames"]
            rejected = [
                item
                for item in diagnostics
                if item["valid"] and not item["orientation_inlier"]
            ]
            self.assertEqual(len(rejected), 1)
            self.assertEqual(rejected[0]["pose_name"], "far")

    def test_three_orientation_inliers_in_one_pose_fails(self) -> None:
        manifest, poses, metadata = _synthetic_capture(
            orientation_outlier_counts=(0, 0, 2)
        )
        self._assert_gate_failure(
            manifest,
            poses,
            metadata,
            "per_pose_orientation_inlier_count",
        )

    def test_thirteen_total_orientation_inliers_fails(self) -> None:
        manifest, poses, metadata = _synthetic_capture(
            orientation_outlier_counts=(0, 1, 1)
        )
        self._assert_gate_failure(
            manifest, poses, metadata, "valid_frame_count"
        )

    def test_fourteen_base_valid_frames_still_fails(self) -> None:
        manifest, poses, metadata = _synthetic_capture(
            base_invalid_counts=(0, 0, 1)
        )
        self._assert_gate_failure(
            manifest, poses, metadata, "frame_quality"
        )

    def _assert_gate_failure(
        self,
        manifest: dict,
        poses: list[PoseObservation],
        metadata: dict,
        gate_name: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            with patch(
                "deltar_p4_pipeline.camera_world_calibration.load_capture_observations",
                return_value=(manifest, poses, metadata),
            ):
                report = calibrate_capture(
                    Path(temporary) / "capture.json",
                    CAMERA_PROFILE,
                    FROZEN_CAMERA_TOF_PROFILE,
                    BOARD_MANIFEST,
                    output,
                    profile_bundle_path=ROOT / "profiles",
                )
            self.assertEqual(report["status"], "FAIL")
            self.assertFalse(report["gates"][gate_name]["passed"])
            self.assertFalse((output / "camera_world_pose.v1.json").exists())

    def test_synthetic_fail_never_writes_formal_profile(self) -> None:
        cases = {
            "fewer_than_3_poses": {"pose_count": 2},
            "fewer_than_15_frames": {"frames_per_pose": 4},
            "frame_reprojection_failure": {"reprojection_rms": 0.6},
            "mechanical_roll_failure": {"roll_deg": 8.0},
            "tof_overlap_failure": {"tof_usable": 10},
            "tof_plane_failure": {"tof_rms": 20.0},
        }
        for name, parameters in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                manifest, poses, metadata = _synthetic_capture(**parameters)
                output = Path(temporary) / "output"
                with patch(
                    "deltar_p4_pipeline.camera_world_calibration.load_capture_observations",
                    return_value=(manifest, poses, metadata),
                ):
                    report = calibrate_capture(
                        Path(temporary) / "capture.json",
                        CAMERA_PROFILE,
                        FROZEN_CAMERA_TOF_PROFILE,
                        BOARD_MANIFEST,
                        output,
                        profile_bundle_path=ROOT / "profiles",
                    )
                self.assertEqual(report["status"], "FAIL")
                self.assertFalse(
                    (output / "camera_world_pose.v1.json").exists()
                )
                candidate = load_strict_json(
                    output / "camera_world_pose.candidate.json"
                )
                self.assertTrue(candidate["candidate_only"])
                self.assertTrue(
                    candidate["must_not_be_used_as_measured_profile"]
                )


if __name__ == "__main__":
    unittest.main()
