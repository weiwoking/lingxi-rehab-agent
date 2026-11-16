"""Offline Phase 4.2B3 score-query contract and source-boundary checks.

The tests use only temporary directories and repository source text.  They do
not access /sdcard, build firmware, open network connections, or mutate runtime
data.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
REPO = COMPONENT.parents[3]
SRC = COMPONENT / "src"
INTEGRATION = COMPONENT.parent / "rehab_integration"
CAPABILITIES = INTEGRATION / "src" / "rehab_capabilities.c"
SKILL = INTEGRATION / "skills" / "rehab-control" / "SKILL.md"

FULL_SESSION = re.compile(r"^[0-9]{8}_TASK[0-9]{3}$")
SHORT_SESSION = re.compile(r"^TASK[0-9]{3}$")
TASK_FIELDS = {
    "task_name",
    "csv_file",
    "start_time",
    "end_time",
    "start_log_seq",
    "end_log_seq",
    "end_reason",
}
STATUS_FIELDS = {
    "schema_version",
    "state",
    "attempt",
    "pc_job_id",
    "updated_at",
    "last_error",
}
RESULT_FIELDS = {
    "schema_version",
    "session_id",
    "task_name",
    "task_number",
    "valid",
    "invalid_reason",
    "average_score",
    "max_score",
    "min_score",
    "repetition_count",
    "target_repetition_count",
    "consistency_score",
    "data_quality_level",
    "warnings",
    "compensation_detected",
    "compensation_action_count",
    "score_version",
    "model_version",
    "completed_at",
    "pc_job_id",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def valid_task() -> dict[str, object]:
    return {
        "task_name": "水杯横向取放训练（一级）",
        "csv_file": "DATA_SEQ0000000001.csv",
        "start_time": "2026-07-14T16:24:00+08:00",
        "end_time": "2026-07-14T16:25:14+08:00",
        "start_log_seq": 1,
        "end_log_seq": 3700,
        "end_reason": "user_stop",
    }


def make_session(root: Path, session_id: str, *, task: dict[str, object] | None = None) -> Path:
    session = root / session_id
    session.mkdir()
    (session / "task.json").write_text(
        json.dumps(task if task is not None else valid_task(), ensure_ascii=False),
        encoding="utf-8",
    )
    return session


def is_valid_session(path: Path) -> bool:
    if not path.is_dir() or not FULL_SESSION.fullmatch(path.name):
        return False
    try:
        task = json.loads((path / "task.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(task, dict) and set(task) == TASK_FIELDS


def resolve(root: Path, session_ref: str | None = None) -> str:
    if session_ref and not (
        FULL_SESSION.fullmatch(session_ref) or SHORT_SESSION.fullmatch(session_ref)
    ):
        raise ValueError("invalid_session_ref")
    candidates = sorted(path.name for path in root.iterdir() if is_valid_session(path))
    if session_ref and SHORT_SESSION.fullmatch(session_ref):
        candidates = [name for name in candidates if name[9:] == session_ref]
        if len(candidates) > 1:
            raise LookupError("session_ref_ambiguous")
    elif session_ref:
        candidates = [name for name in candidates if name == session_ref]
    if not candidates:
        raise FileNotFoundError("session_not_found")
    return candidates[-1]


def valid_status(state: str, *, last_error: str | None = None) -> dict[str, object]:
    return {
        "schema_version": "rehab_score_status_v1",
        "state": state,
        "attempt": 1,
        "pc_job_id": "20260714_TASK006" if state == "succeeded" else None,
        "updated_at": "2026-07-14T23:48:05+08:00",
        "last_error": last_error,
    }


def valid_result(session_id: str = "20260714_TASK006") -> dict[str, object]:
    return {
        "schema_version": "rehab_score_result_v1",
        "session_id": session_id,
        "task_name": "水杯横向取放训练（一级）",
        "task_number": 12,
        "valid": True,
        "invalid_reason": None,
        "average_score": 48.71,
        "max_score": 51.0,
        "min_score": 46.34,
        "repetition_count": 6,
        "target_repetition_count": 10,
        "consistency_score": 98.46,
        "data_quality_level": "use_with_caution",
        "warnings": ["large sampling gaps over 100 ms detected"],
        "compensation_detected": False,
        "compensation_action_count": 0,
        "score_version": "v3.0",
        "model_version": "weighted_dtw_imu_plus_emg_v1.0",
        "completed_at": "2026-07-14T15:47:59.723453+00:00",
        "pc_job_id": session_id,
    }


def read_query(root: Path, session_ref: str | None = None) -> dict[str, object]:
    session_id = resolve(root, session_ref)
    session = root / session_id
    status_path = session / "score_status.json"
    if not status_path.exists():
        raise FileNotFoundError("score_status_missing")
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("score_status_invalid") from error
    if not isinstance(status, dict) or set(status) != STATUS_FIELDS:
        raise ValueError("score_status_invalid")
    response: dict[str, object] = {
        "session_id": session_id,
        "state": status["state"],
        "last_error": status["last_error"],
        "result_available": False,
        "result": None,
    }
    if status["state"] != "succeeded":
        return response
    result_path = session / "score_result.json"
    if not result_path.exists():
        raise FileNotFoundError("score_result_missing")
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("score_result_invalid") from error
    if (
        not isinstance(result, dict)
        or set(result) != RESULT_FIELDS
        or result["session_id"] != session_id
        or result["pc_job_id"] != status["pc_job_id"]
        or result["task_name"] != valid_task()["task_name"]
    ):
        raise ValueError("score_result_invalid")
    response["result_available"] = True
    response["result"] = result
    return response


class SessionResolutionChecks(unittest.TestCase):
    def test_latest_uses_session_id_order_not_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            older = make_session(root, "20260714_TASK004")
            latest = make_session(root, "20260714_TASK006")
            os.utime(older, (2_000_000_000, 2_000_000_000))
            os.utime(latest, (1_000_000_000, 1_000_000_000))
            self.assertEqual("20260714_TASK006", resolve(root, None))
            self.assertEqual("20260714_TASK006", resolve(root, ""))

    def test_latest_skips_non_session_and_invalid_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_session(root, "20260714_TASK004")
            make_session(root, "20260714_TASK999", task={"task_name": "bad"})
            (root / "notes.txt").write_text("not a session", encoding="utf-8")
            self.assertEqual("20260714_TASK004", resolve(root))

    def test_full_and_unique_short_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_session(root, "20260714_TASK006")
            self.assertEqual("20260714_TASK006", resolve(root, "20260714_TASK006"))
            self.assertEqual("20260714_TASK006", resolve(root, "TASK006"))

    def test_short_reference_is_ambiguous_across_dates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_session(root, "20260714_TASK006")
            make_session(root, "20260715_TASK006")
            with self.assertRaisesRegex(LookupError, "session_ref_ambiguous"):
                resolve(root, "TASK006")

    def test_invalid_and_missing_reference_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "invalid_session_ref"):
                resolve(root, "../TASK006")
            with self.assertRaisesRegex(FileNotFoundError, "session_not_found"):
                resolve(root, "20260714_TASK006")


class ContractAndSourceChecks(unittest.TestCase):
    def test_succeeded_query_returns_validated_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = make_session(root, "20260714_TASK006")
            (session / "score_status.json").write_text(
                json.dumps(valid_status("succeeded")), encoding="utf-8"
            )
            (session / "score_result.json").write_text(
                json.dumps(valid_result(), ensure_ascii=False), encoding="utf-8"
            )
            response = read_query(root)
            self.assertTrue(response["result_available"])
            self.assertEqual(48.71, response["result"]["average_score"])

    def test_processing_states_return_no_result(self) -> None:
        for state in ("pending", "uploading", "accepted", "scoring"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                session = make_session(root, "20260714_TASK006")
                (session / "score_status.json").write_text(
                    json.dumps(valid_status(state)), encoding="utf-8"
                )
                response = read_query(root)
                self.assertFalse(response["result_available"])
                self.assertIsNone(response["result"])

    def test_failed_returns_last_error_without_reading_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = make_session(root, "20260714_TASK006")
            (session / "score_status.json").write_text(
                json.dumps(valid_status("failed", last_error="pc_gateway_unavailable")),
                encoding="utf-8",
            )
            response = read_query(root)
            self.assertEqual("failed", response["state"])
            self.assertEqual("pc_gateway_unavailable", response["last_error"])
            self.assertFalse(response["result_available"])

    def test_exact_task002_never_returns_task006_score(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_session = make_session(root, "20260714_TASK006")
            (old_session / "score_status.json").write_text(
                json.dumps(valid_status("succeeded")), encoding="utf-8"
            )
            (old_session / "score_result.json").write_text(
                json.dumps(valid_result(), ensure_ascii=False), encoding="utf-8"
            )

            drinking_task = valid_task()
            drinking_task["task_name"] = "水杯拿取与饮水训练（二级）"
            current = make_session(root, "20260721_TASK002", task=drinking_task)
            (current / "score_status.json").write_text(
                json.dumps(valid_status("failed", last_error="unsupported_task")),
                encoding="utf-8",
            )

            response = read_query(root, "20260721_TASK002")
            self.assertEqual("20260721_TASK002", response["session_id"])
            self.assertEqual("failed", response["state"])
            self.assertEqual("unsupported_task", response["last_error"])
            self.assertFalse(response["result_available"])
            self.assertIsNone(response["result"])

            (current / "score_status.json").unlink()
            with self.assertRaisesRegex(FileNotFoundError, "score_status_missing"):
                read_query(root, "20260721_TASK002")

    def test_missing_status_result_and_mismatched_identity_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = make_session(root, "20260714_TASK006")
            with self.assertRaisesRegex(FileNotFoundError, "score_status_missing"):
                read_query(root)
            invalid_status = valid_status("pending")
            invalid_status["unexpected"] = True
            (session / "score_status.json").write_text(
                json.dumps(invalid_status), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "score_status_invalid"):
                read_query(root)
            (session / "score_status.json").write_text(
                json.dumps(valid_status("succeeded")), encoding="utf-8"
            )
            with self.assertRaisesRegex(FileNotFoundError, "score_result_missing"):
                read_query(root)
            (session / "score_result.json").write_text(
                json.dumps(valid_result("20260714_TASK005"), ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "score_result_invalid"):
                read_query(root)

    def test_frozen_status_and_result_contracts_remain_exact(self) -> None:
        self.assertEqual(6, len(STATUS_FIELDS))
        self.assertEqual(20, len(RESULT_FIELDS))
        contract = read(SRC / "rehab_score_bridge_contract.c")
        for field in STATUS_FIELDS | RESULT_FIELDS:
            self.assertIn(f'"{field}"', contract)
        self.assertIn("rehab_score_contract_validate_result(json,", contract)

    def test_query_is_sd_only_bounded_and_read_only(self) -> None:
        query = read(SRC / "rehab_score_bridge_query.c")
        storage = read(SRC / "rehab_score_bridge_storage.c")
        private = read(SRC / "rehab_score_bridge_priv.h")
        self.assertIn("opendir(REHAB_SCORE_SESSION_ROOT)", query)
        self.assertIn("rehab_score_storage_load_session_metadata", query)
        self.assertIn("rehab_score_storage_load_status", query)
        self.assertIn("rehab_score_storage_read_result", query)
        self.assertIn("REHAB_SCORE_RESULT_MAX_BYTES", storage)
        self.assertIn("(64U * 1024U)", private)
        for forbidden in (
            "rehab_score_http_",
            "rehab_score_bridge_retry(",
            "rehab_score_bridge_notify_finalized(",
            "rehab_score_storage_write_",
            "fwrite(",
            "unlink(",
            "rename(",
        ):
            self.assertNotIn(forbidden, query)
        self.assertNotIn("csv_path", query)
        self.assertNotIn("st_mtime", query)

    def test_query_distinguishes_all_required_errors_and_states(self) -> None:
        query = read(SRC / "rehab_score_bridge_query.c")
        header = read(COMPONENT / "include" / "rehab_score_bridge.h")
        for code in (
            "invalid_session_ref",
            "session_not_found",
            "session_ref_ambiguous",
            "score_status_missing",
            "score_status_invalid",
            "score_result_missing",
            "score_result_invalid",
            "storage_not_ready",
            "internal_error",
        ):
            self.assertIn(f'"{code}"', query)
        for state in (
            "REHAB_SCORE_STATE_PENDING",
            "REHAB_SCORE_STATE_UPLOADING",
            "REHAB_SCORE_STATE_ACCEPTED",
            "REHAB_SCORE_STATE_SCORING",
            "REHAB_SCORE_STATE_SUCCEEDED",
            "REHAB_SCORE_STATE_FAILED",
        ):
            self.assertIn(state, header)
        self.assertIn("status->state != REHAB_SCORE_STATE_SUCCEEDED", query)

    def test_result_identity_is_revalidated_before_copy(self) -> None:
        contract = read(SRC / "rehab_score_bridge_contract.c")
        parse_start = contract.index("esp_err_t rehab_score_contract_parse_result")
        parse_body = contract[parse_start:]
        validation = parse_body.index("rehab_score_contract_validate_result")
        parsing = parse_body.index("cJSON_ParseWithOpts", validation)
        self.assertLess(validation, parsing)
        for comparison in (
            'strcmp(session->valuestring, session_id) == 0',
            'strcmp(job->valuestring, job_id) == 0',
            'strcmp(name->valuestring, task_name) == 0',
        ):
            self.assertIn(comparison, contract)

    def test_capability_accepts_empty_full_and_short_only(self) -> None:
        capabilities = read(CAPABILITIES)
        descriptor = capabilities[capabilities.index('.id = "rehab_score_query"') :]
        self.assertIn('"additionalProperties":false', descriptor.replace("\\\"", '"'))
        self.assertIn("rehab_parse_score_query", capabilities)
        self.assertIn('strcmp(field->string, "session_id") != 0', capabilities)
        self.assertIn("field_count > 1", capabilities)
        for payload in ({}, {"session_id": "20260714_TASK006"}, {"session_id": "TASK006"}):
            self.assertLessEqual(len(payload), 1)
        self.assertNotEqual({"unexpected": True}.keys(), {"session_id"})

    def test_capability_output_is_compact_and_does_not_expose_paths(self) -> None:
        capabilities = read(CAPABILITIES)
        start = capabilities.index("static bool rehab_add_score_result(")
        end = capabilities.index("static esp_err_t rehab_score_query_execute(", start)
        renderer = capabilities[start:end]
        for field in (
            "rehab_score_query_v1",
            "session_id",
            "task_name",
            "state",
            "attempt",
            "updated_at",
            "last_error",
            "result_available",
            "result",
            "warnings",
            "compensation_detected",
        ):
            self.assertIn(field, renderer)
        for forbidden in ("/sdcard", "csv_path", "session_path", "http", "stack"):
            self.assertNotIn(forbidden, renderer.lower())

    def test_skill_reuses_one_path_and_preserves_training_controls(self) -> None:
        skill = read(SKILL)
        self.assertIn('"cap_groups": [', skill)
        self.assertEqual(1, skill.count('"rehab_integration"'))
        self.assertIn("rehab_score_query({})", skill)
        self.assertIn('rehab_score_query({"session_id":"20260714_TASK006"})', skill)
        self.assertIn('rehab_score_query({"session_id":"TASK006"})', skill)
        self.assertIn("任意历史 Session", skill)
        self.assertIn("不按文件修改时间", skill)
        self.assertNotIn("retry", skill.lower())
        for capability in (
            "rehab_training_start",
            "rehab_training_status",
            "rehab_training_stop",
        ):
            self.assertIn(capability, skill)

    def test_skill_preserves_medical_and_failure_boundaries(self) -> None:
        skill = read(SKILL)
        for marker in (
            "不是医学诊断",
            "数据质量需要谨慎使用",
            "不能忽略警告",
            "算法检测到/未检测到代偿特征",
            "不对历史失败记录重新评分",
            "不能自动触发评分",
        ):
            self.assertIn(marker, skill)
        for error in (
            "pc_gateway_unavailable",
            "pc_unreachable",
            "pc_service_not_ready",
            "unsupported_task",
            "invalid_task_contract",
            "job_create_failed",
            "csv_upload_failed",
            "job_status_failed",
            "result_contract_invalid",
            "sd_write_failed",
            "scoring_timeout",
        ):
            self.assertIn(error, skill)


if __name__ == "__main__":
    unittest.main(verbosity=2)
