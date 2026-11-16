from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .camera_tof_calibration import (
    EXPECTED_TX_MM,
    PROPER_D4,
    PlaneObservation,
    nominal_zone_rays,
    points_from_distances,
    residual_and_jacobian,
    residual_summary,
    transform_points,
)
from .camera_tof_contracts import canonical_sha256, write_json
from .zone_orientation import canonical_values_from_raw, raw_index_for_canonical_index

REQUIRED_TRAINING_POSES = 8
REQUIRED_HELDOUT_POSES = 4


def select_candidate(
    ranking: list[dict[str, Any]], observations: list[PlaneObservation],
) -> tuple[dict[str, Any] | None, dict[str, Any], list[dict[str, Any]], list[str]]:
    gates: list[dict[str, Any]] = []
    warnings: list[str] = []
    successes = [item for item in ranking if item["status"] == "success"]
    if not successes:
        gates.append({"name": "solver_success", "status": "FAIL", "details": "no successful candidate"})
        return None, {"absolute_mm": 0.0, "relative": 0.0}, gates, warnings
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in successes:
        key = (item["orientation_class"], item["distance_semantics"])
        current = grouped.get(key)
        if current is None or item["heldout"]["rms_mm"] < current["heldout"]["rms_mm"]:
            grouped[key] = item
    group_best = min(grouped.values(), key=lambda item: item["heldout"]["rms_mm"])
    selected_class = group_best["orientation_class"]
    selected_semantics = group_best["distance_semantics"]
    gauge = [
        item for item in successes
        if item["orientation_class"] == selected_class
        and item["distance_semantics"] == selected_semantics
        and item["heldout"]["rms_mm"] <= group_best["heldout"]["rms_mm"] * 1.02 + 0.25
    ]
    # Residual-equivalent rotations are a representation gauge. Canonicalize
    # to the most mechanically parallel/right-baseline representation in
    # Camera coordinates.  A ToF physically on the observer's right is on the
    # Camera's -X side because the Camera faces the observer.
    best = min(gauge, key=lambda item: (
        not item["mechanical"]["tx_sign_matches_expected"],
        item["mechanical"]["rotation_angle_deg"],
        item["mechanical"]["tx_error_from_expected_mm"],
        item["heldout"]["rms_mm"],
    ))
    alternatives = [
        item for key, item in grouped.items()
        if key != (selected_class, selected_semantics)
    ]
    next_group = min(alternatives, key=lambda item: item["heldout"]["rms_mm"]) if alternatives else None
    absolute_margin = 0.0 if next_group is None else next_group["heldout"]["rms_mm"] - group_best["heldout"]["rms_mm"]
    relative_margin = 0.0 if next_group is None else absolute_margin / max(group_best["heldout"]["rms_mm"], 1e-9)
    opposite_class = "reflection" if selected_class == "proper_rotation_gauge" else "proper_rotation_gauge"
    parity_alt = grouped.get((opposite_class, selected_semantics))
    semantic_alt = grouped.get((selected_class, "radial_range" if selected_semantics == "axial_z" else "axial_z"))
    train_poses = [item for item in observations if item.split == "train"]
    heldout_poses = [item for item in observations if item.split == "heldout"]
    train_normals = np.stack([item.normal_camera for item in train_poses]) if train_poses else np.empty((0, 3))
    held_normals = np.stack([item.normal_camera for item in heldout_poses]) if heldout_poses else np.empty((0, 3))
    held_distances = np.asarray([abs(item.offset_camera_mm) for item in heldout_poses])
    gates.extend([
        {"name": "solver_success", "status": "PASS", "details": f"{len(successes)} candidates succeeded"},
        {"name": "train_pose_count", "status": "PASS" if len(train_poses) >= REQUIRED_TRAINING_POSES else "FAIL", "details": len(train_poses)},
        {"name": "heldout_pose_count", "status": "PASS" if len(heldout_poses) >= REQUIRED_HELDOUT_POSES else "FAIL", "details": len(heldout_poses)},
        {"name": "train_normal_rank", "status": "PASS" if len(train_poses) >= 3 and np.linalg.matrix_rank(train_normals, tol=0.03) >= 3 else "FAIL",
         "details": 0 if not len(train_poses) else int(np.linalg.matrix_rank(train_normals, tol=0.03))},
        {"name": "heldout_normal_diversity", "status": "PASS" if len(heldout_poses) >= 2 and np.linalg.matrix_rank(held_normals, tol=0.03) >= 2 else "FAIL",
         "details": 0 if not len(heldout_poses) else int(np.linalg.matrix_rank(held_normals, tol=0.03))},
        {"name": "heldout_distance_coverage",
         "status": "PASS" if len(held_distances) >= 2 and np.ptp(held_distances) >= 50.0 else "FAIL",
         "details": {
             "min_mm": None if not len(held_distances) else float(np.min(held_distances)),
             "max_mm": None if not len(held_distances) else float(np.max(held_distances)),
             "span_mm": 0.0 if len(held_distances) < 2 else float(np.ptp(held_distances)),
         }},
        {"name": "heldout_residual", "status": "PASS" if best["heldout"]["rms_mm"] <= max(15.0, 3.0 * best["train"]["rms_mm"]) else "FAIL",
         "details": best["heldout"]},
        {"name": "multi_start_stability",
         "status": "PASS" if best["stability"]["successful_starts"] >= 2
         and best["stability"]["rotation_spread_deg"] < 1.0
         and best["stability"]["translation_spread_mm"] < 5.0 else "FAIL",
         "details": best["stability"]},
        {"name": "rank_condition", "status": "PASS" if best["rank"] == 6 and best["condition_number"] < 1e9 else "FAIL",
         "details": {"rank": best["rank"], "condition_number": best["condition_number"]}},
        {"name": "mechanical_plausibility",
         "status": "PASS" if best["mechanical"]["tx_sign_matches_expected"]
         and best["mechanical"]["translation_norm_mm"] < 150.0
         and best["mechanical"]["rotation_angle_deg"] < 45.0 else "FAIL",
         "details": best["mechanical"]},
    ])
    if parity_alt is None:
        gates.append({"name": "reflection_separation", "status": "FAIL", "details": "opposite parity failed, not comparable"})
    else:
        parity_margin = parity_alt["heldout"]["rms_mm"] - group_best["heldout"]["rms_mm"]
        gates.append({"name": "reflection_separation", "status": "PASS" if parity_margin > max(0.25, 0.02 * group_best["heldout"]["rms_mm"]) else "FAIL",
                      "details": {"selected_class": selected_class, "margin_mm": parity_margin}})
    if semantic_alt is None:
        gates.append({"name": "distance_semantics_separation", "status": "FAIL", "details": "alternate semantics failed, not comparable"})
    else:
        semantics_margin = semantic_alt["heldout"]["rms_mm"] - group_best["heldout"]["rms_mm"]
        gates.append({"name": "distance_semantics_separation", "status": "PASS" if semantics_margin > max(0.25, 0.02 * group_best["heldout"]["rms_mm"]) else "FAIL",
                      "details": {"selected": selected_semantics, "margin_mm": semantics_margin}})
    if len(gauge) > 1:
        warnings.append("D4_PROPER_ROTATION_GAUGE_EQUIVALENCE" if selected_class == "proper_rotation_gauge" else "D4_REFLECTION_ROTATION_GAUGE_EQUIVALENCE")
    return best, {
        "absolute_mm": float(absolute_margin), "relative": float(relative_margin),
        "next_group": None if next_group is None else next_group["candidate_id"],
        "gauge_candidate_ids": [item["candidate_id"] for item in gauge],
    }, gates, warnings


