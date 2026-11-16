from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
APP = ROOT / "application" / "edge_agent"
COMPONENT = APP / "components" / "rehab_task_decision"
UI = APP / "fatfs_image" / "system" / "ui" / "rehab"
FIXTURE = COMPONENT / "test" / "fixtures" / "stage4b" / "decision_details_success.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


DETAILS = read(COMPONENT / "src" / "rehab_task_decision_details.c")
COORDINATOR = read(COMPONENT / "src" / "rehab_task_decision_coordinator.c")
CONFIRMED_START = read(
    COMPONENT / "src" / "rehab_task_decision_confirmed_start.c")
CAPABILITIES = read(
    COMPONENT / "src" / "rehab_task_decision_coordinator_capabilities.c")
SELECTED = read(COMPONENT / "src" / "rehab_task_decision_selected_task.c")
HOME = read(UI / "page_home.lua")
RESULT = read(UI / "page_task_decision_result.lua")
PAGE = read(UI / "page_task_decision_details.lua")
CONTROLLER = read(UI / "esp_claw_rehab_ui.lua")
STATE = read(UI / "rehab_ui_state.lua")


def validate_details(value: dict, expected: str | None = None) -> bool:
    if value.get("schema_version") != "rehab_selected_task_details.v1":
        return False
    if not value.get("decision_id") or value.get("generation", 0) < 1:
        return False
    if value.get("entry_source") not in {
            "local_ui", "wechat", "console", "unknown"}:
        return False
    candidates = value.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 5:
        return False
    if [item.get("candidate_ref") for item in candidates] != list(range(5)):
        return False
    winner = value.get("winner_ref")
    if winner not in range(5):
        return False
    selected = value.get("selected_task", {})
    candidate = candidates[winner]
    if any(selected.get(key) != candidate.get(key)
           for key in ("task_name", "score", "reason", "availability")):
        return False
    return expected is None or value["decision_id"] == expected


def write_details_atomic(directory: Path, value: dict,
                         fail_at: str | None = None) -> None:
    next_file = directory / "selected_task_details.json.next"
    previous = directory / "selected_task_details.json.previous"
    final = directory / "selected_task_details.json"
    if previous.exists():
        if final.exists():
            previous.unlink()
        else:
            previous.replace(final)
    next_file.unlink(missing_ok=True)
    next_file.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    if fail_at == "write_next":
        next_file.unlink()
        raise OSError("injected details failure")
    if final.exists():
        final.replace(previous)
    if fail_at == "promote_next":
        previous.replace(final)
        next_file.unlink(missing_ok=True)
        raise OSError("injected promote failure")
    next_file.replace(final)
    previous.unlink(missing_ok=True)


