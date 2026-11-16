from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
from typing import Any
import zipfile


PIPELINE = Path(r"C:\ec\_deltar_p4_pipeline")
WORKSPACE = Path(__file__).resolve().parent
RESULTS = Path(r"C:\ec\_deltar_scan_service_data\results")
sys.path.insert(0, str(PIPELINE / "src"))

from deltar_p4_pipeline.chatgpt_review_bundle import (  # noqa: E402
    build_chatgpt_review_bundle,
    validate_review_zip,
)
from deltar_p4_pipeline.pc_scene_ui_adapter import build_and_render_pc_scene_ui  # noqa: E402
from deltar_p4_pipeline.stage8b4a_contracts import validate_p4_package  # noqa: E402
from deltar_p4_pipeline.stage8b4a_package import build_and_write_stage8b4a  # noqa: E402


REPLAYS = {
    "G001": "scan_20260818_092411_numericfirst01",
    "G002": "scan_20260818_092729_numericfirst01",
    "SCAN_161718": "scan_20260818_161718_numericfirst01",
    "SCAN_165011": "scan_20260818_165011_numericfirst01",
    "SCAN_070604": "scan_20260819_070604_numericfirst01",
    "SCAN_085223": "scan_20260819_085223_numericfirst01",
}


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def prepare_root(scan: str, source_name: str) -> Path:
    source = RESULTS / source_name
    target = WORKSPACE / "contract_replays" / source_name
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        WORKSPACE / "production_replays" / f"{scan}.stage8b_all_object_geometry.v1.json",
        target / "stage8b_all_object_geometry.v1.json",
    )
    if (source / "result.json").is_file():
        shutil.copy2(source / "result.json", target / "result.json")
    compact_optional = (
        "frame_processing_status.json",
        "job_report.json",
        "logs/pipeline_phases.jsonl",
        "logs/pipeline_progress.jsonl",
        "logs/service_phases.jsonl",
        "scan.json",
        "service_job.json",
        "stage7d/stage7d_summary.v1.json",
        "stage8b1_yolo_batch_manifest.json",
        "stage8b1_yolo_batch_timing.json",
        "support_authority_summary.json",
        "tof_frame_diagnostics.json",
        "validation_receipt.json",
        "validation_receipt.v1.json",
    )
    for relative in compact_optional:
        path = source / relative
        if path.is_file():
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    frame_target = target / "frame_results"
    frame_target.mkdir(exist_ok=True)
    for path in sorted((source / "frame_results").glob("frame_*.json")):
        shutil.copy2(path, frame_target / path.name)
    for path in sorted((source / "logs").glob("frame_*/source_rgb.png")):
        destination = target / "logs" / path.parent.name / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    return target


