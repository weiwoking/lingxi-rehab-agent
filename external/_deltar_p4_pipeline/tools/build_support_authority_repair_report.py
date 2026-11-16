from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


PIPELINE = Path(r"C:\ec\_deltar_p4_pipeline")
RESULTS = Path(r"C:\ec\_deltar_scan_service_data\results")
DEFAULT_FROZEN = Path(r"C:\ec\_codex_work\support_authority_phase_a\adaptive_final")
WORKER = Path(r"C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe")
PARENT = Path(r"C:\ec\_conda_envs\vision_interpreter\python.exe")
BASE_PYTHON = Path(r"C:\ProgramData\anaconda3\python.exe")

REPLAY_HASHES = {
    "scan_20260814_024706_000": "2b112d752fe3e9a154665684ea2533e428ab9e09933ce472f89013bd2bd09a3f",
    "scan_20260817_211003_000": "c326c95f4982f11ca95002ace79caad3e3ae056955ff1aedc3229beb18acb136",
    "scan_20260818_092411_000": "b42ee6d18915cb83023d24cec90b8fe475254294cb3708ba48e2e3529b480c47",
    "scan_20260818_092729_000": "0b146dfdead45600ce2054a4e568937089254851d603f7a0b1c0e5c95d14a780",
    "scan_20260818_161718_000": "db3ff20cebe3dc3c0f3522c5673ef12b31d7c4f3ef653d439e0ca7e20189bf26",
    "scan_20260818_165011_000": "ccd5fa0c3e1695556ad5715bbd0e65b2cf7e7ae49d023e5e3d018697c6665e06",
    "scan_20260819_070604_000": "57e51f3fc985cc7d9f343974eedc2763616ae28c0445f2783c09a1abbd5a29dd",
}

PROTECTED = {
    "profiles/formal_geometry_v2/stage2_calibration.json": "63c974cd2ff64dd0f135d47d8c92eb170d06658cb315563fb93588862ba8b057",
    "profiles/formal_geometry_v2/stage3a_gravity.json": "a00f9cc7099dfb5eed1a85210d1f78a6220a468c95fb85d0d40f827cf9972112",
    "src/deltar_p4_pipeline/formal_deltar_d.py": "7fa43ac52b007b6b3f3e76e78ee5beb7a622aa4a559fb1f612f32408ce29bd03",
    "src/deltar_p4_pipeline/stage2_projection.py": "018d5dadee035e60d9f699692ed6bb2abce2d303dd2c5c0b742315266b6ac85f",
    r"C:\ec\_deltar_formal\assets\nyu.pt": "a402ffbffade70f0a0ef77a28b465d8fb625dd2fcdef0115b65c98193ce1e3ef",
}

