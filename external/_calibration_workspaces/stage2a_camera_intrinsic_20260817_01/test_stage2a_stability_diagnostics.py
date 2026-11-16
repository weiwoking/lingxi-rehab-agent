from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("stage2a_stability_diagnostics", ROOT / "stage2a_stability_diagnostics.py")
stability = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(stability)


BASE = {
    "fx": 867.1047481579573, "fy": 866.0458641457095,
    "cx": 302.9457452600097, "cy": 179.38137047375486,
    "k1": 0.05385981260561747, "k2": 0.0783975233935736,
    "p1": 0.00663036010743015, "p2": -0.006245150307360047,
    "k3": -1.5331527308460569,
}


class MappingTests(unittest.TestCase):
    def setUp(self):
        K, _ = stability.params_to_camera(BASE)
        self.grid = stability.make_mapping_grid(640, 360, 16, K)

    def test_identity_is_zero_and_finite(self):
        metrics, raw = stability.compare_camera_solutions(BASE, BASE, self.grid)
        self.assertLessEqual(metrics["full_image"]["equivalent_rectified_displacement_px"]["max"], 1e-12)
        self.assertLessEqual(metrics["full_image"]["angular_difference_deg"]["max"], 1e-12)
        self.assertTrue(np.isfinite(raw["displacement_px"]).all())
        self.assertTrue(np.isfinite(raw["angle_deg"]).all())

    def test_fx_perturbation_is_small_near_center_and_nonzero_at_edges(self):
        changed = dict(BASE); changed["fx"] += 1.0
        metrics, _ = stability.compare_camera_solutions(BASE, changed, self.grid)
        regions = metrics["regions"]
        center = regions["center_middle_50pct"]["equivalent_rectified_displacement_px"]["p95"]
        border = regions["border_outer_15pct"]["equivalent_rectified_displacement_px"]["p95"]
        self.assertGreater(border, 0.0)
        self.assertGreater(border, center)

    def test_different_brown_solutions_return_dense_stats_without_nan(self):
        changed = dict(BASE)
        changed.update({"k1": BASE["k1"] + .01, "k2": BASE["k2"] - .1, "k3": BASE["k3"] + .5})
        metrics, raw = stability.compare_camera_solutions(BASE, changed, self.grid)
        self.assertGreater(len(self.grid), 900)
        self.assertIn("p99", metrics["full_image"]["equivalent_rectified_displacement_px"])
        self.assertGreater(metrics["full_image"]["equivalent_rectified_displacement_px"]["max"], 0.0)
        self.assertTrue(all(np.isfinite(value).all() for value in raw.values()))


