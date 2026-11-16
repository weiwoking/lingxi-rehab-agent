"""Stage3A gravity-only world-Z adapter; no yaw or full-world claim."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np


class GravityAuthorityError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_gravity_candidate(path: Path) -> tuple[dict[str, Any], str]:
    resolved = path.resolve(strict=True)
    document = json.loads(resolved.read_text(encoding="utf-8"))
    failures = []
    if document.get("schema_version") != "camera_gravity.stage3a_candidate.v1":
        failures.append("schema_version")
    if document.get("status") != "CANDIDATE_NOT_PROMOTED":
        failures.append("status")
    if document.get("gravity_status") != "READY_FOR_STAGE3B_Z_VALIDATION":
        failures.append("gravity_status")
    if document.get("yaw_status") != "SEPARATE_LOW_CONFIDENCE":
        failures.append("yaw_status")
    gravity = document.get("gravity_authority") or {}
    if gravity.get("full_world_rotation_authority") is not False:
        failures.append("gravity_authority.full_world_rotation_authority")
    up = np.asarray(gravity.get("u_up_camera"), dtype=np.float64)
    if up.shape != (3,) or not np.isfinite(up).all() or abs(np.linalg.norm(up) - 1.0) > 1e-6:
        failures.append("gravity_authority.u_up_camera")
    height = document.get("camera_height_mm")
    if not isinstance(height, (int, float)) or not np.isfinite(float(height)):
        failures.append("camera_height_mm")
    if failures:
        raise GravityAuthorityError(
            "Stage3A gravity authority contract failed: " + ", ".join(failures)
        )
    return document, sha256_file(resolved)


def z_only_points_from_depth(
    depth_m: np.ndarray,
    camera_model: dict[str, Any],
    gravity_candidate: dict[str, Any],
    *,
    min_depth_m: float,
    max_depth_m: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    depth = np.asarray(depth_m, dtype=np.float64)
    if depth.ndim != 2:
        raise ValueError("depth_m must be a 2D source-aligned map")
    valid = (
        np.isfinite(depth)
        & (depth > float(min_depth_m))
        & (depth < float(max_depth_m))
    )
    yy, xx = np.nonzero(valid)
    compatible = np.full((*depth.shape, 3), np.nan, dtype=np.float64)
    camera_axial_z_mm = np.full(depth.shape, np.nan, dtype=np.float64)
    if xx.size == 0:
        return compatible, camera_axial_z_mm, valid
    uv = np.column_stack((xx, yy)).astype(np.float64)
    intrinsic = np.array(
        [
            [camera_model["fx"], 0.0, camera_model["cx"]],
            [0.0, camera_model["fy"], camera_model["cy"]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    normalized = cv2.undistortPoints(
        uv.reshape(-1, 1, 2),
        intrinsic,
        np.asarray(camera_model["distortion"], dtype=np.float64),
    ).reshape(-1, 2)
    z_mm = depth[yy, xx] * 1000.0
    camera_points = np.column_stack(
        (normalized[:, 0] * z_mm, normalized[:, 1] * z_mm, z_mm)
    )
    up = np.asarray(
        gravity_candidate["gravity_authority"]["u_up_camera"], dtype=np.float64
    )
    height = float(gravity_candidate["camera_height_mm"])
    world_z = camera_points @ up + height
    # X/Y remain camera-frame coordinates.  They are used only as a metric
    # parameterization for connected plane fitting; no world-horizontal/yaw
    # authority is asserted or exported.
    compatible[yy, xx, 0] = camera_points[:, 0]
    compatible[yy, xx, 1] = camera_points[:, 1]
    compatible[yy, xx, 2] = world_z
    camera_axial_z_mm[yy, xx] = z_mm
    return compatible, camera_axial_z_mm, valid


def world_z_from_camera_points(
    points_camera_mm: np.ndarray, gravity_candidate: dict[str, Any]
) -> np.ndarray:
    points = np.asarray(points_camera_mm, dtype=np.float64)
    up = np.asarray(
        gravity_candidate["gravity_authority"]["u_up_camera"], dtype=np.float64
    )
    return points @ up + float(gravity_candidate["camera_height_mm"])
