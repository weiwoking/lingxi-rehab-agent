from __future__ import annotations

import asyncio
import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backend.app.pc_local_runtime_service as runtime_module
import backend.app.session_v12_scoring_service as module


class SessionV12ScoringServiceContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="session-v12-contract-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_only_real_device_pc_udp_session_is_eligible(self) -> None:
        eligible = {
            "session_id": "20260806_TASK001",
            "source_mode": "device",
            "source_origin": "pc_udp_6006_ingest",
            "task_finalized": True,
            "csv_relative_path": "sessions/20260806_TASK001/DATA_SEQ0000000001.csv",
        }
        self.assertTrue(module.SessionV12ScoringService._eligible_runtime_summary(eligible))
        self.assertFalse(module.SessionV12ScoringService._eligible_runtime_summary({**eligible, "source_origin": "showcase"}))
        self.assertFalse(module.SessionV12ScoringService._eligible_runtime_summary({**eligible, "task_finalized": False}))

    def test_orientation_format_requires_all_four_imus(self) -> None:
        euler_header = ["time_ms", "log_seq"] + [
            f"imu{idx}_{field}" for idx in range(1, 5) for field in ("roll", "pitch", "yaw")
        ]
        self.assertEqual(module.SessionV12ScoringService._orientation_format(euler_header), "euler")
        self.assertEqual(module.SessionV12ScoringService._orientation_format(euler_header[:-1]), "missing")

    def test_trajectory_error_is_not_playable(self) -> None:
        error = module.SessionV12ScoringService._trajectory_error("S1", 1, "missing_imu_orientation_columns")
        self.assertFalse(error["playback_available"])
        self.assertEqual(error["frames"], [])
        self.assertEqual(error["playback_error"], "missing_imu_orientation_columns")

    def test_trajectory_preserves_original_time_and_emg_channels(self) -> None:
        path = self.temp_dir / "rep_01.csv"
        header = ["log_seq", "time_ms"]
        header += [f"imu{idx}_{field}" for idx in range(1, 5) for field in ("roll", "pitch", "yaw", "gx", "gy", "gz")]
        header += [f"emg_{field}_{idx}" for idx in range(1, 5) for field in ("latest", "rms", "mav", "peak")]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            row = {field: 1 for field in header}
            row["log_seq"] = 42
            row["time_ms"] = 123456
            writer.writerow(row)
        trajectory = module.SessionV12ScoringService()._trajectory_from_rep_csv("S1", 1, path)
        self.assertTrue(trajectory["playback_available"])
        self.assertEqual(trajectory["frames"][0]["original_time_ms"], 123456.0)
        self.assertEqual(trajectory["frames"][0]["emg1"]["channel"], "CH1")

    def test_csv_contract_rejects_wrong_header_count(self) -> None:
        path = self.temp_dir / "bad.csv"
        path.write_text("log_seq,time_ms\n1,2\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            module.SessionV12ScoringService._validate_source_csv(path)

    def test_invalid_pose_makes_whole_rep_not_playable(self) -> None:
        path = self.temp_dir / "bad_pose.csv"
        header = ["log_seq", "time_ms"] + [
            f"imu{idx}_{field}" for idx in range(1, 5) for field in ("roll", "pitch", "yaw")
        ]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            row = {field: 1 for field in header}
            row["imu4_yaw"] = ""
            writer.writerow(row)
        trajectory = module.SessionV12ScoringService()._trajectory_from_rep_csv("S1", 1, path)
        self.assertFalse(trajectory["playback_available"])
        self.assertIn("line_2:imu4_yaw", trajectory["playback_error"])

    def test_log_seq_is_not_filled_with_zero(self) -> None:
        path = self.temp_dir / "bad_log_seq.csv"
        header = ["log_seq", "time_ms"] + [
            f"imu{idx}_{field}" for idx in range(1, 5) for field in ("roll", "pitch", "yaw")
        ]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            row = {field: 1 for field in header}
            row["log_seq"] = ""
            writer.writerow(row)
        trajectory = module.SessionV12ScoringService()._trajectory_from_rep_csv("S1", 1, path)
        self.assertFalse(trajectory["playback_available"])
        self.assertIn("line_2:log_seq", trajectory["playback_error"])

    def test_finalized_session_never_launches_doctor_local_scorer(self) -> None:
        async def scenario() -> list[tuple[str, str]]:
            service = module.SessionV12ScoringService()
            writes: list[tuple[str, str]] = []

            summary = {
                "session_id": "20260806_TASK002",
                "source_mode": "device",
                "source_origin": "pc_udp_6006_ingest",
                "task_finalized": True,
                "csv_relative_path": "sessions/20260806_TASK002/DATA_SEQ0000000001.csv",
            }
            blocker = asyncio.create_task(asyncio.sleep(0.01))
            service._tasks["20260806_TASK001"] = blocker
            with patch.object(service, "status", return_value={"session_id": summary["session_id"], "status": "not_started"}), \
                patch.object(service, "_source_sha_from_summary", return_value="sha-2"), \
                patch.object(service, "_write_status", side_effect=lambda session_id, status, *_args, **_kwargs: writes.append((session_id, status))):
                service.submit_from_runtime_summary(summary)
                self.assertEqual(writes, [])
                await blocker
                service.submit_from_runtime_summary(summary)
                await asyncio.sleep(0)
            return writes

        writes = asyncio.run(scenario())
        self.assertEqual(writes, [])

    def test_failed_same_sha_does_not_retry(self) -> None:
        service = module.SessionV12ScoringService()
        summary = {
            "session_id": "20260806_TASK003",
            "source_mode": "device",
            "source_origin": "pc_udp_6006_ingest",
            "task_finalized": True,
            "csv_relative_path": "sessions/20260806_TASK003/DATA_SEQ0000000001.csv",
        }
        with patch.object(service, "status", return_value={"session_id": summary["session_id"], "status": "failed", "source_sha256": "SHA-3"}), \
            patch.object(service, "_source_sha_from_summary", return_value="sha-3"), \
            patch.object(service, "_write_status") as write_status:
            service.submit_from_runtime_summary(summary)
        write_status.assert_not_called()

    def test_compensation_labels_are_chinese_for_lf_and_tr(self) -> None:
        self.assertEqual(module.COMPENSATION_LABELS["LF"], "躯干前倾")
        self.assertEqual(module.COMPENSATION_LABELS["TR"], "躯干旋转")
        self.assertEqual(module.COMPENSATION_LABELS["TREMOR"], "手部抖动")

    def test_compensation_payload_returns_tremor_label(self) -> None:
        payload = module.SessionV12ScoringService._compensation_payload_from_score(
            {"compensation": {"detected": True, "types": ["TREMOR"]}},
            "fixture",
        )
        self.assertTrue(payload["detected"])
        self.assertEqual(payload["types"], ["TREMOR"])
        self.assertEqual(payload["labels"], ["手部抖动"])

    def test_compensation_payload_returns_lf_label_and_source_path(self) -> None:
        source_path = self.temp_dir / "rep_01_score.json"
        payload = module.SessionV12ScoringService._compensation_payload_from_score(
            {"compensation": {"detected": True, "types": ["LF"]}},
            "fixture",
            source_path,
        )
        self.assertEqual(payload["labels"], ["躯干前倾"])
        self.assertEqual(payload["source_path"], str(source_path))

    def test_compensation_payload_preserves_lf_and_tremor(self) -> None:
        payload = module.SessionV12ScoringService._compensation_payload_from_score(
            {"compensation": {"detected": True, "types": ["LF", "TREMOR", "LF"]}},
            "fixture",
        )
        self.assertEqual(payload["types"], ["LF", "TREMOR"])
        self.assertEqual(payload["labels"], ["躯干前倾", "手部抖动"])

    def test_compensation_payload_empty_is_not_detected(self) -> None:
        payload = module.SessionV12ScoringService._compensation_payload_from_score(
            {"compensation": {"detected": False, "types": []}},
            "fixture",
        )
        self.assertFalse(payload["detected"])
        self.assertEqual(payload["types"], [])
        self.assertEqual(payload["labels"], [])

    def test_unknown_registry_task_fails_closed_before_tool_selection(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "UNKNOWN_TASK"):
            module.SessionV12ScoringService._score_profile_for_summary(
                {"task_registry_status": "unknown", "score_profile": None}
            )

    def test_resolved_task_requires_explicit_supported_score_profile(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "resolved_task_score_profile_invalid"):
            module.SessionV12ScoringService._score_profile_for_summary(
                {"task_registry_status": "resolved", "score_profile": None}
            )

    def test_true_legacy_summary_can_use_compatibility_heuristic(self) -> None:
        profile = module.SessionV12ScoringService._score_profile_for_summary(
            {"task_name": "TASK054 球类横向取放历史记录"}
        )
        self.assertEqual(profile, "lateral_transfer")

    def test_lateral_current_formal_score_overrides_stale_doctor_compensation(self) -> None:
        scoring_dir = self._write_doctor_scoring_with_compensation("20260807_TASK057", ["LF"])
        current_root = self._write_lateral_formal_score("approved_final_validation_20260808_015814", "20260807_TASK057", ["TREMOR"])
        with patch.object(module, "LATERAL_FORMAL_SCORE_ROOT", current_root):
            reps = module.SessionV12ScoringService()._read_repetitions(scoring_dir, "20260807_TASK057")
        self.assertEqual(reps[0]["compensation"]["types"], ["TREMOR"])
        self.assertEqual(reps[0]["compensation"]["labels"], ["手部抖动"])
        self.assertEqual(reps[0]["compensation"]["source"], module.LATERAL_COMPENSATION_SOURCE_CURRENT)

    def test_lateral_current_formal_score_preserves_multiple_compensation_types(self) -> None:
        scoring_dir = self._write_doctor_scoring_with_compensation("20260807_TASK058", [])
        current_root = self._write_lateral_formal_score("approved_final_validation_20260808_015814", "20260807_TASK058", ["LF", "TREMOR"])
        with patch.object(module, "LATERAL_FORMAL_SCORE_ROOT", current_root):
            reps = module.SessionV12ScoringService()._read_repetitions(scoring_dir, "20260807_TASK058")
        self.assertEqual(reps[0]["compensation"]["types"], ["LF", "TREMOR"])
        self.assertEqual(reps[0]["compensation"]["labels"], ["躯干前倾", "手部抖动"])

    def test_missing_lateral_current_score_falls_back_to_historical_compensation(self) -> None:
        scoring_dir = self._write_doctor_scoring_with_compensation("20260807_TASK099", ["TR"])
        current_root = self.temp_dir / "lateral_transfer_v1"
        current_root.mkdir()
        with patch.object(module, "LATERAL_FORMAL_SCORE_ROOT", current_root):
            reps = module.SessionV12ScoringService()._read_repetitions(scoring_dir, "20260807_TASK099")
        self.assertEqual(reps[0]["compensation"]["types"], ["TR"])
        self.assertEqual(reps[0]["compensation"]["source"], module.LATERAL_COMPENSATION_SOURCE_HISTORICAL)

    def test_lateral_current_score_resolver_uses_latest_approved_validation_dir(self) -> None:
        root = self.temp_dir / "lateral_transfer_v1"
        self._write_lateral_formal_score("approved_final_validation_20260808_015451", "S1", ["LF"], root=root)
        self._write_lateral_formal_score("approved_final_validation_20260808_015814", "S1", ["TREMOR"], root=root)
        with patch.object(module, "LATERAL_FORMAL_SCORE_ROOT", root):
            score, path = module.SessionV12ScoringService._current_lateral_formal_score("S1", 1)
        self.assertIsNotNone(score)
        self.assertIsNotNone(path)
        self.assertEqual(score["compensation"]["types"], ["TREMOR"])
        self.assertIn("approved_final_validation_20260808_015814", str(path))

    def test_lateral_current_score_resolver_does_not_hardcode_specific_task_ids(self) -> None:
        source = Path(module.__file__).read_text(encoding="utf-8")
        for task_id in ("TASK054", "TASK055", "TASK056", "TASK057", "TASK058"):
            self.assertNotIn(task_id, source)

    def test_runtime_reconcile_retries_cached_eligible_summary_when_signature_unchanged(self) -> None:
        runtime_root = self.temp_dir / "runtime"
        session_dir = runtime_root / "sessions" / "20260806_TASK004"
        session_dir.mkdir(parents=True)
        task = {
            "task_name": "喝水动作模拟",
            "csv_file": "DATA_SEQ0000000001.csv",
            "start_time": "2026-08-06T09:00:00+00:00",
            "end_time": "2026-08-06T09:01:00+00:00",
            "end_reason": "completed",
        }
        input_dir = session_dir / "input"
        input_dir.mkdir()
        (input_dir / "task.json").write_text(json.dumps(task), encoding="utf-8")
        with (input_dir / "DATA_SEQ0000000001.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(runtime_module.CSV_COLUMNS)
            writer.writerow([0 for _ in runtime_module.CSV_COLUMNS])

        class FakeSessionV12:
            def __init__(self) -> None:
                self.submit_count = 0

            def submit_from_runtime_summary(self, summary: dict) -> None:
                self.submit_count += 1

            def status(self, session_id: str) -> dict:
                if self.submit_count < 2:
                    return {"session_id": session_id, "status": "not_started"}
                return {"session_id": session_id, "status": "pending", "baseline_version": "drink_water_v2.1.2"}

        fake = FakeSessionV12()
        service = runtime_module.PcLocalRuntimeService()
        with patch.object(runtime_module, "TRAINING_DATA_ROOT", runtime_root), \
            patch.object(runtime_module, "session_v12_scoring_service", fake), \
            patch.object(runtime_module.device_history_store, "save_session"):
            service._reconcile_score_runtime_sessions()
            self.assertEqual(fake.submit_count, 1)
            self.assertNotIn("session_v12_status", service._sessions["20260806_TASK004"])
            service._reconcile_score_runtime_sessions()

        self.assertEqual(fake.submit_count, 2)
        self.assertEqual(service._sessions["20260806_TASK004"]["session_v12_status"], "pending")

    def test_sha_helpers_are_case_insensitive(self) -> None:
        self.assertEqual(module._normalize_sha256(" ABCdef "), "abcdef")
        self.assertTrue(module._sha256_equal("ABCDEF", "abcdef"))
        self.assertFalse(module._sha256_equal("abcdef", "abcdee"))

    def test_scoring_freshness_reports_current_versions(self) -> None:
        service = module.SessionV12ScoringService()
        status = {
            "source_sha256": "sha-current",
            "score_version": module.EXPECTED_SCORE_VERSION,
            "baseline_version": module.EXPECTED_BASELINE_VERSION,
            "formal_template_sha256": module.EXPECTED_FORMAL_TEMPLATE_SHA256,
            "sensor_mapping_version": module.EXPECTED_SENSOR_MAPPING_VERSION,
            "detector_rule_version": module.EXPECTED_DETECTOR_RULE_VERSION,
        }
        current = {**status, "source_sha256": "SHA-CURRENT", "formal_template_sha256": module.EXPECTED_FORMAL_TEMPLATE_SHA256.lower()}
        with patch.object(service, "_current_freshness_versions", return_value=current):
            freshness = service._scoring_freshness("20260806_TASK005", None, status)
        self.assertEqual(freshness["status"], "current")
        self.assertEqual(freshness["reasons"], [])

    def test_scoring_freshness_reports_stale_source_sha(self) -> None:
        service = module.SessionV12ScoringService()
        status = self._fresh_status("sha-old")
        current = {**status, "source_sha256": "sha-new"}
        with patch.object(service, "_current_freshness_versions", return_value=current):
            freshness = service._scoring_freshness("20260806_TASK006", None, status)
        self.assertEqual(freshness["status"], "stale")
        self.assertIn("mismatch_source_sha256", freshness["reasons"])

    def test_scoring_freshness_reports_stale_formal_sha(self) -> None:
        service = module.SessionV12ScoringService()
        status = self._fresh_status("sha-current")
        current = {**status, "formal_template_sha256": "0" * 64}
        with patch.object(service, "_current_freshness_versions", return_value=current):
            freshness = service._scoring_freshness("20260806_TASK006", None, status)
        self.assertEqual(freshness["status"], "stale")
        self.assertIn("mismatch_formal_template_sha256", freshness["reasons"])

    def test_formal_baseline_precheck_accepts_sha_case_only_difference(self) -> None:
        template = self._write_formal_template()
        with patch.object(module, "BASELINE_TEMPLATE_PATH", template), \
            patch.object(module, "_sha256_file", return_value=module.EXPECTED_FORMAL_TEMPLATE_SHA256.lower()):
            result = module.SessionV12ScoringService.formal_baseline_precheck()
        self.assertTrue(result["ok"])
        self.assertIsNone(result["reason"])

    def test_formal_baseline_precheck_rejects_explicit_version_mismatch(self) -> None:
        template = self._write_formal_template({"baseline_version": "old"})
        with patch.object(module, "BASELINE_TEMPLATE_PATH", template), \
            patch.object(module, "_sha256_file", return_value=module.EXPECTED_FORMAL_TEMPLATE_SHA256):
            result = module.SessionV12ScoringService.formal_baseline_precheck()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "baseline_version_mismatch")

    def test_formal_baseline_precheck_rejects_formal_sha_mismatch(self) -> None:
        template = self._write_formal_template()
        with patch.object(module, "BASELINE_TEMPLATE_PATH", template), \
            patch.object(module, "_sha256_file", return_value="0" * 64):
            result = module.SessionV12ScoringService.formal_baseline_precheck()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "formal_template_sha256_mismatch")

    def test_has_completed_scoring_uses_case_insensitive_sha(self) -> None:
        scoring_dir = self.temp_dir / "scoring"
        scoring_dir.mkdir()
        (scoring_dir / "session_summary.json").write_text("{}", encoding="utf-8")
        (scoring_dir / "session_v12_source_manifest.json").write_text(
            json.dumps({"source_sha256": "ABCDEF"}), encoding="utf-8"
        )
        self.assertTrue(module.SessionV12ScoringService._has_completed_scoring(scoring_dir, "abcdef"))

    def test_completed_same_sha_is_idempotent_even_when_case_differs(self) -> None:
        service = module.SessionV12ScoringService()
        summary = {
            "session_id": "20260806_TASK011",
            "source_mode": "device",
            "source_origin": "pc_udp_6006_ingest",
            "task_finalized": True,
            "csv_relative_path": "sessions/20260806_TASK011/DATA_SEQ0000000001.csv",
        }
        with patch.object(service, "status", return_value={"session_id": summary["session_id"], "status": "completed", "source_sha256": "ABCDEF"}), \
            patch.object(service, "_source_sha_from_summary", return_value="abcdef"), \
            patch.object(service, "_write_status") as write_status:
            service.submit_from_runtime_summary(summary)
        write_status.assert_not_called()

    def test_completed_different_sha_becomes_conflict_failed(self) -> None:
        service = module.SessionV12ScoringService()
        summary = {
            "session_id": "20260806_TASK012",
            "source_mode": "device",
            "source_origin": "pc_udp_6006_ingest",
            "task_finalized": True,
            "csv_relative_path": "sessions/20260806_TASK012/DATA_SEQ0000000001.csv",
        }
        with patch.object(service, "status", return_value={"session_id": summary["session_id"], "status": "completed", "source_sha256": "old"}), \
            patch.object(service, "_source_sha_from_summary", return_value="new"), \
            patch.object(service, "_write_status") as write_status:
            service.submit_from_runtime_summary(summary)
        write_status.assert_not_called()

    def test_rescore_rejects_when_another_session_is_running(self) -> None:
        service = module.SessionV12ScoringService()

        class RunningTask:
            def done(self) -> bool:
                return False

        service._tasks["20260806_TASK007"] = RunningTask()  # type: ignore[assignment]
        result = service.rescore("20260806_TASK008")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "score_service_authority_required")

    def test_source_csv_for_stored_session_reads_only_unified_input(self) -> None:
        service = module.SessionV12ScoringService()
        resolver = module.TrainingDataResolver(training_root=self.temp_dir / "unified")
        input_csv = resolver.session_input_file_path(
            "20260806_TASK009", "DATA_SEQ0000000001.csv"
        )
        input_csv.parent.mkdir(parents=True)
        input_csv.write_text("header\n", encoding="utf-8")
        session = {
            "session_id": "20260806_TASK009",
            "csv_file": "DATA_SEQ0000000001.csv",
            "csv_relative_path": "sessions/20260806_TASK009/DATA_SEQ0000000001.csv",
        }
        with patch.object(module, "_score_path_resolver", return_value=resolver):
            self.assertEqual(
                service._source_csv_for_stored_session("20260806_TASK009", session),
                input_csv,
            )

    def test_rescore_rejects_when_actual_source_sha_differs_from_history(self) -> None:
        service, session_dir, source_csv = self._prepared_rescore_session("20260806_TASK010")
        with patch.object(module.device_history_store, "session_directory", return_value=session_dir), \
            patch.object(service, "formal_baseline_precheck", return_value={"ok": True, "reason": None}), \
            patch.object(service, "_source_csv_for_stored_session", return_value=source_csv), \
            patch.object(service, "_validate_source_csv"), \
            patch.object(module, "_sha256_file", return_value="actual-sha"), \
            patch.object(service, "_historical_source_sha", return_value="historical-sha"):
            result = service.rescore("20260806_TASK010")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "score_service_authority_required")

    def test_rescore_returns_event_loop_unavailable_after_sync_checks_pass(self) -> None:
        service, session_dir, source_csv = self._prepared_rescore_session("20260806_TASK013")
        with patch.object(module.device_history_store, "session_directory", return_value=session_dir), \
            patch.object(service, "formal_baseline_precheck", return_value={"ok": True, "reason": None}), \
            patch.object(service, "_source_csv_for_stored_session", return_value=source_csv), \
            patch.object(service, "_validate_source_csv"), \
            patch.object(module, "_sha256_file", return_value="same-sha"), \
            patch.object(service, "_historical_source_sha", return_value="SAME-SHA"):
            result = service.rescore("20260806_TASK013")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "score_service_authority_required")

    def test_rescore_without_historical_sha_marks_legacy_unverifiable(self) -> None:
        service, session_dir, source_csv = self._prepared_rescore_session("20260806_TASK014")

        class FakeTask:
            def done(self) -> bool:
                return False

        class FakeLoop:
            def create_task(self, coroutine, name: str | None = None) -> FakeTask:
                coroutine.close()
                return FakeTask()

        with patch.object(module.device_history_store, "session_directory", return_value=session_dir), \
            patch.object(service, "formal_baseline_precheck", return_value={"ok": True, "reason": None}), \
            patch.object(service, "_source_csv_for_stored_session", return_value=source_csv), \
            patch.object(service, "_validate_source_csv"), \
            patch.object(module, "_sha256_file", return_value="current-sha"), \
            patch.object(service, "_historical_source_sha", return_value=None), \
            patch.object(module.asyncio, "get_running_loop", return_value=FakeLoop()), \
            patch.object(service, "_write_status") as write_status:
            result = service.rescore("20260806_TASK014")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "score_service_authority_required")
        write_status.assert_not_called()

    def _fresh_status(self, source_sha: str) -> dict[str, str]:
        return {
            "source_sha256": source_sha,
            "score_version": module.EXPECTED_SCORE_VERSION,
            "baseline_version": module.EXPECTED_BASELINE_VERSION,
            "formal_template_sha256": module.EXPECTED_FORMAL_TEMPLATE_SHA256,
            "sensor_mapping_version": module.EXPECTED_SENSOR_MAPPING_VERSION,
            "detector_rule_version": module.EXPECTED_DETECTOR_RULE_VERSION,
        }

    def _write_formal_template(self, overrides: dict[str, str] | None = None) -> Path:
        task12 = {
            "baseline_version": module.EXPECTED_BASELINE_VERSION,
            "segmentation_version": module.EXPECTED_SEGMENTATION_VERSION,
            "sensor_mapping_version": module.EXPECTED_SENSOR_MAPPING_VERSION,
        }
        task12.update(overrides or {})
        template = self.temp_dir / "MHH_healthy_template.json"
        template.write_text(json.dumps({"12": task12}), encoding="utf-8")
        return template

    def _prepared_rescore_session(self, session_id: str) -> tuple[module.SessionV12ScoringService, Path, Path]:
        service = module.SessionV12ScoringService()
        session_dir = self.temp_dir / session_id
        session_dir.mkdir()
        (session_dir / "session.json").write_text(
            json.dumps({
                "session_id": session_id,
                "source_mode": "device",
                "source_origin": "pc_udp_6006_ingest",
            }),
            encoding="utf-8",
        )
        source_csv = session_dir / "raw_session.csv"
        source_csv.write_text("placeholder", encoding="utf-8")
        return service, session_dir, source_csv

    def _write_doctor_scoring_with_compensation(self, session_id: str, types: list[str]) -> Path:
        scoring_dir = self.temp_dir / "doctor_sessions" / session_id / "scoring"
        score_dir = scoring_dir / "per_rep_scores"
        rep_dir = scoring_dir / "repetitions"
        score_dir.mkdir(parents=True)
        rep_dir.mkdir()
        (score_dir / "rep_01_score.json").write_text(
            json.dumps(
                {
                    "valid": True,
                    "final_score": 88.0,
                    "subscores": {},
                    "compensation": {"detected": bool(types), "types": types},
                    "feedback": [],
                }
            ),
            encoding="utf-8",
        )
        (rep_dir / "rep_01.metadata.json").write_text(json.dumps({"duration_ms": 1000}), encoding="utf-8")
        return scoring_dir

    def _write_lateral_formal_score(
        self,
        validation_dir_name: str,
        session_id: str,
        types: list[str],
        root: Path | None = None,
    ) -> Path:
        root = root or (self.temp_dir / "lateral_transfer_v1")
        score_dir = root / validation_dir_name / session_id / "per_rep_scores"
        score_dir.mkdir(parents=True, exist_ok=True)
        (score_dir / "rep_01_score.json").write_text(
            json.dumps({"valid": True, "compensation": {"detected": bool(types), "types": types}}),
            encoding="utf-8",
        )
        return root


if __name__ == "__main__":
    unittest.main()
