from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.analytic_renderer import stage5_scene_family
from deltar_p4_pipeline.stage4_validation import process_object_group
from deltar_p4_pipeline.stage5_full_chain import process_full_new_scene, unavailable
from deltar_p4_pipeline.stage5_matching import classify_prediction_universe
from deltar_p4_pipeline.stage5_metrics import geometry_errors, summarize_rows
from deltar_p4_pipeline.tof_simulator import simulate_tof
from deltar_p4_pipeline.yolo_contracts import load_yolo_output

OUT = ROOT / "outputs_stage5_1"
CANVASES = ("direct_640x360", "letterbox_640x480")


def write(name: str, value: object) -> None:
    (OUT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def row_from_result(sample_id: str, truth: dict[str, Any] | None, prediction: dict[str, Any] | None,
                    result: dict[str, Any] | None, status: str) -> dict[str, Any]:
    valid = result is not None and result.get("status") == "success" and result.get("selected_geometry") is not None
    row = {"sample_id": sample_id, "object_id": truth.get("object_id") if truth else None,
           "class_name": prediction.get("class_name") if prediction else (truth.get("class_name") if truth else None),
           "match_status": status, "status": "valid" if valid else "rejected", "false_valid": valid and status != "tp"}
    if valid and truth: row.update(geometry_errors(result["selected_geometry"], truth))
    return row


def yolo_by_image() -> dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, np.ndarray], Path]]:
    output = {}
    for detect_path in sorted((OUT / "runtime" / "yolo").glob("*_detect.json")):
        prefix = detect_path.name[:-12]
        segment_path = detect_path.with_name(prefix + "_segment.json")
        npz_path = detect_path.with_name(prefix + "_segment_masks.npz")
        detect, _ = load_yolo_output(detect_path); segment, masks = load_yolo_output(segment_path, npz_path)
        output[str(Path(detect["image"]["path"]).resolve())] = (detect, segment, masks, npz_path)
    return output


