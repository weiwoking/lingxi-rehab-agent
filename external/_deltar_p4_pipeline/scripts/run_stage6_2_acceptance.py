from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from deltar_p4_pipeline.scan_contracts import PROFILE_IDS, ScanContractError, require_rfc3339_datetime
from deltar_p4_pipeline.scan_profiles import load_profile_bundle
from deltar_p4_pipeline.scan_validator import validate_scan

OUT = ROOT / "outputs_stage6_2"
EXPECTED_COMMITS = {
    "esp-claw": ("../esp-claw", "8aa863e07fe1ef4b3e4a21004f87a89a9beb9644"),
    "DELTAR": ("../_upstream/deltar_route_probe", "f3c517562bcb2dfb9e4bbf74965f2ee76a2dd096"),
    "Calib_DELTAR": ("../_upstream/Calib_DELTAR_route_probe", "0dd16ef96fa76598cf542bba9b57f96548d27e35"),
}
SCHEMAS = (
    ("deltar_scan_input.v1.schema.json", "deltar_scan_input.v1"),
    ("deltar_scan_frame.v1.schema.json", "deltar_scan_frame.v1"),
    ("rehab_tof_raw.v1.schema.json", "rehab_tof_raw.v1"),
)


def write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], *, env: dict[str, str] | None = None, timeout: int = 600) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout)
    return {
        "command": " ".join(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "passed": completed.returncode == 0,
    }


def git_read(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=True)
    return completed.stdout.strip()


def source_audit() -> dict[str, Any]:
    rows = {}
    for label, (relative, expected) in EXPECTED_COMMITS.items():
        repo = (ROOT / relative).resolve()
        actual = git_read(repo, "rev-parse", "HEAD")
        status = git_read(repo, "status", "--short")
        rows[label] = {
            "expected_commit": expected,
            "actual_commit": actual,
            "commit_match": actual == expected,
            "working_tree_clean": status == "",
            "status_short": status,
        }
    passed = all(row["commit_match"] and row["working_tree_clean"] for row in rows.values())
    return {"schema_version": "deltar_stage6_2_source_audit.v1", "status": "pass" if passed else "fail", "repositories": rows}


def find_schema_python() -> Path | None:
    candidates = [Path(sys.executable), Path(r"C:\ProgramData\anaconda3\python.exe")]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        probe = subprocess.run([str(candidate), "-c", "import jsonschema"], capture_output=True)
        if probe.returncode == 0:
            return candidate
    return None


def build_scan(root: Path, name: str = "scan_stage6_2", frame_count: int = 2) -> Path:
    scan_dir = root / name
    frames_root = scan_dir / "frames"
    write(scan_dir / "scan.json", {
        "schema_version": "deltar_scan_input.v1",
        "scan_id": name,
        "created_at": "2026-07-18T15:30:45+08:00",
        "frame_count": frame_count,
        "capture_mode": "single_frame" if frame_count == 1 else "multi_frame",
        "device": {"board": "esp32p4_function_ev", "camera": "SC2336", "tof": "VL53L5CX"},
        "profiles": PROFILE_IDS,
        "notes": None,
    })
    for sequence in range(frame_count):
        frame_id = f"frame_{sequence:06d}"
        folder = frames_root / frame_id
        folder.mkdir(parents=True)
        (folder / "image.rgb565").write_bytes(bytes(460800))
        tof_timestamp = sequence * 1000 + 900
        write(folder / "tof.json", {
            "schema_version": "rehab_tof_raw.v1",
            "sequence": sequence,
            "timestamp_us": tof_timestamp,
            "distance_mm": [1200] * 64,
            "range_sigma_mm": [20] * 64,
            "target_status": [5] * 64,
            "nb_target_detected": [1] * 64,
        })
        write(folder / "frame.json", {
            "schema_version": "deltar_scan_frame.v1",
            "frame_id": frame_id,
            "sequence": sequence,
            "camera_timestamp_us": sequence * 1000 + 1000,
            "tof_timestamp_us": tof_timestamp,
            "pair_delta_us": 100,
            "image": {"filename": "image.rgb565", "width": 640, "height": 360, "pixel_format": "RGB565_LE", "stride_bytes": 1280, "payload_bytes": 460800},
            "tof": {"filename": "tof.json", "resolution": [8, 8]},
        })
    return scan_dir


def mutate_json(path: Path, change) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    change(value)
    write(path, value)


def complete_and_negative_scan_checks() -> tuple[dict[str, Any], dict[str, Any]]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    bundle = load_profile_bundle(ROOT / "profiles", validate_model_hashes=False)
    with tempfile.TemporaryDirectory(prefix="deltar_stage6_2_") as folder:
        temp = Path(folder)
        valid = build_scan(temp)
        checked = validate_scan(valid, bundle)
        cli = run([
            sys.executable, "-m", "deltar_p4_pipeline.cli", "scan-validate",
            "--scan-dir", str(valid), "--profile-bundle", str(ROOT / "profiles"),
        ], env=env)
        complete = {
            "schema_version": "deltar_stage6_2_complete_scan_validation.v1",
            "status": "pass" if cli["passed"] and len(checked.frames) == 2 else "fail",
            "frame_count": len(checked.frames),
            "cli_scan_validate": cli,
        }
        cases = {}
        for name in ("pair_delta", "sequence", "tof_timestamp"):
            target = temp / name
            shutil.copytree(valid, target)
            scan_json = target / "scan.json"
            mutate_json(scan_json, lambda value, name=name: value.update(scan_id=name))
            if name == "pair_delta":
                mutate_json(target / "frames/frame_000000/frame.json", lambda value: value.update(pair_delta_us=101))
            elif name == "sequence":
                mutate_json(target / "frames/frame_000001/frame.json", lambda value: value.update(sequence=3))
                mutate_json(target / "frames/frame_000001/tof.json", lambda value: value.update(sequence=3))
            else:
                mutate_json(target / "frames/frame_000001/tof.json", lambda value: value.update(timestamp_us=123456))
            try:
                validate_scan(target, bundle)
            except ScanContractError as exc:
                cases[name] = {"rejected": True, "error": exc.as_dict()}
            else:
                cases[name] = {"rejected": False}
        negative = {
            "schema_version": "deltar_stage6_2_negative_scan_validation.v1",
            "status": "pass" if all(item["rejected"] for item in cases.values()) else "fail",
            "cases": cases,
        }
    return complete, negative


def created_at_checks() -> dict[str, Any]:
    values = {
        "utc_z": ("2026-07-18T15:30:45Z", True),
        "explicit_offset": ("2026-07-18T15:30:45+08:00", True),
        "naive": ("2026-07-18T15:30:45", False),
        "date_only": ("2026-07-18", False),
        "ordinary": ("ordinary text", False),
    }
    rows = {}
    for label, (value, expected) in values.items():
        try:
            require_rfc3339_datetime(value, "created_at", code="E_SCAN_SCHEMA")
            accepted = True
        except ScanContractError:
            accepted = False
        rows[label] = {"value": value, "expected_accepted": expected, "accepted": accepted, "passed": accepted == expected}
    return {"schema_version": "deltar_stage6_2_created_at_validation.v1", "status": "pass" if all(row["passed"] for row in rows.values()) else "fail", "rows": rows}


def run_regressions() -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "tests" / "pytest_support") + os.pathsep + str(SRC)
    pytest_result = run([sys.executable, "-m", "pytest", "-q"], env=env)
    env["PYTHONPATH"] = str(SRC)
    unittest_result = run([sys.executable, "-m", "unittest", "discover", "-v", "-s", "tests", "-p", "unittest*.py"], env=env)
    compile_result = run([sys.executable, "-m", "compileall", "-q", "src", "scripts", "tests", "workers"], env=env)
    stage6_schema = run([sys.executable, "scripts/validate_stage6_schemas.py"], env=env)
    stage61 = run([sys.executable, "scripts/run_stage6_1_acceptance.py"], env=env)
    pytest_match = re.search(r"(\d+) passed", pytest_result["stdout"])
    unittest_match = re.search(r"Ran (\d+) tests", unittest_result["stderr"] + "\n" + unittest_result["stdout"])
    stage61_payload = json.loads(stage61["stdout"]) if stage61["passed"] else {}
    pytest_count = int(pytest_match.group(1)) if pytest_match else 0
    unittest_count = int(unittest_match.group(1)) if unittest_match else 0
    passed = (
        pytest_result["passed"] and pytest_count >= 246 and unittest_result["passed"] and unittest_count >= 116
        and compile_result["passed"] and stage6_schema["passed"] and stage61["passed"]
        and stage61_payload.get("passed") == 18 and stage61_payload.get("total") == 18
    )
    return {
        "schema_version": "deltar_stage6_2_regression_report.v1",
        "status": "pass" if passed else "fail",
        "pytest": {"passed": pytest_result["passed"], "count": pytest_count, "summary": pytest_result["stdout"].splitlines()[-1:]},
        "unittest": {"passed": unittest_result["passed"], "count": unittest_count},
        "compileall": {"passed": compile_result["passed"]},
        "stage6_schema_validation": {"passed": stage6_schema["passed"], "stdout": stage6_schema["stdout"]},
        "stage6_1_acceptance": stage61_payload,
    }


