#!/usr/bin/env python3
"""Stage-three closeout: selected candidate contract and safe invalid files."""

from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
APP = COMPONENT.parents[1]
FIXTURES = COMPONENT / "test/fixtures"
TASKS = [
    ("OT_CUP_TRANSFER_L1", "水杯横向取放训练（一级）"),
    ("OT_CUP_DRINK_L2", "水杯拿取与饮水训练（二级）"),
    ("OT_CUP_DRINK_L3", "水杯拿取与饮水训练（三级）"),
    ("OT_BALL_TRANSFER_L1", "球类横向取放训练（一级）"),
    ("OT_BALL_TRANSFER_L2", "球类横向取放训练（二级）"),
]
ROOT_FIELDS = {
    "schema_version", "task_id", "task_name", "reason", "score",
    "created_at", "decision_id", "decision_generation", "source_scan_id",
    "source_scene_sha256", "suggested_distance_cm", "candidate_scores",
    "target_object_id", "target_object",
}
CANDIDATE_FIELDS = {"task_name", "score", "reason"}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def validate_new_selected(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != ROOT_FIELDS:
        return False
    candidates = value.get("candidate_scores")
    if not isinstance(candidates, list) or len(candidates) != len(TASKS):
        return False
    if any(not isinstance(item, dict) or set(item) != CANDIDATE_FIELDS
           for item in candidates):
        return False
    if [item["task_name"] for item in candidates] != [item[1] for item in TASKS]:
        return False
    scores = [item["score"] for item in candidates]
    if any(not isinstance(score, (int, float)) or isinstance(score, bool)
           or not 0 <= score <= 100 for score in scores):
        return False
    selected = next((index for index, task in enumerate(TASKS)
                     if task[0] == value.get("task_id")), None)
    if selected is None:
        return False
    task_id, task_name = TASKS[selected]
    return (
        value.get("task_id") == task_id
        and value.get("task_name") == task_name
        and value.get("task_name") == candidates[selected]["task_name"]
        and value.get("reason") == candidates[selected]["reason"]
        and value.get("score") == candidates[selected]["score"]
    )


def normalize_reason(value: str) -> str:
    result = value.replace("sports ball", "排球")
    result = result.replace("volleyball", "排球")
    result = result.replace("水瓶", "杯子").replace("瓶子", "杯子")
    return re.sub(r"(?<![A-Za-z0-9_])ball(?![A-Za-z0-9_])", "排球", result)


class Stage3ContractFixTest(unittest.TestCase):
    def setUp(self) -> None:
        self.selected = json.loads(read(
            FIXTURES / "stage8c1b/selected_v2_current.json"
        ))

    def test_new_selected_has_exact_root_and_candidate_contract(self) -> None:
        self.assertTrue(validate_new_selected(self.selected))
        self.assertEqual(set(self.selected), ROOT_FIELDS)
        self.assertEqual(len(self.selected["candidate_scores"]), 5)
        self.assertTrue(all(
            set(item) == CANDIDATE_FIELDS
            for item in self.selected["candidate_scores"]
        ))
        self.assertTrue(all(
            "task_id" not in item for item in self.selected["candidate_scores"]
        ))

    def test_non_highest_selected_candidate_keeps_identity_score_binding(self) -> None:
        selected = copy.deepcopy(self.selected)
        selected["task_id"], selected["task_name"] = TASKS[1]
        selected["reason"] = selected["candidate_scores"][1]["reason"]
        selected["score"] = selected["candidate_scores"][1]["score"]
        self.assertGreater(selected["candidate_scores"][0]["score"],
                           selected["score"])
        self.assertTrue(validate_new_selected(selected))
        corrupted = copy.deepcopy(selected)
        corrupted["score"] += 1
        self.assertFalse(validate_new_selected(corrupted))

    def test_writer_projects_and_reader_strictly_checks_three_fields(self) -> None:
        coordinator = read(COMPONENT / "src/rehab_task_decision_coordinator.c")
        writer = coordinator[
            coordinator.index("static cJSON *build_selected_candidate_scores"):
            coordinator.index("static cJSON *build_selected_v2")
        ]
        self.assertIn('cJSON_AddStringToObject(entry, "task_name"', writer)
        self.assertIn('cJSON_AddNumberToObject(entry, "score"', writer)
        self.assertIn('cJSON_AddStringToObject(entry, "reason"', writer)
        self.assertNotIn('cJSON_AddStringToObject(entry, "task_id"', writer)
        self.assertNotIn("cJSON_Duplicate(source", writer)

        selected_source = read(
            COMPONENT / "src/rehab_task_decision_selected_task.c"
        )
        parser = selected_source[
            selected_source.index("static bool selected_candidates_valid"):
            selected_source.index("static esp_err_t selected_parse_v2")
        ]
        self.assertIn("cJSON_GetArraySize(entry) != 3", parser)
        self.assertIn("rehab_task_policy_task_at(index)", parser)
        self.assertIn("strcmp(task_name->valuestring, task->task_name)", parser)
        self.assertIn("strcmp(reason->valuestring, selected_reason)", parser)
        self.assertIn("return selected_seen;", parser)
        self.assertNotIn("highest_index", parser)
        self.assertNotIn('entry,\n                                                                "task_id"', parser)

    def test_old_six_unknown_and_mixed_formats_fail_safely(self) -> None:
        old = copy.deepcopy(self.selected)
        old["candidate_scores"] = [
            {"task_id": f"OLD_{index}", "task_name": f"旧任务{index}",
             "score": 50, "reason": "历史理由"}
            for index in range(6)
        ]
        mixed = copy.deepcopy(self.selected)
        mixed["candidate_scores"][0]["task_id"] = TASKS[0][0]
        unknown = copy.deepcopy(self.selected)
        unknown["task_id"] = "UNKNOWN_TASK"
        self.assertFalse(validate_new_selected(old))
        self.assertFalse(validate_new_selected(mixed))
        self.assertFalse(validate_new_selected(unknown))

        selected_source = read(
            COMPONENT / "src/rehab_task_decision_selected_task.c"
        )
        self.assertIn("SELECTED_TASK_CORRUPT_QUARANTINED", selected_source)
        begin = selected_source[
            selected_source.index("rehab_task_decision_selected_begin"):
            selected_source.index("rehab_task_decision_selected_initialize")
        ]
        self.assertIn("err == ESP_ERR_NOT_FOUND", begin)
        self.assertIn("err = ESP_OK", begin)
        self.assertIn("selected_recover_locked", begin)
        self.assertTrue(validate_new_selected(self.selected))

    def test_reason_terms_normalize_without_changing_other_text(self) -> None:
        cases = {
            "检测到瓶子": "检测到杯子",
            "水瓶位于高平面": "杯子位于高平面",
            "volleyball位于低平面": "排球位于低平面",
            "sports ball位于低平面": "排球位于低平面",
            "ball位于低平面": "排球位于低平面",
            "football位于低平面": "football位于低平面",
            "杯子位于低平面": "杯子位于低平面",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(normalize_reason(source), expected)
        policy = read(COMPONENT / "src/rehab_task_decision_policy.c")
        self.assertIn("rehab_task_policy_normalize_reason", policy)
        self.assertIn("standalone_word", policy)
        scoring = read(COMPONENT / "src/rehab_task_decision_scoring.c")
        self.assertNotIn("normalize_reason(candidate)", scoring)
        self.assertNotIn("rehab_task_policy_normalize_reason", scoring)
        self.assertNotIn("reason_uses_canonical_group_name", scoring)

    def test_target_ui_history_and_downstream_contracts_remain_stable(self) -> None:
        coordinator = read(COMPONENT / "src/rehab_task_decision_coordinator.c")
        self.assertIn("cJSON *target = cJSON_Duplicate(source, true)", coordinator)
        self.assertIn("source_class->valuestring", coordinator)
        target_fixture = json.loads(read(
            FIXTURES / "stage8c1e/pc_bottle_llm_cup.json"
        ))
        self.assertEqual(target_fixture["objects"][1]["class_name"], "bottle")

        ui_source = "\n".join(
            read(path) for path in (
                APP / "fatfs_image/system/ui/rehab/rehab_ui_state.lua",
                APP / "fatfs_image/system/ui/rehab/page_task_decision_result.lua",
                APP / "components/rehab_ui/src/rehab_ui_task_decision_lua.c",
            )
        )
        self.assertNotIn("candidate_scores", ui_source)
        self.assertIn("302, 52", ui_source)

        context = read(COMPONENT / "src/rehab_task_decision_context.c")
        self.assertIn("recent_scores_tend_level_two", context)
        self.assertIn("recent->scores[i] >= 50.0", context)

        score_contract = read(
            APP / "components/rehab_score_bridge/src/rehab_score_bridge_contract.c"
        )
        self.assertIn(
            '"task_name", "csv_file", "start_time", "end_time",',
            score_contract,
        )
        self.assertNotIn('"task_id", "csv_file"', score_contract)


if __name__ == "__main__":
    unittest.main()
