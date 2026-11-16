"""Offline contract checks for the Recorder-local session clock failsafe.

These checks follow the repository's existing source-boundary test style. They
do not mount an SD card, change the host clock, or contact the score service.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
REPO = COMPONENT.parents[3]
RECORDER = COMPONENT / "src" / "rehab_training_recorder.c"
INTERNAL = COMPONENT / "src" / "rehab_sensor_internal.h"
BRIDGE = (
    REPO
    / "application"
    / "edge_agent"
    / "components"
    / "rehab_score_bridge"
    / "src"
    / "rehab_score_bridge_core.c"
)

TASK_FIELDS = {
    "task_name",
    "csv_file",
    "start_time",
    "end_time",
    "start_log_seq",
    "end_log_seq",
    "end_reason",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def function(source: str, name: str, next_name: str) -> str:
    return source[source.index(name) : source.index(next_name, source.index(name))]


def c_string_constant(source: str, name: str) -> str:
    declaration = source.index(name)
    terminator = source.index(";", declaration)
    literals = re.findall(r'"(?:\\.|[^"\\])*"', source[declaration:terminator])
    return "".join(ast.literal_eval(literal) for literal in literals)


def c_function(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated C function: {signature}")


class SessionClockSourceChecks(unittest.TestCase):
    def test_actual_c_anchor_and_iso_helpers_on_host(self) -> None:
        compiler = shutil.which("gcc")
        if compiler is None:
            candidate = Path(r"C:\msys64\ucrt64\bin\gcc.exe")
            compiler = str(candidate) if candidate.exists() else None
        idf_json = Path(
            r"C:\esp\v5.5.4\frameworks\esp-idf-v5.5.4\components\json\cJSON"
        )
        if compiler is None or not (idf_json / "cJSON.c").is_file():
            self.skipTest("host GCC or ESP-IDF cJSON source is unavailable")

        source = read(RECORDER)
        functions = (
            "static bool rehab_sensor_utf8_continuation",
            "bool rehab_sensor_valid_task_name",
            "static bool rehab_sensor_parse_decimal",
            "static bool rehab_sensor_leap_year",
            "static int rehab_sensor_days_in_month",
            "static int64_t rehab_sensor_days_from_civil",
            "static bool rehab_sensor_parse_iso8601_epoch",
            "static bool rehab_sensor_session_id_valid",
            "static bool rehab_sensor_exact_task_fields",
            "static bool rehab_sensor_json_uint64",
            "static bool rehab_sensor_csv_name_valid",
            "static bool rehab_sensor_parse_finalized_anchor",
        )
        extracted = "\n\n".join(c_function(source, signature) for signature in functions)
        harness = rf"""
#include <assert.h>
#include <ctype.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "cJSON.h"

#define REHAB_TRAINING_TASK_NAME_MAX_BYTES 96
#define REHAB_SENSOR_TIME_VALID_EPOCH INT64_C(1704067200)

{extracted}

