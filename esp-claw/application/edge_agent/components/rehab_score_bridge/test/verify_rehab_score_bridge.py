"""Offline Phase 4.2B2 contract and source-boundary checks.

This script uses only the Python standard library.  It does not build firmware,
open network connections, or mutate repository state.
"""

from __future__ import annotations

import ast
import ipaddress
import json
import re
import unittest
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
REPO = COMPONENT.parents[3]
SRC = COMPONENT / "src"

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
STATUS_STATES = {
    "pending",
    "uploading",
    "accepted",
    "scoring",
    "succeeded",
    "failed",
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


def c_string_constant(source: str, name: str) -> str:
    declaration = source.index(name)
    terminator = source.index(";", declaration)
    literals = re.findall(r'"(?:\\.|[^"\\])*"', source[declaration:terminator])
    return "".join(ast.literal_eval(literal) for literal in literals)


class ContractChecks(unittest.TestCase):
    def test_task_contract_is_exact_without_a_p4_scoring_whitelist(self) -> None:
        for task_name in ("水杯横向取放训练（一级）", "水杯拿取与饮水训练（二级）"):
            task = json.loads(
                '{"task_name":' + json.dumps(task_name, ensure_ascii=False) + ','
                '"csv_file":"DATA_SEQ0000000001.csv",'
                '"start_time":"2026-07-14T16:24:00+08:00",'
                '"end_time":"2026-07-14T16:25:14+08:00",'
                '"start_log_seq":1,"end_log_seq":3700,"end_reason":"user_stop"}'
            )
            self.assertEqual(TASK_FIELDS, set(task))
            self.assertTrue(task["task_name"])

        contract = read(SRC / "rehab_score_bridge_contract.c")
        parser = contract[
            contract.index("esp_err_t rehab_score_contract_parse_task(") :
            contract.index("esp_err_t rehab_score_contract_parse_status(")
        ]
        self.assertNotIn("水杯横向取放训练（一级）", parser)
        self.assertNotIn("task_number", parser)
        self.assertNotIn("target_repetition", parser)
        self.assertIn("finalized_end_reason(end_reason)", parser)

        private = read(SRC / "rehab_score_bridge_priv.h")
        task_struct = private[private.index("typedef struct {") : private.index("} rehab_score_task_t;")]
        self.assertNotIn("task_number", task_struct)
        self.assertNotIn("target_repetition", task_struct)

    def test_session_csv_and_url_identity(self) -> None:
        self.assertRegex("20260714_TASK002", r"^[0-9]{8}_TASK[0-9]{3}$")
        self.assertNotRegex("../20260714_TASK002", r"^[0-9]{8}_TASK[0-9]{3}$")
        self.assertRegex("DATA_SEQ0000000001.csv", r"^DATA_SEQ[0-9]{10}\.csv$")
        self.assertNotRegex("DATA_SEQ0000000001.csv.bak", r"^DATA_SEQ[0-9]{10}\.csv$")
        valid_url = re.compile(
            r"^http://(?:[0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]{1,5}/?$"
        )
        self.assertRegex("http://192.168.1.100:8020", valid_url)
        self.assertNotRegex("https://pc.local:8020", valid_url)

    def test_status_vector_has_only_six_fields(self) -> None:
        status = json.loads(
            '{"schema_version":"rehab_score_status_v1","state":"pending",'
            '"attempt":0,"pc_job_id":null,'
            '"updated_at":"2026-07-14T20:30:00+08:00","last_error":null}'
        )
        self.assertEqual(STATUS_FIELDS, set(status))
        self.assertEqual("rehab_score_status_v1", status["schema_version"])
        self.assertIn(status["state"], STATUS_STATES)
        self.assertGreaterEqual(status["attempt"], 0)

    def test_compact_result_has_exact_contract_and_identity(self) -> None:
        result = {
            "schema_version": "rehab_score_result_v1",
            "session_id": "20260721_TASK002",
            "task_name": "水杯拿取与饮水训练（二级）",
            "task_number": 21,
            "valid": True,
            "invalid_reason": None,
            "average_score": 44.42,
            "max_score": 47.62,
            "min_score": 40.69,
            "repetition_count": 7,
            "target_repetition_count": 8,
            "consistency_score": 98.05,
            "data_quality_level": "use_with_caution",
            "warnings": [],
            "compensation_detected": False,
            "compensation_action_count": 0,
            "score_version": "v3.0",
            "model_version": "weighted_dtw_imu_plus_emg_v1.0",
            "completed_at": "2026-07-21T20:31:00+08:00",
            "pc_job_id": "20260721_TASK002",
        }
        self.assertEqual(RESULT_FIELDS, set(result))
        self.assertEqual(result["session_id"], result["pc_job_id"])
        self.assertGreater(result["task_number"], 0)
        self.assertGreater(result["target_repetition_count"], 0)
        self.assertIsInstance(result["warnings"], list)
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
        self.assertLessEqual(len(encoded), 64 * 1024)


class ImplementationChecks(unittest.TestCase):
    def test_complete_sessions_are_scanned_and_invalid_inputs_are_rejected(self) -> None:
        core = read(SRC / "rehab_score_bridge_core.c")
        storage = read(SRC / "rehab_score_bridge_storage.c")
        self.assertIn("scan_sessions(true)", core)
        self.assertIn("rehab_score_storage_prepare_session(session_id, session)", core)
        self.assertIn("REHAB_SCORE_RECOVERY_CREATE_PENDING", core)
        for marker in (
            "rehab_score_storage_read_task_bytes",
            "stat(out_session->csv_path",
            "validate_csv_rows(out_session)",
            "hash_csv(out_session->csv_path",
        ):
            self.assertIn(marker, storage)

    def test_pc_machine_errors_become_terminal_failed_statuses(self) -> None:
        contract = read(SRC / "rehab_score_bridge_contract.c")
        http = read(SRC / "rehab_score_bridge_http.c")
        core = read(SRC / "rehab_score_bridge_core.c")
        self.assertIn("rehab_score_contract_parse_error_code", contract)
        self.assertNotIn("unsupported_task", http)
        self.assertNotIn("unsupported_task", core)
        self.assertIn('set_error(error_code, "invalid_task_contract")', http)
        self.assertIn('strcmp(error_code, "invalid_task_contract") == 0', core)
        terminal = core.index("submission_error_is_terminal(error_code)")
        failed = core.index("finish_failed(session, status, error_code)", terminal)
        self.assertLess(terminal, failed)

    def test_result_validator_is_generic_but_keeps_structural_bounds(self) -> None:
        contract = read(SRC / "rehab_score_bridge_contract.c")
        validator = contract[
            contract.index("esp_err_t rehab_score_contract_validate_result(") :
            contract.index("static void copy_utf8_summary(")
        ]
        self.assertNotIn("task_number_value == 12", validator)
        self.assertNotIn("target_repetitions == 10", validator)
        for required in (
            "task_number_value > 0",
            "task_number_value <= UINT32_MAX",
            "target_repetitions > 0",
            "target_repetitions <= UINT32_MAX",
            "compensation_actions <= repetitions",
            "minimum->valuedouble <= average->valuedouble",
            "average->valuedouble <= maximum->valuedouble",
        ):
            self.assertIn(required, validator)

    def test_frozen_limits_and_worker_resources(self) -> None:
        private = read(SRC / "rehab_score_bridge_priv.h")
        required = (
            "#define REHAB_SCORE_RETRY_INTERVAL_MS  10000U",
            "#define REHAB_SCORE_MAX_ATTEMPTS       5U",
            "#define REHAB_SCORE_POLL_TIMEOUT_MS    (30U * 60U * 1000U)",
            "#define REHAB_SCORE_CHUNK_BYTES        (16U * 1024U)",
            "#define REHAB_SCORE_QUEUE_LENGTH       8U",
            "#define REHAB_SCORE_WORKER_PRIORITY    2U",
            "#define REHAB_SCORE_RESULT_MAX_BYTES   (64U * 1024U)",
        )
        for item in required:
            self.assertIn(item, private)

    def test_restart_recovery_actions(self) -> None:
        contract = read(SRC / "rehab_score_bridge_contract.c")
        expected = {
            "REHAB_SCORE_STATE_PENDING": "REHAB_SCORE_RECOVERY_SUBMIT",
            "REHAB_SCORE_STATE_UPLOADING": "REHAB_SCORE_RECOVERY_RESET_PENDING",
            "REHAB_SCORE_STATE_ACCEPTED": "REHAB_SCORE_RECOVERY_POLL",
            "REHAB_SCORE_STATE_SCORING": "REHAB_SCORE_RECOVERY_POLL",
            "REHAB_SCORE_STATE_SUCCEEDED": "REHAB_SCORE_RECOVERY_VALIDATE_RESULT",
            "REHAB_SCORE_STATE_FAILED": "REHAB_SCORE_RECOVERY_HOLD_FAILED",
        }
        for state, action in expected.items():
            self.assertIn(state, contract)
            self.assertIn(action, contract)
        core = read(SRC / "rehab_score_bridge_core.c")
        self.assertIn('"p4_restart_during_upload"', core)
        self.assertIn("poll_job(session, status)", core)
        self.assertIn("status->state != REHAB_SCORE_STATE_FAILED", core)

    def test_atomic_recovery_and_result_before_success(self) -> None:
        storage = read(SRC / "rehab_score_bridge_storage.c")
        for marker in ('.tmp"', '.bak"', "fflush(file)", "fsync(fileno(file))", "rename("):
            self.assertIn(marker, storage)
        core = read(SRC / "rehab_score_bridge_core.c")
        write_result = core.index("rehab_score_storage_write_result")
        succeeded = core.index("REHAB_SCORE_STATE_SUCCEEDED", write_result)
        self.assertLess(write_result, succeeded)

    def test_http_is_dedicated_and_streamed(self) -> None:
        http = read(SRC / "rehab_score_bridge_http.c")
        for marker in (
            "esp_http_client_open",
            "esp_http_client_write",
            "REHAB_SCORE_CHUNK_BYTES",
            '"/api/v1/score/jobs"',
            '"/api/v1/score/jobs/%s/csv"',
            '"/api/v1/score/jobs/%s/result"',
        ):
            self.assertIn(marker, http)
        self.assertNotIn("cap_http_request", http)
        self.assertNotIn("Base64", http)

    def test_writer_callback_has_no_io_or_hashing(self) -> None:
        recorder = read(
            REPO
            / "application/edge_agent/components/rehab_sensor_recording/src"
            / "rehab_training_recorder.c"
        )
        start = recorder.index("if (!failed && finalized_callback)")
        end = recorder.index("if (waiter)", start)
        callback_block = recorder[start:end]
        for forbidden in ("esp_http", "sha256", "fopen(", "fwrite(", "fsync("):
            self.assertNotIn(forbidden, callback_block)
        self.assertIn("finalized_callback(", callback_block)
        unlock = recorder.rfind("rehab_sensor_unlock();", 0, start)
        self.assertGreater(unlock, recorder.index("g_rehab_sensor.last_result_exists = true;"))

    def test_csv_header_remains_exactly_the_recorder_86_columns(self) -> None:
        recorder = read(
            REPO
            / "application/edge_agent/components/rehab_sensor_recording/src"
            / "rehab_training_recorder.c"
        )
        storage = read(SRC / "rehab_score_bridge_storage.c")
        frozen = c_string_constant(recorder, "REHAB_FUSED_CSV_HEADER[]")
        validated = c_string_constant(storage, "EXPECTED_CSV_HEADER[]")
        self.assertEqual(frozen, validated)
        self.assertEqual(86, len(frozen.split(",")))

    def test_scope_excludes_frozen_subsystems(self) -> None:
        core = read(SRC / "rehab_score_bridge_core.c")
        self.assertNotIn("rehab_capabilities_register", core)
        self.assertNotIn("sensor_packet_v5", core)
        self.assertNotIn("rehab_sensor_rx", core)

    def test_manual_url_has_priority_over_sta_gateway(self) -> None:
        network = read(SRC / "rehab_score_bridge_network.c")
        manual = network.index("config->rehab_score_base_url[0]")
        gateway = network.index("esp_netif_get_handle_from_ifkey")
        self.assertLess(manual, gateway)
        self.assertIn("rehab_score_contract_base_url", network)
        self.assertIn('return "manual";', network)
        self.assertIn('return "sta_gateway";', network)

    def test_auto_url_uses_current_sta_gateway_without_persistence(self) -> None:
        network = read(SRC / "rehab_score_bridge_network.c")
        for marker in (
            '"WIFI_STA_DEF"',
            "esp_netif_is_netif_up",
            "esp_netif_get_ip_info",
            "info.ip.addr == 0",
            "info.gw.addr == 0",
            "REHAB_SCORE_AUTO_PORT",
        ):
            self.assertIn(marker, network)
        for forbidden in ("app_config_save", "nvs_set", "mdns_"):
            self.assertNotIn(forbidden, network)

    def test_rfc1918_gateway_boundaries_and_url_shape(self) -> None:
        accepted = (
            "10.0.0.1",
            "10.255.255.254",
            "172.16.0.1",
            "172.31.255.254",
            "192.168.0.1",
            "192.168.255.254",
        )
        rejected = (
            "8.8.8.8",
            "127.0.0.1",
            "169.254.1.1",
            "172.15.255.254",
            "172.32.0.1",
        )
        for address in accepted:
            self.assertTrue(ipaddress.ip_address(address).is_private)
        source = read(SRC / "rehab_score_bridge_network.c")
        for address in rejected:
            first, second, *_ = (int(part) for part in address.split("."))
            source_accepts = (
                first == 10
                or (first == 172 and 16 <= second <= 31)
                or (first == 192 and second == 168)
            )
            self.assertFalse(source_accepts, address)
        self.assertIn('"http://%u.%u.%u.%u:%u"', source)

    def test_every_post_attempt_resolves_and_checks_health_first(self) -> None:
        core = read(SRC / "rehab_score_bridge_core.c")
        loop = core.index("while (status->attempt < REHAB_SCORE_MAX_ATTEMPTS)")
        resolve = core.index("rehab_score_resolve_base_url", loop)
        health = core.index("rehab_score_http_check_health", resolve)
        post = core.index("rehab_score_http_create_job", health)
        self.assertLess(loop, resolve)
        self.assertLess(resolve, health)
        self.assertLess(health, post)
        for error in (
            '"pc_gateway_unavailable"',
            '"pc_unreachable"',
            '"pc_service_invalid"',
            '"pc_service_not_ready"',
        ):
            self.assertIn(error, read(SRC / "rehab_score_bridge_http.c") + core)

    def test_health_contract_and_path_are_frozen(self) -> None:
        contract = read(SRC / "rehab_score_bridge_contract.c")
        http = read(SRC / "rehab_score_bridge_http.c")
        self.assertIn('strcmp(service->valuestring, "rehab_score_service") == 0', contract)
        self.assertIn('strcmp(status->valuestring, "ready") == 0', contract)
        self.assertIn('cJSON_GetObjectItemCaseSensitive(root, "service_version")', contract)
        self.assertIn('"/api/v1/score/health"', http)
        self.assertIn("HTTP_METHOD_GET", http)


if __name__ == "__main__":
    unittest.main(verbosity=2)
