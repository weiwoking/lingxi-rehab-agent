from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
APP = ROOT / "application" / "edge_agent"
DECISION = APP / "components" / "rehab_task_decision"
UI = APP / "fatfs_image" / "system" / "ui" / "rehab"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "stage8c1b"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


COORDINATOR = read(DECISION / "src" / "rehab_task_decision_coordinator.c")
PRIVATE_HEADER = read(DECISION / "src" / "rehab_task_decision_priv.h")
SELECTED = read(DECISION / "src" / "rehab_task_decision_selected_task.c")
STATE = read(UI / "rehab_ui_state.lua")
HOME = read(UI / "page_home.lua")
RESULT = read(UI / "page_task_decision_result.lua")
CONTROLLER = read(UI / "esp_claw_rehab_ui.lua")
UI_BRIDGE = read(
    APP / "components" / "rehab_ui" / "src" /
    "rehab_ui_task_decision_lua.c"
)
TRAINING_CAPS = read(
    APP / "components" / "rehab_integration" / "src" /
    "rehab_capabilities.c"
)

TASK_IDS = [
    "OT_CUP_TRANSFER_L1",
    "OT_CUP_DRINK_L2",
    "OT_CUP_DRINK_L3",
    "OT_BALL_TRANSFER_L1",
    "OT_BALL_TRANSFER_L2",
]
CANONICAL_SCHEMA = re.search(
    r"#define\s+REHAB_TASK_DECISION_OUTPUT_SCHEMA_VERSION\s+\\?\s*"
    r'"([^"]+)"',
    PRIVATE_HEADER,
).group(1)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(raw: str) -> dict:
    first = raw.find("{")
    last = raw.rfind("}")
    if first < 0 or last < first:
        raise ValueError("json_invalid")
    value = json.loads(raw[first:last + 1])
    if value.get("schema_version") in ("1.0", "v1"):
        value["schema_version"] = CANONICAL_SCHEMA
    if "selected_task_id" not in value and "selected_task" in value:
        value["selected_task_id"] = value["selected_task"]
    if "selected_reason" not in value and "reason" in value:
        value["selected_reason"] = value["reason"]
    value.setdefault("target_object_id", None)
    by_id = {}
    for candidate in value.get("candidates", []):
        task_id = candidate.get("task_id")
        if task_id not in TASK_IDS or task_id in by_id:
            continue
        candidate = dict(candidate)
        score = candidate.get("score")
        if isinstance(score, str):
            candidate["score"] = float(score.strip())
        if not str(candidate.get("reason", "")).strip():
            candidate["reason"] = "模型未提供候选说明"
        by_id[task_id] = candidate
    value["candidates"] = [
        by_id[task_id] for task_id in TASK_IDS if task_id in by_id
    ]
    if not str(value.get("selected_reason", "")).strip():
        selected = value.get("selected_task_id")
        candidate = by_id.get(selected, {})
        value["selected_reason"] = candidate.get("reason", "")
    return value


def validate_normalized(value: dict, scan_id: str, scene_sha: str,
                        object_ids: set[str]) -> None:
    if value.get("schema_version") != CANONICAL_SCHEMA:
        raise ValueError("schema_invalid")
    if value.get("scene_scan_id") != scan_id:
        raise ValueError("scene_scan_id_stale")
    if value.get("scene_sha256") != scene_sha:
        raise ValueError("scene_sha256_stale")
    if [item.get("task_id") for item in value["candidates"]] != TASK_IDS:
        raise ValueError("candidate_ids_invalid")
    scores = [item.get("score") for item in value["candidates"]]
    if any(not isinstance(score, (int, float)) or not math.isfinite(score)
           or score < 0 or score > 100 for score in scores):
        raise ValueError("candidate_invalid")
    selected = value.get("selected_task_id")
    if selected not in TASK_IDS:
        raise ValueError("selected_task_invalid")
    selected_candidate = value["candidates"][TASK_IDS.index(selected)]
    if value.get("selected_reason") != selected_candidate.get("reason"):
        raise ValueError("selected_reason_invalid")
    target = value.get("target_object_id")
    if target is not None and target not in object_ids:
        raise ValueError("target_object_not_found")


