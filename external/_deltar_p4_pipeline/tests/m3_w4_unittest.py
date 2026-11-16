from __future__ import annotations

import inspect
import math
import unittest

from deltar_p4_pipeline.geometry_v2 import multimodal_support as m3


class M3W4Tests(unittest.TestCase):
    def test_single_mode_identity(self) -> None:
        self.assertEqual(
            m3.squared_probability_expectation(
                [{"center_mm": 731.25, "probability": 1.0}]
            ),
            731.25,
        )

    def test_two_mode_formula(self) -> None:
        modes = [
            {"center_mm": 700.0, "probability": 0.25},
            {"center_mm": 900.0, "probability": 0.75},
        ]
        expected = (0.25**2 * 700.0 + 0.75**2 * 900.0) / (0.25**2 + 0.75**2)
        self.assertAlmostEqual(m3.squared_probability_expectation(modes), expected, 12)

    def test_three_mode_formula(self) -> None:
        modes = [
            {"center_mm": 600.0, "probability": 0.2},
            {"center_mm": 750.0, "probability": 0.3},
            {"center_mm": 920.0, "probability": 0.5},
        ]
        expected = sum(row["probability"] ** 2 * row["center_mm"] for row in modes)
        expected /= sum(row["probability"] ** 2 for row in modes)
        self.assertAlmostEqual(m3.squared_probability_expectation(modes), expected, 12)

    def test_zero_and_invalid_probabilities(self) -> None:
        self.assertEqual(
            m3.squared_probability_expectation(
                [
                    {"center_mm": 500.0, "probability": 0.0},
                    {"center_mm": 800.0, "probability": 1.0},
                ]
            ),
            800.0,
        )
        for modes in (
            [],
            [{"center_mm": 700.0, "probability": 0.0}],
            [{"center_mm": 700.0, "probability": -0.1}],
            [{"center_mm": math.nan, "probability": 1.0}],
        ):
            with self.assertRaises(ValueError):
                m3.squared_probability_expectation(modes)

    def test_probability_normalization_and_retained_cap(self) -> None:
        rows = [
            {"height_mm": float(height), "weight": 1.0 / 6.0}
            for height in (100.0, 200.0, 300.0, 400.0, 500.0, 600.0)
        ]
        modes = m3.retained_modes(rows)
        self.assertLessEqual(len(modes), m3.LATE_MAXIMUM_MODES)
        self.assertTrue(all(0.0 < row["probability"] <= 1.0 for row in modes))

    def test_object_order_independence(self) -> None:
        a = [
            {"object_id": "a", "height_mm": 700.0, "weight": 0.7},
            {"object_id": "a", "height_mm": 760.0, "weight": 0.3},
        ]
        b = [
            {"object_id": "b", "height_mm": 705.0, "weight": 0.4},
            {"object_id": "b", "height_mm": 900.0, "weight": 0.6},
        ]
        forward = m3.combine_context_distributions([a, b])
        reverse = m3.combine_context_distributions([b, a])
        self.assertAlmostEqual(forward["support_mm"], reverse["support_mm"], 12)
        self.assertEqual(forward["retained_mode_count"], reverse["retained_mode_count"])
        for left, right in zip(forward["retained_modes"], reverse["retained_modes"]):
            self.assertAlmostEqual(left["center_mm"], right["center_mm"], 12)
            self.assertAlmostEqual(left["probability"], right["probability"], 12)

    def test_w0_hard_switch_is_absent(self) -> None:
        source = inspect.getsource(m3)
        self.assertNotIn("0.68", source)
        self.assertNotIn("1.85", source)
        self.assertNotIn("hard_top", source.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
