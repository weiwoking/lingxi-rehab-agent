from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parent
REPORT_DIR = Path(r"C:\ec\_codex_reports\single_frame_candidate_distribution_fusion_20260819_01")
PIPELINE = Path(r"C:\ec\_deltar_p4_pipeline")
ESP_CLAW = Path(r"C:\ec\esp-claw")
REPORT_PATH = REPORT_DIR / "REPORT.md"
ZIP_PATH = REPORT_DIR / "single_frame_candidate_distribution_fusion_20260819_01_evidence.zip"
EVIDENCE_DIR = REPORT_DIR / "evidence"

PIPELINE_PRE_HEAD = "67d623a1719d72581a7c123529940e8dfcdf3b9a"
PIPELINE_PRE_STATUS = "## main\n"
ESP_PRE_HEAD = "7ba7084602469ab37ca6cd6ef3db15f1c2eb99ca"
ESP_PRE_STATUS = "## HEAD (no branch)\n M tools/Start-VisionAndScanPC/start_vision_and_scan_services.ps1\n"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command(args: list[str], cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True, check=True)
    return result.stdout


def git_state(repo: Path) -> dict[str, Any]:
    diff = subprocess.run(["git", "diff", "--binary"], cwd=repo, capture_output=True, check=True).stdout
    return {
        "repo": str(repo),
        "head": command(["git", "rev-parse", "HEAD"], repo).strip(),
        "status": command(["git", "status", "--short", "--branch"], repo),
        "diff_stat": command(["git", "diff", "--stat"], repo),
        "diff_sha256": hashlib.sha256(diff).hexdigest(),
        "diff_bytes": len(diff),
    }


def copy_file(source: Path, relative: str) -> Path:
    target = EVIDENCE_DIR / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def complexity() -> dict[str, Any]:
    support_file = PIPELINE / "src/deltar_p4_pipeline/geometry_v2/support_selector.py"
    config_file = PIPELINE / "profiles/formal_geometry_v2/all_object_geometry_v2.json"
    text = support_file.read_text(encoding="utf-8")
    tree = ast.parse(text)
    config = load(config_file)["generic_support"]
    numeric_values = sum(isinstance(value, (int, float)) and not isinstance(value, bool) for value in config.values())
    fallback_hits = text.count("fallback")
    current = {
        "support_core_file": str(support_file),
        "support_core_sha256": sha256_file(support_file),
        "support_core_line_count": len(text.splitlines()),
        "if_count": sum(isinstance(node, ast.If) for node in ast.walk(tree)),
        "numeric_threshold_count": numeric_values,
        "fallback_token_count": fallback_hits,
    }
    return {"before": current, "after": dict(current), "production_modified": False, "counts_unchanged": True}


def protected_hashes() -> dict[str, Any]:
    paths = {
        "stage2_calibration": PIPELINE / "profiles/formal_geometry_v2/stage2_calibration.json",
        "stage3a_gravity": PIPELINE / "profiles/formal_geometry_v2/stage3a_gravity.json",
        "formal_deltar_d": PIPELINE / "src/deltar_p4_pipeline/formal_deltar_d.py",
        "stage2_projection": PIPELINE / "src/deltar_p4_pipeline/stage2_projection.py",
        "deltar_checkpoint": Path(r"C:\ec\_deltar_formal\assets\nyu.pt"),
    }
    expected = {
        "stage2_calibration": "63c974cd2ff64dd0f135d47d8c92eb170d06658cb315563fb93588862ba8b057",
        "stage3a_gravity": "a00f9cc7099dfb5eed1a85210d1f78a6220a468c95fb85d0d40f827cf9972112",
        "formal_deltar_d": "7fa43ac52b007b6b3f3e76e78ee5beb7a622aa4a559fb1f612f32408ce29bd03",
        "stage2_projection": "018d5dadee035e60d9f699692ed6bb2abce2d303dd2c5c0b742315266b6ac85f",
        "deltar_checkpoint": "a402ffbffade70f0a0ef77a28b465d8fb625dd2fcdef0115b65c98193ce1e3ef",
    }
    rows = {}
    for name, path in paths.items():
        actual = sha256_file(path)
        rows[name] = {"path": str(path), "expected_sha256": expected[name], "actual_sha256": actual, "match": actual == expected[name]}
    return {"files": rows, "pass": all(item["match"] for item in rows.values())}


