from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from feature_extractors.lateral_transfer_profile import DERIVED_ROOT, EXPECTED_SESSIONS
from tools import drink_water_v1_pipeline as drink_pipeline
from tools.lateral_transfer_dataset import sha256_file, source_csv_for_session
from scoring_assets import DERIVED_ROOT as ASSET_DERIVED_ROOT


EVALUATION_VERSION = "lateral_transfer_drink_reuse_evaluation_v1"
DRINK_SEGMENTATION_VERSION = "drink_hybrid_segmentation_v2_1_1"
DEFAULT_OUTPUT_ROOT = (
    ASSET_DERIVED_ROOT
    / f"lateral_transfer_drink_reuse_evaluation_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
)


def session_group(session_id: str) -> str:
    suffix = int(session_id.rsplit("TASK", 1)[1])
    if 24 <= suffix <= 39:
        return "normal_single"
    if 40 <= suffix <= 43:
        return "normal_3"
    if 45 <= suffix <= 46:
        return "normal_5"
    if suffix in {47, 48, 49, 50, 52, 53}:
        return "mixed_normal_lf_normal"
    if 54 <= suffix <= 58:
        return "tremor_single"
    return "excluded_or_unknown"


def json_safe(value: Any) -> Any:
    return drink_pipeline.make_json_safe(value)


def compact_reject_reasons(diagnostic: dict[str, Any]) -> str:
    reasons: list[str] = []
    for key in ("rejected_cycle_candidates", "rejected_candidates", "rejected_boundaries"):
        items = diagnostic.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    reason = item.get("invalid_reason") or item.get("reject_reason") or item.get("reason")
                    if reason is not None:
                        reasons.append(str(reason))
    return ";".join(reasons[:12])


def boundary_times(reps: list[dict[str, Any]]) -> str:
    values: list[str] = []
    for rep in reps:
        start = rep.get("start_time_ms")
        end = rep.get("end_time_ms")
        values.append(f"{start}->{end}")
    return ";".join(values)


def rep_durations(reps: list[dict[str, Any]]) -> str:
    return ";".join(str(rep.get("duration_ms")) for rep in reps)


