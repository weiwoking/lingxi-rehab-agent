from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from feature_extractors.lateral_transfer_profile import (
    DERIVED_ROOT,
    EXPECTED_SESSIONS,
    SEGMENTATION_CORE_VERSION,
    SEGMENTATION_VERSION,
)
from tools.lateral_transfer_dataset import source_csv_for_session
from tools.lateral_transfer_segmentation_v1 import segment_lateral_transfer_file


class SegmentationNotReadyError(RuntimeError):
    pass


APPROVED_MANIFEST_VERSION = "lateral_transfer_approved_calibration_manifest_v1"
APPROVED_MANIFEST_PATH = DERIVED_ROOT / "approved_calibration_manifest" / "lateral_transfer_approved_calibration_manifest.json"


def evaluate_raw_pool_counts() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    mismatch: list[dict[str, Any]] = []
    valid_total = 0
    for session_id, labels in EXPECTED_SESSIONS.items():
        csv_path = source_csv_for_session(session_id)
        reps, _diagnostic, _df = segment_lateral_transfer_file(csv_path)
        actual = sum(1 for rep in reps if rep.get("valid") is True)
        expected = len(labels)
        row = {
            "session_id": session_id,
            "source_csv": str(csv_path),
            "expected_valid_rep_count": expected,
            "actual_valid_rep_count": actual,
            "matches_expected": actual == expected,
        }
        rows.append(row)
        valid_total += actual
        if actual != expected:
            mismatch.append(row)
    return {
        "segmentation_version": SEGMENTATION_VERSION,
        "segmentation_core_version": SEGMENTATION_CORE_VERSION,
        "raw_pool_evaluation": True,
        "expected_session_count": len(EXPECTED_SESSIONS),
        "matched_session_count": len(EXPECTED_SESSIONS) - len(mismatch),
        "mismatch_session_count": len(mismatch),
        "expected_valid_rep_total": sum(len(labels) for labels in EXPECTED_SESSIONS.values()),
        "actual_valid_rep_total": valid_total,
        "mismatches": mismatch,
        "rows": rows,
        "blocks_calibration": False,
        "note": "raw pool metrics are audit evidence; calibration readiness is controlled by approved manifest",
    }


