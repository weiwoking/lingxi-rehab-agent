from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

import numpy as np

from deltar_p4_pipeline.horizontal_planes import (
    camera_points_m_to_world_mm,
    extract_horizontal_planes,
    world_points_from_depth,
)
from deltar_p4_pipeline.scan_contracts import ScanContractError, load_strict_json
from deltar_p4_pipeline.stage7d_contracts import (
    FORMAL_CAMERA_PATH,
    FORMAL_CAMERA_SHA256,
    FORMAL_CAMERA_TOF_PATH,
    FORMAL_CAMERA_TOF_SHA256,
    FORMAL_CAMERA_WORLD_PATH,
    FORMAL_CAMERA_WORLD_SHA256,
    bind_stage7d_profiles,
    validate_bound_profile_documents,
    validate_stage7d_profile,
)


ROOT = Path(__file__).resolve().parents[1]
STAGE7D_PROFILE = ROOT / "profiles" / "stage7d_horizontal_plane_profile.json"


class Stage7DProfileBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.camera = load_strict_json(FORMAL_CAMERA_PATH, code="TEST")
        cls.world = load_strict_json(FORMAL_CAMERA_WORLD_PATH, code="TEST")
        cls.tof = load_strict_json(FORMAL_CAMERA_TOF_PATH, code="TEST")

    def test_formal_profile_binding_passes(self) -> None:
        context = bind_stage7d_profiles(
            FORMAL_CAMERA_PATH,
            FORMAL_CAMERA_WORLD_PATH,
            STAGE7D_PROFILE,
            FORMAL_CAMERA_TOF_PATH,
        )
        self.assertEqual(
            context.camera_summary["sha256"], FORMAL_CAMERA_SHA256
        )
        self.assertEqual(
            context.camera_world_summary["sha256"], FORMAL_CAMERA_WORLD_SHA256
        )
        self.assertEqual(
            context.camera_tof_summary["sha256"], FORMAL_CAMERA_TOF_SHA256
        )
        self.assertFalse(context.camera_world_profile["absolute_geometry_valid"])

    def test_wrong_hash_and_profile_id_fail(self) -> None:
        with self.assertRaises(ScanContractError):
            validate_bound_profile_documents(
                self.camera,
                self.world,
                self.tof,
                camera_sha256="0" * 64,
                world_sha256=FORMAL_CAMERA_WORLD_SHA256,
                tof_sha256=FORMAL_CAMERA_TOF_SHA256,
            )
        bad_profile = deepcopy(json.loads(STAGE7D_PROFILE.read_text(encoding="utf-8")))
        bad_profile["expected_camera_profile_id"] = "wrong"
        with self.assertRaises(ScanContractError):
            validate_stage7d_profile(bad_profile)

    def test_unresolved_world_pose_and_absolute_true_fail(self) -> None:
        unresolved = deepcopy(self.world)
        unresolved["world_pose"] = {"status": "unresolved"}
        unresolved["world_pose_resolved"] = False
        with self.assertRaises(ScanContractError):
            validate_bound_profile_documents(
                self.camera,
                unresolved,
                self.tof,
                camera_sha256=FORMAL_CAMERA_SHA256,
                world_sha256=FORMAL_CAMERA_WORLD_SHA256,
                tof_sha256=FORMAL_CAMERA_TOF_SHA256,
            )
        asserted = deepcopy(self.world)
        asserted["absolute_geometry_valid"] = True
        with self.assertRaises(ScanContractError):
            validate_bound_profile_documents(
                self.camera,
                asserted,
                self.tof,
                camera_sha256=FORMAL_CAMERA_SHA256,
                world_sha256=FORMAL_CAMERA_WORLD_SHA256,
                tof_sha256=FORMAL_CAMERA_TOF_SHA256,
            )


