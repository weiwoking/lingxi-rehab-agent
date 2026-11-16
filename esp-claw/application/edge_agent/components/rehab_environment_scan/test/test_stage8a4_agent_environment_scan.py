from pathlib import Path
import re
import unittest


EDGE = Path(__file__).resolve().parents[3]
COMPONENT = EDGE / "components" / "rehab_environment_scan"
CAPABILITY = (COMPONENT / "src" / "rehab_environment_scan_capabilities.c").read_text(
    encoding="utf-8"
)
HEADER = (COMPONENT / "include" / "rehab_environment_scan.h").read_text(
    encoding="utf-8"
)
COORDINATOR = (COMPONENT / "src" / "rehab_environment_scan.c").read_text(
    encoding="utf-8"
)
CMAKE = (COMPONENT / "CMakeLists.txt").read_text(encoding="utf-8")
SKILL = (
    COMPONENT / "skills" / "rehab-environment-scan" / "SKILL.md"
).read_text(encoding="utf-8")
MAIN = (EDGE / "main" / "main.c").read_text(encoding="utf-8")
UI_BRIDGE = (
    EDGE / "components" / "rehab_ui" / "src" / "rehab_ui_environment_scan_lua.c"
).read_text(encoding="utf-8")


def function_body(source: str, name: str) -> str:
    match = re.search(
        rf"static esp_err_t {re.escape(name)}\([^)]*\)(.*?)\n\}}",
        source,
        re.S,
    )
    if match is None:
        raise AssertionError(f"function {name} not found")
    return match.group(1)


def void_function_body(source: str, name: str) -> str:
    match = re.search(
        rf"static void {re.escape(name)}\([^)]*\)(.*?)\n\}}",
        source,
        re.S,
    )
    if match is None:
        raise AssertionError(f"function {name} not found")
    return match.group(1)


class Stage8A4EnvironmentScanCapabilityTests(unittest.TestCase):
    def test_formal_capability_names_and_empty_inputs(self):
        for name in (
            "rehab_environment_scan_start",
            "rehab_environment_scan_status",
            "rehab_environment_scan_cancel",
        ):
            self.assertEqual(CAPABILITY.count(f'.id = "{name}"'), 1)
            self.assertEqual(CAPABILITY.count(f'.name = "{name}"'), 1)
        self.assertEqual(
            CAPABILITY.count(
                r'{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}'
            ),
            3,
        )
        self.assertNotIn('"scan_id":{"type"', CAPABILITY)

    def test_start_status_cancel_each_call_the_same_coordinator_once(self):
        expected = {
            "start_execute": "rehab_environment_scan_start(",
            "status_execute": "rehab_environment_scan_get_status(",
            "cancel_execute": "rehab_environment_scan_cancel(",
        }
        for execute, call in expected.items():
            body = function_body(CAPABILITY, execute)
            self.assertEqual(body.count(call), 1)
            self.assertIn("s_environment_scan", body)

    def test_start_is_async_and_busy_reuses_existing_snapshot(self):
        body = function_body(CAPABILITY, "start_execute")
        self.assertIn('busy ? "already_running" : "started"', body)
        self.assertIn("err == ESP_OK && !busy", body)
        self.assertNotIn("while", body)
        self.assertNotIn("vTaskDelay", body)
        self.assertIn("xTaskCreate(", COORDINATOR)
        worker = void_function_body(COORDINATOR, "environment_worker")
        self.assertLess(
            worker.index("signal_worker_started(handle, ESP_OK)"),
            worker.index("while (true)"),
        )

    def test_snapshot_contract_exposes_real_progress(self):
        for field in (
            "ok",
            "accepted",
            "busy",
            "state",
            "active",
            "scan_id",
            "warmup_done",
            "warmup_total",
            "directions_done",
            "directions_total",
            "upload_state",
            "reason",
        ):
            self.assertIn(f'"{field}"', CAPABILITY)
        self.assertIn("rehab_environment_scan_state_name(status->state)", CAPABILITY)
        self.assertIn("rehab_scan_transfer_state_name(status->upload_state)", CAPABILITY)

    def test_only_coordinator_api_is_used(self):
        for forbidden in (
            "rehab_car_control_",
            "rehab_scan_capture_start",
            "rehab_scan_capture_abort",
            "rehab_scan_transfer_start",
            "rehab_scan_transfer_abort",
            "dev_camera",
            "rehab_tof",
        ):
            self.assertNotIn(forbidden, CAPABILITY)
        self.assertNotIn("selected_task", CAPABILITY)
        self.assertNotIn("latest_successful_scene_result", CAPABILITY)

    def test_single_main_owned_handle_reaches_all_three_entries(self):
        self.assertEqual(MAIN.count("rehab_environment_scan_create("), 1)
        for binding in (
            "rehab_environment_scan_console_register(\n                s_environment_scan)",
            "rehab_environment_scan_register_capabilities(\n                    s_environment_scan)",
            "rehab_ui_start(\n"
            "            s_person_follow, s_environment_scan, s_task_decision)",
        ):
            self.assertIn(binding, MAIN)
        self.assertNotIn("rehab_environment_scan_create", UI_BRIDGE)
        self.assertIn("static rehab_environment_scan_handle_t s_environment_scan", UI_BRIDGE)
        self.assertIn("s_environment_scan = environment_scan", UI_BRIDGE)

    def test_component_registers_capability_dependencies_and_api(self):
        self.assertIn('"src/rehab_environment_scan_capabilities.c"', CMAKE)
        self.assertIn("claw_cap", CMAKE)
        self.assertIn("json", CMAKE)
        self.assertIn("rehab_environment_scan_register_capabilities(", HEADER)
        self.assertIn('.group_id = "rehab_environment_scan"', CAPABILITY)

    def test_skill_routes_intents_without_phrase_matcher_or_task_decision(self):
        for capability in (
            "rehab_environment_scan_start({})",
            "rehab_environment_scan_status({})",
            "rehab_environment_scan_cancel({})",
        ):
            self.assertIn(capability, SKILL)
        for phrase in (
            "扫描环境",
            "开始环境扫描",
            "帮我扫描周围",
            "环境扫描到哪了",
            "扫描完成了吗",
            "停止扫描",
            "取消环境扫描",
        ):
            self.assertIn(phrase, SKILL)
        self.assertIn("不要再次调用 start", SKILL)
        self.assertIn("无需用户提供 scan_id", SKILL)
        self.assertIn("不要把“扫描环境”路由到任务决策", SKILL)
        self.assertNotIn("rehab_task_decision_", SKILL)

    def test_skill_preserves_stage_boundary_and_selected_task(self):
        self.assertIn("Stage 8B", SKILL)
        self.assertIn("/sdcard/rehab/selected_task.json", SKILL)
        self.assertIn("不得创建 `latest_successful_scene_result`", SKILL)
        self.assertIn("不得宣称已经识别出", SKILL)


if __name__ == "__main__":
    unittest.main()
