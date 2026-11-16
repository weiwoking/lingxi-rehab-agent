from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE = Path(r"C:\ec\_calibration_workspaces\stage2_final_bundle_20260818_01")
REPORT_DIR = Path(r"C:\ec\_codex_reports\stage2_final_bundle_promotion_plan_20260818_01")

S2A = Path(r"C:\ec\_calibration_workspaces\stage2a_camera_intrinsic_20260817_01\candidate\camera_intrinsics.stage2a_candidate.v1.json")
S2A_VALIDATION = Path(r"C:\ec\_calibration_workspaces\stage2a_camera_intrinsic_20260817_01\validation\candidate_validation.json")
S2A_STABILITY = Path(r"C:\ec\_calibration_workspaces\stage2a_camera_intrinsic_20260817_01\stability_diagnostics\stability_summary.json")
S2B = Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\candidate\camera_tof.stage2b_all16_candidate.v2.json")
S2B_FR = Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\candidate\deltar_fr.stage2b_all16_candidate.v2.json")
S2B_COMPARE = Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\final_all16\variant_comparison.json")
S2B_PROTECTED = Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\final_all16\protected_hash_check.final.json")
S2C_ROOT = Path(r"C:\ec\_calibration_workspaces\stage2c_camera_tof_validation_20260818_01")
S2C_VALIDATION = S2C_ROOT / r"validation\validation.stage2c.json"
S2C_SUMMARY = S2C_ROOT / r"validation\summary.stage2c.json"
S2C_COVERAGE = S2C_ROOT / r"coverage\coverage.stage2c.json"
S2C_FROZEN = S2C_ROOT / "frozen_authority_manifest.json"

OLD_S7A = Path(r"C:\calibration\result_20260719_172703\calibration_profile.v2.json")
OLD_S7B = Path(r"C:\calibration\stage7b_result_unitfix_20260724_012041\camera_tof_extrinsics.v1.json")
OLD_S7C = Path(r"C:\calibration\stage7c_world_result_robustfix_20260724_044035\camera_world_pose.v1.json")

PIPE = Path(r"C:\ec\_deltar_p4_pipeline")
DELTAR = Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128")
ESP = Path(r"C:\ec\esp-claw")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def write_text(path: Path, value: str) -> None:
    path.write_text(value.rstrip() + "\n", encoding="utf-8", newline="\n")


def git_state(repo: Path) -> dict:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).stdout.rstrip()

    return {"path": str(repo), "commit": run("rev-parse", "HEAD"), "status_short": run("status", "--short")}


def finite(values) -> bool:
    if isinstance(values, list):
        return all(finite(x) for x in values)
    if isinstance(values, dict):
        return all(finite(x) for x in values.values())
    return not isinstance(values, float) or math.isfinite(values)


def matmul(R, v):
    return [sum(R[i][j] * v[j] for j in range(3)) for i in range(3)]


def add(a, b):
    return [a[i] + b[i] for i in range(3)]


def norm(v):
    return math.sqrt(sum(x * x for x in v))


def determinant3(R):
    return (
        R[0][0] * (R[1][1] * R[2][2] - R[1][2] * R[2][1])
        - R[0][1] * (R[1][0] * R[2][2] - R[1][2] * R[2][0])
        + R[0][2] * (R[1][0] * R[2][1] - R[1][1] * R[2][0])
    )


def project_brown(camera, point):
    intr = camera["intrinsics"]
    fx, fy, cx, cy = intr["fx"], intr["fy"], intr["cx"], intr["cy"]
    dist = intr["distortion"]
    k1, k2, p1, p2, k3 = dist["k1"], dist["k2"], dist["p1"], dist["p2"], dist["k3"]
    x, y = point[0] / point[2], point[1] / point[2]
    r2 = x * x + y * y
    radial = 1.0 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2
    xd = x * radial + 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x * x)
    yd = y * radial + p1 * (r2 + 2.0 * y * y) + 2.0 * p2 * x * y
    return [fx * xd + cx, fy * yd + cy]


def projected_ray(camera, R, t, ray, axial_z_mm):
    scale = axial_z_mm / ray[2]
    return project_brown(camera, add(matmul(R, [scale * x for x in ray]), t))


def source_info(path: Path, role: str) -> dict:
    data = load(path)
    return {
        "role": role,
        "path": str(path),
        "sha256": sha256(path),
        "schema_version": data.get("schema_version"),
        "status": data.get("status", data.get("candidate_status")),
    }


PROTECTED_PATHS = [
    S2A, S2A_VALIDATION, S2A_STABILITY, S2B, S2B_FR, S2B_COMPARE, S2B_PROTECTED,
    S2C_VALIDATION, S2C_SUMMARY, S2C_COVERAGE, S2C_FROZEN,
    Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\stage2b_camera_tof.py"),
    Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\advanced_diagnostics.py"),
    Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\final_all16_analysis.py"),
    OLD_S7A, OLD_S7B, OLD_S7C,
    PIPE / r"profiles\calibration_profile.example.json",
    PIPE / r"profiles\zone_orientation_profile.json",
    PIPE / r"profiles\algorithm_profile.json",
    PIPE / r"profiles\stage7d_horizontal_plane_profile.json",
    PIPE / r"profiles\stage7e_object_geometry_profile.json",
    PIPE / r"profiles\all_object_geometry_profile.json",
    PIPE / r"src\deltar_p4_pipeline\frame_pipeline.py",
    PIPE / r"src\deltar_p4_pipeline\tof_adapter.py",
    PIPE / r"src\deltar_p4_pipeline\canvas.py",
    PIPE / r"src\deltar_p4_pipeline\contracts.py",
    PIPE / r"src\deltar_p4_pipeline\deltar_bridge.py",
    PIPE / r"src\deltar_p4_pipeline\scan_service.py",
    PIPE / r"src\deltar_p4_pipeline\scan_service_worker.py",
    PIPE / r"src\deltar_p4_pipeline\stage7d_contracts.py",
    PIPE / r"src\deltar_p4_pipeline\stage7d_outputs.py",
    PIPE / r"src\deltar_p4_pipeline\stage7e_contracts.py",
    PIPE / r"src\deltar_p4_pipeline\camera_world_contracts.py",
    PIPE / r"src\deltar_p4_pipeline\stage8b4a_package.py",
    DELTAR / r"src\utils\dataloader.py",
    DELTAR / r"src\models\deltar.py",
    DELTAR / r"src\models\encoder.py",
    DELTAR / r"src\utils\model_io.py",
    DELTAR / "evaluate.py",
    DELTAR / r"configs\test_demo_compat.txt",
    ESP / r"application\edge_agent\components\rehab_tof\src\rehab_tof_provider.c",
    ESP / r"application\edge_agent\components\rehab_scan_capture\src\rehab_scan_capture_contract.c",
    ESP / r"application\edge_agent\components\rehab_scan_capture\src\rehab_scan_capture_pair.c",
    ESP / r"application\edge_agent\components\rehab_scan_capture\src\rehab_scan_capture_priv.h",
    ESP / r"application\edge_agent\components\rehab_scan_transfer\src\rehab_scan_transfer_scan.c",
]


