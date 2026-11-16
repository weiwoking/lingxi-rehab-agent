from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
COMPONENT = (
    ROOT / "application/edge_agent/components/rehab_task_decision"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


COORDINATOR = read(COMPONENT / "src/rehab_task_decision_coordinator.c")
CAPS = read(COMPONENT / "src/rehab_task_decision_coordinator_capabilities.c")
FACADE = read(COMPONENT / "src/rehab_task_decision_confirmed_start.c")
EVENT = read(COMPONENT / "src/rehab_task_decision_event.c")
PRIVATE = read(COMPONENT / "src/rehab_task_decision_priv.h")
SKILL = read(COMPONENT / "skills/rehab-task-decision/SKILL.md")
CMAKE = read(COMPONENT / "CMakeLists.txt")
CLAW_CAP = read(
    ROOT / "components/claw_modules/claw_cap/src/claw_cap.c"
)
POLICY = read(COMPONENT / "src/rehab_task_decision_policy.c")
SCORING = read(COMPONENT / "src/rehab_task_decision_scoring.c")


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


class PhaseARootAgentMigrationTest(unittest.TestCase):
    def test_root_tools_replace_formal_run_without_fallback(self) -> None:
        descriptor_ids = re.findall(r'\.id = "([^"]+)"', CAPS)
        self.assertEqual(
            descriptor_ids,
            [
                "rehab_task_decision_formal_input_get",
                "rehab_task_decision_commit",
                "rehab_task_decision_status",
            ],
        )
        self.assertNotIn('"rehab_task_decision_run"', CAPS)
        self.assertNotIn("rehab_task_decision_run", EVENT)
        self.assertIn("rehab_task_decision_formal_input_get", EVENT)
        self.assertIn("rehab_task_decision_commit", EVENT)
        for name in descriptor_ids:
            block = CAPS[CAPS.index(f'.id = "{name}"') :]
            block = block[: block.index(".execute =")]
            self.assertIn("CLAW_CAP_FLAG_ROOT_AGENT_ONLY", block)

    def test_formal_input_is_one_complete_raw_v4_tool_result(self) -> None:
        render = function_body(
            COORDINATOR, "formal_input_render"
        )
        execute = function_body(CAPS, "formal_input_execute")
        self.assertIn("formal_scene_load(handle, &package->scene)", render)
        self.assertEqual(render.count("build_llm_input("), 1)
        self.assertIn("memcpy(output, package->input, input_length + 1U)", render)
        self.assertNotIn("truncate", render.lower())
        self.assertNotIn("compress", render.lower())
        self.assertNotIn("file_path", render)
        self.assertIn("rehab_task_decision_formal_input_render", execute)
        self.assertNotIn("status_json", execute)
        self.assertIn('"rehab_task_decision_input.v4"', COORDINATOR)

    def test_request_and_machine_bindings_are_frozen(self) -> None:
        package = PRIVATE[PRIVATE.index("coordinator_formal_package") - 200 :]
        for token in (
            "coordinator_formal_package",
            "coordinator_root_request_id",
            "coordinator_generation",
            "coordinator_entry_source",
        ):
            self.assertIn(token, package)
        render = function_body(
            COORDINATOR, "formal_input_render"
        )
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
            self.assertIn(token, render)
        self.assertIn("package->request_id != request_id", commit)
        self.assertIn("session_binding_matches", commit)
        self.assertIn("formal_scene_is_current", COORDINATOR)

    def test_migrated_and_legacy_replay_share_one_machine_tail(self) -> None:
        worker = function_body(COORDINATOR, "coordinator_worker")
        commit = function_body(
            COORDINATOR, "rehab_task_decision_formal_commit"
        )
        tail = function_body(COORDINATOR, "commit_validated_decision")
        self.assertEqual(COORDINATOR.count("commit_validated_decision("), 3)
        self.assertIn("commit_validated_decision(", worker)
        self.assertIn("commit_validated_decision(", commit)
        for invariant in (
            "formal_scene_is_current",
            "build_selected_v2",
            "selected_target_binding_valid",
            "rehab_task_decision_selected_commit_v2",
            "rehab_task_decision_selected_get",
            "rehab_task_decision_details_write_atomic",
            "REHAB_TASK_DECISION_COORDINATOR_SELECTED",
        ):
            self.assertEqual(tail.count(invariant), 1)

    def test_commit_reuses_output_validator_and_has_one_bounded_retry(self) -> None:
        commit = function_body(
            COORDINATOR, "rehab_task_decision_formal_commit"
        )
        error = function_body(CAPS, "tool_error_json")
        self.assertEqual(commit.count("validate_output("), 1)
        self.assertIn("rehab_task_scoring_validate", COORDINATOR)
        self.assertIn("!retry_used", commit)
        self.assertIn("coordinator_status.retry_used = true", commit)
        self.assertIn("validation_retry_exhausted", commit)
        self.assertIn("formal_package_detach", commit)
        for field in ("code", "field", "detail", "retry_allowed"):
            self.assertIn(f'"{field}"', error)
        self.assertNotIn("build_repair_prompt", commit)
        self.assertNotIn("llm_call", commit)

    def test_ui_uses_programmatic_root_without_out_message(self) -> None:
        start_root = function_body(
            COORDINATOR, "rehab_task_decision_coordinator_start_root"
        )
        waiter = function_body(
            COORDINATOR, "formal_root_completion_waiter"
        )
        facade = function_body(FACADE, "rehab_task_decision_start_confirmed")
        self.assertIn("rehab_task_decision_coordinator_start_root", facade)
        self.assertIn("REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI", facade)
        self.assertIn("claw_agent_mgr_submit_root_text", start_root)
        self.assertIn(
            "REHAB_TASK_DECISION_FORMAL_UI_SESSION_ID, 0U", start_root
        )
        self.assertNotIn("CLAW_CORE_REQUEST_FLAG_PUBLISH_OUT_MESSAGE", start_root)
        self.assertIn("claw_agent_mgr_receive_root_for", waiter)
        self.assertIn("root_completed_without_commit", waiter)
        self.assertIn("coordinator_fail", waiter)
        self.assertIn("claw_manager", CMAKE)

    def test_formal_routes_do_not_reach_dedicated_2241(self) -> None:
        formal_functions = "\n".join(
            function_body(COORDINATOR, name)
            for name in (
                "coordinator_reserve_formal",
                "formal_input_render",
                "rehab_task_decision_formal_commit",
                "rehab_task_decision_coordinator_start_root",
                "formal_root_completion_waiter",
            )
        )
        for forbidden in (
            "llm_call",
            "build_repair_prompt",
            "coordinator_llm_core",
            "0x8C1",
            "claw_core_create",
            "claw_core_submit",
        ):
            self.assertNotIn(forbidden, formal_functions)
        self.assertIn("0x8C1", COORDINATOR)
        self.assertIn("coordinator_llm_core", COORDINATOR)
        initialize = function_body(
            COORDINATOR, "rehab_task_decision_coordinator_initialize"
        )
        self.assertNotIn("claw_core_create", initialize)
        self.assertNotIn("claw_core_start", initialize)
        self.assertIn("legacy_2241=disabled", initialize)

    def test_generic_bridge_is_64k_and_formal_bound_is_48k(self) -> None:
        self.assertIn(
            "#define CLAW_CAP_CORE_OUTPUT_SIZE (64 * 1024)", CLAW_CAP
        )
        self.assertIn(
            "REHAB_TASK_DECISION_LLM_INPUT_MAX (48U * 1024U)", PRIVATE
        )
        render = function_body(
            COORDINATOR, "formal_input_render"
        )
        self.assertIn("output_size", render)
        self.assertIn("formal_input_bridge_too_small", render)

    def test_business_authority_and_fixed_candidate_identity_remain(self) -> None:
        task_ids = (
            "OT_CUP_TRANSFER_L1",
            "OT_CUP_DRINK_L2",
            "OT_CUP_DRINK_L3",
            "OT_BALL_TRANSFER_L1",
            "OT_BALL_TRANSFER_L2",
        )
        positions = [POLICY.index(f'"{task_id}"') for task_id in task_ids]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("winner_ref.not_highest", SCORING)
        self.assertNotIn("scores[i] > scores[highest]", SCORING)
        for owner in (
            "difficulty",
            "advice",
            "compensation",
            "frequency",
            "perception",
            "final_raw",
            "winner_ref",
        ):
            self.assertIn(owner, SKILL)
        self.assertIn("不得再次 input_get", SKILL)
        self.assertIn("memory", SKILL)
        self.assertIn("不得覆盖", SKILL)


if __name__ == "__main__":
    unittest.main()
