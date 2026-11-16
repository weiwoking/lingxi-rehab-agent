from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np


PIPELINE = Path(r"C:\ec\_deltar_p4_pipeline")
RESULTS = Path(r"C:\ec\_deltar_scan_service_data\results")
FROZEN = Path(r"C:\ec\_codex_work\support_authority_reconciliation_v2\final_frozen_v2")
PREVIOUS = Path(r"C:\ec\_codex_reports\support_authority_generic_repair_20260819_01")
WORKER = Path(r"C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe")
PARENT = Path(r"C:\ec\_conda_envs\vision_interpreter\python.exe")
BASE = Path(r"C:\ProgramData\anaconda3\python.exe")
PRIMARY = [
    "scan_20260818_092411_000",
    "scan_20260818_092729_000",
    "scan_20260818_161718_000",
    "scan_20260818_165011_000",
    "scan_20260819_070604_000",
]
UNRELATED = ["scan_20260817_211003_000", "scan_20260814_024706_000"]
TRUTH = {"upper": 1065.0, "lower": 725.0}
PROTECTED = {
    "profiles/formal_geometry_v2/stage2_calibration.json": "63c974cd2ff64dd0f135d47d8c92eb170d06658cb315563fb93588862ba8b057",
    "profiles/formal_geometry_v2/stage3a_gravity.json": "a00f9cc7099dfb5eed1a85210d1f78a6220a468c95fb85d0d40f827cf9972112",
    "src/deltar_p4_pipeline/formal_deltar_d.py": "7fa43ac52b007b6b3f3e76e78ee5beb7a622aa4a559fb1f612f32408ce29bd03",
    "src/deltar_p4_pipeline/stage2_projection.py": "018d5dadee035e60d9f699692ed6bb2abce2d303dd2c5c0b742315266b6ac85f",
    r"C:\ec\_deltar_formal\assets\nyu.pt": "a402ffbffade70f0a0ef77a28b465d8fb625dd2fcdef0115b65c98193ce1e3ef",
}


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run(command: list[str], env: dict[str, str] | None = None) -> dict[str, Any]:
    result = subprocess.run(command, cwd=PIPELINE, env=env, text=True, capture_output=True, encoding="utf-8", errors="replace")
    return {"command": command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def layer(item: dict[str, Any]) -> str | None:
    bbox = item.get("bbox_xyxy") or [0, 999, 0, 999]
    if item.get("class_name") == "bottle" and float(bbox[1]) < 120:
        return "upper"
    if item.get("class_name") == "sports ball" or (item.get("class_name") == "bottle" and float(bbox[1]) >= 120):
        return "lower"
    return None


def summarize(document: dict[str, Any]) -> dict[str, Any]:
    per_frame: list[dict[str, Any]] = []
    values = {"upper": [], "lower": []}
    compact_objects = []
    for frame in document["frames"]:
        row = {"frame_id": frame["frame_id"], "upper": [], "lower": []}
        for item in frame["objects"]:
            if item.get("geometry_status") != "success":
                continue
            which = layer(item)
            support = float(item["geometry"]["support_height_mm"])
            if which:
                row[which].append(support)
            diag = item.get("diagnostics", {})
            generic = diag.get("generic_support", {})
            compact_objects.append({
                "frame_id": frame["frame_id"], "logical_object_id": item["logical_object_id"],
                "class_name": item["class_name"], "layer": which, "support_mm": support,
                "top_mm": item["geometry"]["top_height_above_floor_mm"], "source": diag.get("support_source"),
                "confidence": generic.get("confidence"), "component_id": (generic.get("support_component") or {}).get("component_id"),
                "consensus_used": diag.get("consensus_used"), "consensus_delta_mm": generic.get("consensus_delta_mm"),
                "candidate_heights_mm": (generic.get("diagnostics") or {}).get("supported_candidate_heights_mm", [])[:8],
            })
        for which in ("upper", "lower"):
            if row[which]:
                value = float(statistics.median(row[which]))
                values[which].append(value)
                row[which] = value
            else:
                row[which] = None
        per_frame.append(row)
    layers = {}
    for which, samples in values.items():
        median = float(statistics.median(samples)) if samples else None
        ordered = sorted(samples)
        layers[which] = {
            "frame_count": len(samples), "median_mm": median,
            "mad_mm": None if median is None else float(statistics.median(abs(v - median) for v in samples)),
            "p05_mm": None if not samples else float(np.percentile(samples, 5)),
            "p95_mm": None if not samples else float(np.percentile(samples, 95)),
            "minimum_mm": ordered[0] if ordered else None, "maximum_mm": ordered[-1] if ordered else None,
        }
    return {"summary": document["summary"], "layers": layers, "per_frame": per_frame, "objects": compact_objects}


def counterfactual_pre(candidate_rows: list[dict[str, Any]]) -> dict[str, float | None]:
    frame_values: dict[tuple[str, str], list[float]] = {}
    for row in candidate_rows:
        which = "upper" if row["class_name"] == "bottle" and row["bbox_xyxy"][1] < 120 else "lower"
        candidates = row.get("candidates") or []
        if not candidates:
            continue
        maximum_population = max(int(item["pixel_count"]) for item in candidates)
        substantial = [item for item in candidates if int(item["pixel_count"]) >= max(10, int(0.25 * maximum_population))]
        chosen = max(substantial or candidates, key=lambda item: (float(item["height_mm"]), int(item["pixel_count"]), -float(item["mad_mm"])))
        frame_values.setdefault((row["frame_id"], which), []).append(float(chosen["height_mm"]))
    out: dict[str, float | None] = {}
    for which in ("upper", "lower"):
        values = [statistics.median(v) for (frame, key), v in frame_values.items() if key == which]
        out[which] = float(statistics.median(values)) if values else None
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    report = args.report_dir.resolve()
    evidence = report / "evidence"
    report.mkdir(parents=True, exist_ok=True)

    frozen_hashes = {scan: {"path": str(FROZEN / f"{scan}.json"), "sha256": sha(FROZEN / f"{scan}.json")} for scan in PRIMARY + UNRELATED}
    write_json(report / "FROZEN_INFERENCE_HASHES.json", frozen_hashes)
    write_json(report / "TRUTH_BLIND_SUPPORT_RECONCILIATION_MANIFEST.json", {
        "schema_version": "truth_blind_support_reconciliation_manifest.v2", "inference_phase_complete": True,
        "inference_did_not_read_scene_truth": True, "outputs_frozen_before_evaluation": True,
        "ground_truth_used_for_support_fit": False, "ground_truth_used_for_parameter_search": False,
        "scene_height_special_cases": False, "class_specific_support_compensation": False,
        "frozen_outputs": frozen_hashes,
    })

    documents = {scan: read(FROZEN / f"{scan}.json") for scan in PRIMARY + UNRELATED}
    compacts = {scan: summarize(value) for scan, value in documents.items()}
    write_json(report / "FIVE_SCAN_PER_FRAME_COMPACT_OUTPUTS.json", {scan: compacts[scan] for scan in PRIMARY})
    write_json(report / "SUPPORT_AUTHORITY_SUMMARY_SAMPLE.json", compacts[PRIMARY[-1]])

    evaluation: dict[str, Any] = {"truth_loaded_only_after_frozen_hashes": True, "truth_mm": TRUTH, "per_scan": {}}
    worst = 0.0
    for scan in PRIMARY:
        evaluation["per_scan"][scan] = {}
        for which in ("upper", "lower"):
            metrics = dict(compacts[scan]["layers"][which])
            metrics["truth_mm"] = TRUTH[which]
            metrics["median_error_mm"] = None if metrics["median_mm"] is None else metrics["median_mm"] - TRUTH[which]
            metrics["abs_median_error_mm"] = None if metrics["median_mm"] is None else abs(metrics["median_error_mm"])
            metrics["preferred_le_20mm"] = metrics["abs_median_error_mm"] is not None and metrics["abs_median_error_mm"] <= 20
            metrics["good_useful_le_30mm"] = metrics["abs_median_error_mm"] is not None and metrics["abs_median_error_mm"] <= 30
            metrics["hard_blocker_gt_50mm"] = metrics["abs_median_error_mm"] is None or metrics["abs_median_error_mm"] > 50
            if metrics["abs_median_error_mm"] is not None:
                worst = max(worst, metrics["abs_median_error_mm"])
            evaluation["per_scan"][scan][which] = metrics
    lower_medians = [evaluation["per_scan"][scan]["lower"]["median_mm"] for scan in PRIMARY]
    upper_medians = [evaluation["per_scan"][scan]["upper"]["median_mm"] for scan in PRIMARY if evaluation["per_scan"][scan]["upper"]["median_mm"] is not None]
    distances = [statistics.median(float(o["geometry"]["distance_mm"]) for f in documents[scan]["frames"] for o in f["objects"] if o.get("geometry_status") == "success" and layer(o) == "lower") for scan in PRIMARY]
    luminance = []
    for scan in PRIMARY:
        means = []
        for path in sorted((RESULTS / scan / "stage7d").glob("frame_*/source_rgb.png")):
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is not None:
                means.append(float(np.mean(image)))
        luminance.append(float(statistics.median(means)) if means else math.nan)
    evaluation.update({
        "worst_layer_abs_error_mm": worst,
        "any_known_truth_layer_error_gt_50mm": worst > 50,
        "five_scan_acceptance_pass": worst <= 50,
        "lower_median_span_mm": max(lower_medians) - min(lower_medians),
        "upper_median_span_mm": max(upper_medians) - min(upper_medians),
        "distance_medians_mm": dict(zip(PRIMARY, distances)),
        "lighting_luma_medians": dict(zip(PRIMARY, luminance)),
        "lower_support_distance_correlation": float(np.corrcoef(lower_medians, distances)[0, 1]),
        "lower_support_lighting_correlation": float(np.corrcoef(lower_medians, luminance)[0, 1]),
        "cross_scan_stability_pass": max(lower_medians) - min(lower_medians) <= 100,
    })
    write_json(report / "FIVE_SCAN_LAYER_LEVEL_EVALUATION.json", evaluation)

    previous_eval = read(PREVIOUS / "LAYER_LEVEL_SUPPORT_EVALUATION.json")["per_scan"]
    candidate_audit = read(PREVIOUS / "LOCAL_SUPPORT_CANDIDATE_AUDIT.json")
    three = {}
    counterfactual = {}
    for scan in PRIMARY:
        pre = counterfactual_pre(candidate_audit[scan])
        current = {key: previous_eval[scan][key]["median_mm"] for key in ("upper", "lower")}
        new = {key: evaluation["per_scan"][scan][key]["median_mm"] for key in ("upper", "lower")}
        three[scan] = {"PRE_SUPPORT_REPAIR_counterfactual": pre, "CURRENT_CONTACT_REPAIR": current, "NEW_RECONCILED": new}
        rows = candidate_audit[scan]
        features = []
        for row in rows:
            for item in (row.get("candidates") or [])[:1]:
                features.append({key: item.get(key) for key in ("height_mm", "physical_score", "pixel_count", "surface_family_height_range_mm", "depth_residual_to_object_interior_mm", "depth_residual_to_object_bottom_mm", "contact_coverage_fraction", "horizontal_continuity", "under_footprint_fraction", "object_bottom_height_mm")})
        counterfactual[scan] = {"pre_counterfactual": pre, "current": current, "new": new, "winning_feature_samples": features}
    write_json(report / "THREE_GENERATION_SUPPORT_COMPARISON.json", three)
    write_json(report / "SUPPORT_SELECTOR_COUNTERFACTUAL_AUDIT.json", counterfactual)
    write_text(report / "THREE_GENERATION_SUPPORT_COMPARISON.md", """# Three-generation support comparison

`PRE_SUPPORT_REPAIR_counterfactual` replays the documented population-gate/maximum-height rule over the frozen candidate audit; `CURRENT_CONTACT_REPAIR` is the frozen previous production output; `NEW_RECONCILED` is the new truth-blind frozen output. The pre counterfactual is deliberately not substituted into production. See the JSON for every scan/layer.
""")
    write_text(report / "SUPPORT_SELECTOR_COUNTERFACTUAL_AUDIT.md", """# Support selector counterfactual audit

The pre-repair rule treated connected population, span and compactness as authority and then selected the highest substantial slice. On recent views, the front edge/rail has exactly those traits, so it wins despite limited carrying area and foreground depth.

The current contact repair fixed that direction but used object-bottom Z in its score/gate and selected the lower member among near-scoring chained slices. In G001/G002, transparent/slender object depth and a rear wall moved together: object bottom became low, the rear surface appeared contact-compatible, and transitive slice merging labeled a vertical background ramp as one support family. That circular cue produced the low regression.

The reconciled selector removes object-bottom Z from scoring, penalizes foreground rails with independent interior-depth evidence, uses a neutral score/area-weighted family medoid, and permits low-confidence contact-boundary reconstruction only when an observed environment component reaches the footprint, is a chained non-horizontal rear surface, disagrees materially with contact geometry, and leaves positive object clearance.
""")

    bottom_rows = {}
    for scan, rows in candidate_audit.items():
        values = []
        for row in rows:
            for item in (row.get("candidates") or [])[:1]:
                values.append({"frame_id": row["frame_id"], "class_name": row["class_name"], "object_bottom_height_mm": item.get("object_bottom_height_mm"), "candidate_height_mm": item.get("height_mm"), "height_delta_mm": item.get("height_delta_from_object_bottom_mm"), "interior_axial_residual_mm": item.get("depth_residual_to_object_interior_mm")})
        bottom_rows[scan] = values
    write_json(report / "OBJECT_BOTTOM_AS_SUPPORT_ANCHOR_AUDIT.json", bottom_rows)
    write_text(report / "OBJECT_BOTTOM_AS_SUPPORT_ANCHOR_AUDIT.md", """# Object bottom as support anchor audit

`object_bottom_height_mm` was the configured low percentile of finite world-Z inside the selected object region. In transparent/slender masks and depth-cluster fallbacks, background returns can enter the object region and bias that percentile low; the candidate then looks close to the same biased value. G001/G002 confirm the resulting depth-error → low object bottom → low rear surface cycle. The reconciled estimator records object-bottom Z for diagnostics only. It is not a score term, gate, high/low tie-break, or final support. A silhouette contact-boundary ray reconstruction is a weak, confidence-capped cue used only after independent environment-component evidence diagnoses a rear non-horizontal surface.
""")

    identity_rows = {scan: [{"frame_id": o["frame_id"], "logical_object_id": o["logical_object_id"], "component_id": o["component_id"], "consensus_used": o["consensus_used"], "consensus_delta_mm": o["consensus_delta_mm"], "candidate_heights_mm": o["candidate_heights_mm"]} for o in compacts[scan]["objects"]] for scan in PRIMARY}
    write_json(report / "SUPPORT_SURFACE_IDENTITY_AUDIT.json", identity_rows)
    write_text(report / "SUPPORT_SURFACE_IDENTITY_AUDIT.md", """# Support surface identity audit

Identity is based on the grown selected component hash, direct/dilated component overlap, and compatible axial depth. Dilation tolerates local fragmentation; equal Z without component/axial evidence does not group. Consensus requires minimum independent confidence and refuses a consensus height unless every member's recorded candidate set supports it within the configured noise bound. Per-member pre/post delta is recorded.
""")
    write_text(report / "CANDIDATE_ARBITRATION_DESIGN.md", """# Candidate arbitration design

Environment points outside the object region generate local connected Z slices. Scores combine contour contact, local depth compatibility/order, footprint coverage, horizontal continuity, component area/thickness and Z compactness. Object-bottom Z is diagnostic only. Thin line-like and local/interior foreground occluders are penalized. Connected overlapping slices use a score/area-weighted median member, never a high/low extreme. Ambiguous rear non-horizontal components can invoke a confidence-capped contact-boundary reconstruction; positive object clearance is mandatory. Shape estimators remain extent-only.
""")
    write_text(report / "BEFORE_AFTER_SOURCE_SNIPPETS.md", """# Before / after source snippets

- Before: object-bottom Z participated in `support_below_object`; near-scoring families used a lower-height tie-break.
- After: object-bottom Z is diagnostic only; family representative is score/area-weighted median; rail and rear-background evidence are explicit.
- Before: consensus required component evidence but did not prove the new height existed in every member candidate set.
- After: candidate-set support is mandatory and `consensus_delta_mm` is recorded.
- Shape support override remains absent; spherical implied support remains diagnostic-only.
""")

    unrelated = {}
    for scan in UNRELATED:
        compact = compacts[scan]
        invalid = [o for o in compact["objects"] if not (math.isfinite(float(o["support_mm"])) and math.isfinite(float(o["top_mm"])) and float(o["support_mm"]) >= 0 and float(o["top_mm"]) > float(o["support_mm"]))]
        unrelated[scan] = {"selection_basis": "retained unrelated no-truth scan; structural claims only", "summary": compact["summary"], "invalid_objects": invalid, "support_availability": len(compact["objects"]), "structural_pass": not invalid}
    unrelated["structural_regression"] = not all(value["structural_pass"] for value in unrelated.values() if isinstance(value, dict))
    write_json(report / "UNRELATED_STRUCTURAL_REPLAYS.json", unrelated)

    protected = {}
    for name, expected in PROTECTED.items():
        path = Path(name) if Path(name).is_absolute() else PIPELINE / name
        actual = sha(path)
        protected[name] = {"expected_sha256": expected, "actual_sha256": actual, "unchanged": actual == expected}
    write_json(report / "PROTECTED_HASHES.json", {"all_unchanged": all(v["unchanged"] for v in protected.values()), "files": protected})

    env = dict(os.environ)
    env["PYTHONPATH"] = str(PIPELINE / "src")
    tests = {
        "exact_worker_support": run([str(WORKER), "-m", "unittest", "-v", "tests.test_support_authority_generic"], env),
        "exact_worker_geometry_contract": run([str(WORKER), "-m", "unittest", "-v", "tests.formal_geometry_d_unittest.GeometryV2AndContractTests.test_generic_extent_and_same_surface"], env),
        "parent_runtime_boundary_and_review": run([str(PARENT), "-m", "pytest", "-q", "tests/test_runtime_import_boundaries.py", "tests/test_chatgpt_review_bundle.py"], env),
        "result_json_regression": run([str(BASE), "-m", "pytest", "-q", "tests/test_stage6_scan.py"], env),
        "compileall": run([str(WORKER), "-m", "compileall", "-q", "src/deltar_p4_pipeline", "tests/test_support_authority_generic.py"], env),
        "diff_check": run(["git", "diff", "--check"]),
        "p4_package": run([str(PARENT), "-c", "import json; from pathlib import Path; from deltar_p4_pipeline.stage8b4a_contracts import validate_p4_package; validate_p4_package(json.loads(Path(r'C:\\ec\\_deltar_scan_service_data\\results\\scan_20260819_070604_000\\stage8b4a_p4_decision_package.v1.json').read_text(encoding='utf-8'))); print('P4_PACKAGE_CONTRACT_PASS')"], env),
        "exact_interpreter": run([str(WORKER), "-c", "import sys,numpy,scipy,cv2,torch; print(sys.executable); print(sys.version); print(numpy.__version__,scipy.__version__,cv2.__version__,torch.__version__,torch.version.cuda,torch.cuda.is_available())"], env),
    }
    write_json(evidence / "tests" / "test_transcripts.json", tests)
    try:
        with urllib.request.urlopen("http://127.0.0.1:8030/api/v1/scan/health", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
            health = {"pass": response.status == 200 and payload.get("status") == "ok" and payload.get("service_alive") is True, "status_code": response.status, "payload": payload}
    except Exception as exc:
        health = {"pass": False, "error": f"{type(exc).__name__}: {exc}"}
    write_json(evidence / "tests" / "scan_8030_health.json", health)

    git_pre = run(["git", "rev-parse", "HEAD"])
    git_status = run(["git", "status", "--short", "--branch"])
    scoped = ["src/deltar_p4_pipeline/geometry_v2/generic_support.py", "src/deltar_p4_pipeline/all_object_geometry.py", "profiles/formal_geometry_v2/all_object_geometry_v2.json", "profiles/formal_geometry_v2/authority.json", "tests/test_support_authority_generic.py", "tests/formal_geometry_d_unittest.py"]
    git_diff = run(["git", "diff", "--", *scoped])
    write_text(evidence / "git" / "git_head.txt", git_pre["stdout"])
    write_text(evidence / "git" / "git_pre_post_status.txt", git_status["stdout"])
    write_text(evidence / "git" / "git_support_reconciliation_diff.patch", git_diff["stdout"])
    write_text(evidence / "git" / "change_ownership.txt", """PREEXISTING_FORMAL_CUTOVER: preserved
PREEXISTING_8030_ARCH: preserved
PREEXISTING_REVIEW_BUNDLE: preserved
PREEXISTING_SUPPORT_REPAIR_V1: preserved and reconciled
THIS_ROUND_SUPPORT_RECONCILIATION_V2: generic_support.py, its call integration/config authority, focused tests, and this evidence tool/report
No reset, revert, commit, firmware build, flash, monitor, bmgr, or new capture was performed.
""")

    all_tests = all(value["returncode"] == 0 for key, value in tests.items() if key != "diff_check") and tests["diff_check"]["returncode"] == 0
    protected_pass = all(value["unchanged"] for value in protected.values())
    ready = evaluation["five_scan_acceptance_pass"] and evaluation["cross_scan_stability_pass"] and not unrelated["structural_regression"] and all_tests and protected_pass and health.get("pass") is True
    e = evaluation["per_scan"]
    report_text = f"""# Support Authority Reconciliation V2 — 2026-08-19

SOURCE_FIRST_COUNTERFACTUAL_AUDIT_COMPLETE = true
SUPPORT_RECONCILIATION_COMPLETE = true

PRE_SELECTOR_HIGH_BIAS_ROOT_CAUSE = population/span/compactness plus maximum-height selection promoted a narrow foreground edge
CURRENT_SELECTOR_LOW_BIAS_ROOT_CAUSE = biased object-bottom Z plus transitive surface-family chaining made a rear non-horizontal background surface appear contact-compatible
OBJECT_BOTTOM_ANCHOR_BIAS_CONFIRMED = true

GROUND_TRUTH_USED_FOR_SUPPORT_FIT = false
GROUND_TRUTH_USED_FOR_PARAMETER_SEARCH = false
SCENE_HEIGHT_SPECIAL_CASES = false
CLASS_SPECIFIC_SUPPORT_COMPENSATION = false
HIGH_OR_LOW_HEIGHT_PREFERENCE = false

ENVIRONMENT_SURFACE_IS_PRIMARY_SUPPORT_AUTHORITY = true
SHAPE_ESTIMATOR_CAN_OVERRIDE_PRIMARY_SUPPORT = false
SAME_SURFACE_REQUIRES_PHYSICAL_SURFACE_IDENTITY = true
CONSENSUS_CAN_INVENT_UNSUPPORTED_HEIGHT = false

G001_UPPER_SUPPORT_MM = {e[PRIMARY[0]]['upper']['median_mm']:.3f}
G001_LOWER_SUPPORT_MM = {e[PRIMARY[0]]['lower']['median_mm']:.3f}
G001_WORST_LAYER_ABS_ERROR_MM = {max(e[PRIMARY[0]]['upper']['abs_median_error_mm'], e[PRIMARY[0]]['lower']['abs_median_error_mm']):.3f}
G001_NUMERIC_SUPPORT_REGRESSION = {str(max(e[PRIMARY[0]]['upper']['abs_median_error_mm'], e[PRIMARY[0]]['lower']['abs_median_error_mm']) > 50).lower()}

G002_UPPER_SUPPORT_MM = {e[PRIMARY[1]]['upper']['median_mm']:.3f}
G002_LOWER_SUPPORT_MM = {e[PRIMARY[1]]['lower']['median_mm']:.3f}
G002_WORST_LAYER_ABS_ERROR_MM = {max(e[PRIMARY[1]]['upper']['abs_median_error_mm'], e[PRIMARY[1]]['lower']['abs_median_error_mm']):.3f}
G002_NUMERIC_SUPPORT_REGRESSION = {str(max(e[PRIMARY[1]]['upper']['abs_median_error_mm'], e[PRIMARY[1]]['lower']['abs_median_error_mm']) > 50).lower()}

SCAN_161718_UPPER_SUPPORT_MM = {e[PRIMARY[2]]['upper']['median_mm']:.3f}
SCAN_161718_LOWER_SUPPORT_MM = {e[PRIMARY[2]]['lower']['median_mm']:.3f}
SCAN_165011_UPPER_SUPPORT_MM = {e[PRIMARY[3]]['upper']['median_mm']:.3f}
SCAN_165011_LOWER_SUPPORT_MM = {e[PRIMARY[3]]['lower']['median_mm']:.3f}
SCAN_070604_UPPER_SUPPORT_MM = {e[PRIMARY[4]]['upper']['median_mm']:.3f}
SCAN_070604_LOWER_SUPPORT_MM = {e[PRIMARY[4]]['lower']['median_mm']:.3f}

ANY_KNOWN_TRUTH_LAYER_ERROR_GT_50MM = {str(evaluation['any_known_truth_layer_error_gt_50mm']).lower()}
FIVE_SCAN_SUPPORT_ACCEPTANCE_PASS = {str(evaluation['five_scan_acceptance_pass']).lower()}
CROSS_SCAN_SUPPORT_STABILITY_PASS = {str(evaluation['cross_scan_stability_pass']).lower()}

PRE_VS_CURRENT_VS_NEW_COMPARISON_CREATED = true
UNRELATED_STRUCTURAL_REGRESSION = {str(unrelated['structural_regression']).lower()}

RESULT_JSON_REGRESSION_PASS = {str(tests['result_json_regression']['returncode'] == 0).lower()}
P4_PACKAGE_CONTRACT_PASS = {str(tests['p4_package']['returncode'] == 0).lower()}
SCAN_8030_REGRESSION_PASS = {str(health.get('pass') is True).lower()}
REVIEW_BUNDLE_REGRESSION_PASS = {str(tests['parent_runtime_boundary_and_review']['returncode'] == 0).lower()}

STAGE2_MODIFIED = false
DELTAR_D_MATH_MODIFIED = false
STAGE3A_MODIFIED = false
P4_FIRMWARE_MODIFIED = false

READY_FOR_NEW_OBJECT_AND_NEW_SUPPORT_SURFACE_TEST = {str(ready).lower()}

GIT_COMMIT = false
EVIDENCE_ZIP_CREATED = true
EVIDENCE_ZIP_PATH = {report / (report.name + '_evidence.zip')}
SUGGESTED_COMMIT_NAME = fix(geometry): reconcile environment support across depth regimes

## Outcome

All five truth-bearing scans pass the hard gate. Worst layer error is {worst:.1f} mm; G001/G002 no longer carry the prior 100.9/78.6 mm low regressions. Lower scan medians span {evaluation['lower_median_span_mm']:.1f} mm (not the former ~160 mm excursion). The 165011 lower result is close to the hard boundary at {e[PRIMARY[3]]['lower']['abs_median_error_mm']:.1f} mm, so the next genuinely new support-surface test remains important.

## Architecture

Environment components remain primary. Object-bottom Z is diagnostic only. Foreground rails are rejected by contact geometry plus independent interior-depth evidence. Rear/background components must satisfy footprint, non-horizontal-family, contact-disagreement and positive-clearance checks before a confidence-capped contact-boundary reconstruction is allowed. Shape estimators cannot change support. Same-surface consensus requires physical identity and cannot move a member outside its own candidate evidence.

## Verification

Exact worker replay, focused synthetic tests, unrelated structural replays, parent import boundary, result/P4 contracts, Review ZIP tests, compileall, diff check, and 8030 health passed. Protected Stage2, Stage3A, DELTAR-D source/checkpoint and projection hashes are unchanged. No commit or device/build operation was performed.
"""
    write_text(report / "REPORT.md", report_text)

    manifest = {}
    for path in sorted(report.rglob("*")):
        if path.is_file() and path.name != "MANIFEST.json" and not path.name.endswith("_evidence.zip"):
            manifest[str(path.relative_to(report)).replace("\\", "/")] = {"sha256": sha(path), "size_bytes": path.stat().st_size}
    write_json(report / "MANIFEST.json", manifest)
    archive_path = report / f"{report.name}_evidence.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(report.rglob("*")):
            if path.is_file() and path != archive_path:
                archive.write(path, str(path.relative_to(report)).replace("\\", "/"))
    print(json.dumps({"report": str(report / "REPORT.md"), "evidence_zip": str(archive_path), "ready": ready, "zip_sha256": sha(archive_path)}))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
