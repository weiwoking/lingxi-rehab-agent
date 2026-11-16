from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
APP = ROOT / "application" / "edge_agent"
DECISION = APP / "components" / "rehab_task_decision"
UI = APP / "fatfs_image" / "system" / "ui" / "rehab"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


RULES = read(DECISION / "src" / "rehab_task_decision_rules.c")
COORDINATOR = read(
    DECISION / "src" / "rehab_task_decision_coordinator.c"
)
SELECTED = read(
    DECISION / "src" / "rehab_task_decision_selected_task.c"
)
HOME = read(UI / "page_home.lua")
STATE = read(UI / "rehab_ui_state.lua")
CONTROLLER = read(UI / "esp_claw_rehab_ui.lua")
SCAN_PAGE = read(UI / "page_environment_scan.lua")
UI_BRIDGE = read(APP / "components" / "rehab_ui" / "src" /
                 "rehab_ui_task_decision_lua.c")
MAIN = read(APP / "main" / "main.c")
FONT_GENERATOR = read(
    APP / "tools" / "rehab_ui_font" / "scripts" /
    "generate_rehab_ui_font.py"
)
PACKAGING = read(
    ROOT / "components" / "common" / "skill_builder" / "tools" /
    "sync_component_skills.py"
)


TASKS = [
    ("OT_CUP_TRANSFER_L1", "水杯横向取放训练（一级）", "easy"),
    ("OT_CUP_DRINK_L2", "水杯拿取与饮水训练（二级）", "medium"),
    ("OT_CUP_DRINK_L3", "水杯拿取与饮水训练（三级）", "hard"),
    ("OT_BALL_TRANSFER_L1", "球类横向取放训练（一级）", "easy"),
    ("OT_BALL_TRANSFER_L2", "球类横向取放训练（二级）", "medium"),
]


