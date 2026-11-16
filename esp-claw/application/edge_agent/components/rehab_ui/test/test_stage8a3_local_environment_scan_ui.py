from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
APP = ROOT / "application" / "edge_agent"
UI = APP / "fatfs_image" / "system" / "ui" / "rehab"
REHAB_UI = APP / "components" / "rehab_ui"
ENV_SCAN = APP / "components" / "rehab_environment_scan"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class Stage8A3LocalEnvironmentScanUiTest(unittest.TestCase):
    def test_home_entry_and_runtime_page_are_present(self) -> None:
        home = read(UI / "page_home.lua")
        runtime = read(UI / "page_environment_scan.lua")
        controller = read(UI / "esp_claw_rehab_ui.lua")

        self.assertIn('text = "扫描环境"', home)
        self.assertIn("app.open_environment_scan()", home)
        self.assertIn('text = "停止扫描"', runtime)
        self.assertIn('scan.can_decide and "决策任务" or "返回首页"', runtime)
        self.assertIn("page_environment_scan", controller)

    def test_local_module_calls_the_existing_coordinator_api(self) -> None:
        bridge = read(
            REHAB_UI / "src" / "rehab_ui_environment_scan_lua.c"
        )
        for api in (
            "rehab_environment_scan_start",
            "rehab_environment_scan_get_status",
            "rehab_environment_scan_cancel",
        ):
            self.assertEqual(bridge.count(api), 1)
        self.assertNotIn("rehab_environment_scan_create", bridge)
        self.assertNotIn("rehab_environment_scan_console_register", bridge)

    def test_console_and_ui_receive_the_same_handle(self) -> None:
        main = read(APP / "main" / "main.c")
        self.assertRegex(
            main,
            r"rehab_environment_scan_console_register\(\s*"
            r"s_environment_scan\)",
        )
        self.assertRegex(
            main,
            r"rehab_ui_start\(\s*s_person_follow,\s*"
            r"s_environment_scan,\s*s_task_decision\)",
        )
        self.assertEqual(
            main.count("rehab_environment_scan_create("),
            1,
            "only one environment coordinator may be created",
        )

    def test_active_entry_does_not_submit_a_second_start(self) -> None:
        controller = read(UI / "esp_claw_rehab_ui.lua")
        match = re.search(
            r"function app\.open_environment_scan\(\)(.*?)\nend",
            controller,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group(1)
        self.assertIn("is_environment_scan_active", body)
        self.assertIn('queue_action("start_environment_scan")', body)
        self.assertLess(
            body.index("is_environment_scan_active"),
            body.index('queue_action("start_environment_scan")'),
        )

    def test_state_mapping_covers_coordinator_states(self) -> None:
        state = read(UI / "rehab_ui_state.lua")
        for name in (
            "IDLE",
            "WARMING_CAMERA",
            "CAPTURING",
            "WRITING",
            "ROTATING",
            "STOPPING",
            "SETTLING",
            "COMMITTING_SCAN",
            "UPLOADING",
            "ANALYZING",
            "DECISION_READY",
            "FAILED",
            "CANCELLING",
            "CANCELLED",
        ):
            self.assertIn(f'"{name}"', state)
        for text in (
            "准备中",
            "相机预热",
            "扫描中",
            "正在上传环境数据",
            "正在分析环境",
            "正在分析环境，请稍候",
            "环境分析完成",
            "环境分析完成，可以进行任务决策",
            "环境分析失败",
            "已取消",
        ):
            self.assertIn(text, state)
        for forbidden in ("上传到电脑", "发送到电脑", "PC端"):
            self.assertNotIn(forbidden, state)

    def test_polling_and_cancel_use_only_the_local_module(self) -> None:
        state = read(UI / "rehab_ui_state.lua")
        self.assertIn("ENVIRONMENT_SCAN_ACTIVE_PERIOD_MS = 500", state)
        self.assertIn("ENVIRONMENT_SCAN_IDLE_PERIOD_MS = 1000", state)
        self.assertIn('call_environment_scan("status")', state)
        self.assertIn('call_environment_scan("start")', state)
        self.assertIn('call_environment_scan("cancel")', state)
        self.assertNotIn('"environment_scan_status"', state)
        self.assertNotIn('"environment_scan_start"', state)
        self.assertNotIn('"environment_scan_cancel"', state)

    def test_stage8a_motion_contract_is_unchanged(self) -> None:
        header = read(ENV_SCAN / "include" / "rehab_environment_scan.h")
        source = read(ENV_SCAN / "src" / "rehab_environment_scan.c")
        for declaration in (
            "#define REHAB_ENVIRONMENT_SCAN_DIRECTIONS        11U",
            "#define REHAB_ENVIRONMENT_SCAN_WARMUP_FRAMES      5U",
            "#define REHAB_ENVIRONMENT_SCAN_ROTATE_SPEED        3U",
            "#define REHAB_ENVIRONMENT_SCAN_ROTATE_MS         500U",
            "#define REHAB_ENVIRONMENT_SCAN_SETTLE_MS         200U",
            "#define REHAB_ENVIRONMENT_SCAN_WORKER_STACK_SIZE 8192U",
        ):
            self.assertIn(declaration, header)
        self.assertIn("REHAB_CAR_DIRECTION_ROTATE_CCW", source)
        self.assertIn("rehab_car_control_move(", source)
        self.assertIn("rehab_car_control_stop(", source)
        self.assertIn("rehab_car_control_release(", source)


if __name__ == "__main__":
    unittest.main()
