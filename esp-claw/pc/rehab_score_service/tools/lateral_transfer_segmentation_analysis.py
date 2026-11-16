from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from feature_extractors.lateral_transfer_profile import (
    DERIVED_ROOT,
    EXPECTED_SESSIONS,
    SEGMENTATION_CORE_VERSION,
    SEGMENTATION_VERSION,
    expected_rep_count,
)
from tools.lateral_transfer_dataset import source_csv_for_session, write_dataset_manifest
from tools.lateral_transfer_segmentation_gate import write_candidate_approved_manifest
from tools.lateral_transfer_segmentation_v1 import segment_lateral_transfer_file


def analyze(output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_dataset_manifest(output_dir)
    rows = []
    candidate_rows = []
    per_session = {}
    for session_id, labels in EXPECTED_SESSIONS.items():
        csv_path = source_csv_for_session(session_id)
        reps, diagnostic, _df = segment_lateral_transfer_file(csv_path)
        actual_valid = sum(1 for rep in reps if rep.get("valid") is True)
        row = {
            "session_id": session_id,
            "expected_rep_count": len(labels),
            "actual_candidate_count": len(reps),
            "actual_valid_rep_count": actual_valid,
            "matches_expected": actual_valid == len(labels),
            "source_csv": str(csv_path),
            "candidate_boundaries": diagnostic.get("candidate_boundary_count"),
        }
        rows.append(row)
        for rep in reps:
            candidate_rows.append(
                {
                    "session_id": session_id,
                    "rep_index": rep.get("rep_index"),
                    "expected_rep_count": len(labels),
                    "valid": rep.get("valid"),
                    "invalid_reason": rep.get("invalid_reason"),
                    "start_index": rep.get("start_index"),
                    "end_index": rep.get("end_index"),
                    "start_time_ms": rep.get("start_time_ms"),
                    "end_time_ms": rep.get("end_time_ms"),
                    "duration_ms": rep.get("duration_ms"),
                    "segmentation_source": rep.get("segmentation_source"),
                    "segmentation_wrapper_version": rep.get("segmentation_wrapper_version"),
                    "segmentation_core_version": rep.get("segmentation_core_version"),
                    "core_split_method": rep.get("core_split_method"),
                    "returned_to_neutral": rep.get("returned_to_neutral"),
                    "quality_warning": rep.get("quality_warning"),
                    "activity_coverage": rep.get("activity_coverage"),
                    "duration_pass": rep.get("duration_pass"),
                    "activity_pass": rep.get("activity_pass"),
                    "boundary_start_pass": rep.get("boundary_start_pass"),
                    "boundary_end_pass": rep.get("boundary_end_pass"),
                    "departure_pass": rep.get("departure_pass"),
                    "return_pass": rep.get("return_pass"),
                    "departure_score": rep.get("departure_score"),
                    "start_boundary_sources": "+".join(rep.get("start_boundary", {}).get("boundary_sources", [])),
                    "end_boundary_sources": "+".join(rep.get("end_boundary", {}).get("boundary_sources", [])),
                    "start_quiet_duration_ms": rep.get("start_boundary", {}).get("quiet_duration_ms"),
                    "end_quiet_duration_ms": rep.get("end_boundary", {}).get("quiet_duration_ms"),
                    "start_return_to_neutral_score": rep.get("start_boundary", {}).get("return_to_neutral_score"),
                    "end_return_to_neutral_score": rep.get("end_boundary", {}).get("return_to_neutral_score"),
                    "start_pose_distance_from_neutral_deg": rep.get("start_boundary", {}).get("pose_distance_from_neutral_deg"),
                    "end_pose_distance_from_neutral_deg": rep.get("end_boundary", {}).get("pose_distance_from_neutral_deg"),
                }
            )
        per_session[session_id] = {
            "summary": row,
            "expected_labels": labels,
            "repetitions": reps,
            "diagnostic": diagnostic,
        }
    csv_out = output_dir / "lateral_transfer_segmentation_analysis.csv"
    json_out = output_dir / "lateral_transfer_segmentation_analysis.json"
    report_out = output_dir / "lateral_transfer_segmentation_analysis_report.md"
    candidates_out = output_dir / "lateral_transfer_segmentation_candidates.csv"
    pd.DataFrame(rows).to_csv(csv_out, index=False, encoding="utf-8-sig")
    pd.DataFrame(candidate_rows).to_csv(candidates_out, index=False, encoding="utf-8-sig")
    matched_count = sum(1 for row in rows if row["matches_expected"])
    mismatch_count = len(EXPECTED_SESSIONS) - matched_count
    valid_total = sum(int(row["actual_valid_rep_count"]) for row in rows)
    raw_summary = {
        "expected_session_count": len(EXPECTED_SESSIONS),
        "matched_session_count": matched_count,
        "mismatch_session_count": mismatch_count,
        "expected_valid_rep_total": expected_rep_count(),
        "actual_valid_rep_total": valid_total,
        "mismatches": [row for row in rows if not row["matches_expected"]],
        "rows": rows,
    }
    candidate_manifest = write_candidate_approved_manifest(output_dir, raw_summary)
    payload = {
        "segmentation_version": SEGMENTATION_VERSION,
        "segmentation_core_version": SEGMENTATION_CORE_VERSION,
        "segmentation_strategy": "REUSE_DRINK_HYBRID_SEGMENTATION_V2_1_1",
        "raw_pool_evaluation": True,
        "expected_session_count": len(EXPECTED_SESSIONS),
        "expected_rep_count": expected_rep_count(),
        "matched_session_count": matched_count,
        "mismatch_session_count": mismatch_count,
        "actual_valid_rep_total": valid_total,
        "raw_pool_blocks_calibration": False,
        "approved_calibration_manifest_candidate": str(candidate_manifest),
        "calibration_gate": {
            "mechanism": "approved_calibration_manifest",
            "action_required": "copy/review candidate manifest, mark approved sessions explicitly, then place it at the approved manifest path",
        },
        "sessions": per_session,
    }
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_lines = [
        "# Lateral Transfer Segmentation Analysis",
        "",
        f"- expected_session_count: {len(EXPECTED_SESSIONS)}",
        f"- expected_rep_count: {expected_rep_count()}",
        f"- matched_session_count: {matched_count}",
        f"- mismatch_session_count: {mismatch_count}",
        f"- actual_valid_rep_total: {valid_total}",
        f"- segmentation_version: {SEGMENTATION_VERSION}",
        f"- segmentation_core_version: {SEGMENTATION_CORE_VERSION}",
        "- raw_pool_blocks_calibration: false",
        f"- approved_calibration_manifest_candidate: {candidate_manifest}",
        "- status: raw pool audit only; calibration requires an approved manifest",
        "",
        "| session | expected | actual_valid | match |",
        "| --- | ---: | ---: | --- |",
    ]
    for row in rows:
        report_lines.append(
            f"| {row['session_id']} | {row['expected_rep_count']} | {row['actual_valid_rep_count']} | {row['matches_expected']} |"
        )
    report_out.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(f"MATCHED_SESSION_COUNT={matched_count}")
    print(f"MISMATCH_SESSION_COUNT={mismatch_count}")
    print(f"ACTUAL_VALID_REP_TOTAL={valid_total}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze lateral_transfer segmentation on the 33-session labeled dataset.")
    parser.add_argument("--output-dir", type=Path, default=DERIVED_ROOT / "segmentation_analysis")
    args = parser.parse_args()
    result = analyze(args.output_dir)
    print(json.dumps({"output_dir": str(args.output_dir), "expected_rep_count": result["expected_rep_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
