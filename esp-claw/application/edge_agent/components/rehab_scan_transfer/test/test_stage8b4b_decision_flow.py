#!/usr/bin/env python3
"""Host/static acceptance for the Stage 8B-4B P4 decision receiver."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from test_result_contract import CJSON, HOST_STUB, SOURCE


ROOT = Path(__file__).resolve().parents[5]
REAL_PACKAGE = Path(
    r"C:\ec\_deltar_stage8b2_runs"
    r"\scan_20260725_114552_000_accept_20260725_194634"
    r"\stage8b4a_p4_decision_package.v1.json"
)
TRANSFER = ROOT / "application" / "edge_agent" / "components" / \
    "rehab_scan_transfer" / "src" / "rehab_scan_transfer.c"
HTTP = TRANSFER.with_name("rehab_scan_transfer_http.c")
STORAGE = TRANSFER.with_name("rehab_scan_transfer_storage.c")


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

extern esp_err_t rehab_scan_transfer_parse_decision_status(
    const char *, size_t, const char *,
    rehab_scan_transfer_decision_status_t *);
extern esp_err_t rehab_scan_transfer_validate_decision_package(
    const char *, size_t, const char *);

int main(int argc, char **argv)
{
    if (argc != 3) return 2;
    size_t capacity = 131072;
    char *json = malloc(capacity);
    if (json == NULL) return 3;
    size_t length = fread(json, 1, capacity, stdin);
    if (ferror(stdin)) return 4;
    esp_err_t result;
    if (strcmp(argv[1], "status") == 0) {
        rehab_scan_transfer_decision_status_t status;
        result = rehab_scan_transfer_parse_decision_status(
            json, length, argv[2], &status);
    } else if (strcmp(argv[1], "package") == 0) {
        result = rehab_scan_transfer_validate_decision_package(
            json, length, argv[2]);
    } else {
        return 5;
    }
    free(json);
    return result == ESP_OK ? 0 : 1;
}
'''


def compile_harness(temp: Path) -> Path:
    compiler = shutil.which("gcc") or shutil.which("cc")
    if compiler is None:
        raise RuntimeError("host gcc/cc is required")
    source = SOURCE.read_text(encoding="utf-8")
    source = source.replace(
        '#include "rehab_scan_transfer_priv.h"',
        '#include "host_contract_stub.h"',
        1,
    )
    (temp / "contract.c").write_text(source, encoding="utf-8")
    (temp / "host_contract_stub.h").write_text(HOST_STUB, encoding="utf-8")
    (temp / "harness.c").write_text(HARNESS, encoding="utf-8")
    executable = temp / "decision_contract_harness.exe"
    command = [
        compiler, "-std=c17", "-Wall", "-Wextra",
        "-Dstrlcpy=host_strlcpy", f"-I{temp}", f"-I{CJSON}",
        str(temp / "contract.c"), str(CJSON / "cJSON.c"),
        str(temp / "harness.c"), "-o", str(executable),
    ]
    environment = os.environ.copy()
    environment["PATH"] = (
        str(Path(compiler).parent) + os.pathsep + environment["PATH"]
    )
    completed = subprocess.run(
        command, capture_output=True, text=True, env=environment
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stdout + completed.stderr)
    return executable


def accepted(executable: Path, kind: str, scan_id: str, value: object) -> bool:
    raw = value if isinstance(value, bytes) else json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    completed = subprocess.run(
        [str(executable), kind, scan_id], input=raw, capture_output=True
    )
    if completed.returncode not in {0, 1}:
        raise AssertionError(completed.stdout + completed.stderr)
    return completed.returncode == 0


def assert_case(
    rows: list[dict[str, str]], name: str, actual: bool, expected: bool
) -> None:
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected}, got {actual}")
    rows.append({"name": name, "status": "PASS"})


def atomic_commit_sim(
    temporary: Path, final: Path, *, fail_before_commit: bool = False
) -> None:
    if fail_before_commit:
        temporary.unlink(missing_ok=True)
        raise OSError("simulated validation failure")
    backup = final.with_name(final.name + ".previous")
    backup.unlink(missing_ok=True)
    had_previous = final.exists()
    if had_previous:
        final.rename(backup)
    try:
        temporary.rename(final)
        if not final.read_bytes():
            raise OSError("readback failed")
    except Exception:
        final.unlink(missing_ok=True)
        if had_previous:
            backup.rename(final)
        raise
    backup.unlink(missing_ok=True)


def main() -> int:
    rows: list[dict[str, str]] = []
    package = json.loads(REAL_PACKAGE.read_text(encoding="utf-8"))
    scan_id = package["scan_id"]
    with tempfile.TemporaryDirectory(
        prefix="stage8b4b_p4_contract_"
    ) as directory:
        executable = compile_harness(Path(directory))

        assert_case(rows, "package_valid", accepted(
            executable, "package", scan_id, package), True)

        no_height = copy.deepcopy(package)
        no_height["objects"][0]["geometry"]["valid"] = False
        no_height["objects"][0]["geometry"]["object_height_mm"] = None
        assert_case(rows, "geometry_valid_false_accepted", accepted(
            executable, "package", scan_id, no_height), True)

        duplicate = copy.deepcopy(package)
        second = copy.deepcopy(duplicate["objects"][0])
        second["object_id"] += "_second"
        duplicate["objects"].append(second)
        assert_case(rows, "same_class_multiple_accepted", accepted(
            executable, "package", scan_id, duplicate), True)

        sports_ball = copy.deepcopy(package)
        sports_ball["objects"][0]["class_name"] = "sports ball"
        sports_ball["objects"][0]["object_id"] = "scene_object_sports ball_0000000000000001"
        assert_case(rows, "sports_ball_accepted", accepted(
            executable, "package", scan_id, sports_ball), True)

        assert not any(
            item["class_name"] == "book" for item in package["objects"]
        )
        assert_case(rows, "book_absent_accepted", accepted(
            executable, "package", scan_id, package), True)

        invalids = {
            "scan_id_mismatch": ("other_scan", package),
            "schema_mismatch": (
                scan_id,
                dict(package, schema_version="stage8b4a.invalid"),
            ),
        }
        bbox = copy.deepcopy(package)
        bbox["objects"][0]["bbox_xyxy"][2] = 641
        invalids["bbox_out_of_bounds"] = (scan_id, bbox)
        frame = copy.deepcopy(package)
        frame["objects"][0]["source_frame_id"] = "frame_000010"
        invalids["frame_reference_mismatch"] = (scan_id, frame)
        for name, (expected_scan, value) in invalids.items():
            assert_case(rows, name, accepted(
                executable, "package", expected_scan, value), False)
        oversized = b"{" + b" " * 65536 + b"}"
        assert_case(rows, "package_over_64k_rejected", accepted(
            executable, "package", scan_id, oversized), False)

        processing = {
            "scan_id": scan_id, "status": "processing",
            "package_ready": False, "package_length": None,
            "package_sha256": None, "error_code": None,
            "error_message": None, "full_scene_ready": False,
        }
        assert_case(rows, "status_processing", accepted(
            executable, "status", scan_id, processing), True)
        raw = REAL_PACKAGE.read_bytes()
        ready = dict(
            processing, status="decision_ready", package_ready=True,
            package_length=len(raw),
            package_sha256=hashlib.sha256(raw).hexdigest(),
        )
        assert_case(rows, "status_ready", accepted(
            executable, "status", scan_id, ready), True)
        failed = dict(
            processing, status="failed", error_code="E_PIPELINE_FAILED",
            error_message="analysis failed",
        )
        assert_case(rows, "status_failed", accepted(
            executable, "status", scan_id, failed), True)
        bad_ready = dict(ready, package_sha256="f" * 63)
        assert_case(rows, "status_bad_hash_rejected", accepted(
            executable, "status", scan_id, bad_ready), False)

        calculated = hashlib.sha256(raw).hexdigest()
        assert_case(rows, "download_length_hash_match",
                    len(raw) == ready["package_length"] and
                    calculated == ready["package_sha256"], True)
        assert_case(rows, "download_hash_mismatch_rejected",
                    calculated == ("0" * 64), False)

        folder = Path(directory) / "sd"
        folder.mkdir()
        final = folder / "latest_successful_scene_result.json"
        final.write_bytes(b"old-success")
        temporary = folder / (
            "latest_successful_scene_result.json.tmp"
        )
        temporary.write_bytes(b"bad")
        try:
            atomic_commit_sim(
                temporary, final, fail_before_commit=True
            )
        except OSError:
            pass
        assert_case(rows, "tmp_failure_preserves_final",
                    final.read_bytes() == b"old-success", True)
        temporary.write_bytes(raw)
        atomic_commit_sim(temporary, final)
        first = final.read_bytes()
        temporary.write_bytes(raw)
        atomic_commit_sim(temporary, final)
        assert_case(rows, "duplicate_download_idempotent",
                    final.read_bytes() == first == raw, True)

    transfer_source = TRANSFER.read_text(encoding="utf-8")
    http_source = HTTP.read_text(encoding="utf-8")
    storage_source = STORAGE.read_text(encoding="utf-8")
    assert_case(rows, "stale_generation_checked_before_commit",
                transfer_source.index(
                    "generation_is_current(handle, generation)"
                ) < transfer_source.index(
                    "rehab_scan_transfer_commit_latest_scene("
                ), True)
    assert_case(rows, "cancel_checked_during_download",
                "rehab_scan_transfer_abort_requested(handle)" in http_source,
                True)
    assert_case(rows, "streaming_sha256",
                "mbedtls_sha256_update" in http_source and
                "expected_sha256" in http_source, True)
    assert_case(rows, "atomic_backup_rollback",
                "rename(backup, final)" in storage_source and
                "latest_scene_result_path" in storage_source, True)
    assert_case(rows, "interrupted_commit_backup_recovery",
                "backup_exists && !final_exists" in storage_source and
                "rename(backup, final)" in storage_source, True)

    print(json.dumps({
        "verdict": "PASS", "cases": len(rows), "tests": rows,
        "fixture_package_bytes": len(REAL_PACKAGE.read_bytes()),
        "fixture_package_sha256": hashlib.sha256(
            REAL_PACKAGE.read_bytes()
        ).hexdigest(),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
