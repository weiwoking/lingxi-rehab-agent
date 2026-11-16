from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .scan_contracts import ScanContractError, atomic_write_json, load_strict_json
from .formal_geometry_authority import load_formal_geometry_authority
from .geometry_contracts import (
    ARTIFACT_FILENAME,
    CONFIDENCE_THRESHOLD,
    SCHEMA_VERSION,
    validate_geometry_document,
)
from .geometry_v2.generic_extent import estimate_generic_extent
from .geometry_v2.generic_support import (
    SupportUnavailable,
    bbox_mask as v2_bbox_mask,
    estimate_support,
    group_same_surfaces,
)
from .geometry_v2.ball_extent import estimate_ball_extent
from .geometry_v2.shape_router import route_shape
from .geometry_v2.upright_container import estimate_upright_container
from .geometry_v2.upright_slender import _rays


IMAGE_SHAPE = (360, 640)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_geometry_profile(path: Path) -> dict[str, Any]:
    profile = load_strict_json(path, code="E_ALL_OBJECT_GEOMETRY_PROFILE")
    if profile.get("schema_version") != "formal_geometry_region_selection.v2":
        raise ScanContractError(
            "E_ALL_OBJECT_GEOMETRY_PROFILE",
            "all-object geometry profile schema mismatch",
            "schema_version",
        )
    required = {
        "schema_version", "profile_id", "min_points", "depth_cluster_bin_width_mm",
        "depth_cluster_neighbor_bins", "depth_cluster_min_points",
        "depth_cluster_max_bins", "robust_mad_scale", "robust_min_window_mm",
    }
    if set(profile) != required:
        raise ScanContractError(
            "E_ALL_OBJECT_GEOMETRY_PROFILE",
            "formal region-selection keys mismatch",
            "$",
        )
    for name in required - {"schema_version", "profile_id"}:
        if not isinstance(profile[name], (int, float)) or not math.isfinite(float(profile[name])):
            raise ScanContractError(
                "E_ALL_OBJECT_GEOMETRY_PROFILE", "region parameter must be finite", name
            )
    return profile