class Stage7DGeometryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = json.loads(STAGE7D_PROFILE.read_text(encoding="utf-8"))
        self.profile.update(
            {
                "fit_stride_px": 2,
                "ransac_iterations": 220,
                "min_ground_pixels": 300,
                "min_support_pixels": 250,
                "min_visible_span_px": 20,
            }
        )

    def test_camera_metres_to_world_millimetres(self) -> None:
        transform = np.eye(4)
        transform[:3, 3] = [0.0, 0.0, 969.0]
        result = camera_points_m_to_world_mm(
            np.asarray([[0.0, 0.0, 0.0], [0.1, -0.2, 1.0]]), transform
        )
        np.testing.assert_allclose(result[0], [0.0, 0.0, 969.0])
        np.testing.assert_allclose(result[1], [100.0, -200.0, 1969.0])
        self.assertEqual(float(result[1, 2]), 1969.0)

    def test_off_axis_axial_and_radial_hypotheses_differ(self) -> None:
        depth = np.zeros((3, 3), dtype=np.float32)
        depth[0, 2] = 1.0
        camera = {
            "model": "pinhole",
            "fx": 1.0,
            "fy": 1.0,
            "cx": 1.0,
            "cy": 1.0,
            "distortion": [],
        }
        axial, axial_valid = world_points_from_depth(
            depth, camera, np.eye(4), "axial_z", min_depth_m=0.1, max_depth_m=2.0
        )
        radial, radial_valid = world_points_from_depth(
            depth,
            camera,
            np.eye(4),
            "radial_range",
            min_depth_m=0.1,
            max_depth_m=2.0,
        )
        self.assertTrue(np.array_equal(axial_valid, radial_valid))
        self.assertFalse(np.allclose(axial[0, 2], radial[0, 2]))
        self.assertAlmostEqual(float(axial[0, 2, 2]), 1000.0)
        self.assertAlmostEqual(float(np.linalg.norm(radial[0, 2])), 1000.0)

    def _synthetic_world(self) -> tuple[np.ndarray, np.ndarray]:
        height, width = 120, 160
        world = np.full((height, width, 3), np.nan, dtype=np.float64)
        valid = np.zeros((height, width), dtype=bool)

        yy, xx = np.mgrid[70:120, 0:160]
        world[70:120, :, 0] = (xx - 80) * 12.0
        world[70:120, :, 1] = 300.0 + (yy - 70) * 25.0
        world[70:120, :, 2] = 0.0
        valid[70:120, :] = True

        yy, xx = np.mgrid[25:70, 30:130]
        world[25:70, 30:130, 0] = (xx - 80) * 8.0
        world[25:70, 30:130, 1] = 900.0 + (yy - 25) * 8.0
        world[25:70, 30:130, 2] = 700.0
        valid[25:70, 30:130] = True

        yy, xx = np.mgrid[0:25, 0:160]
        world[0:25, :, 0] = (xx - 80) * 10.0
        world[0:25, :, 1] = 1500.0
        world[0:25, :, 2] = 100.0 + yy * 30.0
        valid[0:25, :] = True

        rng = np.random.default_rng(3)
        indices = np.flatnonzero(valid)
        outliers = rng.choice(indices, size=80, replace=False)
        flat = world.reshape(-1, 3)
        flat[outliers, 2] += rng.uniform(80.0, 250.0, size=outliers.size)
        missing = rng.choice(indices, size=70, replace=False)
        valid.reshape(-1)[missing] = False
        world.reshape(-1, 3)[missing] = np.nan
        return world, valid

    def test_synthetic_ground_table_and_vertical_rejection(self) -> None:
        world, valid = self._synthetic_world()
        first = extract_horizontal_planes(world, valid, self.profile)
        second = extract_horizontal_planes(world, valid, self.profile)
        labels = [item["label"] for item in first["planes"]]
        self.assertIn("ground", labels)
        self.assertIn("support_surface", labels)
        support = next(
            item for item in first["planes"] if item["label"] == "support_surface"
        )
        self.assertLess(abs(support["median_height_mm"] - 700.0), 2.0)
        self.assertTrue(
            any(
                item["reason"] == "non_horizontal"
                for item in first["rejected_candidates"]
            )
        )
        self.assertEqual(
            [(item["plane_id"], item["label"]) for item in first["planes"]],
            [(item["plane_id"], item["label"]) for item in second["planes"]],
        )
        for item in first["planes"]:
            x1, y1, x2, y2 = item["bbox_xyxy"]
            self.assertLessEqual(x1, x2)
            self.assertLessEqual(y1, y2)
            self.assertGreaterEqual(len(item["contour_xy"]), 4)

    def test_non_horizontal_surface_is_not_accepted(self) -> None:
        yy, xx = np.mgrid[0:80, 0:100]
        world = np.stack(
            [xx * 10.0, np.full_like(xx, 800.0), yy * 10.0], axis=2
        ).astype(np.float64)
        valid = np.ones((80, 100), dtype=bool)
        result = extract_horizontal_planes(world, valid, self.profile)
        self.assertEqual(result["planes"], [])
        self.assertTrue(
            any(
                item["reason"] == "non_horizontal"
                for item in result["rejected_candidates"]
            )
        )


if __name__ == "__main__":
    unittest.main()