def main() -> int:
    rows = []
    totals = {
        "frame_count": 0,
        "rgb_count": 0,
        "numeric_count": 0,
        "multi_mode_numeric_count": 0,
        "shape_support_mismatch_count": 0,
        "p4_object_count": 0,
    }
    for scan, source_name in REPLAYS.items():
        root = prepare_root(scan, source_name)
        geometry = json.loads(
            (root / "stage8b_all_object_geometry.v1.json").read_text(encoding="utf-8")
        )
        package_result = build_and_write_stage8b4a(root, repository_root=PIPELINE)
        package = validate_p4_package(package_result["package"])
        ui_manifest = build_and_render_pc_scene_ui(root)
        review = build_chatgpt_review_bundle(root, scan_terminal_status="completed")
        review_manifest = validate_review_zip(review.zip_path)
        with zipfile.ZipFile(review.zip_path) as archive:
            crc_error = archive.testzip()

        support_mismatches = 0
        multi_mode_numeric = 0
        numeric = 0
        for frame in geometry["frames"]:
            for obj in frame["objects"]:
                if not obj["numeric_available"]:
                    continue
                numeric += 1
                diagnostic = (
                    obj["diagnostics"]["generic_support"]["diagnostics"]["m3_w4"]
                )
                if diagnostic["retained_mode_count"] > 1:
                    multi_mode_numeric += 1
                if abs(
                    float(obj["geometry"]["support_height_mm"])
                    - float(diagnostic["support_mm"])
                ) > 1e-12:
                    support_mismatches += 1

        rgb_count = len(list((root / "logs").glob("frame_*/source_rgb.png")))
        html_path = root / ui_manifest["html"]["path"]
        html = html_path.read_text(encoding="utf-8")
        selected_ids = {
            item["object_id"]
            for item in package_result["selected_document"]["items"]
        }
        selected_by_id = {
            item["object_id"]: item
            for item in package_result["selected_document"]["items"]
        }
        package_ids = {item["object_id"] for item in package["objects"]}
        p4_fields = all(
            item.get("source_frame_id")
            and item.get("class_name")
            and selected_by_id.get(item["object_id"], {}).get("logical_object_id")
            and selected_by_id[item["object_id"]].get("source_frame_id")
            == item.get("source_frame_id")
            for item in package["objects"]
        )
        row = {
            "scan": scan,
            "frame_count": len(geometry["frames"]),
            "rgb_count": rgb_count,
            "numeric_count": numeric,
            "unavailable_count": geometry["summary"]["geometry_unavailable_count"],
            "hard_failure_count": geometry["summary"]["geometry_failure_count"],
            "multi_mode_numeric_count": multi_mode_numeric,
            "shape_support_mismatch_count": support_mismatches,
            "upright_slender_count": geometry["summary"]["upright_slender_count"],
            "roughly_spherical_count": geometry["summary"]["roughly_spherical_count"],
            "generic_baseline_count": geometry["summary"]["generic_baseline_count"],
            "p4_object_count": len(package["objects"]),
            "p4_selected_identity_exact": selected_ids == package_ids,
            "p4_required_fields_pass": p4_fields,
            "p4_inference_invocations": package_result["diagnostics"]["inference_invocations"],
            "ui_frame_count": ui_manifest["frame_count"],
            "ui_html_frame_section_count": html.count('class="frame-section"'),
            "ui_rgb_reference_count": html.count("../logs/frame_"),
            "ui_contains_m3_w4": "m3_w4" in html,
            "review_zip": str(review.zip_path),
            "review_bundle_status": review_manifest["bundle_status"],
            "review_included_file_count": len(review_manifest["included_files"]),
            "review_preview_count": review_manifest["preview_policy"]["included_count"],
            "review_crc_pass": crc_error is None,
        }
        row["pass"] = (
            row["frame_count"] == 11
            and row["rgb_count"] == 11
            and row["numeric_count"] > 0
            and row["unavailable_count"] == 0
            and row["hard_failure_count"] == 0
            and row["shape_support_mismatch_count"] == 0
            and row["upright_slender_count"] > 0
            and row["roughly_spherical_count"] > 0
            and row["generic_baseline_count"] > 0
            and row["p4_selected_identity_exact"]
            and row["p4_required_fields_pass"]
            and not any(row["p4_inference_invocations"].values())
            and row["ui_frame_count"] == 11
            and row["ui_html_frame_section_count"] == 11
            and row["ui_rgb_reference_count"] >= 11
            and row["ui_contains_m3_w4"]
            and row["review_preview_count"] == 11
            and row["review_bundle_status"] == "full_success"
            and row["review_crc_pass"]
        )
        rows.append(row)
        for key in totals:
            totals[key] += int(row.get(key, 0))

    passed = all(row["pass"] for row in rows)
    gates = {
        "numeric_first_ambiguity_retains_number": totals["multi_mode_numeric_count"] > 0,
        "shape_can_override_support": False,
        "cup_bottle_specialized_retained": all(row["upright_slender_count"] > 0 for row in rows),
        "ball_specialized_retained": all(row["roughly_spherical_count"] > 0 for row in rows),
        "generic_extent_retained": all(row["generic_baseline_count"] > 0 for row in rows),
        "tof_degraded_regression_pass": all(row["hard_failure_count"] == 0 for row in rows),
        "p4_package_contract_pass": all(
            row["p4_selected_identity_exact"] and row["p4_required_fields_pass"] for row in rows
        ),
        "ui_regression_pass": all(
            row["ui_frame_count"] == 11
            and row["ui_html_frame_section_count"] == 11
            and row["ui_rgb_reference_count"] >= 11
            for row in rows
        ),
        "review_bundle_regression_pass": all(
            row["review_bundle_status"] == "full_success"
            and row["review_preview_count"] == 11
            and row["review_crc_pass"]
            for row in rows
        ),
        "all_contract_regressions_pass": passed,
    }
    document = {
        "schema_version": "m3_w4_contract_regressions.v1",
        "gates": gates,
        "totals": totals,
        "scans": rows,
    }
    write_json(WORKSPACE / "CONTRACT_REGRESSIONS.json", document)
    (WORKSPACE / "CONTRACT_REGRESSIONS.md").write_text(
        "# M3 + W4 production contract regressions\n\n"
        f"- Six-scan frame/RGB count: {totals['frame_count']}/{totals['rgb_count']}\n"
        f"- Numeric objects: {totals['numeric_count']}\n"
        f"- Numeric multi-mode objects: {totals['multi_mode_numeric_count']}\n"
        f"- Shape/support mismatches: {totals['shape_support_mismatch_count']}\n"
        f"- P4 objects: {totals['p4_object_count']}\n"
        f"- P4 gate: **{gates['p4_package_contract_pass']}**\n"
        f"- UI gate: **{gates['ui_regression_pass']}**\n"
        f"- Review ZIP gate: **{gates['review_bundle_regression_pass']}**\n"
        f"- Overall: **{passed}**\n",
        encoding="utf-8",
    )
    print(json.dumps({"pass": passed, "gates": gates, "totals": totals}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
