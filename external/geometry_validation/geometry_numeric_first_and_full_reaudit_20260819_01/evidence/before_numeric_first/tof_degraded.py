"""Per-frame ToF degradation decisions; capture existence is independent."""
from __future__ import annotations

import math
from typing import Any


FORMAL_STATUSES = {5, 6, 9, 10}
MAX_BORROW_AGE_MS = 100.0
MAX_BORROW_YAW_DEG = 2.0
MAX_BORROW_TRANSLATION_MM = 25.0


def formal_valid_count(tof: dict[str, Any]) -> int:
    return sum(
        int(count) > 0 and int(status) in FORMAL_STATUSES
        for status, count in zip(tof.get("target_status", []), tof.get("nb_target_detected", []))
    )


def _pose(frame: dict[str, Any]) -> tuple[float, tuple[float, float, float]] | None:
    value = frame.get("capture_pose") or frame.get("pose")
    if not isinstance(value, dict):
        return None
    yaw = value.get("yaw_deg")
    translation = value.get("translation_mm") or value.get("position_mm")
    if not isinstance(yaw, (int, float)) or not isinstance(translation, list) or len(translation) != 3:
        return None
    if not math.isfinite(float(yaw)) or not all(isinstance(item, (int, float)) and math.isfinite(float(item)) for item in translation):
        return None
    return float(yaw), tuple(float(item) for item in translation)


def _pose_safe(current: dict[str, Any], candidate: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    left, right = _pose(current), _pose(candidate)
    if left is None or right is None:
        return False, {"pose_available": False, "yaw_delta_deg": None, "translation_delta_mm": None}
    yaw_delta = abs(left[0] - right[0])
    translation_delta = math.sqrt(sum((a - b) ** 2 for a, b in zip(left[1], right[1])))
    return bool(yaw_delta <= MAX_BORROW_YAW_DEG and translation_delta <= MAX_BORROW_TRANSLATION_MM), {
        "pose_available": True,
        "yaw_delta_deg": yaw_delta,
        "translation_delta_mm": translation_delta,
    }


def build_tof_decisions(frames: list[Any]) -> dict[str, dict[str, Any]]:
    usable = [frame for frame in frames if formal_valid_count(frame.tof) > 0]
    decisions: dict[str, dict[str, Any]] = {}
    for frame in frames:
        frame_id = str(frame.frame["frame_id"])
        count = formal_valid_count(frame.tof)
        if count > 0:
            decisions[frame_id] = {
                "tof": frame.tof,
                "tof_status": "TOF_NORMAL" if count >= 8 else "TOF_DEGRADED",
                "degradation_mode": "NORMAL" if count >= 8 else "PARTIAL_ZONE_TOF",
                "source": "current",
                "formal_valid_count": count,
                "confidence": float(math.sqrt(count / 64.0)),
                "reason_codes": [] if count >= 8 else ["sparse_formal_valid_zones"],
                "borrowed_from_frame_id": None,
                "borrowed_age_ms": None,
                "pose_gate": None,
            }
            continue
        nearest = None
        if usable:
            nearest = min(
                usable,
                key=lambda item: abs(int(frame.frame["camera_timestamp_us"]) - int(item.tof["timestamp_us"])),
            )
        age_ms = None if nearest is None else abs(int(frame.frame["camera_timestamp_us"]) - int(nearest.tof["timestamp_us"])) / 1000.0
        pose_ok, pose_gate = (False, {"pose_available": False, "yaw_delta_deg": None, "translation_delta_mm": None})
        if nearest is not None:
            pose_ok, pose_gate = _pose_safe(frame.frame, nearest.frame)
        if nearest is not None and age_ms is not None and age_ms <= MAX_BORROW_AGE_MS and pose_ok:
            decisions[frame_id] = {
                "tof": nearest.tof,
                "tof_status": "TOF_BORROWED",
                "degradation_mode": "NEAREST_TOF_FALLBACK",
                "source": "borrowed",
                "formal_valid_count": formal_valid_count(nearest.tof),
                "confidence": float(max(0.0, 0.5 * (1.0 - age_ms / MAX_BORROW_AGE_MS))),
                "reason_codes": ["current_tof_unavailable", "safe_nearest_tof_borrowed"],
                "borrowed_from_frame_id": str(nearest.frame["frame_id"]),
                "borrowed_age_ms": age_ms,
                "pose_gate": pose_gate,
            }
        else:
            reasons = ["zero_formal_valid_tof", "no_safe_tof"]
            if nearest is not None and age_ms is not None and age_ms > MAX_BORROW_AGE_MS:
                reasons.append("nearest_tof_age_gate_failed")
            if nearest is not None and not pose_ok:
                reasons.append("nearest_tof_pose_gate_failed")
            decisions[frame_id] = {
                "tof": None,
                "tof_status": "TOF_UNAVAILABLE",
                "degradation_mode": "RGB_ONLY_GEOMETRY_UNAVAILABLE",
                "source": "none",
                "formal_valid_count": 0,
                "confidence": 0.0,
                "reason_codes": reasons,
                "borrowed_from_frame_id": None if nearest is None else str(nearest.frame["frame_id"]),
                "borrowed_age_ms": age_ms,
                "pose_gate": pose_gate,
            }
    return decisions


def public_tof_decision(decision: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in decision.items() if key != "tof"}