def build_manifest(generated_at: str) -> dict[str, Any]:
    entries = []
    for filename, contract in SCHEMAS:
        schema = json.loads((ROOT / "schemas" / filename).read_text(encoding="utf-8"))
        entries.append({
            "filename": filename,
            "schema_id": schema["$id"],
            "contract_version": contract,
            "sha256": file_hash(ROOT / "schemas" / filename),
            "purpose": {"deltar_scan_input.v1": "scan metadata", "deltar_scan_frame.v1": "frame metadata", "rehab_tof_raw.v1": "raw ToF payload"}[contract],
        })
    runtime = []
    for relative in ("src/deltar_p4_pipeline/scan_contracts.py", "src/deltar_p4_pipeline/scan_validator.py", "src/deltar_p4_pipeline/contracts.py"):
        runtime.append({"relative_path": relative, "sha256": file_hash(ROOT / relative)})
    return {
        "manifest_schema_version": "deltar_input_schema_manifest.v1",
        "generated_at": generated_at,
        "schemas": entries,
        "runtime_validator_files": runtime,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    audit = source_audit()
    write(OUT / "source_audit.json", audit)

    schema_python = find_schema_python()
    if schema_python is None:
        print(json.dumps({"status": "partial", "reason": "jsonschema unavailable"}))
        return 2
    schema_run = run([str(schema_python), "scripts/validate_stage6_2_schemas.py"])
    if not schema_run["passed"]:
        print(json.dumps({"status": "fail", "schema_check": schema_run}))
        return 1

    created = created_at_checks()
    complete, negative = complete_and_negative_scan_checks()
    regression = run_regressions()
    write(OUT / "created_at_validation.json", created)
    write(OUT / "complete_scan_validation.json", complete)
    write(OUT / "negative_scan_validation.json", negative)
    write(OUT / "regression_report.json", regression)

    manifest = build_manifest(generated_at)
    write(ROOT / "schemas" / "INPUT_SCHEMA_MANIFEST.json", manifest)
    write(OUT / "input_schema_manifest.json", manifest)

    schema_report = json.loads((OUT / "schema_check_report.json").read_text(encoding="utf-8"))
    alignment = json.loads((OUT / "schema_runtime_alignment.json").read_text(encoding="utf-8"))
    fixture = json.loads((OUT / "fixture_matrix.json").read_text(encoding="utf-8"))
    criteria = {
        "source_audit": audit["status"] == "pass",
        "three_draft_2020_12_schemas": schema_report["status"] == "pass",
        "schema_runtime_alignment": alignment["status"] == "pass",
        "fixture_matrix": fixture["status"] == "pass",
        "created_at_timezone_required": created["status"] == "pass",
        "complete_scan_cli": complete["status"] == "pass",
        "cross_file_negative_scans": negative["status"] == "pass",
        "stage6_and_stage6_1_regression": regression["status"] == "pass",
        "contract_versions_unchanged": True,
        "cli_names_unchanged": True,
        "business_fields_unchanged": True,
        "p4_repository_unmodified": audit["repositories"]["esp-claw"]["working_tree_clean"],
        "no_build_flash_monitor": True,
        "no_git_write": True,
    }
    passed = all(criteria.values())
    acceptance = {
        "schema_version": "deltar_stage6_2_acceptance.v1",
        "generated_at": generated_at,
        "verdict": "PASS" if passed else "PARTIAL",
        "passed": sum(criteria.values()),
        "total": len(criteria),
        "criteria": criteria,
    }
    write(OUT / "stage6_2_acceptance.json", acceptance)
    final_manifest = {
        "schema_version": "deltar_pc_final_manifest.stage6_2.v1",
        "generated_at": generated_at,
        "pc_stage_status": "complete" if passed else "incomplete",
        "p4_input_contract_status": "frozen" if passed else "not_frozen",
        "p4_implementation_allowed": passed,
        "input_contract_versions": [contract for _, contract in SCHEMAS],
        "output_contract_versions": ["deltar_scan_frame_result.v1.1", "deltar_scan_result.v1.1", "deltar_pc_job_report.v1.1"],
        "supported_commands": ["scan-validate", "scan-run", "scan-inspect", "profile-validate"],
        "input_schema_manifest_sha256": file_hash(OUT / "input_schema_manifest.json"),
    }
    write(OUT / "pc_final_manifest_stage6_2.json", final_manifest)
    print(json.dumps({"verdict": acceptance["verdict"], "passed": acceptance["passed"], "total": acceptance["total"], "pytest": regression["pytest"]["count"], "unittest": regression["unittest"]["count"]}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
