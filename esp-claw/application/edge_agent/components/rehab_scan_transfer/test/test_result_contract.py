#!/usr/bin/env python3
"""Host tests for the real P4 result-contract validator."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
SOURCE = COMPONENT / "src" / "rehab_scan_transfer_contract.c"
CJSON = (
    COMPONENT.parents[1]
    / "managed_components"
    / "espressif__cjson"
    / "cJSON"
)
SCAN_ID = "scan_20260718_151104_000"
JOB_ID = "job_698579c305c54d6ef7fd"


HOST_STUB = r'''
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#include "cJSON.h"

typedef int esp_err_t;

#define ESP_OK 0
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_RESPONSE 0x108
#define ESP_ERR_INVALID_SIZE 0x104
#define REHAB_SCAN_TRANSFER_PIPELINE_JOB_ID_MAX 129
#define REHAB_SCAN_TRANSFER_ERROR_CODE_MAX 48
#define REHAB_SCAN_TRANSFER_SCAN_ID_MAX 129
#define REHAB_SCAN_TRANSFER_SHA256_HEX_MAX 65
#define REHAB_SCAN_TRANSFER_DECISION_MAX_BYTES 65536U

typedef struct {
    char service_job_id[37];
    char pipeline_job_id[REHAB_SCAN_TRANSFER_PIPELINE_JOB_ID_MAX];
    char scan_id[129];
    char state[24];
    char pipeline_result_status[24];
} rehab_scan_transfer_job_t;

typedef struct {
    char scan_id[REHAB_SCAN_TRANSFER_SCAN_ID_MAX];
    char state[24];
    bool package_ready;
    size_t package_length;
    char package_sha256[REHAB_SCAN_TRANSFER_SHA256_HEX_MAX];
    char error_code[REHAB_SCAN_TRANSFER_ERROR_CODE_MAX];
} rehab_scan_transfer_decision_status_t;

size_t host_strlcpy(char *destination, const char *source, size_t size);

esp_err_t rehab_scan_transfer_validate_result(const char *json, size_t length,
                                              const char *scan_id,
                                              const char *pipeline_job_id,
                                              uint32_t max_objects);
'''


HARNESS = r'''
#include "host_contract_stub.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

size_t host_strlcpy(char *destination, const char *source, size_t size)
{
    size_t length = strlen(source);
    if (size != 0) {
        size_t copied = length < size - 1 ? length : size - 1;
        memcpy(destination, source, copied);
        destination[copied] = '\0';
    }
    return length;
}

int main(int argc, char **argv)
{
    if (argc != 3) return 2;
    size_t capacity = 4096;
    size_t length = 0;
    char *json = malloc(capacity);
    if (json == NULL) return 3;
    for (;;) {
        if (length == capacity) {
            capacity *= 2;
            char *larger = realloc(json, capacity);
            if (larger == NULL) {
                free(json);
                return 3;
            }
            json = larger;
        }
        size_t read = fread(json + length, 1, capacity - length, stdin);
        length += read;
        if (read == 0) break;
    }
    uint32_t max_objects = (uint32_t)strtoul(argv[2], NULL, 10);
    esp_err_t result = rehab_scan_transfer_validate_result(
        json, length, argv[1], "job_698579c305c54d6ef7fd", max_objects);
    free(json);
    printf("%d\n", result);
    return result == ESP_OK ? 0 : 1;
}
'''


def fixture() -> dict:
    return {
        "schema_version": "deltar_scan_result.v1.1",
        "status": "partial_success",
        "scan_id": SCAN_ID,
        "job_id": JOB_ID,
        "input": {},
        "models": {},
        "profiles": {},
        "objects": [
            {
                "scan_object_id": "scan_object_000001",
                "class_name": "chair",
                "class_scope": "core_now",
                "visibility": {
                    "status": "partial",
                    "top_visible": True,
                    "bottom_visible": True,
                    "full_extent_visible": False,
                },
                "distance": {
                    "value_m": 1.25,
                    "semantics": "visible_surface",
                    "valid": True,
                    "source_frame_id": "frame_000000",
                },
                "physical_height": {
                    "value_m": None,
                    "available": True,
                    "absolute_valid": False,
                },
                "bottom_height": {
                    "value_m": None,
                    "available": True,
                    "absolute_valid": False,
                },
                "placement_level": "unknown",
                "rejection_reason": None,
            }
        ],
        "warnings": ["W_UNMEASURED_CALIBRATION"],
        "errors": [],
        "aggregation": {},
        "provenance": {},
        "processing": {},
    }


def compact(value: dict) -> str:
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def compile_harness(temp: Path) -> Path:
    compiler = shutil.which("gcc") or shutil.which("cc")
    if compiler is None:
        raise RuntimeError("host gcc/cc is required")

    source = SOURCE.read_text(encoding="utf-8")
    include = '#include "rehab_scan_transfer_priv.h"'
    assert source.count(include) == 1
    (temp / "contract_host.c").write_text(
        source.replace(include, '#include "host_contract_stub.h"', 1),
        encoding="utf-8",
    )
    (temp / "host_contract_stub.h").write_text(HOST_STUB, encoding="utf-8")
    (temp / "harness.c").write_text(HARNESS, encoding="utf-8")
    executable = temp / "result_contract_harness.exe"
    command = [
        compiler,
        "-std=c17",
        "-Wall",
        "-Wextra",
        "-Dstrlcpy=host_strlcpy",
        f"-I{temp}",
        f"-I{CJSON}",
        str(temp / "contract_host.c"),
        str(CJSON / "cJSON.c"),
        str(temp / "harness.c"),
        "-o",
        str(executable),
    ]
    compiler_env = os.environ.copy()
    compiler_env["PATH"] = str(Path(compiler).parent) + os.pathsep + compiler_env["PATH"]
    result = subprocess.run(command, capture_output=True, text=True, env=compiler_env)
    if result.returncode != 0:
        raise AssertionError(
            f"host compile exited {result.returncode}: {command!r}\n"
            f"{result.stdout}{result.stderr}"
        )
    return executable


def validate(executable: Path, raw: str, *, scan_id: str = SCAN_ID,
             maximum: int = 100) -> bool:
    result = subprocess.run(
        [str(executable), scan_id, str(maximum)],
        input=raw,
        capture_output=True,
        text=True,
    )
    if result.returncode not in {0, 1}:
        raise AssertionError(result.stdout + result.stderr)
    return result.returncode == 0


def main() -> int:
    rows: list[dict[str, str]] = []

    with tempfile.TemporaryDirectory(prefix="rehab_scan_result_contract_") as directory:
        executable = compile_harness(Path(directory))

        def check(name: str, value: dict | str, expected: bool, **kwargs) -> None:
            raw = value if isinstance(value, str) else compact(value)
            actual = validate(executable, raw, **kwargs)
            if actual != expected:
                raise AssertionError(f"{name}: expected {expected}, got {actual}")
            rows.append({"name": name, "status": "PASS"})

        complete = fixture()
        check("complete_result_nullable_unmeasured_calibration", complete, True)

        legal_heights = [
            ("number_available_absolute", 0.25, True, True),
            ("null_available_not_absolute", None, True, False),
            ("number_available_not_absolute", 0.25, True, False),
            ("null_unavailable_not_absolute", None, False, False),
        ]
        for name, value_m, available, absolute in legal_heights:
            candidate = fixture()
            candidate["objects"][0]["physical_height"] = {
                "value_m": value_m,
                "available": available,
                "absolute_valid": absolute,
            }
            check(f"height_valid_{name}", candidate, True)

        invalid_heights = [
            ("null_available_absolute", {"value_m": None, "available": True, "absolute_valid": True}),
            ("null_unavailable_absolute", {"value_m": None, "available": False, "absolute_valid": True}),
            ("negative_number", {"value_m": -0.01, "available": True, "absolute_valid": False}),
            ("missing_field", {"value_m": 0.25, "available": True}),
            ("extra_field", {"value_m": 0.25, "available": True, "absolute_valid": False, "unit": "m"}),
            ("value_type", {"value_m": "0.25", "available": True, "absolute_valid": False}),
            ("available_type", {"value_m": 0.25, "available": 1, "absolute_valid": False}),
            ("absolute_type", {"value_m": 0.25, "available": True, "absolute_valid": 0}),
        ]
        for name, height in invalid_heights:
            candidate = fixture()
            candidate["objects"][0]["physical_height"] = height
            check(f"height_invalid_{name}", candidate, False)

        finite_base = compact(fixture())
        check(
            "height_invalid_nan",
            finite_base.replace('"value_m":null', '"value_m":NaN', 1),
            False,
        )
        check(
            "height_invalid_infinity",
            finite_base.replace('"value_m":null', '"value_m":1e9999', 1),
            False,
        )

        rejected = fixture()
        rejected.update(status="rejected", objects=[])
        check("regression_objects_zero_rejected", rejected, True)

        wrong_scan = fixture()
        wrong_scan["scan_id"] = "wrong"
        check("regression_scan_id_mismatch", wrong_scan, False)
        wrong_job = fixture()
        wrong_job["job_id"] = "wrong"
        check("regression_job_id_mismatch", wrong_job, False)
        wrong_schema = fixture()
        wrong_schema["schema_version"] = "deltar_scan_result.v1"
        check("regression_schema_mismatch", wrong_schema, False)
        too_many = fixture()
        too_many["objects"] = [copy.deepcopy(too_many["objects"][0]) for _ in range(101)]
        check("regression_objects_over_100", too_many, False)

    print(json.dumps({
        "verdict": "PASS",
        "cases": len(rows),
        "complete_result_status": complete["status"],
        "complete_result_warning": complete["warnings"][0],
        "tests": rows,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
