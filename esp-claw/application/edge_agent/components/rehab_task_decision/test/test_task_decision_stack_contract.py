from pathlib import Path
import re
import unittest


COMPONENT = Path(__file__).resolve().parents[1]
COMPONENTS = COMPONENT.parent
REPO = COMPONENT.parents[3]
HEADER = (COMPONENT / "include" / "rehab_task_decision.h").read_text(
    encoding="utf-8"
)
FACADE = (COMPONENT / "src" / "rehab_task_decision_confirmed_start.c").read_text(
    encoding="utf-8"
)
COORDINATOR = (COMPONENT / "src" / "rehab_task_decision_coordinator.c").read_text(
    encoding="utf-8"
)
LUA = (COMPONENTS / "rehab_ui" / "src" / "rehab_ui_task_decision_lua.c").read_text(
    encoding="utf-8"
)
CAPABILITY = (
    COMPONENT / "src" / "rehab_task_decision_coordinator_capabilities.c"
).read_text(encoding="utf-8")
CONSOLE = (
    COMPONENT / "src" / "rehab_task_decision_coordinator_console.c"
).read_text(encoding="utf-8")
CAP_LUA_ASYNC = (
    REPO
    / "components"
    / "claw_capabilities"
    / "cap_lua"
    / "src"
    / "cap_lua_async.c"
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
    raise AssertionError(f"unterminated function {name}")


class TaskDecisionStackContractTest(unittest.TestCase):
    def test_result_is_compact_and_does_not_embed_full_status(self):
        end = HEADER.index("} rehab_task_decision_start_result_t;")
        start = HEADER.rfind("typedef struct {", 0, end)
        result = HEADER[start:end]
        self.assertNotIn("status_snapshot", result)
        self.assertNotIn("coordinator_status", result)
        size_probe = (COMPONENT / "test" / "test_stack_size_contract.c").read_text(
            encoding="utf-8"
        )
        self.assertIn("sizeof(rehab_task_decision_start_result_t) <= 320U", size_probe)

    def test_facade_has_no_full_status_stack_local(self):
        body = function_body(FACADE, "rehab_task_decision_start_confirmed")
        self.assertNotRegex(
            body, r"rehab_task_decision_coordinator_status_t\s+[A-Za-z_]"
        )
        self.assertIn("calloc(1U, sizeof(*status))", body)
        self.assertIn("free(status);", body)
        self.assertEqual(body.count("rehab_task_decision_coordinator_start("), 1)
        self.assertEqual(body.count("rehab_task_decision_coordinator_get_status("), 1)

    def test_lua_start_uses_heap_status_and_frees_every_return_path(self):
        body = function_body(LUA, "lua_task_decision_start")
        self.assertNotRegex(
            body, r"rehab_task_decision_coordinator_status_t\s+[A-Za-z_]"
        )
        self.assertNotRegex(body, r"\bstatic\s+.*\b(result|status)\b")
        allocation = body.index("heap_caps_calloc(")
        start = body.index("rehab_task_decision_start_confirmed(")
        release = body.index("heap_caps_free(status)")
        returned = body.rindex("return 1;")
        self.assertLess(allocation, start)
        self.assertLess(start, release)
        self.assertLess(release, returned)
        self.assertIn("status == NULL ? ESP_ERR_NO_MEM", body)

    def test_start_adapters_request_compact_result_only(self):
        body = function_body(CONSOLE, "coordinator_console_handler")
        start_call = body.index("rehab_task_decision_start_confirmed(")
        call_end = body.index(");", start_call)
        self.assertIn("NULL", body[start_call:call_end])
        input_body = function_body(CAPABILITY, "formal_input_execute")
        self.assertIn("rehab_task_decision_formal_input_render(", input_body)
        self.assertNotIn("coordinator_status_t", input_body)

    def test_coordinator_start_large_scratch_is_heap_backed(self):
        body = function_body(COORDINATOR, "coordinator_reserve_formal")
        self.assertIn("formal_scene_t *preflight_scene = calloc(", body)
        self.assertIn(
            "rehab_task_decision_selected_snapshot_t *previous = calloc(", body
        )
        self.assertNotIn("formal_scene_t preflight_scene", body)
        self.assertNotIn("selected_snapshot_t previous", body)

    def test_cap_lua_async_stack_is_not_expanded(self):
        self.assertRegex(CAP_LUA_ASYNC, r"\.stack_size\s*=\s*12\s*\*\s*1024")


if __name__ == "__main__":
    unittest.main()