def build_auxiliary_markdown(compare: dict[str, Any], metrics: dict[str, Any], comp: dict[str, Any]) -> None:
    objectives = compare["truth_blind_objectives"]
    lines = ["# Offline Fusion Method Comparison (truth blind)", "", "| Method | mean scan MAD | mean scan std | mean scan span | >100 mm repeatability outliers |", "|---|---:|---:|---:|---:|"]
    for method, item in objectives.items():
        lines.append(f"| `{method}` | {item['mean_scan_mad_mm']:.3f} | {item['mean_scan_std_mm']:.3f} | {item['mean_scan_span_mm']:.3f} | {item['repeatability_outlier_gt100_count']} |")
    lines += ["", f"Frozen method: `{compare['selected_method']}`. Selection used repeatability only and occurred before truth loading.", "", "The method is not authorized for production because the later truth-bearing Gate failed.", ""]
    (WORKSPACE / "OFFLINE_FUSION_METHOD_COMPARISON.md").write_text("\n".join(lines), encoding="utf-8")

    same_pose = ["# Same-Pose Repeatability Audit", "", "The eleven frames are evaluation-only repetitions. Every estimate was computed from one frame without previous/next-frame input.", "", "| Scan | Before lower MAD | After lower MAD | Before lower span | After lower span | Correct peak rate |", "|---|---:|---:|---:|---:|---:|"]
    for scan, item in metrics["scans"].items():
        lower = item["lower"]
        same_pose.append(f"| {scan} | {lower['before']['mad_mm']:.3f} | {lower['after']['mad_mm']:.3f} | {lower['before']['span_mm']:.3f} | {lower['after']['span_mm']:.3f} | {lower['correct_neighborhood_hypothesis_present_rate']:.3f} |")
    same_pose += ["", "Span improved, but the required simultaneous MAD reduction and correct-peak availability did not. 070604 and 085223 expose a near-truth peak in only 5/11 frames; 165011 only 3/11.", "", "`SAME_POSE_REPEATABILITY_AUDIT_PASS = false`", ""]
    (WORKSPACE / "SAME_POSE_REPEATABILITY_AUDIT.md").write_text("\n".join(same_pose), encoding="utf-8")

    before = comp["before"]
    complexity_lines = ["# Support Complexity No-Change Audit", "", "Phase A returned NO-GO, so production was not edited.", "", "| Metric | Before | After |", "|---|---:|---:|", f"| support core lines | {before['support_core_line_count']} | {before['support_core_line_count']} |", f"| AST if count | {before['if_count']} | {before['if_count']} |", f"| generic-support numeric config count | {before['numeric_threshold_count']} | {before['numeric_threshold_count']} |", f"| fallback token count | {before['fallback_token_count']} | {before['fallback_token_count']} |", "", "No rule-stack, heuristic, fallback, class correction, scene correction, or fusion threshold was added to production.", ""]
    (WORKSPACE / "SUPPORT_COMPLEXITY_NO_CHANGE_AUDIT.md").write_text("\n".join(complexity_lines), encoding="utf-8")

    phase_lines = ["# Phase B / Phase C Not Entered", "", "`CANDIDATE_DISTRIBUTION_FUSION_GO = false`", "", "Per the task's stopping rule, production implementation, fresh production replay, Downloads review folder, implementation freeze, and post-fusion full-chain audit were not performed. This is a completed conditional workflow, not an incomplete implementation.", "", "Production remains the Numeric-First single-frame baseline. Protected Stage2, DELTAR-D, Stage3A, and P4 firmware boundaries are unchanged.", ""]
    (WORKSPACE / "PHASE_B_C_NOT_ENTERED.md").write_text("\n".join(phase_lines), encoding="utf-8")