int main(void)
{{
    int64_t epoch = 0;
    assert(rehab_sensor_parse_iso8601_epoch("2024-01-01T00:00:00Z", &epoch));
    assert(epoch == INT64_C(1704067200));
    assert(rehab_sensor_parse_iso8601_epoch("2024-01-01T08:00:00+08:00", &epoch));
    assert(epoch == INT64_C(1704067200));
    assert(!rehab_sensor_parse_iso8601_epoch("2025-02-29T00:00:00Z", &epoch));
    assert(!rehab_sensor_parse_iso8601_epoch("2024-01-01T00:00:00", &epoch));
    assert(rehab_sensor_session_id_valid("20240101_TASK001"));
    assert(!rehab_sensor_session_id_valid("20240101_TASK000"));

    const char *valid =
        "{{\"task_name\":\"task\",\"csv_file\":\"DATA_SEQ0000000001.csv\","
        "\"start_time\":\"2024-01-01T08:00:01+08:00\","
        "\"end_time\":\"2024-01-01T08:00:21+08:00\","
        "\"start_log_seq\":1,\"end_log_seq\":1000,\"end_reason\":\"user_stop\"}}";
    assert(rehab_sensor_parse_finalized_anchor("20240101_TASK001", valid, &epoch));
    assert(epoch == INT64_C(1704067221));
    assert(!rehab_sensor_parse_finalized_anchor("20240102_TASK001", valid, &epoch));

    const char *placeholder =
        "{{\"task_name\":\"task\",\"csv_file\":\"DATA_SEQ0000000001.csv\","
        "\"start_time\":\"2024-01-01T08:00:01+08:00\",\"end_time\":\"\","
        "\"start_log_seq\":1,\"end_log_seq\":0,\"end_reason\":\"\"}}";
    assert(!rehab_sensor_parse_finalized_anchor("20240101_TASK002", placeholder, &epoch));

    const char *extra =
        "{{\"task_name\":\"task\",\"csv_file\":\"DATA_SEQ0000000001.csv\","
        "\"start_time\":\"2024-01-01T08:00:01+08:00\","
        "\"end_time\":\"2024-01-01T08:00:21+08:00\","
        "\"start_log_seq\":1,\"end_log_seq\":1000,\"end_reason\":\"completed\","
        "\"unexpected\":true}}";
    assert(!rehab_sensor_parse_finalized_anchor("20240101_TASK003", extra, &epoch));
    return 0;
}}
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_file = root / "session_clock_host.c"
            executable = root / "session_clock_host.exe"
            source_file.write_text(harness, encoding="utf-8")
            env = os.environ.copy()
            env["PATH"] = str(Path(compiler).parent) + os.pathsep + env["PATH"]
            compiled = subprocess.run(
                [
                    compiler,
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(idf_json),
                    str(source_file),
                    str(idf_json / "cJSON.c"),
                    "-lm",
                    "-o",
                    str(executable),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(0, compiled.returncode, compiled.stderr)
            completed = subprocess.run(
                [str(executable)],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_resolver_priority_and_deterministic_floor_are_explicit(self) -> None:
        source = read(RECORDER)
        resolver = function(
            source,
            "static bool rehab_sensor_resolve_session_clock",
            "static bool rehab_sensor_fallback_end_time",
        )
        real = resolver.index("rehab_sensor_wall_clock")
        cache = resolver.index("latest_finalized_end_epoch")
        scan = resolver.index("rehab_sensor_find_latest_finalized_end", cache)
        build = resolver.index("rehab_sensor_parse_build_epoch", scan)
        floor = resolver.index("REHAB_SENSOR_TIME_VALID_EPOCH", build)
        self.assertLess(real, cache)
        self.assertLess(cache, scan)
        self.assertLess(scan, build)
        self.assertLess(build, floor)
        self.assertIn("esp_timer_get_time()", resolver)
        self.assertNotIn("settimeofday", source)

    def test_cold_scan_is_single_pass_and_keeps_the_latest_valid_candidate(self) -> None:
        source = read(RECORDER)
        scan = function(
            source,
            "static bool rehab_sensor_find_latest_finalized_end",
            "static bool rehab_sensor_parse_build_epoch",
        )
        self.assertEqual(1, scan.count("opendir("))
        self.assertEqual(1, scan.count("readdir("))
        self.assertIn("S_ISDIR", scan)
        self.assertIn("rehab_sensor_read_bounded_task", scan)
        self.assertIn("rehab_sensor_parse_finalized_anchor", scan)
        self.assertIn("strcmp(entry->d_name, context->latest_id) > 0", scan)
        for forbidden in ("qsort(", "scandir(", "remove(", "unlink(", "rmdir("):
            self.assertNotIn(forbidden, scan)

    def test_anchor_parser_requires_the_frozen_finalized_contract(self) -> None:
        source = read(RECORDER)
        parser = function(
            source,
            "static bool rehab_sensor_parse_finalized_anchor",
            "static bool rehab_sensor_find_latest_finalized_end",
        )
        fields_match = re.search(
            r"static const char \*const fields\[\] = \{(?P<body>.*?)\};",
            source,
            flags=re.S,
        )
        self.assertIsNotNone(fields_match)
        fields = set(re.findall(r'"([a-z_]+)"', fields_match.group("body")))
        self.assertEqual(TASK_FIELDS, fields)
        for marker in (
            'strcmp(end_reason->valuestring, "user_stop") == 0',
            'strcmp(end_reason->valuestring, "completed") == 0',
            "end >= start",
            "end_epoch >= start_epoch",
            "memcmp(session_id, start_date, 8) == 0",
        ):
            self.assertIn(marker, parser)

        valid = {
            "task_name": "球类横向取放训练（二级）",
            "csv_file": "DATA_SEQ0000000001.csv",
            "start_time": "2026-08-12T10:00:00+08:00",
            "end_time": "2026-08-12T10:00:20+08:00",
            "start_log_seq": 1,
            "end_log_seq": 1000,
            "end_reason": "user_stop",
        }
        self.assertEqual(TASK_FIELDS, set(json.loads(json.dumps(valid))))
        self.assertGreaterEqual(
            datetime.fromisoformat(valid["end_time"]),
            datetime.fromisoformat(valid["start_time"]),
        )

    def test_fallback_mode_is_frozen_and_end_uses_stop_monotonic_anchor(self) -> None:
        source = read(RECORDER)
        start = function(source, "esp_err_t rehab_training_start", "static void rehab_writer_apply_item")
        finish = function(
            source,
            "static void rehab_writer_finish_session",
            "esp_err_t rehab_training_register_finalized_callback",
        )
        stop = source[source.index("esp_err_t rehab_training_stop") :]
        self.assertIn("session_clock_fallback = context->clock_fallback", start)
        self.assertIn("session_start_epoch = context->start_epoch", start)
        self.assertNotIn('"time_not_ready"', start)
        self.assertIn("session_stop_us = esp_timer_get_time()", stop)
        self.assertIn("final_clock_fallback", finish)
        self.assertIn("rehab_sensor_fallback_end_time", finish)
        self.assertIn("latest_finalized_end_epoch = final_end_epoch", finish)
        self.assertIn("session_clock_fallback = false", finish)

    def test_real_path_and_frozen_csv_contract_remain_present(self) -> None:
        source = read(RECORDER)
        finish = function(
            source,
            "static void rehab_writer_finish_session",
            "esp_err_t rehab_training_register_finalized_callback",
        )
        self.assertIn("rehab_sensor_wall_clock(date, context->final_end_time)", finish)
        header = c_string_constant(source, "REHAB_FUSED_CSV_HEADER[]")
        self.assertEqual(86, len(header.split(",")))
        self.assertIn("REHAB_SENSOR_OUTPUT_RATE_HZ            50", read(INTERNAL))


class ScoreStatusFallbackChecks(unittest.TestCase):
    def test_all_status_transitions_use_real_now_then_validated_task_end(self) -> None:
        source = read(BRIDGE)
        helper = function(source, "static esp_err_t status_timestamp", "esp_err_t rehab_score_bridge_set_success_observer")
        self.assertLess(
            helper.index("rehab_score_storage_format_now"),
            helper.index("session->task.end_time"),
        )
        update = function(source, "static esp_err_t update_status", "static esp_err_t finish_failed")
        self.assertIn("status_timestamp(session, status->updated_at)", update)
        self.assertNotIn("rehab_score_storage_format_now", update)

        retry = source[source.index("esp_err_t rehab_score_bridge_retry") : source.index("esp_err_t rehab_score_bridge_init")]
        self.assertIn("rehab_score_storage_load_session_metadata(session_id, session)", retry)
        self.assertIn("update_status(session, status, REHAB_SCORE_STATE_PENDING", retry)
        self.assertNotIn("rehab_score_storage_format_now", retry)

    def test_poll_timeout_remains_monotonic(self) -> None:
        source = read(BRIDGE)
        poll = function(source, "static esp_err_t poll_job", "static esp_err_t submit_job")
        self.assertGreaterEqual(poll.count("esp_timer_get_time()"), 2)
        self.assertIn("REHAB_SCORE_POLL_TIMEOUT_MS", poll)
        self.assertNotIn("updated_at", poll)


if __name__ == "__main__":
    unittest.main()
