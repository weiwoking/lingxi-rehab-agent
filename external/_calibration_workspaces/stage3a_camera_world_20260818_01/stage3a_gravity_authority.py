from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

import stage3a_oblique_gravity_score as oblique
import stage3a_straightness_score as base


WORKSPACE = Path(__file__).resolve().parent
AUTHORITY_REGISTRY = WORKSPACE / "observations" / "gravity_authority_registry.json"
AUTHORITY_DIAGNOSTICS = WORKSPACE / "diagnostics" / "gravity_authority_execution"
CANDIDATE_PATH = WORKSPACE / "candidate" / "camera_gravity.stage3a_candidate.v1.json"
CANDIDATE_SCHEMA = WORKSPACE / "camera_gravity.stage3a_candidate.v1.schema.json"
VALIDATED_SCAN_ROOT = Path(r"C:\ec\_deltar_scan_service_data\validated")
EXPECTED_STAGE2_SHA256 = base.EXPECTED_STAGE2_SHA256
EXPECTED_MAIN_TOOL_SHA256 = "8aa81cb4e6a0d176050232c3acf01fa91c682c96919c4e9a38fd661c3798b2ef"
EXPECTED_STRAIGHTNESS_SCORER_SHA256 = "f5a1af5d8f9db6cb00101ca5a11cef182ac350ef17e66965e3caf14dc15a40af"
EXPECTED_OBLIQUE_SCORER_SHA256 = "20e4b7113888a05a057f4c3b02a70a707ce5c9ad0e3eb67fd23c664ed9e673cf"
EXPECTED_EXISTING_REGISTRY_SHA256 = "77219b55b4a1716b6bc51c643a84d5a1b20171e3565f323cb443b2245b9f7050"


class GravityAuthorityError(RuntimeError):
    pass


def json_ready(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    return value


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(json_ready(value), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def file_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)).replace("/", "\\"): base.sha256(path)
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def distribution(values: list[float]) -> dict[str, float | int]:
    data = np.asarray(values, dtype=np.float64)
    if data.size == 0:
        return {"count": 0}
    return {
        "count": int(data.size),
        "min": float(np.min(data)),
        "p05": float(np.percentile(data, 5)),
        "median": float(np.median(data)),
        "p95": float(np.percentile(data, 95)),
        "max": float(np.max(data)),
        "rms": float(np.sqrt(np.mean(data**2))),
    }


def robust_unit_vector(vectors: list[np.ndarray], minimum_inliers: int = 3) -> dict[str, Any]:
    if len(vectors) < minimum_inliers:
        raise GravityAuthorityError("insufficient unit vectors for robust aggregation")
    normalized = [oblique.normalize(value) for value in vectors]
    estimate = oblique.vector_mean(normalized)
    inliers = np.ones(len(normalized), dtype=bool)
    threshold = 0.0
    for _ in range(5):
        angles = np.asarray([oblique.angular_distance_deg(value, estimate) for value in normalized])
        active = angles[inliers]
        median = float(np.median(active))
        mad = float(np.median(np.abs(active - median)))
        threshold = max(0.35, median + 3.5 * 1.4826 * max(mad, 1e-6))
        updated = angles <= threshold
        if int(np.sum(updated)) < minimum_inliers:
            keep = np.argsort(angles)[:minimum_inliers]
            updated = np.zeros(len(normalized), dtype=bool)
            updated[keep] = True
        new_estimate = oblique.vector_mean([value for value, keep in zip(normalized, updated) if keep])
        if np.array_equal(updated, inliers) and oblique.angular_distance_deg(new_estimate, estimate) < 1e-10:
            estimate = new_estimate
            inliers = updated
            break
        estimate, inliers = new_estimate, updated
    angles = [oblique.angular_distance_deg(value, estimate) for value in normalized]
    return {
        "vector": estimate,
        "inlier_indices": [index for index, keep in enumerate(inliers) if keep],
        "outlier_indices": [index for index, keep in enumerate(inliers) if not keep],
        "threshold_deg": threshold,
        "angular_residual_deg": distribution(angles),
    }


