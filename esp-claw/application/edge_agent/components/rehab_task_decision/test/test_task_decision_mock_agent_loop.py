from __future__ import annotations

import json
from pathlib import Path
import unittest


TEST_DIR = Path(__file__).resolve().parent
SKILL = (
    TEST_DIR.parent / "skills" / "rehab-task-decision" / "SKILL.md"
).read_text(encoding="utf-8")
INTENTS = json.loads(
    (TEST_DIR / "fixtures" / "wechat_task_decision_intents.json").read_text(
        encoding="utf-8"
    )
)["cases"]
START_MESSAGES = {
    case["message"]
    for case in INTENTS
    if case["expected_tool"] == "rehab_task_decision_formal_input_get"
}
STATUS_MESSAGES = {
    case["message"]
    for case in INTENTS
    if case["expected_tool"] == "rehab_task_decision_status"
}


class MockAgentLoop:
    """Deterministic policy model for one root-agent user turn only."""

    def __init__(self, tool_results: dict[str, object]) -> None:
        self.tool_results = tool_results
        self.calls: list[str] = []
        self.wait_calls = 0

    def answer(self, message: str) -> str:
        if message in START_MESSAGES:
            self.calls.append("rehab_task_decision_formal_input_get")
            package = self.tool_results["formal_input"]
            assert isinstance(package, dict)
            assert package["schema_version"] == "rehab_task_decision_input.v4"
            commits = list(self.tool_results["commit_results"])
            self.calls.append("rehab_task_decision_commit")
            result = commits.pop(0)
            if not result["accepted"] and result["retry_allowed"]:
                self.calls.append("rehab_task_decision_commit")
                result = commits.pop(0)
            if result["accepted"]:
                return f"已完成正式任务选择：{result['task_name']}"
            return f"任务决策失败：{result['code']}"
        if message in STATUS_MESSAGES:
            self.calls.append("rehab_task_decision_status")
            result = self.tool_results["rehab_task_decision_status"]
            assert result["one_shot_query"] is True
            assert result["should_poll"] is False
            assert result["next_action"] == "reply_immediately"
            return f"当前任务决策状态：{result['state']}"
        raise AssertionError("unexpected intent")


def formal_input() -> dict[str, object]:
    return {
        "schema_version": "rehab_task_decision_input.v4",
        "decision_binding": {"decision_id": "decision-fixed"},
    }


def commit_result(accepted: bool, retry_allowed: bool = False) -> dict[str, object]:
    return {
        "accepted": accepted,
        "retry_allowed": retry_allowed,
        "code": "selected" if accepted else "validation_failed",
        "task_name": "水杯横向取放训练（一级）",
    }


class TaskDecisionMockAgentLoopTest(unittest.TestCase):
    def assert_start_is_one_shot(self, message: str,
                                 results: list[dict[str, object]]) -> str:
        loop = MockAgentLoop({
            "formal_input": formal_input(),
            "commit_results": results,
        })
        text = loop.answer(message)
        self.assertTrue(text)
        self.assertEqual(loop.calls[0], "rehab_task_decision_formal_input_get")
        self.assertEqual(loop.calls.count(
            "rehab_task_decision_formal_input_get"), 1)
        self.assertIn(loop.calls.count("rehab_task_decision_commit"), (1, 2))
        self.assertNotIn("rehab_task_decision_status", loop.calls)
        self.assertFalse(any("scan" in call for call in loop.calls))
        self.assertEqual(loop.wait_calls, 0)
        return text

    def test_all_start_intent_fixtures_input_then_commit_then_final_text(self) -> None:
        self.assertIn("帮我决定一个任务", START_MESSAGES)
        self.assertIn("直接决策", START_MESSAGES)
        for message in START_MESSAGES:
            with self.subTest(message=message):
                self.assertEqual(
                    self.assert_start_is_one_shot(
                        message, [commit_result(True)]),
                    "已完成正式任务选择：水杯横向取放训练（一级）",
                )

    def test_commit_retry_is_bounded_and_does_not_reread_input(self) -> None:
        self.assert_start_is_one_shot(
            "帮我决定一个任务",
            [commit_result(False, True), commit_result(True)],
        )
        self.assert_start_is_one_shot(
            "帮我决定一个任务", [commit_result(False, False)]
        )

    def test_explicit_status_is_one_snapshot_then_final_text(self) -> None:
        for message in STATUS_MESSAGES:
            with self.subTest(message=message):
                loop = MockAgentLoop(
                    {
                        "rehab_task_decision_status": {
                            "state": "SELECTED",
                            "one_shot_query": True,
                            "should_poll": False,
                            "next_action": "reply_immediately",
                        }
                    }
                )
                self.assertIn("SELECTED", loop.answer(message))
                self.assertEqual(loop.calls, ["rehab_task_decision_status"])
                self.assertNotIn("rehab_task_decision_run", loop.calls)
                self.assertEqual(loop.wait_calls, 0)

    def test_skill_has_mutually_exclusive_one_shot_flows(self) -> None:
        self.assertIn("## START INTENT", SKILL)
        self.assertIn("一次且仅一次", SKILL)
        self.assertIn("不得再次 input_get", SKILL)
        self.assertIn("不得调用 status", SKILL)
        self.assertIn("retry_allowed=true", SKILL)
        self.assertIn("第二次失败", SKILL)
        self.assertIn("## STATUS INTENT", SKILL)
        self.assertIn("不得调用 `rehab_task_decision_run({})`", SKILL)
        self.assertIn("当前 turn 内不得第二次调用 status", SKILL)
        self.assertNotIn("随后最多一次 status", SKILL)
        self.assertNotIn("可再检查一次 status", SKILL)


if __name__ == "__main__":
    unittest.main()
