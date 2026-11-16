from pathlib import Path
import unittest


APP = Path(__file__).resolve().parents[3]
UI = APP / "fatfs_image" / "system" / "ui" / "rehab"
STATE = (UI / "rehab_ui_state.lua").read_text(encoding="utf-8")
PROGRESS = (UI / "page_task_decision_progress.lua").read_text(encoding="utf-8")
RESULT = (UI / "page_task_decision_result.lua").read_text(encoding="utf-8")
BRIDGE = (
    APP / "components" / "rehab_ui" / "src" /
    "rehab_ui_task_decision_lua.c"
).read_text(encoding="utf-8")


class P4DecisionTraceUiTest(unittest.TestCase):
    def test_status_projection_preserves_business_fields_and_adds_trace(self):
        for field in (
            '"state"', '"active"', '"generation"', '"decision_id"',
            '"task_id"', '"task_name"', '"trace_stage"', '"elapsed_sec"',
        ):
            self.assertIn(field, BRIDGE)

    def test_required_stage_copy_is_present(self):
        expected = {
            "PREPARING": "正在启动任务决策…",
            "INPUT_READY": "正在分析环境信息…",
            "CONTEXT_READY": "正在生成候选任务…",
            "MODEL_WAIT": "DeepSeek 正在分析候选任务…",
            "TOOL_CALL": "正在校验任务方案…",
            "VALIDATING": "正在校验任务方案…",
            "COMMITTING": "正在生成最终方案…",
            "SUCCESS": "决策完成",
            "FAILED": "决策失败",
        }
        for stage, text in expected.items():
            self.assertIn(f'{stage} = "{text}"', STATE)

    def test_progress_is_a_presentation_focused_chinese_stage_list(self):
        for text in (
            "正在生成康复任务", "当前阶段：", "决策耗时：",
            "✓ 已加载患者状态", "✓ 已分析环境信息",
            "✓ 已生成候选任务", "✓ 已完成候选任务分析",
            "✓ 已完成任务校验", 'string.format("%ds"',
        ):
            self.assertIn(text, PROGRESS)
        for forbidden in (
            "Decision ID", "Generation：", "PREPARING", "DECIDING",
            "data.state", "view.state", "Session", "不会创建训练",
        ):
            self.assertNotIn(forbidden, PROGRESS)

    def test_completed_stage_counts_are_monotonic_by_trace_order(self):
        expected = {
            "PREPARING": 0,
            "INPUT_READY": 1,
            "CONTEXT_READY": 2,
            "MODEL_WAIT": 3,
            "TOOL_CALL": 4,
            "VALIDATING": 4,
            "COMMITTING": 5,
            "SUCCESS": 5,
        }
        for stage, count in expected.items():
            self.assertIn(f"{stage} = {count}", STATE)
        self.assertIn("math.max(view.completed_count", PROGRESS)

    def test_result_page_remains_compatible(self):
        self.assertIn("AI决策耗时：%ds", RESULT)
        self.assertIn("data.elapsed_sec", RESULT)


if __name__ == "__main__":
    unittest.main()
