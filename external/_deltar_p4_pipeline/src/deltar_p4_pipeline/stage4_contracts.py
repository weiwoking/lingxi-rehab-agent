from __future__ import annotations

from typing import Any

from .errors import ErrorCode, PipelineError

INPUT_SCHEMA = "deltar_stage4_object_group_input.v1.1"
OUTPUT_SCHEMA = "deltar_stage4_geometry.v1.1"


def validate_input_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != INPUT_SCHEMA:
        raise PipelineError(ErrorCode.STAGE4_INPUT_SCHEMA, f"schema_version must be {INPUT_SCHEMA}")
    groups = manifest.get("object_groups")
    if not isinstance(groups, list):
        raise PipelineError(ErrorCode.STAGE4_INPUT_SCHEMA, "object_groups must be a list")
    for index, group in enumerate(groups):
        for key in ("sample_id", "logical_object_id", "class_id", "class_name", "segmentation_primary", "detection_fallback", "match", "source_depth_refs", "provenance"):
            if key not in group:
                raise PipelineError(ErrorCode.STAGE4_INPUT_SCHEMA, f"missing {key}", f"object_groups[{index}].{key}")
        if group["match"].get("status") not in {"matched", "unmatched_segmentation", "unmatched_detection"}:
            raise PipelineError(ErrorCode.STAGE4_INPUT_SCHEMA, "invalid match status", f"object_groups[{index}].match.status")
        for canvas in ("direct_640x360", "letterbox_640x480"):
            if canvas not in group["source_depth_refs"]:
                raise PipelineError(ErrorCode.STAGE4_INPUT_SCHEMA, "both source-depth candidates are required", f"object_groups[{index}].source_depth_refs")
    return manifest


def validate_output(output: dict[str, Any]) -> dict[str, Any]:
    if output.get("schema_version") != OUTPUT_SCHEMA or output.get("status") not in {"success", "partial", "rejected"}:
        raise PipelineError(ErrorCode.STAGE4_INPUT_SCHEMA, "invalid Stage 4 output envelope")
    if not isinstance(output.get("objects"), list):
        raise PipelineError(ErrorCode.STAGE4_INPUT_SCHEMA, "objects must be a list")
    valid_selection = {"agreed", "disagreed", "direct_only", "letterbox_only", "both_rejected"}
    for index, obj in enumerate(output["objects"]):
        if obj.get("canvas_selection_status") not in valid_selection:
            raise PipelineError(ErrorCode.STAGE4_INPUT_SCHEMA, "invalid canvas_selection_status", f"objects[{index}].canvas_selection_status")
        for key in ("selection_is_provisional", "single_canvas_only", "automatic_geometry_valid", "absolute_geometry_valid"):
            if not isinstance(obj.get(key), bool):
                raise PipelineError(ErrorCode.STAGE4_INPUT_SCHEMA, f"{key} must be boolean", f"objects[{index}].{key}")
        if obj["canvas_selection_status"] == "disagreed" and obj.get("selected_geometry") is not None:
            raise PipelineError(ErrorCode.STAGE4_INPUT_SCHEMA, "disagreed canvas must not select geometry", f"objects[{index}].selected_geometry")
    return output