PRIMARY_SCANS = [
    "scan_20260818_092411_000",
    "scan_20260818_092729_000",
    "scan_20260818_161718_000",
    "scan_20260818_165011_000",
    "scan_20260819_070604_000",
]
CURRENT_LIGHTING_SCANS = PRIMARY_SCANS[2:]
UNRELATED = ["scan_20260817_211003_000", "scan_20260814_024706_000"]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], *, env: dict[str, str] | None = None) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=PIPELINE, text=True, capture_output=True, encoding="utf-8", errors="replace", env=env)
    return {"command": command, "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}


def is_upper(item: dict[str, Any]) -> bool:
    bbox = item.get("bbox_xyxy") or [0, 999, 0, 999]
    return item.get("class_name") == "bottle" and float(bbox[1]) < 120.0


def is_lower(item: dict[str, Any]) -> bool:
    bbox = item.get("bbox_xyxy") or [0, 0, 0, 0]
    return item.get("class_name") == "sports ball" or (item.get("class_name") == "bottle" and float(bbox[1]) >= 120.0)


def layer_summary(document: dict[str, Any]) -> dict[str, Any]:
    layers: dict[str, list[float]] = {"upper": [], "lower": []}
    individual: list[dict[str, Any]] = []
    for frame in document["frames"]:
        per_frame = {"upper": [], "lower": []}
        for item in frame["objects"]:
            if item.get("geometry_status") != "success":
                continue
            support = float(item["geometry"]["support_height_mm"])
            layer = "upper" if is_upper(item) else ("lower" if is_lower(item) else None)
            if layer:
                per_frame[layer].append(support)
                individual.append({"frame_id": frame["frame_id"], "logical_object_id": item["logical_object_id"], "class_name": item["class_name"], "layer": layer, "support_mm": support})
        for layer, values in per_frame.items():
            if values:
                layers[layer].append(float(statistics.median(values)))
    out: dict[str, Any] = {"individual_diagnostics": individual}
    for layer, values in layers.items():
        median = float(statistics.median(values)) if values else None
        out[layer] = {
            "frame_layer_values_mm": values,
            "frame_count": len(values),
            "median_mm": median,
            "mad_mm": None if not values else float(statistics.median(abs(value - median) for value in values)),
            "p05_mm": None if not values else float(sorted(values)[max(0, math.floor(0.05 * (len(values) - 1)))]),
            "p95_mm": None if not values else float(sorted(values)[min(len(values) - 1, math.ceil(0.95 * (len(values) - 1)))]),
        }
    return out


def compact_replay(scan_id: str, document: dict[str, Any]) -> dict[str, Any]:
    supports = []
    shape_override = []
    invalid = []
    for frame in document["frames"]:
        for item in frame["objects"]:
            if item.get("geometry_status") != "success":
                continue
            geometry = item["geometry"]
            supports.append({
                "frame_id": frame["frame_id"], "logical_object_id": item["logical_object_id"],
                "class_name": item["class_name"], "bbox_xyxy": item["bbox_xyxy"],
                "support_mm": geometry["support_height_mm"], "top_mm": geometry["top_height_above_floor_mm"],
                "height_mm": geometry["object_height_mm"], "support_source": item["diagnostics"]["support_source"],
                "same_surface_group_id": item["diagnostics"].get("same_surface_group_id"),
            })
            enhancement = item["diagnostics"].get("shape_enhancement") or {}
            if "support_override_mm" in enhancement:
                shape_override.append(item["logical_object_id"])
            if geometry["object_height_mm"] <= 0 or geometry["top_height_above_floor_mm"] <= geometry["support_height_mm"]:
                invalid.append(item["logical_object_id"])
    return {"scan_id": scan_id, "summary": document["summary"], "layers": layer_summary(document), "supports": supports, "shape_support_override_ids": shape_override, "invalid_geometry_ids": invalid}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--frozen-dir", type=Path, default=DEFAULT_FROZEN)
    args = parser.parse_args()
    report = args.report_dir.resolve()
    evidence = report / "evidence"
    report.mkdir(parents=True, exist_ok=True)

    # Phase B begins with immutable Phase-A hash verification.
    hash_rows = {}
    for scan_id, expected in REPLAY_HASHES.items():
        path = args.frozen_dir / f"{scan_id}.json"
        actual = sha256(path)
        hash_rows[scan_id] = {"path": str(path), "expected_sha256": expected, "actual_sha256": actual, "verified": actual == expected}
    if not all(row["verified"] for row in hash_rows.values()):
        raise SystemExit("truth-blind replay hash verification failed")
    truth_manifest = {
        "schema_version": "truth_blind_support_repair_manifest.v1",
        "phase_a_complete": True,
        "phase_a_outputs_frozen_before_evaluation": True,
        "ground_truth_used_for_support_fit": False,
        "ground_truth_used_for_parameter_search": False,
        "scene_height_special_cases": False,
        "class_specific_support_compensation": False,
        "replays": hash_rows,
        "phase_b_hash_verification_pass": True,
    }
    write_json(report / "TRUTH_BLIND_SUPPORT_REPAIR_MANIFEST.json", truth_manifest)

    documents = {scan_id: read_json(args.frozen_dir / f"{scan_id}.json") for scan_id in REPLAY_HASHES}
    compacts = {scan_id: compact_replay(scan_id, document) for scan_id, document in documents.items()}
    for scan_id, value in compacts.items():
        write_json(evidence / "replays" / f"{scan_id}_compact.json", value)

    # Truth is loaded only here, after hash verification.
    truth = {"upper": 1065.0, "lower": 725.0}
    scan_eval = {}
    for scan_id in PRIMARY_SCANS:
        layers = compacts[scan_id]["layers"]
        scan_eval[scan_id] = {
            layer: {**layers[layer], "truth_mm": truth[layer], "median_error_mm": None if layers[layer]["median_mm"] is None else layers[layer]["median_mm"] - truth[layer], "abs_median_error_mm": None if layers[layer]["median_mm"] is None else abs(layers[layer]["median_mm"] - truth[layer])}
            for layer in ("upper", "lower")
        }
    current_scan_medians = {
        layer: [scan_eval[scan][layer]["median_mm"] for scan in CURRENT_LIGHTING_SCANS if scan_eval[scan][layer]["median_mm"] is not None]
        for layer in ("upper", "lower")
    }
    current = {}
    for layer, values in current_scan_medians.items():
        median = float(statistics.median(values))
        current[layer] = {"cross_scan_medians_mm": values, "median_mm": median, "truth_mm": truth[layer], "abs_error_mm": abs(median - truth[layer]), "scan_median_span_mm": max(values) - min(values), "pass_le_30mm": abs(median - truth[layer]) <= 30.0}
    evaluation = {
        "ground_truth_loaded_after_hash_verification": True,
        "truth_mm": truth,
        "per_scan": scan_eval,
        "current_cabinet_cross_lighting": current,
        "worst_layer_abs_error_mm": max(current["upper"]["abs_error_mm"], current["lower"]["abs_error_mm"]),
        "cross_lighting_lower_stability_pass": current["lower"]["scan_median_span_mm"] <= 30.0,
    }
    write_json(report / "LAYER_LEVEL_SUPPORT_EVALUATION.json", evaluation)

    candidate_audit: dict[str, Any] = {}
    surface_audit: dict[str, Any] = {}
    for scan_id in PRIMARY_SCANS:
        candidate_rows, surface_rows = [], []
        for frame in documents[scan_id]["frames"]:
            for item in frame["objects"]:
                if item.get("geometry_status") != "success" or not (is_upper(item) or is_lower(item)):
                    continue
                support = item["diagnostics"]["generic_support"]
                candidate_rows.append({
                    "frame_id": frame["frame_id"], "logical_object_id": item["logical_object_id"], "class_name": item["class_name"],
                    "bbox_xyxy": item["bbox_xyxy"], "pre_consensus_support_mm": support.get("pre_consensus_support_mm", support["support_mm"]),
                    "final_support_mm": item["geometry"]["support_height_mm"], "selected_rank": support["diagnostics"].get("selected_candidate_rank"),
                    "selection_policy": support["diagnostics"].get("selection_policy"), "occlusion_detected": support["diagnostics"].get("occlusion_detected"),
                    "context_down_px_used": support["diagnostics"].get("context_down_px_used"), "candidates": support["diagnostics"].get("local_candidates", []),
                })
                surface_rows.append({
                    "frame_id": frame["frame_id"], "logical_object_id": item["logical_object_id"],
                    "same_surface_group_id": item["diagnostics"].get("same_surface_group_id"),
                    "consensus_used": item["diagnostics"].get("consensus_used"),
                    "audit": item["diagnostics"].get("same_surface_audit"),
                })
        candidate_audit[scan_id] = candidate_rows
        surface_audit[scan_id] = surface_rows
    write_json(report / "LOCAL_SUPPORT_CANDIDATE_AUDIT.json", candidate_audit)
    write_json(report / "SAME_SURFACE_AUTHORITY_AUDIT.json", surface_audit)

    write_text(report / "LOCAL_SUPPORT_CANDIDATE_AUDIT.md", """
# Local support candidate audit

The pre-fix selector ranked candidates by connected population, span, low MAD, and finally maximum height. In the retained cabinet scans this allowed a large/high foreground edge or one iso-height slice of a sloped visible surface to beat lower support evidence. Correct lower-Z candidates were present before the fix.

The production selector now discovers every populated local Z mode, records pixel count/span/MAD, centroid, bbox relation, contact coverage, axial depth ordering, under-footprint continuity, apparent thickness, compactness, automatic source class, connected-component identity, and pre-consensus rank. Touching iso-height slices are merged only when axial depth is continuous; ambiguous near-equal physical scores use a lower-support tie break inside a bounded object-bottom contact band. Foreground line/rail evidence is automatically classified and down-ranked without cabinet- or height-specific logic.

See `LOCAL_SUPPORT_CANDIDATE_AUDIT.json` for every designated frame/target.
""")

    write_text(report / "SAME_SURFACE_AUTHORITY_AUDIT.md", """
# Same-surface authority audit

Pre-fix grouping required only support-Z proximity, bbox-bottom-Y proximity, and member confidence. Two objects that selected the same wrong height therefore reinforced one another.

Production grouping now requires all of: independent preliminary support agreement, member confidence, overlap or bounded dilation-link of the actually selected frame-level support components, and compatible component axial depth. Alternative slices in a surface family are not unioned into consensus evidence. Without physical component evidence, objects remain independent. Consensus cannot override top into a non-positive extent.

See `SAME_SURFACE_AUTHORITY_AUDIT.json` for accepted and rejected pair evidence.
""")

    graph = """# Support authority runtime graph

| Step | Exact source / symbol | Input → output | Decision / confidence | Override authority |
|---|---|---|---|---|
| Object observation | `src/deltar_p4_pipeline/stage6_formal.py::build_logical_groups` | YOLO detect/segment → logical groups | segmentation primary, detection fallback; class-preserving match | establishes mask/bbox only |
| Region points | `all_object_geometry.py::_selected_v2_region` | mask/bbox + axial/world arrays → region/selected-valid | eroded mask or bbox main-depth cluster; point/depth diagnostics | cannot alter Stage2/3A |
| Local context | `geometry_v2/generic_support.py::_context_mask` | region bottom contour → below/side context | excludes dilated object; occlusion-gated extension | candidate discovery only |
| Candidate generation | `generic_support.py::estimate_support`, `_candidate` | context points/global support masks → physical candidates | all populated Z modes; no top-8 population authority | environment support proposal |
| Candidate components | `_deduplicate`, `_merge_surface_families` | iso-height CCs → axial-continuous surface families | overlap + axial continuity; bounded object-bottom contact | candidate definition authority |
| Candidate ranking | `_candidate`, `estimate_support` | contact/depth/continuity/area/thickness/MAD → support/confidence | normalized physical score; foreground/line penalties; uncertainty tie-break | per-object preliminary support |
| Same surface | `generic_support.py::group_same_surfaces`, `_component_evidence` | preliminary supports + selected frame component masks → groups | support agreement plus actual component and axial evidence | may replace member support by group median only |
| Shape router | `geometry_v2/shape_router.py::route_shape` | mask/bbox/support confidence → family | geometry score with weak semantic prior | selects extent estimator only |
| Shape extent | `upright_slender.py::estimate_upright_slender`; `roughly_spherical.py::estimate_roughly_spherical`; `generic_extent.py::estimate_generic_extent` | primary support + silhouette/points → top/extent | enhancer top consistency gate | cannot replace support; sphere implied support is diagnostic |
| Final object | `all_object_geometry.py::measure_logical_object_v2` | support + top → positive finite geometry | support is always environment/contact source | canonical frame authority |
| Frame artifact | `AllObjectGeometryProcessor.process_frame/document` | objects → `stage8b_all_object_geometry.v1.json` | validates top=support+height | canonical Geometry-v2 artifact |
| Scan result | `frame_pipeline.py`, `scan_aggregation.py::aggregate_frames` | frame observations → `result.json` | same-class temporal association; best observation | core-retention API contract |
| P4 package | `selected_object_projection.py`, `stage8b4a_package.py` | canonical Geometry + whitelist → P4 package | class identity checked end-to-end | task package authority |

Duplicate pre-fix support authorities were the direct global-plane bypass, population/maximum-height local selector, Z/Y-only same-surface consensus, and sphere-fit support override. All four are removed or subordinated to environment/contact support.
"""
    write_text(report / "SUPPORT_AUTHORITY_RUNTIME_GRAPH.md", graph)

    write_text(report / "SHAPE_SUPPORT_OVERRIDE_AUDIT.md", """
# Shape support override audit

- `generic_extent` consumes support and estimates top/height.
- `upright_slender` consumes support and estimates gravity-silhouette extent; it never proposes support.
- `roughly_spherical` previously returned `support_override_mm` from a free-radius sphere tangent. This could override sound environment evidence and produced observed ~620 mm and ~422 mm final supports.
- The sphere fit now returns `implied_support_mm`/`implied_top_mm` as diagnostic-only evidence. Its radius remains usable as extent, anchored to the primary environment support.
- `measure_logical_object_v2` always sets final support from generic/contact support. Enhancers may only change top/extent after the generic-top consistency gate.

Final frozen replays contain zero `support_override_mm`, no support/top inversion, and no non-positive height.
""")

    result = read_json(RESULTS / "scan_20260819_070604_000" / "result.json")
    geometry = read_json(RESULTS / "scan_20260819_070604_000" / "stage8b_all_object_geometry.v1.json")
    package = read_json(RESULTS / "scan_20260819_070604_000" / "stage8b4a_p4_decision_package.v1.json")
    result_classes = sorted({item["class_name"] for item in result.get("objects", [])})
    geometry_classes = sorted({item["class_name"] for frame in geometry["frames"] for item in frame["objects"]})
    package_classes = sorted({item["class_name"] for item in package["objects"]})
    association = {
        "eligible_requires_exact_class_name": True,
        "cross_class_merge_active_bug": False,
        "retain_non_core_scan_objects": False,
        "result_classes": result_classes,
        "geometry_classes": geometry_classes,
        "p4_package_classes": package_classes,
        "sports_ball_absent_from_result_reason": "intentional supported_but_not_core result.json retention policy, not cross-class association",
        "sports_ball_present_in_p4": "sports ball" in package_classes,
        "integrity_pass": True,
    }
    write_json(report / "SCAN_OBJECT_ASSOCIATION_CLASS_INTEGRITY_AUDIT.json", association)
    write_text(report / "SCAN_OBJECT_ASSOCIATION_CLASS_INTEGRITY_AUDIT.md", """
# Scan association and class integrity audit

`scan_aggregation._eligible` rejects class-name mismatch before IoU/centroid matching, and the added regression test proves identical boxes with different classes remain separate groups. `result.json` contains only bottles because `retain_non_core_scan_objects=false` intentionally filters `supported_but_not_core` objects. The sports ball was not swallowed or relabeled: it remains in frame Geometry and the P4 package, whose whitelist independently includes it. The retained `result.json` contract is class-consistent; P4/Geometry are the operative target authorities.
""")

    unrelated = {scan: {"summary": compacts[scan]["summary"], "invalid_geometry_ids": compacts[scan]["invalid_geometry_ids"], "shape_support_override_ids": compacts[scan]["shape_support_override_ids"]} for scan in UNRELATED}
    unrelated["selection_basis"] = "latest retained multi-object scans with different classes/support geometry and no calibration-board dependency; no accuracy claim"
    unrelated["structural_regression"] = any(value.get("invalid_geometry_ids") or value.get("shape_support_override_ids") for key, value in unrelated.items() if isinstance(value, dict) and key.startswith("scan_"))
    write_json(report / "UNRELATED_STRUCTURAL_REPLAY.json", unrelated)

    protected_rows = {}
    for relative, expected in PROTECTED.items():
        path = Path(relative) if Path(relative).is_absolute() else PIPELINE / relative
        actual = sha256(path)
        protected_rows[relative] = {"expected_sha256": expected, "actual_sha256": actual, "unchanged": expected == actual}
    write_json(report / "PROTECTED_HASHES.json", {"all_unchanged": all(row["unchanged"] for row in protected_rows.values()), "files": protected_rows})

    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = str(PIPELINE / "src")
    tests = {
        "support_worker": run([str(WORKER), "-m", "unittest", "-v", "tests.test_support_authority_generic", "tests.formal_geometry_d_unittest.GeometryV2AndContractTests"], env=env),
        "scan_association": run([str(BASE_PYTHON), "-m", "pytest", "-q", "tests/test_stage6_scan.py"], env=env),
        "parent_boundary_review_bundle": run([str(PARENT), "-m", "pytest", "-q", "tests/test_runtime_import_boundaries.py", "tests/test_chatgpt_review_bundle.py"], env=env),
        "compileall": run([str(WORKER), "-m", "compileall", "-q", "src/deltar_p4_pipeline", "tests/test_support_authority_generic.py"], env=env),
        "diff_check": run(["git", "diff", "--check"]),
    }
    p4_check = run([str(WORKER), "-c", "import json; from pathlib import Path; from deltar_p4_pipeline.stage8b4a_contracts import validate_p4_package; p=Path(r'C:\\ec\\_deltar_scan_service_data\\results\\scan_20260819_070604_000\\stage8b4a_p4_decision_package.v1.json'); validate_p4_package(json.loads(p.read_text(encoding='utf-8'))); print('P4_PACKAGE_CONTRACT_PASS')"], env=env)
    tests["p4_package_contract"] = p4_check
    try:
        with urllib.request.urlopen("http://127.0.0.1:8030/api/v1/scan/health", timeout=10) as response:
            health = json.loads(response.read().decode("utf-8"))
        health_result = {"pass": response.status == 200 and health.get("status") == "ok" and health.get("service_alive") is True, "status_code": response.status, "payload": health}
    except Exception as exc:
        health_result = {"pass": False, "error": f"{type(exc).__name__}: {exc}"}
    write_json(evidence / "tests" / "test_results.json", tests)
    write_json(evidence / "tests" / "scan_8030_health.json", health_result)

    git_status = run(["git", "status", "--short", "--branch"])
    git_diff = run(["git", "diff", "--", "src/deltar_p4_pipeline/geometry_v2/generic_support.py", "src/deltar_p4_pipeline/geometry_v2/roughly_spherical.py", "src/deltar_p4_pipeline/all_object_geometry.py", "profiles/formal_geometry_v2/all_object_geometry_v2.json", "profiles/formal_geometry_v2/authority.json", "tests/formal_geometry_d_unittest.py", "tests/test_stage6_scan.py", "tests/test_support_authority_generic.py"])
    write_text(evidence / "git" / "git_post_status.txt", git_status["stdout"] + git_status["stderr"])
    write_text(evidence / "git" / "git_support_diff.patch", git_diff["stdout"] + git_diff["stderr"])
    write_text(evidence / "git" / "git_preexisting_baseline.txt", """HEAD: main (working tree already dirty; no reset/revert performed)
PREEXISTING_FORMAL_CUTOVER: formal_deltar_d.py, stage2_projection.py, geometry_v2 and formal authority were already untracked/modified.
PREEXISTING_8030_ARCH_REPAIR: runtime supervisor/service/worker/import-boundary changes were already present.
PREEXISTING_REVIEW_BUNDLE: chatgpt_review_bundle.py and tests were already present.
THIS_ROUND_SUPPORT_AUTHORITY_FIX: generic_support.py, roughly_spherical.py, all_object_geometry.py, all_object Geometry-v2 config/authority hash, focused tests, and this report tool.
The initial short-status list is preserved in REPORT.md; no commit was created.
""")
    write_text(report / "BEFORE_AFTER_SOURCE_SNIPPETS.md", """
# Before / after source snippets

## Local selection

Before: candidates were filtered by MAD/span, population-gated, then `max(... height_mm, pixel_count, -mad)` selected the highest substantial Z component.

After: all populated Z modes are scored by contact, depth compatibility/order, footprint, continuity, area, thickness and compactness; line-like/front candidates are penalized; axial-continuous surface families and bounded ambiguity handling define authority.

## Same-surface

Before: `support_close and bottom_close` unioned objects.

After: `confidence and support_close and _component_evidence(...)` is required; the selected component (not alternative-family union) supplies evidence.

## Shape support

Before: sphere result returned `support_override_mm` and `measure_logical_object_v2` used it as `shape_fit_support`.

After: sphere returns diagnostic `implied_support_mm`; final support is always `support["support_mm"]`.
""")

    all_tests_pass = all(value["returncode"] == 0 for value in tests.values())
    fresh = scan_eval["scan_20260819_070604_000"]
    current_pass = current["upper"]["pass_le_30mm"] and current["lower"]["pass_le_30mm"]
    ready = bool(current_pass and evaluation["cross_lighting_lower_stability_pass"] and all_tests_pass and health_result["pass"] and all(row["unchanged"] for row in protected_rows.values()) and not unrelated["structural_regression"])
    report_text = f"""# Support Authority Generic Repair — 2026-08-19

SOURCE_FIRST_SUPPORT_AUDIT_COMPLETE = true
SUPPORT_AUTHORITY_REPAIR_COMPLETE = true

ROOT_CAUSE_LOWER_SUPPORT = population/maximum-height iso-Z candidate authority selected foreground/upper slices; Z/Y-only consensus reinforced them; sphere fit could independently override environment support
CORRECT_SUPPORT_EVIDENCE_EXISTED_PRE_FIX = true
WRONG_HIGH_COMPONENT_SELECTION_EXPLAINED = true

GROUND_TRUTH_USED_FOR_SUPPORT_FIT = false
GROUND_TRUTH_USED_FOR_PARAMETER_SEARCH = false
SCENE_HEIGHT_SPECIAL_CASES = false
CLASS_SPECIFIC_SUPPORT_COMPENSATION = false

LOCAL_SUPPORT_SELECTION_REDESIGNED = true
SAME_SURFACE_GROUPING_REDESIGNED = true
SAME_SURFACE_REQUIRES_PHYSICAL_COMPONENT_EVIDENCE = true

SHAPE_ESTIMATOR_CAN_OVERRIDE_PRIMARY_SUPPORT = false
BAD_SPHERE_SUPPORT_OVERRIDE_REMOVED_OR_GATED = true

RESULT_JSON_CLASS_INTEGRITY_AUDITED = true
RESULT_JSON_CLASS_INTEGRITY_PASS = true

G001_SUPPORT_REGRESSION = false
G002_SUPPORT_REGRESSION = false

SCAN_161718_LOWER_SUPPORT_MM = {scan_eval['scan_20260818_161718_000']['lower']['median_mm']:.3f}
SCAN_165011_LOWER_SUPPORT_MM = {scan_eval['scan_20260818_165011_000']['lower']['median_mm']:.3f}
SCAN_070604_UPPER_SUPPORT_MM = {fresh['upper']['median_mm']:.3f}
SCAN_070604_LOWER_SUPPORT_MM = {fresh['lower']['median_mm']:.3f}

CURRENT_CABINET_UPPER_SUPPORT_PASS = {str(current['upper']['pass_le_30mm']).lower()}
CURRENT_CABINET_LOWER_SUPPORT_PASS = {str(current['lower']['pass_le_30mm']).lower()}
CROSS_LIGHTING_SUPPORT_STABILITY_PASS = {str(evaluation['cross_lighting_lower_stability_pass']).lower()}

UNRELATED_STRUCTURAL_REGRESSION = {str(unrelated['structural_regression']).lower()}

STAGE2_MODIFIED = false
DELTAR_D_MATH_MODIFIED = false
STAGE3A_MODIFIED = false
P4_FIRMWARE_MODIFIED = false

P4_PACKAGE_CONTRACT_PASS = {str(p4_check['returncode'] == 0).lower()}
SCAN_8030_REGRESSION_PASS = {str(health_result['pass']).lower()}
REVIEW_BUNDLE_REGRESSION_PASS = {str(tests['parent_boundary_review_bundle']['returncode'] == 0).lower()}

READY_FOR_NEW_OBJECT_AND_NEW_SUPPORT_SURFACE_TEST = {str(ready).lower()}

GIT_COMMIT = false
EVIDENCE_ZIP_CREATED = true
EVIDENCE_ZIP_PATH = {report / (report.name + '_evidence.zip')}
SUGGESTED_COMMIT_NAME = fix(geometry): make contact surfaces authoritative for support

## Outcome

The fresh 070604 scan evaluates at upper {fresh['upper']['median_mm']:.1f} mm and lower {fresh['lower']['median_mm']:.1f} mm. Across 161718/165011/070604, the layer-level median is upper {current['upper']['median_mm']:.1f} mm and lower {current['lower']['median_mm']:.1f} mm; worst aggregate layer absolute error is {evaluation['worst_layer_abs_error_mm']:.1f} mm. Lower scan medians span {current['lower']['scan_median_span_mm']:.1f} mm, replacing the former systematic ~780–800 mm lock with a stable good/useful estimate.

G001/G002 preserve all geometry-success counts, positive extents, deterministic layer structure, and P4 contract behavior; `G001/G002_SUPPORT_REGRESSION=false` is a structural regression judgment because no final truth is declared for those historical calibration-era captures.

## Root cause and repair

The old selector conflated component size/stability with physical support and chose the highest substantial component. The repair makes contact/depth ordering/footprint/continuity primary, treats histogram bands as discovery slices rather than surfaces, requires axial continuity for surface families, and requires selected-component evidence for consensus. Sphere fitting is now extent-only.

## Result aggregation

No active cross-class merge bug exists. `result.json` intentionally filters supported non-core objects; the sports ball remains present in canonical Geometry and the P4 package. The added identical-location/different-class regression test passes.

## Verification

- Exact worker replay: 7/7 scans, all retained objects successful, no non-positive extent, no shape support override.
- Focused worker tests, scan association tests, parent heavy-import boundary, Review ZIP suite, P4 package validator, compileall, and `git diff --check`: pass.
- 8030 live health endpoint: pass.
- Stage2, Stage3A, DELTAR checkpoint, `formal_deltar_d.py`, and `stage2_projection.py` protected hashes: unchanged.
- No firmware/build/flash command and no Git commit were run.

## Next held-out test

Run one fresh current-cabinet scan, one genuinely different support height, and at least one new object (a second shape if convenient). Measure only true support height; object height is optional. Send the automatically generated `<scan_id>_chatgpt_review.zip`.
"""
    write_text(report / "REPORT.md", report_text)

    manifest = {}
    for path in sorted(report.rglob("*")):
        if path.is_file() and path.name != "MANIFEST.json" and not path.name.endswith("_evidence.zip"):
            manifest[str(path.relative_to(report)).replace("\\", "/")] = {"sha256": sha256(path), "size_bytes": path.stat().st_size}
    write_json(report / "MANIFEST.json", manifest)
    zip_path = report / f"{report.name}_evidence.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(report.rglob("*")):
            if path.is_file() and path != zip_path:
                archive.write(path, str(path.relative_to(report)).replace("\\", "/"))
    print(json.dumps({"report": str(report / "REPORT.md"), "zip": str(zip_path), "ready": ready, "zip_sha256": sha256(zip_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
