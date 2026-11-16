from __future__ import annotations

import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.analytic_renderer import calibration_from_scene
from deltar_p4_pipeline.baseline_old_fusion import evaluate_old_detection
from deltar_p4_pipeline.cli import run_sample
from deltar_p4_pipeline.deltar_bridge import DeltarBridge
from deltar_p4_pipeline.stage5_full_chain import process_full_new_scene, process_mask_oracle_scene
from deltar_p4_pipeline.stage5_matching import classify_prediction_universe, perception_counts
from deltar_p4_pipeline.stage5_metrics import distribution, geometry_errors, summarize_rows
from deltar_p4_pipeline.yolo_worker_client import run_yolo_worker

OUT = ROOT / "outputs_stage5_1"
FORMAL = Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128")
CHECKPOINT = Path(r"C:\ec\_deltar_formal\assets\nyu.pt")
VISION_PYTHON = Path(r"C:\ec\_conda_envs\vision_interpreter\python.exe")
WORKER = ROOT / "workers" / "yolo_worker.py"
DETECT_MODEL = Path(r"C:\ec\esp-claw\pc\vision_interpreter\models\yolo11n.pt")
SEGMENT_MODEL = ROOT / "models" / "yolo11n-seg.pt"
DETECT_SHA = "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"
SEGMENT_SHA = "55ed65c56c91713d23e8402371c6c49a6fd84f257f7dce452e8d70e41dcbe152"
CANVASES = ("direct_640x360", "letterbox_640x480")


def write(name: str, value: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def raw_rgb_hash(path: Path) -> str:
    return hashlib.sha256(np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8).tobytes()).hexdigest()


def result_row(layer: str, sample_id: str, scene: dict[str, Any], truth: dict[str, Any] | None,
               result: dict[str, Any] | None, match_status: str, *, prediction: dict[str, Any] | None = None,
               iou: float | None = None, false_valid: bool = False) -> dict[str, Any]:
    valid = result is not None and result.get("status") in {"success", "valid"} and result.get("selected_geometry", result.get("geometry")) is not None
    geometry = (result.get("selected_geometry") or result.get("geometry")) if valid else None
    row: dict[str, Any] = {"layer": layer, "sample_id": sample_id, "scene_id": scene["scene_id"],
        "scenario_family": scene["scenario_family"], "appearance_kind": scene["appearance_kind"],
        "appearance_asset_id": scene.get("appearance_asset_id"), "measurement_semantics": scene["tof"].get("measurement_semantics"),
        "object_id": truth.get("object_id") if truth else None, "class_name": prediction.get("class_name") if prediction else (truth.get("class_name") if truth else None),
        "truth_class_name": truth.get("class_name") if truth else None, "match_status": match_status, "iou": iou,
        "status": "valid" if valid else ("rejected" if prediction is not None or truth is not None else "failure"),
        "false_valid": bool(false_valid), "selected_geometry": geometry,
        "canvas_selection_status": result.get("canvas_selection_status") if result else None,
        "selected_source": result.get("selected_source") if result else None,
        "stage4_absolute_geometry_valid": result.get("absolute_geometry_valid") if result else None,
        "gt_used_for_inference": layer == "L0"}
    if valid and truth is not None:
        row.update(geometry_errors(geometry, truth))
    return row


