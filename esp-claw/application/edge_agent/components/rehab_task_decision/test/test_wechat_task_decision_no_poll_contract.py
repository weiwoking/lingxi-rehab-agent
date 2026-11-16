from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[5]
SOURCE = (
    ROOT
    / "application/edge_agent/components/rehab_task_decision/src/"
    "rehab_task_decision_coordinator_capabilities.c"
).read_text(encoding="utf-8")
CORE_LOOP = (
    ROOT / "components/claw_modules/claw_core/src/claw_core_agent_loop.c"
).read_text(encoding="utf-8")
CAP_HEADER = (
    ROOT / "components/claw_modules/claw_cap/include/claw_cap.h"
).read_text(encoding="utf-8")


def function_body(source: str, name: str) -> str:
    start = source.index(name)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace : index + 1]
    raise AssertionError(f"unterminated function: {name}")


class WechatNoPollContractTest(unittest.TestCase):
    def test_formal_start_returns_atomic_input_and_commit_result(self) -> None:
        input_body = function_body(SOURCE, "formal_input_execute")
        commit_body = function_body(SOURCE, "commit_execute")
        self.assertIn("rehab_task_decision_formal_input_render", input_body)
        self.assertNotIn("coordinator_get_status", input_body)
        self.assertIn("rehab_task_decision_formal_commit", commit_body)
        self.assertIn('status_json(&status, true, "selected")', commit_body)

    def test_commit_error_is_bounded_and_exposes_retry_policy(self) -> None:
        body = function_body(SOURCE, "tool_error_json")
        for field in ("code", "field", "detail", "retry_allowed"):
            self.assertIn(f'"{field}"', body)

    def test_status_result_is_explicitly_one_shot(self) -> None:
        body = function_body(SOURCE, "status_json")
        self.assertIn('"one_shot_query", true', body)
        self.assertIn('"should_poll", false', body)
        self.assertIn('"next_action", "reply_immediately"', body)

    def test_no_generic_runtime_stop_hook_is_claimed(self) -> None:
        context = CAP_HEADER[
            CAP_HEADER.index("typedef struct {\n    uint32_t request_id") :
            CAP_HEADER.index("} claw_cap_call_context_t;")
        ]
        self.assertNotIn("stop_after_tool", context)
        self.assertNotIn("final_response", context)
        self.assertIn("iteration++", CORE_LOOP)
        self.assertIn("core->max_tool_iterations", CORE_LOOP)


if __name__ == "__main__":
    unittest.main()
