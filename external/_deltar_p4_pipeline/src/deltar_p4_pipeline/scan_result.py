from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .scan_profiles import ProfileBundle
from .stage6_schema import SCAN_RESULT_SCHEMA


def build_scan_result(*, scan: dict[str, Any], job_id: str, frame_results: list[dict[str, Any]],
                      rejected_frames: list[dict[str, Any]], objects: list[dict[str, Any]],
                      warnings: list[str], errors: list[dict[str, Any]], status: str,
                      aggregation: dict[str, Any], provenance: dict[str, Any], processing: dict[str, Any],
                      bundle: ProfileBundle) -> dict[str, Any]:
    valid_count = sum(frame.get("status") in {"success", "rejected"} for frame in frame_results)
    statuses = []
    rejected_by_id = {str(item["frame_id"]): item for item in rejected_frames}
    by_id = {str(item["frame_id"]): item for item in frame_results}
    for sequence in range(int(scan["frame_count"])):
        frame_id = f"frame_{sequence:06d}"
        frame = by_id.get(frame_id)
        if frame is not None and isinstance(frame.get("processing_status"), dict):
            frame_status = dict(frame["processing_status"])
        elif frame_id in rejected_by_id:
            frame_status = {
                "capture_status": "CAPTURE_OK",
                "tof_status": "TOF_UNAVAILABLE",
                "deltar_status": "DELTAR_UNAVAILABLE",
                "detection_status": "DETECTION_UNAVAILABLE",
                "geometry_status": "GEOMETRY_UNAVAILABLE",
                "degradation_mode": "FRAME_PROCESSING_REJECTED",
                "reason_codes": [str(rejected_by_id[frame_id]["error"].get("code", "E_FRAME_PROCESSING"))],
                "tof_diagnostics": {},
            }
        else:
            frame_status = {
                "capture_status": "CAPTURE_FAILURE",
                "tof_status": "TOF_UNAVAILABLE",
                "deltar_status": "DELTAR_UNAVAILABLE",
                "detection_status": "DETECTION_UNAVAILABLE",
                "geometry_status": "GEOMETRY_UNAVAILABLE",
                "degradation_mode": "CAPTURE_UNAVAILABLE",
                "reason_codes": ["capture_unavailable"],
                "tof_diagnostics": {},
            }
        statuses.append({"frame_id": frame_id, "sequence": sequence, **frame_status})
    return {
        "schema_version": SCAN_RESULT_SCHEMA,
        "status": status,
        "scan_id": scan["scan_id"],
        "job_id": job_id,
        "input": {
            "frame_count": scan["frame_count"],
            "valid_frame_count": valid_count,
            "rejected_frame_count": scan["frame_count"] - valid_count,
            "rejected_frames": rejected_frames,
        },
        "models": {task: {"task": task, "sha256": item["sha256"]} for task, item in provenance["models"].items()},
        "profiles": bundle.public_provenance(),
        "objects": objects,
        "warnings": sorted(set(warnings)),
        "errors": errors,
        "aggregation": aggregation,
        "provenance": provenance,
        "processing": processing,
        "frame_processing_status": statuses,
    }
