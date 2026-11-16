from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent
DIAGNOSTICS = WORKSPACE / "diagnostics" / "distance_drift_20260818"
REPORT_DIR = Path(r"C:\ec\_codex_reports\stage3a_distance_drift_diagnostic_20260818_01")
EVIDENCE = REPORT_DIR / "evidence"
ZIP_PATH = REPORT_DIR / "stage3a_distance_drift_diagnostic_20260818_01_evidence.zip"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    if not (REPORT_DIR / "REPORT.md").is_file():
        raise RuntimeError("REPORT.md missing")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    diagnostic_target = EVIDENCE / "diagnostics"
    diagnostic_target.mkdir(parents=True, exist_ok=True)
    for source in sorted(DIAGNOSTICS.iterdir()):
        if source.is_file():
            shutil.copy2(source, diagnostic_target / source.name)

    source_target = EVIDENCE / "source"
    source_target.mkdir(parents=True, exist_ok=True)
    for source in (
        WORKSPACE / "stage3a_distance_drift_diagnostic.py",
        WORKSPACE / "build_distance_drift_evidence.py",
        WORKSPACE / "tests" / "test_stage3a_distance_drift_diagnostic.py",
        WORKSPACE / "stage3a_camera_world.py",
        WORKSPACE / "observations" / "registry.json",
        WORKSPACE / "camera_height.json",
        WORKSPACE / "fixed_board_height.json",
        WORKSPACE / "protected_hashes_after_fixed_board_fix.json",
    ):
        shutil.copy2(source, source_target / source.name)

    test = subprocess.run(
        ["python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
        cwd=WORKSPACE,
        text=True,
        capture_output=True,
        check=False,
    )
    transcript = (
        "COMMAND: python -m unittest discover -s tests -p 'test_*.py' -v\n"
        f"EXIT_CODE: {test.returncode}\n\nSTDOUT:\n{test.stdout}\nSTDERR:\n{test.stderr}"
    )
    (EVIDENCE / "TEST_TRANSCRIPT.txt").write_text(transcript, encoding="utf-8")
    if test.returncode != 0:
        raise RuntimeError("tests failed while building evidence")

    baseline = json.loads((WORKSPACE / "protected_hashes_after_fixed_board_fix.json").read_text(encoding="utf-8-sig"))
    protected_checks = []
    for row in baseline["checks"]:
        path = Path(row["path"])
        current = sha256(path) if path.is_file() else None
        expected = row["after_sha256"]
        protected_checks.append(
            {
                "path": str(path),
                "expected_sha256": expected,
                "current_sha256": current,
                "unchanged": current == expected,
            }
        )

    registry = json.loads((WORKSPACE / "observations" / "registry.json").read_text(encoding="utf-8-sig"))
    raw_checks = []
    for pose_id, binding in registry["poses"].items():
        for path_text, expected in binding["source_hashes"].items():
            path = Path(path_text)
            current = sha256(path) if path.is_file() else None
            raw_checks.append(
                {
                    "pose_id": pose_id,
                    "path": path_text,
                    "expected_sha256": expected,
                    "current_sha256": current,
                    "unchanged": current == expected,
                }
            )

    boundary = {
        "schema_version": "stage3a_distance_drift_boundary_verification.v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "protected_checks": protected_checks,
        "protected_all_unchanged": all(row["unchanged"] for row in protected_checks),
        "raw_scan_file_count": len(raw_checks),
        "raw_scan_checks": raw_checks,
        "raw_scans_all_unchanged": all(row["unchanged"] for row in raw_checks),
        "main_stage3a_tool_sha256": sha256(WORKSPACE / "stage3a_camera_world.py"),
        "diagnostic_tool_sha256": sha256(WORKSPACE / "stage3a_distance_drift_diagnostic.py"),
        "formal_runtime_modified": False,
        "promotion": False,
        "new_capture": False,
    }
    write_json(EVIDENCE / "BOUNDARY_VERIFICATION.json", boundary)
    if not boundary["protected_all_unchanged"] or not boundary["raw_scans_all_unchanged"]:
        raise RuntimeError("boundary verification failed")

    files = [REPORT_DIR / "REPORT.md", *sorted(path for path in EVIDENCE.rglob("*") if path.is_file())]
    manifest = {
        "schema_version": "stage3a_distance_drift_evidence_manifest.v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "root_cause_classification": "MIXED",
        "candidate_status": "CANDIDATE_NOT_PROMOTED",
        "files": [
            {
                "path": str(path.relative_to(REPORT_DIR)),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in files
        ],
    }
    write_json(EVIDENCE / "EVIDENCE_MANIFEST.json", manifest)

    zip_files = [REPORT_DIR / "REPORT.md", *sorted(path for path in EVIDENCE.rglob("*") if path.is_file())]
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in zip_files:
            archive.write(path, path.relative_to(REPORT_DIR))
    print(json.dumps({
        "report": str(REPORT_DIR / "REPORT.md"),
        "evidence_zip": str(ZIP_PATH),
        "evidence_zip_sha256": sha256(ZIP_PATH),
        "protected_all_unchanged": boundary["protected_all_unchanged"],
        "raw_scans_all_unchanged": boundary["raw_scans_all_unchanged"],
        "tests_exit_code": test.returncode,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
