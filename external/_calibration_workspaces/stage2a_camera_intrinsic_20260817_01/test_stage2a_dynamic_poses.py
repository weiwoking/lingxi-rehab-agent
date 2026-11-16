import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import stage2a_camera_intrinsic as stage2a


class DynamicPoseDatasetTest(unittest.TestCase):
    pose_ids = (*stage2a.LEGACY_POSE_IDS, "C09")

    @staticmethod
    def _old_profile():
        return {
            "camera": {
                "resolution": {"width": stage2a.WIDTH, "height": stage2a.HEIGHT},
                "intrinsics": {
                    "fx": 620.0, "fy": 615.0, "cx": 319.5, "cy": 179.5,
                    "distortion": {"k1": -0.1, "k2": 0.02, "p1": 0.001, "p2": -0.001, "k3": 0.005},
                },
            },
            "quality": {"fit_rms_px": 0.2, "heldout_rms_px": 0.3},
        }

    @staticmethod
    def _manifest(pose_ids, ranked=False):
        poses = {}
        for pose in pose_ids:
            item = {
                "state": "REGISTERED", "scan_id": f"scan_{pose}",
                "frames": [
                    {"frame_id": f"frame_{index:06d}", "camera_file": f"unused/{pose}/{index}.rgb565"}
                    for index in range(11)
                ],
            }
            if ranked:
                item.update({
                    "state": "SELECTED", "selected_frame": "frame_000000",
                    "heldout_frame": "frame_000001", "heldout_state": "HELD_OUT",
                })
            poses[pose] = item
        return {
            "schema_version": "stage2a_workspace_manifest.v1",
            "board": stage2a.BOARD,
            "poses": poses,
        }

    @contextlib.contextmanager
    def _workspace(self, pose_ids=None, ranked=False):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            manifest_path = workspace / "workspace_manifest.json"
            old_path = workspace / "old_stage7a.json"
            candidate_path = workspace / "candidate" / "camera_intrinsics.stage2a_candidate.v1.json"
            manifest_path.write_text(json.dumps(self._manifest(pose_ids or self.pose_ids, ranked=ranked)), encoding="utf-8")
            old_path.write_text(json.dumps(self._old_profile()), encoding="utf-8")
            with mock.patch.multiple(
                stage2a,
                WORKSPACE=workspace,
                MANIFEST_PATH=manifest_path,
                OLD_STAGE7A=old_path,
                CANDIDATE_PATH=candidate_path,
                SCHEMA_PATH=workspace / "candidate_schema.json",
            ):
                yield workspace

    def test_safe_dynamic_pose_ids_and_illegal_ids(self):
        for pose in ("C09", "D01", "EX01", "LOW_UP_01", "EXTRA01"):
            self.assertEqual(stage2a.require_pose(pose.lower()), pose)
        for pose in ("", "_C09", "C09_", "../C09", "C 09", "C.09", "A" * 65):
            with self.assertRaises(stage2a.Stage2AError, msg=pose):
                stage2a.require_pose(pose)

    def test_status_and_rank_enumerate_all_21_registered_poses(self):
        corners = np.zeros((12, 1, 2), np.float32)
        ids = np.arange(12, dtype=np.int32).reshape(-1, 1)
        metrics = {"decode_status": "OK", "quality_score": 10.0, "detected_charuco_corner_count": 12}
        with self._workspace() as workspace, \
             mock.patch.object(stage2a, "detect_image", return_value=(metrics, corners, ids)) as detect, \
             mock.patch.object(stage2a, "contact_sheet"), \
             mock.patch.object(stage2a, "coverage_plot"):
            rows = stage2a.rank_all()
            self.assertEqual(len(rows), 21 * 11)
            self.assertEqual(detect.call_count, 21 * 11)
            updated = stage2a.manifest()
            self.assertEqual(stage2a.registered_pose_ids(updated), self.pose_ids)
            self.assertEqual(updated["poses"]["C09"]["selected_frame"], "frame_000000")
            self.assertEqual(updated["poses"]["C09"]["heldout_frame"], "frame_000001")
            self.assertTrue((workspace / "rankings" / "C09.json").is_file())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                stage2a.cmd_status(argparse.Namespace())
            self.assertIn("registered=21 selected=21 heldout=21 extras=1", output.getvalue())
            self.assertIn("C09 SELECTED", output.getvalue())

    def test_run_all_candidate_and_compare_include_pose_21(self):
        heldout_selections = []

        def fake_fit(selection):
            per_views = [
                {"pose_id": pose, "frame_id": frame, "corner_count": 12, "rms_px": 0.2, "p95_px": 0.3, "max_px": 0.4}
                for pose, frame in selection
            ]
            return {
                "rms": 0.2,
                "K": np.array([[620.0, 0.0, 319.5], [0.0, 615.0, 179.5], [0.0, 0.0, 1.0]]),
                "dist": np.array([-0.1, 0.02, 0.0, 0.0, 0.0]),
                "rvecs": [], "tvecs": [], "per_views": per_views, "all_errors": np.array([]),
            }

        def fake_heldout(_K, _dist, selection):
            heldout_selections.append(tuple(selection))
            return {
                "frames": len(selection), "corners": len(selection) * 12,
                "rms_px": 0.3, "p95_px": 0.4, "max_px": 0.5,
                "per_pose": [], "vectors": [], "radii": [], "errors": [],
            }

        with self._workspace(ranked=True), \
             mock.patch.object(stage2a, "rank_all", return_value=[]), \
             mock.patch.object(stage2a, "calibrate_set", side_effect=fake_fit) as calibrate, \
             mock.patch.object(stage2a, "heldout_metrics", side_effect=fake_heldout), \
             mock.patch.object(stage2a, "save_residual_outputs"):
            rc = stage2a.cmd_run_all(argparse.Namespace(allow_weak=False))
            self.assertEqual(rc, 0)
            candidate = json.loads(stage2a.CANDIDATE_PATH.read_text(encoding="utf-8"))
            self.assertEqual(candidate["source_pose_ids"], list(self.pose_ids))
            self.assertEqual(candidate["metrics"]["fit_frames"], 21)
            self.assertEqual(candidate["metrics"]["heldout_frames"], 21)
            self.assertIn({"pose_id": "C09", "frame_id": "frame_000000"}, candidate["selected_frames"])
            self.assertIn({"pose_id": "C09", "frame_id": "frame_000001"}, candidate["heldout_frames"])
            self.assertEqual([len(call.args[0]) for call in calibrate.call_args_list], [21, 42])
            self.assertEqual([len(selection) for selection in heldout_selections], [21, 21, 21])

    def test_duplicate_pose_id_fails_before_scan_access_and_preserves_manifest(self):
        with self._workspace(("C06",)):
            before = stage2a.MANIFEST_PATH.read_bytes()
            args = argparse.Namespace(pose="C06", scan_id=None, latest=True, replace=False, dry_run=False)
            with mock.patch.object(stage2a, "resolve_scan") as resolve:
                with self.assertRaisesRegex(stage2a.Stage2AError, "already registered"):
                    stage2a.cmd_register(args)
                resolve.assert_not_called()
            self.assertEqual(stage2a.MANIFEST_PATH.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
