from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
SRC = COMPONENT / "src"
TEST_DIR = COMPONENT / "test"
sys.path.insert(0, str(TEST_DIR))

from llm_compact_reference import prepare_formal_input  # noqa: E402


def formal_paths() -> list[Path]:
    downloads = Path.home() / "Downloads"
    return sorted(downloads.rglob("latest_formal_decision_input*.json"))


class DecisionPreparationHostTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="decision_preparation_")
        cls.exe = Path(cls.temp.name) / "decision_preparation.exe"
        cjson = Path(
            r"C:\esp\v5.5.4\frameworks\esp-idf-v5.5.4\components\json\cJSON"
        )
        env = os.environ.copy()
        env["PATH"] = r"C:\msys64\ucrt64\bin;" + env["PATH"]
        subprocess.run(
            [
                r"C:\msys64\ucrt64\bin\gcc.exe",
                "-std=gnu11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(SRC),
                "-I",
                str(TEST_DIR / "stage4a_host_stubs"),
                "-I",
                str(cjson),
                str(TEST_DIR / "test_decision_preparation_host.c"),
                str(SRC / "rehab_task_decision_preparation.c"),
                str(SRC / "rehab_task_decision_policy.c"),
                str(cjson / "cJSON.c"),
                "-lm",
                "-o",
                str(cls.exe),
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def c_prepare(self, formal: dict) -> dict:
        with tempfile.TemporaryDirectory(prefix="decision_preparation_case_") as folder:
            path = Path(folder) / "formal.json"
            path.write_text(
                json.dumps(formal, ensure_ascii=False), encoding="utf-8"
            )
            result = subprocess.run(
                [str(self.exe), str(path)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        return json.loads(result.stdout)["decision_preparation"]

    def assert_matches_reference(self, formal: dict) -> dict:
        expected = prepare_formal_input(formal)
        actual = self.c_prepare(formal)
        self.assert_json_near(actual, expected)
        return actual

    def assert_json_near(self, actual: object, expected: object) -> None:
        if isinstance(expected, dict):
            self.assertIsInstance(actual, dict)
            self.assertEqual(set(actual), set(expected))
            for key in expected:
                self.assert_json_near(actual[key], expected[key])
        elif isinstance(expected, list):
            self.assertIsInstance(actual, list)
            self.assertEqual(len(actual), len(expected))
            for left, right in zip(actual, expected, strict=True):
                self.assert_json_near(left, right)
        elif isinstance(expected, float):
            self.assertIsInstance(actual, (int, float))
            self.assertAlmostEqual(float(actual), expected, places=12)
        else:
            self.assertEqual(actual, expected)

    def test_all_recursive_download_formal_inputs_match_reference(self) -> None:
        paths = formal_paths()
        if not paths:
            self.skipTest("download formal replay corpus is not installed")
        for path in paths:
            with self.subTest(path=path):
                formal = json.loads(path.read_text(encoding="utf-8-sig"))
                prepared = self.assert_matches_reference(formal)
                self.assertEqual(
                    [entry["candidate_ref"] for entry in prepared["candidates"]],
                    [0, 1, 2, 3, 4],
                )

    def test_synthetic_ability_tendency_compensation_frequency_fallback_blocked(self) -> None:
        paths = formal_paths()
        if not paths:
            self.skipTest("download formal replay corpus is not installed")
        formal = json.loads(paths[-1].read_text(encoding="utf-8-sig"))
        synthetic = copy.deepcopy(formal)
        synthetic["patient_ability"]["level"] = 3
        synthetic["recent_performance"]["tendency_level"] = "level_1"
        synthetic["item_frequency"]["cup_count"] = 3
        synthetic["item_frequency"]["ball_count"] = 0
        synthetic["latest_compensation"].update({
            "status": "available",
            "exact_task_id": "OT_CUP_DRINK_L2",
            "action_family": "drink_water",
            "mapped_group": "cup",
            "compensation_action_count": 2,
            "severity": 0.9,
        })
        facts = synthetic["candidates"][1]["target_facts"]
        facts.pop("perception_quality", None)
        facts["geometry_confidence"] = None
        facts["detection_confidence"] = 0.9
        facts["height_confidence"] = 1.0

        preparation = self.assert_matches_reference(synthetic)
        prepared = preparation["candidates"]
        self.assertEqual(prepared[0]["fixed_components"]["difficulty"], 35)
        self.assertEqual(prepared[1]["fixed_components"]["difficulty"], 14)
        self.assertAlmostEqual(
            prepared[1]["fixed_components"]["compensation"], 2.0
        )
        self.assertEqual(prepared[0]["fixed_components"]["frequency"], 0)
        self.assertEqual(prepared[3]["fixed_components"]["frequency"], 10)
        self.assertAlmostEqual(
            prepared[1]["fixed_components"]["perception"], 9.76
        )
        self.assertIsNone(prepared[2]["fixed_components"])
        self.assertNotIn("advice_score_lookup", prepared[2])
        self.assertEqual(
            preparation["advice_policy"],
            {
                "range": [16, 25],
                "positive_range": [21, 25],
                "negative_range": [16, 19],
                "neutral": 20,
            },
        )


if __name__ == "__main__":
    unittest.main()