class SelectionTests(unittest.TestCase):
    def test_lopo_has_n_runs_and_removed_pose_is_absent(self):
        selection = [(f"P{i:02d}", f"F{i:02d}") for i in range(7)]
        runs = stability.leave_one_out_selections(selection)
        self.assertEqual(len(runs), len(selection))
        for removed, subset in runs:
            self.assertNotIn(removed, {pose for pose, _ in subset})

    def test_preedge22_excludes_only_three_named_poses(self):
        selection = ([(f"A{i:02d}", f"F{i:02d}") for i in range(1, 5)] +
                     [(f"B{i:02d}", f"F{i:02d}") for i in range(1, 9)] +
                     [(f"C{i:02d}", f"F{i:02d}") for i in range(1, 14)])
        result = stability.reconstruct_preedge22(selection)
        self.assertEqual(len(result), 22)
        self.assertEqual(result, [item for item in selection if item[0] not in {"C11", "C12", "C13"}])

    def test_historical_subsets_are_explicit_and_ignore_current_extras(self):
        selection = ([(f"A{i:02d}", f"FA{i:02d}") for i in range(1, 5)] +
                     [(f"B{i:02d}", f"FB{i:02d}") for i in range(1, 9)] +
                     [(f"C{i:02d}", f"FC{i:02d}") for i in range(1, 19)])
        self.assertEqual([pose for pose, _ in stability.reconstruct_preedge22(selection)], list(stability.PREEDGE22_POSE_IDS))
        self.assertEqual([pose for pose, _ in stability.reconstruct_pre_supplement25(selection)], list(stability.PRE_SUPPLEMENT25_POSE_IDS))

    def test_subsets_are_deterministic_unique_and_fixed_size(self):
        first = stability.deterministic_subsets(25, 20, 100, 20260817)
        second = stability.deterministic_subsets(25, 20, 100, 20260817)
        self.assertEqual(first, second)
        self.assertTrue(all(len(run) == len(set(run)) == 20 for run in first))

    def test_dynamic_subset_size_uses_ceil_rule(self):
        self.assertEqual(stability.derive_subset_size(25, 0.8), 20)
        self.assertEqual(stability.derive_subset_size(28, 0.8), 23)
        self.assertEqual(stability.derive_subset_size(30, 0.8), 24)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReadOnlyCommandTests(unittest.TestCase):
    def _run_complete_diagnostics(self, pose_count: int):
        with tempfile.TemporaryDirectory() as temp_name:
            workspace = Path(temp_name)
            (workspace / "candidate").mkdir()
            poses = (list(stability.PRE_SUPPLEMENT25_POSE_IDS) +
                     [f"C{i:02d}" for i in range(14, 14 + pose_count - 25)])
            selected = [{"pose_id": pose, "frame_id": f"frame_{index:06d}"} for index, pose in enumerate(poses)]
            candidate = {
                "selected_frames": selected,
                "heldout_frames": [],
                "strategy_stability": {"primary": BASE, "top2": {**BASE, "fx": BASE["fx"] - 1.0},
                                       "small_outlier_filtered": {**BASE, "cx": BASE["cx"] + .25}},
                "outlier_diagnostics": {"excluded_for_diagnostic_only": [{"pose_id": "A04", "frame_id": selected[3]["frame_id"]}]},
            }
            candidate_path = workspace / "candidate" / "camera_intrinsics.stage2a_candidate.v1.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            manifest_path = workspace / "workspace_manifest.json"
            manifest_path.write_text(json.dumps({"poses": {row["pose_id"]: {"selected_frame": row["frame_id"]} for row in selected}}), encoding="utf-8")
            ranking_path = workspace / "pose_frame_ranking.csv"
            ranking_path.write_text("pose_id,frame_id,rank\n" + "".join(f"{row['pose_id']},{row['frame_id']},1\n" for row in selected), encoding="utf-8")

            def calibrate_set(selection):
                numbers = [poses.index(pose) + 1 for pose, _ in selection]
                shift = (sum(numbers) / len(numbers) - 13.0) * 0.01
                params = dict(BASE)
                params.update({"fx": BASE["fx"] + shift, "fy": BASE["fy"] + shift * .9,
                               "cx": BASE["cx"] + shift * .2, "cy": BASE["cy"] - shift * .1,
                               "k1": BASE["k1"] + shift * .0001})
                K, D = stability.params_to_camera(params)
                return {"rms": .15 + abs(shift) * .001, "K": K, "dist": D}

            def parameter_dict(fit):
                K, D = fit["K"], fit["dist"]
                return {"fx": float(K[0, 0]), "fy": float(K[1, 1]), "cx": float(K[0, 2]), "cy": float(K[1, 2]),
                        "k1": float(D[0]), "k2": float(D[1]), "p1": float(D[2]), "p2": float(D[3]), "k3": float(D[4])}

            class FakeError(RuntimeError):
                pass

            tool = SimpleNamespace(
                WORKSPACE=workspace, MANIFEST_PATH=manifest_path, CANDIDATE_PATH=candidate_path,
                WIDTH=640, HEIGHT=360, Stage2AError=FakeError,
                sha256_file=_sha, load_json=lambda path: json.loads(path.read_text(encoding="utf-8")),
                validate_candidate=lambda value: [], calibrate_set=calibrate_set, parameter_dict=parameter_dict,
                now=lambda: "2026-08-17T00:00:00Z",
            )
            before = {path: _sha(path) for path in (manifest_path, ranking_path, candidate_path)}
            with mock.patch.object(stability, "_heatmap"):
                summary = stability.run_diagnostics(
                    tool, workspace / "diagnostics", subsample_runs=3, seed=17, grid_step=16,
                    progress=lambda _: None, expected_candidate_sha256=before[candidate_path],
                )
            after = {path: _sha(path) for path in before}
            self.assertEqual(before, after)
            self.assertTrue(summary["protected_files_unchanged"])
            self.assertEqual(summary["selection_validation"]["pose_count"], pose_count)
            self.assertEqual(summary["lopo"]["run_count"], pose_count)
            self.assertEqual(summary["subsample80"]["run_count"], 3)
            self.assertEqual(summary["subsample80"]["subset_size"], stability.derive_subset_size(pose_count, 0.8))
            self.assertEqual(summary["pre_supplement25"]["lopo"]["run_count"], 25)
            self.assertEqual(summary["historical_preedge22"]["pose_count"], 22)
            self.assertEqual(len(summary["current"]["reference_parameters"]), 9)
            return summary

    def test_complete_diagnostics_are_dynamic_and_read_only_for_25_28_30(self):
        for pose_count in (25, 28, 30):
            with self.subTest(pose_count=pose_count):
                summary = self._run_complete_diagnostics(pose_count)
                selected_ids = [row["pose_id"] for row in summary["selection_validation"]["selected_frames"]]
                if pose_count >= 28:
                    self.assertTrue({"C14", "C15", "C16"}.issubset(selected_ids))


if __name__ == "__main__":
    unittest.main()