def _selected_observation(group: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    segment = group.get("segmentation_primary") or {}
    detection = group.get("detection_fallback") or {}
    if segment.get("available"):
        return "segmentation", segment
    if detection.get("available"):
        return "detection", detection
    return "none", {}


def erode_mask_one_pixel(mask: np.ndarray) -> np.ndarray:
    source = np.asarray(mask, dtype=bool)
    if source.shape != IMAGE_SHAPE:
        raise ValueError("mask must be source-aligned 640x360")
    padded = np.pad(source, 1, mode="constant", constant_values=False)
    result = np.ones(source.shape, dtype=bool)
    for dy in range(3):
        for dx in range(3):
            result &= padded[dy : dy + source.shape[0], dx : dx + source.shape[1]]
    return result


def _mad(values: np.ndarray) -> float:
    median = float(np.median(values))
    return float(np.median(np.abs(values - median)))


def _robust_keep(
    region: np.ndarray,
    finite: np.ndarray,
    axial: np.ndarray,
    profile: dict[str, Any],
) -> np.ndarray:
    base = region & finite
    values = axial[base]
    minimum = int(profile["min_points"])
    if values.size < minimum:
        return base
    median = float(np.median(values))
    limit = max(
        float(profile["robust_min_window_mm"]),
        float(profile["robust_mad_scale"]) * 1.4826 * _mad(values),
    )
    kept = base & (np.abs(axial - median) <= limit)
    return kept if int(np.count_nonzero(kept)) >= minimum else base


def _main_depth_cluster(
    region: np.ndarray,
    finite: np.ndarray,
    axial: np.ndarray,
    profile: dict[str, Any],
) -> np.ndarray | None:
    base = region & finite
    values = axial[base]
    minimum = int(profile["depth_cluster_min_points"])
    if values.size < minimum:
        return None
    low, high = (float(value) for value in np.percentile(values, [1.0, 99.0]))
    width = float(profile["depth_cluster_bin_width_mm"])
    if not math.isfinite(low) or not math.isfinite(high) or high <= low or width <= 0.0:
        return None
    origin = math.floor(low / width) * width
    bin_count = max(1, min(int(profile["depth_cluster_max_bins"]), int(math.ceil((high - origin) / width))))
    edges = origin + np.arange(bin_count + 1, dtype=np.float64) * width
    if edges[-1] <= high:
        edges = np.append(edges, edges[-1] + width)
    counts, edges = np.histogram(values, bins=edges)
    if counts.size == 0 or int(np.max(counts)) <= 0:
        return None
    # np.flatnonzero returns increasing indices: equal-size clusters resolve to
    # the nearest (lowest axial-Z) bin deterministically.
    peak = int(np.flatnonzero(counts == np.max(counts))[0])
    neighbours = int(profile["depth_cluster_neighbor_bins"])
    first = max(0, peak - neighbours)
    last = min(len(edges) - 1, peak + neighbours + 1)
    clustered = base & (axial >= edges[first]) & (axial <= edges[last])
    return clustered if int(np.count_nonzero(clustered)) >= minimum else None


def _failure_object(
    group: dict[str, Any],
    reason: str,
    *,
    region_source: str = "none",
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_source, selected = _selected_observation(group)
    bbox = selected.get("bbox_xyxy") if selected else None
    return {
        "logical_object_id": str(group.get("logical_object_id", "")),
        "class_id": group.get("class_id"),
        "class_name": str(group.get("class_name") or selected.get("class_name") or "unknown"),
        "confidence": float(selected.get("confidence", 0.0)) if selected else 0.0,
        "bbox_xyxy": [float(value) for value in bbox] if isinstance(bbox, list) and len(bbox) == 4 else None,
        "selected_source": selected_source,
        "provenance": dict(group.get("provenance") or {}),
        "geometry_status": "failure",
        "geometry": {
            "distance_mm": None,
            "object_height_mm": None,
            "support_height_mm": None,
            "top_height_above_floor_mm": None,
        },
        "geometry_confidence": None,
        "failure_reason": reason,
        "diagnostics": {"region_source": region_source, **(diagnostics or {})},
    }


def _selected_v2_region(
    group: dict[str, Any],
    segment_masks: dict[str, np.ndarray],
    axial: np.ndarray,
    world: np.ndarray,
    profile: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, list[float], dict[str, Any]]:
    selected_source, selected = _selected_observation(group)
    if not selected:
        raise ValueError("no_valid_mask_or_bbox")
    bbox = [float(value) for value in selected["bbox_xyxy"]]
    mask_key = selected.get("mask_key")
    raw_mask = segment_masks.get(str(mask_key)) if mask_key is not None else None
    if raw_mask is not None and np.asarray(raw_mask).shape != axial.shape:
        raw_mask = None
    finite = np.isfinite(axial) & (axial > 0.0) & np.isfinite(world[..., 2])
    minimum = int(profile["min_points"])
    region: np.ndarray | None = None
    keep: np.ndarray | None = None
    method = "none"
    if raw_mask is not None and np.any(raw_mask):
        raw = np.asarray(raw_mask, dtype=bool)
        eroded = erode_mask_one_pixel(raw)
        if np.count_nonzero(eroded & finite) >= minimum:
            region = eroded
            keep = _robust_keep(eroded, finite, axial, profile)
            method = "mask_eroded_1px_robust"
        else:
            region = raw
            keep = _robust_keep(raw, finite, axial, profile)
            method = "mask_raw_robust"
    if region is None:
        region = v2_bbox_mask(bbox, axial.shape)
        cluster = _main_depth_cluster(region, finite, axial, profile)
        keep = cluster if cluster is not None else _robust_keep(
            region, finite, axial, profile
        )
        method = "bbox_main_depth_cluster" if cluster is not None else "bbox_robust"
    if keep is None:
        raise ValueError("no_v2_region")
    valid = keep & finite
    if np.count_nonzero(valid) < minimum:
        raise ValueError("insufficient_v2_points")
    values = axial[valid]
    meta = {
        "selected_source": selected_source,
        "region_source": "mask" if raw_mask is not None else "bbox",
        "region_method": method,
        "point_count": int(np.count_nonzero(valid)),
        "depth_iqr_mm": float(np.percentile(values, 75) - np.percentile(values, 25)),
    }
    return region, valid, (
        None if raw_mask is None else np.asarray(raw_mask, dtype=bool)
    ), bbox, meta


def measure_logical_object_v2(
    group: dict[str, Any],
    segment_masks: dict[str, np.ndarray],
    camera_axial_z_mm: np.ndarray,
    world_points_mm: np.ndarray,
    support_planes: list[dict[str, Any]],
    profile: dict[str, Any],
    camera: dict[str, Any],
    gravity: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    axial = np.asarray(camera_axial_z_mm, dtype=np.float64)
    world = np.asarray(world_points_mm, dtype=np.float64)
    if axial.shape != IMAGE_SHAPE or world.shape != (*IMAGE_SHAPE, 3):
        raise ValueError("formal Geometry v2 input shape invalid")
    region, valid, raw_mask, bbox, meta = _selected_v2_region(
        group, segment_masks, axial, world, profile
    )
    support = estimate_support(
        raw_mask if raw_mask is not None else region,
        bbox,
        axial,
        world,
        valid,
        support_planes,
        config["generic_support"],
        gravity,
    )
    generic = estimate_generic_extent(
        valid,
        world[..., 2],
        float(support["support_mm"]),
        config["generic_extent"],
    )
    router = route_shape(
        str(group.get("class_name", "")),
        raw_mask,
        bbox,
        float(support["confidence"]),
        meta["depth_iqr_mm"],
        config["router"],
    )
    enhancement: dict[str, Any] = {
        "available": False,
        "reason": "generic_family_selected",
    }
    if router["selected_family"] == "upright_container" and raw_mask is not None:
        enhancement = estimate_upright_container(
            raw_mask,
            float(support["support_mm"]),
            camera,
            gravity,
            axial,
            valid,
            config["upright_container"],
        )
    elif router["selected_family"] == "sports_ball":
        enhancement = estimate_ball_extent(
            raw_mask,
            bbox,
            float(support["support_mm"]),
            axial,
            world,
            camera,
            gravity,
            config["sports_ball"],
        )
    if enhancement.get("available"):
        disagreement = abs(float(enhancement["top_mm"]) - float(generic["top_mm"]))
        enhancement["generic_top_disagreement_mm"] = disagreement
        if disagreement > float(
            config["generic_extent"]["maximum_enhancer_top_disagreement_mm"]
        ):
            enhancement["available"] = False
            enhancement["reason"] = "generic_top_consistency_gate"
    if enhancement.get("available"):
        # Support is primary environment/contact geometry.  Shape-family
        # estimators may improve extent/top but may not replace support.
        final_support = float(support["support_mm"])
        final_top = float(enhancement["top_mm"])
        final = {
            "support_mm": final_support,
            "support_source": support["source"],
            "top_mm": final_top,
            "object_height_mm": final_top - final_support,
            "extent_method": enhancement["method"],
            "extent_confidence": float(enhancement["confidence"]),
            "estimator_path": f"generic_support->{router['selected_family']}",
        }
    else:
        final = {
            "support_mm": float(support["support_mm"]),
            "support_source": support["source"],
            "top_mm": float(generic["top_mm"]),
            "object_height_mm": float(generic["object_height_mm"]),
            "extent_method": generic["method"],
            "extent_confidence": float(generic["confidence"]),
            "estimator_path": "generic_support->generic_extent",
        }
        if router["selected_family"] != "generic":
            router["fallback_reason"] = f"enhancer_failed:{enhancement.get('reason')}"
    if not all(
        np.isfinite(
            [final["support_mm"], final["top_mm"], final["object_height_mm"]]
        )
    ) or final["object_height_mm"] <= 0.0:
        raise ValueError("Geometry v2 must preserve finite positive geometry")
    selected_source, selected = _selected_observation(group)
    base_confidence = min(
        float(support["confidence"]), float(final["extent_confidence"])
    )
    # Confidence is an evidence bound, not a mask-presence classification.
    # In particular, an otherwise good silhouette cannot promote weak or
    # occlusion-derived support evidence across the normal-confidence gate.
    geometry_confidence_value = (
        min(CONFIDENCE_THRESHOLD - 1e-6, base_confidence)
        if meta["region_source"] == "bbox"
        else base_confidence
    )
    public = {
        "logical_object_id": str(group["logical_object_id"]),
        "class_id": int(group["class_id"]),
        "class_name": str(group["class_name"]),
        "confidence": float(selected["confidence"]),
        "bbox_xyxy": bbox,
        "selected_source": selected_source,
        "provenance": dict(group.get("provenance") or {}),
        "geometry_status": "success",
        "geometry": {
            "distance_mm": float(np.median(axial[valid])),
            "object_height_mm": float(final["object_height_mm"]),
            "support_height_mm": float(final["support_mm"]),
            "top_height_above_floor_mm": float(final["top_mm"]),
        },
        "geometry_confidence": float(geometry_confidence_value),
        "failure_reason": None,
        "diagnostics": {
            **meta,
            "distance_semantics": "camera_axial_z_median",
            "height_semantics": "stage3a_gravity_world_z",
            "support_source": final["support_source"],
            "extent_method": final["extent_method"],
            "estimator_path": final["estimator_path"],
            "shape_family": router["selected_family"],
            "same_surface_group_id": None,
            "consensus_used": False,
        },
    }
    return {
        "public": public,
        "bbox_xyxy": bbox,
        "generic_support": support,
        "generic_extent": generic,
        "router": router,
        "enhancement": enhancement,
        "final": final,
        "same_surface_group_id": None,
        "consensus_used": False,
    }

@dataclass
class AllObjectGeometryProcessor:
    output_root: Path
    scan_id: str
    frame_count: int
    profile: dict[str, Any]
    frames: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.frame_count != 11:
            raise ValueError("all-object geometry processor only accepts 11-frame scans")
        self.authority = load_formal_geometry_authority()
        self.v2_config = self.authority.geometry_v2

    def process_frame(
        self,
        frame_id: str,
        sequence: int,
        logical_groups: list[dict[str, Any]],
        segment_masks: dict[str, np.ndarray],
        camera_axial_z_mm: np.ndarray,
        world_points_or_z_mm: np.ndarray,
        support_planes: list[dict[str, Any]],
        source_image_path: Path | None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        world = np.asarray(world_points_or_z_mm)
        if world.ndim == 2:
            # Cached replay stores authoritative Stage3A world-height plus
            # Stage2 axial depth, not a redundant XYZ tensor.  Reconstruct X/Y
            # only for shape extent; preserve cached world Z bit-for-bit.
            yy, xx = np.indices(world.shape, dtype=np.float64)
            uv = np.column_stack((xx.ravel(), yy.ravel()))
            rays = _rays(uv, self.authority.camera_model).reshape(*world.shape, 3)
            axial = np.asarray(camera_axial_z_mm, dtype=np.float64)
            rebuilt = rays * axial[..., None]
            rebuilt[..., 2] = world
            world = rebuilt
        objects: list[dict[str, Any]] = []
        v2_records: list[dict[str, Any]] = []
        for group in logical_groups:
            try:
                record = measure_logical_object_v2(
                    group,
                    segment_masks,
                    camera_axial_z_mm,
                    world,
                    support_planes,
                    self.profile,
                    self.authority.camera_model,
                    self.authority.stage3a,
                    self.v2_config,
                )
                measured = record["public"]
                v2_records.append(record)
            except SupportUnavailable as exc:
                measured = _failure_object(
                    group,
                    exc.reason,
                    diagnostics={"support_diagnostics": exc.diagnostics},
                )
            except Exception as exc:  # One malformed object must not hide its peers.
                measured = _failure_object(
                    group, f"geometry_runtime_exception:{type(exc).__name__}"
                )
            objects.append(measured)
        group_same_surfaces(v2_records, self.v2_config["same_surface"], frame_id)
        for record in v2_records:
            public = record["public"]
            final = record["final"]
            public["geometry"].update(
                {
                    "support_height_mm": float(final["support_mm"]),
                    "top_height_above_floor_mm": float(final["top_mm"]),
                    "object_height_mm": float(final["object_height_mm"]),
                }
            )
            public["diagnostics"].update(
                {
                    "support_source": final["support_source"],
                    "same_surface_group_id": record["same_surface_group_id"],
                    "consensus_used": bool(record["consensus_used"]),
                    "generic_support": record["generic_support"],
                    "generic_extent": record["generic_extent"],
                    "shape_router": record["router"],
                    "shape_enhancement": record["enhancement"],
                    "same_surface_audit": record["same_surface_audit"],
                }
            )
        frame = {
            "frame_id": str(frame_id),
            "sequence": int(sequence),
            "source_image_path": str(source_image_path.resolve()) if source_image_path else None,
            "object_count": len(objects),
            "geometry_success_count": sum(item["geometry_status"] == "success" for item in objects),
            "geometry_failure_count": sum(item["geometry_status"] != "success" for item in objects),
            "processing_ms": (time.perf_counter() - started) * 1000.0,
            "objects": objects,
        }
        self.frames.append(frame)
        return frame

    def add_failed_frame_objects(
        self,
        frame_id: str,
        sequence: int,
        logical_groups: list[dict[str, Any]],
        reason: str,
        source_image_path: Path | None,
    ) -> None:
        objects = [_failure_object(group, reason) for group in logical_groups]
        self.frames.append(
            {
                "frame_id": str(frame_id),
                "sequence": int(sequence),
                "source_image_path": str(source_image_path.resolve()) if source_image_path else None,
                "object_count": len(objects),
                "geometry_success_count": 0,
                "geometry_failure_count": len(objects),
                "processing_ms": 0.0,
                "objects": objects,
            }
        )

    def document(self, *, generated_at: str | None = None) -> dict[str, Any]:
        ordered = sorted(self.frames, key=lambda item: (item["sequence"], item["frame_id"]))
        objects = [item for frame in ordered for item in frame["objects"]]
        confidences = [float(item["geometry_confidence"]) for item in objects if item["geometry_confidence"] is not None]
        document = {
            "schema_version": SCHEMA_VERSION,
            "scan_id": self.scan_id,
            "frame_count": self.frame_count,
            "generated_at": generated_at or _utc_now(),
            "profile": {
                "schema_version": self.v2_config["schema_version"],
                "profile_id": self.authority.manifest["profile_id"],
                "confidence_threshold": CONFIDENCE_THRESHOLD,
                "geometry_authority": "ALLOBJECT_V2",
                "world_z_authority": "STAGE3A_GRAVITY",
                "camera_authority": "STAGE2",
                "promoted_payload_set_sha256": self.authority.manifest[
                    "promoted_payload_set_sha256"
                ],
            },
            "frames": ordered,
            "summary": {
                "object_count": len(objects),
                "geometry_success_count": sum(item["geometry_status"] == "success" for item in objects),
                "geometry_failure_count": sum(item["geometry_status"] != "success" for item in objects),
                "mask_count": sum(item.get("diagnostics", {}).get("region_source") == "mask" for item in objects),
                "bbox_count": sum(item.get("diagnostics", {}).get("region_source") == "bbox" for item in objects),
                "confidence_min": min(confidences) if confidences else None,
                "confidence_max": max(confidences) if confidences else None,
                "processing_ms": sum(float(frame["processing_ms"]) for frame in ordered),
                "generic_baseline_count": sum(
                    item.get("geometry_status") == "success" for item in objects
                ),
                "same_surface_consensus_count": sum(
                    item.get("diagnostics", {}).get("consensus_used") is True
                    for item in objects
                ),
                "upright_slender_count": sum(
                    item.get("diagnostics", {}).get("shape_family")
                    == "upright_container"
                    for item in objects
                ),
                "roughly_spherical_count": sum(
                    item.get("diagnostics", {}).get("shape_family")
                    == "sports_ball"
                    for item in objects
                ),
            },
        }
        return validate_geometry_document(document)

    def write(self, destination: Path | None = None) -> Path:
        path = destination or (self.output_root / ARTIFACT_FILENAME)
        atomic_write_json(path, self.document())
        return path


def _load_cached_masks(result_root: Path, sequence: int) -> dict[str, np.ndarray]:
    matches = sorted(
        (result_root / "stage8b1_yolo_batch_outputs" / "segment").glob(
            f"{sequence:03d}_*_segment_masks.npz"
        )
    )
    if not matches:
        return {}
    with np.load(matches[0], allow_pickle=False) as archive:
        return {
            key: np.asarray(archive[key], dtype=bool)
            for key in archive.files
            if np.asarray(archive[key]).shape == IMAGE_SHAPE
        }


def _load_cached_planes(stage7d_root: Path) -> list[dict[str, Any]]:
    plane_path = stage7d_root / "horizontal_planes.v1.json"
    mask_path = stage7d_root / "plane_masks.npz"
    if not plane_path.exists() or not mask_path.exists():
        return []
    document = load_strict_json(plane_path, code="E_ALL_OBJECT_GEOMETRY_REPLAY")
    with np.load(mask_path, allow_pickle=False) as archive:
        masks = {
            key: np.asarray(archive[key], dtype=bool)
            for key in archive.files
            if np.asarray(archive[key]).shape == IMAGE_SHAPE
        }
    planes: list[dict[str, Any]] = []
    for value in document.get("planes", []):
        if value.get("label") != "support_surface":
            continue
        plane = dict(value)
        key = f"{plane.get('plane_id')}_{plane.get('label')}"
        if key in masks:
            plane["_mask"] = masks[key]
        planes.append(plane)
    return planes


def rebuild_all_object_geometry_from_cache(
    result_root: Path,
    profile: dict[str, Any],
    *,
    destination: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    root = result_root.resolve()
    result_path = root / "result.json"
    if result_path.exists():
        result = load_strict_json(result_path, code="E_ALL_OBJECT_GEOMETRY_REPLAY")
        scan_id = str(result["scan_id"])
        frame_count = int((result.get("input") or {}).get("frame_count", -1))
    else:
        scan_id = root.name
        validated_scan = root.parent.parent / "validated" / scan_id / "scan.json"
        scan = load_strict_json(validated_scan, code="E_ALL_OBJECT_GEOMETRY_REPLAY")
        frame_count = int(scan.get("frame_count", -1))
    if frame_count != 11:
        raise ScanContractError(
            "E_ALL_OBJECT_GEOMETRY_REPLAY",
            "geometry replay only accepts 11-frame scans",
            "frame_count",
        )
    processor = AllObjectGeometryProcessor(root, scan_id, 11, profile)
    frame_paths = sorted((root / "frame_results").glob("frame_*.json"))
    for frame_path in frame_paths:
        frame = load_strict_json(frame_path, code="E_ALL_OBJECT_GEOMETRY_REPLAY")
        frame_id = str(frame["frame_id"])
        sequence = int(frame["sequence"])
        groups = list(frame.get("logical_object_groups", []))
        stage7d = root / "stage7d" / frame_id / "axial_z"
        source_image = root / "logs" / frame_id / "source_rgb.png"
        if not source_image.exists():
            alternate = root / "stage7d" / frame_id / "source_rgb.png"
            source_image = alternate if alternate.exists() else source_image
        try:
            axial = np.asarray(
                np.load(stage7d / "depth_source_640x360.npy", mmap_mode="r"),
                dtype=np.float64,
            ) * 1000.0
            world_z = np.asarray(
                np.load(stage7d / "world_height_mm.npy", mmap_mode="r"),
                dtype=np.float64,
            )
            planes = _load_cached_planes(stage7d)
            processor.process_frame(
                frame_id,
                sequence,
                groups,
                _load_cached_masks(root, sequence),
                axial,
                world_z,
                list(planes),
                source_image if source_image.exists() else None,
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            processor.add_failed_frame_objects(
                frame_id,
                sequence,
                groups,
                f"camera_world_unavailable:{type(exc).__name__}",
                source_image if source_image.exists() else None,
            )
    document = processor.document()
    path = destination or (root / ARTIFACT_FILENAME)
    atomic_write_json(path, document)
    return document, path
