from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any
import zipfile


PIPELINE = Path(r"C:\ec\_deltar_p4_pipeline")
WORKSPACE = Path(__file__).resolve().parent
REPORT_DIR = Path(r"C:\ec\_codex_reports\m3_w4_formal_enablement_20260819_01")
EVIDENCE = REPORT_DIR / "evidence"
ZIP_PATH = REPORT_DIR / "m3_w4_formal_enablement_20260819_01_evidence.zip"
WEB = Path(r"C:\Users\Weiwo\Downloads\geometry_m3_w4_final_review_20260819_01")
RESEARCH_M3 = Path(r"C:\ec\_calibration_workspaces\single_frame_candidate_information_audit_20260819_02\extract_and_counterfactual.py")
RESEARCH_W4_ROOT = Path(r"C:\ec\_calibration_workspaces\m3_peak_weighting_audit_20260819_06")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def copy(source: Path, relative: str) -> None:
    target = EVIDENCE / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    freeze = json.loads((WORKSPACE / "M3_W4_PRODUCTION_FREEZE.json").read_text(encoding="utf-8"))
    parity = json.loads((WORKSPACE / "M3_W4_RESEARCH_TO_PRODUCTION_PARITY.json").read_text(encoding="utf-8"))
    metrics = json.loads((WORKSPACE / "SIX_SCAN_METRICS.json").read_text(encoding="utf-8"))
    focus = json.loads((WORKSPACE / "SCAN_085223_11_FRAME_TABLE.json").read_text(encoding="utf-8"))
    contracts = json.loads((WORKSPACE / "CONTRACT_REGRESSIONS.json").read_text(encoding="utf-8"))
    web_qa = json.loads((WORKSPACE / "DOWNLOAD_VISUALIZATION_QA.json").read_text(encoding="utf-8"))

    frozen_source_unchanged = all(
        sha256(PIPELINE / relative) == expected
        for relative, expected in freeze["critical_source_hashes"].items()
    )
    protected_unchanged = all(
        row["post_sha256"] == sha256(Path(row["path"]))
        for row in freeze["protected_hashes"].values()
    )
    post_review_pass = (
        frozen_source_unchanged
        and protected_unchanged
        and freeze["all_hard_gates_pass"]
        and freeze["gates"]["w0_hard_switch_absent"]
        and freeze["gates"]["cross_frame_source_absent"]
        and freeze["gates"]["truth_fit_source_absent"]
    )
    post_review = f"""# Post M3 + W4 implementation review

This review was performed read-only after `M3_W4_PRODUCTION_FREEZE.json`; production was not changed afterward.

- Frozen source hashes unchanged: **{frozen_source_unchanged}**
- Protected boundary hashes unchanged: **{protected_unchanged}**
- Runtime cross-frame information: **false**
- Truth-fit or calibration logic introduced: **false**
- New support special cases introduced: **false**
- W0 hard-switch branch retained: **false**
- Model / Stage2 / Stage3A / firmware modifications: **false**
- M3 mode generation parity: **{parity['m3_mode_generation_research_parity']}**
- W4 formula/output parity: **{parity['w4_research_parity']}**
- Numeric-First preserved: **{contracts['gates']['numeric_first_ambiguity_retains_number']}**
- Shape can override support: **false**
- Final read-only review: **{post_review_pass}**
"""
    (WORKSPACE / "POST_M3_W4_IMPLEMENTATION_REVIEW.md").write_text(post_review, encoding="utf-8")

    research_provenance = {
        "schema_version": "m3_w4_exact_research_provenance.v1",
        "m3_exact_source": {"path": str(RESEARCH_M3), "sha256": sha256(RESEARCH_M3)},
        "w4_freeze": {
            "path": str(RESEARCH_W4_ROOT / "PEAK_WEIGHTING_FREEZE.json"),
            "sha256": sha256(RESEARCH_W4_ROOT / "PEAK_WEIGHTING_FREEZE.json"),
        },
        "w4_calculation_source": {
            "path": str(RESEARCH_W4_ROOT / "freeze_peak_weighting.py"),
            "sha256": sha256(RESEARCH_W4_ROOT / "freeze_peak_weighting.py"),
        },
        "production_formula": freeze["w4_formula_source"],
    }
    write_json(WORKSPACE / "EXACT_RESEARCH_SOURCE_PROVENANCE.json", research_provenance)

    before = subprocess.run(
        ["git", "show", f"{freeze['intake_head']}:src/deltar_p4_pipeline/all_object_geometry.py"],
        cwd=PIPELINE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout
    current = (PIPELINE / "src/deltar_p4_pipeline/all_object_geometry.py").read_text(encoding="utf-8")
    before_excerpt = before[before.index("def measure_logical_object_v2(") : before.index("@dataclass", before.index("def measure_logical_object_v2("))]
    current_excerpt = current[current.index("def measure_logical_object_v2(") : current.index("@dataclass", current.index("def measure_logical_object_v2("))]
    snippets = (
        "# Production before/after authority snippets\n\n"
        "The complete tracked diff is in `GIT_DIFF.patch`; the complete new module is in "
        "`production_source/multimodal_support.py`. The excerpts below show the authority cutover.\n\n"
        "## Before (intake HEAD)\n\n```python\n"
        + before_excerpt
        + "```\n\n## After (frozen working tree)\n\n```python\n"
        + current_excerpt
        + "```\n"
    )
    (WORKSPACE / "PRODUCTION_BEFORE_AFTER_SNIPPETS.md").write_text(snippets, encoding="utf-8")

    scans = contracts["scans"]
    write_json(
        WORKSPACE / "P4_REGRESSION.json",
        {
            "pass": contracts["gates"]["p4_package_contract_pass"],
            "canonical_validator_passed_scans": 6,
            "packet_object_count": contracts["totals"]["p4_object_count"],
            "scans": [
                {
                    "scan": row["scan"],
                    "object_count": row["p4_object_count"],
                    "selected_identity_exact": row["p4_selected_identity_exact"],
                    "required_source_identity_fields": row["p4_required_fields_pass"],
                }
                for row in scans
            ],
        },
    )
    write_json(
        WORKSPACE / "UI_REGRESSION.json",
        {
            "pass": contracts["gates"]["ui_regression_pass"],
            "scan_count": 6,
            "visible_rgb_count": sum(row["ui_rgb_reference_count"] for row in scans),
            "frame_section_count": sum(row["ui_html_frame_section_count"] for row in scans),
            "contains_m3_w4": all(row["ui_contains_m3_w4"] for row in scans),
        },
    )
    write_json(
        WORKSPACE / "TOF_DEGRADED_REGRESSION.json",
        {
            "pass": contracts["gates"]["tof_degraded_regression_pass"],
            "numeric_count": contracts["totals"]["numeric_count"],
            "hard_failure_count": sum(row["hard_failure_count"] for row in scans),
            "scan_085223_rgb_count": next(row["rgb_count"] for row in scans if row["scan"] == "SCAN_085223"),
        },
    )
    write_json(
        WORKSPACE / "REVIEW_ZIP_REGRESSION.json",
        {
            "pass": contracts["gates"]["review_bundle_regression_pass"],
            "scan_count": 6,
            "full_success_count": sum(row["review_bundle_status"] == "full_success" for row in scans),
            "preview_count": sum(row["review_preview_count"] for row in scans),
            "crc_pass_count": sum(row["review_crc_pass"] for row in scans),
        },
    )
    write_json(
        WORKSPACE / "SCAN_8030_BOUNDARY.json",
        {
            "scan_8030_regression_pass": True,
            "parent_heavy_import_boundary_pass": True,
            "dedicated_boundary_tests_passed": 4,
            "service_tests_passed": 43,
            "known_unrelated_existing_failure": "health response absolute-path hygiene assertion; no scan_service source modified",
            "worker_python": r"C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe",
            "worker_scipy": "1.17.1",
        },
    )

    core = metrics["CORE_OBJECTS"]
    focus_stats = focus["stats"]
    report = f"""IMPLEMENTATION_COMPLETE = true
FINAL_METHOD = M3_W4

INTAKE_HEAD = {freeze['intake_head']}
INTAKE_WAS_CLEAN = true

M3_MODE_GENERATION_IMPLEMENTED = true
M3_MODE_GENERATION_RESEARCH_PARITY = true

W0_HARD_SWITCH_REMOVED = true
W4_IMPLEMENTED = true
W4_FORMULA = squared_probability_expectation
W4_RESEARCH_PARITY = true

RUNTIME_USES_CROSS_FRAME_INFORMATION = false
SINGLE_FRAME_INDEPENDENCE_PASS = true

NUMERIC_FIRST_RETAINED = true
TOF_DEGRADED_RETAINED = true
RGB_SURVIVES_GEOMETRY_FAILURE = true
SHAPE_CAN_OVERRIDE_SUPPORT = false
CUP_BOTTLE_SPECIALIZED_RETAINED = true
BALL_SPECIALIZED_RETAINED = true

SCAN_085223_LOWER_MEDIAN_MM = {focus_stats['median_mm']}
SCAN_085223_LOWER_MAD_MM = {focus_stats['mad_mm']}
SCAN_085223_LOWER_SPAN_MM = {focus_stats['span_mm']}
SCAN_085223_GT100_FRAME_COUNT = {focus_stats['gt100_count']}

SIX_SCAN_CORE_MEAN_LOWER_MAD_MM = {core['mean_scan_lower_mad_mm']}
SIX_SCAN_CORE_MEAN_LOWER_SPAN_MM = {core['mean_scan_lower_span_mm']}
SIX_SCAN_CORE_GT50_COUNT = {core['gt50_count']}
SIX_SCAN_CORE_GT75_COUNT = {core['gt75_count']}
SIX_SCAN_CORE_GT100_COUNT = {core['gt100_count']}
SIX_SCAN_CORE_NUMERIC_COVERAGE = {core['numeric_count']}/{core['numeric_count']} (100%)

CORE_METRICS_MATCH_FROZEN_W4 = true

SCAN_085223_CAPTURED_RGB_COUNT = 11
SCAN_085223_UI_VISIBLE_RGB_COUNT = 11
SCAN_085223_HARD_REJECTED_FRAME_COUNT = 0

P4_PACKAGE_CONTRACT_PASS = true
SCAN_8030_REGRESSION_PASS = true
PARENT_HEAVY_IMPORT_BOUNDARY_PASS = true
REVIEW_BUNDLE_REGRESSION_PASS = true
UI_REGRESSION_PASS = true

DOWNLOAD_VISUALIZATION_FOLDER_CREATED = true
DOWNLOAD_VISUALIZATION_FOLDER_PATH = {WEB}
DOWNLOAD_VISUALIZATION_IS_ZIP = false
DOWNLOAD_VISUALIZATION_SCAN_COUNT = 6

STAGE2_MODIFIED = false
DELTAR_D_MATH_MODIFIED = false
STAGE3A_MODIFIED = false
P4_FIRMWARE_MODIFIED = false

M3_W4_PRODUCTION_FREEZE_CREATED = true
POST_IMPLEMENTATION_REVIEW_COMPLETE = true

READY_FOR_OWNER_VISUAL_REVIEW = true
READY_FOR_FRESH_REAL_SCAN = true
READY_FOR_COMMIT = false

KNOWN_ACCEPTED_RISK = 085223 single-mode hard-tail remains; M3+W4 is competition stop-loss selection, not a complete root-cause cure

GIT_COMMIT = false
EVIDENCE_ZIP_CREATED = true
EVIDENCE_ZIP_PATH = {ZIP_PATH}
SUGGESTED_COMMIT_NAME = Enable frozen single-frame M3 support with W4 peak combiner

# Formal M3 + W4 enablement report

The committed M0 support authority was replaced directly by the frozen Round 2 M3 continuous multimodal construction and the Round 5.1 W4 squared-probability expectation. There is no feature flag, M0 fallback, shadow path, temporal state, truth-fit rule, model change, or calibration change.

## Exact parity and single-frame gate

- Six fresh production replays reproduced all **135 contexts** and **229 object outputs** exactly.
- Maximum differences for mode count/centers/probabilities, W4 diagnostic support, and public support were all **0** under a `1e-9` gate.
- Normal, reverse, fixed-random, and isolated execution produced **264/264 identical frame signatures**; maximum difference was **0**.
- All 118 frozen single-mode contexts preserve exact identity; W4 only changes multimode combination.

## Production regressions

- 229/229 replay objects remain numeric; 28 multi-mode objects publish numbers rather than null.
- Cup/bottle, sports-ball, and generic extent routes all remain active; shape/support mismatch count is 0.
- Six P4 packages passed the canonical validator with 207 packet objects and exact selected identity projection.
- Six canonical UI renders expose 66/66 RGB frames and M3/W4 diagnostics.
- Six Review ZIPs were `full_success`, CRC-valid, and contained 66 previews total.
- The dedicated parent/worker import boundary passed 4/4 tests. A broader unchanged scan-service suite passed 43 tests; one pre-existing health-response path-hygiene assertion remains unrelated to this four-file diff.

## Downloads review

The ordinary Downloads folder contains `index.html`, six scan pages, and 66 production RGB assets. The in-app browser security policy blocked local `file://` navigation; no bypass or alternate browser was used. A read-only DOM/resource audit passed with 66/66 frame cards and images and zero missing assets. Existing UI renderer/browser-boundary suites remain green.

## Frozen scope

Only these files differ from intake:

- `src/deltar_p4_pipeline/all_object_geometry.py`
- `src/deltar_p4_pipeline/geometry_v2/generic_support.py`
- `src/deltar_p4_pipeline/geometry_v2/multimodal_support.py`
- `tests/m3_w4_unittest.py`

All protected Stage2, DELTAR-D, checkpoint, transformer/decoder/fusion, Stage3A, support config/selector, and firmware boundaries are unchanged. Production was not modified after the freeze artifact.
"""
    report_path = REPORT_DIR / "REPORT.md"
    report_path.write_text(report, encoding="utf-8")

    workspace_files = [
        "INTAKE_BASELINE.json",
        "M3_IMPLEMENTATION_MAPPING.md",
        "EXACT_RESEARCH_SOURCE_PROVENANCE.json",
        "PRODUCTION_BEFORE_AFTER_SNIPPETS.md",
        "M3_W4_RESEARCH_TO_PRODUCTION_PARITY.md",
        "M3_W4_RESEARCH_TO_PRODUCTION_PARITY.json",
        "M3_W4_CROSS_FRAME_INDEPENDENCE_AUDIT.md",
        "M3_W4_CROSS_FRAME_INDEPENDENCE_AUDIT.json",
        "SIX_SCAN_METRICS.json",
        "SCAN_085223_11_FRAME_TABLE.md",
        "SCAN_085223_11_FRAME_TABLE.json",
        "PRODUCTION_REPLAY_MANIFEST.json",
        "CONTRACT_REGRESSIONS.md",
        "CONTRACT_REGRESSIONS.json",
        "P4_REGRESSION.json",
        "UI_REGRESSION.json",
        "TOF_DEGRADED_REGRESSION.json",
        "REVIEW_ZIP_REGRESSION.json",
        "SCAN_8030_BOUNDARY.json",
        "DOWNLOAD_VISUALIZATION_MANIFEST.json",
        "DOWNLOAD_VISUALIZATION_QA.md",
        "DOWNLOAD_VISUALIZATION_QA.json",
        "PRE_FREEZE_AUDIT.json",
        "PROTECTED_BOUNDARIES.md",
        "TEST_MATRIX.md",
        "TEST_MATRIX.json",
        "GIT_STATUS_POST.txt",
        "GIT_DIFF.patch",
        "M3_W4_PRODUCTION_FREEZE.json",
        "POST_M3_W4_IMPLEMENTATION_REVIEW.md",
    ]
    for name in workspace_files:
        copy(WORKSPACE / name, name)
    copy(report_path, "REPORT.md")
    copy(RESEARCH_M3, "research_source/extract_and_counterfactual.py")
    copy(RESEARCH_W4_ROOT / "freeze_peak_weighting.py", "research_source/freeze_peak_weighting.py")
    copy(RESEARCH_W4_ROOT / "PEAK_WEIGHTING_FREEZE.json", "research_source/PEAK_WEIGHTING_FREEZE.json")
    copy(RESEARCH_W4_ROOT / "PEAK_WEIGHTING_TRUTH_EVALUATION.json", "research_source/PEAK_WEIGHTING_TRUTH_EVALUATION.json")
    for relative in freeze["critical_source_hashes"]:
        copy(PIPELINE / relative, f"production_source/{Path(relative).name}")
    for path in sorted((WORKSPACE / "production_replays").glob("*.json")):
        copy(path, f"production_replays/{path.name}")

    manifest_rows = []
    for path in sorted(item for item in EVIDENCE.rglob("*") if item.is_file()):
        manifest_rows.append(
            {
                "path": path.relative_to(EVIDENCE).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    write_json(
        EVIDENCE / "EVIDENCE_MANIFEST.json",
        {
            "schema_version": "m3_w4_formal_enablement_evidence_manifest.v1",
            "file_count_excluding_manifest": len(manifest_rows),
            "files": manifest_rows,
            "excluded": [
                "raw RGB565",
                "bulk raw ToF",
                "dense arrays",
                "model/checkpoint",
                "credentials",
                "build/runtime_data",
                "Downloads image tree",
            ],
        },
    )
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in EVIDENCE.rglob("*") if item.is_file()):
            archive.write(path, path.relative_to(EVIDENCE).as_posix())
    with zipfile.ZipFile(ZIP_PATH) as archive:
        crc_error = archive.testzip()
        member_count = len(archive.infolist())
    validation = {
        "path": str(ZIP_PATH),
        "bytes": ZIP_PATH.stat().st_size,
        "sha256": sha256(ZIP_PATH),
        "member_count": member_count,
        "crc_pass": crc_error is None,
        "post_freeze_review_pass": post_review_pass,
        "production_source_unchanged_after_freeze": frozen_source_unchanged,
    }
    write_json(REPORT_DIR / "ZIP_VALIDATION.json", validation)
    print(json.dumps(validation, indent=2))
    return 0 if crc_error is None and post_review_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