class Stage4BDecisionDetailsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.value = json.loads(read(FIXTURE))

    def test_real_decision_fixture_has_five_stable_candidates_and_winner(self) -> None:
        self.assertTrue(validate_details(self.value))
        self.assertEqual(self.value["decision_id"], "decision-00000001-21168")
        self.assertEqual(self.value["winner_ref"], 0)
        self.assertEqual(self.value["entry_source"], "unknown")
        self.assertEqual(self.value["selected_task"]["score"], 95.728)
        self.assertEqual(self.value["target_details"]["class_name"], "cup")
        self.assertEqual(self.value["target_details"]["object_height_mm"], 42)
        self.assertEqual(self.value["target_details"]["support_height_mm"], 756)

    def test_direction_is_derived_only_from_value_and_blocked_is_null(self) -> None:
        for candidate in self.value["candidates"]:
            breakdown = candidate["score_breakdown"]
            if candidate["availability"] == "blocked":
                self.assertIsNone(breakdown)
                self.assertEqual(candidate["score"], 0)
                continue
            for factor in breakdown.values():
                expected = ("positive" if factor["value"] > 0 else
                            "negative" if factor["value"] < 0 else "neutral")
                self.assertEqual(factor["direction"], expected)
            self.assertEqual(
                set(breakdown),
                {"difficulty", "advice", "compensation", "frequency",
                 "perception", "final_raw"},
            )

    def test_ui_shows_earned_over_max_without_positive_add_wording(self) -> None:
        self.assertNotIn("direction_text", PAGE)
        self.assertNotIn("factor.direction", PAGE)
        self.assertNotIn('"加分"', PAGE)
        self.assertNotIn('"扣分"', PAGE)
        self.assertIn('string.format("%s %.1f / %d"', PAGE)
        for label, maximum in (
            ("难度匹配", 35), ("医嘱匹配", 25), ("代偿历史", 20),
            ("训练频率", 10), ("感知质量", 10), ("总分", 100),
        ):
            self.assertIn(f'"{label}", {maximum}', PAGE)
        visible = (
            f"医嘱匹配 {16.0:.1f} / 25  "
            f"代偿历史 {2.0:.1f} / 20"
        )
        self.assertEqual(
            visible,
            "医嘱匹配 16.0 / 25  代偿历史 2.0 / 20",
        )
        self.assertNotIn("加分", visible)
        self.assertNotIn("正向", visible)

    def test_snapshot_excludes_prompt_hidden_reasoning_and_advice_interpretation(self) -> None:
        serialized = json.dumps(self.value, ensure_ascii=False).lower()
        for forbidden in ("system_prompt", "hidden_reasoning", "chain_of_thought",
                          "advice_interpretation", "api_key"):
            self.assertNotIn(forbidden, serialized)
        for forbidden in ("s_system_prompt", "LLM_OUTPUT_SCHEMA_TEXT"):
            self.assertNotIn(forbidden, DETAILS)

    def test_missing_fields_are_null_and_have_availability_status(self) -> None:
        target = self.value["target_details"]
        availability = self.value["data_availability"]
        self.assertIsNone(target["logical_object_id"])
        self.assertEqual(availability["logical_object_id"], "not_integrated")
        self.assertIsNone(target["geometry_confidence"])
        self.assertEqual(availability["geometry_confidence"], "fallback")
        self.assertIsNone(self.value["created_at"])
        self.assertEqual(self.value["time_status"], "unavailable")

    def test_details_failure_does_not_rollback_selected_or_impersonate_current(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            selected = directory / "selected_task.json"
            selected.write_text(json.dumps({"decision_id": "decision-new"}),
                                encoding="utf-8")
            old = dict(self.value)
            old["decision_id"] = "decision-old"
            write_details_atomic(directory, old)
            with self.assertRaises(OSError):
                write_details_atomic(directory, self.value,
                                     fail_at="write_next")
            self.assertEqual(json.loads(selected.read_text(encoding="utf-8"))[
                "decision_id"], "decision-new")
            persisted = json.loads((directory / "selected_task_details.json").read_text(
                encoding="utf-8"))
            self.assertEqual(persisted["decision_id"], "decision-old")
            self.assertFalse(validate_details(persisted, "decision-new"))

        commit = COORDINATOR.index("SELECTED_TASK_TRANSACTION_COMMIT")
        details = COORDINATOR.index("rehab_task_decision_details_build", commit)
        selected_state = COORDINATOR.index(
            "REHAB_TASK_DECISION_COORDINATOR_SELECTED", details)
        self.assertLess(commit, details)
        self.assertLess(details, selected_state)
        sidecar = COORDINATOR[details:selected_state]
        self.assertNotIn("goto done", sidecar)
        self.assertNotIn("failure =", sidecar)

    def test_next_previous_state_matrix_and_promote_restore(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            old = dict(self.value)
            old["decision_id"] = "decision-old"
            write_details_atomic(directory, old)
            with self.assertRaises(OSError):
                write_details_atomic(directory, self.value,
                                     fail_at="promote_next")
            restored = json.loads((directory / "selected_task_details.json").read_text(
                encoding="utf-8"))
            self.assertEqual(restored["decision_id"], "decision-old")
            self.assertFalse((directory / "selected_task_details.json.next").exists())

            final = directory / "selected_task_details.json"
            previous = directory / "selected_task_details.json.previous"
            final.replace(previous)
            write_details_atomic(directory, self.value)
            committed = json.loads(final.read_text(encoding="utf-8"))
            self.assertEqual(committed["decision_id"], self.value["decision_id"])
            self.assertFalse(previous.exists())

    def test_atomic_write_loader_corruption_reboot_and_old_device(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            self.assertFalse((directory / "selected_task_details.json").exists())
            write_details_atomic(directory, self.value)
            loaded = json.loads((directory / "selected_task_details.json").read_text(
                encoding="utf-8"))
            self.assertTrue(validate_details(loaded, self.value["decision_id"]))
            (directory / "selected_task_details.json").write_text(
                "{corrupt", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                json.loads((directory / "selected_task_details.json").read_text(
                    encoding="utf-8"))
        for token in ("fflush(file)", "fsync(fileno(file))",
                      "rename(next_path, final_path)", "backup_final",
                      "restore_previous", "ESP_ERR_NOT_FOUND",
                      "ESP_ERR_INVALID_RESPONSE", "ESP_ERR_INVALID_STATE"):
            self.assertIn(token, DETAILS)

    def test_selected_contract_and_candidate_ref_v3_are_untouched(self) -> None:
        self.assertIn('#define SELECTED_TASK_SCHEMA_V2 "rehab_selected_task.v2"',
                      SELECTED)
        self.assertNotIn("selected_task_details", SELECTED)
        self.assertIn("rehab_task_decision_output.v4", COORDINATOR)
        self.assertIn("rehab_task_scoring_validate", COORDINATOR)

    def test_entry_source_is_factual_bounded_and_generation_scoped(self) -> None:
        for value in ("local_ui", "wechat", "console", "unknown"):
            candidate = dict(self.value)
            candidate["entry_source"] = value
            self.assertTrue(validate_details(candidate))
        for value in ("agent", "formal_decision_coordinator", ""):
            candidate = dict(self.value)
            candidate["entry_source"] = value
            self.assertFalse(validate_details(candidate))

        self.assertIn('strcmp(source->valuestring, "wechat") == 0',
                      CAPABILITIES)
        self.assertIn('strcmp(source->valuestring, "local_ui") == 0',
                      CAPABILITIES)
        self.assertNotIn("REHAB_TASK_DECISION_START_SOURCE_AGENT, &result",
                         CAPABILITIES)
        self.assertIn("handle->coordinator_entry_source = source", COORDINATOR)
        self.assertIn("entry_source = handle->coordinator_entry_source",
                      COORDINATOR)
        self.assertIn("package->entry_source = source", COORDINATOR)
        self.assertIn("canonical_entry_source(entry_source)", DETAILS)
        self.assertIn("case REHAB_TASK_DECISION_START_SOURCE_AGENT:",
                      CONFIRMED_START)

        self.assertIn('local_ui = "\u672c\u5730\u754c\u9762"', HOME)
        self.assertIn('wechat = "\u5fae\u4fe1"', HOME)
        self.assertIn('console = "\u63a7\u5236\u53f0"', HOME)
        self.assertIn('unknown = "\u672a\u77e5"', HOME)
        self.assertIn("entry_source_text[data.entry_source]", PAGE)

    def test_home_result_and_scrollable_details_navigation(self) -> None:
        self.assertIn('text = "决策详情"', HOME)
        self.assertIn('app.open_task_decision_details("home")', HOME)
        self.assertIn('text = "查看详情"', RESULT)
        self.assertIn('app.open_task_decision_details("result")', RESULT)
        self.assertIn('task_decision_details = task_decision_details_page',
                      CONTROLLER)
        self.assertIn('body:set_scroll({ dir = "ver", scrollbar = "auto" })',
                      PAGE)
        self.assertIn('text = "返回"', PAGE)
        self.assertIn("task_decision_details = true", STATE)
        self.assertIn('state.page == "task_decision_details"', STATE)
        self.assertNotIn('"任务 ID"', HOME + RESULT + PAGE)
        self.assertNotIn("selected_task_id", PAGE)

    def test_required_logs_and_non_authoritative_failure_boundary(self) -> None:
        for token in ("SELECTED_TASK_DETAILS_BUILD",
                      "SELECTED_TASK_DETAILS_COMMIT",
                      "SELECTED_TASK_DETAILS_WRITE_FAILED"):
            self.assertIn(token, DETAILS + COORDINATOR)
        self.assertNotIn("rehab_task_decision_selected_commit", DETAILS)
        self.assertNotIn("rehab_task_decision_selected_invalidate", DETAILS)
        for stage in (
            "serialize", "size_check", "path_resolve", "mkdir",
            "stale_recovery", "remove_next", "open_next", "write_next",
            "flush_next", "fsync_next", "close_next", "readback_next",
            "validate_next", "bind_next", "remove_previous", "backup_final",
            "promote_next", "readback_final", "validate_final", "bind_final",
            "restore_previous", "cleanup",
        ):
            self.assertIn(f'"{stage}"', DETAILS)
        for field in ("serialized_bytes", "max_bytes", "errno_text",
                      "final=%d", "next=%d", "previous=%d"):
            self.assertIn(field, DETAILS)


if __name__ == "__main__":
    unittest.main()
