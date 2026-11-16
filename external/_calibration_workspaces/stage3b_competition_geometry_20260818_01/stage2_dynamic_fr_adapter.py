"""Validation-only Stage2 dynamic DELTAR ``fr`` adapter.

The adapter consumes only the frozen Stage2 final bundle.  It preserves raw
zone order, treats the sensor values as axial Z, projects each zone's four
model-derived boundary rays at that zone's current depth, and finally applies
the existing DELTAR canvas transform.  It never mutates runtime profiles.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

EXPECTED_STAGE2_SHA256 = (
    "63c974cd2ff64dd0f135d47d8c92eb170d06658cb315563fb93588862ba8b057"
)
EXPECTED_STAGE2_SCHEMA = "stage2_final_calibration_bundle.v1"


class AuthorityError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_stage2_bundle(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    actual = sha256_file(resolved)
    if actual != EXPECTED_STAGE2_SHA256:
        raise AuthorityError(
            f"Stage2 SHA-256 mismatch: expected {EXPECTED_STAGE2_SHA256}, got {actual}"
        )
    document = json.loads(resolved.read_text(encoding="utf-8"))
    failures = []
    if document.get("schema_version") != EXPECTED_STAGE2_SCHEMA:
        failures.append("schema_version")
    if document.get("bundle_status") != "CANDIDATE_NOT_PROMOTED":
        failures.append("bundle_status")
    camera_tof = document.get("camera_tof") or {}
    fr = document.get("deltar_fr") or {}
    if camera_tof.get("range_semantics", {}).get("selected") != "axial_z":
        failures.append("camera_tof.range_semantics.selected")
    if fr.get("measurement_semantics") != "axial_z":
        failures.append("deltar_fr.measurement_semantics")
    if fr.get("field_order") != ["sy", "sx", "ey", "ex"]:
        failures.append("deltar_fr.field_order")
    if fr.get("shape") != [64, 4]:
        failures.append("deltar_fr.shape")
    mapping = camera_tof.get("raw_zone_camera_mapping")
    if not isinstance(mapping, list) or len(mapping) != 64:
        failures.append("camera_tof.raw_zone_camera_mapping")
    elif [item.get("raw_index") for item in mapping] != list(range(64)):
        failures.append("camera_tof.raw_zone_camera_mapping.raw_index")
    if camera_tof.get("gauge", {}).get("raw_index_for_canonical_index") != list(
        range(64)
    ):
        failures.append("camera_tof.gauge.raw_index_for_canonical_index")
    if failures:
        raise AuthorityError("Stage2 authority contract failed: " + ", ".join(failures))
    return document


def stage2_camera_model(bundle: dict[str, Any]) -> dict[str, Any]:
    intr = bundle["camera_intrinsics"]["camera"]["intrinsics"]
    distortion = intr["distortion"]
    return {
        "model": "opencv_brown",
        "fx": float(intr["fx"]),
        "fy": float(intr["fy"]),
        "cx": float(intr["cx"]),
        "cy": float(intr["cy"]),
        "distortion": [
            float(distortion[name]) for name in ("k1", "k2", "p1", "p2", "k3")
        ],
    }


def _source_fr(bundle: dict[str, Any], axial_depth_mm: np.ndarray) -> np.ndarray:
    depth = np.asarray(axial_depth_mm, dtype=np.float64)
    if depth.shape != (64,) or not np.isfinite(depth).all() or np.any(depth <= 0):
        raise AuthorityError("dynamic fr depths must be positive finite [64]")
    camera_model = stage2_camera_model(bundle)
    intrinsic = np.array(
        [
            [camera_model["fx"], 0.0, camera_model["cx"]],
            [0.0, camera_model["fy"], camera_model["cy"]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    distortion = np.asarray(camera_model["distortion"], dtype=np.float64)
    transform = bundle["camera_tof"]["T_camera_from_tof"]
    rotation = np.asarray(transform["R"], dtype=np.float64)
    translation = np.asarray(transform["t_mm"], dtype=np.float64)
    rectangles = np.empty((64, 4), dtype=np.float64)
    for index, item in enumerate(bundle["camera_tof"]["raw_zone_camera_mapping"]):
        corners = []
        for raw_ray in item["tof_frame_corner_rays_model_derived"]:
            ray = np.asarray(raw_ray, dtype=np.float64)
            if ray.shape != (3,) or ray[2] <= 0:
                raise AuthorityError(f"invalid Stage2 boundary ray at raw zone {index}")
            point_tof = ray * (depth[index] / ray[2])
            point_camera = rotation @ point_tof + translation
            if point_camera[2] <= 0:
                raise AuthorityError(f"projected zone {index} is behind the camera")
            uv, _ = cv2.projectPoints(
                point_camera.reshape(1, 3),
                np.zeros(3),
                np.zeros(3),
                intrinsic,
                distortion,
            )
            corners.append(uv.reshape(2))
        points = np.asarray(corners, dtype=np.float64)
        rectangles[index] = [
            float(points[:, 1].min()),
            float(points[:, 0].min()),
            float(points[:, 1].max()),
            float(points[:, 0].max()),
        ]
    return rectangles


def _canvas_transform(fr: np.ndarray, strategy: str) -> np.ndarray:
    result = np.asarray(fr, dtype=np.float64).copy()
    if strategy == "direct_640x360":
        return result
    if strategy == "letterbox_640x480":
        result[:, (0, 2)] += 60.0
        return result
    raise AuthorityError(f"unsupported formal canvas strategy: {strategy}")


def dynamic_fr_from_tof(
    bundle: dict[str, Any], tof: dict[str, Any], canvas_strategy: str
) -> tuple[np.ndarray, dict[str, Any]]:
    distance = np.asarray(tof.get("distance_mm"), dtype=np.float64)
    status = np.asarray(tof.get("target_status"))
    count = np.asarray(tof.get("nb_target_detected"))
    if distance.shape != status.shape or distance.shape != count.shape or distance.shape != (64,):
        raise AuthorityError("ToF distance/status/count must each have 64 raw-zone values")
    valid = (
        np.isfinite(distance)
        & (distance > 0.0)
        & (count != 0)
        & (status != 255)
    )
    if not np.any(valid):
        raise AuthorityError("frame has no valid ToF zones for dynamic fr")
    fallback = float(np.median(distance[valid]))
    effective = np.where(valid, distance, fallback)
    source = _source_fr(bundle, effective)
    transformed = _canvas_transform(source, canvas_strategy).astype(np.float32)
    return transformed, {
        "authority": "Stage2 final bundle dynamic per-zone projection",
        "measurement_semantics": "axial_z",
        "zone_order": "raw_index_0_to_63",
        "valid_zone_count": int(np.count_nonzero(valid)),
        "masked_invalid_zone_depth_policy": "valid_zone_median_for_patch_info_only",
        "masked_invalid_zone_fallback_mm": fallback,
        "canvas_strategy": canvas_strategy,
        "shape": list(transformed.shape),
        "dtype": str(transformed.dtype),
    }


def reference_parity(bundle: dict[str, Any]) -> dict[str, Any]:
    fixtures = bundle["deltar_fr"]["model_derived_rectangle_by_reference_distance_mm"]
    rows = []
    for key, expected_values in fixtures.items():
        distance = float(key)
        actual = _source_fr(bundle, np.full(64, distance, dtype=np.float64))
        expected = np.asarray(expected_values, dtype=np.float64)
        error = np.abs(actual - expected)
        rows.append(
            {
                "reference_distance_mm": distance,
                "max_abs_error_px": float(np.max(error)),
                "rms_error_px": float(np.sqrt(np.mean(np.square(error)))),
                "pass": bool(np.max(error) <= 1e-6),
            }
        )
    return {
        "schema_version": "stage3b_dynamic_fr_reference_parity.v1",
        "fixture_count": len(rows),
        "all_pass": all(item["pass"] for item in rows),
        "fixtures": rows,
    }