class Stage8C1ARulesAssetAndHomeEntryTest(unittest.TestCase):
    def test_formal_asset_is_valid_and_device_runtime_includes_skill(self) -> None:
        asset = DECISION / "skills" / "rehab-task-decision" / \
            "decision_rules.json"
        payload = json.loads(asset.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["schema_version"], "rehab.task_decision.formal_rules.v1"
        )
        self.assertEqual(payload["recent_score_window"], 3)
        excluded = PACKAGING[
            PACKAGING.index("DEVICE_RUNTIME_EXCLUDED_SKILLS") :
            PACKAGING.index("})", PACKAGING.index(
                "DEVICE_RUNTIME_EXCLUDED_SKILLS"
            ))
        ]
        self.assertNotIn("'rehab-task-decision'", excluded)

    def test_external_search_order_and_missing_only_fallback(self) -> None:
        initializer = RULES[
            RULES.index("rehab_task_decision_rules_initialize") :
            RULES.index("void rehab_task_decision_rules_release")
        ]
        self.assertLess(
            initializer.index("CLAW_PATH_DATA"),
            initializer.index("CLAW_PATH_SYSTEM"),
        )
        self.assertIn(
            'strcmp(data_root, "/sdcard") == 0', initializer
        )
        self.assertIn("TASK_DECISION_RULES_INVALID", initializer)
        self.assertIn("err != ESP_ERR_NOT_FOUND", initializer)
        self.assertLess(
            initializer.index("err != ESP_ERR_NOT_FOUND"),
            initializer.index("build_builtin_rules"),
        )
        self.assertIn(
            "TASK_DECISION_RULES_READY source=builtin_fallback",
            initializer,
        )
        self.assertIn("sha256=%s", initializer)

    def test_builtin_rules_have_fixed_five_task_order(self) -> None:
        builtin = RULES[
            RULES.index("static esp_err_t build_builtin_rules") :
            RULES.index("rehab_task_decision_rules_initialize")
        ]
        self.assertIn('"recent_score_window", 3', builtin)
        self.assertIn('"difficulty"', builtin)
        self.assertNotIn("hard_gate", builtin)
        self.assertNotIn("required_any", builtin)
        positions = [RULES.index(f'"{task_id}"') for task_id, _, _ in TASKS]
        self.assertEqual(positions, sorted(positions))
        for task_id, task_name, difficulty in TASKS:
            definition = (
                f'{{"{task_id}", "{task_name}", "{difficulty}"}}'
            )
            self.assertIn(definition, RULES)
        self.assertIn(
            '"rehab.task_decision.formal_rules.v1"', RULES
        )

    def test_formal_coordinator_never_consumes_legacy_hard_gates(self) -> None:
        self.assertIn("hard_gates", COORDINATOR)
        self.assertNotIn("required_any", COORDINATOR)
        self.assertIn("TASK_DECISION_COORDINATOR_READY", MAIN)
        self.assertIn("state=PREPARING", COORDINATOR)
        self.assertIn("decision_id=%s", COORDINATOR)

    def test_home_has_two_non_overlapping_peer_entries(self) -> None:
        self.assertIn(
            'text = "扫描环境", x = 18, y = 166, w = 100, h = 50',
            HOME,
        )
        self.assertIn(
            'text = "决策任务", x = 126, y = 166, w = 100, h = 50',
            HOME,
        )
        self.assertLessEqual(18 + 100, 126)
        self.assertLessEqual(126 + 100, 18 + 208)
        self.assertIn("app.open_environment_scan()", HOME)
        self.assertIn("app.open_task_decision_prepare()", HOME)
        self.assertIn(
            'scan.can_decide and "决策任务" or "返回首页"',
            SCAN_PAGE,
        )
        self.assertEqual(
            CONTROLLER.count("function app.open_task_decision_prepare()"),
            1,
        )

    def test_home_gate_and_pre_start_rejection_stays_on_prepare(self) -> None:
        gate = STATE[
            STATE.index("function M.open_task_decision_prepare") :
            STATE.index("local function task_decision_signature")
        ]
        self.assertIn('call_task_decision("scene_status")', gate)
        self.assertIn('"暂无环境分析结果，请先扫描环境"', gate)
        self.assertIn('"环境结果不可用，请重新扫描"', gate)
        start = STATE[
            STATE.index("function M.start_task_decision") :
            STATE.index("function M.close_task_decision")
        ]
        self.assertNotIn('state = "FAILED"', start)
        self.assertNotIn("coordinator_started", start)
        self.assertIn("task_decision_pending_generation = 0", start)
        self.assertIn("REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI", UI_BRIDGE)
        for state in (
            "PREPARING", "DECIDING", "VALIDATING", "COMMITTING",
            "SELECTED", "FAILED",
        ):
            self.assertIn(state, STATE)

    def test_old_v1_is_compatible_but_not_a_new_formal_success(self) -> None:
        self.assertIn('"rehab.selected_task.v1"', SELECTED)
        self.assertIn('"rehab_selected_task.v2"', SELECTED)
        self.assertIn(
            "rehab_task_decision_selected_begin", COORDINATOR
        )
        self.assertLess(
            COORDINATOR.index(
                "rehab_task_decision_selected_begin"
            ),
            COORDINATOR.index("xTaskCreate(coordinator_worker"),
        )
        selected_branch = STATE[
            STATE.index('if result.state == "SELECTED" then') :
            STATE.index("return before ~=", STATE.index(
                'if result.state == "SELECTED" then'
            ))
        ]
        self.assertIn("state.selected_available = true", selected_branch)

    def test_new_home_and_failure_copy_is_present_in_font_inputs(self) -> None:
        for text in (
            "决策任务",
            "暂无环境分析结果，请先扫描环境",
            "环境结果不可用，请重新扫描",
        ):
            self.assertIn(f'"{text}"', FONT_GENERATOR)


if __name__ == "__main__":
    unittest.main()
