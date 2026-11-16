from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class DrinkWaterTremorPcDisplayContractTest(unittest.TestCase):
    def test_actual_trajectory_labels_lf_tr_and_tremor(self) -> None:
        source = read_text("frontend/src/twin/actualTrajectory.ts")
        self.assertIn('LF: "躯干前倾"', source)
        self.assertIn('TR: "躯干旋转"', source)
        self.assertIn('TREMOR: "手部抖动"', source)

    def test_session_results_uses_shared_compensation_labels(self) -> None:
        source = read_text("frontend/src/pages/SessionResultsPage.tsx")
        self.assertIn("compensationLabels(rep.compensation.types).join", source)
        self.assertNotIn('type === "LF"', source)

    def test_patient_reports_labels_tremor(self) -> None:
        source = read_text("frontend/src/pages/PatientReportsPage.tsx")
        self.assertIn('if (type === "TREMOR") return "手部抖动"', source)

    def test_report_formatter_lists_specific_compensation_types(self) -> None:
        source = read_text("frontend/src/utils/reportFieldFormatters.ts")
        self.assertIn('if (type === "TREMOR") return "手部抖动"', source)
        self.assertIn('if (type === "LF") return "躯干前倾"', source)
        self.assertIn('labels.length ? labels.join(" / ") : "检测到代偿"', source)

    def test_tremor_does_not_modify_reconstruction_bones_or_kinematic_prior(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        pose = read_text("frontend/src/twin/HybridDrinkPose.ts")
        scene = read_text("frontend/src/twin/AnatomyScene.ts")
        self.assertIn("composeHybridDrinkPose(activeFrame", page)
        self.assertIn("sampleDrinkReferencePose(normalizedPhase)", pose)
        self.assertIn("检测到手部抖动代偿", page)
        self.assertIn("代偿提示", page)
        self.assertNotIn("TREMOR", pose)
        self.assertNotIn("TREMOR", scene)


if __name__ == "__main__":
    unittest.main()