def canvas_rows(sample_id: str, objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for obj in objects:
        direct = obj.get("canvas_results", {}).get(CANVASES[0], {}); letter = obj.get("canvas_results", {}).get(CANVASES[1], {})
        dg, lg = direct.get("geometry"), letter.get("geometry")
        row = {"sample_id": sample_id, "logical_object_id": obj["logical_object_id"], "canvas_selection_status": obj.get("canvas_selection_status"),
               "direct_status": direct.get("status"), "letterbox_status": letter.get("status")}
        for name, field in (("distance", "distance_m"), ("height", "object_height_m"), ("bottom", "bottom_height_m"), ("visible_height", "object_height_m")):
            row[f"{name}_prediction_delta_m"] = abs(float(dg[field]) - float(lg[field])) if dg and lg else None
        rows.append(row)
    return rows


def main() -> None:
    started = time.perf_counter(); OUT.mkdir(parents=True, exist_ok=True)
    parents = sorted([p for folder in (ROOT / "samples" / "stage5_1_analytic", ROOT / "samples" / "stage5_1_appearance") if folder.is_dir()
                      for p in folder.iterdir() if p.is_dir() and (p / "source_rgb_from_rgb565.png").is_file()])
    images = [p / "source_rgb_from_rgb565.png" for p in parents]
    worker_out = OUT / "runtime" / "yolo"
    detect_outputs = run_yolo_worker(VISION_PYTHON, WORKER, DETECT_MODEL, "detect", images, worker_out, expected_sha256=DETECT_SHA, timeout_s=1200)
    segment_outputs = run_yolo_worker(VISION_PYTHON, WORKER, SEGMENT_MODEL, "segment", images, worker_out, expected_sha256=SEGMENT_SHA, timeout_s=1200)
    bridge = DeltarBridge(FORMAL, CHECKPOINT, device="cuda:0")
    geometry_profile = json.loads((ROOT / "geometry_profile.json").read_text(encoding="utf-8"))
    matrix_scenes = []; all_rows: dict[str, list[dict[str, Any]]] = {key: [] for key in ("L0", "L1", "L2", "L3")}
    canvas_evidence = []; rgb_evidence = []; paired = []; stage4_calls = 0
    perception_totals = {"L2": Counter(), "L3": Counter()}
    for parent, detect_item, segment_item in zip(parents, detect_outputs, segment_outputs, strict=True):
        sample_id = parent.name
        metadata = json.loads((parent / "scene_profile.json").read_text(encoding="utf-8")); scene = json.loads(json.dumps(metadata))
        # scene_profile stores ToF fields at the top; the authoritative scene is retained in render metadata.
        source_meta = json.loads((parent / "direct_640x360" / "source_metadata.json").read_text(encoding="utf-8"))
        truth_doc = json.loads((parent / "ground_truth.json").read_text(encoding="utf-8"))
        calibration = json.loads((parent / "calibration.json").read_text(encoding="utf-8"))
        # Restore compact scene labels needed by result reporting.
        scene = {"scene_id": truth_doc["scene_id"], "scenario_family": metadata["scenario_family"], "appearance_kind": metadata["appearance_kind"],
                 "appearance_asset_id": metadata.get("appearance_asset_id"), "tof": {"measurement_semantics": metadata["measurement_semantics"]}}
        truths = truth_doc["objects"]
        with np.load(parent / "ground_truth_masks.npz", allow_pickle=False) as archive: truth_masks = {key: archive[key] for key in archive.files}
        depths: dict[str, np.ndarray] = {}; deltar_hashes = []
        for canvas in CANVASES:
            run = run_sample(parent / canvas, OUT / "runtime" / "deltar" / sample_id, FORMAL, CHECKPOINT, "cuda:0", bridge=bridge)
            folder = OUT / "runtime" / "deltar" / sample_id / canvas / run["profile_id"]
            depths[canvas] = np.load(folder / "depth_source_640x360.npy", allow_pickle=False)
            deltar_hashes.append(raw_rgb_hash(folder / "decoded_rgb.png"))
        expected_hash = source_meta["decoded_rgb_sha256"]
        yolo_hash = raw_rgb_hash(parent / "source_rgb_from_rgb565.png")
        rgb_evidence.append({"sample_id": sample_id, "expected_decoded_rgb_sha256": expected_hash, "yolo_input_rgb_sha256": yolo_hash,
                             "deltar_source_rgb_sha256": deltar_hashes, "all_equal": yolo_hash == expected_hash and all(x == expected_hash for x in deltar_hashes),
                             "gt_used_for_inference": False})
        depth_dir = OUT / "runtime" / "stage3" / sample_id; depth_dir.mkdir(parents=True, exist_ok=True)
        depth_refs = {}
        for canvas, depth in depths.items():
            path = depth_dir / f"{canvas}.npy"; np.save(path, depth, allow_pickle=False); depth_refs[canvas] = path.relative_to(ROOT).as_posix()
        full = process_full_new_scene(sample_id, depths, detect_item["document"], segment_item["document"], segment_item["masks"],
                                      segment_item["npz_path"], depth_refs, calibration, geometry_profile, ROOT)
        stage4_calls += len(full["objects"]); canvas_evidence.extend(canvas_rows(sample_id, full["objects"]))
        l2_match = classify_prediction_universe(full["predictions"], truths, prediction_masks=segment_item["masks"], truth_masks=truth_masks)
        l2_valid = {i: full["objects"][i]["status"] == "success" for i in range(len(full["objects"]))}
        l2_counts = perception_counts(l2_match, l2_valid); perception_totals["L2"].update({k: v for k, v in l2_counts.items() if isinstance(v, int)})
        l2_by_truth = {}
        for match in l2_match:
            pi, ti = match["prediction_index"], match["truth_index"]
            if pi is None:
                row = result_row("L2", sample_id, scene, truths[ti], None, "fn")
            else:
                truth = truths[ti] if ti is not None else None; result = full["objects"][pi]; false_valid = l2_valid[pi] and match["match_status"] != "tp"
                row = result_row("L2", sample_id, scene, truth, result, match["match_status"], prediction=full["predictions"][pi], iou=match["iou"], false_valid=false_valid)
                if match["match_status"] == "tp": l2_by_truth[ti] = row
            all_rows["L2"].append(row)
        tof = json.loads((parent / "tof.json").read_text(encoding="utf-8")); orientation = json.loads((parent / "direct_640x360" / "profile.json").read_text(encoding="utf-8"))["stage5_tof_profile"]["zone_orientation"]
        old_results = [evaluate_old_detection(det, tof, calibration, orientation) for det in detect_item["document"]["instances"]]
        l3_match = classify_prediction_universe(detect_item["document"]["instances"], truths)
        l3_valid = {i: old_results[i]["status"] == "valid" for i in range(len(old_results))}
        l3_counts = perception_counts(l3_match, l3_valid); perception_totals["L3"].update({k: v for k, v in l3_counts.items() if isinstance(v, int)})
        l3_by_truth = {}
        for match in l3_match:
            pi, ti = match["prediction_index"], match["truth_index"]
            if pi is None: row = result_row("L3", sample_id, scene, truths[ti], None, "fn")
            else:
                truth = truths[ti] if ti is not None else None; old = old_results[pi]
                adapted = {"status": old["status"], "geometry": old.get("geometry"), "selected_geometry": old.get("geometry")}
                row = result_row("L3", sample_id, scene, truth, adapted, match["match_status"], prediction=detect_item["document"]["instances"][pi], iou=match["iou"], false_valid=l3_valid[pi] and match["match_status"] != "tp")
                if match["match_status"] == "tp": l3_by_truth[ti] = row
            all_rows["L3"].append(row)
        gt_depth = np.load(parent / "ground_truth_depth.npy", allow_pickle=False); oracle_depths = {canvas: gt_depth for canvas in CANVASES}
        oracle_dir = OUT / "runtime" / "oracle" / sample_id; oracle_dir.mkdir(parents=True, exist_ok=True); oracle_refs = {}
        for canvas in CANVASES:
            path = oracle_dir / f"{canvas}.npy"; np.save(path, gt_depth, allow_pickle=False); oracle_refs[canvas] = path.relative_to(ROOT).as_posix()
        l0_objects = process_mask_oracle_scene(sample_id, truth_masks, truths, oracle_depths, parent / "ground_truth_masks.npz", oracle_refs, calibration, geometry_profile, ROOT, "L0")
        l1_objects = process_mask_oracle_scene(sample_id, truth_masks, truths, depths, parent / "ground_truth_masks.npz", depth_refs, calibration, geometry_profile, ROOT, "L1")
        stage4_calls += len(l0_objects) + len(l1_objects)
        for layer, objects in (("L0", l0_objects), ("L1", l1_objects)):
            all_rows[layer].extend(result_row(layer, sample_id, scene, truth, obj, "oracle_isolation") for truth, obj in zip(truths, objects, strict=True))
        for ti in sorted(set(l2_by_truth) & set(l3_by_truth)):
            new, old = l2_by_truth[ti], l3_by_truth[ti]
            if new["status"] == old["status"] == "valid":
                paired.append({"sample_id": sample_id, "scene_id": scene["scene_id"], "truth_object_id": truths[ti]["object_id"], "class_name": truths[ti]["class_name"],
                               "appearance_asset_id": scene.get("appearance_asset_id"), "new": new, "old": old})
        matrix_scenes.append({"sample_id": sample_id, "scene": scene, "truth_count": len(truths), "stage3_1": full,
                              "l2_perception": l2_counts, "l3_perception": l3_counts, "rgb565_shared_source_valid": rgb_evidence[-1]["all_equal"]})
    layer_summaries = {layer: summarize_rows(rows) for layer, rows in all_rows.items()}
    matrix = {"schema_version": "deltar_stage5_1_full_matrix.v1", "status": "success", "sample_count": len(parents),
              "base_scene_count": len({json.loads((p / "ground_truth.json").read_text(encoding="utf-8"))["scene_id"] for p in parents}),
              "object_truth_cases": sum(s["truth_count"] for s in matrix_scenes), "stage4_process_object_group_call_count": stage4_calls,
              "stage3_1_frozen_methods": {"segmentation_primary": "segment_eroded_3px + mad_median", "detection_fallback": "bbox_full + median"},
              "layers": layer_summaries, "rows": all_rows, "scenes": matrix_scenes, "gt_used_for_inference": False,
              "performance": {"total_ms": (time.perf_counter() - started) * 1000}}
    write("full_matrix.json", matrix)
    perception = {"schema_version": "deltar_stage5_1_perception_summary.v1", "layers": {layer: dict(counts) for layer, counts in perception_totals.items()},
                  "per_image_false_valid_rate": {layer: perception_totals[layer]["false_valid_count"] / max(len(parents), 1) for layer in ("L2", "L3")}}
    write("perception_summary.json", perception)
    write("false_valid_summary.json", {"schema_version": "deltar_stage5_1_false_valid_summary.v1", **perception})
    delta_fields = ("distance_prediction_delta_m", "height_prediction_delta_m", "bottom_prediction_delta_m", "visible_height_prediction_delta_m")
    canvas_summary = {"schema_version": "deltar_stage5_1_canvas_prediction_summary.v1", "object_count": len(canvas_evidence), "rows": canvas_evidence,
                      "selection_status_counts": dict(Counter(r["canvas_selection_status"] for r in canvas_evidence)),
                      "prediction_deltas": {field: distribution([r[field] for r in canvas_evidence if r[field] is not None]) for field in delta_fields}}
    write("canvas_prediction_summary.json", canvas_summary)
    metrics = ("distance_absolute_error_m", "visible_height_absolute_error_m", "height_absolute_error_m", "bottom_height_absolute_error_m")
    paired_summary = {"schema_version": "deltar_stage5_1_old_vs_new_summary.v1", "paired_object_count": len(paired),
                      "unique_source_asset_paired_count": len({r["appearance_asset_id"] for r in paired if r["appearance_asset_id"]}),
                      "placement_paired_count": len({(r["scene_id"], r["appearance_asset_id"]) for r in paired if r["appearance_asset_id"]}),
                      "metrics": {metric: {"new": distribution([r["new"][metric] for r in paired if r["new"].get(metric) is not None and r["old"].get(metric) is not None]),
                                           "old": distribution([r["old"][metric] for r in paired if r["new"].get(metric) is not None and r["old"].get(metric) is not None])} for metric in metrics},
                      "rows": paired}
    write("old_vs_new_summary.json", paired_summary)
    write("class_summary.json", {"schema_version": "deltar_stage5_1_class_summary.v1", "truth": dict(Counter(r["truth_class_name"] for r in all_rows["L2"] if r.get("truth_class_name"))),
                                  "predicted": dict(Counter(r["class_name"] for r in all_rows["L2"] if r.get("class_name")))})
    write("scenario_summary.json", {"schema_version": "deltar_stage5_1_scenario_summary.v1", "counts": dict(Counter(r["scenario_family"] for r in all_rows["L2"]))})
    semantic_rows = {semantic: [r for r in all_rows["L1"] if r["measurement_semantics"] == semantic] for semantic in ("radial_range", "camera_axial_z")}
    write("tof_semantics_summary.json", {"schema_version": "deltar_stage5_1_tof_semantics_summary.v1", "semantics": {key: summarize_rows(value) for key, value in semantic_rows.items()},
                                         "winner_by_median_distance": min(semantic_rows, key=lambda key: summarize_rows(semantic_rows[key])["distance_absolute_error_m"]["median"] or float("inf"))})
    rgb_doc = json.loads((OUT / "rgb565_source_validation.json").read_text(encoding="utf-8")) if (OUT / "rgb565_source_validation.json").is_file() else {"rows": []}
    rgb_doc["model_execution_rows"] = rgb_evidence; rgb_doc["model_execution_all_equal"] = all(r["all_equal"] for r in rgb_evidence); write("rgb565_source_validation.json", rgb_doc)
    print(json.dumps({"status": "success", "samples": len(parents), "stage4_calls": stage4_calls, "paired": len(paired), "rgb_shared": all(r["all_equal"] for r in rgb_evidence), "total_s": (time.perf_counter() - started)}, indent=2))


if __name__ == "__main__":
    main()
