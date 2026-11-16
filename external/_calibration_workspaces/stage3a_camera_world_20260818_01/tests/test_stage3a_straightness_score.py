from __future__ import annotations

import contextlib
import hashlib
import io
import sys
import unittest
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE))
import stage3a_straightness_score as scorer


class StraightnessSyntheticTests(unittest.TestCase):
    def test_pure_lateral_translation_does_not_imply_yaw(self) -> None:
        for lateral in (-0.15, 0.0, 0.15):
            score = scorer.synthetic_single_score(0.0, lateral, 0.8)
            proxy = score["aggregate"]["signed_straightness_proxy_deg"]["median"]
            self.assertLess(abs(proxy), 5e-5)
            self.assertNotIn(score["decision"], {"TOO_LEFT", "TOO_RIGHT"})

    def test_yaw_proxy_is_signed_and_monotonic(self) -> None:
        values = []
        for yaw in (-5.0, -3.0, -1.0, 0.0, 1.0, 3.0, 5.0):
            score = scorer.synthetic_single_score(yaw, 0.0, 0.8)
            values.append(score["aggregate"]["signed_straightness_proxy_deg"]["median"])
        self.assertTrue(all(a < b for a, b in zip(values, values[1:])))
        self.assertLess(values[0], 0.0)
        self.assertGreater(values[-1], 0.0)

    def test_left_right_decision_sign(self) -> None:
        self.assertEqual(scorer.synthetic_single_score(-6.0, 0.0, 0.8)["decision"], "TOO_LEFT")
        self.assertEqual(scorer.synthetic_single_score(+6.0, 0.0, 0.8)["decision"], "TOO_RIGHT")

    def test_realistic_noise_is_robust_at_decision_boundary(self) -> None:
        for yaw in (-3.0, 0.0, 3.0):
            decisions = {
                scorer.synthetic_single_score(yaw, 0.0, 0.8, noise_px=0.30, seed=seed)["decision"]
                for seed in range(12)
            }
            self.assertNotIn("TOO_RIGHT" if yaw <= 0 else "TOO_LEFT", decisions)


class StraightnessExistingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = scorer.score_existing(include_sensitivity=True)

    def test_existing_scores_are_deterministic(self) -> None:
        second = scorer.score_existing(include_sensitivity=False)
        for pose in scorer.POSE_IDS:
            a = self.result["scores"][pose]
            b = second["scores"][pose]
            self.assertEqual(a["decision"], b["decision"])
            self.assertEqual(a["straightness_score_0_100"], b["straightness_score_0_100"])

    def test_w03_special_case_is_image_frontal(self) -> None:
        self.assertTrue(self.result["w03_is_image_frontal"])
        self.assertIn(self.result["scores"]["W03"]["decision"], {"STRAIGHT_ENOUGH", "BOARD_OFF_CENTER_BUT_STRAIGHT"})
        prior = scorer.read_json(
            WORKSPACE / "diagnostics" / "distance_drift_20260818" / "W01_W02_W03_POSE_COMPARISON.json"
        )["current_iterative"]["fixed_height_using_aggregate_R"]["per_pose"]
        residuals = {pose: prior[pose]["signed_residual_mm"] for pose in scorer.POSE_IDS}
        self.assertEqual(max(residuals, key=residuals.get), "W03")

    def test_intrinsics_ensemble_does_not_flip_decision(self) -> None:
        sensitivity = self.result["intrinsics_ensemble_sensitivity"]
        self.assertEqual(sensitivity["model_count"], 28)
        self.assertTrue(all(value["all_decisions_match_nominal"] for value in sensitivity["per_pose"].values()))

    def test_human_output_never_requests_measurement(self) -> None:
        forbidden = ("degree", "angle", "ruler", "distance", "°", "量角", "测量")
        for pose in scorer.POSE_IDS:
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                scorer.print_human(self.result["scores"][pose], pose)
            output = stream.getvalue().lower()
            self.assertFalse(any(word in output for word in forbidden), output)

    def test_no_capture_or_registration_command(self) -> None:
        parser = scorer.build_parser()
        help_text = parser.format_help().lower()
        self.assertNotIn("capture", help_text)
        self.assertNotIn("register", help_text)

    def test_frozen_hashes_unchanged(self) -> None:
        self.assertEqual(scorer.sha256(scorer.STAGE2_BUNDLE), scorer.EXPECTED_STAGE2_SHA256)
        self.assertEqual(scorer.sha256(scorer.REGISTRY), "77219b55b4a1716b6bc51c643a84d5a1b20171e3565f323cb443b2245b9f7050")
        self.assertEqual(
            scorer.sha256(WORKSPACE / "stage3a_camera_world.py"),
            "8aa81cb4e6a0d176050232c3acf01fa91c682c96919c4e9a38fd661c3798b2ef",
        )


if __name__ == "__main__":
    unittest.main()