def protected_manifest() -> dict:
    result = {}
    for path in PROTECTED_PATHS:
        result[str(path)] = {"exists": path.is_file(), "sha256": sha256(path) if path.is_file() else None}
    return result


WORKSPACE.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)
created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
before = protected_manifest()
write_json(WORKSPACE / "protected_hashes.before.json", {"generated_at": created_at, "files": before})

s2a = load(S2A)
s2a_validation = load(S2A_VALIDATION)
s2a_stability = load(S2A_STABILITY)
s2a_stability_displacement = s2a_stability["current"]["comparisons"]["primary_vs_top2"]["mapping"]["full_image"]["equivalent_rectified_displacement_px"]
s2b = load(S2B)
s2b_fr = load(S2B_FR)
s2c_validation = load(S2C_VALIDATION)
s2c_summary = load(S2C_SUMMARY)
s2c_coverage = load(S2C_COVERAGE)

authorities = [
    source_info(S2A, "Stage2A camera intrinsics candidate authority"),
    source_info(S2A_VALIDATION, "Stage2A candidate validation"),
    source_info(S2A_STABILITY, "Stage2A strategy stability diagnostics"),
    source_info(S2B, "Stage2B camera-to-ToF candidate authority"),
    source_info(S2B_FR, "Stage2B DELTAR fr candidate authority"),
    source_info(S2B_COMPARE, "Stage2B all-16 variant comparison"),
    source_info(S2B_PROTECTED, "Stage2B final protected-hash check"),
    source_info(S2C_VALIDATION, "Stage2C independent validation detail"),
    source_info(S2C_SUMMARY, "Stage2C validation summary"),
    source_info(S2C_COVERAGE, "Stage2C validation coverage"),
    source_info(S2C_FROZEN, "Stage2C frozen authority manifest"),
]

source_manifest = {
    "schema_version": "stage2_final_source_authority_manifest.v1",
    "created_at": created_at,
    "assembly_mode": "read_only_sources_no_refit_no_promotion",
    "authorities": authorities,
    "formal_runtime_inventory_not_bundle_authority": [
        {"role": "pre-remount Stage7A inventory", "path": str(OLD_S7A), "sha256": sha256(OLD_S7A)},
        {"role": "pre-remount Stage7B inventory", "path": str(OLD_S7B), "sha256": sha256(OLD_S7B)},
        {"role": "stale-after-remount Stage7C inventory; MUST NOT REUSE", "path": str(OLD_S7C), "sha256": sha256(OLD_S7C)},
    ],
}
write_json(WORKSPACE / "SOURCE_AUTHORITY_MANIFEST.json", source_manifest)

bundle = {
    "schema_version": "stage2_final_calibration_bundle.v1",
    "bundle_status": "CANDIDATE_NOT_PROMOTED",
    "created_at": created_at,
    "assembly": {
        "workspace": str(WORKSPACE),
        "source_mode": "frozen_authorities_copied_without_refit",
        "promotion_executed": False,
        "stage3_started": False,
        "runtime_modified": False,
        "formal_calibration_modified": False,
    },
    "source_authorities": authorities,
    "camera_intrinsics": {
        "authority": {"path": str(S2A), "sha256": sha256(S2A)},
        "candidate_status": s2a["candidate_status"],
        "camera": deepcopy(s2a["camera"]),
        "board": deepcopy(s2a["board"]),
        "solver": deepcopy(s2a["solver"]),
        "metrics": deepcopy(s2a["metrics"]),
        "source_pose_ids": deepcopy(s2a["source_pose_ids"]),
        "source_scan_ids": deepcopy(s2a["source_scan_ids"]),
        "strategy_stability": deepcopy(s2a["strategy_stability"]),
        "stability_diagnostic": deepcopy(s2a_stability),
        "candidate_validation": deepcopy(s2a_validation),
    },
    "camera_tof": {
        "authority": {"path": str(S2B), "sha256": sha256(S2B)},
        "status": s2b["status"],
        "selected_solver_variant": s2b["selected_solver_variant"],
        "camera_intrinsics_reference": deepcopy(s2b["camera_intrinsics_reference"]),
        "gauge": deepcopy(s2b["gauge"]),
        "range_semantics": deepcopy(s2b["range_semantics"]),
        "tof_low_dimensional_model": deepcopy(s2b["tof_low_dimensional_model"]),
        "T_camera_from_tof": deepcopy(s2b["T_camera_from_tof"]),
        "raw_zone_camera_mapping": deepcopy(s2b["raw_zone_camera_mapping"]),
        "validation_evidence": deepcopy(s2b["validation_evidence"]),
        "stability_evidence_summary": deepcopy(s2b["stability_evidence_summary"]),
        "provenance": deepcopy(s2b["provenance"]),
        "warnings": deepcopy(s2b["warnings"]),
    },
    "deltar_fr": {
        "authority": {"path": str(S2B_FR), "sha256": sha256(S2B_FR)},
        "schema_version": s2b_fr["schema_version"],
        "status": s2b_fr["status"],
        "selected_solver_variant": s2b_fr["selected_solver_variant"],
        "shape": deepcopy(s2b_fr["shape"]),
        "dtype": s2b_fr["dtype"],
        "units": s2b_fr["units"],
        "field_order": deepcopy(s2b_fr["field_order"]),
        "rectangle_semantics": s2b_fr["rectangle_semantics"],
        "zone_order": s2b_fr["zone_order"],
        "measurement_semantics": s2b_fr["measurement_semantics"],
        "zone_center_projection_by_reference_distance_mm": deepcopy(s2b_fr["zone_center_projection_by_reference_distance_mm"]),
        "model_derived_rectangle_by_reference_distance_mm": deepcopy(s2b_fr["model_derived_rectangle_by_reference_distance_mm"]),
        "depth_dependence": deepcopy(s2b_fr["depth_dependence"]),
        "runtime_replaced": False,
        "promotion": False,
        "runtime_design_note": "Reference-distance tables are parity fixtures. Planned runtime strategy projects each raw zone's four model boundary rays at that frame's valid axial_z value; no silent 600 mm production approximation.",
    },
    "independent_validation": {
        "status": s2c_summary["status"],
        "decision_semantics": "evidence_ready_not_a_promotion_pass_or_failure",
        "summary": deepcopy(s2c_summary),
        "coverage": deepcopy(s2c_coverage),
        "validation_detail": deepcopy(s2c_validation),
        "frozen_authorities_unchanged": s2c_summary["frozen_authorities_unchanged"],
        "candidate_bytes_unchanged": s2c_summary["candidate_bytes_unchanged"],
        "promotion": False,
        "stage3_started": False,
    },
    "mechanical_and_world_boundary": {
        "camera_tof_rig_must_be_mechanically_fixed_and_glue_cured_before_stage3a": True,
        "world_extrinsics_included": False,
        "pre_remount_stage7c_must_not_be_reused": True,
        "stage3a_started": False,
    },
}

