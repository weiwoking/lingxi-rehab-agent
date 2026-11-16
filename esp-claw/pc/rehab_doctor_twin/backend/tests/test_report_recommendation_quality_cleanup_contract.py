import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"


class ReportRecommendationQualityCleanupContractTest(unittest.TestCase):
    def read_source(self, relative: str) -> str:
        return (FRONTEND / relative).read_text(encoding="utf-8")

    def test_helper_is_single_pure_recommendation_source(self) -> None:
        source = self.read_source("utils/reportRecommendation.ts")
        self.assertIn("export function buildRehabRecommendations", source)
        self.assertIn("CURRENT_SUBSCORE_MAX[key]", source)
        self.assertIn("RECOMMENDATION_WEAK_THRESHOLD = 0.9", source)
        self.assertIn("RECOMMENDATION_MAX_WEAK_DIMENSIONS = 2", source)
        self.assertNotIn("report.score.feedback", source)
        self.assertNotIn("localStorage", source)
        self.assertNotIn("fetch(", source)

    def test_helper_does_not_duplicate_subscore_maxima(self) -> None:
        source = self.read_source("utils/reportRecommendation.ts")
        self.assertNotIn("speed_score: 15", source)
        self.assertNotIn("ROM_score: 20", source)
        self.assertNotIn("smoothness_score: 20", source)
        self.assertNotIn("coordination_score: 15", source)
        self.assertNotIn("muscle_score: 30", source)

    def test_task046_recommendation_contract_is_represented(self) -> None:
        source = self.read_source("utils/reportRecommendation.ts")
        self.assertIn("手部抖动代偿", source)
        self.assertIn("末端动作稳定控制", source)
        self.assertIn("肌肉激活模式仍有优化空间", source)
        self.assertIn("上肢各节段协同仍有提升空间", source)
        self.assertNotIn("提高动作速度", source)
        self.assertNotIn("扩大ROM", source)

    def test_lf_normal_and_high_quality_contracts_are_present(self) -> None:
        source = self.read_source("utils/reportRecommendation.ts")
        self.assertIn("躯干前倾代偿", source)
        self.assertIn("减少通过身体前倾辅助完成上肢任务", source)
        self.assertIn("本次训练未检测到明显代偿", source)
        self.assertIn("本次训练整体动作完成质量较好", source)

    def test_forbidden_recommendation_text_is_absent(self) -> None:
        source = self.read_source("utils/reportRecommendation.ts")
        forbidden = [
            "IMU DTW",
            "EMG评分",
            "fatigue",
            "疲劳",
            "BRS",
            "暂无数据",
            "诊断为",
            "确诊",
            "肌力不足",
            "肌肉萎缩",
            "肌肉无力",
            "越快越好",
            "提高动作速度",
            "加快动作",
            "增加训练强度",
            "减少训练强度",
        ]
        for text in forbidden:
            self.assertNotIn(text, source)

    def test_all_report_surfaces_use_shared_recommendation_helper(self) -> None:
        for relative in [
            "pages/SessionHistoryPage.tsx",
            "pages/PatientReportsPage.tsx",
            "pages/PrintableReportPage.tsx",
            "components/ReportPreview.tsx",
        ]:
            source = self.read_source(relative)
            self.assertIn("buildRehabRecommendations", source)
        self.assertNotIn(
            "(report.score.feedback?.length ? report.score.feedback : [report.summary])",
            self.read_source("pages/SessionHistoryPage.tsx"),
        )
        self.assertNotIn(
            "(report.score.feedback?.length ? report.score.feedback : [report.summary])",
            self.read_source("pages/PatientReportsPage.tsx"),
        )
        self.assertNotIn(
            "(report.score.feedback?.length ? report.score.feedback : [report.summary])",
            self.read_source("pages/PrintableReportPage.tsx"),
        )

    def test_report_preview_keeps_feedback_separate_from_recommendation(self) -> None:
        source = self.read_source("components/ReportPreview.tsx")
        self.assertIn("<h2>康复建议</h2>", source)
        self.assertIn("<h2>动作反馈</h2>", source)
        self.assertIn("const feedback = report.score.feedback.length", source)
        self.assertIn("const rehabRecommendations = buildRehabRecommendations(report).items", source)

    def test_doctor_note_is_not_recommendation_storage(self) -> None:
        source = self.read_source("pages/SessionHistoryPage.tsx")
        notes = self.read_source("utils/doctorNotes.ts")
        self.assertIn("<h3>医生备注</h3>", source)
        self.assertNotIn("医生备注 / 康复建议", source)
        self.assertIn("医生可结合患者实际情况补充专业意见", source)
        self.assertIn("saveDoctorNote(sessionId, note)", source)
        self.assertIn("rehab_doctor_note:${recordId}", notes)
        self.assertIn("note,", notes)

    def test_print_keeps_empty_note_hidden_and_signature_visible(self) -> None:
        source = self.read_source("pages/PrintableReportPage.tsx")
        self.assertIn("{note && (", source)
        self.assertIn("<h2>医生备注</h2>", source)
        self.assertIn("医生签名", source)
        self.assertIn("日期", source)


if __name__ == "__main__":
    unittest.main()