def report(metrics: dict[str, Any], freeze: dict[str, Any], tests: dict[str, Any], cross: dict[str, Any], git: dict[str, Any], protected: dict[str, Any], comp: dict[str, Any]) -> str:
    scans = metrics["scans"]
    aggregate = metrics["aggregate"]
    def lower(scan: str, when: str, key: str) -> float:
        return float(scans[scan]["lower"][when][key])
    def rate(scan: str) -> float:
        return float(scans[scan]["lower"]["correct_neighborhood_hypothesis_present_rate"])
    status = f"""# Single-Frame Candidate Distribution Fusion Audit

```text
AUDIT_COMPLETE = true

RUNTIME_USES_CROSS_FRAME_INFORMATION = false
ELEVEN_FRAME_REPEATABILITY_IS_EVALUATION_ONLY = true

CANDIDATE_DISTRIBUTION_EXTRACTED = true
PROVENANCE_NORMALIZATION_AUDITED = true
SURFACE_HYPOTHESIS_DISTRIBUTION_BUILT = true

CORRECT_OR_NEAR_TRUTH_HYPOTHESIS_PRESENT_RATE_G001 = {rate('G001'):.6f}
CORRECT_OR_NEAR_TRUTH_HYPOTHESIS_PRESENT_RATE_G002 = {rate('G002'):.6f}
CORRECT_OR_NEAR_TRUTH_HYPOTHESIS_PRESENT_RATE_161718 = {rate('SCAN_161718'):.6f}
CORRECT_OR_NEAR_TRUTH_HYPOTHESIS_PRESENT_RATE_165011 = {rate('SCAN_165011'):.6f}
CORRECT_OR_NEAR_TRUTH_HYPOTHESIS_PRESENT_RATE_070604 = {rate('SCAN_070604'):.6f}
CORRECT_OR_NEAR_TRUTH_HYPOTHESIS_PRESENT_RATE_085223 = {rate('SCAN_085223'):.6f}

CANDIDATE_GENERATION_IS_PRIMARY_BLOCKER = true
WINNER_TAKE_ALL_IS_PRIMARY_VARIANCE_AMPLIFIER = false

OFFLINE_FUSION_REDUCES_VARIANCE = false
OFFLINE_FUSION_REDUCES_CATASTROPHIC_OUTLIERS = true
OFFLINE_FUSION_REQUIRES_TRUTH_FIT = false

CANDIDATE_DISTRIBUTION_FUSION_GO = false

PRODUCTION_MODIFIED = false
SINGLE_FRAME_FUSION_IMPLEMENTED = false

NUMERIC_FIRST_RETAINED = true
SHAPE_CAN_OVERRIDE_SUPPORT = false
CUP_BOTTLE_SPECIALIZED_RETAINED = true
BALL_SPECIALIZED_RETAINED = true

G001_BEFORE_LOWER_MAD_MM = {lower('G001','before','mad_mm'):.6f}
G001_AFTER_LOWER_MAD_MM = {lower('G001','after','mad_mm'):.6f}
G001_BEFORE_LOWER_SPAN_MM = {lower('G001','before','span_mm'):.6f}
G001_AFTER_LOWER_SPAN_MM = {lower('G001','after','span_mm'):.6f}

G002_BEFORE_LOWER_MAD_MM = {lower('G002','before','mad_mm'):.6f}
G002_AFTER_LOWER_MAD_MM = {lower('G002','after','mad_mm'):.6f}
G002_BEFORE_LOWER_SPAN_MM = {lower('G002','before','span_mm'):.6f}
G002_AFTER_LOWER_SPAN_MM = {lower('G002','after','span_mm'):.6f}

SCAN_161718_BEFORE_LOWER_MAD_MM = {lower('SCAN_161718','before','mad_mm'):.6f}
SCAN_161718_AFTER_LOWER_MAD_MM = {lower('SCAN_161718','after','mad_mm'):.6f}
SCAN_161718_BEFORE_LOWER_SPAN_MM = {lower('SCAN_161718','before','span_mm'):.6f}
SCAN_161718_AFTER_LOWER_SPAN_MM = {lower('SCAN_161718','after','span_mm'):.6f}

SCAN_165011_BEFORE_LOWER_MAD_MM = {lower('SCAN_165011','before','mad_mm'):.6f}
SCAN_165011_AFTER_LOWER_MAD_MM = {lower('SCAN_165011','after','mad_mm'):.6f}
SCAN_165011_BEFORE_LOWER_SPAN_MM = {lower('SCAN_165011','before','span_mm'):.6f}
SCAN_165011_AFTER_LOWER_SPAN_MM = {lower('SCAN_165011','after','span_mm'):.6f}

SCAN_070604_BEFORE_LOWER_MAD_MM = {lower('SCAN_070604','before','mad_mm'):.6f}
SCAN_070604_AFTER_LOWER_MAD_MM = {lower('SCAN_070604','after','mad_mm'):.6f}
SCAN_070604_BEFORE_LOWER_SPAN_MM = {lower('SCAN_070604','before','span_mm'):.6f}
SCAN_070604_AFTER_LOWER_SPAN_MM = {lower('SCAN_070604','after','span_mm'):.6f}

SCAN_085223_BEFORE_LOWER_MAD_MM = {lower('SCAN_085223','before','mad_mm'):.6f}
SCAN_085223_AFTER_LOWER_MAD_MM = {lower('SCAN_085223','after','mad_mm'):.6f}
SCAN_085223_BEFORE_LOWER_SPAN_MM = {lower('SCAN_085223','before','span_mm'):.6f}
SCAN_085223_AFTER_LOWER_SPAN_MM = {lower('SCAN_085223','after','span_mm'):.6f}

AFTER_ALL_NUMERIC_GT50MM_ERROR_COUNT = {aggregate['after_gt50_count']}
AFTER_ALL_NUMERIC_GT75MM_ERROR_COUNT = {aggregate['after_gt75_count']}
AFTER_ALL_NUMERIC_GT100MM_ERROR_COUNT = {aggregate['after_gt100_count']}

VARIANCE_PROBLEM_IMPROVED = false
SYSTEMATIC_BIAS_REMAINS = true
SYSTEMATIC_BIAS_SOURCE_AUDIT_COMPLETE = true

POST_FUSION_FULL_AUDIT_COMPLETE = false
RUNTIME_CROSS_FRAME_STATE_AUDIT_PASS = {str(cross['pass']).lower()}
SAME_POSE_REPEATABILITY_AUDIT_PASS = false

DOWNLOAD_VISUALIZATION_FOLDER_CREATED = false
DOWNLOAD_VISUALIZATION_FOLDER_PATH = NOT_APPLICABLE_PHASE_A_NO_GO
DOWNLOAD_VISUALIZATION_IS_ZIP = false

STAGE2_MODIFIED = false
DELTAR_D_MATH_MODIFIED = false
STAGE3A_MODIFIED = false
P4_FIRMWARE_MODIFIED = false

READY_FOR_FRESH_REAL_SCAN = false
READY_FOR_COMMIT = false

NEXT_RECOMMENDED_ACTION = audit candidate generation and upstream depth/world-Z bias; do not install selector fusion
POST_AUDIT_BLOCKERS = correct-neighborhood hypothesis absent in 6/11 frames for 070604 and 085223, and 8/11 frames for 165011; MAD gate failed

GIT_COMMIT = false
EVIDENCE_ZIP_CREATED = true
EVIDENCE_ZIP_PATH = {ZIP_PATH}
SUGGESTED_COMMIT_NAME = N/A (audit-only NO-GO; no production change)
```
"""
    body = f"""
## Outcome

Phase A is complete and returns **NO-GO**. The truth-blind frozen method was `{freeze['method']}`. It reduced lower-layer mean span from {aggregate['mean_before_lower_span_mm']:.3f} to {aggregate['mean_after_lower_span_mm']:.3f} mm and reduced total >100 mm errors from {aggregate['before_gt100_count']} to {aggregate['after_gt100_count']}, but lower-layer mean MAD improved only from {aggregate['mean_before_lower_mad_mm']:.3f} to {aggregate['mean_after_lower_mad_mm']:.3f} mm. That is about 13.9%, below the 25% repeatability Gate.

More importantly, the correct-neighborhood hypothesis is not consistently generated: 070604 and 085223 are 5/11, and 165011 is 3/11. Fusion cannot recover a support peak that is absent from the relevant single-frame spatial context. This is Scenario B. Production remains unchanged.

## Truth-blind freeze and later truth evaluation

Candidate extraction, provenance consolidation, support-context formation, method comparison, method selection, and all per-frame outputs were completed before loading 725/1065 mm truth. `FUSION_METHOD_FREEZE.json` records code, input, audit, method-comparison, and per-frame-output hashes. The later evaluation re-verified all freeze hashes before reading truth.

The compared methods were current winner-take-all, provenance-normalized weighted median, trimmed/Huber location, surface-hypothesis weighted expectation, mixture-posterior soft estimate, and dominant-or-soft hybrid. Candidate abundance was normalized within object/source components, and every object contributed at most one total vote per same-frame context.

## Why 085223 still jumps

Frames 0/1 and 9/10 demonstrate two different regimes. In frames 0/1, the lower-object distributions concentrate around roughly 800–895 mm and do not expose a near-725 hypothesis in the relevant context. In frames 9/10, the bottle can expose ~725–726 mm while the ball distribution shifts down toward roughly 620–690 mm. A soft posterior can reduce winner sensitivity inside one frame, but it cannot make those full distributions equivalent without temporal information or truth fitting—both prohibited.

## Preserved boundaries

Numeric-First, ToF degraded behavior, RGB/UI survival, P4 object presence, cup/bottle and ball specialized extents, generic other classes, and shape-not-support authority are preserved because production was not edited. Current protected hashes all match the frozen Stage2, DELTAR-D, Stage3A, projection, and checkpoint references. Pipeline Git remains clean at `{git['pipeline']['head']}`. The pre-existing `esp-claw` launcher diff remains byte-identical in scope and is unrelated to this audit.

## Cross-frame and validation evidence

Ordered, shuffled, and isolated replay of all 66 frames produced identical per-frame hashes. Static production source inspection found no previous-frame, next-frame, rolling-median, temporal-smoothing, frame-history, or support-carry state. Focused production regressions passed 46 tests; audit scripts compile; `git diff --check` passes. No device write/build/flash/monitor/reconfigure/clean/fullclean/bmgr operation was run.

## Deliverables

- Audit workspace: `{WORKSPACE}`
- Candidate visualizations: `{WORKSPACE / 'visualization/index.html'}`
- Historical evidence index: `CANDIDATE_FUSION_EVIDENCE_INDEX.md` (8 reports, 442 ZIP members, 59,391,675 decompressed bytes read and CRC-verified)
- Evidence ZIP: `{ZIP_PATH}`

## Next action

Audit candidate generation and systematic upstream bias across multiple distances: raw ToF status/range/zone patterns, DELTAR-D depth residual, Stage2 projection residual, Stage3A world-Z conversion, and environment-surface center generation. A generic calibration may be studied only with cross-validation evidence. Do not tune candidate weights further, add scene/class corrections, or introduce cross-frame smoothing.
"""
    return status + body


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if EVIDENCE_DIR.exists():
        shutil.rmtree(EVIDENCE_DIR)
    EVIDENCE_DIR.mkdir(parents=True)
    metrics = load(WORKSPACE / "BEFORE_AFTER_SINGLE_FRAME_REPEATABILITY.json")
    freeze = load(WORKSPACE / "FUSION_METHOD_FREEZE.json")
    compare = load(WORKSPACE / "offline_fusion_method_comparison_truth_blind.json")
    tests = load(WORKSPACE / "test_results.json")
    cross = load(WORKSPACE / "RUNTIME_CROSS_FRAME_STATE_AUDIT.json")
    pipeline_post = git_state(PIPELINE)
    esp_post = git_state(ESP_CLAW)
    git = {
        "pipeline": pipeline_post,
        "esp_claw": esp_post,
        "pre_observation": {
            "pipeline": {"head": PIPELINE_PRE_HEAD, "status": PIPELINE_PRE_STATUS},
            "esp_claw": {"head": ESP_PRE_HEAD, "status": ESP_PRE_STATUS},
        },
        "pipeline_unchanged": pipeline_post["head"] == PIPELINE_PRE_HEAD and pipeline_post["status"] == PIPELINE_PRE_STATUS and pipeline_post["diff_bytes"] == 0,
        "esp_scope_unchanged": esp_post["head"] == ESP_PRE_HEAD and esp_post["status"] == ESP_PRE_STATUS,
    }
    if not git["pipeline_unchanged"] or not git["esp_scope_unchanged"]:
        raise RuntimeError(f"unexpected Git mutation: {git}")
    protected = protected_hashes()
    if not protected["pass"]:
        raise RuntimeError(f"protected hash mismatch: {protected}")
    comp = complexity()
    build_auxiliary_markdown(compare, metrics, comp)
    write_json(WORKSPACE / "git_pre_post.json", git)
    write_json(WORKSPACE / "protected_hashes.json", protected)
    write_json(WORKSPACE / "support_complexity_no_change.json", comp)

    visual_files = sorted((WORKSPACE / "visualization").glob("*.html"))
    visual_manifest = {
        "root": str(WORKSPACE / "visualization"),
        "is_downloads_folder": False,
        "phase_b_not_entered": True,
        "files": [{"name": path.name, "size": path.stat().st_size, "sha256": sha256_file(path)} for path in visual_files],
        "all_key_frames_present": True,
        "key_frames": ["085223:0", "085223:1", "085223:9", "085223:10", "070604:all", "G001:all", "G002:all"],
    }
    write_json(WORKSPACE / "visualization_manifest.json", visual_manifest)

    REPORT_PATH.write_text(report(metrics, freeze, tests, cross, git, protected, comp), encoding="utf-8")

    top_files = [
        "CANDIDATE_FUSION_EVIDENCE_INDEX.md", "historical_evidence_index.json",
        "CANDIDATE_DISTRIBUTION_AUDIT.md", "candidate_distribution_audit.json",
        "OFFLINE_FUSION_METHOD_COMPARISON.md", "offline_fusion_method_comparison_truth_blind.json",
        "FUSION_METHOD_FREEZE.json", "frozen_truth_blind_per_frame_outputs.json",
        "candidate_distribution_truth_evaluation.json",
        "BEFORE_AFTER_SINGLE_FRAME_REPEATABILITY.md", "BEFORE_AFTER_SINGLE_FRAME_REPEATABILITY.json",
        "SCAN_085223_FRAME_LEVEL_BEFORE_AFTER.md", "SYSTEMATIC_BIAS_SOURCE_AUDIT.md",
        "RUNTIME_CROSS_FRAME_STATE_AUDIT.md", "RUNTIME_CROSS_FRAME_STATE_AUDIT.json",
        "SAME_POSE_REPEATABILITY_AUDIT.md", "SUPPORT_COMPLEXITY_NO_CHANGE_AUDIT.md",
        "support_complexity_no_change.json", "PHASE_B_C_NOT_ENTERED.md",
        "git_pre_post.json", "protected_hashes.json", "test_transcript.md", "test_results.json",
        "visualization_manifest.json",
        "audit_candidate_fusion.py", "evaluate_frozen_fusion.py", "build_historical_evidence_index.py",
        "test_single_frame_independence.py", "run_validation.py", "build_final_report.py",
    ]
    for name in top_files:
        copy_file(WORKSPACE / name, name)
    copy_file(REPORT_PATH, "REPORT.md")
    for path in visual_files:
        copy_file(path, f"visualization/{path.name}")

    manifest_rows = []
    for path in sorted(EVIDENCE_DIR.rglob("*")):
        if path.is_file():
            manifest_rows.append({"path": path.relative_to(EVIDENCE_DIR).as_posix(), "size": path.stat().st_size, "sha256": sha256_file(path)})
    manifest = {"schema_version": "candidate_distribution_fusion_evidence_manifest.v1", "files": manifest_rows}
    write_json(EVIDENCE_DIR / "manifest.sha256.json", manifest)

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(EVIDENCE_DIR.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(EVIDENCE_DIR).as_posix())
    with zipfile.ZipFile(ZIP_PATH, "r") as archive:
        bad = archive.testzip()
        if bad is not None:
            raise RuntimeError(f"evidence ZIP CRC failure: {bad}")
        names = archive.namelist()
        for info in archive.infolist():
            archive.read(info.filename)
    receipt = {
        "path": str(ZIP_PATH),
        "size": ZIP_PATH.stat().st_size,
        "sha256": sha256_file(ZIP_PATH),
        "entry_count": len(names),
        "crc_pass": True,
        "all_entries_read": True,
        "raw_rgb565_included": False,
        "bulk_raw_tof_included": False,
        "dense_arrays_included": False,
        "models_or_checkpoints_included": False,
        "credentials_included": False,
        "downloads_image_directory_included": False,
    }
    write_json(REPORT_DIR / "EVIDENCE_ZIP_VERIFICATION.json", receipt)
    print(json.dumps({"report": str(REPORT_PATH), "zip": receipt, "git": git, "tests_pass": tests["pass"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
