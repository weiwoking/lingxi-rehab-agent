from __future__ import annotations

from pathlib import Path
from typing import Any

from .geometry_contracts import ARTIFACT_FILENAME, validate_geometry_document
from .dual_gate_selection import DUAL_GATE_FILENAME, validate_dual_gate_document
from .pc_scene_ui_renderer import (
    SCHEMA_VERSION,
    render_pc_scene_ui,
    validate_pc_scene_ui_input,
)
from .scan_contracts import atomic_write_json, load_strict_json
from .selected_object_projection import (
    SELECTED_OBJECTS_FILENAME,
    validate_selected_objects_document,
)
from .stage8b4a_contracts import validate_p4_package
from .stage8b4a_contracts import P4_PACKAGE_FILENAME


UI_INPUT_FILENAME = "pc_scene_ui_input.v1.json"


def _canonical_ui_objects(
    selected_document: dict[str, Any], canonical_package: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Bind each selected UI object to the exact canonical P4 package object."""

    validate_p4_package(canonical_package)
    if canonical_package["scan_id"] != selected_document["scan_id"]:
        raise ValueError("selected-object and canonical package scan IDs differ")
    selected_by_id = {
        str(item["object_id"]): item for item in selected_document["items"]
    }
    canonical_by_id = {
        str(item["object_id"]): item for item in canonical_package["objects"]
    }
    if set(selected_by_id) != set(canonical_by_id):
        raise ValueError("selected-object and canonical package object IDs differ")
    for object_id, selected in selected_by_id.items():
        canonical = canonical_by_id[object_id]
        for field in ("class_name", "source_frame_id"):
            if selected[field] != canonical[field]:
                raise ValueError(
                    f"selected-object and canonical package {field} differ: {object_id}"
                )
    return canonical_by_id


def build_pc_scene_ui_input(
    geometry_document: dict[str, Any],
    gate_document: dict[str, Any],
    selected_document: dict[str, Any],
    *,
    canonical_package: dict[str, Any] | None = None,
    frame_processing_status: dict[str, dict[str, Any]] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    validate_geometry_document(geometry_document)
    validate_dual_gate_document(gate_document)
    validate_selected_objects_document(selected_document)
    scan_ids = {
        geometry_document["scan_id"], gate_document["scan_id"], selected_document["scan_id"]
    }
    if len(scan_ids) != 1:
        raise ValueError("geometry, gate, and selected-object scan IDs differ")
    selected = {
        (str(item["source_frame_id"]), str(item["logical_object_id"])): item
        for item in selected_document["items"]
    }
    canonical_by_id = (
        _canonical_ui_objects(selected_document, canonical_package)
        if canonical_package is not None
        else {}
    )
    gates = {
        (str(item["source_frame_id"]), str(item["logical_object_id"])): item
        for item in gate_document["objects"]
    }
    selected_identities = [
        {
            "frame_id": str(item["source_frame_id"]),
            "logical_object_id": str(item["logical_object_id"]),
            "object_id": str(item["object_id"]),
        }
        for item in selected_document["items"]
    ]
    geometry_frames = {int(frame["sequence"]): frame for frame in geometry_document["frames"]}
    frames: list[dict[str, Any]] = []
    status_by_id = frame_processing_status or {}
    for sequence in range(11):
        frame = geometry_frames.get(sequence)
        frame_id = f"frame_{sequence:06d}" if frame is None else str(frame["frame_id"])
        processing_status = status_by_id.get(frame_id) or {
            "capture_status": "CAPTURE_OK" if frame is not None else "CAPTURE_FAILURE",
            "tof_status": "TOF_UNAVAILABLE",
            "deltar_status": "DELTAR_UNAVAILABLE",
            "detection_status": "DETECTION_UNAVAILABLE" if frame is None else "DETECTION_OK",
            "geometry_status": "GEOMETRY_UNAVAILABLE" if frame is None else (
                "GEOMETRY_OK" if frame.get("geometry_success_count", 0) else "GEOMETRY_UNAVAILABLE"
            ),
            "degradation_mode": "NOT_REPORTED",
            "reason_codes": ["processing_status_not_reported"],
            "tof_diagnostics": {},
        }
        if frame is None:
            frames.append(
                {
                    "frame_id": frame_id,
                    "sequence": sequence,
                    "source_image_path": None,
                    "processing_status": processing_status,
                    "objects": [],
                }
            )
            continue
        frame_id = str(frame["frame_id"])
        objects = []
        for item in frame["objects"]:
            identity = (frame_id, str(item["logical_object_id"]))
            gate = gates.get(identity)
            selected_item = selected.get(identity)
            canonical_item = (
                canonical_by_id[str(selected_item["object_id"])]
                if selected_item is not None and canonical_by_id
                else None
            )
            geometry = (
                canonical_item["geometry"] if canonical_item is not None else item["geometry"]
            )
            gate1_passed = bool(gate and gate["gate1_passed"])
            gate2_passed = bool(gate and gate["gate2_passed"])
            selected_for_p4 = selected_item is not None
            if selected_for_p4:
                display_level = "L1"
            elif gate1_passed:
                display_level = "L2"
            elif item["numeric_available"]:
                display_level = "L3"
            else:
                display_level = "L4"
            objects.append(
                {
                    "logical_object_id": str(item["logical_object_id"]),
                    "class_name": str(item["class_name"]),
                    "confidence": float(item["confidence"]),
                    "bbox_xyxy": [float(value) for value in item["bbox_xyxy"]],
                    "display_level": display_level,
                    "gate1_outcome": gate["gate1_outcome"] if gate else "blocked_failure",
                    "gate1_passed": gate1_passed,
                    "gate2_passed": gate2_passed,
                    "selected_for_p4": selected_for_p4,
                    "numeric_available": bool(item["numeric_available"]),
                    "geometry_status": str(item["geometry_status"]),
                    "distance_mm": geometry["distance_mm"],
                    "object_height_mm": geometry["object_height_mm"],
                    "support_height_mm": geometry["support_height_mm"],
                    "top_height_above_floor_mm": geometry["top_height_above_floor_mm"],
                    "geometry_confidence": item["geometry_confidence"],
                    "degradation_reason": item.get("degradation_reason"),
                    "failure_reason": item["failure_reason"],
                    "support_method": (item.get("diagnostics") or {}).get("support_source"),
                    "shape_method": (item.get("diagnostics") or {}).get("extent_method"),
                    "diagnostics": item.get("diagnostics"),
                    "overlay_paths": {},
                }
            )
        frames.append(
            {
                "frame_id": frame_id,
                "sequence": sequence,
                "source_image_path": frame.get("source_image_path"),
                "processing_status": processing_status,
                "objects": objects,
            }
        )
    document = {
        "schema_version": SCHEMA_VERSION,
        "scan_id": str(geometry_document["scan_id"]),
        "frame_count": 11,
        "generated_at": generated_at or str(geometry_document["generated_at"]),
        "selected_object_identities": selected_identities,
        "scan_summary": dict(geometry_document.get("summary") or {}),
        "frames": frames,
    }
    return validate_pc_scene_ui_input(document)


def build_pc_scene_ui_input_from_result(
    result_root: Path,
    *,
    output_path: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    root = result_root.resolve()
    geometry = load_strict_json(root / ARTIFACT_FILENAME, code="E_PC_SCENE_UI_INPUT")
    gate = load_strict_json(root / DUAL_GATE_FILENAME, code="E_PC_SCENE_UI_INPUT")
    selected = load_strict_json(root / SELECTED_OBJECTS_FILENAME, code="E_PC_SCENE_UI_INPUT")
    canonical_package = load_strict_json(
        root / P4_PACKAGE_FILENAME, code="E_PC_SCENE_UI_INPUT"
    )
    result_path = root / "result.json"
    status_by_id: dict[str, dict[str, Any]] = {}
    if result_path.is_file():
        result = load_strict_json(result_path, code="E_PC_SCENE_UI_INPUT")
        status_by_id = {
            str(item["frame_id"]): {
                key: value for key, value in item.items()
                if key not in {"frame_id", "sequence"}
            }
            for item in result.get("frame_processing_status", [])
        }
    document = build_pc_scene_ui_input(
        geometry,
        gate,
        selected,
        canonical_package=canonical_package,
        frame_processing_status=status_by_id,
    )
    for frame in document["frames"]:
        canonical = root / "logs" / frame["frame_id"] / "source_rgb.png"
        if not canonical.is_file() and frame["processing_status"]["capture_status"] == "CAPTURE_OK":
            raise ValueError(f"canonical source image is missing: {canonical}")
        frame["source_image_path"] = (
            canonical.relative_to(root).as_posix() if canonical.is_file() else None
        )
    destination = output_path.resolve() if output_path else root / UI_INPUT_FILENAME
    atomic_write_json(destination, document)
    return document, destination


def build_and_render_pc_scene_ui(result_root: Path) -> dict[str, Any]:
    root = result_root.resolve()
    document, input_path = build_pc_scene_ui_input_from_result(root)
    visualization_root = root / "visualization"
    html_path = render_pc_scene_ui(
        input_path,
        visualization_root / "index.html",
        artifact_root=root,
    )
    manifest = {
        "schema_version": "pc_scene_ui_visualization_manifest.v1",
        "visualization_complete": True,
        "scan_id": document["scan_id"],
        "frame_count": document["frame_count"],
        "candidate_count": sum(len(frame["objects"]) for frame in document["frames"]),
        "ui_input": {"path": input_path.name},
        "html": {"path": str(html_path.relative_to(root)).replace("\\", "/")},
        "processing": {"visualization_total_ms": None},
    }
    atomic_write_json(visualization_root / "visualization_manifest.v1.json", manifest)
    return manifest
