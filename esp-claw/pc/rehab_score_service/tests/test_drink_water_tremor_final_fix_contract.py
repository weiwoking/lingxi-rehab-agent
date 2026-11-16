from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATION_SCRIPT = ROOT / "tools" / "drink_water_tremor_real_data_validation_v1.ps1"


class DrinkWaterTremorFinalFixContractTest(unittest.TestCase):
    def test_validation_script_uses_strict_score_json_filter(self) -> None:
        source = VALIDATION_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('-Filter "*_score.json"', source)
        self.assertNotIn('-Filter "*score*.json"', source)

    def test_validation_script_excludes_score_metadata_json(self) -> None:
        source = VALIDATION_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('Name -notlike "*_score_metadata.json"', source)

    def test_validation_script_checks_exact_rep_count(self) -> None:
        source = VALIDATION_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("REP_COUNT_MISMATCH", source)
        self.assertIn("$actualCount -ne $expectedCount", source)
        self.assertNotIn("$scoreFiles.Count -lt $spec.Labels.Count", source)

    def test_validation_script_outputs_effective_penalty_and_checks_expected_penalty(self) -> None:
        source = VALIDATION_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("effective_penalty", source)
        self.assertIn("expected_additional_penalty", source)
        self.assertIn("PENALTY_CHECK_PASS", source)
        self.assertIn("PENALTY_CHECK_FAIL", source)
        self.assertIn("$penaltyTolerance = 0.05", source)
        self.assertIn("penalty_error", source)
        self.assertIn("penalty_tolerance", source)
        self.assertNotIn("0.000001", source)

    def test_validation_script_reads_score_json_as_utf8(self) -> None:
        source = VALIDATION_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("Get-Content -LiteralPath $scoreFiles[$i].FullName -Raw -Encoding UTF8 | ConvertFrom-Json", source)
        self.assertNotIn("Get-Content -LiteralPath $scoreFiles[$i].FullName -Raw | ConvertFrom-Json", source)

    def test_validation_tolerance_does_not_change_scoring_penalty_constants(self) -> None:
        source = (ROOT / "scoring" / "score_action_integrated.py").read_text(encoding="utf-8")
        self.assertIn("LF_EXTRA_PENALTY = 10.0", source)
        self.assertIn("TREMOR_PENALTY = 15.0", source)

    def test_validation_script_reports_required_metrics(self) -> None:
        source = VALIDATION_SCRIPT.read_text(encoding="utf-8")
        for token in (
            "REP_COUNT_TOTAL",
            "TREMOR_TP",
            "TREMOR_FN",
            "TREMOR_FP",
            "TREMOR_TN",
            "LF_TP",
            "LF_FN",
            "LF_FP",
            "LF_TN",
            "AVG_FINAL_SCORE_NORMAL",
            "AVG_FINAL_SCORE_LF",
            "AVG_FINAL_SCORE_TREMOR",
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
