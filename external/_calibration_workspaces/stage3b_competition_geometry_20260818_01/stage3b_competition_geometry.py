#!/usr/bin/env python3
"""No-argument-command Stage3B competition geometry validation tool.

This is an isolated validation runner.  It reads formal source and models but
does not alter formal runtime, calibration candidates, or firmware.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import sys
import tempfile
import traceback
import zipfile
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

WORKSPACE = Path(r"C:\ec\_calibration_workspaces\stage3b_competition_geometry_20260818_01")
PIPELINE_ROOT = Path(r"C:\ec\_deltar_p4_pipeline")
FORMAL_ROOT = Path(r"C:\ec\_deltar_formal")
VALIDATED_ROOT = Path(r"C:\ec\_deltar_scan_service_data\validated")
STAGE2_PATH = Path(
    r"C:\ec\_calibration_workspaces\stage2_final_bundle_20260818_01"
    r"\calibration_bundle.stage2_final_candidate.v1.json"
)
STAGE3A_PATH = Path(
    r"C:\ec\_calibration_workspaces\stage3a_camera_world_20260818_01"
    r"\candidate\camera_gravity.stage3a_candidate.v1.json"
)
REGISTRY_PATH = WORKSPACE / "registry.json"
TRUTH_PATH = WORKSPACE / "scene_truth.stage3b.json"
AUTHORITIES_PATH = WORKSPACE / "frozen_authorities.json"
RESULTS_ROOT = WORKSPACE / "results"
EXPORT_ROOT = WORKSPACE / "exports"
PROFILE_ROOT = PIPELINE_ROOT / "profiles"
PIPELINE_SRC = PIPELINE_ROOT / "src"

if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))
if str(PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(PIPELINE_SRC))

from stage2_dynamic_fr_adapter import (  # noqa: E402
    EXPECTED_STAGE2_SHA256,
    dynamic_fr_from_tof,
    load_stage2_bundle,
    reference_parity,
    sha256_file,
    stage2_camera_model,
)
from target_matching import (  # noqa: E402
    TARGET_ORDER,
    match_targets,
    observations_from_package,
    observations_from_raw,
)
from z_only_geometry_adapter import (  # noqa: E402
    load_gravity_candidate,
    z_only_points_from_depth,
)


class Stage3BError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"
    fd, raw_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _new_registry() -> dict[str, Any]:
    return {
        "schema_version": "stage3b_dynamic_registry.v1",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "dynamic": True,
        "capture_count_limit": None,
        "distance_required": False,
        "samples": [],
    }


def load_registry() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        registry = _new_registry()
        atomic_json(REGISTRY_PATH, registry)
        return registry
    registry = read_json(REGISTRY_PATH)
    if registry.get("schema_version") != "stage3b_dynamic_registry.v1":
        raise Stage3BError("registry schema mismatch")
    if registry.get("dynamic") is not True or registry.get("capture_count_limit") is not None:
        raise Stage3BError("registry is not dynamic/unbounded")
    if not isinstance(registry.get("samples"), list):
        raise Stage3BError("registry samples must be an array")
    return registry


def save_registry(registry: dict[str, Any]) -> None:
    registry["updated_at"] = utc_now()
    atomic_json(REGISTRY_PATH, registry)


def verify_authorities() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    stage2 = load_stage2_bundle(STAGE2_PATH)
    stage3a, stage3a_sha = load_gravity_candidate(STAGE3A_PATH)
    truth = read_json(TRUTH_PATH)
    expected_targets = {"upper_bottle", "lower_bottle", "lower_volleyball"}
    if set(truth.get("targets", {})) != expected_targets:
        raise Stage3BError("scene truth target set mismatch")
    manifest = {
        "schema_version": "stage3b_frozen_authorities.v1",
        "verified_at": utc_now(),
        "stage2": {
            "path": str(STAGE2_PATH),
            "sha256": sha256_file(STAGE2_PATH),
            "expected_sha256": EXPECTED_STAGE2_SHA256,
            "sha_verified": True,
            "bundle_status": stage2["bundle_status"],
            "runtime_replaced": stage2["deltar_fr"]["runtime_replaced"],
        },
        "stage3a": {
            "path": str(STAGE3A_PATH),
            "sha256": stage3a_sha,
            "status": stage3a["status"],
            "gravity_status": stage3a["gravity_status"],
            "yaw_status": stage3a["yaw_status"],
            "u_up_camera": stage3a["gravity_authority"]["u_up_camera"],
            "uncertainty_deg": stage3a["gravity_authority"]["uncertainty_deg"],
            "camera_height_mm": stage3a["camera_height_mm"],
            "camera_height_uncertainty_mm": stage3a[
                "camera_height_uncertainty_mm"
            ],
            "full_world_rotation_authority": False,
        },
        "world_z_authority": "Stage3A gravity candidate",
        "horizontal_world_authority": "NOT_VALIDATED",
        "scene_truth": {
            "path": str(TRUTH_PATH),
            "sha256": sha256_file(TRUTH_PATH),
            "measurement_method": truth["measurement_method"],
        },
        "promotion": False,
    }
    atomic_json(AUTHORITIES_PATH, manifest)
    return stage2, stage3a, truth


def _scan_created_key(path: Path) -> tuple[str, int, str]:
    created = ""
    scan_json = path / "scan.json"
    try:
        created = str(read_json(scan_json).get("created_at") or "")
    except Exception:
        pass
    return created, path.stat().st_mtime_ns, path.name


def latest_validated_scan() -> Path:
    if not VALIDATED_ROOT.is_dir():
        raise Stage3BError(f"validated scan root is missing: {VALIDATED_ROOT}")
    candidates = [
        item
        for item in VALIDATED_ROOT.iterdir()
        if item.is_dir()
        and (item / "scan.json").is_file()
        and (item / ".validation_receipt.json").is_file()
    ]
    if not candidates:
        raise Stage3BError("no receipted validated formal scan exists")
    return max(candidates, key=_scan_created_key).resolve()


def _hash_scan_sources(scan_dir: Path) -> dict[str, str]:
    names = [".validation_receipt.json", "scan.json", "service_job.json"]
    paths = [scan_dir / name for name in names if (scan_dir / name).is_file()]
    frames_root = scan_dir / "frames"
    if frames_root.is_dir():
        for frame_dir in sorted(item for item in frames_root.iterdir() if item.is_dir()):
            for name in ("frame.json", "tof.json", "image.rgb565"):
                path = frame_dir / name
                if path.is_file():
                    paths.append(path)
    return {
        str(path.relative_to(scan_dir)).replace("/", "\\"): sha256_file(path)
        for path in paths
    }


def _verify_registered_sources(sample: dict[str, Any]) -> Path:
    scan_dir = Path(sample["scan_path"]).resolve(strict=True)
    if scan_dir.name != sample["scan_id"]:
        raise Stage3BError("registered scan path/id mismatch")
    actual = _hash_scan_sources(scan_dir)
    if actual != sample["source_hashes"]:
        raise Stage3BError(f"registered scan source hashes changed: {sample['scan_id']}")
    return scan_dir


def register_latest() -> dict[str, Any]:
    stage2, stage3a, truth = verify_authorities()
    del stage2, stage3a, truth
    registry = load_registry()
    scan = latest_validated_scan()
    scan_id = scan.name
    duplicates = [item for item in registry["samples"] if item["scan_id"] == scan_id]
    if duplicates:
        raise Stage3BError(
            f"duplicate scan rejected (fail closed): {scan_id} is already {duplicates[0]['sample_id']}"
        )
    ordinal = len(registry["samples"]) + 1
    sample_id = f"G{ordinal:03d}"
    role = "PRIMARY_COMPETITION_VIEW" if ordinal == 1 else "ROBUSTNESS_EXTRA"
    sample = {
        "sample_id": sample_id,
        "role": role,
        "scan_id": scan_id,
        "scan_path": str(scan),
        "registered_at": utc_now(),
        "source_hashes": _hash_scan_sources(scan),
        "authority_hashes": {
            "stage2": sha256_file(STAGE2_PATH),
            "stage3a": sha256_file(STAGE3A_PATH),
            "scene_truth": sha256_file(TRUTH_PATH),
        },
        "analysis_status": "PENDING",
        "result_path": None,
        "failure": None,
    }
    registry["samples"].append(sample)
    save_registry(registry)
    print(f"REGISTERED {sample_id} = {scan_id}")
    print(f"ROLE = {role}")
    return analyze_sample(sample_id)


def _jsonable_planes(planes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for plane in planes:
        result.append({key: value for key, value in plane.items() if key != "_mask"})
    return result


def _run_formal_candidate_chain(
    scan_dir: Path,
    output_dir: Path,
    sample_id: str,
    stage2: dict[str, Any],
    stage3a: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Run formal decode/YOLO/DELTAR plus validation-only dynamic/Z adapters."""
    from deltar_p4_pipeline.all_object_geometry import (
        AllObjectGeometryProcessor,
        load_geometry_profile,
    )
    from deltar_p4_pipeline.frame_pipeline import FramePipeline
    from deltar_p4_pipeline.horizontal_planes import extract_horizontal_planes
    from deltar_p4_pipeline.scan_contracts import atomic_write_json
    from deltar_p4_pipeline.scan_profiles import ProfileBundle, load_profile_bundle
    from deltar_p4_pipeline.stage8b4a_package import (
        P4_PACKAGE_FILENAME,
        build_and_write_stage8b4a,
    )
    from deltar_p4_pipeline.validation_receipt import load_receipted_scan

    class ZOnlyStage7DProcessor:
        def __init__(self) -> None:
            self.frame_records: list[dict[str, Any]] = []
            self.profile = read_json(PROFILE_ROOT / "stage7d_horizontal_plane_profile.json")
            self.camera_model = stage2_camera_model(stage2)

        def process_frame(
            self,
            frame_id: str,
            source_rgb: np.ndarray,
            depth_m: np.ndarray,
            *,
            source_rgb_path: Path,
        ) -> dict[str, Any]:
            del source_rgb
            compatible, axial, valid = z_only_points_from_depth(
                depth_m,
                self.camera_model,
                stage3a,
                min_depth_m=float(self.profile["min_depth_m"]),
                max_depth_m=float(self.profile["max_depth_m"]),
            )
            extraction = extract_horizontal_planes(compatible, valid, self.profile)
            planes = extraction["planes"]
            record = {
                "frame_id": frame_id,
                "world_z_authority": "Stage3A gravity candidate dot product",
                "horizontal_world_authority": "NOT_VALIDATED",
                "source_rgb_path": str(source_rgb_path),
                "valid_depth_count": int(np.count_nonzero(valid)),
                "support_planes": _jsonable_planes(planes),
                "rejected_candidates": _jsonable_planes(
                    extraction.get("rejected_candidates", [])
                ),
                "warnings": extraction.get("warnings", []),
                "world_height_statistics": extraction.get(
                    "world_height_statistics", {}
                ),
                "_stage7d_timing": {
                    "stage7d_total_ms": 0.0,
                    "stage7d_diagnostics_enabled": False,
                },
                "_stage7e_selected": {
                    "camera_axial_z_mm": axial,
                    "world_points_mm": compatible,
                    "support_planes": planes,
                },
            }
            public = {
                key: value
                for key, value in record.items()
                if not key.startswith("_stage")
            }
            self.frame_records.append(public)
            frame_root = output_dir / "z_only_stage7d" / frame_id
            frame_root.mkdir(parents=True, exist_ok=True)
            atomic_json(frame_root / "horizontal_planes.stage3b.json", public)
            return record

    class DynamicFramePipeline(FramePipeline):
        def __post_init__(self) -> None:
            super().__post_init__()
            import threading

            self._stage3b_state = threading.local()
            self.dynamic_fr_audit: list[dict[str, Any]] = []

        def _run_depth(self, rgb: np.ndarray, tof: dict[str, Any]):
            self._stage3b_state.tof = tof
            try:
                return super()._run_depth(rgb, tof)
            finally:
                self._stage3b_state.tof = None

        def _adapter_profile(self, canvas_strategy: str) -> dict[str, Any]:
            profile = super()._adapter_profile(canvas_strategy)
            tof = getattr(self._stage3b_state, "tof", None)
            if tof is None:
                raise Stage3BError("dynamic fr requested without current frame ToF")
            values, audit = dynamic_fr_from_tof(stage2, tof, canvas_strategy)
            profile["fr"] = {
                "strategy": "explicit_model_canvas",
                "values": values.tolist(),
                "authority": "Stage2 final bundle per-frame dynamic projection",
            }
            self.dynamic_fr_audit.append(audit)
            return profile

    validated = load_receipted_scan(scan_dir)
    if int(validated.scan.get("frame_count", -1)) != 11 or len(validated.frames) != 11:
        raise Stage3BError("formal Stage3B replay requires one intact 11-frame formal scan")
    bundle = load_profile_bundle(PROFILE_ROOT, validate_model_hashes=True)
    validation_calibration = copy.deepcopy(bundle.calibration)
    validation_calibration["profile_id"] = "stage3b_stage2_camera_z_only_validation"
    validation_calibration["camera_model"] = stage2_camera_model(stage2)
    validation_calibration["depth"] = {
        "semantics": "axial_z",
        "semantics_status": "STAGE2_CANDIDATE_VALIDATION_ONLY",
        "scale_to_meter": 1.0,
    }
    validation_calibration["calibration_status"] = "unverified"
    validation_calibration["absolute_geometry_valid"] = False
    validation_calibration["physical_height_valid"] = False
    validation_calibration["bottom_height_valid"] = False
    bundle = ProfileBundle(
        bundle.root,
        bundle.model_manifest,
        bundle.algorithm,
        bundle.scan_strategy,
        validation_calibration,
        bundle.zone_orientation,
        bundle.hashes,
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "logs").mkdir()
    (output_dir / "frame_results").mkdir()
    z_processor = ZOnlyStage7DProcessor()
    geometry_processor = AllObjectGeometryProcessor(
        output_dir,
        str(validated.scan["scan_id"]),
        11,
        load_geometry_profile(PROFILE_ROOT / "all_object_geometry_profile.json"),
    )
    pipeline = DynamicFramePipeline(
        bundle,
        output_dir,
        device="cuda:0",
        stage7d_processor=z_processor,
        all_object_geometry_processor=geometry_processor,
    )
    pipeline.prepare_scan_yolo(str(validated.scan["scan_id"]), validated.frames)
    frame_failures = []
    for frame in validated.frames:
        before = len(geometry_processor.frames)
        try:
            result = pipeline.process(frame)
            atomic_write_json(
                output_dir / "frame_results" / f"{frame.frame['frame_id']}.json",
                result,
            )
        except Exception as exc:
            failure = {
                "frame_id": frame.frame["frame_id"],
                "sequence": frame.frame["sequence"],
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            frame_failures.append(failure)
            atomic_json(
                output_dir / "frame_results" / f"{frame.frame['frame_id']}.error.json",
                failure,
            )
            if len(geometry_processor.frames) == before:
                geometry_processor.add_failed_frame_objects(
                    str(frame.frame["frame_id"]),
                    int(frame.frame["sequence"]),
                    [],
                    f"frame_runtime_exception:{type(exc).__name__}",
                    None,
                )
    raw_path = geometry_processor.write()
    raw_document = read_json(raw_path)
    atomic_json(output_dir / "dynamic_fr_audit.json", {
        "schema_version": "stage3b_dynamic_fr_runtime_audit.v1",
        "entry_count": len(pipeline.dynamic_fr_audit),
        "entries": pipeline.dynamic_fr_audit,
    })
    atomic_json(output_dir / "frame_failures.json", {
        "schema_version": "stage3b_frame_failures.v1",
        "failure_count": len(frame_failures),
        "failures": frame_failures,
    })
    package_error = None
    package_document: dict[str, Any] = {
        "schema_version": "stage8b4a_p4_decision_package.v1",
        "objects": [],
    }
    try:
        build_and_write_stage8b4a(output_dir, repository_root=PIPELINE_ROOT)
        package_document = read_json(output_dir / P4_PACKAGE_FILENAME)
    except Exception as exc:
        package_error = {
            "error_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        atomic_json(output_dir / "stabilized_secondary_error.json", package_error)
    chain = {
        "schema_version": "stage3b_candidate_chain_execution.v1",
        "sample_id": sample_id,
        "scan_id": validated.scan["scan_id"],
        "formal_frame_count": len(validated.frames),
        "frame_failure_count": len(frame_failures),
        "raw_geometry_path": str(raw_path),
        "raw_geometry_authority": "RAW_GEOMETRY_BEFORE_STABILIZER",
        "stabilized_secondary_available": package_error is None,
        "stabilized_secondary_error": package_error,
        "stage2_dynamic_fr_runtime_entries": len(pipeline.dynamic_fr_audit),
        "world_z_authority": "Stage3A gravity candidate",
        "horizontal_world_authority": "NOT_VALIDATED",
        "full_world_rotation_constructed": False,
    }
    atomic_json(output_dir / "candidate_chain_execution.json", chain)
    return raw_document, package_document, chain


def _tof_scene_diagnostic(scan_dir: Path) -> dict[str, Any]:
    values = []
    valid_counts = []
    for path in sorted((scan_dir / "frames").glob("*/tof.json")):
        tof = read_json(path)
        distance = np.asarray(tof.get("distance_mm"), dtype=np.float64)
        count = np.asarray(tof.get("nb_target_detected"))
        status = np.asarray(tof.get("target_status"))
        valid = (
            np.isfinite(distance)
            & (distance > 0)
            & (count != 0)
            & (status != 255)
        )
        values.extend(distance[valid].tolist())
        valid_counts.append(int(np.count_nonzero(valid)))
    array = np.asarray(values, dtype=np.float64)
    return {
        "distance_is_diagnostic_only": True,
        "tof_valid_zone_observations": int(array.size),
        "tof_valid_zones_per_frame": valid_counts,
        "tof_axial_z_mm": {
            "median": None if not array.size else float(np.median(array)),
            "p05": None if not array.size else float(np.percentile(array, 5)),
            "p95": None if not array.size else float(np.percentile(array, 95)),
        },
    }


def _acceptance(raw_match: dict[str, Any], role: str) -> dict[str, Any]:
    targets = raw_match["targets"]
    resolved = all(item.get("match_status") == "RESOLVED" for item in targets.values())
    detected = all(item.get("detected") for item in targets.values())
    catastrophic_reasons = []
    for name, item in targets.items():
        if not item.get("detected"):
            continue
        support = item["support_height_mm"]["median"]
        height = item["object_height_mm"]["median"]
        if support is None or support < -100.0:
            catastrophic_reasons.append(f"{name}:support_sign_or_nonfinite")
        if item["support_error"]["absolute_mm"] >= 300.0:
            catastrophic_reasons.append(f"{name}:support_error_hundreds_mm")
        if height is None or height <= 0.0 or item["object_height_error"]["absolute_mm"] >= 300.0:
            catastrophic_reasons.append(f"{name}:object_height_catastrophic")
    lower_consistency = None
    lower_bottle = targets["lower_bottle"]
    lower_ball = targets["lower_volleyball"]
    if lower_bottle.get("detected") and lower_ball.get("detected"):
        lower_consistency = abs(
            lower_bottle["support_height_mm"]["median"]
            - lower_ball["support_height_mm"]["median"]
        )
    preferred = resolved and all(
        item["support_error"]["absolute_mm"] <= 20.0
        and item["object_height_error"]["percent"] <= 5.0
        for item in targets.values()
    )
    acceptable = resolved and all(
        item["support_error"]["absolute_mm"] <= 50.0
        and item["object_height_error"]["percent"] <= 10.0
        for item in targets.values()
    )
    if role == "PRIMARY_COMPETITION_VIEW":
        if catastrophic_reasons or not detected or not resolved:
            decision = "REVIEW"
            owner_state = "NOT_READY"
        elif acceptable and (lower_consistency is None or lower_consistency <= 50.0):
            decision = "GO"
            owner_state = "PRIMARY_READY_FOR_OWNER_JUDGMENT"
        else:
            decision = "REVIEW"
            owner_state = "PRIMARY_READY_FOR_OWNER_JUDGMENT"
    else:
        decision = (
            "CATASTROPHIC_BLOCKER" if catastrophic_reasons else "ROBUSTNESS_DIAGNOSTIC"
        )
        owner_state = "NOT_PRIMARY"
    return {
        "role": role,
        "decision": decision,
        "owner_judgment_state": owner_state,
        "all_targets_detected": detected,
        "all_targets_resolved": resolved,
        "preferred_band_all_targets": preferred,
        "competition_acceptable_band_all_targets": acceptable,
        "same_lower_support_difference_mm": lower_consistency,
        "catastrophic_geometry": bool(catastrophic_reasons),
        "catastrophic_reasons": catastrophic_reasons,
        "thresholds_are_engineering_bands_not_single_hard_gate": True,
    }


def _print_target(name: str, item: dict[str, Any]) -> None:
    print(f"{name}:")
    print(f"  detected = {str(bool(item.get('detected'))).lower()}")
    print(f"  match = {item.get('match_status')}")
    if not item.get("detected"):
        print(f"  reason = {item.get('unresolved_reason')}")
        return
    support = item["support_height_mm"]["median"]
    height = item["object_height_mm"]["median"]
    support_truth = item["truth"]["expected_support_mm"]
    height_truth = item["truth"]["expected_object_height_mm"]
    print(f"  support_source = {item['support_source']}")
    print(
        f"  support = {support:.1f} mm (truth {support_truth:.1f}, "
        f"signed error {item['support_error']['signed_mm']:+.1f}, "
        f"abs {item['support_error']['absolute_mm']:.1f}, "
        f"{item['support_error']['percent']:.2f}%)"
    )
    print(
        f"  object_height = {height:.1f} mm (truth {height_truth:.1f}, "
        f"signed error {item['object_height_error']['signed_mm']:+.1f}, "
        f"abs {item['object_height_error']['absolute_mm']:.1f}, "
        f"{item['object_height_error']['percent']:.2f}%)"
    )
    print(
        f"  frames = {item['valid_frame_count']}, source_consistent = "
        f"{str(item['source_consistent']).lower()}"
    )


def print_sample_result(result: dict[str, Any]) -> None:
    print(f"SAMPLE = {result['sample_id']}")
    print(f"ROLE = {result['role']}")
    print("PRIMARY AUTHORITY = RAW_GEOMETRY_BEFORE_STABILIZER")
    for name in TARGET_ORDER:
        _print_target(name, result["raw"]["targets"][name])
    label = "PRIMARY_ACCEPTANCE" if result["role"] == "PRIMARY_COMPETITION_VIEW" else "ROBUSTNESS_RESULT"
    print(f"{label} = {result['acceptance']['decision']}")
    print(f"OWNER_STATE = {result['acceptance']['owner_judgment_state']}")
    print(f"FULL_RESULT = {result['result_path']}")


def analyze_sample(sample_id: str) -> dict[str, Any]:
    stage2, stage3a, truth = verify_authorities()
    parity = reference_parity(stage2)
    if not parity["all_pass"]:
        raise Stage3BError("Stage2 dynamic fr reference parity failed")
    atomic_json(WORKSPACE / "diagnostics" / "dynamic_fr_reference_parity.json", parity)
    registry = load_registry()
    matches = [item for item in registry["samples"] if item["sample_id"] == sample_id]
    if len(matches) != 1:
        raise Stage3BError(f"sample is not uniquely registered: {sample_id}")
    sample = matches[0]
    scan_dir = _verify_registered_sources(sample)
    if sample["authority_hashes"] != {
        "stage2": sha256_file(STAGE2_PATH),
        "stage3a": sha256_file(STAGE3A_PATH),
        "scene_truth": sha256_file(TRUTH_PATH),
    }:
        raise Stage3BError("registered authority hashes changed")
    sample_root = RESULTS_ROOT / sample_id
    attempt = 1
    while (sample_root / f"candidate_chain_attempt_{attempt:02d}").exists():
        attempt += 1
    output_dir = sample_root / f"candidate_chain_attempt_{attempt:02d}"
    sample["analysis_status"] = "RUNNING"
    sample["failure"] = None
    save_registry(registry)
    try:
        raw_document, package_document, chain = _run_formal_candidate_chain(
            scan_dir, output_dir, sample_id, stage2, stage3a
        )
        raw_match = match_targets(observations_from_raw(raw_document), truth)
        stabilized_match = match_targets(
            observations_from_package(package_document), truth
        )
        acceptance = _acceptance(raw_match, sample["role"])
        result_path = RESULTS_ROOT / sample_id / "sample_result.stage3b.json"
        result = {
            "schema_version": "stage3b_sample_result.v1",
            "generated_at": utc_now(),
            "sample_id": sample_id,
            "role": sample["role"],
            "scan_id": sample["scan_id"],
            "scan_path": sample["scan_path"],
            "raw_geometry_authority": "RAW_GEOMETRY_BEFORE_STABILIZER",
            "world_z_authority": "Stage3A gravity candidate",
            "horizontal_world_authority": "NOT_VALIDATED",
            "full_world_rotation_constructed": False,
            "raw": raw_match,
            "stabilized_secondary": stabilized_match,
            "acceptance": acceptance,
            "scene_depth_diagnostic": _tof_scene_diagnostic(scan_dir),
            "candidate_chain": chain,
            "source_hashes": sample["source_hashes"],
            "authority_hashes": sample["authority_hashes"],
            "result_path": str(result_path),
        }
        atomic_json(result_path, result)
        sample["analysis_status"] = "COMPLETE"
        sample["result_path"] = str(result_path)
        sample["failure"] = None
        save_registry(registry)
        print_sample_result(result)
        return result
    except Exception as exc:
        sample["analysis_status"] = "FAILED"
        sample["failure"] = {
            "at": utc_now(),
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        save_registry(registry)
        raise


def analyze_pending() -> None:
    registry = load_registry()
    pending = [
        item["sample_id"]
        for item in registry["samples"]
        if item.get("analysis_status") in {"PENDING", "FAILED"}
    ]
    if not pending:
        if registry["samples"] and registry["samples"][-1].get("result_path"):
            print_sample_result(read_json(Path(registry["samples"][-1]["result_path"])))
            return
        print("NO_REGISTERED_SAMPLE_TO_ANALYZE")
        return
    for sample_id in pending:
        analyze_sample(sample_id)


def status() -> None:
    verify_authorities()
    registry = load_registry()
    counts = Counter(item.get("analysis_status") for item in registry["samples"])
    print("STAGE3B_TOOL_STATUS = READY_FOR_CAPTURE")
    print(f"REGISTERED_SAMPLE_COUNT = {len(registry['samples'])}")
    print("REGISTRY_MODE = DYNAMIC_UNBOUNDED")
    print("REAL_STAGE3B_CAPTURE_REGISTERED = " + str(bool(registry["samples"])).lower())
    print("ANALYSIS_COUNTS = " + json.dumps(dict(sorted(counts.items())), sort_keys=True))
    if registry["samples"]:
        latest = registry["samples"][-1]
        print(
            f"LATEST_REGISTERED = {latest['sample_id']} {latest['role']} "
            f"{latest['scan_id']} {latest['analysis_status']}"
        )
    else:
        candidate = latest_validated_scan()
        print(f"LATEST_VALIDATED_NOT_REGISTERED = {candidate.name}")
        print("NOTE = Do not register until the competition cabinet capture is complete.")


def inspect_latest() -> None:
    registry = load_registry()
    if registry["samples"]:
        latest = registry["samples"][-1]
        print(json.dumps(latest, ensure_ascii=False, indent=2, sort_keys=True))
        if latest.get("result_path"):
            print_sample_result(read_json(Path(latest["result_path"])))
        return
    candidate = latest_validated_scan()
    print("REGISTRY_EMPTY = true")
    print(f"LATEST_VALIDATED_CANDIDATE = {candidate}")
    print("REGISTERED = false")


def build_summary() -> dict[str, Any]:
    registry = load_registry()
    samples = []
    for item in registry["samples"]:
        result = None
        if item.get("result_path") and Path(item["result_path"]).is_file():
            result = read_json(Path(item["result_path"]))
        samples.append(
            {
                "sample_id": item["sample_id"],
                "role": item["role"],
                "scan_id": item["scan_id"],
                "analysis_status": item["analysis_status"],
                "result": result,
            }
        )
    primary = next(
        (item for item in samples if item["role"] == "PRIMARY_COMPETITION_VIEW"),
        None,
    )
    robustness = [item for item in samples if item["role"] == "ROBUSTNESS_EXTRA"]
    blockers = [
        item["sample_id"]
        for item in robustness
        if item["result"]
        and item["result"]["acceptance"]["catastrophic_geometry"]
    ]
    if primary is None:
        overall = "WAITING_FOR_PRIMARY_CAPTURE"
    elif primary["result"] is None:
        overall = "PRIMARY_ANALYSIS_INCOMPLETE"
    elif blockers:
        overall = "REVIEW_CATASTROPHIC_ROBUSTNESS_REGRESSION"
    else:
        overall = primary["result"]["acceptance"]["owner_judgment_state"]
    summary = {
        "schema_version": "stage3b_primary_robustness_summary.v1",
        "generated_at": utc_now(),
        "PRIMARY_RESULT": primary,
        "ROBUSTNESS_RESULTS": robustness,
        "OVERALL_DIAGNOSTIC": {
            "status": overall,
            "catastrophic_robustness_blockers": blockers,
            "robustness_not_averaged_into_primary": True,
        },
    }
    atomic_json(WORKSPACE / "results" / "SUMMARY.stage3b.json", summary)
    return summary


def summary_command() -> None:
    summary = build_summary()
    primary = summary["PRIMARY_RESULT"]
    print("PRIMARY")
    if primary is None:
        print("  WAITING_FOR_PRIMARY_CAPTURE")
    elif primary["result"] is None:
        print(f"  {primary['sample_id']} = {primary['analysis_status']}")
    else:
        print_sample_result(primary["result"])
    print("ROBUSTNESS")
    extras = summary["ROBUSTNESS_RESULTS"]
    if not extras:
        print("  NO_EXTRA_SAMPLES (optional)")
    for item in extras:
        decision = (
            item["result"]["acceptance"]["decision"]
            if item["result"]
            else item["analysis_status"]
        )
        depth = (
            item["result"]["scene_depth_diagnostic"]["tof_axial_z_mm"]["median"]
            if item["result"]
            else None
        )
        print(f"  {item['sample_id']} {item['scan_id']} = {decision}; depth_proxy_mm={depth}")
    print(
        "OVERALL_DIAGNOSTIC = "
        + summary["OVERALL_DIAGNOSTIC"]["status"]
    )


def export_results() -> Path:
    verify_authorities()
    summary = build_summary()
    del summary
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = EXPORT_ROOT / f"stage3b_results_{stamp}.zip"
    included_roots = [
        REGISTRY_PATH,
        TRUTH_PATH,
        AUTHORITIES_PATH,
        WORKSPACE / "results" / "SUMMARY.stage3b.json",
    ]
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in included_roots:
            if path.is_file():
                archive.write(path, path.relative_to(WORKSPACE))
        for path in sorted(RESULTS_ROOT.rglob("*.json")):
            archive.write(path, path.relative_to(WORKSPACE))
        for path in sorted((WORKSPACE / "diagnostics").glob("*.json")):
            archive.write(path, path.relative_to(WORKSPACE))
    print(f"EXPORTED_RESULTS = {destination}")
    return destination


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Stage3B dynamic competition geometry validation (all commands take no values)"
    )
    sub = value.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("status", "verify authorities and show dynamic registry status"),
        ("register-latest", "register the latest validated scan and analyze it"),
        ("inspect-latest", "inspect latest registration or unregistered candidate"),
        ("analyze", "analyze every pending/failed registered sample"),
        ("summary", "show primary separately from optional robustness samples"),
        ("export-results", "export compact JSON results without raw imagery/depth"),
    ):
        sub.add_parser(name, help=help_text)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "status":
            status()
        elif args.command == "register-latest":
            register_latest()
        elif args.command == "inspect-latest":
            inspect_latest()
        elif args.command == "analyze":
            analyze_pending()
        elif args.command == "summary":
            summary_command()
        elif args.command == "export-results":
            export_results()
        return 0
    except Exception as exc:
        print(f"STAGE3B_ERROR = {type(exc).__name__}: {exc}", file=sys.stderr)
        if os.environ.get("STAGE3B_DEBUG") == "1":
            traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