def gravity_extraction_ready(
    valid_frame_count: int,
    robust_inlier_count: int,
    frame_spread_p95_deg: float,
    solver_disagreement_deg: float,
    branch_catastrophe_count: int,
) -> bool:
    return bool(
        valid_frame_count >= 10
        and robust_inlier_count >= 9
        and frame_spread_p95_deg <= 1.5
        and solver_disagreement_deg <= 0.75
        and branch_catastrophe_count == 0
    )


def default_registry() -> dict[str, Any]:
    return {
        "schema_version": "stage3a_gravity_authority_registry.v1",
        "single_w04g_only": True,
        "silent_replace_forbidden": True,
        "latest_resolution_explicit_only": True,
        "board_mode": "FIXED_VERTICAL_BOARD",
        "w04g": None,
        "real_gravity_candidate_exists": False,
        "promotion": False,
    }


class GravityAuthorityTool:
    def __init__(
        self,
        workspace: Path = WORKSPACE,
        stage2_bundle: Path = base.STAGE2_BUNDLE,
        validated_scan_root: Path = VALIDATED_SCAN_ROOT,
        scan_inspector: Callable[[Path], dict[str, Any]] | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.stage2_bundle = Path(stage2_bundle)
        self.validated_scan_root = Path(validated_scan_root)
        self.existing_registry_path = self.workspace / "observations" / "registry.json"
        self.registry_path = self.workspace / "observations" / "gravity_authority_registry.json"
        self.diagnostics = self.workspace / "diagnostics" / "gravity_authority_execution"
        self.candidate_path = self.workspace / "candidate" / "camera_gravity.stage3a_candidate.v1.json"
        self.schema_path = self.workspace / "camera_gravity.stage3a_candidate.v1.schema.json"
        self._scan_inspector = scan_inspector

    def load_registry(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return default_registry()
        value = base.read_json(self.registry_path)
        if value.get("single_w04g_only") is not True or value.get("board_mode") != "FIXED_VERTICAL_BOARD":
            raise GravityAuthorityError("invalid gravity-authority registry")
        return value

    def status(self) -> dict[str, Any]:
        registry = self.load_registry()
        return {
            "schema_version": "stage3a_gravity_authority_status.v1",
            "real_w04g_registered": registry["w04g"] is not None,
            "real_gravity_candidate_exists": self.candidate_path.exists(),
            "w04g": registry["w04g"],
            "existing_pose_ids": list(base.read_json(self.existing_registry_path)["poses"]),
            "existing_registry_sha256": base.sha256(self.existing_registry_path),
            "stage2_sha256": base.sha256(self.stage2_bundle),
            "formal_runtime_modified": False,
            "promotion": False,
        }

    def resolve_latest(self) -> Path:
        scans = sorted(
            (path for path in self.validated_scan_root.glob("scan_*") if path.is_dir()),
            key=lambda path: path.stat().st_mtime_ns,
        )
        if not scans:
            raise GravityAuthorityError("no validated scan is available")
        return scans[-1].resolve()

    def validate_scan_structure(self, scan: Path) -> list[Path]:
        scan = scan.resolve()
        if not scan.is_dir() or not (scan / "scan.json").is_file():
            raise GravityAuthorityError(f"not a validated scan directory: {scan}")
        frame_dirs = sorted((scan / "frames").glob("frame_*"))
        if len(frame_dirs) != 11:
            raise GravityAuthorityError(f"W04G must contain exactly 11 frames; found {len(frame_dirs)}")
        required = ("frame.json", "image.rgb565", "tof.json")
        for frame_dir in frame_dirs:
            missing = [name for name in required if not (frame_dir / name).is_file()]
            if missing:
                raise GravityAuthorityError(f"{frame_dir.name} is missing: {', '.join(missing)}")
        return frame_dirs

    def inspect_scan(self, scan: Path) -> dict[str, Any]:
        if self._scan_inspector is not None:
            return self._scan_inspector(scan)
        frame_dirs = self.validate_scan_structure(scan)
        K, D, _ = base.nominal_camera_model()
        frames, total = base.detect_scan(scan)
        aggregate = oblique.aggregate_analysis(frames, K, D, total, include_lopo=True)
        per_frame = []
        for frame in frames:
            geometry = base.frame_geometry(frame, K, D)
            outline = np.asarray(geometry["outer_quadrilateral_px"], dtype=np.float32)
            frame_score = oblique.aggregate_analysis([frame], K, D, 1, include_lopo=False)
            analysis = oblique.frame_analysis(frame, K, D)
            per_frame.append(
                {
                    "frame": frame.name,
                    "corner_count": len(frame.ids),
                    "marker_count": frame.marker_count,
                    "board_coverage_fraction": float(abs(cv2.contourArea(outline)) / (base.WIDTH * base.HEIGHT)),
                    "minimum_margin_px": analysis["shape"]["minimum_margin_px"],
                    "minimum_outer_edge_px": analysis["shape"]["minimum_outer_edge_px"],
                    "obliquity_proxy": analysis["shape"]["obliquity_proxy"],
                    "preview_decision": frame_score["decision"],
                    "core_solver_disagreement_deg": analysis["core_solver_disagreement_deg"],
                    "methods": analysis["methods"],
                    "ippe_branch": analysis["ippe_branch"],
                }
            )
        good_fraction = float(np.mean([row["preview_decision"] == "GOOD" for row in per_frame])) if per_frame else 0.0
        return {
            "schema_version": "stage3a_w04g_inspection.v1",
            "scan_id": scan.name,
            "scan_path": str(scan.resolve()),
            "frame_directory_count": len(frame_dirs),
            "valid_frame_count": len(frames),
            "good_frame_fraction": good_fraction,
            "scan_score": aggregate,
            "per_frame": per_frame,
            "preview_good_is_authority_ready": False,
            "scan_good_plus_consensus_may_be_authority_ready": True,
            "capture_or_registration_performed": False,
        }

    def _existing_scan_paths(self) -> set[Path]:
        registry = base.read_json(self.existing_registry_path)
        return {Path(binding["scan_path"]).resolve() for binding in registry["poses"].values()}

    def register_w04g(self, scan: Path) -> dict[str, Any]:
        registry = self.load_registry()
        if registry["w04g"] is not None:
            raise GravityAuthorityError("W04G is already registered; duplicate/overwrite refused")
        scan = scan.resolve()
        self.validate_scan_structure(scan)
        if scan in self._existing_scan_paths():
            raise GravityAuthorityError("refusing to reuse W01/W02/W03 as W04G")
        inspection = self.inspect_scan(scan)
        score = inspection["scan_score"]
        gravity_eligible = bool(
            score["decision"] == "GOOD"
            and inspection["valid_frame_count"] == 11
            and inspection["good_frame_fraction"] >= 0.8
        )
        binding = {
            "pose_id": "W04G",
            "pose_role": "OBLIQUE_GRAVITY_AUTHORITY_CANDIDATE",
            "scan_id": scan.name,
            "scan_path": str(scan),
            "board_mode": "FIXED_VERTICAL_BOARD",
            "gravity_only": True,
            "full_world_rotation_authority": False,
            "gravity_eligible": gravity_eligible,
            "scorer_decision": score["decision"],
            "scorer_result": score,
            "source_hashes": file_hashes(scan),
            "frame_count": 11,
            "extraction": None,
            "cross_distance_validation": None,
            "candidate": None,
        }
        registry["w04g"] = binding
        atomic_write_json(self.registry_path, registry)
        return {
            "status": "W04G_REGISTERED_ELIGIBLE" if gravity_eligible else "W04G_REGISTERED_DIAGNOSTIC_ONLY",
            "gravity_eligible": gravity_eligible,
            "scorer_decision": score["decision"],
            "scan_id": scan.name,
            "raw_scan_modified": False,
        }

    def _binding(self) -> tuple[dict[str, Any], dict[str, Any]]:
        registry = self.load_registry()
        if registry["w04g"] is None:
            raise GravityAuthorityError("no W04G is registered")
        return registry, registry["w04g"]

    def verify_sources(self, binding: dict[str, Any]) -> None:
        scan = Path(binding["scan_path"])
        current = file_hashes(scan)
        if current != binding["source_hashes"]:
            raise GravityAuthorityError("W04G source hash mismatch; fail closed")

    def inspect_registered(self) -> dict[str, Any]:
        _, binding = self._binding()
        self.verify_sources(binding)
        result = self.inspect_scan(Path(binding["scan_path"]))
        result["registered_gravity_eligible"] = binding["gravity_eligible"]
        result["registered_source_hashes_verified"] = True
        return result

    def extract_gravity(self) -> dict[str, Any]:
        registry, binding = self._binding()
        self.verify_sources(binding)
        if not binding["gravity_eligible"] or binding["scorer_decision"] != "GOOD":
            raise GravityAuthorityError("registered W04G is not GOOD/gravity-eligible; extraction refused")
        scan = Path(binding["scan_path"])
        inspection = self.inspect_scan(scan)
        if inspection["scan_score"]["decision"] != "GOOD" or inspection["valid_frame_count"] < 10:
            raise GravityAuthorityError("current W04G inspection is not gravity-ready")
        K, D, _ = base.nominal_camera_model()
        frames, _ = base.detect_scan(scan)
        frame_vectors = []
        per_frame = []
        method_vectors: dict[str, list[np.ndarray]] = {name: [] for name in oblique.CORE_METHODS}
        branch_catastrophes = 0
        for frame in frames:
            solved = oblique.solve_frame(frame, K, D)
            selected = {name: oblique.selected_solution(solved[name]) for name in oblique.CORE_METHODS}
            if any(item is None for item in selected.values()):
                raise GravityAuthorityError(f"core solver missing for {frame.name}")
            vectors = [selected[name]["gravity"] for name in oblique.CORE_METHODS]
            consensus = robust_unit_vector(vectors, minimum_inliers=2)
            frame_vectors.append(consensus["vector"])
            for name in oblique.CORE_METHODS:
                method_vectors[name].append(selected[name]["gravity"])
            ippe = solved["IPPE"]
            gap = None
            separation = None
            if len(ippe) >= 2:
                gap = float(ippe[1]["rms_px"] - ippe[0]["rms_px"])
                separation = oblique.angular_distance_deg(ippe[0]["gravity"], ippe[1]["gravity"])
                if gap < 0.05 and separation > 1.0:
                    branch_catastrophes += 1
            else:
                branch_catastrophes += 1
            per_frame.append(
                {
                    "frame": frame.name,
                    "u_up_camera": consensus["vector"].tolist(),
                    "core_solver_angular_residual_deg": consensus["angular_residual_deg"],
                    "ippe_reprojection_gap_px": gap,
                    "ippe_branch_gravity_separation_deg": separation,
                }
            )
        aggregate = robust_unit_vector(frame_vectors, minimum_inliers=8)
        up = aggregate["vector"]
        frame_spread = distribution([oblique.angular_distance_deg(value, up) for value in frame_vectors])
        method_aggregate = {name: oblique.vector_mean(values) for name, values in method_vectors.items()}
        method_values = list(method_aggregate.values())
        solver_disagreement = max(
            oblique.angular_distance_deg(first, second)
            for index, first in enumerate(method_values) for second in method_values[index + 1 :]
        )
        lopo = inspection["scan_score"]["solver"]["lopo_gravity_change_from_nominal_deg"]
        uncertainty = max(float(frame_spread["p95"]), solver_disagreement, float(lopo.get("p95", 0.0)))
        ready = gravity_extraction_ready(
            len(frames),
            len(aggregate["inlier_indices"]),
            float(frame_spread["p95"]),
            solver_disagreement,
            branch_catastrophes,
        )
        result = {
            "schema_version": "stage3a_camera_gravity_extraction.v1",
            "status": "GRAVITY_READY_FOR_CROSS_DISTANCE_VALIDATION" if ready else "GRAVITY_ROUTE_FAILED",
            "gravity_authority": {
                "u_up_camera": up.tolist(),
                "uncertainty_deg": uncertainty,
                "frame_spread_deg": frame_spread,
                "solver_disagreement_deg": solver_disagreement,
                "stage2_lopo_sensitivity_deg": lopo,
                "source_pose": "W04G",
                "source_scan_id": binding["scan_id"],
                "vector_first": True,
                "matrix_first": False,
                "full_R_authority": False,
                "full_world_rotation_authority": False,
            },
            "robust_frame_aggregation": {
                "inlier_indices": aggregate["inlier_indices"],
                "outlier_indices": aggregate["outlier_indices"],
                "threshold_deg": aggregate["threshold_deg"],
            },
            "method_aggregate_u_up_camera": {name: value.tolist() for name, value in method_aggregate.items()},
            "branch_catastrophe_count": branch_catastrophes,
            "per_frame": per_frame,
            "yaw_status": "SEPARATE_LOW_CONFIDENCE",
            "no_second_oblique_capture_recommended": not ready,
            "promotion": False,
        }
        output = self.diagnostics / "W04G_GRAVITY_EXTRACTION.json"
        atomic_write_json(output, result)
        binding["extraction"] = {"path": str(output), "sha256": base.sha256(output), "status": result["status"]}
        registry["w04g"] = binding
        atomic_write_json(self.registry_path, registry)
        return result

    def _load_extraction(self, binding: dict[str, Any]) -> dict[str, Any]:
        ref = binding.get("extraction")
        if not ref or ref.get("status") != "GRAVITY_READY_FOR_CROSS_DISTANCE_VALIDATION":
            raise GravityAuthorityError("gravity extraction is not ready")
        path = Path(ref["path"])
        if not path.is_file() or base.sha256(path) != ref["sha256"]:
            raise GravityAuthorityError("gravity extraction evidence hash mismatch")
        return base.read_json(path)

    def validate_cross_distance(self) -> dict[str, Any]:
        registry, binding = self._binding()
        self.verify_sources(binding)
        extraction = self._load_extraction(binding)
        up = np.asarray(extraction["gravity_authority"]["u_up_camera"], dtype=np.float64)
        K, D, _ = base.nominal_camera_model()
        detected, existing_registry = base.collect_existing()
        per_pose: dict[str, Any] = {}
        distances = []
        pose_residuals = []
        all_abs_bottom = []
        all_reprojection = []
        fits_by_pose: dict[str, list[dict[str, Any]]] = {}
        for pose in base.POSE_IDS:
            frame_rows = []
            fits = []
            for frame in detected[pose]:
                fit = oblique.gravity_fixed_fit(frame.object_points, frame.image_points, K, D, up)
                validation = oblique.validate_height(fit, up, camera_height_mm=972.0)
                plane_distance = abs(float(fit["R_cb"][:, 2] @ fit["tvec_m"])) * 1000.0
                frame_rows.append(
                    {
                        "frame": frame.name,
                        "plane_distance_mm": plane_distance,
                        "reprojection_rms_px": fit["reprojection_rms_px"],
                        **validation,
                    }
                )
                fits.append(fit)
                all_abs_bottom.append(abs(validation["bottom_signed_residual_mm"]))
                all_reprojection.append(fit["reprojection_rms_px"])
            fits_by_pose[pose] = fits
            distance = float(np.median([row["plane_distance_mm"] for row in frame_rows]))
            bottom = [row["bottom_signed_residual_mm"] for row in frame_rows]
            top = [row["top_signed_residual_mm"] for row in frame_rows]
            residual = float(np.median(bottom))
            distances.append(distance / 1000.0)
            pose_residuals.append(residual)
            per_pose[pose] = {
                "scan_id": existing_registry["poses"][pose]["scan_id"],
                "distance_mm": distance,
                "bottom_signed_residual_mm": distribution(bottom),
                "bottom_absolute_residual_mm": distribution([abs(value) for value in bottom]),
                "top_signed_residual_mm": distribution(top),
                "top_absolute_residual_mm": distribution([abs(value) for value in top]),
                "reprojection_rms_px": distribution([row["reprojection_rms_px"] for row in frame_rows]),
                "frames": frame_rows,
            }
        slope = float(np.polyfit(distances, pose_residuals, 1)[0])
        baseline_path = self.workspace / "diagnostics" / "distance_drift_20260818" / "W01_W02_W03_POSE_COMPARISON.json"
        baseline = base.read_json(baseline_path)["current_iterative"]["fixed_height_using_aggregate_R"]
        baseline_slope = float(baseline["slope_mm_per_m"])
        reduction_fraction = 1.0 - abs(slope) / max(1e-12, abs(baseline_slope))
        abs_summary = distribution(all_abs_bottom)
        reprojection_summary = distribution(all_reprojection)
        strong_slope_pass = abs(slope) <= 15.0 and reduction_fraction >= 0.65
        residual_preferred = abs_summary["median"] <= 20.0 and abs_summary["p95"] <= 30.0
        if strong_slope_pass and residual_preferred:
            status = "READY_FOR_STAGE3B_Z_VALIDATION"
        elif abs(slope) <= 25.0 and reduction_fraction >= 0.50:
            status = "NEEDS_OWNER_JUDGMENT"
        else:
            status = "GRAVITY_ROUTE_FAILED"
        height_sensitivity = {}
        for camera_height in (962.0, 972.0, 982.0):
            medians = []
            for pose in base.POSE_IDS:
                values = []
                for fit in fits_by_pose[pose]:
                    values.append(oblique.validate_height(fit, up, camera_height)["bottom_signed_residual_mm"])
                medians.append(float(np.median(values)))
            height_sensitivity[str(int(camera_height))] = {
                "per_pose_median_bottom_residual_mm": dict(zip(base.POSE_IDS, medians)),
                "slope_mm_per_m": float(np.polyfit(distances, medians, 1)[0]),
            }
        result = {
            "schema_version": "stage3a_gravity_fixed_cross_distance_validation.v1",
            "status": status,
            "gravity_source_pose": "W04G",
            "yaw_status": "SEPARATE_LOW_CONFIDENCE",
            "fit_layer": {
                "fixed": ["Stage2 K/D", "u_up_camera", "board metric geometry"],
                "free_per_frame": ["relative_yaw_nuisance", "translation_x", "translation_y", "translation_z"],
                "uses_camera_height_or_board_world_height": False,
            },
            "validation_layer": {
                "camera_height_mm": 972.0,
                "camera_height_uncertainty_mm": 10.0,
                "board_active_bottom_mm": 912.0,
                "board_active_top_mm": 1037.0,
                "board_height_uncertainty_mm": 5.0,
            },
            "per_pose": per_pose,
            "bottom_residual_summary_mm": abs_summary,
            "reprojection_rms_px": reprojection_summary,
            "residual_vs_distance_slope_mm_per_m": slope,
            "baseline_slope_mm_per_m": baseline_slope,
            "slope_reduction_fraction": reduction_fraction,
            "camera_height_sensitivity": height_sensitivity,
            "camera_height_used_to_change_slope_decision": False,
            "no_second_oblique_capture_recommended": status == "GRAVITY_ROUTE_FAILED",
            "candidate_export_allowed": status == "READY_FOR_STAGE3B_Z_VALIDATION",
            "promotion": False,
        }
        output = self.diagnostics / "W01_W02_W03_GRAVITY_FIXED_VALIDATION.json"
        atomic_write_json(output, result)
        binding["cross_distance_validation"] = {"path": str(output), "sha256": base.sha256(output), "status": status}
        registry["w04g"] = binding
        atomic_write_json(self.registry_path, registry)
        return result

    def _load_validation(self, binding: dict[str, Any]) -> dict[str, Any]:
        ref = binding.get("cross_distance_validation")
        if not ref:
            raise GravityAuthorityError("cross-distance validation is absent")
        path = Path(ref["path"])
        if not path.is_file() or base.sha256(path) != ref["sha256"]:
            raise GravityAuthorityError("cross-distance validation evidence hash mismatch")
        return base.read_json(path)

    def export_gravity_candidate(self) -> dict[str, Any]:
        registry, binding = self._binding()
        self.verify_sources(binding)
        extraction = self._load_extraction(binding)
        validation = self._load_validation(binding)
        if validation["status"] != "READY_FOR_STAGE3B_Z_VALIDATION" or not validation["candidate_export_allowed"]:
            raise GravityAuthorityError("cross-distance validation does not permit candidate export")
        if self.candidate_path.exists():
            raise GravityAuthorityError("gravity candidate already exists; overwrite refused")
        candidate = {
            "schema_version": "camera_gravity.stage3a_candidate.v1",
            "status": "CANDIDATE_NOT_PROMOTED",
            "gravity_status": "READY_FOR_STAGE3B_Z_VALIDATION",
            "yaw_status": "SEPARATE_LOW_CONFIDENCE",
            "gravity_authority": {
                "u_up_camera": extraction["gravity_authority"]["u_up_camera"],
                "uncertainty_deg": extraction["gravity_authority"]["uncertainty_deg"],
                "source_pose_id": "W04G",
                "source_scan_id": binding["scan_id"],
                "vector_first": True,
                "matrix_first": False,
                "full_world_rotation_authority": False,
            },
            "camera_height_mm": 972.0,
            "camera_height_uncertainty_mm": 10.0,
            "fixed_board_active_bottom_mm": 912.0,
            "fixed_board_active_top_mm": 1037.0,
            "fixed_board_uncertainty_mm": 5.0,
            "W01_W02_W03_cross_distance_validation": validation,
            "provenance": {
                "stage2_bundle_path": str(self.stage2_bundle),
                "stage2_bundle_sha256": base.sha256(self.stage2_bundle),
                "source_scan_path": binding["scan_path"],
                "source_hashes": binding["source_hashes"],
                "gravity_authority_tool_sha256": base.sha256(Path(__file__)),
                "oblique_scorer_sha256": base.sha256(WORKSPACE / "stage3a_oblique_gravity_score.py"),
                "existing_registry_sha256": base.sha256(self.existing_registry_path),
            },
            "no_promotion": True,
            "promotion": False,
        }
        self.validate_candidate_schema(candidate)
        atomic_write_json(self.candidate_path, candidate)
        binding["candidate"] = {"path": str(self.candidate_path), "sha256": base.sha256(self.candidate_path)}
        registry["w04g"] = binding
        registry["real_gravity_candidate_exists"] = True
        atomic_write_json(self.registry_path, registry)
        return candidate

    def validate_candidate_schema(self, candidate: dict[str, Any]) -> None:
        required = {
            "schema_version", "status", "gravity_status", "yaw_status", "gravity_authority",
            "camera_height_mm", "camera_height_uncertainty_mm", "fixed_board_active_bottom_mm",
            "fixed_board_uncertainty_mm", "W01_W02_W03_cross_distance_validation", "provenance",
            "no_promotion", "promotion",
        }
        missing = sorted(required - set(candidate))
        if missing:
            raise GravityAuthorityError(f"candidate missing fields: {missing}")
        gravity = candidate["gravity_authority"]
        if gravity.get("vector_first") is not True or gravity.get("full_world_rotation_authority") is not False:
            raise GravityAuthorityError("candidate violates vector-only authority contract")
        if candidate["yaw_status"] != "SEPARATE_LOW_CONFIDENCE" or candidate["promotion"] is not False:
            raise GravityAuthorityError("candidate yaw/promotion contract invalid")
        if len(gravity.get("u_up_camera", [])) != 3 or abs(np.linalg.norm(gravity["u_up_camera"]) - 1.0) > 1e-6:
            raise GravityAuthorityError("candidate gravity vector is not unit length")

    def summary(self) -> dict[str, Any]:
        registry = self.load_registry()
        binding = registry["w04g"]
        if binding is None:
            return {
                "status": "READY_FOR_ONE_W04G_CAPTURE",
                "next": "Use oblique scorer on preview; capture only when GOOD.",
                "real_w04g_registered": False,
                "real_gravity_candidate_exists": False,
            }
        validation_status = (binding.get("cross_distance_validation") or {}).get("status")
        extraction_status = (binding.get("extraction") or {}).get("status")
        if validation_status == "READY_FOR_STAGE3B_Z_VALIDATION":
            return {
                "status": "READY_FOR_STAGE3B_Z_VALIDATION",
                "gravity_candidate": str(self.candidate_path) if self.candidate_path.exists() else "NOT_EXPORTED",
                "yaw": "SEPARATE_LOW_CONFIDENCE",
            }
        if validation_status == "NEEDS_OWNER_JUDGMENT":
            return {"status": "NEEDS_OWNER_JUDGMENT", "next": "Review cross-distance evidence; do not capture another W04G."}
        if (
            not binding["gravity_eligible"]
            or extraction_status == "GRAVITY_ROUTE_FAILED"
            or validation_status == "GRAVITY_ROUTE_FAILED"
        ):
            return {
                "status": "GRAVITY_ROUTE_FAILED",
                "next": "Do not capture a second oblique pose; use external gravity reference.",
                "no_second_oblique_capture_recommended": True,
            }
        if extraction_status != "GRAVITY_READY_FOR_CROSS_DISTANCE_VALIDATION":
            return {"status": "W04G_REGISTERED_NEEDS_GRAVITY_EXTRACTION", "next": "Run extract-gravity."}
        return {"status": "NEEDS_CROSS_DISTANCE_VALIDATION", "next": "Run validate-cross-distance."}


def print_summary(value: dict[str, Any]) -> None:
    print(f"status = {value['status']}")
    if "next" in value:
        print(f"next = {value['next']}")
    if "gravity_candidate" in value:
        print(f"gravity candidate = {value['gravity_candidate']}")
    if "yaw" in value:
        print(f"yaw = {value['yaw']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage3A W04G gravity-authority execution tool (no capture)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show provenance state without writing")
    register = sub.add_parser("register-w04g", help="explicitly bind one existing 11-frame scan")
    source = register.add_mutually_exclusive_group(required=True)
    source.add_argument("--scan", type=Path)
    source.add_argument("--latest", action="store_true", help="explicitly select newest validated scan")
    sub.add_parser("inspect-w04g", help="reinspect registered W04G and verify hashes")
    sub.add_parser("extract-gravity", help="extract vector-only gravity authority")
    sub.add_parser("validate-cross-distance", help="gravity-fixed W01/W02/W03 validation")
    sub.add_parser("summary", help="show the next human action")
    sub.add_parser("export-gravity-candidate", help="export only after real validation PASS")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tool = GravityAuthorityTool()
    try:
        if args.command == "status":
            result = tool.status()
        elif args.command == "register-w04g":
            scan = tool.resolve_latest() if args.latest else args.scan.resolve()
            result = tool.register_w04g(scan)
        elif args.command == "inspect-w04g":
            result = tool.inspect_registered()
        elif args.command == "extract-gravity":
            result = tool.extract_gravity()
        elif args.command == "validate-cross-distance":
            result = tool.validate_cross_distance()
        elif args.command == "export-gravity-candidate":
            result = tool.export_gravity_candidate()
        else:
            print_summary(tool.summary())
            return 0
    except GravityAuthorityError as error:
        print("status = REFUSED")
        print(f"reason = {error}")
        return 2
    print(json.dumps(json_ready(result), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
