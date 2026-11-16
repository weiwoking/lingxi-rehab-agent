from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
APP = ROOT / "application" / "edge_agent"
DECISION = APP / "components" / "rehab_task_decision"
UI = APP / "components" / "rehab_ui"
UI_LUA = APP / "fatfs_image" / "system" / "ui" / "rehab"
INTEGRATION = APP / "components" / "rehab_integration" / "src"
LAUNCHER = (
    ROOT
    / "tools"
    / "Start-VisionAndScanPC"
    / "start_vision_and_scan_services.ps1"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


COORDINATOR = read(DECISION / "src" / "rehab_task_decision_coordinator.c")
SCORING = read(DECISION / "src" / "rehab_task_decision_scoring.c")
PRIVATE_HEADER = read(DECISION / "src" / "rehab_task_decision_priv.h")
SELECTED = read(DECISION / "src" / "rehab_task_decision_selected_task.c")
CONTEXT = read(DECISION / "src" / "rehab_task_decision_context.c")
RULES = read(DECISION / "src" / "rehab_task_decision_rules.c")
HEADER = read(DECISION / "include" / "rehab_task_decision.h")
CAPS = read(
    DECISION / "src" / "rehab_task_decision_coordinator_capabilities.c"
)
CLAW_CAP = read(
    ROOT / "components" / "claw_modules" / "claw_cap" / "src" / "claw_cap.c"
)
SKILL = read(DECISION / "skills" / "rehab-task-decision" / "SKILL.md")
ENV_SKILL = read(
    APP / "components" / "rehab_environment_scan" / "skills"
    / "rehab-environment-scan" / "SKILL.md"
)
INTENT_FIXTURE = json.loads(read(
    DECISION / "test" / "fixtures" / "wechat_task_decision_intents.json"
))
UI_BRIDGE = read(UI / "src" / "rehab_ui_task_decision_lua.c")
CONFIRMED_START = read(
    DECISION / "src" / "rehab_task_decision_confirmed_start.c"
)
CONSOLE = read(
    DECISION / "src" / "rehab_task_decision_coordinator_console.c"
)
UI_STATE = read(UI_LUA / "rehab_ui_state.lua")
UI_CONTROLLER = read(UI_LUA / "esp_claw_rehab_ui.lua")
TRAINING_CAPS = read(INTEGRATION / "rehab_capabilities.c")


TASK_IDS = [
    "OT_CUP_TRANSFER_L1",
    "OT_CUP_DRINK_L2",
    "OT_CUP_DRINK_L3",
    "OT_BALL_TRANSFER_L1",
    "OT_BALL_TRANSFER_L2",
]


def descriptor_blocks(source: str) -> list[tuple[str, str]]:
    return re.findall(
        r'\{\s*\.id = "([^"]+)"(?P<body>.*?)\.execute = [^,]+,\s*\}',
        source,
        re.S,
    )


def enumerate_formal_tools(caller: str) -> set[str]:
    tools: set[str] = set()
    for tool_id, body in descriptor_blocks(CAPS):
        if "CLAW_CAP_FLAG_CALLABLE_BY_LLM" not in body:
            continue
        if caller == "sub" and "CLAW_CAP_FLAG_ROOT_AGENT_ONLY" in body:
            continue
        tools.add(tool_id)
    return tools


class Stage8C1FormalTaskDecisionTest(unittest.TestCase):
    def test_five_candidates_keep_fixed_identity_without_local_winner_selection(self) -> None:
        positions = [RULES.index(f'"{task_id}"') for task_id in TASK_IDS]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("scores[i] > scores[highest]", SCORING)
        self.assertNotIn("winner_ref.not_highest", SCORING)
        self.assertIn('"candidate_count_or_summary"', SCORING)
        self.assertIn('"winner_ref.invalid"', SCORING)

    def test_one_shared_single_flight_coordinator_state_machine(self) -> None:
        for state in (
            "IDLE",
            "PREPARING",
            "DECIDING",
            "VALIDATING",
            "COMMITTING",
            "SELECTED",
            "FAILED",
            "CANCELLED",
        ):
            self.assertIn(
                f"REHAB_TASK_DECISION_COORDINATOR_{state}", HEADER
            )
        self.assertIn("handle->coordinator_status.active", COORDINATOR)
        self.assertIn("handle->coordinator_task != NULL", COORDINATOR)
        self.assertIn("++handle->coordinator_generation", COORDINATOR)
        self.assertIn("decision_id", COORDINATOR)
        self.assertLess(
            COORDINATOR.index("rehab_task_decision_selected_begin"),
            COORDINATOR.index("xTaskCreate(coordinator_worker"),
        )

    def test_scene_is_frozen_by_full_file_sha_and_rechecked(self) -> None:
        self.assertIn(
            '"rehab/latest_successful_scene_result.json"', COORDINATOR
        )
        self.assertIn("bytes_sha256", COORDINATOR)
        load = COORDINATOR[
            COORDINATOR.index("static esp_err_t formal_scene_load") :
            COORDINATOR.index("static esp_err_t formal_scene_is_current")
        ]
        self.assertNotIn("rehab_environment_scan_get_status", load)
        self.assertNotIn("REHAB_ENVIRONMENT_SCAN_STATE_DECISION_READY", load)
        current = COORDINATOR[
            COORDINATOR.index("static esp_err_t formal_scene_is_current") :
            COORDINATOR.index("static cJSON *compact_object")
        ]
        for evidence in (
            "strcmp(sha256, frozen->sha256)",
            "handle->coordinator_generation == coordinator_generation",
        ):
            self.assertIn(evidence, current)

    def test_scene_accepts_multiple_classes_and_locally_gates_invalid_geometry(self) -> None:
        self.assertIn("semantic_group_count", COORDINATOR)
        self.assertIn("geometry_height_valid", COORDINATOR)
        self.assertIn("hard_gates", COORDINATOR)
        self.assertNotIn("required_any", COORDINATOR)
        self.assertNotRegex(COORDINATOR, r"class_(?:white|allow)list")
        self.assertIn("cJSON_ArrayForEach(object, objects)", COORDINATOR)
        self.assertNotIn("object_height_cm", COORDINATOR)

    def test_deepseek_core_contract_is_strict_after_normalization(self) -> None:
        self.assertIn(
            '"rehab_task_decision_output.v4"', PRIVATE_HEADER
        )
        self.assertIn(
            "REHAB_TASK_DECISION_OUTPUT_SCHEMA_VERSION", COORDINATOR
        )
        self.assertNotIn("cJSON_GetArraySize(decision->root) != 8", COORDINATOR)
        self.assertIn("rehab_task_scoring_validate", COORDINATOR)
        self.assertIn("score_breakdown", SCORING)
        self.assertEqual(
            len(re.findall(r"\berr = llm_call\(", COORDINATOR)), 2
        )
        self.assertIn("retry_used = true", COORDINATOR)
        self.assertIn("rehab_task_target_resolve", COORDINATOR)
        self.assertIn("target_required", COORDINATOR)
        self.assertNotIn("target_object_not_found", COORDINATOR)
        self.assertIn("REHAB_TASK_DECISION_LLM_INPUT_MAX (48U * 1024U)", read(
            DECISION / "src" / "rehab_task_decision_priv.h"
        ))
        self.assertIn("REHAB_TASK_DECISION_LLM_OUTPUT_MAX 8192U", read(
            DECISION / "src" / "rehab_task_decision_priv.h"
        ))

    def test_prescription_and_history_inputs_are_bounded(self) -> None:
        self.assertIn('snapshot->status = "ok"', CONTEXT)
        self.assertIn('patient_condition, "raw_text", prescription.text', CONTEXT)
        self.assertNotIn('root, "doctor_advice"', CONTEXT)
        self.assertNotIn("demo_prescription", CONTEXT)
        self.assertIn("load_recent_scores(handle, &recent, 3U)", CONTEXT)
        self.assertIn("recent_performance", CONTEXT)
        self.assertIn("item_frequency", CONTEXT)
        self.assertIn("latest_compensation", CONTEXT)
        self.assertIn(
            "while (position < used && strcmp(ids[position], session_id) > 0)",
            CONTEXT,
        )
        self.assertIn("REHAB_SCORE_STATE_SUCCEEDED", CONTEXT)

    def test_v2_commit_is_atomic_and_failure_restores_current_selection(self) -> None:
        self.assertIn('"rehab_selected_task.v2"', SELECTED)
        self.assertIn('"rehab/selected_task.json.next"', SELECTED)
        self.assertIn('"rehab/selected_task.json.previous"', SELECTED)
        self.assertIn("fflush(file)", SELECTED)
        self.assertIn("fsync(fileno(file))", SELECTED)
        self.assertGreaterEqual(SELECTED.count("validate_file(handle"), 4)
        self.assertIn("rename(final_path, previous)", SELECTED)
        self.assertIn("rename(next, final_path)", SELECTED)
        self.assertIn("rename(previous, final_path)", SELECTED)
        self.assertIn("SELECTED_TASK_ROLLBACK_RESTORED", SELECTED)
        self.assertIn("(void)unlink(final_path)", SELECTED)
        self.assertIn("cJSON_GetArraySize(root) == 14", SELECTED)
        self.assertIn("selected_candidates_valid", SELECTED)

    def test_agent_and_ui_only_call_the_shared_coordinator(self) -> None:
        self.assertIn(
            '"rehab_task_decision_coordinator"', CAPS
        )
        self.assertEqual(CAPS.count(".execute = "), 3)
        self.assertIn("rehab_task_decision_formal_input_get", CAPS)
        self.assertIn("rehab_task_decision_commit", CAPS)
        self.assertIn("rehab_task_decision_status", CAPS)
        for legacy in (
            "rehab_task_decision_scan_start",
            "rehab_task_decision_context_get",
            "rehab_task_decision_rules_get",
            "rehab_task_decision_validate",
            "rehab_task_decision_selected_clear",
        ):
            self.assertNotIn(legacy, SKILL)
        self.assertIn("rehab_task_decision_start_confirmed", UI_BRIDGE)
        self.assertIn("rehab_task_decision_coordinator_get_status", UI_BRIDGE)
        self.assertNotIn("rehab_task_decision_coordinator_start(", UI_BRIDGE)
        self.assertNotIn("capability.call", UI_BRIDGE)

    def test_formal_tools_enumerate_for_root_only(self) -> None:
        blocks = descriptor_blocks(CAPS)
        self.assertEqual(
            {tool_id for tool_id, _ in blocks},
            {
                "rehab_task_decision_formal_input_get",
                "rehab_task_decision_commit",
                "rehab_task_decision_status",
            },
        )
        for _, block in blocks:
            self.assertIn("CLAW_CAP_FLAG_CALLABLE_BY_LLM", block)
            self.assertIn("CLAW_CAP_FLAG_RESTRICTED", block)
            self.assertIn("CLAW_CAP_FLAG_ROOT_AGENT_ONLY", block)
        self.assertEqual(
            enumerate_formal_tools("root"),
            {
                "rehab_task_decision_formal_input_get",
                "rehab_task_decision_commit",
                "rehab_task_decision_status",
            },
        )
        self.assertEqual(enumerate_formal_tools("sub"), set())
        visibility = CLAW_CAP[
            CLAW_CAP.index("static bool claw_cap_is_llm_visible") :
            CLAW_CAP.index("static bool claw_cap_group_id_in_list")
        ]
        self.assertIn("CLAW_CAP_CALLER_SUB_AGENT", visibility)
        self.assertIn("CLAW_CAP_FLAG_ROOT_AGENT_ONLY", visibility)

    def test_formal_tools_separate_machine_package_and_commit(self) -> None:
        formal_input = CAPS[CAPS.index("static esp_err_t formal_input_execute") :
                            CAPS.index("static esp_err_t commit_execute")]
        commit = CAPS[CAPS.index("static esp_err_t commit_execute") :
                      CAPS.index("static claw_cap_descriptor_t")]
        self.assertIn("rehab_task_decision_formal_input_render", formal_input)
        self.assertNotIn("coordinator_get_status", formal_input)
        self.assertIn("rehab_task_decision_formal_commit", commit)
        self.assertIn("complete frozen", CAPS)
        self.assertIn("Retry once only", CAPS)
        self.assertIn("Use only when the user explicitly", CAPS)
        self.assertIn("/sdcard/rehab/", SKILL)
        self.assertIn("latest_successful_scene_result.json", SKILL)
        self.assertIn("不调用 `rehab_environment_scan_status({})`", SKILL)
        self.assertIn("不调用 `rehab_environment_scan_start({})`", SKILL)

    def test_all_production_starts_share_one_confirmed_facade(self) -> None:
        self.assertNotIn("rehab_task_decision_coordinator_start(", HEADER)
        self.assertIn("rehab_task_decision_start_confirmed(", HEADER)
        self.assertEqual(
            CONFIRMED_START.count(
                "rehab_task_decision_coordinator_start_root("
            ),
            2,
        )
        self.assertEqual(
            CONFIRMED_START.count("rehab_task_decision_coordinator_start("),
            2,
        )
        self.assertEqual(
            CONFIRMED_START.count(
                "rehab_task_decision_coordinator_get_status("
            ),
            1,
        )
        for adapter in (UI_BRIDGE, CONSOLE):
            self.assertIn("rehab_task_decision_start_confirmed(", adapter)
            self.assertNotIn("rehab_task_decision_coordinator_start(", adapter)
        self.assertNotIn("rehab_task_decision_start_confirmed(", CAPS)
        self.assertIn("rehab_task_decision_formal_input_render(", CAPS)

    def test_preflight_rejections_do_not_allocate_a_generation(self) -> None:
        start = COORDINATOR[
            COORDINATOR.index("static esp_err_t coordinator_reserve_formal") :
            COORDINATOR.index("static bool session_binding_matches")
        ]
        generation = start.index("++handle->coordinator_generation")
        self.assertLess(start.index("formal_scene_load(handle"), generation)
        self.assertLess(start.index('"scene_unavailable"'), generation)
        self.assertLess(start.index('"scene_invalid"'), generation)
        self.assertIn("created_generation != 0U", CONFIRMED_START)

    def test_wechat_intent_guidance_routes_to_formal_tools(self) -> None:
        self.assertEqual(
            INTENT_FIXTURE["schema_version"],
            "rehab.wechat_tool_choice_fixture.v1",
        )
        decision_tools = {
            "rehab_task_decision_formal_input_get",
            "rehab_task_decision_status",
        }
        scan_tools = {
            "rehab_environment_scan_start",
            "rehab_environment_scan_status",
        }
        for case in INTENT_FIXTURE["cases"]:
            tool = case["expected_tool"]
            skill = SKILL if tool in decision_tools else ENV_SKILL
            self.assertIn(case["message"], skill)
            if tool in decision_tools:
                self.assertNotIn(tool, scan_tools)
        self.assertIn("`rehab_task_decision_formal_input_get` 一次且仅一次", SKILL)
        self.assertIn("`rehab_task_decision_commit`", SKILL)
        self.assertIn("调用 `rehab_task_decision_status({})`，一次且仅一次", SKILL)
        self.assertIn("不调用 `rehab_environment_scan_status({})`", SKILL)
        self.assertIn("不调用 `rehab_environment_scan_start({})`", SKILL)

    def test_decision_does_not_create_session_and_training_reuses_chain(self) -> None:
        for source in (COORDINATOR, CAPS, UI_BRIDGE):
            self.assertNotIn("rehab_training_start(", source)
            self.assertNotIn("rehab_session", source.lower())
            self.assertNotIn("task.json", source)
        selected_body = re.search(
            r"static esp_err_t rehab_training_start_selected_execute"
            r"\(.*?\n\}",
            TRAINING_CAPS,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(selected_body)
        body = selected_body.group(0)
        self.assertIn("rehab_task_decision_selected_get", body)
        self.assertEqual(body.count("rehab_training_start("), 1)
        self.assertIn("selected->task_name, result", body)
        self.assertIn(
            '"rehab_training_start_selected"', TRAINING_CAPS
        )
        self.assertIn(
            '"rehab_training_start_selected"', UI_STATE
        )
        self.assertNotIn("rehab_training_start(", UI_CONTROLLER)

    def test_result_preview_uses_full_rgb565_frame_and_green_bbox(self) -> None:
        self.assertIn("#define TARGET_IMAGE_WIDTH 640", UI_BRIDGE)
        self.assertIn("#define TARGET_IMAGE_HEIGHT 360", UI_BRIDGE)
        self.assertIn('"RGBP"', UI_BRIDGE)
        self.assertIn(
            '"rehab/scans/%s/frames/frame_%06" PRIu32 "/image.rgb565"',
            UI_BRIDGE,
        )
        result_page = read(UI_LUA / "page_task_decision_result.lua")
        self.assertIn("w = 640, h = 360", result_page)
        self.assertIn("border_color = theme.normal", result_page)
        self.assertIn("border_width = 3", result_page)
        self.assertIn("目标图片不可用", result_page)
        self.assertIn("重新决策", result_page)
        self.assertIn("开始训练", result_page)

    def test_launcher_forcibly_restarts_only_exact_port_owners(self) -> None:
        launcher = read(LAUNCHER)
        reset = re.search(
            r"function Reset-TargetPort \{.*?\n\}", launcher, re.DOTALL
        )
        self.assertIsNotNone(reset)
        body = reset.group(0)
        self.assertIn("Get-PortListeners -Port $Port", body)
        self.assertIn("Stop-Process -Id $listenerPid", body)
        self.assertIn("CommandLine=$command", read(LAUNCHER))
        self.assertIn("AddSeconds(8)", body)
        self.assertNotRegex(body, r"Get-Process\s+python|python\*")
        start = launcher.index("function Start-ServiceSafely")
        self.assertLess(
            launcher.index("Reset-TargetPort -Service", start),
            launcher.index("Start-Process -FilePath", start),
        )
        self.assertIn("'--port', '8030'", launcher)


if __name__ == "__main__":
    unittest.main()
