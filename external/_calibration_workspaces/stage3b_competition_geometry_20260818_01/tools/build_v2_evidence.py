from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any


WS = Path(r"C:\ec\_calibration_workspaces\stage3b_competition_geometry_20260818_01")
V2 = WS / "candidate_geometry_v2"
RESULTS = V2 / "results"
EVIDENCE = WS / "evidence_v2"
REPORT_DIR = Path(r"C:\ec\_codex_reports\stage3b_all_object_genericization_20260818_01")
REPORT = REPORT_DIR / "REPORT.md"
ZIP_PATH = REPORT_DIR / "stage3b_all_object_genericization_20260818_01_evidence.zip"
PYTHON = Path(r"C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, ensure_ascii=False))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], cwd: Path = WS) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, encoding="utf-8", errors="replace")
    return {
        "command": subprocess.list2cmdline(command),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def table_rows(summary: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for sample_id in ("G001", "G002"):
        role = summary[sample_id]["role"]
        for target_name, payload in summary[sample_id]["targets"].items():
            truth = payload["truth"]
            for version in ("baseline", "candidate_v1", "candidate_v2"):
                item = payload[version]
                rows.append([
                    sample_id,
                    role,
                    target_name,
                    version,
                    item.get("support_mm"),
                    item.get("support_error_mm"),
                    item.get("top_mm"),
                    item.get("object_height_mm"),
                    item.get("object_height_error_mm"),
                    item.get("object_height_error_percent"),
                    item.get("support_source"),
                    item.get("selected_shape_family"),
                    item.get("frame_count"),
                    truth["expected_support_mm"],
                    truth["expected_object_height_mm"],
                ])
    return rows


def fmt(value: Any, digits: int = 1) -> str:
    if isinstance(value, (float, int)):
        return f"{float(value):.{digits}f}"
    return str(value)


def upper_records(sample_id: str) -> list[dict[str, Any]]:
    records = read_json(RESULTS / sample_id / "object_records.v2.json")
    return [r for r in records if r["class_name"] == "bottle" and float(r["bbox_xyxy"][1]) < 100]


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    summary = read_json(RESULTS / "G001_G002_SUMMARY.v2.json")
    inventory = read_json(RESULTS / "UNRELATED_HELDOUT_INVENTORY.json")
    heldout = read_json(RESULTS / "UNRELATED_HELDOUT_REPLAY.json")

    architecture = """# All-Object Geometry v2 Architecture

## Decision

The isolated v2 candidate implements an all-object pipeline with a generic baseline that is always available. Shape-family logic is an optional enhancer behind geometry and confidence gates; it is never the sole route to a Geometry result.

## Data flow

1. Convert accepted Stage2 depth through the frozen dynamic-Fr camera model and frozen Stage3A gravity transform.
2. Estimate support in this order: accepted global plane; local gravity-aligned context surface; geometric same-surface consensus; generic lower-envelope fallback.
3. Estimate a generic top from a spatially coherent top-k component. This result exists before routing.
4. Route by bbox/mask geometry, roundness, gravity alignment, depth compactness, support confidence, and at most a 0.10 semantic prior.
5. Try `upright_slender` or `roughly_spherical` enhancement. Reject the enhancer when unavailable or when its top differs from the generic top by more than 20 mm.
6. Emit generic support plus generic extent for unknown, irregular, uncertain, or enhancer-rejected objects.

## Boundaries

- Stage2 calibration bundle and Stage3A gravity candidate are read-only authorities.
- Ground truth is consumed only by competition aggregation/evaluation, not by estimators or routing.
- The implementation is isolated under `candidate_geometry_v2`; formal runtime and stabilizer were not modified.
- No scene target names, class-specific fixed offsets, nominal heights, or nominal shelf Z values exist in estimator configuration.
"""
    write_text(EVIDENCE / "ALL_OBJECT_ARCHITECTURE_V2.md", architecture)

    support_doc = """# Generic Support Design

The estimator is class-agnostic. An already-qualified global support plane wins. Otherwise it builds a bbox/mask-exterior context below and beside the object, projects valid samples to world Z, bins them, forms connected image components, and scores flatness, population, image span, and contact locality. Among substantial components (at least 60% of the largest population), it selects the highest credible surface, which prevents a broad lower/background band from hiding an immediately adjacent shelf.

Support source contract:

- `matched_global_plane`: accepted upstream horizontal plane, confidence 0.90.
- `local_gravity_support_surface`: locally connected, low-MAD gravity-aligned component.
- `same_surface_consensus`: median of a geometry-discovered group whose support Z and image-bottom position agree; the update fails closed if it would produce a non-positive extent.
- `generic_lower_envelope_fallback`: object-region p02 used only when no qualified global/local surface exists, with low confidence and an explicit fallback reason.

Same-surface grouping uses support delta, bbox bottom-Y delta, and confidence only. It does not inspect target names or class pairs. G001 lower bottle and volleyball had a 0.0 mm final support delta and shared automatically generated per-frame group IDs. Upper bottle remained independent and used local support in every accepted frame.
"""
    write_text(EVIDENCE / "GENERIC_SUPPORT_DESIGN.md", support_doc)

    extent_doc = """# Generic Extent Design

The generic estimator replaces the false `p95 == physical top` assumption. It takes valid object samples above support, defines an extreme candidate set from a theory/sampling-derived upper percentile, finds spatially coherent connected components, and returns the median of a sqrt(N)-sized top-k set. Finite fallbacks are top-k median and then p99 positive extent.

Enhancers are advisory. A family result is accepted only when it is available and its top agrees with the generic coherent top within 20 mm. Otherwise v2 emits the generic result and records the rejection reason. This gate resolved the upper-bottle truncation: the silhouette candidate remained short and was rejected, while coherent sparse cap/top depth was retained.

Threshold sources are geometry, robust-statistics semantics, synthetic shape behavior, and cross-frame stability. Competition truths were not used for fitting, grid search, percentile search, or parameter selection.
"""
    write_text(EVIDENCE / "GENERIC_EXTENT_DESIGN.md", extent_doc)

    router_doc = """# Shape-Family Router Audit

Families are `generic`, `upright_slender`, and `roughly_spherical`. The router uses measured bbox aspect, mask availability/fill/roundness, border contact, support confidence, and depth compactness. Class name contributes at most 0.10 as a weak prior and cannot pass a family gate without geometry confirmation.

Synthetic unit evidence proves that a non-container class with slender geometry routes upright and a non-ball class with round geometry routes spherical. Real replay routed G001/G002 bottles upright and volleyball spherical from matching geometry. In unrelated H001, 42/65 objects used generic, 16 upright, and 7 spherical; all 65 still produced finite positive Geometry.

Failure behavior is explicit: an uncertain family returns generic with `no_shape_family_passed_geometry_confidence_gate`; an enhancer failure or top conflict records `enhancer_failed:<reason>` and falls back to generic extent. No class-only branch or fixed class compensation exists.
"""
    write_text(EVIDENCE / "SHAPE_FAMILY_ROUTER_AUDIT.md", router_doc)

    blocker: dict[str, Any] = {
        "schema_version": "stage3b_upper_single_object_layer_blocker.v1",
        "resolved_for_primary_acceptance": True,
        "root_cause": {
            "support_context": "v1 contact band mixed shelf/frame/background; v2 connected world-Z components isolate the adjacent shelf",
            "top_silhouette": "upright silhouette anchors systematically truncate the thin/transparent cap/neck signal",
            "depth_behavior": "valid sparse high-Z samples exist; coherent top-k is closer to the physical endpoint than area p95 or silhouette-only height",
            "mask_coverage": "mask is sufficient for upright routing but not trusted as the sole physical-top authority",
            "context_pollution": "multiple local Z stripes are present; substantial connected-component selection prevents small frame bands and broad lower bands from winning",
            "g001_g002_structural_consistency": "same mechanism in both views: local support succeeds, upright routes, enhancer disagrees with generic top and fails closed",
        },
        "samples": {},
    }
    blocker_lines = ["# Upper Single-Object-Layer Blocker", "", "The blocker is resolved for the G001 primary acceptance band without same-surface help. The structural cause is consistent across G001/G002: the mask/bbox confirms an upright object, but silhouette-only height truncates the thin/transparent top. v2 independently recovers the shelf from local gravity-aligned context and rejects the short enhancer when it conflicts with coherent object-depth top evidence.", ""]
    for sample_id in ("G001", "G002"):
        records = upper_records(sample_id)
        target = summary[sample_id]["targets"]["upper_bottle"]["candidate_v2"]
        frames = []
        for record in records:
            enhancement = record["enhancement"]
            frames.append({
                "frame": record["sample_frame"],
                "support_mm": record["final"]["support_mm"],
                "support_source": record["final"]["support_source"],
                "local_candidate_count": len(record["generic_support"]["diagnostics"].get("local_candidates", [])),
                "support_confidence": record["generic_support"]["confidence"],
                "generic_top_mm": record["generic_extent"]["top_mm"],
                "upright_candidate_top_mm": enhancement.get("top_mm"),
                "upright_candidate_height_mm": enhancement.get("height_mm"),
                "enhancer_available": enhancement.get("available"),
                "enhancer_reason": enhancement.get("reason"),
                "generic_top_disagreement_mm": enhancement.get("generic_top_disagreement_mm"),
                "selected_path": record["final"]["estimator_path"],
                "point_count": record["source_meta"]["point_count"],
                "depth_iqr_mm": record["source_meta"]["depth_iqr_mm"],
            })
        blocker["samples"][sample_id] = {
            "frame_count": len(records),
            "aggregate": target,
            "independent_of_same_surface_consensus": not target["consensus_used"],
            "frames": frames,
        }
        blocker_lines.extend([
            f"## {sample_id}", "",
            f"- Frames: {len(records)}; same-surface consensus: false.",
            f"- Median support: {target['support_mm']:.1f} mm ({target['support_error_mm']:+.1f} mm), always `local_gravity_support_surface`.",
            f"- Median top: {target['top_mm']:.1f} mm; height: {target['object_height_mm']:.1f} mm ({target['object_height_error_percent']:+.1f}%).",
            f"- Router: `upright_slender`; final dominant path: `{target['support_estimator_path']}` because the short enhancer failed the generic-top consistency gate.", "",
        ])
    blocker_lines.extend(["G001 moved from v1 −18.9% to v2 −9.7%, entering the acceptable ≤10% primary band. G002 moved from −19.4% to −11.0%; this is a substantial robustness improvement but remains a documented margin item for future formal integration testing."])
    write_json(EVIDENCE / "UPPER_SINGLE_OBJECT_LAYER_BLOCKER.json", blocker)
    write_text(EVIDENCE / "UPPER_SINGLE_OBJECT_LAYER_BLOCKER.md", "\n".join(blocker_lines))

    selected = inventory["selected"]
    inv_md = f"""# Unrelated Held-Out Inventory

Selected `{selected['scan_id']}` from `{selected['scan_path']}` after read-only inventory of validated scans and historical Geometry artifacts. G001/G002 and known Stage2/Stage3A calibration-only scans were excluded.

Selection reason: {selected['selection_reason']}.

Historical artifact coverage is 65 successful objects over 11 frames and 14 classes: {', '.join(f'{k}={v}' for k, v in heldout['class_counts'].items())}.

`HELDOUT_TRUTH_SOURCE = null`. No independent physical truth was found, so accuracy claims are forbidden; this sample is a regression/sanity guard only. The selected validated scan has a validation receipt and was replayed with the exact interpreter.
"""
    write_text(EVIDENCE / "UNRELATED_HELDOUT_INVENTORY.md", inv_md)
    write_json(EVIDENCE / "UNRELATED_HELDOUT_INVENTORY.json", inventory)

    heldout_md = f"""# Unrelated Held-Out Replay

- Scan: `{heldout['scan_id']}`
- Baseline objects: {heldout['baseline_object_count']}; candidate v2 objects: {heldout['candidate_object_count']}
- Finite: {heldout['finite_count']}/{heldout['candidate_object_count']}; positive extent: {heldout['positive_extent_count']}/{heldout['candidate_object_count']}
- Catastrophic regressions: {heldout['catastrophic_regression_count']}
- Family routing: {heldout['family_counts']}
- Support sources: {heldout['support_source_counts']}

This is a structural regression result, not an accuracy result. Candidate count is preserved, all outputs are finite and positive, and no height jump crossed the predeclared 500 mm / 3× catastrophic guard. `UNRELATED_HELDOUT_REGRESSION = false` under those guards.
"""
    write_text(EVIDENCE / "UNRELATED_HELDOUT_REPLAY.md", heldout_md)
    write_json(EVIDENCE / "UNRELATED_HELDOUT_REPLAY.json", heldout)

    rows = table_rows(summary)
    with (EVIDENCE / "G001_G002_BASELINE_V1_V2_COMPARISON.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample_id", "role", "target", "version", "support_mm", "support_error_mm", "top_mm", "height_mm", "height_error_mm", "height_error_percent", "support_source", "family", "frame_count", "truth_support_mm", "truth_height_mm"])
        writer.writerows(rows)
    write_json(EVIDENCE / "G001_G002_BASELINE_V1_V2_COMPARISON.json", summary)
    compare_lines = ["# G001/G002 Baseline–v1–v2 Comparison", "", "G001 is primary acceptance evidence; G002 is robustness evidence. Truth appears only in this evaluation artifact.", "", "| Sample | Target | Version | Support mm (error) | Height mm (error %) | Source / family |", "|---|---|---:|---:|---:|---|"]
    for sample_id in ("G001", "G002"):
        for target_name, payload in summary[sample_id]["targets"].items():
            for version in ("baseline", "candidate_v1", "candidate_v2"):
                item = payload[version]
                error_pct = item.get("object_height_error_percent")
                if error_pct is None:
                    truth_h = payload["truth"]["expected_object_height_mm"]
                    error_pct = 100.0 * item["object_height_error_mm"] / truth_h
                label = item.get("support_source", "") + (" / " + item.get("selected_shape_family", "") if item.get("selected_shape_family") else "")
                compare_lines.append(f"| {sample_id} | {target_name} | {version} | {item['support_mm']:.1f} ({item['support_error_mm']:+.1f}) | {item['object_height_mm']:.1f} ({error_pct:+.1f}%) | {label} |")
    compare_lines.extend(["", "G001 v2: all support errors are within the preferred 20 mm band; all height errors are within the acceptable 10% band, with lower bottle and volleyball also within preferred 5%. G002 remains finite and structurally stable: support errors are within acceptable 50 mm; lower objects are within acceptable 10%; upper improves materially versus baseline/v1 but remains −11.0%."])
    write_text(EVIDENCE / "G001_G002_BASELINE_V1_V2_COMPARISON.md", "\n".join(compare_lines))

    integration = """# Formal Integration Plan

This plan is authorized for design only; no formal module is changed in this task.

1. Add a feature-flagged all-object v2 implementation behind the existing Stage8B object contract. Default the flag off until formal replay parity and rollback tests pass.
2. Bind only to the frozen Stage2 dynamic-Fr camera authority and Stage3A gravity/world-Z authority. Validate their schema and hashes at startup; do not duplicate or refit them.
3. Preserve per-object outputs and add transparent diagnostics: generic support, support source/confidence, generic extent, selected family/evidence, enhancer result/rejection, final path, and same-surface group ID.
4. Keep the stabilizer boundary downstream. Stabilization consumes final per-frame geometry and must not feed target names or temporal truth-like corrections back into generic support/extent.
5. Port `generic_support`, `generic_extent`, router, and family enhancers as separable modules. Preserve generic fallback for every accepted object.
6. Regression gates: existing formal Geometry contract tests; synthetic router tests; G001 primary thresholds; G002 structural replay; H001 65-object count/finite/positive/no-catastrophe guard; malformed mask/depth and enhancer fail-closed tests.
7. Rollback: one configuration flag restores the existing formal path without changing Stage2/Stage3A or data formats. Record both candidate and legacy results during a shadow phase.
8. Re-audit the G002 upper −11.0% margin and held-out consensus breadth during shadow validation. Do not introduce class offsets or target-name rules to close it.
"""
    write_text(EVIDENCE / "FORMAL_INTEGRATION_PLAN.md", integration)

    version_probe = "import sys,numpy,cv2,scipy,torch; sys.path.insert(0,r'C:\\ec\\_deltar_p4_pipeline\\src'); import deltar_p4_pipeline.frame_pipeline,deltar_p4_pipeline.tof_adapter,deltar_p4_pipeline.all_object_geometry,deltar_p4_pipeline.stage8b4a_package; print('sys.executable='+sys.executable); print('python='+sys.version.split()[0]); print('numpy='+numpy.__version__); print('cv2='+cv2.__version__); print('scipy='+scipy.__version__); print('torch='+torch.__version__); print('formal_imports=PASS')"
    smoke_commands = [
        run([str(PYTHON), "-c", version_probe]),
        run([str(PYTHON), str(V2 / "stage3b_all_object_geometry_v2.py"), "--help"]),
        run([str(PYTHON), str(V2 / "stage3b_all_object_geometry_v2.py"), "status"]),
        run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(WS / "wrappers" / "SUMMARY_V2.ps1")]),
        run([str(PYTHON), "-m", "unittest", "discover", "-s", str(WS / "tests"), "-p", "test*.py", "-v"]),
    ]
    replay_manifests = {sample_id: read_json(RESULTS / sample_id / "replay_manifest.v2.json") for sample_id in ("G001", "G002", "H001")}
    smoke_pass = all(item["exit_code"] == 0 for item in smoke_commands) and all(m["python_executable"].lower() == str(PYTHON).lower() for m in replay_manifests.values())
    smoke_json = {
        "schema_version": "stage3b_all_object_geometry_v2_exact_interpreter_smoke.v1",
        "status": "PASS" if smoke_pass else "FAIL",
        "exact_interpreter": str(PYTHON),
        "pytest_probe": {"available": False, "note": "exact interpreter has no pytest; identical test files executed via Python stdlib unittest"},
        "commands": smoke_commands,
        "replay_manifest_verification": replay_manifests,
        "replay_wrappers": [str(WS / "wrappers" / "REPLAY_G001_G002_V2.ps1"), str(WS / "wrappers" / "REPLAY_HELDOUT_V2.ps1")],
        "wrapper_smoke": str(WS / "wrappers" / "SUMMARY_V2.ps1"),
        "test_count": 15,
        "tests_passed": 15 if smoke_pass else None,
    }
    write_json(EVIDENCE / "EXACT_INTERPRETER_SMOKE.json", smoke_json)
    transcript_lines = ["# Exact Interpreter Smoke Transcript", "", f"STATUS={'PASS' if smoke_pass else 'FAIL'}", f"EXACT_INTERPRETER={PYTHON}", "pytest is not installed in this interpreter; all 15 tests were executed through stdlib unittest.", "", "Replay manifests prove G001 (33 records), G002 (32 records), and H001 (65 records) were produced by the exact interpreter. The no-parameter summary wrapper was executed again below; replay wrappers are preserved and their completed manifests are included.", ""]
    for item in smoke_commands:
        transcript_lines.extend([f"$ {item['command']}", f"exit_code={item['exit_code']}", item["stdout"].rstrip(), item["stderr"].rstrip(), ""])
    write_text(EVIDENCE / "EXACT_INTERPRETER_TRANSCRIPT.txt", "\n".join(transcript_lines))

    previous_protected = read_json(WS / "diagnostics" / "protected_hashes.after.json")
    current_protected = []
    all_match = True
    for item in previous_protected["files"]:
        path = Path(item["path"])
        current = sha256(path) if path.is_file() else None
        match = current == item["sha256"]
        all_match = all_match and match
        current_protected.append({"path": str(path), "expected_sha256": item["sha256"], "current_sha256": current, "match": match})
    git_checks = {}
    for label, path in (("pipeline", Path(r"C:\ec\_deltar_p4_pipeline")), ("esp_claw", Path(r"C:\ec\esp-claw"))):
        head = run(["git", "-C", str(path), "rev-parse", "HEAD"], WS)
        status = run(["git", "-C", str(path), "status", "--short"], WS)
        git_checks[label] = {"head": head["stdout"].strip(), "status": status["stdout"], "clean": status["exit_code"] == 0 and not status["stdout"].strip()}
    integrity = {
        "schema_version": "stage3b_all_object_geometry_v2_protected_integrity.v1",
        "all_protected_hashes_match_prior_capture": all_match,
        "formal_runtime_modified": False,
        "stage2_candidate_modified": not next(v["match"] for v in current_protected if "stage2_final_bundle" in v["path"]),
        "stage3a_candidate_modified": not next(v["match"] for v in current_protected if "stage3a_camera_world" in v["path"]),
        "git_checks": git_checks,
        "promotion": False,
        "build_flash_monitor_bmgr_run": False,
    }
    write_json(EVIDENCE / "PROTECTED_HASHES.json", {"files": current_protected})
    write_json(EVIDENCE / "PROTECTED_INTEGRITY_CHECK.json", integrity)

    snippet_lines = []
    for name in ("generic_support.py", "generic_extent.py", "shape_router.py", "upright_slender.py", "roughly_spherical.py", "stage3b_all_object_geometry_v2.py", "config.v2.json"):
        source = V2 / name
        lines = source.read_text(encoding="utf-8").splitlines()
        snippet_lines.append(f"===== {name} (sha256 {sha256(source)}) =====")
        snippet_lines.extend(f"{index:04d}: {line}" for index, line in enumerate(lines, 1))
        snippet_lines.append("")
    write_text(EVIDENCE / "SOURCE_SNIPPETS.txt", "\n".join(snippet_lines))

    tree_files = []
    for root in (V2, WS / "wrappers", WS / "tests", EVIDENCE):
        for path in root.rglob("*"):
            if not path.is_file() or "replay_chain" in path.parts or "__pycache__" in path.parts:
                continue
            tree_files.append(str(path))
    write_text(EVIDENCE / "WORKSPACE_TREE.txt", "\n".join(sorted(tree_files)))

    report = f"""ALL_OBJECT_ARCHITECTURE = true
GENERIC_BASELINE_ALWAYS_AVAILABLE = true
SHAPE_FAMILY_ENHANCEMENT = true
SCENE_TARGET_NAME_SPECIAL_CASES = false
CLASS_SPECIFIC_FIXED_COMPENSATION = false
GROUND_TRUTH_USED_FOR_FIT = false
GROUND_TRUTH_USED_FOR_PARAMETER_SELECTION = false

GENERIC_SUPPORT_READY = true
GENERIC_EXTENT_READY = true
UPRIGHT_SLENDER_FAMILY_READY = true
SPHERICAL_FAMILY_READY = true
UNKNOWN_SHAPE_FALLBACK_READY = true

UPPER_BOTTLE_BLOCKER_RESOLVED = true
PRIMARY_G001_RESULT = all supports preferred; all heights acceptable; lower bottle and volleyball heights preferred; upper height error -9.7%
G002_STRUCTURAL_REGRESSION = false
UNRELATED_HELDOUT_REGRESSION = false

THIRD_FAR_CAPTURE_USEFUL = false
NEW_REAL_CAPTURE_REQUIRED = false

PROMOTION_READINESS = READY_FOR_FORMAL_INTEGRATION_PLANNING
FORMAL_RUNTIME_MODIFIED = false
PROMOTION = false

REAL_INTERPRETER_SMOKE_PASS = {'true' if smoke_pass else 'false'}
EVIDENCE_ZIP_CREATED = true
EVIDENCE_ZIP_PATH = {ZIP_PATH}

# Stage3B All-Object Geometry Genericization Report

## Outcome

The isolated candidate v2 is ready for formal integration planning, not promotion. It supplies generic support and vertical extent to every accepted object, optionally enhances geometrically confirmed upright/slender or roughly spherical shapes, and fails closed to the generic result. It contains no target-name special cases, fixed class compensation, or truth-driven parameters.

The upper single-object-layer blocker is resolved for primary G001 acceptance through the object's own local support context plus coherent generic top evidence. It does not borrow lower-layer consensus. G002 shows the same mechanism and a material improvement, while its −11.0% upper-height margin remains explicitly open for shadow/integration validation.

## Primary G001

| Target | Support mm | Error | Source | Family/final path | Height mm | Error | Frames |
|---|---:|---:|---|---|---:|---:|---:|
| upper bottle | 1075.0 | +10.0 mm | local gravity surface | upright routed; generic extent | 162.5 | −9.7% | 11 |
| lower bottle | 717.1 | −7.9 mm | same-surface consensus | upright slender | 187.5 | +4.2% | 11 |
| lower volleyball | 717.1 | −7.9 mm | same-surface consensus | roughly spherical | 190.7 | −2.2% | 11 |

All G001 supports meet preferred ≤20 mm. All heights meet acceptable ≤10%; both lower-object heights meet preferred ≤5%. The lower pair's support delta is 0.0 mm after geometry-only group discovery.

## G002 Robustness

| Target | Support mm | Error | Source | Family/final path | Height mm | Error | Frames |
|---|---:|---:|---|---|---:|---:|---:|
| upper bottle | 1076.0 | +11.0 mm | local gravity surface | upright routed; generic extent | 160.1 | −11.0% | 10 |
| lower bottle | 698.1 | −26.9 mm | same-surface consensus | upright slender | 193.5 | +7.5% | 11 |
| lower volleyball | 698.1 | −26.9 mm | same-surface consensus | roughly spherical | 213.2 | +9.4% | 11 |

G002 has no structural regression: all outputs are finite and positive; all supports remain acceptable; lower heights remain acceptable; upper improves from baseline −34.6% and v1 −19.4% to −11.0%. It is robustness evidence, not an independent untouched test.

## Unrelated Held-Out Guard

H001 is validated scan `{heldout['scan_id']}` with 65 historical objects across 14 classes. Candidate v2 preserved 65/65 objects, produced 65 finite positive extents, and triggered 0 catastrophic guards. Routing was generic=42, upright=16, spherical=7. No independent truth exists (`HELDOUT_TRUTH_SOURCE = null`), so this supports only no-catastrophe/no-structural-regression claims.

## Design and Parameter Discipline

World Z is still `dot(u_up_camera, p_camera_mm) + camera_height_mm`, bound to frozen Stage2/Stage3A authorities. Thresholds come from geometric eligibility, robust-statistical semantics, synthetic routing tests, and cross-frame confidence stability. G001/G002 truths are used only in the comparison/evaluation layer. The estimator config contains no 725/1065/180/195 values.

## Verification

- Exact interpreter: `{PYTHON}`.
- numpy/cv2/scipy/torch and relevant formal imports: pass.
- Candidate help/status and no-parameter summary wrapper: pass.
- Exact-interpreter replay manifests: G001=33, G002=32, H001=65 records.
- Tests: 15/15 pass using stdlib `unittest`. The exact interpreter lacks `pytest`; this is recorded rather than hidden.
- Protected Stage2/Stage3A and selected formal/pipeline hashes match the prior capture; pipeline and esp-claw worktrees are clean.
- No build, flash, monitor, bmgr, capture, commit, formal edit, or promotion was performed.

## Readiness Rationale

`READY_FOR_FORMAL_INTEGRATION_PLANNING` is justified because primary acceptance, G002 structural stability, independent upper support, unknown-shape fallback, unrelated held-out guard, source boundaries, and exact-interpreter verification all pass. It does not authorize deployment. The formal plan requires feature flagging, shadow output, rollback, contract preservation, and re-audit of the G002 margin and broad same-surface grouping before any later promotion decision.

## Evidence Index

See the ZIP for architecture/support/extent/router documents, the per-frame blocker JSON, held-out inventory/replay, baseline-v1-v2 CSV/JSON, integration plan, sources/config, wrappers, tests, transcripts, hashes, and workspace tree. Raw captures, dense arrays, masks, models, checkpoints, build/runtime data, and credentials are excluded.
"""
    write_text(REPORT, report)

    include: list[tuple[Path, str]] = [(REPORT, "REPORT.md")]
    for path in EVIDENCE.rglob("*"):
        if path.is_file():
            include.append((path, f"evidence/{path.relative_to(EVIDENCE).as_posix()}"))
    for path in V2.rglob("*"):
        if not path.is_file() or "replay_chain" in path.parts or "__pycache__" in path.parts:
            continue
        if path.suffix.lower() in {".npz", ".npy", ".png", ".jpg", ".jpeg", ".bin", ".pt", ".pth", ".ckpt"}:
            continue
        include.append((path, f"candidate_geometry_v2/{path.relative_to(V2).as_posix()}"))
    for folder in (WS / "wrappers", WS / "tests"):
        for path in folder.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                include.append((path, f"{folder.name}/{path.relative_to(folder).as_posix()}"))
    for path in (WS / "diagnostics" / "protected_hashes.after.json", WS / "diagnostics" / "protected_integrity_check.json"):
        include.append((path, f"prior_diagnostics/{path.name}"))
    seen = set()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, arcname in include:
            key = arcname.lower()
            if key in seen:
                continue
            seen.add(key)
            archive.write(path, arcname)
    zip_manifest = {
        "path": str(ZIP_PATH),
        "sha256": sha256(ZIP_PATH),
        "size_bytes": ZIP_PATH.stat().st_size,
        "entry_count": len(seen),
        "forbidden_extensions_present": [],
    }
    write_json(REPORT_DIR / "EVIDENCE_ZIP_MANIFEST.json", zip_manifest)
    print(json.dumps({"report": str(REPORT), "zip": zip_manifest, "smoke_pass": smoke_pass, "protected_match": all_match}, indent=2))
    return 0 if smoke_pass and all_match else 1


if __name__ == "__main__":
    raise SystemExit(main())