def evaluate(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    session_rows: list[dict[str, Any]] = []
    rep_rows: list[dict[str, Any]] = []
    mismatch_rows: list[dict[str, Any]] = []

    for session_id, labels in EXPECTED_SESSIONS.items():
        csv_path = source_csv_for_session(session_id)
        repetitions, diagnostic, _df = drink_pipeline.segment_file(csv_path)
        actual_count = sum(1 for rep in repetitions if rep.get("valid") is True)
        expected_count = len(labels)
        group = session_group(session_id)
        matches = actual_count == expected_count
        row = {
            "session_id": session_id,
            "group": group,
            "expected_rep_count": expected_count,
            "actual_rep_count": actual_count,
            "count_delta": actual_count - expected_count,
            "matches_expected": matches,
            "source_csv": str(csv_path),
            "source_sha256": sha256_file(csv_path),
            "candidate_count": len(repetitions),
            "valid_rep_count": actual_count,
            "segmentation_version": str(diagnostic.get("method") or DRINK_SEGMENTATION_VERSION),
            "diagnostic_summary": str(diagnostic.get("diagnostic") or diagnostic.get("base_method") or ""),
        }
        session_rows.append(row)
        for rep in repetitions:
            rep_rows.append(
                {
                    "session_id": session_id,
                    "group": group,
                    "expected_rep_count": expected_count,
                    "rep_index": rep.get("rep_index"),
                    "start_index": rep.get("start_index"),
                    "end_index": rep.get("end_index"),
                    "start_time_ms": rep.get("start_time_ms"),
                    "end_time_ms": rep.get("end_time_ms"),
                    "duration_ms": rep.get("duration_ms"),
                    "valid": rep.get("valid"),
                    "confidence": rep.get("confidence"),
                    "split_method": rep.get("split_method"),
                    "invalid_reason": rep.get("invalid_reason"),
                    "merged_candidate": rep.get("merged_candidate"),
                    "incomplete_candidate": rep.get("incomplete_candidate"),
                }
            )
        if not matches:
            mismatch_rows.append(
                {
                    **row,
                    "detected_rep_durations_ms": rep_durations(repetitions),
                    "boundary_times_ms": boundary_times(repetitions),
                    "reject_reasons": compact_reject_reasons(diagnostic),
                    "candidate_reasons": json.dumps(
                        json_safe(diagnostic.get("cycle_candidates", []))[:8],
                        ensure_ascii=False,
                    )
                    if isinstance(json_safe(diagnostic.get("cycle_candidates", [])), list)
                    else "",
                }
            )

    group_names = [
        "normal_single",
        "normal_3",
        "normal_5",
        "mixed_normal_lf_normal",
        "tremor_single",
    ]
    group_summary: dict[str, dict[str, Any]] = {}
    for group in group_names:
        rows = [row for row in session_rows if row["group"] == group]
        group_summary[group] = {
            "matched": sum(1 for row in rows if row["matches_expected"]),
            "total": len(rows),
            "match_text": f"{sum(1 for row in rows if row['matches_expected'])}/{len(rows)}",
        }

    expected_total = sum(len(labels) for labels in EXPECTED_SESSIONS.values())
    actual_total = sum(int(row["actual_rep_count"]) for row in session_rows)
    total_abs_error = sum(abs(int(row["count_delta"])) for row in session_rows)
    matched_count = sum(1 for row in session_rows if row["matches_expected"])
    summary = {
        "evaluation_version": EVALUATION_VERSION,
        "drink_segmentation_version": DRINK_SEGMENTATION_VERSION,
        "drink_authority_file": str(SERVICE_ROOT / "tools" / "drink_water_v1_pipeline.py"),
        "exact_drink_reuse": True,
        "compatibility_adapter_used": False,
        "expected_count_used_by_segmenter": False,
        "task_id_used_by_segmenter": False,
        "parameter_sweep": False,
        "session_count": len(session_rows),
        "expected_rep_total": expected_total,
        "matched_session_count": matched_count,
        "mismatch_session_count": len(session_rows) - matched_count,
        "session_match_rate": (matched_count / len(session_rows)) if session_rows else 0.0,
        "actual_rep_total": actual_total,
        "total_abs_count_error": total_abs_error,
        "group_summary": group_summary,
        "output_files": {
            "sessions_csv": "drink_reuse_sessions.csv",
            "repetitions_csv": "drink_reuse_repetitions.csv",
            "mismatch_csv": "drink_reuse_mismatch.csv",
        },
    }

    write_csv(output_dir / "drink_reuse_sessions.csv", session_rows)
    write_csv(output_dir / "drink_reuse_repetitions.csv", rep_rows)
    write_csv(output_dir / "drink_reuse_mismatch.csv", mismatch_rows)
    (output_dir / "drink_reuse_summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row.keys()})
    if not fieldnames:
        fieldnames = ["empty"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate exact frozen drink_water segmentation reuse on lateral_transfer raw Sessions."
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    summary = evaluate(args.output_dir)
    groups = summary["group_summary"]
    print(f"DRINK_SEGMENTATION_VERSION={summary['drink_segmentation_version']}")
    print(f"SESSION_COUNT={summary['session_count']}")
    print(f"MATCHED_SESSION_COUNT={summary['matched_session_count']}")
    print(f"MISMATCH_SESSION_COUNT={summary['mismatch_session_count']}")
    print(f"SESSION_MATCH_RATE={summary['session_match_rate']:.6f}")
    print(f"EXPECTED_REP_TOTAL={summary['expected_rep_total']}")
    print(f"ACTUAL_REP_TOTAL={summary['actual_rep_total']}")
    print(f"TOTAL_ABS_COUNT_ERROR={summary['total_abs_count_error']}")
    print(f"NORMAL_SINGLE_MATCH={groups['normal_single']['match_text']}")
    print(f"NORMAL_3_MATCH={groups['normal_3']['match_text']}")
    print(f"NORMAL_5_MATCH={groups['normal_5']['match_text']}")
    print(f"MIXED_MATCH={groups['mixed_normal_lf_normal']['match_text']}")
    print(f"TREMOR_SINGLE_MATCH={groups['tremor_single']['match_text']}")
    print(f"OUTPUT_DIR={args.output_dir}")


if __name__ == "__main__":
    main()
