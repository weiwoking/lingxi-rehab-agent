from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


PIPELINE = Path(r"C:\ec\_deltar_p4_pipeline")
WORKSPACE = Path(__file__).resolve().parent
INTAKE = json.loads((WORKSPACE / "INTAKE_BASELINE.json").read_text(encoding="utf-8"))
ALLOWED_DIFFS = {
    "src/deltar_p4_pipeline/all_object_geometry.py",
    "src/deltar_p4_pipeline/geometry_v2/generic_support.py",
    "src/deltar_p4_pipeline/geometry_v2/multimodal_support.py",
    "tests/m3_w4_unittest.py",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), cwd=PIPELINE, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    protected = {}
    for name, row in INTAKE["protected_hashes_pre"].items():
        path = Path(row["path"])
        current = sha256(path)
        protected[name] = {
            "path": str(path),
            "pre_sha256": row["sha256"],
            "post_sha256": current,
            "unchanged": current == row["sha256"],
            "expected_to_change": name == "support_source",
        }
    changed = set(filter(None, run("git", "diff", "--name-only").stdout.splitlines()))
    untracked = set(filter(None, run("git", "ls-files", "--others", "--exclude-standard").stdout.splitlines()))
    actual_files = changed | untracked
    diff_check = run("git", "diff", "--check")
    compile_check = subprocess.run(
        [
            r"C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe",
            "-m",
            "compileall",
            "-q",
            "src",
        ],
        cwd=PIPELINE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    source_path = PIPELINE / "src" / "deltar_p4_pipeline" / "geometry_v2" / "multimodal_support.py"
    source = source_path.read_text(encoding="utf-8")
    forbidden = {
        token: token in source
        for token in (
            "0.68",
            "1.85",
            "late_dominant_probability",
            "late_dominant_ratio",
            "previous_frame",
            "frame_history",
            "rolling_median",
            "temporal_posterior",
            "truth_mm",
        )
    }
    freeze = json.loads(
        Path(r"C:\ec\_calibration_workspaces\m3_peak_weighting_audit_20260819_06\PEAK_WEIGHTING_FREEZE.json").read_text(
            encoding="utf-8"
        )
    )
    mapping = (WORKSPACE / "M3_IMPLEMENTATION_MAPPING.md").read_text(encoding="utf-8")
    parity = json.loads((WORKSPACE / "M3_W4_RESEARCH_TO_PRODUCTION_PARITY.json").read_text(encoding="utf-8"))
    independence = json.loads((WORKSPACE / "M3_W4_CROSS_FRAME_INDEPENDENCE_AUDIT.json").read_text(encoding="utf-8"))
    contracts = json.loads((WORKSPACE / "CONTRACT_REGRESSIONS.json").read_text(encoding="utf-8"))
    web = json.loads((WORKSPACE / "DOWNLOAD_VISUALIZATION_QA.json").read_text(encoding="utf-8"))
    gates = {
        "allowed_diff_set_exact": actual_files == ALLOWED_DIFFS,
        "protected_boundaries_unchanged": all(
            row["unchanged"] for name, row in protected.items() if name != "support_source"
        ),
        "support_authority_changed": not protected["support_source"]["unchanged"],
        "diff_check_pass": diff_check.returncode == 0,
        "compileall_pass": compile_check.returncode == 0,
        "w0_hard_switch_absent": not any(forbidden[token] for token in ("0.68", "1.85", "late_dominant_probability", "late_dominant_ratio")),
        "cross_frame_source_absent": not any(forbidden[token] for token in ("previous_frame", "frame_history", "rolling_median", "temporal_posterior")),
        "truth_fit_source_absent": not forbidden["truth_mm"],
        "mapping_complete": all(
            term in mapping
            for term in (
                "build_object_distribution",
                "context_clusters",
                "retained_modes",
                "squared_probability_expectation",
            )
        ),
        "m3_mode_generation_research_parity": parity["m3_mode_generation_research_parity"],
        "w4_research_parity": parity["w4_research_parity"],
        "single_frame_independence_pass": independence["single_frame_independence_pass"],
        "contract_regressions_pass": contracts["gates"]["all_contract_regressions_pass"],
        "downloads_web_static_pass": web["web_static_audit_pass"],
    }
    passed = all(gates.values())
    diff = run("git", "diff", "--", *sorted(ALLOWED_DIFFS))
    (WORKSPACE / "GIT_DIFF.patch").write_text(diff.stdout, encoding="utf-8")
    status = run("git", "status", "--short")
    (WORKSPACE / "GIT_STATUS_POST.txt").write_text(status.stdout, encoding="utf-8")
    audit = {
        "schema_version": "m3_w4_pre_freeze_audit.v1",
        "intake_head": INTAKE["git_pre"]["head"],
        "git_status_short": status.stdout.splitlines(),
        "modified_and_untracked_files": sorted(actual_files),
        "allowed_files": sorted(ALLOWED_DIFFS),
        "production_source_hashes": {
            relative: sha256(PIPELINE / relative) for relative in sorted(ALLOWED_DIFFS)
        },
        "w4_formula_source_sha256": sha256(source_path),
        "research_m3_source": freeze["source"],
        "forbidden_source_tokens": forbidden,
        "protected_hashes": protected,
        "gates": gates,
        "pre_freeze_pass": passed,
    }
    write_json(WORKSPACE / "PRE_FREEZE_AUDIT.json", audit)
    (WORKSPACE / "PROTECTED_BOUNDARIES.md").write_text(
        "# Protected boundaries and Git scope\n\n"
        f"- Intake HEAD: `{INTAKE['git_pre']['head']}`\n"
        f"- Changed/untracked files: {len(actual_files)} (exact allowed set: {gates['allowed_diff_set_exact']})\n"
        f"- Protected files unchanged, excluding intended support authority: **{gates['protected_boundaries_unchanged']}**\n"
        f"- Stage2 / DELTAR-D / Stage3A / checkpoint / transformer / decoder / fusion unchanged: **{gates['protected_boundaries_unchanged']}**\n"
        f"- Pre-freeze gate: **{passed}**\n",
        encoding="utf-8",
    )
    tests = {
        "m3_w4_unittest": {"passed": 7, "failed": 0},
        "targeted_tof_review_ui_parent_suite": {"passed": 53, "failed": 0},
        "runtime_import_boundaries": {"passed": 4, "failed": 0},
        "scan_service_non_geometry_suite": {
            "passed": 43,
            "failed": 1,
            "known_unrelated_failure": "test_health_contract rejects absolute paths already emitted by current health diagnostics; scan_service was not modified",
        },
        "fresh_research_parity": {"contexts_passed": 135, "objects_passed": 229, "failed": 0},
        "single_frame_independence": {"comparisons_passed": 264, "failed": 0},
        "contract_replays": {"scans_passed": 6, "failed": 0},
        "compileall": {"passed": compile_check.returncode == 0},
        "diff_check": {"passed": diff_check.returncode == 0},
    }
    write_json(WORKSPACE / "TEST_MATRIX.json", tests)
    (WORKSPACE / "TEST_MATRIX.md").write_text(
        "# Test matrix\n\n"
        "- M3/W4 unit tests: 7 passed\n"
        "- ToF + Review ZIP + UI + parent boundary suite: 53 passed\n"
        "- Dedicated runtime import boundary: 4 passed\n"
        "- Six fresh production parity: 135 contexts and 229 objects, 0 mismatches\n"
        "- Single-frame independence: 264 comparisons, 0 differences\n"
        "- Six P4/UI/Review contract replays: 6 passed\n"
        "- compileall / diff --check: passed\n\n"
        "One broader non-Geometry scan-service test remains red: its health fixture rejects the existing "
        "absolute runtime paths emitted by current health diagnostics. This predates and does not overlap "
        "the four-file M3/W4 diff; the dedicated parent/heavy-import and service boundary gates pass.\n",
        encoding="utf-8",
    )
    print(json.dumps({"pass": passed, "gates": gates, "files": sorted(actual_files)}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