def _project_points(
    points_tof: np.ndarray, rotation: np.ndarray, translation: np.ndarray,
    camera_matrix: np.ndarray, distortion: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    camera_points = transform_points(rotation, translation, points_tof)
    visible = camera_points[:, 2] > 0
    projected = np.full((len(points_tof), 2), np.nan)
    if np.any(visible):
        values, _ = cv2.projectPoints(
            camera_points[visible], np.zeros(3), np.zeros(3), camera_matrix, distortion,
        )
        projected[visible] = values.reshape(-1, 2)
    return projected, visible


def write_overlays(
    output_dir: Path, observations: list[PlaneObservation], best: dict[str, Any],
    pose_diagnostics: dict[str, Any], camera_matrix: np.ndarray, distortion: np.ndarray,
) -> tuple[list[bool], dict[str, list[float]]]:
    overlay_dir = output_dir / "overlays"
    overlay_dir.mkdir()
    rays = nominal_zone_rays()
    rotation = np.asarray(best["rotation_matrix"], dtype=np.float64)
    translation = np.asarray(best["translation_mm"], dtype=np.float64)
    orientation = best["orientation"]
    semantics = best["distance_semantics"]
    raw_map = raw_index_for_canonical_index(orientation)
    overlap_counts = np.zeros(64, dtype=np.int64)
    residual_rows: dict[str, list[float]] = {}
    for pose in observations:
        distance = canonical_values_from_raw(pose.raw_distance_mm, orientation)
        valid = canonical_values_from_raw(pose.raw_valid, orientation).astype(bool) & np.isfinite(distance)
        zones = np.flatnonzero(valid)
        points = points_from_distances(rays[zones], distance[zones], semantics)
        projected, positive = _project_points(points, rotation, translation, camera_matrix, distortion)
        inside = positive & (projected[:, 0] >= 0) & (projected[:, 0] < 640) & (projected[:, 1] >= 0) & (projected[:, 1] < 360)
        overlap_counts[zones[inside]] += 1
        camera_points = transform_points(rotation, translation, points)
        residual = camera_points @ pose.normal_camera + pose.offset_camera_mm
        residual_rows[pose.pose_name] = residual.tolist()
        inlier_set = set(pose_diagnostics[pose.pose_name]["inlier_canonical_zones"])
        base = pose.image_rgb.copy()
        board = base.copy()
        polygon: np.ndarray | None = None
        if np.isfinite(pose.board_polygon).all():
            polygon = np.round(pose.board_polygon).astype(np.int32)
            pose_diagnostics[pose.pose_name]["board_polygon_overlay"] = {
                "status": "DRAWN"
            }
            cv2.polylines(
                board, [polygon], True, (0, 255, 255), 2, cv2.LINE_AA
            )
            board_label = f"{pose.pose_name} [{pose.split}] board plane"
        else:
            pose_diagnostics[pose.pose_name]["board_polygon_overlay"] = {
                "status": "SKIPPED_NONFINITE",
                "diagnostic": "board polygon contains NaN or infinity",
            }
            board_label = (
                f"{pose.pose_name} [{pose.split}] polygon skipped: non-finite"
            )
        cv2.putText(board, board_label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1, cv2.LINE_AA)
        cv2.imwrite(str(overlay_dir / f"{pose.pose_name}_board_plane.png"), board)
        centres = base.copy()
        outliers = base.copy()
        if polygon is not None:
            cv2.polylines(
                centres, [polygon], True, (0, 255, 255), 2, cv2.LINE_AA
            )
            cv2.polylines(
                outliers, [polygon], True, (0, 255, 255), 2, cv2.LINE_AA
            )
        for local, canonical in enumerate(zones):
            raw = int(raw_map[canonical])
            if not positive[local]:
                label = f"c{canonical}/r{raw} BEHIND"
                cv2.putText(centres, label, (8, 42 + 14 * (canonical % 18)), cv2.FONT_HERSHEY_PLAIN, 0.7, (0, 0, 255), 1)
                continue
            point = tuple(np.round(projected[local]).astype(int))
            inlier = int(canonical) in inlier_set
            color = (0, 220, 0) if inlier else (0, 0, 255)
            if not inside[local]:
                color = (255, 0, 255)
            cv2.circle(centres, point, 3, color, -1, cv2.LINE_AA)
            cv2.circle(outliers, point, 4, color, 1, cv2.LINE_AA)
            if canonical in {0, 7, 56, 63}:
                corner_x = 10 if canonical in {0, 56} else 460
                corner_y = 28 if canonical in {0, 7} else 315
                cv2.putText(
                    centres, f"CORNER c{canonical}/r{raw} {distance[canonical]:.0f}",
                    (corner_x, corner_y), cv2.FONT_HERSHEY_PLAIN, 0.75, (0, 255, 255), 1, cv2.LINE_AA,
                )
            else:
                label_x = min(max(point[0] + 2, 1), 600)
                label_y = min(max(point[1] - 2, 9), 350)
                cv2.putText(
                    centres, f"{canonical}/{raw}", (label_x, label_y),
                    cv2.FONT_HERSHEY_PLAIN, 0.5, color, 1, cv2.LINE_AA,
                )
        cv2.putText(centres, f"{pose.split} green=inlier red=outlier magenta=outside", (8, 345), cv2.FONT_HERSHEY_PLAIN, 0.8, (255, 255, 255), 1)
        cv2.putText(outliers, f"{pose.pose_name} raw/canonical zone inlier mask", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.imwrite(str(overlay_dir / f"{pose.pose_name}_zone_centres.png"), centres)
        cv2.imwrite(str(overlay_dir / f"{pose.pose_name}_inlier_outlier.png"), outliers)
    threshold = max(1, len(observations) // 2)
    return (overlap_counts >= threshold).tolist(), residual_rows


def write_residual_csv(
    path: Path, observations: list[PlaneObservation], best: dict[str, Any],
    pose_diagnostics: dict[str, Any],
) -> None:
    rays = nominal_zone_rays()
    rotation = np.asarray(best["rotation_matrix"])
    translation = np.asarray(best["translation_mm"])
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["pose_name", "split", "canonical_zone", "raw_zone", "distance_mm", "residual_mm", "dominant_plane_inlier"])
        mapping = raw_index_for_canonical_index(best["orientation"])
        for pose in observations:
            distance = canonical_values_from_raw(pose.raw_distance_mm, best["orientation"])
            valid = canonical_values_from_raw(pose.raw_valid, best["orientation"]).astype(bool) & np.isfinite(distance)
            zones = np.flatnonzero(valid)
            points = points_from_distances(rays[zones], distance[zones], best["distance_semantics"])
            camera = transform_points(rotation, translation, points)
            residuals = camera @ pose.normal_camera + pose.offset_camera_mm
            inliers = set(pose_diagnostics[pose.pose_name]["inlier_canonical_zones"])
            for zone, residual in zip(zones, residuals):
                writer.writerow([pose.pose_name, pose.split, int(zone), int(mapping[zone]), float(distance[zone]), float(residual), int(zone) in inliers])


def build_profile(
    best: dict[str, Any], margin: dict[str, Any], observations: list[PlaneObservation],
    pose_diagnostics: dict[str, Any], metadata: dict[str, Any], overlap_mask: list[bool],
    warnings: list[str],
) -> dict[str, Any]:
    rotation = np.asarray(best["rotation_matrix"], dtype=np.float64)
    translation = np.asarray(best["translation_mm"], dtype=np.float64)
    transform = np.eye(4)
    transform[:3, :3] = rotation
    transform[:3, 3] = translation
    train = [item for item in observations if item.split == "train"]
    heldout = [item for item in observations if item.split == "heldout"]
    zone_counts = np.zeros(64, dtype=np.int64)
    for pose in train:
        for zone in pose_diagnostics[pose.pose_name]["inlier_canonical_zones"]:
            zone_counts[zone] += 1
    valid_mask = (zone_counts >= max(1, len(train) // 2)).tolist()
    orientation = best["orientation"]
    gauge_ids = margin["gauge_candidate_ids"]
    profile_seed = {
        "camera_profile_id": metadata["camera_profile_id"], "T": transform.tolist(),
        "orientation": orientation, "semantics": best["distance_semantics"],
        "source_hashes": metadata["source_hashes"],
    }
    return {
        "schema_version": "camera_tof_extrinsics.v1",
        "profile_id": "camera_tof_extrinsics_" + canonical_sha256(profile_seed)[:16],
        "created_at": metadata["created_at"],
        "camera_profile_id": metadata["camera_profile_id"],
        "camera_resolution": [640, 360], "tof_model": "VL53L5CX",
        "tof_resolution": [8, 8], "tof_frequency_hz": 15,
        "transform_direction": "T_camera_from_tof",
        "matrix_convention": "row-major, column-vector",
        "T_camera_from_tof": transform.tolist(),
        "rotation_matrix": rotation.tolist(), "translation": translation.tolist(),
        "translation_unit": "mm",
        "orientation": {
            "name": orientation,
            "raw_index_for_canonical_index": raw_index_for_canonical_index(orientation).tolist(),
        },
        "orientation_resolution_status": "canonicalized_gauge; physical_orientation_unresolved" if len(gauge_ids) > 1 else "physically_identified",
        "zone_index_contract": "raw_index=raw_row*8+raw_col; canonical values consume raw_index_for_canonical_index",
        "ray_model": {
            "name": "vl53l5cx_nominal_regular_45deg_centres",
            "hfov_deg": 45.0, "vfov_deg": 45.0,
            "provenance": "Calib_DELTAR-style nominal regular model; not manufacturer per-zone measured rays",
            "distance_semantics": best["distance_semantics"],
        },
        "distance_semantics": best["distance_semantics"],
        "zone_rays": nominal_zone_rays().tolist(),
        "zone_valid_mask": [bool(item) for item in valid_mask],
        "camera_tof_overlap_mask": [bool(item) for item in overlap_mask],
        "solver_method": "NumPy/OpenCV robust Huber point-to-plane LM; left rotation perturbation",
        "training_pose_count": len(train), "heldout_pose_count": len(heldout),
        "training_point_count": best["train"]["count"], "heldout_point_count": best["heldout"]["count"],
        "residual_summary": {"train": best["train"], "heldout": best["heldout"]},
        "candidate_margin": margin,
        "gauge_equivalence": {
            "present": len(gauge_ids) > 1,
            "candidate_ids": gauge_ids,
            "canonicalization": "minimum rotation angle, then tx near -20 mm in Camera coordinates, after equivalent combined mapping",
        },
        "mechanical_prior": {
            "tof_is_camera_right": True, "expected_tx_mm": EXPECTED_TX_MM,
            "expected_abs_ty_mm": 3.5, "expected_abs_tz_mm": 6.5,
            "usage": "initialization, wide plausibility gate, and gauge tie-break only",
        },
        "mechanical_prior_is_measured": False,
        "validation_status": "PASS",
        "camera_tof_extrinsics": {"status": "measured"},
        "absolute_geometry_valid": False, "world_pose_resolved": False,
        "source_data_hashes": metadata["source_hashes"],
        "warnings": warnings + ["NOMINAL_RAYS_NOT_MANUFACTURER_MEASURED", "WORLD_POSE_UNRESOLVED"],
        "unresolved_fields": ["physical_zone_orientation_when_rotation_gauge_present", "manufacturer_per_zone_rays", "world_pose"],
    }
