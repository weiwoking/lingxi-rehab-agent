from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import switch_drink_water_task12_baseline_v2_1_2 as switcher


class DrinkWaterTask12BaselineSwitchContractTests(unittest.TestCase):
    def _task12(self, **overrides):
        task = {
            "baseline_version": switcher.EXPECTED_BASELINE_VERSION,
            "segmentation_version": switcher.EXPECTED_SEGMENTATION_VERSION,
            "sensor_mapping_version": switcher.EXPECTED_SENSOR_MAPPING_VERSION,
            "marker": "candidate_task12",
        }
        task.update(overrides)
        return task

    def _write_json(self, path: Path, payload) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_01_only_replaces_task_12(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate.json"
            formal = root / "formal.json"
            self._write_json(candidate, {"12": self._task12()})
            self._write_json(formal, {"1": {"a": 1}, "12": {"old": True}, "99": {"z": 9}})
            switcher.switch_task12_baseline(candidate, formal, root / "rollback")
            payload = json.loads(formal.read_text(encoding="utf-8"))
            self.assertEqual(payload["12"], self._task12())
            self.assertEqual(payload["1"], {"a": 1})
            self.assertEqual(payload["99"], {"z": 9})

    def test_02_other_tasks_semantics_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate.json"
            formal = root / "formal.json"
            other = {"1": {"list": [1, 2], "nested": {"x": True}}}
            self._write_json(candidate, {"12": self._task12()})
            self._write_json(formal, {**other, "12": {"old": True}})
            switcher.switch_task12_baseline(candidate, formal)
            self.assertEqual(json.loads(formal.read_text(encoding="utf-8"))["1"], other["1"])

    def test_03_candidate_missing_task_12_fails(self) -> None:
        with self.assertRaises(ValueError):
            switcher.validate_candidate_task12({"11": self._task12()})

    def test_04_wrong_baseline_version_fails(self) -> None:
        with self.assertRaises(ValueError):
            switcher.validate_candidate_task12({"12": self._task12(baseline_version="wrong")})

    def test_05_wrong_sensor_mapping_version_fails(self) -> None:
        with self.assertRaises(ValueError):
            switcher.validate_candidate_task12({"12": self._task12(sensor_mapping_version="wrong")})

    def test_06_formal_invalid_json_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate.json"
            formal = root / "formal.json"
            self._write_json(candidate, {"12": self._task12()})
            formal.write_text("{bad", encoding="utf-8")
            with self.assertRaises(ValueError):
                switcher.switch_task12_baseline(candidate, formal)

    def test_07_written_task_12_equals_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate.json"
            formal = root / "formal.json"
            task = self._task12(values=[1, 2, 3])
            self._write_json(candidate, {"12": task})
            self._write_json(formal, {"12": {"old": True}})
            result = switcher.switch_task12_baseline(candidate, formal)
            self.assertTrue(result["task12_equal_candidate"])
            self.assertEqual(json.loads(formal.read_text(encoding="utf-8"))["12"], task)

    def test_08_rollback_can_restore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate.json"
            formal = root / "formal.json"
            self._write_json(candidate, {"12": self._task12()})
            original = {"12": {"old": True}, "2": {"keep": True}}
            self._write_json(formal, original)
            result = switcher.switch_task12_baseline(candidate, formal)
            Path(result["rollback"]).replace(formal)
            self.assertEqual(json.loads(formal.read_text(encoding="utf-8")), original)

    def test_09_candidate_file_is_not_modified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate.json"
            formal = root / "formal.json"
            self._write_json(candidate, {"12": self._task12()})
            self._write_json(formal, {"12": {"old": True}})
            before = candidate.read_bytes()
            switcher.switch_task12_baseline(candidate, formal)
            self.assertEqual(candidate.read_bytes(), before)

    def test_10_copied_rollback_is_created_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate.json"
            formal = root / "formal.json"
            rollback_dir = root / "delivery_rollback"
            self._write_json(candidate, {"12": self._task12()})
            self._write_json(formal, {"12": {"old": True}})
            result = switcher.switch_task12_baseline(candidate, formal, rollback_dir)
            self.assertTrue(Path(result["copied_rollback"]).exists())


if __name__ == "__main__":
    unittest.main()