bundle_path = WORKSPACE / "calibration_bundle.stage2_final_candidate.v1.json"
write_json(bundle_path, bundle)
bundle_hash = sha256(bundle_path)
write_text(WORKSPACE / "calibration_bundle.stage2_final_candidate.v1.sha256", f"{bundle_hash}  {bundle_path.name}")

# Consistency checks: hashes, schemas, raw-zone authority, rigid transform, and exact FR regeneration.
checks = []


def check(name: str, ok: bool, detail) -> None:
    checks.append({"name": name, "status": "PASS" if ok else "FAIL", "detail": detail})


expected_hashes = {
    str(S2A): "99e5fe45e56b9f9a5a05f26179a9ee124610c3d4524b4e257b88c7b149979f6b",
    str(S2B): "b518d878be175d42906dd17f9c7e9ead74f33eef77ad11165dc5b2aacd76543e",
    str(S2B_FR): "90c6d7d57f70178231843fbc000d6aedd23ec262837c14df1f62a5f6c762ec9c",
    str(S2C_VALIDATION): "d19a0aef2ab86069897325279dd14c98ccfd4c4e3b455e2afd68348b4a4a8637",
    str(S2C_SUMMARY): "8b7b8d58ecc94f2a303cc5c4b0e2120976e2f8ff99711b27c0c2b0d42bea6568",
    str(S2C_COVERAGE): "3ed91129ef2503955ac55a5950d4dfd05460ef2d93dae1e07fb45631592c4d62",
    str(S2C_FROZEN): "a43538db5d817e7704f99192dadb63ba06d32a5cc1a2bf69fad60680bdbf4e23",
}
for path_s, expected in expected_hashes.items():
    actual = sha256(Path(path_s))
    check(f"authority_hash::{Path(path_s).name}", actual == expected, {"expected": expected, "actual": actual})

check("bundle_json_round_trip", load(bundle_path) == bundle, {"sha256": bundle_hash})
check("all_numeric_values_finite", finite(bundle), "recursive finite-value scan")
check("stage2a_schema", s2a["schema_version"] == "stage2a_camera_intrinsics_candidate.v1", s2a["schema_version"])
check("stage2b_schema", s2b["schema_version"] == "camera_tof.stage2b_all16_candidate.v2", s2b["schema_version"])
check("stage2b_fr_schema", s2b_fr["schema_version"] == "deltar_fr.stage2b_all16_candidate.v2", s2b_fr["schema_version"])
check("stage2c_evidence_status", s2c_summary["status"] == "VALIDATION_EVIDENCE_READY", s2c_summary["status"])
check("no_promotion_or_stage3", not bundle["assembly"]["promotion_executed"] and not bundle["assembly"]["stage3_started"], bundle["assembly"])

