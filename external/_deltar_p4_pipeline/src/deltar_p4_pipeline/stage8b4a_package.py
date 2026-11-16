from __future__ import annotations

import copy
import json
import logging
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .geometry_contracts import ARTIFACT_FILENAME, validate_geometry_document
from .dual_gate_selection import (
    SELECTION_POLICY_ID,
    build_and_write_dual_gate,
)
from .scan_contracts import (
    ScanContractError,
    atomic_write_json,
    canonical_sha256,
    load_strict_json,
    sha256_file,
)
from .selected_object_projection import (
    SELECTED_OBJECTS_FILENAME,
    build_and_write_selected_objects,
    project_selected_items,
    validate_projection_invariants,
    validate_selected_objects_document,
)
from .stage8b4a_contracts import (
    DIAGNOSTICS_FILENAME,
    MAX_PACKAGE_BYTES,
    P4_PACKAGE_FILENAME,
    WHITELIST_PROFILE_FILENAME,
    WHITELIST_CLASSES,
    validate_p4_package,
)


_LOGGER = logging.getLogger(__name__)

def _atomic_publish_package(path: Path, package: dict[str, Any]) -> None:
    payload = (
        json.dumps(
            package,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    fd, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".unpublished", dir=str(path.parent)
    )
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        candidate = json.loads(
            temporary.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
        validate_p4_package(candidate)
        if candidate.get("scan_id") != package.get("scan_id"):
            raise ScanContractError(
                "E_STAGE8B4A_PACKAGE_ID",
                "temporary package scan_id changed before publication",
                "scan_id",
            )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_p4_package(
    geometry_document: dict[str, Any],
    selected_document: dict[str, Any],
    *,
    selected_objects_sha256: str,
    metadata_adapter_sha256: str,
    whitelist_profile_sha256: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    validate_geometry_document(geometry_document)
    validate_selected_objects_document(selected_document)
    if selected_document["scan_id"] != geometry_document["scan_id"]:
        raise ScanContractError(
            "E_STAGE8B4A_SELECTION_PROJECTION",
            "selected-object and geometry scan IDs differ",
            "scan_id",
        )
    package_objects = project_selected_items(selected_document)
    summaries: list[dict[str, Any]] = []
    for class_name in WHITELIST_CLASSES:
        class_objects = [
            item for item in package_objects if item["class_name"] == class_name
        ]
        summaries.append(
            {
                "class_name": class_name,
                "present": bool(class_objects),
                "observation_count": len(class_objects),
                "fused_object_count": len(class_objects),
                "selected_object_id": (
                    class_objects[0]["object_id"] if len(class_objects) == 1 else None
                ),
            }
        )
    package = {
        "schema_version": "stage8b4a_p4_decision_package.v1",
        "scan_id": geometry_document["scan_id"],
        "status": "decision_ready",
        "generated_at": generated_at or geometry_document["generated_at"],
        "whitelist": {
            "classes": list(WHITELIST_CLASSES),
            "profile_sha256": whitelist_profile_sha256,
        },
        "source": {
            "frame_count": int(geometry_document["frame_count"]),
            # These field names are frozen by the current PC/P4 packet contract.
            # Their values now bind the exact metadata adapter and selected-v2
            # artifacts; neither source decides gate membership or geometry.
            "candidate_set_sha256": metadata_adapter_sha256,
            "scene_objects_sha256": selected_objects_sha256,
            "scene_objects_semantic_sha256": canonical_sha256(
                selected_document["items"]
            ),
        },
        "selection_policy_id": SELECTION_POLICY_ID,
        "class_summaries": summaries,
        "objects": package_objects,
    }
    validate_p4_package(package)
    validate_projection_invariants(selected_document, package)
    encoded = json.dumps(
        package,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) + 1 > MAX_PACKAGE_BYTES:
        raise ScanContractError(
            "E_STAGE8B4A_PACKAGE_TOO_LARGE",
            "P4 decision package exceeds 64 KiB; no truncation was performed",
            "objects",
            {"encoded_bytes": len(encoded) + 1, "limit_bytes": MAX_PACKAGE_BYTES},
        )
    return package


def _write_sidecar(package_path: Path) -> tuple[Path, str]:
    package_sha256 = sha256_file(package_path)
    sidecar_path = package_path.with_name(package_path.name + ".sha256")
    fd, name = tempfile.mkstemp(
        prefix=f".{sidecar_path.name}.", suffix=".tmp", dir=str(sidecar_path.parent)
    )
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as handle:
            handle.write(package_sha256 + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, sidecar_path)
    finally:
        temporary.unlink(missing_ok=True)
    return sidecar_path, package_sha256


def build_and_write_stage8b4a(
    job_output: Path,
    *,
    repository_root: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    job_output = job_output.resolve()
    repository_root = repository_root.resolve()
    geometry_path = job_output / ARTIFACT_FILENAME
    geometry_document = load_strict_json(geometry_path, code="E_STAGE8B4A_INPUT")
    validate_geometry_document(geometry_document)
    whitelist_path = repository_root / "profiles" / WHITELIST_PROFILE_FILENAME

    gate_document, gate_path = build_and_write_dual_gate(
        job_output, whitelist_path
    )
    if set(gate_document["whitelist_profile"]["classes"]) != set(WHITELIST_CLASSES):
        raise ScanContractError(
            "E_STAGE8B4A_WHITELIST",
            "the current P4 packet boundary requires book/cup/bottle/sports ball",
            "whitelist_profile.classes",
        )
    selected_document, selected_path, metadata_source = (
        build_and_write_selected_objects(job_output, gate_document, gate_path)
    )
    selected_sha256 = sha256_file(selected_path)
    whitelist_sha256 = sha256_file(whitelist_path)
    package = build_p4_package(
        geometry_document,
        selected_document,
        selected_objects_sha256=selected_sha256,
        metadata_adapter_sha256=metadata_source["sha256"],
        whitelist_profile_sha256=whitelist_sha256,
    )
    package_path = job_output / P4_PACKAGE_FILENAME
    _atomic_publish_package(package_path, package)
    sidecar_path, package_sha256 = _write_sidecar(package_path)

    diagnostics = {
        "schema_version": "stage8b_dual_gate_diagnostics.v1",
        "scan_id": geometry_document["scan_id"],
        "selection_policy_id": SELECTION_POLICY_ID,
        "authority": {
            "path": ARTIFACT_FILENAME,
            "sha256": sha256_file(geometry_path),
        },
        "dual_gate": {
            "path": gate_path.name,
            "sha256": sha256_file(gate_path),
            "object_count": len(gate_document["objects"]),
        },
        "selected": {
            "path": SELECTED_OBJECTS_FILENAME,
            "sha256": selected_sha256,
            "object_count": len(selected_document["items"]),
        },
        "metadata_adapter": metadata_source,
        "sanitizer": selected_document["sanitizer"],
        "packet": {
            "path": P4_PACKAGE_FILENAME,
            "sha256": package_sha256,
            "bytes": package_path.stat().st_size,
            "object_count": len(package["objects"]),
        },
        "inference_invocations": {
            "yolo_detect": 0,
            "yolo_segment": 0,
            "deltar": 0,
            "stage7d_recompute": 0,
        },
    }
    diagnostics_path = job_output / DIAGNOSTICS_FILENAME
    atomic_write_json(diagnostics_path, diagnostics)
    return {
        "gate_document": gate_document,
        "gate_path": gate_path,
        "selected_document": selected_document,
        "selected_path": selected_path,
        "package": package,
        "package_path": package_path,
        "package_bytes": package_path.stat().st_size,
        "package_sha256": package_sha256,
        "sidecar_path": sidecar_path,
        "diagnostics": diagnostics,
        "diagnostics_path": diagnostics_path,
        "wall_clock_ms": (time.perf_counter() - started) * 1000.0,
    }