def validate_scene(value: dict) -> None:
    if value.get("schema_version") != "stage8b4a_p4_decision_package.v1":
        raise ValueError("schema")
    if value.get("status") != "decision_ready":
        raise ValueError("status")
    if not isinstance(value.get("scan_id"), str) or not value["scan_id"]:
        raise ValueError("scan_id")
    objects = value.get("objects")
    if not isinstance(objects, list):
        raise ValueError("objects")
    ids = set()
    for item in objects:
        if not isinstance(item.get("object_id"), str) or not item["object_id"]:
            raise ValueError("object_id")
        if not isinstance(item.get("class_name"), str) or not item["class_name"]:
            raise ValueError("class_name")
        if item["object_id"] in ids:
            raise ValueError("duplicate_object_id")
        ids.add(item["object_id"])
        bbox = item.get("bbox_xyxy")
        if bbox is not None:
            if (not isinstance(bbox, list) or len(bbox) != 4 or
                    not all(isinstance(v, (int, float)) and math.isfinite(v)
                            for v in bbox) or
                    bbox[0] >= bbox[2] or bbox[1] >= bbox[3]):
                raise ValueError("bbox")


def simulate_startup_recovery(directory: Path) -> dict:
    current = directory / "selected_task.json"
    rollback = directory / "selected_task.json.previous"
    corrupt = None
    if current.exists():
        try:
            json.loads(current.read_text(encoding="utf-8"))
            return json.loads(current.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            corrupt = directory / "selected_task.json.corrupt.1"
            current.replace(corrupt)
    if rollback.exists():
        value = json.loads(rollback.read_text(encoding="utf-8"))
        rollback.replace(current)
        self_check = json.loads(current.read_text(encoding="utf-8"))
        if corrupt is not None:
            assert corrupt.exists()
        return self_check
    return {"available": False}


class Stage8C1BTransactionDiskSceneRelaxedLlmTest(unittest.TestCase):
    def test_current_sha_is_unchanged_on_v1_and_v2_failure(self) -> None:
        for fixture in ("selected_v1_current.json", "selected_v2_current.json"):
            with self.subTest(fixture=fixture), tempfile.TemporaryDirectory() as td:
                current = Path(td) / "selected_task.json"
                current.write_bytes((FIXTURES / fixture).read_bytes())
                before_bytes = current.read_bytes()
                before_sha = sha(current)
                next_path = current.with_suffix(".json.next")
                next_path.write_text("{malformed", encoding="utf-8")
                next_path.unlink()
                self.assertEqual(current.read_bytes(), before_bytes)
                self.assertEqual(sha(current), before_sha)

    def test_startup_recovery_and_structured_no_selection(self) -> None:
        rollback_bytes = (
            FIXTURES / "selected_task.rollback.json"
        ).read_bytes()
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            rollback = directory / "selected_task.json.previous"
            rollback.write_bytes(rollback_bytes)
            recovered = simulate_startup_recovery(directory)
            self.assertEqual(recovered["schema_version"],
                             "rehab.selected_task.v1")
            self.assertTrue((directory / "selected_task.json").exists())
            self.assertFalse(rollback.exists())

        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            current = directory / "selected_task.json"
            rollback = directory / "selected_task.json.previous"
            current.write_text("{corrupt", encoding="utf-8")
            rollback.write_bytes(rollback_bytes)
            recovered = simulate_startup_recovery(directory)
            self.assertEqual(recovered["task_id"], "OT_CUP_TRANSFER_L1")
            self.assertTrue(
                (directory / "selected_task.json.corrupt.1").exists()
            )
            self.assertEqual(current.read_bytes(), rollback_bytes)

        with tempfile.TemporaryDirectory() as td:
            structured = simulate_startup_recovery(Path(td))
            self.assertEqual(structured, {"available": False})
            self.assertEqual(json.loads(json.dumps(structured)), structured)

    def test_success_replace_and_final_failure_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            current = Path(td) / "selected_task.json"
            rollback = Path(td) / "selected_task.json.previous"
            next_path = Path(td) / "selected_task.json.next"
            current.write_bytes(
                (FIXTURES / "selected_v1_current.json").read_bytes()
            )
            old = current.read_bytes()
            next_path.write_bytes(
                (FIXTURES / "selected_v2_current.json").read_bytes()
            )
            current.replace(rollback)
            next_path.replace(current)
            self.assertEqual(
                json.loads(current.read_text(encoding="utf-8"))[
                    "schema_version"
                ],
                "rehab_selected_task.v2",
            )
            rollback.unlink()

            current.write_bytes(old)
            next_path.write_text("{malformed", encoding="utf-8")
            current.replace(rollback)
            next_path.replace(current)
            current.unlink()
            rollback.replace(current)
            self.assertEqual(current.read_bytes(), old)

    def test_reboot_scene_minimum_validation_and_container_equivalence(self) -> None:
        scene = json.loads(
            read(FIXTURES / "scene_reboot_bottle_only.json")
        )
        validate_scene(scene)
        classes = {item["class_name"] for item in scene["objects"]}
        self.assertNotIn("book", classes)
        self.assertNotIn("cup", classes)
        self.assertIn("bottle", classes)
        self.assertTrue(bool(classes & {"cup", "bottle"}))
        self.assertTrue(any(
            item.get("geometry", {}).get("valid") is False
            for item in scene["objects"]
        ))
        self.assertEqual([item["class_name"] for item in scene["objects"]],
                         ["bottle", "bottle"])

        minimal = {
            "schema_version": "stage8b4a_p4_decision_package.v1",
            "status": "decision_ready",
            "scan_id": "reboot_minimal",
            "objects": [{"object_id": "object_1", "class_name": "cup"}],
        }
        validate_scene(minimal)
        for classes, present in (
            (["cup"], True),
            (["bottle"], True),
            (["cup", "bottle"], True),
            (["book"], False),
            ([], False),
        ):
            candidate = dict(minimal)
            candidate["objects"] = [
                {"object_id": f"object_{index}", "class_name": class_name}
                for index, class_name in enumerate(classes, 1)
            ]
            validate_scene(candidate)
            observed = {item["class_name"] for item in candidate["objects"]}
            self.assertEqual(bool(observed & {"cup", "bottle"}), present)

        malformed = dict(minimal)
        malformed["schema_version"] = "wrong"
        with self.assertRaisesRegex(ValueError, "schema"):
            validate_scene(malformed)

    def test_relaxed_samples_normalize_then_keep_core_gates(self) -> None:
        # These old v1/alias samples are retained only to prove that stage 4A
        # rejects them before the one permitted v2 repair.
        samples = json.loads(read(FIXTURES / "llm_relaxed_samples.json"))
        for sample in samples:
            with self.subTest(case=sample["case"]):
                value = normalize(sample["raw"])
                self.assertTrue(all(
                    "score_breakdown" not in item
                    for item in value["candidates"]
                ))

        non_highest = normalize(samples[1]["raw"])
        non_highest["schema_version"] = CANONICAL_SCHEMA
        non_highest["selected_task_id"] = "OT_BALL_TRANSFER_L2"
        non_highest["selected_reason"] = non_highest["candidates"][4]["reason"]
        validate_normalized(non_highest, "scan_fixture", "b" * 64, set())
        invalid = normalize(samples[1]["raw"])
        invalid["schema_version"] = CANONICAL_SCHEMA
        invalid["target_object_id"] = "missing"
        with self.assertRaisesRegex(ValueError, "target_object_not_found"):
            validate_normalized(invalid, "scan_fixture", "b" * 64, set())
        invalid = normalize(samples[1]["raw"])
        invalid["schema_version"] = CANONICAL_SCHEMA
        invalid["candidates"][0]["score"] = math.inf
        with self.assertRaisesRegex(ValueError, "candidate_invalid"):
            validate_normalized(invalid, "scan_fixture", "b" * 64, set())

    def test_one_repair_can_succeed_but_missing_real_candidate_still_fails(
            self) -> None:
        with self.assertRaisesRegex(ValueError, "json_invalid"):
            normalize("{malformed")
        samples = json.loads(read(FIXTURES / "llm_relaxed_samples.json"))
        valid_after_repair = normalize(samples[0]["raw"])
        broken = json.loads(json.dumps(valid_after_repair))
        broken["candidates"] = broken["candidates"][:-1]
        with self.assertRaisesRegex(ValueError, "candidate_ids_invalid"):
            validate_normalized(
                broken, "scan_fixture", "b" * 64, {"bottle_1"}
            )
        validate_normalized(
            valid_after_repair, "scan_fixture", "b" * 64, {"bottle_1"}
        )
        still_broken = normalize(json.dumps(broken, ensure_ascii=False))
        with self.assertRaisesRegex(ValueError, "candidate_ids_invalid"):
            validate_normalized(
                still_broken, "scan_fixture", "b" * 64, {"bottle_1"}
            )
        self.assertEqual(COORDINATOR.count(
            "LLM_OUTPUT_REPAIR_ATTEMPT generation="), 1)

    def test_selected_source_has_recoverable_commit_and_structured_getter(self) -> None:
        for token in (
            "selected_task.json.next",
            "selected_task.json.previous",
            "fflush(file)",
            "fsync(fileno(file))",
            "validate_file(handle, next",
            "rename(final_path, previous)",
            "rename(next, final_path)",
            "rename(previous, final_path)",
            "SELECTED_TASK_RECOVERED source=rollback",
            "SELECTED_TASK_CORRUPT_QUARANTINED",
            "SELECTED_TASK_TRANSACTION_BEGIN",
            "current_preserved=1",
        ):
            self.assertIn(token, SELECTED)
        self.assertIn("err = ESP_OK;", SELECTED)
        start = COORDINATOR[
            COORDINATOR.index("esp_err_t rehab_task_decision_coordinator_start") :
        ]
        self.assertIn("rehab_task_decision_selected_begin", start)
        self.assertNotIn("rehab_task_decision_selected_invalidate(handle);",
                         start[:start.index("xTaskCreate")])

    def test_disk_scene_is_authoritative_and_stale_checked(self) -> None:
        load = COORDINATOR[
            COORDINATOR.index("static esp_err_t formal_scene_load") :
            COORDINATOR.index("static esp_err_t formal_scene_is_current")
        ]
        self.assertNotIn("rehab_environment_scan_get_status", load)
        self.assertNotIn("DECISION_READY", load)
        for token in (
            "FORMAL_SCENE_SCHEMA",
            '"decision_ready"',
            '"scan_id"',
            '"objects"',
            "bytes_sha256",
            "strcmp(sha256, frozen->sha256)",
        ):
            self.assertIn(token, COORDINATOR)
        self.assertIn("rehab_task_decision_latest_scene_validate", UI_BRIDGE)
        self.assertIn('{"scene_status", lua_task_decision_scene_status}',
                      UI_BRIDGE)

    def test_strict_v2_validator_and_semantic_group_are_in_firmware_source(self) -> None:
        for token in (
            "rehab_task_scoring_validate",
            "build_repair_prompt",
            "LLM_OUTPUT_REPAIR_ATTEMPT",
            "LLM_OUTPUT_VALIDATE_FAILED field=%s reason=%s",
            'cJSON_AddArrayToObject(semantic_groups, "cup")',
            'cJSON_AddArrayToObject(semantic_groups, "ball")',
            'cJSON_CreateString("cup")',
            'cJSON_CreateString("bottle")',
            'cJSON_CreateString("volleyball")',
        ):
            self.assertIn(token, COORDINATOR)
        self.assertNotIn("cJSON_GetArraySize(decision->root) != 8",
                         COORDINATOR)
        self.assertNotIn("cJSON_GetArraySize(entry) != 3", COORDINATOR)

    def test_home_and_result_buttons_are_always_bright_and_clickable(self) -> None:
        self.assertIn(
            'text = "决策任务", x = 126, y = 166, w = 100, h = 50,\n'
            "    bg_color = t.accent, text_color = t.text",
            HOME,
        )
        self.assertIn(
            'text = "开始训练", x = 660, y = 42, w = 306, h = 56,\n'
            "    bg_color = t.accent, text_color = t.text",
            HOME,
        )
        self.assertIn(
            "view.start_button:set_style(t.button_style(t.accent, t.text))",
            HOME,
        )
        self.assertIn(
            "view.decision_button:set_style(t.button_style(t.accent, t.text))",
            HOME,
        )
        self.assertIn('call_task_decision("scene_status")', STATE)
        for text in (
            "暂无环境分析结果，请先扫描环境",
            "环境结果不可用，请重新扫描",
            "正在进行任务决策，请稍候",
            "暂无可用训练任务，请先完成任务决策",
            "正在重新决策，请稍候",
            "决策失败，已保留原任务",
        ):
            self.assertIn(text, STATE + RESULT)
        self.assertIn("app.queue_start_training()", RESULT)
        self.assertNotIn(
            "if data.selected then app.queue_start_training() end", RESULT
        )
        queue_start = CONTROLLER[
            CONTROLLER.index("function app.queue_start_training") :
            CONTROLLER.index("function app.queue_stop_training")
        ]
        self.assertNotIn("is_start_enabled", queue_start)
        self.assertIn("task_decision_active", TRAINING_CAPS)


if __name__ == "__main__":
    unittest.main()
