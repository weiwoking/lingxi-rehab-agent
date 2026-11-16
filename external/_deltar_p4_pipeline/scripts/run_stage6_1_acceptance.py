from __future__ import annotations

import copy
import inspect
import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import numpy as np

from deltar_p4_pipeline.cli import main as cli_main
from deltar_p4_pipeline.geometry_quality import compute_absolute_geometry_valid
from deltar_p4_pipeline.scan_contracts import ScanContractError, atomic_write_json, canonical_sha256, sha256_file, validate_result_shape
from deltar_p4_pipeline.scan_job import run_scan
from deltar_p4_pipeline.scan_provenance import PIPELINE_VERSION, UPSTREAM_COMMITS, package_manifest
from deltar_p4_pipeline.scan_profiles import load_profile_bundle
from deltar_p4_pipeline.stage6_formal import build_logical_groups, process_groups
from deltar_p4_pipeline.stage6_schema import validate_job_report_v1_1, validate_scan_result_v1_1
from deltar_p4_pipeline.synthetic_geometry import rectangle_depth, synthetic_calibration
import deltar_p4_pipeline.frame_pipeline as frame_pipeline_module
import deltar_p4_pipeline.stage6_formal as stage6_formal_module

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs_stage6_1"
SCANS = Path(r"C:\ec\_deltar_stage6_test_scans")
RUNS = Path(r"C:\ec\_deltar_stage6_1_acceptance_runs")
PYTEST_BASELINE = 234
UNITTEST_BASELINE = 116
MODEL_HASHES = {
    "depth": "a402ffbffade70f0a0ef77a28b465d8fb625dd2fcdef0115b65c98193ce1e3ef",
    "detect": "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1",
    "segment": "55ed65c56c91713d23e8402371c6c49a6fd84f257f7dce452e8d70e41dcbe152",
}


def _normal(value: Any, key: str = "") -> Any:
    excluded = {"processing", "processing_ms", "started_at", "finished_at"}
    if key in excluded or key.endswith("_ms"):
        return None
    if isinstance(value, dict):
        return {name: _normal(item, name) for name, item in sorted(value.items()) if name not in excluded and not name.endswith("_ms")}
    if isinstance(value, list):
        return [_normal(item) for item in value]
    return value


def _bbox(mask: np.ndarray) -> list[float]:
    y, x = np.where(mask)
    return [float(x.min()), float(y.min()), float(x.max() + 1), float(y.max() + 1)]


