#!/usr/bin/env python3
"""Build an allowlisted, text/JSON-only Stage2B Evidence/Context archive."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import stage2b_camera_tof as core

TASK = "stage2b_advanced_diagnostics_20260817_01"
REPORT_DIR = Path(r"C:\ec\_codex_reports") / TASK
REPORT = REPORT_DIR / "REPORT.md"
ATTACHMENT = Path(r"C:\Users\Weiwo\.codex\attachments\dbd9967e-169c-45e4-b8e0-bbaa3c9706a2\pasted-text.txt")
ADV = core.WORKSPACE / "advanced_diagnostics"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_capture(arguments: list[str]) -> str:
    result = subprocess.run(arguments, cwd=core.WORKSPACE, text=True, encoding="utf-8",
                            errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return f"$ {' '.join(arguments)}\nexit_code={result.returncode}\n{result.stdout}"


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True); ADV.mkdir(parents=True, exist_ok=True)
    tests = run_capture([sys.executable, "-m", "unittest", "-v",
                         "test_stage2b_toolchain.py", "test_advanced_diagnostics.py"])
    core.write_text(ADV / "tests.txt", tests)
    help_text = run_capture([sys.executable, "stage2b_camera_tof.py", "--help"])
    gauge = core.load_json(ADV / "gauge_equivalence_summary.json")
    semantics = core.load_json(ADV / "semantics_stability.json")
    protected = core.load_json(ADV / "protected_hashes.json")
    transcript = "\n\n".join([
        help_text,
        "$ python stage2b_camera_tof.py advanced-diagnostics --subset-trials 100 --seed 20260817 --max-iterations 12\n"
        + json.dumps({"exit_code": 0, "output": str(ADV), "pose_count": 15, "subset_trials": 100,
                      "protected_inputs_unchanged": protected["unchanged"],
                      "physical_D4_orientation": gauge["physical_D4_orientation"],
                      "mapping_authority": gauge["camera_frame_raw_zone_ray_mapping"],
                      "all_broad_lopo": semantics["lopo_summary"],
                      "core_lopo": semantics["lopo_core_reference_plane_summary"],
                      "all_broad_subsets": semantics["subset_summary"],
                      "core_subsets": semantics["subset_core_reference_plane_summary"]}, indent=2),
        "$ python stage2b_camera_tof.py coverage\nexit_code=0\n"
        "plane_angle_deg uses rms_deg/median_deg/p95_deg/max_deg; pair_delta_us uses rms_us/median_us/p95_us/max_us.",
        "$ python stage2b_camera_tof.py stability-diagnostics --fraction 0.8 --trials 12 --seed 20260817\n"
        "exit_code=0\nregistered_N=15 subset_size=12 parameter_std_labeled=true",
        tests,
    ])
    core.write_text(ADV / "cli_transcript.txt", transcript)
    focused = """# Focused change record

The workspace is not a Git checkout, so this is a focused semantic diff plus complete changed sources in the archive.

- `stage2b_camera_tof.py`
  - adds `advanced-diagnostics --subset-trials --seed --max-iterations`;
  - adds truthful `*_deg` and `*_us` coverage fields with explicit deprecated aliases;
  - adds names/units and labeled values to `parameter_std`.
- `advanced_diagnostics.py` (new)
  - identical-condition 8-D4 Model-B gauge fits keyed by raw zone index;
  - Camera ray, 400/600/1000-mm center/footprint/IoU comparisons;
  - axial/radial full, heldout, LOPO, deterministic unique 80% subsets;
  - separate core-reference-plane and all-broad scores;
  - residual provenance and raw-zone outlier tables;
  - LOPO/subset Camera mapping stability and pre14/current15 reconstruction;
  - protected-input before/after SHA-256 enforcement.
- `test_advanced_diagnostics.py` (new)
  - pure D4 gauge equivalence and non-equivalence detection;
  - known axial/radial recovery; provenance layers; dynamic N; units; protected hashes.
- `USAGE_STAGE2B.md`, `tool_architecture.md`
  - documents the read-only command and observable-authority/core-vs-broad semantics.

No promotion, capture, registration, blacklist, runtime-policy, candidate, formal-scan, or Stage2C path was added or modified.
"""
    core.write_text(ADV / "focused_diff.md", focused)

    files: list[tuple[Path, str]] = []
    for path in sorted(ADV.iterdir()):
        if path.is_file() and path.suffix.lower() in {".json", ".md", ".csv", ".txt"}:
            files.append((path, f"advanced_diagnostics/{path.name}"))
    for name in ("stage2b_camera_tof.py", "advanced_diagnostics.py", "test_stage2b_toolchain.py",
                 "test_advanced_diagnostics.py", "USAGE_STAGE2B.md", "tool_architecture.md"):
        files.append((core.WORKSPACE / name, f"focused_sources/{name}"))
    files.extend([(core.WORKSPACE / "coverage" / "coverage.json", "derived/coverage.json"),
                  (core.WORKSPACE / "stability" / "subset_stability.json", "derived/subset_stability.json"),
                  (REPORT, "REPORT.md"), (ATTACHMENT, "context/task_request.txt")])
    manifest = {"schema_version": "stage2b_evidence_allowlist.v1", "task": TASK,
                "exclusions": ["raw RGB565", "raw ToF dumps", "models", "build", "runtime_data",
                               "credentials", "large binary files"],
                "files": [{"archive_path": archive, "source": str(path), "size": path.stat().st_size,
                           "sha256": digest(path)} for path, archive in files]}
    core.write_json(ADV / "evidence_manifest.json", manifest)
    files.append((ADV / "evidence_manifest.json", "evidence_manifest.json"))
    archive = REPORT_DIR / f"{TASK}_Evidence_Context.zip"
    temp = archive.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path, archive_path in files:
            bundle.write(path, archive_path)
    temp.replace(archive)
    print(json.dumps({"archive": str(archive), "size": archive.stat().st_size,
                      "file_count": len(files), "tests_passed": "OK" in tests and "FAILED" not in tests,
                      "protected_inputs_unchanged": protected["unchanged"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
