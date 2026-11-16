from __future__ import annotations

import json
import unittest
from pathlib import Path

from backend.app.pc_local_runtime_service import PcLocalRuntimeService
from backend.app.task_registry_adapter import (
    TASK_REGISTRY_PATH,
    TASK_REGISTRY_VERSION,
    legacy_task_fields,
    load_task_registry,
    resolve_task_name,
)


ROOT = Path(__file__).resolve().parents[2]
SCORE_ROOT = Path(r"C:\ec\esp-claw\pc\rehab_score_service")


def read_doctor(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def read_score(relative: str) -> str:
    return (SCORE_ROOT / relative).read_text(encoding="utf-8")


class FiveTaskRegistryDynamicUiContractTest(unittest.TestCase):
    def test_doctor_adapter_reads_score_service_registry_authority(self) -> None:
        self.assertEqual(TASK_REGISTRY_VERSION, "rehab_task_registry_v1")
        self.assertEqual(TASK_REGISTRY_PATH, SCORE_ROOT / "config" / "rehab_task_registry_v1.json")
        registry = load_task_registry()
        self.assertEqual(registry["registry_version"], TASK_REGISTRY_VERSION)
        self.assertEqual(len(registry["tasks"]), 5)

    def test_doctor_resolver_matrix_matches_five_formal_tasks(self) -> None:
        expected = [
            ("水杯拿取与饮水训练（二级）", "drink_water", "cup", 2),
            ("水杯拿取与饮水训练（三级）", "drink_water", "cup", 3),
            ("水杯横向取放训练（一级）", "lateral_transfer", "cup", 1),
            ("球类横向取放训练（一级）", "lateral_transfer", "ball", 1),
            ("球类横向取放训练（二级）", "lateral_transfer", "ball", 2),
        ]
        for task_name, family, object_type, level in expected:
            with self.subTest(task_name=task_name):
                resolved = resolve_task_name(task_name)
                self.assertEqual(resolved["task_registry_status"], "resolved")
                self.assertEqual(resolved["task_name"], task_name)
                self.assertEqual(resolved["motion_family"], family)
                self.assertEqual(resolved["object_type"], object_type)
                self.assertEqual(resolved["score_profile"], family)
                self.assertEqual(resolved["twin_profile"], family)
                self.assertEqual(resolved["task_level"], level)

    def test_real_task_json_contract_parser_resolves_all_five_tasks(self) -> None:
        for task in load_task_registry()["tasks"]:
            contract = {
                "task_name": task["task_name"],
                "csv_file": "DATA_SEQ0000000001.csv",
                "start_time": "2026-08-08T00:00:00+00:00",
                "end_time": "2026-08-08T00:01:00+00:00",
                "start_log_seq": 1,
                "end_log_seq": 2,
                "end_reason": "completed",
            }
            parsed = PcLocalRuntimeService._parse_task_contract_bytes(
                json.dumps(contract, ensure_ascii=False).encode("utf-8")
            )
            resolved = resolve_task_name(parsed["task_name"])
            self.assertEqual(resolved["task_registry_status"], "resolved")

    def test_real_task_json_contract_parser_preserves_unknown_for_rejection(self) -> None:
        contract = {
            "task_name": "不存在的训练任务",
            "csv_file": "DATA_SEQ0000000001.csv",
            "start_time": "2026-08-08T00:00:00+00:00",
            "end_time": "2026-08-08T00:01:00+00:00",
            "start_log_seq": 1,
            "end_log_seq": 2,
            "end_reason": "completed",
        }
        parsed = PcLocalRuntimeService._parse_task_contract_bytes(
            json.dumps(contract, ensure_ascii=False).encode("utf-8")
        )
        resolved = resolve_task_name(parsed["task_name"])
        self.assertEqual(resolved["task_registry_status"], "unknown")
        self.assertEqual(resolved["unsupported_task_reason"], "UNKNOWN_TASK")
        self.assertIsNone(resolved["score_profile"])

    def test_unknown_task_is_not_defaulted_to_drink(self) -> None:
        resolved = resolve_task_name("不存在的训练任务")
        self.assertEqual(resolved["task_registry_status"], "unknown")
        self.assertEqual(resolved["unsupported_task_reason"], "UNKNOWN_TASK")
        self.assertIsNone(resolved["motion_family"])
        self.assertIsNone(resolved["score_profile"])

    def test_session_metadata_is_persisted_from_task_json_name(self) -> None:
        source = read_doctor("backend/app/pc_local_runtime_service.py")
        self.assertIn("resolved_task_fields(task.get(\"task_name\") or \"\")", source)
        self.assertIn("summary.update(resolved_task_fields", source)
        self.assertIn('"original_task_name"', read_doctor("backend/app/device_history_store.py"))
        self.assertIn('"task_registry_version"', read_doctor("backend/app/device_history_store.py"))
        self.assertIn('"motion_family"', read_doctor("backend/app/device_history_store.py"))

    def test_score_routing_uses_score_profile_not_object_type(self) -> None:
        source = read_doctor("backend/app/session_v12_scoring_service.py")
        self.assertIn("_score_profile_for_summary(summary or {})", source)
        self.assertIn('if profile == "lateral_transfer"', source)
        self.assertIn('elif profile == "drink_water"', source)
        self.assertIn("LATERAL_SESSION_SCORING_TOOL_PATH", source)
        self.assertNotIn('get("score_profile") or "drink_water"', source)
        self.assertNotIn('object_type") == "cup"', source)

    def test_reconstruction_routes_motion_and_object_independently(self) -> None:
        source = read_doctor("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("selectedObjectType", source)
        self.assertIn("scene.setPresentationObjectMode(selectedObjectType)", source)
        self.assertIn("function objectTypeForSession", source)
        self.assertIn("function motionFamilyForSession", source)
        self.assertIn("function displayTaskNameForSession", source)
        self.assertNotIn('selectedMotionFamily === "lateral_transfer" ? "ball" : "cup"', source)

    def test_lateral_cup_uses_lateral_motion_and_cup_object(self) -> None:
        resolved = resolve_task_name("水杯横向取放训练（一级）")
        self.assertEqual(resolved["motion_family"], "lateral_transfer")
        self.assertEqual(resolved["object_type"], "cup")
        source = read_doctor("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn('session.object_type === "cup"', source)
        self.assertIn('session.twin_profile === "lateral_transfer"', source)

    def test_reconstruction_unknown_task_fails_closed_without_task12_default(self) -> None:
        source = read_doctor("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn('session.task_registry_status === "unknown"', source)
        self.assertIn('return "";', source)
        self.assertNotIn('return "TASK12"', source)
        self.assertIn('playable: Boolean(session.session_id && routeReady)', source)

    def test_reconstruction_record_action_goes_directly_to_actual(self) -> None:
        source = read_doctor("frontend/src/pages/ReconstructionPage.tsx")
        play_selected = source.split("function playSelectedRecord", 1)[1].split("function returnToRecords", 1)[0]
        self.assertIn('view: "actual"', play_selected)
        self.assertIn('setView("actual")', play_selected)
        self.assertNotIn('view: "fixed"', play_selected)
        self.assertNotIn("data-driven-reconstruction-entry", source)

    def test_task_library_supported_tasks_are_derived_from_registry(self) -> None:
        source = read_doctor("backend/app/task_library_service.py")
        self.assertIn("load_task_registry()", source)
        self.assertIn("SUPPORTED_TASKS: dict[str, dict[str, Any]] = _supported_tasks_from_registry()", source)
        self.assertNotIn("pick_book_right", source)
        self.assertNotIn("drink_water_right", source)

    def test_registry_does_not_enter_algorithm_authorities(self) -> None:
        algorithm_files = [
            "feature_extractors/lf_detector.py",
            "feature_extractors/tremor_detector.py",
            "tools/drink_water_v1_pipeline.py",
            "tools/lateral_transfer_segmentation_v1.py",
            "scoring/score_action_integrated.py",
        ]
        for relative in algorithm_files:
            with self.subTest(relative=relative):
                self.assertNotIn("task_registry", read_score(relative))

    def test_legacy_heuristic_is_fallback_only(self) -> None:
        fields = legacy_task_fields({"task_name": "TASK054 球类横向取放历史记录"})
        self.assertEqual(fields["task_registry_status"], "legacy_fallback")
        self.assertEqual(fields["motion_family"], "lateral_transfer")
        self.assertEqual(fields["object_type"], "ball")

    def test_unknown_registry_session_cannot_reenter_legacy_fallback(self) -> None:
        fields = legacy_task_fields(
            {
                "task_registry_status": "unknown",
                "task_name": "未知水杯训练",
            }
        )
        self.assertEqual(fields, {})


if __name__ == "__main__":
    unittest.main()
