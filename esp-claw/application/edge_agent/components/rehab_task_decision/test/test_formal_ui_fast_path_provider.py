from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
COMPONENT = ROOT / "application/edge_agent/components/rehab_task_decision"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*\([^;]*?\)\s*\{{", source, re.S)
    if match is None:
        raise AssertionError(f"function not found: {name}")
    brace = source.index("{", match.start())
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace : index + 1]
    raise AssertionError(f"unterminated function: {name}")


CAPS = read(COMPONENT / "src/rehab_task_decision_coordinator_capabilities.c")
COORDINATOR = read(COMPONENT / "src/rehab_task_decision_coordinator.c")
PRIVATE = read(COMPONENT / "src/rehab_task_decision_priv.h")
HEADER = read(COMPONENT / "include/rehab_task_decision.h")
REHAB_CMAKE = read(COMPONENT / "CMakeLists.txt")
SKILL = read(COMPONENT / "skills/rehab-task-decision/SKILL.md")
APP_CLAW = read(ROOT / "components/common/app_claw/app_claw.c")
APP_CMAKE = read(ROOT / "components/common/app_claw/CMakeLists.txt")
CAP_SKILL = read(
    ROOT / "components/claw_capabilities/cap_skill_mgr/src/cap_skill_mgr.c"
)
CLAW_CAP = read(ROOT / "components/claw_modules/claw_cap/src/claw_cap.c")
CLAW_MANAGER = read(
    ROOT / "components/claw_modules/claw_manager/src/claw_agent_mgr.c"
)


class FormalUiFastPathProviderTest(unittest.TestCase):
    def test_existing_static_provider_idiom_is_used(self) -> None:
        provider = CAPS[CAPS.index("rehab_task_decision_formal_fast_context_provider") :]
        self.assertIn(".collect = formal_fast_context_collect", provider)
        self.assertIn(".user_ctx = NULL", provider)
        self.assertIn(
            "CLAW_CORE_CONTEXT_PROVIDER_FLAG_REQUEST_START_ONLY", provider
        )
        providers = APP_CLAW[
            APP_CLAW.index("claw_core_context_provider_t base_providers[5]") :
            APP_CLAW.index("const char *root_agent_id")
        ]
        self.assertIn("claw_memory_session_history_provider", providers)
        self.assertIn("claw_skill_skills_list_provider", providers)
        self.assertIn(
            "rehab_task_decision_formal_fast_context_provider_get", providers
        )
        self.assertIn(".base_context_providers = base_providers", APP_CLAW)
        self.assertIn("__attribute__((weak))", APP_CLAW)
        self.assertNotIn("rehab_task_decision", APP_CMAKE)
        self.assertNotIn("app_claw", REHAB_CMAKE)

    def test_provider_only_matches_reserved_local_ui_request(self) -> None:
        collect = function_body(CAPS, "formal_fast_context_collect")
        validate = function_body(
            COORDINATOR,
            "rehab_task_decision_formal_fast_request_validate",
        )
        self.assertIn("REHAB_TASK_DECISION_FORMAL_UI_SESSION_ID", collect)
        self.assertIn("return ESP_ERR_NOT_FOUND", collect)
        self.assertIn("request->flags != 0U", collect)
        self.assertIn("request->user_text == NULL", collect)
        self.assertIn("REHAB_TASK_DECISION_FORMAL_ROOT_REQUEST_TEXT", collect)
        self.assertIn("request->request_id == 0U", collect)
        self.assertIn("coordinator_status.active", validate)
        self.assertIn("REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI", validate)
        self.assertIn("coordinator_formal_package == NULL", validate)
        self.assertIn("coordinator_root_request_id == 0U", validate)
        self.assertIn("coordinator_root_request_id == request_id", validate)
        self.assertIn(
            '#define REHAB_TASK_DECISION_FORMAL_UI_SESSION_ID "rehab-formal-ui"',
            HEADER,
        )

    def test_programmatic_activation_reuses_exact_skill_capability(self) -> None:
        collect = function_body(CAPS, "formal_fast_context_collect")
        activate = function_body(CAP_SKILL, "cap_skill_activate_execute")
        self.assertIn('claw_cap_call("activate_skill"', collect)
        self.assertIn('"{\\"skill_id\\":\\"rehab-task-decision\\"}"', collect)
        self.assertIn("claw_skill_read_document", activate)
        self.assertIn("claw_skill_activate_for_session", activate)
        self.assertIn("claw_cap_restore_session_skill_groups", activate)
        self.assertIn("skill, FORMAL_FAST_INPUT_PREFIX", collect)
        self.assertGreater(len(SKILL), 1000)

    def test_fast_and_tool_paths_share_one_freeze_renderer(self) -> None:
        common = function_body(COORDINATOR, "formal_input_render")
        tool = function_body(COORDINATOR, "rehab_task_decision_formal_input_render")
        fast = function_body(
            COORDINATOR, "rehab_task_decision_formal_fast_input_render"
        )
        collect = function_body(CAPS, "formal_fast_context_collect")
        self.assertIn("formal_input_render", tool)
        self.assertIn("out_error, false", tool)
        self.assertIn("formal_input_render", fast)
        self.assertIn("out_error, true", fast)
        self.assertIn("rehab_task_decision_formal_fast_input_render", collect)
        self.assertEqual(common.count("build_llm_input("), 1)
        self.assertEqual(common.count("formal_scene_load("), 1)
        self.assertIn("memcpy(output, package->input, input_length + 1U)", common)
        self.assertIn("formal_input_bridge_too_small", common)
        self.assertNotIn("truncate", collect.lower())
        self.assertNotIn("compress", collect.lower())

    def test_request_generation_scene_and_sha_binding_remain_exact(self) -> None:
        common = function_body(COORDINATOR, "formal_input_render")
        commit = function_body(
            COORDINATOR, "rehab_task_decision_formal_commit"
        )
        for token in (
            "package->request_id = request_id",
            "package->generation = generation",
            "package->entry_source = source",
            "package->session_id",
            "package->scene.scan_id",
            "package->scene.sha256",
        ):
            self.assertIn(token, common)
        self.assertIn("package->request_id != request_id", commit)
        self.assertIn("session_binding_matches", commit)
        self.assertIn("commit_validated_decision", commit)
        machine_tail = function_body(
            COORDINATOR, "commit_validated_decision"
        )
        self.assertIn("formal_scene_is_current", machine_tail)

    def test_first_inference_keeps_history_memory_and_tools(self) -> None:
        start = function_body(
            COORDINATOR, "rehab_task_decision_coordinator_start_root"
        )
        self.assertNotIn('claw_cap_call("activate_skill"', start)
        self.assertNotIn("rehab_task_decision_formal_input_get exactly", start)
        self.assertIn("REHAB_TASK_DECISION_FORMAL_ROOT_REQUEST_TEXT", start)
        self.assertIn("claw_memory_profile_provider", APP_CLAW)
        self.assertIn("claw_memory_long_term", APP_CLAW)
        self.assertIn("claw_memory_session_history_provider", APP_CLAW)
        self.assertIn("claw_skill_skills_list_provider", APP_CLAW)
        self.assertIn("claw_cap_root_agent_tools_provider", CLAW_MANAGER)
        self.assertIn("claw_cap_restore_session_skill_groups", CLAW_CAP)

    def test_no_generic_provider_framework_or_2241_fallback(self) -> None:
        combined = APP_CLAW + HEADER + CAPS
        for token in (
            "extra_provider",
            "extra_providers",
            "provider_registry",
            "register_extra_provider",
        ):
            self.assertNotIn(token, combined)
        collect = function_body(CAPS, "formal_fast_context_collect")
        for forbidden in (
            "claw_core_create",
            "claw_core_submit",
            "coordinator_llm_core",
            "0x8C1",
            "llm_call",
        ):
            self.assertNotIn(forbidden, collect)
        self.assertIn(
            "#define CLAW_CAP_CORE_OUTPUT_SIZE (64 * 1024)", CLAW_CAP
        )
        self.assertIn(
            "REHAB_TASK_DECISION_LLM_INPUT_MAX (48U * 1024U)", PRIVATE
        )


if __name__ == "__main__":
    unittest.main()
