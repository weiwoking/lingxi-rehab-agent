from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jsonschema import Draft202012Validator, FormatChecker

from deltar_p4_pipeline.contracts import validate_tof
from deltar_p4_pipeline.scan_validator import validate_frame_document, validate_scan_document

SCHEMA_FILES = {
    "scan": "deltar_scan_input.v1.schema.json",
    "frame": "deltar_scan_frame.v1.schema.json",
    "tof": "rehab_tof_raw.v1.schema.json",
}
INVALID_KIND = {
    "frame_unknown_field.json": "frame",
    "frame_missing_image.json": "frame",
    "frame_bad_resolution.json": "frame",
    "tof_63_items.json": "tof",
    "tof_65_items.json": "tof",
    "tof_unknown_field.json": "tof",
    "scan_bad_datetime.json": "scan",
    "scan_naive_datetime.json": "scan",
    "scan_unknown_profile.json": "scan",
    "scan_path_id.json": "scan",
}
RUNTIME: dict[str, Callable[[dict[str, Any]], Any]] = {
    "scan": validate_scan_document,
    "frame": validate_frame_document,
    "tof": lambda value: validate_tof(value, max_sequence=63),
}


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def accepted(call: Callable[[], Any]) -> tuple[bool, str | None]:
    try:
        call()
    except Exception as exc:
        return False, str(exc).splitlines()[0]
    return True, None


def main() -> int:
    fixture_root = ROOT / "tests" / "fixtures" / "stage6_2_input_contract"
    schemas = {kind: load(ROOT / "schemas" / name) for kind, name in SCHEMA_FILES.items()}
    checks = {}
    validators = {}
    for kind, schema in schemas.items():
        ok, error = accepted(lambda schema=schema: Draft202012Validator.check_schema(schema))
        checks[kind] = {"filename": SCHEMA_FILES[kind], "draft_2020_12_valid": ok, "error": error}
        validators[kind] = Draft202012Validator(schema, format_checker=FormatChecker())

    rows = []
    for kind in ("scan", "frame", "tof"):
        value = load(fixture_root / "valid" / f"{kind}.json")
        schema_ok, schema_error = accepted(lambda kind=kind, value=value: validators[kind].validate(value))
        runtime_ok, runtime_error = accepted(lambda kind=kind, value=value: RUNTIME[kind](value))
        rows.append({
            "fixture": f"valid/{kind}.json", "expected": "accepted", "schema_accepted": schema_ok,
            "runtime_accepted": runtime_ok, "aligned": schema_ok == runtime_ok, "schema_error": schema_error,
            "runtime_error": runtime_error,
        })
    for filename, kind in INVALID_KIND.items():
        value = load(fixture_root / "invalid" / filename)
        schema_ok, schema_error = accepted(lambda kind=kind, value=value: validators[kind].validate(value))
        runtime_ok, runtime_error = accepted(lambda kind=kind, value=value: RUNTIME[kind](value))
        rows.append({
            "fixture": f"invalid/{filename}", "expected": "rejected", "schema_accepted": schema_ok,
            "runtime_accepted": runtime_ok, "aligned": schema_ok == runtime_ok, "schema_error": schema_error,
            "runtime_error": runtime_error,
        })

    valid_scan = load(fixture_root / "valid" / "scan.json")
    valid_frame = load(fixture_root / "valid" / "frame.json")
    valid_tof = load(fixture_root / "valid" / "tof.json")
    generated: list[tuple[str, str, dict[str, Any]]] = []
    for field in ("filename", "width", "height", "pixel_format", "stride_bytes", "payload_bytes"):
        value = deepcopy(valid_frame)
        value["image"][field] = "wrong" if field in {"filename", "pixel_format"} else value["image"][field] + 1
        generated.append((f"generated/frame_bad_image_{field}", "frame", value))
    for field, bad in (("distance_mm", -1), ("distance_mm", 65536), ("range_sigma_mm", 65536),
                       ("target_status", 256), ("nb_target_detected", 256)):
        value = deepcopy(valid_tof)
        value[field][0] = bad
        generated.append((f"generated/tof_bad_{field}_{bad}", "tof", value))
    value = deepcopy(valid_tof)
    value["sequence"] = 64
    generated.append(("generated/tof_bad_sequence_64", "tof", value))
    for label, created_at in (("date_only", "2026-07-18"), ("ordinary", "ordinary text")):
        value = deepcopy(valid_scan)
        value["created_at"] = created_at
        generated.append((f"generated/scan_created_at_{label}", "scan", value))
    for label, scan_id in (("dot", "."), ("dotdot", ".."), ("slash", "a/b"), ("backslash", "a\\b"), ("space", "a b")):
        value = deepcopy(valid_scan)
        value["scan_id"] = scan_id
        generated.append((f"generated/scan_id_{label}", "scan", value))
    for kind, field in (("scan", "device"), ("frame", "image"), ("frame", "tof")):
        value = deepcopy(valid_scan if kind == "scan" else valid_frame)
        value[field]["extra"] = True
        generated.append((f"generated/{kind}_{field}_unknown_field", kind, value))
    for name, kind, value in generated:
        schema_ok, schema_error = accepted(lambda kind=kind, value=value: validators[kind].validate(value))
        runtime_ok, runtime_error = accepted(lambda kind=kind, value=value: RUNTIME[kind](value))
        rows.append({
            "fixture": name, "expected": "rejected", "schema_accepted": schema_ok,
            "runtime_accepted": runtime_ok, "aligned": schema_ok == runtime_ok, "schema_error": schema_error,
            "runtime_error": runtime_error,
        })

    correct = all(
        row["aligned"] and ((row["expected"] == "accepted" and row["schema_accepted"]) or
                            (row["expected"] == "rejected" and not row["schema_accepted"]))
        for row in rows
    )
    schema_ok = all(item["draft_2020_12_valid"] for item in checks.values())
    schema_report = {
        "schema_version": "deltar_stage6_2_schema_check_report.v1",
        "validator": "jsonschema.Draft202012Validator",
        "format_checker": True,
        "status": "pass" if schema_ok else "fail",
        "checks": checks,
    }
    fixture_report = {
        "schema_version": "deltar_stage6_2_fixture_matrix.v1",
        "status": "pass" if correct else "fail",
        "rows": rows,
    }
    alignment = {
        "schema_version": "deltar_stage6_2_schema_runtime_alignment.v1",
        "status": "pass" if correct else "fail",
        "single_file_fixture_results_equal": correct,
        "cross_file_constraints_runtime_only": [
            "scan_id equals final directory name",
            "frame_count equals frame directory count",
            "sequence is contiguous from zero",
            "capture_mode agrees with frame_count",
            "image payload file size is 460800",
            "tof.sequence equals frame.sequence",
            "tof.timestamp_us equals frame.tof_timestamp_us",
            "pair_delta_us equals absolute timestamp difference",
        ],
    }
    out = ROOT / "outputs_stage6_2"
    write(out / "schema_check_report.json", schema_report)
    write(out / "fixture_matrix.json", fixture_report)
    write(out / "schema_runtime_alignment.json", alignment)
    passed = schema_ok and correct
    print(json.dumps({"status": "pass" if passed else "fail", "schemas": len(checks), "fixtures": len(rows)}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