def candidate_approved_manifest(raw_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    if raw_summary is None:
        raw_summary = evaluate_raw_pool_counts()
    rows_by_session = {row["session_id"]: row for row in raw_summary.get("rows", [])}
    sessions: list[dict[str, Any]] = []
    for session_id, labels in EXPECTED_SESSIONS.items():
        row = rows_by_session.get(session_id, {})
        sessions.append(
            {
                "session_id": session_id,
                "expected_labels": labels,
                "expected_valid_rep_count": len(labels),
                "observed_valid_rep_count": row.get("actual_valid_rep_count"),
                "raw_pool_matches_expected": row.get("matches_expected"),
                "approved_for_calibration": False,
                "review_status": "manual_review_required",
                "review_notes": "",
            }
        )
    return {
        "manifest_version": APPROVED_MANIFEST_VERSION,
        "segmentation_version": SEGMENTATION_VERSION,
        "segmentation_core_version": SEGMENTATION_CORE_VERSION,
        "approval_status": "USER_APPROVAL_REQUIRED",
        "auto_excluded_sessions": [],
        "task036_automatically_excluded": False,
        "task039_automatically_excluded": False,
        "sessions": sessions,
        "raw_pool_summary": {
            "expected_session_count": raw_summary.get("expected_session_count"),
            "matched_session_count": raw_summary.get("matched_session_count"),
            "mismatch_session_count": raw_summary.get("mismatch_session_count"),
            "expected_valid_rep_total": raw_summary.get("expected_valid_rep_total"),
            "actual_valid_rep_total": raw_summary.get("actual_valid_rep_total"),
            "mismatches": raw_summary.get("mismatches", []),
        },
    }


def write_candidate_approved_manifest(output_dir: Path, raw_summary: dict[str, Any] | None = None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "lateral_transfer_approved_calibration_manifest.candidate.json"
    path.write_text(json.dumps(candidate_approved_manifest(raw_summary), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_approved_calibration_manifest(manifest_path: Path = APPROVED_MANIFEST_PATH) -> dict[str, Any]:
    if not manifest_path.exists():
        raise SegmentationNotReadyError(f"APPROVED_CALIBRATION_MANIFEST_MISSING:{manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SegmentationNotReadyError(f"APPROVED_CALIBRATION_MANIFEST_INVALID_JSON:{manifest_path}") from exc
    if manifest.get("manifest_version") != APPROVED_MANIFEST_VERSION:
        raise SegmentationNotReadyError("APPROVED_CALIBRATION_MANIFEST_VERSION_MISMATCH")
    if manifest.get("segmentation_version") != SEGMENTATION_VERSION:
        raise SegmentationNotReadyError("APPROVED_CALIBRATION_MANIFEST_WRAPPER_VERSION_MISMATCH")
    if manifest.get("segmentation_core_version") != SEGMENTATION_CORE_VERSION:
        raise SegmentationNotReadyError("APPROVED_CALIBRATION_MANIFEST_CORE_VERSION_MISMATCH")
    return manifest


def assert_lateral_segmentation_ready(manifest_path: Path = APPROVED_MANIFEST_PATH) -> dict[str, Any]:
    manifest = load_approved_calibration_manifest(manifest_path)
    if manifest.get("approval_status") != "APPROVED_FOR_LATERAL_FINALIZATION":
        raise SegmentationNotReadyError("APPROVED_CALIBRATION_MANIFEST_NOT_FINAL")
    approved = [
        item for item in manifest.get("sessions", [])
        if item.get("approved_for_calibration") is True and item.get("review_status") == "approved"
    ]
    if not approved:
        raise SegmentationNotReadyError("APPROVED_CALIBRATION_MANIFEST_HAS_NO_APPROVED_SESSIONS")
    validated: list[dict[str, Any]] = []
    actual_rep_total = 0
    expected_rep_total = 0
    label_counts = {"normal": 0, "lf": 0, "tremor": 0}
    for item in approved:
        session_id = str(item.get("session_id") or "")
        expected_labels = list(item.get("expected_labels") or [])
        expected_count = int(item.get("expected_valid_rep_count") or 0)
        if not session_id:
            raise SegmentationNotReadyError("APPROVED_SESSION_ID_MISSING")
        if len(expected_labels) != expected_count:
            raise SegmentationNotReadyError(
                f"APPROVED_SESSION_LABEL_COUNT_MISMATCH:{session_id}:expected={expected_count}:labels={len(expected_labels)}"
            )
        csv_path = source_csv_for_session(session_id)
        if not csv_path.exists():
            raise SegmentationNotReadyError(f"APPROVED_SESSION_SOURCE_CSV_MISSING:{session_id}:{csv_path}")
        reps, _diagnostic, _df = segment_lateral_transfer_file(csv_path)
        actual_count = sum(1 for rep in reps if rep.get("valid") is True)
        if actual_count != expected_count:
            raise SegmentationNotReadyError(
                f"APPROVED_SESSION_REP_COUNT_MISMATCH:{session_id}:expected={expected_count}:actual={actual_count}"
            )
        expected_rep_total += expected_count
        actual_rep_total += actual_count
        for label in expected_labels:
            if label not in label_counts:
                raise SegmentationNotReadyError(f"APPROVED_SESSION_UNKNOWN_LABEL:{session_id}:{label}")
            label_counts[label] += 1
        validated.append(
            {
                **item,
                "source_csv": str(csv_path),
                "actual_valid_rep_count": actual_count,
                "count_validation_passed": True,
            }
        )
    if len(validated) != 31:
        raise SegmentationNotReadyError(f"APPROVED_SESSION_COUNT_MISMATCH:expected=31:actual={len(validated)}")
    if expected_rep_total != 59 or actual_rep_total != 59:
        raise SegmentationNotReadyError(
            f"APPROVED_REP_TOTAL_MISMATCH:expected=59:expected_labels={expected_rep_total}:actual={actual_rep_total}"
        )
    if label_counts != {"normal": 48, "lf": 6, "tremor": 5}:
        raise SegmentationNotReadyError(f"APPROVED_LABEL_COUNTS_MISMATCH:{label_counts}")
    excluded = {
        str(item.get("session_id")): item
        for item in manifest.get("sessions", [])
        if item.get("approved_for_calibration") is False
    }
    for session_id in ("20260807_TASK036", "20260807_TASK039"):
        item = excluded.get(session_id)
        if item is None:
            raise SegmentationNotReadyError(f"APPROVED_EXCLUSION_MISSING:{session_id}")
        if item.get("review_status") != "excluded_from_calibration_by_user":
            raise SegmentationNotReadyError(f"APPROVED_EXCLUSION_STATUS_MISMATCH:{session_id}")
        if item.get("exclusion_reason") != "segmentation_count_mismatch_raw_pool":
            raise SegmentationNotReadyError(f"APPROVED_EXCLUSION_REASON_MISMATCH:{session_id}")
    return {
        "segmentation_version": SEGMENTATION_VERSION,
        "segmentation_core_version": SEGMENTATION_CORE_VERSION,
        "approved_manifest_path": str(manifest_path),
        "approved_session_count": len(validated),
        "approved_expected_rep_total": expected_rep_total,
        "approved_actual_rep_total": actual_rep_total,
        "approved_label_counts": label_counts,
        "healthy_normal_rep_count": label_counts["normal"],
        "approved_count_validation_passed": True,
        "approved_rep_total": expected_rep_total,
        "approved_sessions": validated,
        "raw_pool_summary": manifest.get("raw_pool_summary", {}),
    }


def write_gate_failure_marker(output_dir: Path, error: Exception) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "SEGMENTATION_NOT_READY.txt").write_text(str(error) + "\n", encoding="utf-8")