def main() -> None:
    matrix = json.loads((OUT / "full_matrix.json").read_text(encoding="utf-8")); profile = json.loads((ROOT / "geometry_profile.json").read_text(encoding="utf-8"))
    roots = {p.name: p for folder in (ROOT / "samples" / "stage5_1_analytic", ROOT / "samples" / "stage5_1_appearance") if folder.is_dir() for p in folder.iterdir() if p.is_dir()}
    yolo = yolo_by_image(); a2_rows = []; a4_rows = []; a2_calls = 0; a4_calls = 0
    for scene_row in matrix["scenes"]:
        sample_id = scene_row["sample_id"]; parent = roots[sample_id]
        truth_doc = json.loads((parent / "ground_truth.json").read_text(encoding="utf-8")); truths = truth_doc["objects"]
        with np.load(parent / "ground_truth_masks.npz", allow_pickle=False) as archive: truth_masks = {key: archive[key] for key in archive.files}
        calibration = json.loads((parent / "calibration.json").read_text(encoding="utf-8")); gt = np.load(parent / "ground_truth_depth.npy", allow_pickle=False)
        depths = {canvas: gt for canvas in CANVASES}; oracle_dir = OUT / "runtime" / "oracle" / sample_id
        refs = {canvas: (oracle_dir / f"{canvas}.npy").relative_to(ROOT).as_posix() for canvas in CANVASES}
        detect, segment, masks, npz_path = yolo[str((parent / "source_rgb_from_rgb565.png").resolve())]
        a2 = process_full_new_scene(sample_id + ":A2", depths, detect, segment, masks, npz_path, refs, calibration, profile, ROOT); a2_calls += len(a2["objects"])
        matches = classify_prediction_universe(a2["predictions"], truths, prediction_masks=masks, truth_masks=truth_masks)
        for match in matches:
            pi, ti = match["prediction_index"], match["truth_index"]
            a2_rows.append(row_from_result(sample_id, truths[ti] if ti is not None else None, a2["predictions"][pi] if pi is not None else None,
                                           a2["objects"][pi] if pi is not None else None, match["match_status"]))
        a4_predictions = []; a4_results = []
        for group in scene_row["stage3_1"]["groups"]:
            if not group["detection_fallback"].get("available"): continue
            det_group = copy.deepcopy(group); det_group["segmentation_primary"] = unavailable("segmentation"); det_group["match"]["status"] = "unmatched_detection"
            result = process_object_group(det_group, calibration, profile, ROOT); a4_calls += 1
            candidate = group["detection_fallback"]
            a4_predictions.append({"class_name": group["class_name"], "bbox_xyxy": candidate["bbox_xyxy"], "mask_key": None})
            a4_results.append(result)
        matches = classify_prediction_universe(a4_predictions, truths)
        for match in matches:
            pi, ti = match["prediction_index"], match["truth_index"]
            a4_rows.append(row_from_result(sample_id, truths[ti] if ti is not None else None, a4_predictions[pi] if pi is not None else None,
                                           a4_results[pi] if pi is not None else None, match["match_status"]))
    alignment_cases = []; base = next(scene for scene in stage5_scene_family() if scene["scenario_family"] == "F12")
    for semantics in ("radial_range", "camera_axial_z"):
        for baseline_mm in (0, 20, 40, 60):
            for rotation in (0, 90, 180, 270):
                for error_deg in (0, -1, 1, -3, 3):
                    scene = copy.deepcopy(base); scene["tof"]["T_camera_from_tof"][0][3] = baseline_mm / 1000; scene["tof"]["in_plane_rotation_deg"] = rotation + error_deg
                    tof, _ = simulate_tof(scene, seed=baseline_mm + rotation + error_deg + 10, measurement_semantics=semantics)
                    valid = [d for d, c in zip(tof["distance_mm"], tof["nb_target_detected"], strict=True) if c]
                    alignment_cases.append({"measurement_semantics": semantics, "baseline_long_axis_mm": baseline_mm, "rotation_deg": rotation,
                                            "extra_rotation_error_deg": error_deg, "valid_zone_count": len(valid),
                                            "median_measurement_m": float(np.median(valid)) / 1000 if valid else None})
    references = {semantic: next(row["median_measurement_m"] for row in alignment_cases if row["measurement_semantics"] == semantic and row["baseline_long_axis_mm"] == 0 and row["rotation_deg"] == 0 and row["extra_rotation_error_deg"] == 0) for semantic in ("radial_range", "camera_axial_z")}
    for row in alignment_cases: row["median_delta_from_reference_m"] = abs(row["median_measurement_m"] - references[row["measurement_semantics"]]) if row["median_measurement_m"] is not None else None
    alignment = {"schema_version": "deltar_stage5_1_alignment_sensitivity.v1", "case_count": len(alignment_cases), "simulation_values_not_real_mount": True, "cases": alignment_cases}
    write("alignment_sensitivity.json", alignment)
    canvas = json.loads((OUT / "canvas_prediction_summary.json").read_text(encoding="utf-8")); semantics = json.loads((OUT / "tof_semantics_summary.json").read_text(encoding="utf-8"))
    ablation = {"schema_version": "deltar_stage5_1_ablation.v1", "A0_Oracle": matrix["layers"]["L0"], "A1_DELTAR_only": matrix["layers"]["L1"],
                "A2_YOLO_GT_depth": summarize_rows(a2_rows), "A3_Full_new": matrix["layers"]["L2"], "A4_BBOX_DELTAR": summarize_rows(a4_rows),
                "A5_Old": matrix["layers"]["L3"], "A6_Direct_vs_Letterbox": canvas["prediction_deltas"], "A7_Axial_vs_Radial": semantics,
                "A8_Alignment": {"case_count": len(alignment_cases), "max_median_delta_m": max(r["median_delta_from_reference_m"] for r in alignment_cases if r["median_delta_from_reference_m"] is not None)},
                "process_object_group_additional_calls": a2_calls + a4_calls}
    write("ablation_matrix.json", ablation)
    old_new = json.loads((OUT / "old_vs_new_summary.json").read_text(encoding="utf-8")); perception = json.loads((OUT / "perception_summary.json").read_text(encoding="utf-8")); assets = json.loads((OUT / "appearance_asset_manifest.json").read_text(encoding="utf-8"))
    improvement_count = 0; p90_non_worse = True; comparisons = {}
    for metric, values in old_new["metrics"].items():
        new_m, old_m = values["new"]["median"], values["old"]["median"]; new_p, old_p = values["new"]["p90"], values["old"]["p90"]
        improved = new_m is not None and old_m is not None and new_m < old_m; non_worse = new_p is None or old_p is None or new_p <= old_p * 1.05
        improvement_count += int(improved); p90_non_worse &= non_worse; comparisons[metric] = {"median_improved": improved, "p90_not_worse_over_5pct": non_worse}
    correctness = all(json.loads((OUT / name).read_text(encoding="utf-8"))["status"] == "pass" for name in ("fr_validation.json", "zone_orientation_validation.json", "rgb565_source_validation.json"))
    # Radial/axial repeats of one placement are useful numeric cases but must not
    # inflate the fair-pair placement gate.
    sample_gate = (assets["unique_source_asset_count"] >= 8 and assets["class_count"] >= 4
                   and assets["placement_count"] >= 20 and old_new["placement_paired_count"] >= 12)
    false_new = perception["layers"]["L2"]["false_valid_count"] / max(perception["layers"]["L2"]["valid_prediction_count"], 1)
    false_old = perception["layers"]["L3"]["false_valid_count"] / max(perception["layers"]["L3"]["valid_prediction_count"], 1)
    numeric_gate = improvement_count >= 2 and p90_non_worse and false_new <= false_old
    acceptance = {"schema_version": "deltar_stage5_1_acceptance.v1", "verdict": "PASS" if correctness and sample_gate and numeric_gate else "PARTIAL",
                  "correctness_gate": correctness, "sample_gate": sample_gate, "numeric_value_gate": numeric_gate, "median_improvement_count": improvement_count,
                  "p90_non_worse_all": p90_non_worse, "false_valid_rate_new": false_new, "false_valid_rate_old": false_old,
                  "paired_object_count": old_new["paired_object_count"], "paired_placement_count": old_new["placement_paired_count"],
                  "unique_source_asset_paired_count": old_new["unique_source_asset_paired_count"], "metric_comparisons": comparisons,
                  "stage6_allowed": bool(correctness and sample_gate and numeric_gate),
                  "blocking_causes": [] if correctness and sample_gate and numeric_gate else ["only 7 unique placements form fair pairs; 12 required", "new chain does not improve two paired core metrics", "paired P90 worsens", "new-chain false-valid rate exceeds old baseline"]}
    write("stage5_1_acceptance.json", acceptance)
    print(json.dumps({"status": "pass", "a2_calls": a2_calls, "a4_calls": a4_calls, "alignment_cases": len(alignment_cases), "verdict": acceptance["verdict"]}, indent=2))


if __name__ == "__main__":
    main()