zones = s2b["raw_zone_camera_mapping"]
raw_indices = [z["raw_index"] for z in zones]
check("raw_zone_count_and_order", len(zones) == 64 and raw_indices == list(range(64)), {"count": len(zones), "first": raw_indices[:4], "last": raw_indices[-4:]})
check("raw_row_col_consistency", all(z["raw_row"] == z["raw_index"] // 8 and z["raw_col"] == z["raw_index"] % 8 for z in zones), "raw_index = raw_row*8+raw_col")
check("canonical_identity_gauge", s2b["gauge"]["raw_index_for_canonical_index"] == list(range(64)), s2b["gauge"])
check("physical_d4_unresolved", s2b["gauge"]["physical_D4_orientation"] == "UNRESOLVED_GAUGE", s2b["gauge"]["physical_D4_orientation"])
check("axial_z_semantics", s2b["range_semantics"]["selected"] == "axial_z" and s2b_fr["measurement_semantics"] == "axial_z", {"camera_tof": s2b["range_semantics"]["selected"], "fr": s2b_fr["measurement_semantics"]})

R = s2b["T_camera_from_tof"]["R"]
t = s2b["T_camera_from_tof"]["t_mm"]
orth_err = max(abs(sum(R[k][i] * R[k][j] for k in range(3)) - (1.0 if i == j else 0.0)) for i in range(3) for j in range(3))
det = determinant3(R)
check("rotation_is_proper_orthonormal", orth_err < 1e-10 and abs(det - 1.0) < 1e-10, {"max_RtR_error": orth_err, "determinant": det})
check(
    "transform_convention_and_units",
    s2b["T_camera_from_tof"]["direction"] == "T_camera_from_tof"
    and s2b["T_camera_from_tof"]["convention"] == "column_vector: p_camera=R*p_tof+t"
    and s2b["T_camera_from_tof"]["units"] == {"rotation": "dimensionless", "translation": "mm"},
    {
        "direction": s2b["T_camera_from_tof"]["direction"],
        "convention": s2b["T_camera_from_tof"]["convention"],
        "units": s2b["T_camera_from_tof"]["units"],
    },
)

max_ray_norm_error = 0.0
max_center_error = 0.0
max_rect_error = 0.0
for z in zones:
    for key in ("tof_frame_center_ray", "camera_frame_center_ray"):
        max_ray_norm_error = max(max_ray_norm_error, abs(norm(z[key]) - 1.0))
    for ray in z["tof_frame_corner_rays_model_derived"]:
        max_ray_norm_error = max(max_ray_norm_error, abs(norm(ray) - 1.0))
    idx = z["raw_index"]
    for distance_key in ("400", "600", "1000"):
        distance = float(distance_key)
        uv = projected_ray(s2a["camera"], R, t, z["tof_frame_center_ray"], distance)
        expected_uv = s2b_fr["zone_center_projection_by_reference_distance_mm"][distance_key][idx]
        max_center_error = max(max_center_error, max(abs(uv[i] - expected_uv[i]) for i in range(2)))
        corners = [projected_ray(s2a["camera"], R, t, ray, distance) for ray in z["tof_frame_corner_rays_model_derived"]]
        rect = [min(p[1] for p in corners), min(p[0] for p in corners), max(p[1] for p in corners), max(p[0] for p in corners)]
        expected_rect = s2b_fr["model_derived_rectangle_by_reference_distance_mm"][distance_key][idx]
        max_rect_error = max(max_rect_error, max(abs(rect[i] - expected_rect[i]) for i in range(4)))
check("all_model_rays_unit_length", max_ray_norm_error < 1e-12, {"max_norm_error": max_ray_norm_error})
check("fr_center_projection_regeneration", max_center_error < 1e-9, {"max_abs_px_error": max_center_error})
check("fr_rectangle_regeneration", max_rect_error < 1e-9, {"max_abs_px_error": max_rect_error})

for distance_key in ("400", "600", "1000"):
    rects = s2b_fr["model_derived_rectangle_by_reference_distance_mm"][distance_key]
    ok = len(rects) == 64 and all(len(r) == 4 and r[2] > r[0] and r[3] > r[1] for r in rects)
    check(f"fr_shape_and_positive_extents::{distance_key}mm", ok, {"rows": len(rects), "field_order": s2b_fr["field_order"]})

overall = s2c_summary["core_overall"]
check("stage2c_no_catastrophic_core_collapse", overall["p95_abs_mm"] < 30.0 and overall["max_abs_mm"] < 30.0, {"p95_abs_mm": overall["p95_abs_mm"], "max_abs_mm": overall["max_abs_mm"], "interpretation": "diagnostic only; not a promotion gate"})
check("stage2c_comparison_is_not_hard_gate", s2c_validation["stage2b_reference"]["hard_pass_gate"] is False, s2c_validation["stage2b_reference"])

check_status = "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"
consistency = {
    "schema_version": "stage2_final_consistency_checks.v1",
    "generated_at": created_at,
    "status": check_status,
    "check_count": len(checks),
    "failed_count": sum(c["status"] == "FAIL" for c in checks),
    "checks": checks,
}
write_json(WORKSPACE / "CONSISTENCY_CHECKS.json", consistency)
write_text(WORKSPACE / "CONSISTENCY_CHECKS.txt", "\n".join([f"overall={check_status}", f"checks={len(checks)}", f"failed={consistency['failed_count']}"] + [f"{c['status']} {c['name']}: {json.dumps(c['detail'], ensure_ascii=False)}" for c in checks]))

repos = {"pipeline": git_state(PIPE), "official_deltar": git_state(DELTAR), "firmware": git_state(ESP)}
write_json(WORKSPACE / "RUNTIME_REPOSITORY_STATE.json", {"captured_at": created_at, "repositories": repos, "note": "Read-only snapshot; official DELTAR dirty entries pre-existed this assembly."})

intr = s2a["camera"]["intrinsics"]
cam_res = s2a["camera"]["resolution"]
core = s2c_summary["core_overall"]
stage2b_held = s2b["validation_evidence"]["core_scan_heldout"]
bundle_summary = f"""# Stage2 Final Calibration Bundle Summary

Status: `CANDIDATE_NOT_PROMOTED`  
Promotion executed: `false`  
Stage3 started: `false`  
Bundle SHA-256: `{bundle_hash}`

## Authority closure

- Stage2A: `{S2A}` (`{sha256(S2A)}`)
- Stage2B camera↔ToF: `{S2B}` (`{sha256(S2B)}`)
- Stage2B DELTAR `fr`: `{S2B_FR}` (`{sha256(S2B_FR)}`)
- Stage2C status: `{s2c_summary['status']}`; its diagnostic comparison is not a hard promotion gate.

No fitting, refitting, candidate mutation, runtime switch, formal-chain promotion, or Stage3A work was performed.

## Calibration content

- Camera raster: `{cam_res['width']}×{cam_res['height']}`; OpenCV Brown-5.
- Intrinsics: `fx={intr['fx']}`, `fy={intr['fy']}`, `cx={intr['cx']}`, `cy={intr['cy']}`.
- Distortion: `k1={intr['distortion']['k1']}`, `k2={intr['distortion']['k2']}`, `p1={intr['distortion']['p1']}`, `p2={intr['distortion']['p2']}`, `k3={intr['distortion']['k3']}`.
- Stage2A held-out RMS/P95/max: `{s2a['metrics']['heldout_rms_px']:.6f}` / `{s2a['metrics']['heldout_p95_px']:.6f}` / `{s2a['metrics']['heldout_max_px']:.6f}` px.
- Camera-from-ToF convention: `p_camera = R * p_tof + t`, column vectors, millimetres.
- ToF measurement semantics: `axial_z`.
- Raw zone authority: raw index `0..63`, identity canonical gauge; physical D4 orientation remains `UNRESOLVED_GAUGE`.
- `fr`: `[64,4]`, `[sy,sx,ey,ex]`, source-camera pixels, continuous half-open model-derived bounds, reference tables at 400/600/1000 mm.

## Validation reading

- Stage2B core held-out: median `{stage2b_held['median_mm']:.3f}` mm, P95 `{stage2b_held['p95_mm']:.3f}` mm, RMS `{stage2b_held['rms_mm']:.3f}` mm, max `{stage2b_held['max_mm']:.3f}` mm.
- Stage2C independent core: median `{core['median_abs_mm']:.3f}` mm, P95 `{core['p95_abs_mm']:.3f}` mm, RMS `{core['rms_mm']:.3f}` mm, max `{core['max_abs_mm']:.3f}` mm.
- Stage2C is worse than the Stage2B reference on the reported aggregate metrics, but shows no tens-to-hundreds-of-millimetres core geometry collapse. This is evidence for the decision owner, not an automatic pass or failure.

## Mechanical/world boundary

The old Stage7C camera-world pose predates the new vehicle mounting and is inventory only. It is not carried as authority in this bundle and must not be reused. Stage3A may begin only after the camera↔ToF assembly is mechanically fixed, adhesive has cured, and the Stage2 residual trade-off has explicit owner acceptance.
"""
write_text(WORKSPACE / "BUNDLE_SUMMARY.md", bundle_summary)

runtime_matrix = f"""# Runtime Consumer Matrix

This is a read-only inventory and a proposed change map. No row below has been promoted.

| Consumer / boundary | Current authority and behavior | Stage2 bundle field | Planned change | Risk / acceptance evidence |
|---|---|---|---|---|
| Firmware VL53L5CX provider | ULD results emitted as raw indices 0..63; no rotation | `camera_tof.raw_zone_camera_mapping`, identity raw-index authority | Preserve raw order; do not introduce D4 names | Golden raw-index fixture; scan byte parity |
| Firmware capture/transfer | Pairs RGB565 + 64-zone arrays; hard-coded profile IDs still say unverified/identity | Bundle identity + hashes | P4-only profile-ID/schema update after PC acceptance | Upload/capture contract and backward-compat tests |
| PC `zone_orientation_profile.json` | Identity reorder is applied to distance/sigma/mask and `fr` | `gauge.raw_index_for_canonical_index` | Make bundle identity authoritative; fail closed on non-identity mismatch | 64-zone permutation test; no double reorder |
| PC calibration profile | Synthetic pinhole/no distortion; unresolved semantics | Stage2A K/D + Stage2B `axial_z` and transform | New immutable bundle loader/validator; keep existing profile as rollback | Schema/hash validation; legacy-off/on replay |
| `ToFAdapter` | Generates uniform 8×8 source rectangles, then canvas transform | Per-zone rays, R/t, K/D, raw axial depth | Add feature-gated per-frame dynamic projection strategy | Reference 400/600/1000 parity ≤1e-6 px; invalid-zone policy tests |
| Canvas transform | `[sy,sx,ey,ex]`; direct or +60 y letterbox | Stage2B `fr` field order/units | Reuse existing transform after source-camera projection | Bounds/half-open/direct/letterbox tests |
| Official DELTAR forward | Receives `fr` as `[1,64,4]` per inference; computes patch info per call | Dynamic per-frame `fr` | No model architecture change expected; validate loader assumptions | Replay parity, batch/shape/dtype/device tests |
| Base point-cloud Geometry | Uses calibration profile camera model, depth semantics and ground transform | Stage2A K/D; Stage3A future world pose | Stage2 Camera fields can be plumbed first; world switch waits for Stage3A | Point-cloud projection/reprojection regression |
| Stage7D/7E contracts | Hash-pin old Stage7A/B/C paths and IDs | New Stage2A/B plus future Stage3A | Versioned binding manifest; do not overwrite old artifacts | Frozen profile rebuild and golden geometry diffs |
| Competition Shelf Stabilizer | Post-package support/object height adjustment | Downstream only | No calibration-promotion edit; regression target after new world chain | support/object/top-height invariants |

## Current data flow

`VL53L5CX raw[0..63] → capture/transfer unchanged → PC identity raw order → per-zone axial_z → project ToF boundary rays through Stage2B R/t and Stage2A K/D → source-camera fr[64,4] → existing canvas transform → DELTAR fr[1,64,4]`

The old Stage7B label `rotate_90_cw` belongs to an older canonical gauge artifact and is not the current firmware/PC raw-index operation. It must not be reintroduced as physical truth.
"""
write_text(WORKSPACE / "RUNTIME_CONSUMER_MATRIX.md", runtime_matrix)

promotion_plan = f"""# Formal-Chain Promotion Plan — Not Executed

Status: planning only. `promotion_executed=false`; `stage3_started=false`.

## Release rule

Install new immutable, hash-addressed artifacts beside the old chain. Never overwrite Stage7A/B/C in place. Use one versioned binding/config switch for activation and one switch back for rollback. Stage2 Camera/ToF activation and Stage3A world activation are separate decisions.

## P0 — Add bundle contract and dark plumbing

1. Add a JSON schema such as `schemas/stage2_final_calibration_bundle.v1.schema.json` and a strict loader such as `src/deltar_p4_pipeline/stage2_bundle.py`.
2. Add bundle path, expected SHA-256, and independent flags for measured camera intrinsics, raw-zone mapping, and dynamic `fr` to service configuration (`scan_service.py`, `scan_service_worker.py`, `scripts/run_scan_service.py`, plus frozen runtime resource packaging).
3. Keep every flag off. Reject hash/schema/status mismatches, any non-64 zone map, any non-`axial_z` semantics, and any attempt to treat unresolved physical D4 as resolved.
4. Tests: bundle round trip, exact source hashes, K/D numeric parity, R orthonormality/determinant, raw-index bijection, malformed input failures.

## P1 — Promote Stage2A camera fields behind a flag

1. Generate a runtime calibration view from this bundle rather than editing `profiles/calibration_profile.example.json` in place.
2. Plumb measured 640×360 Brown-5 K/D to consumers that actually project points. Verify whether each consumer supports distortion; fail closed where it currently assumes zero distortion.
3. Refactor old Stage7D/7E literal Stage7A hashes/IDs into a versioned binding manifest. Do not touch world pose yet.
4. Acceptance: image/depth raster identity, reprojection fixtures, old-vs-new replay, service restart persistence, rollback switch verified.

## P2 — Promote Stage2B mapping/semantics/transform behind separate flags

1. Preserve firmware/capture raw indices 0..63. Bind `raw_zone_index_to_camera_frame_ray`; do not apply the old `rotate_90_cw` label.
2. Enforce `axial_z`. Add the Stage2B `p_camera = R*p_tof+t` adapter with millimetre units and column vectors.
3. Firmware profile IDs remain unchanged until PC acceptance; when later updated, change manifest IDs/schema only in a compatibility release with old-scan replay.
4. Acceptance: raw-zone marker fixture, identity permutation, per-zone center projection at 400/600/1000 mm, candidate-hash provenance in output manifests.

## P3 — Replace uniform-grid `fr` with feature-gated dynamic projection

1. Add a new `ToFAdapter` strategy. For each raw zone with valid axial depth, scale its four model-derived ToF boundary rays by `z/ray_z`, transform with Stage2B R/t, distort/project with Stage2A K/D, then emit `[min_y,min_x,max_y,max_x]` in source-camera pixels and apply the existing canvas transform.
2. Define one explicit invalid-zone policy and test it. Recommended safe first implementation: use a frame-robust valid axial depth only for invalid zones, emit an invalid-zone mask, and never feed zero/NaN as geometry. Temporal fallback, if adopted, needs reset/age semantics.
3. The 400/600/1000 mm tables are parity fixtures and diagnostics. A static 600 mm table is not acceptable as the formal 400–1000 mm runtime solution.
4. Existing DELTAR accepts per-call `fr`; test `[1,64,4]`, float32, device placement, direct/letterbox canvases, out-of-canvas padding, and variable-depth frames.
5. Ship plumbing with the feature flag off. Enable only after candidate replay, Stage2C replay, synthetic edge cases, and service end-to-end tests pass.

## P4 — Formal binding and downstream regression

1. After P0–P3 acceptance, install a new immutable formal Stage2 binding and update service defaults/profile IDs in one reviewed promotion commit. Record old and new hashes in every scan manifest.
2. Do not bind any camera-world transform from the old Stage7C. Mechanically fix the assembly, wait for adhesive cure, then run Stage3A as a separate authorized task.
3. After Stage3A, generate a new versioned world binding, rebuild Stage7D/7E frozen profiles, and run Geometry and Competition Shelf Stabilizer regressions. Do not tune the stabilizer to mask calibration changes.
4. Acceptance includes cm-level decision thresholds chosen by the owner, spatial/zone bias review, all Stage2C poses, plane/height invariants, support/object/top-height invariants, and rollback rehearsal.

## Decision gates

- Stage2C label `WORSE_THAN_STAGE2B_REFERENCE` is diagnostic, not a hard gate. Owner must explicitly accept or require more data for the observed Stage2C median/P95/RMS `{core['median_abs_mm']:.3f}/{core['p95_abs_mm']:.3f}/{core['rms_mm']:.3f}` mm.
- Mechanical fixation/glue cure is mandatory before Stage3A.
- No world/absolute Geometry promotion occurs until a fresh Stage3A result is available.
"""
write_text(WORKSPACE / "PROMOTION_PLAN.md", promotion_plan)

rollback_plan = f"""# Rollback Plan

No rollback was needed in this task because no promotion occurred. This is the required rehearsal plan for a future promotion.

## Known-good anchors

| Anchor | Path / authority | SHA-256 |
|---|---|---|
| Old formal Stage7A | `{OLD_S7A}` | `{sha256(OLD_S7A)}` |
| Old formal Stage7B | `{OLD_S7B}` | `{sha256(OLD_S7B)}` |
| Old formal Stage7C (inventory only; stale after remount) | `{OLD_S7C}` | `{sha256(OLD_S7C)}` |
| Current synthetic PC calibration profile | `{PIPE / r'profiles\calibration_profile.example.json'}` | `{sha256(PIPE / r'profiles\calibration_profile.example.json')}` |
| Current PC identity orientation profile | `{PIPE / r'profiles\zone_orientation_profile.json'}` | `{sha256(PIPE / r'profiles\zone_orientation_profile.json')}` |
| Current uniform-grid generator source | `{PIPE / r'src\deltar_p4_pipeline\tof_adapter.py'}` | `{sha256(PIPE / r'src\deltar_p4_pipeline\tof_adapter.py')}` |

The old Stage7C is a forensic anchor only. Rolling back Stage2 must not reactivate it as a valid post-remount world pose.

## One-switch rollback

1. Stop accepting new scans at the service boundary; preserve in-flight manifests.
2. Set the versioned runtime binding back to the recorded pre-promotion binding and turn all Stage2 bundle flags off.
3. Restart the service through the normal operational mechanism and verify reported bundle/profile hashes before accepting scans.
4. Run the legacy golden replay and one hardware smoke scan; compare raster, raw 64-zone ordering, DELTAR shapes and Geometry invariants.
5. For Git-managed changes, revert the promotion commit with a reviewed `git revert`; do not reset a dirty worktree. For immutable calibration artifacts, change only the binding pointer—do not delete either version.
6. Quarantine outputs produced under the rejected binding by manifest hash. Never merge them into formal Geometry evidence.

## Trigger conditions

Rollback on hash/schema mismatch, raw-zone permutation mismatch, NaN/non-positive `fr` extents, DELTAR shape/device failure, unexplained spatial/zone bias, cm-level thresholds exceeded, or any provenance manifest reporting the wrong bundle.
"""
write_text(WORKSPACE / "ROLLBACK_PLAN.md", rollback_plan)

stage3_handoff = f"""# Stage3A Handoff

Stage3A has not started. This handoff is intentionally world-parameter-free.

## Preconditions

1. Camera↔ToF bracket and vehicle mounting are in final mechanical configuration.
2. Fasteners are witness-marked; adhesive has completed the manufacturer's cure interval; no subsequent adjustment is planned.
3. Stage2 decision owner has explicitly accepted the Stage2C evidence or requested and completed additional validation.
4. The exact Stage2 bundle SHA is `{bundle_hash}` and every Stage3A capture manifest records it.
5. Old Stage7C `{OLD_S7C}` (`{sha256(OLD_S7C)}`) is blocked from reuse.

## Inputs to Stage3A

- This Stage2 bundle and its SHA file.
- Fixed camera raster/K/D and Camera↔ToF raw-zone/axial-z/transform authority.
- A new board/ground/world capture set collected only after the mechanical preconditions.
- Environmental and fixture measurements with units and uncertainty.

## Required Stage3A outputs

- New immutable camera-world pose with explicit direction, convention, units, covariance/uncertainty and fit/held-out evidence.
- Source scan/pose IDs, file hashes, board/ground definition, time-sync diagnostics and protected-hash proof.
- A versioned formal binding proposal that references this Stage2 bundle SHA; no overwriting of old Stage7C.
- Reprojection, plane, height, multi-pose stability and downstream Geometry acceptance results.

## Stop conditions

Stop and reacquire if the rig moves, cure is incomplete, the bundle hash differs, the camera mode/resolution changes, timestamps violate the gate, or any attempt is made to initialize from/reuse old Stage7C as truth.

## Readiness verdict

`CONDITIONALLY_READY_AFTER_MECHANICAL_FIXATION_AND_GLUE_CURE`: Stage2 evidence is assembled and internally consistent. Entry also requires explicit owner disposition of the Stage2C degradation; this bundle itself does not make that promotion decision.
"""
write_text(WORKSPACE / "STAGE3A_HANDOFF.md", stage3_handoff)

validation_summary = f"""# Validation Summary

## Stage2A

- Train RMS `{s2a['metrics']['train_rms_px']:.6f}` px; held-out RMS `{s2a['metrics']['heldout_rms_px']:.6f}` px; held-out P95 `{s2a['metrics']['heldout_p95_px']:.6f}` px; max `{s2a['metrics']['heldout_max_px']:.6f}` px.
- 28 fit frames and 28 held-out frames. Candidate validation says `{s2a_validation.get('valid')}`.
- Primary-vs-top2 full-image mapping displacement P95/max: `{s2a_stability_displacement['p95']:.6f}` / `{s2a_stability_displacement['max']:.6f}` px.

## Stage2B

- Selected solver: `{s2b['selected_solver_variant']}`; core all-data RMS `{s2b['validation_evidence']['core_all_data']['rms_mm']:.3f}` mm.
- Core scan-held-out median/P95/RMS/max: `{stage2b_held['median_mm']:.3f}` / `{stage2b_held['p95_mm']:.3f}` / `{stage2b_held['rms_mm']:.3f}` / `{stage2b_held['max_mm']:.3f}` mm.
- LOPO angular P95 summary p50/p95/max: `{s2b['stability_evidence_summary']['lopo_angular_p95_deg']['p50']:.4f}` / `{s2b['stability_evidence_summary']['lopo_angular_p95_deg']['p95']:.4f}` / `{s2b['stability_evidence_summary']['lopo_angular_p95_deg']['max']:.4f}` deg.
- Subset angular P95 summary p50/p95/max: `{s2b['stability_evidence_summary']['subset_angular_p95_deg']['p50']:.4f}` / `{s2b['stability_evidence_summary']['subset_angular_p95_deg']['p95']:.4f}` / `{s2b['stability_evidence_summary']['subset_angular_p95_deg']['max']:.4f}` deg.

## Stage2C independent validation

- 4 registered scans; core count `{core['count']}`.
- Median/P90/P95/P99/RMS/max absolute: `{core['median_abs_mm']:.3f}` / `{core['p90_abs_mm']:.3f}` / `{core['p95_abs_mm']:.3f}` / `{core['p99_abs_mm']:.3f}` / `{core['rms_mm']:.3f}` / `{core['max_abs_mm']:.3f}` mm.
- Signed mean `{core['signed_mean_mm']:.3f}` mm; absolute mean `{core['absolute_mean_mm']:.3f}` mm.
- Coverage: near/mid/far `{s2c_coverage['distance']['pose_bins']}`; low/medium/high angle `{s2c_coverage['angle']['pose_bins']}`.
- Novelty: two novel, one novel-relative, one likely repeat. No pose or zone was deleted; no automatic blacklist; frozen authorities and candidate bytes remained unchanged.
- Diagnostic delta versus Stage2B: median `+1.596`, P95 `+3.735`, RMS `+2.019`, max `+5.198` mm. The label is not a hard gate. The data shows cm-scale degradation worth an owner decision, not a 10s/100s-mm collapse.

## Bundle consistency

- `{len(checks)}` checks; `{consistency['failed_count']}` failed; overall `{check_status}`.
- Brown projection regenerated all center and rectangle tables at 400/600/1000 mm with max errors `{max_center_error:.3e}` / `{max_rect_error:.3e}` px.
- Raw-zone count/order, identity canonical gauge, axial-z semantics, transform convention, R orthonormality and authority hashes passed.
"""
write_text(WORKSPACE / "VALIDATION_SUMMARY.md", validation_summary)


def snippet(path: Path, start: int, end: int) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    chosen = [f"{i:04d}: {lines[i-1]}" for i in range(start, min(end, len(lines)) + 1)]
    return f"### `{path}`\n\nSHA-256: `{sha256(path)}`; lines {start}–{min(end, len(lines))}\n\n```text\n" + "\n".join(chosen) + "\n```\n"


snippet_specs = [
    (Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\final_all16_analysis.py"), 474, 488),
    (Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\advanced_diagnostics.py"), 71, 92),
    (Path(r"C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\stage2b_camera_tof.py"), 702, 711),
    (PIPE / r"src\deltar_p4_pipeline\tof_adapter.py", 180, 260),
    (PIPE / r"src\deltar_p4_pipeline\canvas.py", 120, 135),
    (PIPE / r"src\deltar_p4_pipeline\deltar_bridge.py", 350, 375),
    (DELTAR / r"src\utils\dataloader.py", 35, 90),
    (ESP / r"application\edge_agent\components\rehab_tof\src\rehab_tof_provider.c", 490, 510),
    (ESP / r"application\edge_agent\components\rehab_scan_capture\src\rehab_scan_capture_priv.h", 30, 40),
]
snippet_doc = "# Focused Source Audit Snippets\n\nRead-only excerpts supporting the consumer matrix and dynamic-`fr` plan. Line numbers are snapshot-local.\n\n"
for path, start, end in snippet_specs:
    if path.is_file():
        snippet_doc += snippet(path, start, end) + "\n"
write_text(WORKSPACE / "FOCUSED_SOURCE_AUDIT_SNIPPETS.md", snippet_doc)

after = protected_manifest()
changed = [path for path in before if before[path] != after[path]]
protected_check = {
    "schema_version": "stage2_final_protected_hash_check.v1",
    "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "status": "PASS" if not changed else "FAIL",
    "protected_files_unchanged": not changed,
    "changed_paths": changed,
    "before": before,
    "after": after,
}
write_json(WORKSPACE / "protected_hashes.after.json", {"generated_at": protected_check["generated_at"], "files": after})
write_json(WORKSPACE / "PROTECTED_HASH_CHECK.json", protected_check)

report = f"""# Stage2 Final Calibration Bundle + Formal-Chain Promotion Planning Report

`PROMOTION_EXECUTED=false`  
`STAGE3_STARTED=false`  
`RUNTIME_MODIFIED=false`  
`FORMAL_CALIBRATION_MODIFIED=false`  
`BUNDLE_STATUS=CANDIDATE_NOT_PROMOTED`  
`CONSISTENCY_CHECKS={check_status}`  
`PROTECTED_HASH_CHECK={protected_check['status']}`  
`READY_TO_ENTER_STAGE3A_AFTER_MECHANICAL_FIXATION_GLUE_CURE=CONDITIONAL_ON_EXPLICIT_STAGE2C_OWNER_ACCEPTANCE`

## Outcome

The frozen Stage2A/Stage2B calibration authorities and Stage2C independent validation evidence were assembled into one candidate bundle without refitting or mutating any source. The bundle includes all 64 raw-zone mappings and all 400/600/1000 mm DELTAR `fr` center/rectangle tables.

- Bundle: `{bundle_path}`
- Bundle SHA-256: `{bundle_hash}`
- Workspace: `{WORKSPACE}`
- Source authorities: `{WORKSPACE / 'SOURCE_AUTHORITY_MANIFEST.json'}`
- Consistency checks: `{WORKSPACE / 'CONSISTENCY_CHECKS.json'}` (`{len(checks)}` checks, `{consistency['failed_count']}` failures)
- Protected-hash check: `{WORKSPACE / 'PROTECTED_HASH_CHECK.json'}` (`{protected_check['status']}`)

## Decision statement

Stage2C is `VALIDATION_EVIDENCE_READY`. Its `WORSE_THAN_STAGE2B_REFERENCE` comparison is explicitly diagnostic and not a hard pass gate. Independent core residuals are median `{core['median_abs_mm']:.3f}` mm, P95 `{core['p95_abs_mm']:.3f}` mm, RMS `{core['rms_mm']:.3f}` mm and max `{core['max_abs_mm']:.3f}` mm. This rules out a catastrophic tens-to-hundreds-of-millimetres core collapse, but the degradation versus Stage2B held-out evidence remains a cm-scale accuracy decision for the owner.

The evidence package is technically ready to hand to Stage3A after final mechanical fixation and adhesive cure, but only after the owner explicitly accepts the Stage2C residual trade-off or requests more validation. The stale pre-remount Stage7C is not reusable.

## Runtime findings that control promotion

1. Firmware and capture preserve raw ToF indices 0..63. The PC currently applies an identity orientation profile. The old Stage7B `rotate_90_cw` label is not a physical operation in the current chain.
2. The PC adapter currently constructs a synthetic uniform 8×8 `fr`. Official DELTAR receives `[1,64,4]` per inference and can accept a different rectangle tensor each call.
3. Because translation produces depth-dependent parallax, the formal solution must generate per-zone `fr` from valid per-frame axial depth. The 400/600/1000 mm tables are exact parity fixtures; a static 600 mm table is not a production substitute.
4. Base Geometry consumes camera/depth/world fields; Stage7D/7E hard-pin old Stage7A/B/C. Those bindings need versioning, and a fresh Stage3A must precede any new world/absolute Geometry promotion.
5. Competition Shelf Stabilizer is a downstream regression target, not a calibration component to edit.

## Repository state snapshot

- PC pipeline: commit `{repos['pipeline']['commit']}`, tracked status `{repos['pipeline']['status_short'] or 'clean'}`.
- Official DELTAR: commit `{repos['official_deltar']['commit']}`; pre-existing tracked status preserved verbatim in `RUNTIME_REPOSITORY_STATE.json`.
- Firmware: commit `{repos['firmware']['commit']}`, tracked status `{repos['firmware']['status_short'] or 'clean'}`.

## Deliverables

- `BUNDLE_SUMMARY.md`
- `SOURCE_AUTHORITY_MANIFEST.json`
- `RUNTIME_CONSUMER_MATRIX.md`
- `PROMOTION_PLAN.md`
- `ROLLBACK_PLAN.md`
- `STAGE3A_HANDOFF.md`
- `VALIDATION_SUMMARY.md`
- `FOCUSED_SOURCE_AUDIT_SNIPPETS.md`
- `CONSISTENCY_CHECKS.json/.txt`
- `protected_hashes.before.json`, `protected_hashes.after.json`, `PROTECTED_HASH_CHECK.json`
- `RUNTIME_REPOSITORY_STATE.json`
- `WORKSPACE_TREE.txt`
- candidate bundle JSON and SHA file

No build, flash, monitor, reconfiguration, clean, bmgr, promotion, Stage3A/world calibration, runtime switch, formal raw scan modification, or protected-source write was performed.
"""
report_path = REPORT_DIR / "REPORT.md"
write_text(report_path, report)

# Tree is generated before the ZIP and includes the report as an external deliverable.
files = sorted(p for p in WORKSPACE.iterdir() if p.is_file())
tree_lines = [str(WORKSPACE)] + [f"├── {p.name} ({p.stat().st_size} bytes, sha256={sha256(p)})" for p in files]
tree_lines += ["", str(REPORT_DIR), f"├── REPORT.md ({report_path.stat().st_size} bytes, sha256={sha256(report_path)})"]
write_text(WORKSPACE / "WORKSPACE_TREE.txt", "\n".join(tree_lines))

zip_path = REPORT_DIR / "stage2_final_bundle_promotion_plan_20260818_01_evidence.zip"
with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for p in sorted(WORKSPACE.iterdir()):
        if p.is_file() and p.name != Path(__file__).name:
            zf.write(p, arcname=f"stage2_final_bundle_20260818_01/{p.name}")
    zf.write(report_path, arcname="stage2_final_bundle_promotion_plan_20260818_01/REPORT.md")

zip_manifest = {
    "path": str(zip_path),
    "sha256": sha256(zip_path),
    "size_bytes": zip_path.stat().st_size,
    "entries": len(zipfile.ZipFile(zip_path).namelist()),
}
write_json(REPORT_DIR / "EVIDENCE_ZIP_MANIFEST.json", zip_manifest)

if check_status != "PASS" or protected_check["status"] != "PASS":
    raise SystemExit(f"ASSEMBLY_FAILED consistency={check_status} protected={protected_check['status']}")

print(json.dumps({
    "status": "PASS",
    "bundle": str(bundle_path),
    "bundle_sha256": bundle_hash,
    "checks": len(checks),
    "protected_hash_check": protected_check["status"],
    "report": str(report_path),
    "zip": str(zip_path),
    "zip_sha256": zip_manifest["sha256"],
}, indent=2))