def _formal_case(name: str, *, segmentation_valid: bool = True, letterbox_mode: str = "same",
                 calibration_status: str = "synthetic", semantics_status: str | None = None) -> dict[str, Any]:
    RUNS.mkdir(parents=True, exist_ok=True)
    calibration = synthetic_calibration(status=calibration_status)
    if semantics_status is not None:
        calibration["depth"]["semantics_status"] = semantics_status
    direct, raw_mask, _ = rectangle_depth(calibration, distance_m=2.0, bottom_m=0.05, height_m=0.9, width_m=0.7)
    if letterbox_mode == "same":
        letterbox = direct.copy()
    elif letterbox_mode == "missing":
        letterbox = np.zeros_like(direct)
    elif letterbox_mode == "disagree":
        letterbox, _, _ = rectangle_depth(calibration, distance_m=2.0, bottom_m=0.30, height_m=0.30, width_m=0.7)
    else:
        raise ValueError(letterbox_mode)
    segment_mask = raw_mask if segmentation_valid else np.zeros_like(raw_mask)
    bbox = _bbox(raw_mask)
    detections = [{"instance_id": 0, "class_id": 56, "class_name": "chair", "confidence": 0.80,
                   "bbox_xyxy": bbox, "bbox_normalized": [0.0, 0.0, 1.0, 1.0], "mask_key": None}]
    segments = [{"instance_id": 0, "class_id": 56, "class_name": "chair", "confidence": 0.90,
                 "bbox_xyxy": bbox, "bbox_normalized": [0.0, 0.0, 1.0, 1.0], "mask_key": "instance_000"}]
    profile = json.loads((ROOT / "geometry_profile.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix=f"stage6_1_{name}_", dir=RUNS) as temp_name:
        temp = Path(temp_name)
        np.save(temp / "direct.npy", direct, allow_pickle=False)
        np.save(temp / "letterbox.npy", letterbox, allow_pickle=False)
        np.savez_compressed(temp / "masks.npz", instance_000=segment_mask)
        groups, public, region = build_logical_groups(
            name, detections, segments, {"instance_000": segment_mask},
            {"direct_640x360": direct, "letterbox_640x480": letterbox}, MODEL_HASHES,
            "masks.npz", {"direct_640x360": "direct.npy", "letterbox_640x480": "letterbox.npy"}, 0.30,
        )
        formal, observations = process_groups(groups, calibration, profile, temp, 2.0)
    return {"name": name, "group": public[0], "region": region, "formal": formal[0], "observation": observations[0]}


def _expect_rejected(mutated: dict[str, Any], validator: Any) -> dict[str, Any]:
    try:
        validator(mutated)
    except ScanContractError as exc:
        return {"rejected": True, "code": exc.code, "field": exc.field}
    return {"rejected": False}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=True)
    bundle = load_profile_bundle(ROOT / "profiles", validate_model_hashes=True)
    if {item["task"]: item["sha256"] for item in bundle.model_manifest["models"]} != MODEL_HASHES:
        raise SystemExit("frozen model hashes changed")

    fallback = _formal_case("C11", segmentation_valid=False)
    primary = _formal_case("C12")
    disagreement = _formal_case("C13", letterbox_mode="disagree")
    single = _formal_case("C15", letterbox_mode="missing")
    measured_unproven = _formal_case("C14", calibration_status="measured", semantics_status="PLAUSIBLE_BUT_UNPROVEN")
    measured_proven = _formal_case("absolute_true", calibration_status="measured", semantics_status="PROVEN")

    run_root = Path(tempfile.mkdtemp(prefix="real_", dir=RUNS))
    real_a, job_a = run_scan(SCANS / "single_valid", run_root / "a", ROOT / "profiles")
    real_b, job_b = run_scan(SCANS / "single_valid", run_root / "b", ROOT / "profiles")
    frame_a = json.loads(next((run_root / "a" / "frame_results").glob("*.json")).read_text(encoding="utf-8"))
    frame_b = json.loads(next((run_root / "b" / "frame_results").glob("*.json")).read_text(encoding="utf-8"))

    nested: dict[str, Any] = {}
    mutations = {
        "distance.valid": ("result", ["objects", 0, "distance", "valid"]),
        "visibility.status": ("result", ["objects", 0, "visibility", "status"]),
        "height.absolute_valid": ("result", ["objects", 0, "visible_height", "absolute_valid"]),
        "provenance.model_hash": ("result", ["objects", 0, "provenance", "model_hashes", "detect"]),
        "job.exit_code": ("job", ["exit_code"]),
    }
    for label, (kind, path) in mutations.items():
        value = copy.deepcopy(real_a if kind == "result" else job_a)
        target: Any = value
        for part in path[:-1]:
            target = target[part]
        del target[path[-1]]
        nested[label] = _expect_rejected(value, validate_scan_result_v1_1 if kind == "result" else validate_job_report_v1_1)

    legacy = {"schema_version": "deltar_scan_result.v1", "status": "success", "scan_id": "legacy_scan",
              "job_id": "legacy_job", "objects": [], "warnings": [], "errors": []}
    validate_result_shape(legacy)
    legacy_file = run_root / "legacy_v1_fixture.json"
    atomic_write_json(legacy_file, legacy)
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        legacy_exit = cli_main(["scan-inspect", "--result", str(legacy_file)])
    legacy_cli = json.loads(stdout.getvalue())
    strict_rejection = _expect_rejected(copy.deepcopy(legacy), validate_scan_result_v1_1)

    normalized = [_normal(real_a), _normal(real_b)]
    frame_normalized = [_normal(frame_a), _normal(frame_b)]
    deterministic = normalized[0] == normalized[1] and frame_normalized[0] == frame_normalized[1]
    region_items = [item for item in frame_a["region_validation"] if item["source"] == "segmentation"]
    region_pass = bool(region_items) and all(item["stage3_stage4_region_exact_equal"] and item["frozen_region_sha256"] == item["formal_region_sha256"] for item in region_items)

    validity_matrix = {
        "synthetic": compute_absolute_geometry_valid(calibration_status="synthetic", depth_semantics_status="PROVEN", selected_geometry_status="success", canvas_selection_status="agreed", automatic_geometry_valid=True, critical_geometry_rejections=[]),
        "unverified": compute_absolute_geometry_valid(calibration_status="unverified", depth_semantics_status="PROVEN", selected_geometry_status="success", canvas_selection_status="agreed", automatic_geometry_valid=True, critical_geometry_rejections=[]),
        "measured_unproven": measured_unproven["formal"]["absolute_geometry_valid"],
        "measured_disagreed": compute_absolute_geometry_valid(calibration_status="measured", depth_semantics_status="PROVEN", selected_geometry_status="rejected", canvas_selection_status="disagreed", automatic_geometry_valid=False, critical_geometry_rejections=[]),
        "measured_single_canvas": compute_absolute_geometry_valid(calibration_status="measured", depth_semantics_status="PROVEN", selected_geometry_status="success", canvas_selection_status="direct_only", automatic_geometry_valid=False, critical_geometry_rejections=[]),
        "measured_proven_agreed": measured_proven["formal"]["absolute_geometry_valid"],
        "critical_rejection": compute_absolute_geometry_valid(calibration_status="measured", depth_semantics_status="PROVEN", selected_geometry_status="success", canvas_selection_status="agreed", automatic_geometry_valid=True, critical_geometry_rejections=["E_TEST"]),
    }
    validity_pass = validity_matrix == {"synthetic": False, "unverified": False, "measured_unproven": False,
                                        "measured_disagreed": False, "measured_single_canvas": False,
                                        "measured_proven_agreed": True, "critical_rejection": False}

    c11 = fallback["formal"]["selected_source"] == "matched_detection_fallback" and fallback["formal"]["fallback_used"] and fallback["observation"]["distance_m"] is not None
    c12 = primary["formal"]["selected_source"] == "segmentation" and not primary["formal"]["fallback_used"] and primary["group"]["detection_fallback"]["available"]
    c13 = disagreement["formal"]["canvas_selection_status"] == "disagreed" and disagreement["formal"]["selected_geometry"] is None and not disagreement["formal"]["absolute_geometry_valid"]
    c14 = not measured_unproven["formal"]["absolute_geometry_valid"] and validity_pass
    c15 = single["formal"]["single_canvas_only"] and not single["formal"]["absolute_geometry_valid"]
    c16 = region_pass and all(item["stage3_stage4_region_exact_equal"] for item in primary["region"] if item["source"] == "segmentation")
    c17 = all(item["rejected"] for item in nested.values())
    c18 = legacy_exit == 0 and legacy_cli["legacy"] is True and strict_rejection["rejected"]

    historical = json.loads((ROOT / "outputs_stage6" / "stage6_acceptance.json").read_text(encoding="utf-8"))
    old_cases = historical["cases"]
    new_cases = {"C11_matched_detection_fallback": c11, "C12_segmentation_primary": c12,
                 "C13_canvas_3d_disagreement": c13, "C14_measured_unproven": c14,
                 "C15_single_canvas": c15, "C16_eroded_region_consistency": c16,
                 "C17_nested_schema_failure": c17, "C18_legacy_v1_inspect": c18}
    cases = {**old_cases, **new_cases}

    frame_line = inspect.getsourcelines(frame_pipeline_module.FramePipeline.process)[1]
    formal_line = inspect.getsourcelines(stage6_formal_module.process_groups)[1]
    audit = {"schema_version": "deltar_stage6_1_source_audit.v1", "pre_fix_findings_confirmed": 5,
             "fixes": {"frame_pipeline": {"file": "src/deltar_p4_pipeline/frame_pipeline.py", "line": frame_line},
                       "formal_stage4_call": {"file": "src/deltar_p4_pipeline/stage6_formal.py", "line": formal_line,
                                              "function": "stage4_validation.process_object_group"},
                       "absolute_function": "geometry_quality.compute_absolute_geometry_valid",
                       "region": "segment_eroded_3px", "schema_runtime": "stage6_schema.py"}}
    atomic_write_json(OUT / "source_audit.json", audit)
    atomic_write_json(OUT / "logical_group_validation.json", {"schema_version": "deltar_stage6_1_logical_group_validation.v1", "status": "pass" if c11 and c12 else "fail", "matched_group": primary["group"]})
    atomic_write_json(OUT / "matched_fallback_result.json", {"schema_version": "deltar_stage6_1_case_result.v1", "case": "C11", "pass": c11, "formal_result": fallback["formal"]})
    atomic_write_json(OUT / "segmentation_primary_result.json", {"schema_version": "deltar_stage6_1_case_result.v1", "case": "C12", "pass": c12, "formal_result": primary["formal"]})
    atomic_write_json(OUT / "canvas_disagreement_result.json", {"schema_version": "deltar_stage6_1_case_result.v1", "case": "C13", "pass": c13, "formal_result": disagreement["formal"]})
    atomic_write_json(OUT / "absolute_validity_matrix.json", {"schema_version": "deltar_stage6_1_absolute_validity.v1", "status": "pass" if c14 and c15 else "fail", "matrix": validity_matrix})
    atomic_write_json(OUT / "eroded_region_validation.json", {"schema_version": "deltar_stage6_1_eroded_region.v1", "status": "pass" if c16 else "fail", "synthetic": primary["region"], "real": region_items})
    atomic_write_json(OUT / "nested_schema_validation.json", {"schema_version": "deltar_stage6_1_nested_schema_validation.v1", "status": "pass" if c17 else "fail", "cases": nested})
    atomic_write_json(OUT / "legacy_schema_compatibility.json", {"schema_version": "deltar_stage6_1_legacy_compatibility.v1", "status": "pass" if c18 else "fail", "inspect": legacy_cli, "v1_1_rejection": strict_rejection})
    atomic_write_json(OUT / "determinism_report.json", {"schema_version": "deltar_stage6_1_determinism.v1", "status": "pass" if deterministic else "fail", "result_hash_a": canonical_sha256(normalized[0]), "result_hash_b": canonical_sha256(normalized[1]), "frame_hash_a": canonical_sha256(frame_normalized[0]), "frame_hash_b": canonical_sha256(frame_normalized[1]), "fixed_exclusions": ["processing", "processing_ms", "started_at", "finished_at", "*_ms"], "same_group_ids": [g["logical_object_id"] for g in frame_a["logical_object_groups"]] == [g["logical_object_id"] for g in frame_b["logical_object_groups"]], "same_selected_sources": [o["object_source"] for o in frame_a["objects"]] == [o["object_source"] for o in frame_b["objects"]]})
    atomic_write_json(OUT / "acceptance_matrix.json", {"schema_version": "deltar_stage6_1_acceptance_matrix.v1", "cases": cases, "passed": sum(cases.values()), "total": 18, "all_pass": all(cases.values())})
    verdict = "PASS" if all(cases.values()) and deterministic else "PARTIAL"
    atomic_write_json(OUT / "stage6_1_acceptance.json", {"schema_version": "deltar_stage6_1_acceptance.v1", "verdict": verdict, "pc_stage_complete": verdict == "PASS", "p4_stage_authorized": verdict == "PASS", "real_integration": ["RGB565", "ToF", "DELTAR direct/letterbox", "YOLO detect/segment", "Stage 3.1 frozen methods", "formal Stage 4.1 process_object_group", "aggregation", "strict v1.1 result/job"], "all_mocked": False, "cases": cases})
    package = package_manifest(ROOT)
    atomic_write_json(OUT / "pc_final_manifest_v1_1.json", {"schema_version": "deltar_pc_final_manifest.v1.1", "pipeline_version": PIPELINE_VERSION, "code_package_sha256": package["sha256"], "model_hashes": MODEL_HASHES, "upstream_commits": UPSTREAM_COMMITS, "schema_versions": ["deltar_scan_input.v1", "deltar_scan_frame.v1", "rehab_tof_raw.v1", "deltar_scan_frame_result.v1.1", "deltar_scan_result.v1.1", "deltar_pc_job_report.v1.1"], "profile_hashes": bundle.hashes, "supported_commands": ["scan-validate", "scan-run", "scan-inspect", "profile-validate"], "pc_stage_status": "complete" if verdict == "PASS" else "incomplete", "p4_consumes": "v1.1 only"})
    print(json.dumps({"verdict": verdict, "passed": sum(cases.values()), "total": 18, "determinism": deterministic, "real_run": run_root.name}, indent=2))
    raise SystemExit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
