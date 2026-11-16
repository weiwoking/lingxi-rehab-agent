#!/usr/bin/env python3
"""Isolated generic all-object Geometry v2 replay and regression runner."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

WORKSPACE = Path(r"C:\ec\_calibration_workspaces\stage3b_competition_geometry_20260818_01")
ROOT = WORKSPACE / "candidate_geometry_v2"
RESULTS = ROOT / "results"
CONFIG_PATH = ROOT / "config.v2.json"
PIPELINE_SRC = Path(r"C:\ec\_deltar_p4_pipeline\src")
VALIDATED = Path(r"C:\ec\_deltar_scan_service_data\validated")
EXPECTED_PYTHON = Path(r"C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe")
for path in (WORKSPACE, ROOT, PIPELINE_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import stage3b_competition_geometry as stage3b  # noqa: E402
from stage2_dynamic_fr_adapter import stage2_camera_model  # noqa: E402
from generic_support import bbox_mask, estimate_support, group_same_surfaces  # noqa: E402
from generic_extent import estimate_generic_extent  # noqa: E402
from shape_router import route_shape  # noqa: E402
from upright_slender import estimate_upright_slender  # noqa: E402
from roughly_spherical import estimate_roughly_spherical  # noqa: E402


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    stage3b.atomic_json(path, value)


def _safe_replace(path: Path) -> None:
    resolved, root = path.resolve(), RESULTS.resolve()
    if root not in resolved.parents:
        raise RuntimeError(f"refusing to replace outside v2 results: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def _selected_region(group: dict[str, Any], masks: dict[str, np.ndarray], axial: np.ndarray, world: np.ndarray, profile: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, list[float], dict[str, Any]]:
    import deltar_p4_pipeline.all_object_geometry as aog
    source, selected = aog._selected_observation(group)
    bbox = [float(v) for v in selected["bbox_xyxy"]]
    mask_key = selected.get("mask_key")
    raw_mask = masks.get(str(mask_key)) if mask_key is not None else None
    if raw_mask is not None and np.asarray(raw_mask).shape != axial.shape:
        raw_mask = None
    finite = np.isfinite(axial) & (axial > 0) & np.isfinite(world[..., 2])
    minimum = int(profile["min_points"])
    region = None
    keep = None
    method = None
    if raw_mask is not None and np.any(raw_mask):
        raw = np.asarray(raw_mask, dtype=bool)
        eroded = aog.erode_mask_one_pixel(raw)
        if np.count_nonzero(eroded & finite) >= minimum:
            region, keep, method = eroded, aog._robust_keep(eroded, finite, axial, profile), "mask_eroded_1px_robust"
        else:
            region, keep, method = raw, aog._robust_keep(raw, finite, axial, profile), "mask_raw_robust"
    if region is None:
        region = aog._bbox_mask(bbox, 1.0)
        cluster = aog._main_depth_cluster(region, finite, axial, profile)
        keep = cluster if cluster is not None else aog._robust_keep(region, finite, axial, profile)
        method = "bbox_main_depth_cluster" if cluster is not None else "bbox_robust"
    valid = keep & finite
    silhouette = np.asarray(raw_mask, dtype=bool) if raw_mask is not None and np.any(raw_mask) else bbox_mask(bbox, axial.shape)
    depth_values = axial[valid]
    meta = {"selected_source": source, "region_method": method, "point_count": int(np.count_nonzero(valid)), "depth_iqr_mm": float(np.percentile(depth_values, 75) - np.percentile(depth_values, 25)) if depth_values.size else None}
    return region, valid, None if raw_mask is None else np.asarray(raw_mask, dtype=bool), bbox, meta


class Capture:
    records: list[dict[str, Any]] = []


def processor_class(base: type, camera: dict[str, Any], gravity: dict[str, Any], config: dict[str, Any]) -> type:
    class V2Processor(base):
        def process_frame(self, frame_id, sequence, logical_groups, segment_masks, camera_axial_z_mm, world_points_or_z_mm, support_planes, source_image_path):
            baseline_frame = super().process_frame(frame_id, sequence, logical_groups, segment_masks, camera_axial_z_mm, world_points_or_z_mm, support_planes, source_image_path)
            axial, world = np.asarray(camera_axial_z_mm), np.asarray(world_points_or_z_mm)
            groups = {str(g.get("logical_object_id")): g for g in logical_groups}
            frame_records = []
            for baseline in baseline_frame.get("objects", []):
                if baseline.get("geometry_status") != "success":
                    continue
                group = groups.get(str(baseline.get("logical_object_id")))
                if group is None:
                    continue
                region, valid, raw_mask, bbox, meta = _selected_region(group, segment_masks, axial, world, self.profile)
                support = estimate_support(raw_mask if raw_mask is not None else region, bbox, axial, world, valid, support_planes, config["generic_support"])
                generic = estimate_generic_extent(valid, world[..., 2], float(support["support_mm"]), config["generic_extent"])
                router = route_shape(str(baseline.get("class_name", "")), raw_mask, bbox, float(support["confidence"]), meta["depth_iqr_mm"], config["router"])
                enhancement: dict[str, Any] = {"available": False, "reason": "generic_family_selected"}
                if router["selected_family"] == "upright_slender" and raw_mask is not None:
                    enhancement = estimate_upright_slender(raw_mask, float(support["support_mm"]), camera, gravity, config["upright_slender"])
                elif router["selected_family"] == "roughly_spherical":
                    enhancement = estimate_roughly_spherical(raw_mask, bbox, float(support["support_mm"]), axial, world, camera, gravity, config["roughly_spherical"])
                if enhancement.get("available"):
                    top_disagreement = abs(float(enhancement["top_mm"]) - float(generic["top_mm"]))
                    enhancement["generic_top_disagreement_mm"] = top_disagreement
                    if top_disagreement > float(config["generic_extent"]["maximum_enhancer_top_disagreement_mm"]):
                        enhancement["available"] = False
                        enhancement["reason"] = "generic_top_consistency_gate"
                if enhancement.get("available"):
                    final_support = float(enhancement.get("support_override_mm", support["support_mm"]))
                    final_top = float(enhancement["top_mm"])
                    final = {"support_mm": final_support, "support_source": support["source"] if "support_override_mm" not in enhancement else "shape_fit_support", "top_mm": final_top, "object_height_mm": final_top - final_support, "extent_method": enhancement["method"], "extent_confidence": enhancement["confidence"], "estimator_path": f"generic_support->{router['selected_family']}"}
                else:
                    final = {"support_mm": float(support["support_mm"]), "support_source": support["source"], "top_mm": float(generic["top_mm"]), "object_height_mm": float(generic["object_height_mm"]), "extent_method": generic["method"], "extent_confidence": generic["confidence"], "estimator_path": "generic_support->generic_extent"}
                    if router["selected_family"] != "generic":
                        router["fallback_reason"] = f"enhancer_failed:{enhancement.get('reason')}"
                if not all(np.isfinite([final["support_mm"], final["top_mm"], final["object_height_mm"]])) or final["object_height_mm"] <= 0:
                    raise ValueError("v2 must preserve finite positive all-object geometry")
                frame_records.append({"sample_frame": frame_id, "sequence": sequence, "logical_object_id": baseline["logical_object_id"], "class_name": baseline["class_name"], "bbox_xyxy": bbox, "baseline": {"support_source": baseline["diagnostics"].get("support_source"), "support_mm": baseline["geometry"]["support_height_mm"], "top_mm": baseline["geometry"]["top_height_above_floor_mm"], "object_height_mm": baseline["geometry"]["object_height_mm"]}, "generic_support": support, "generic_extent": generic, "router": router, "enhancement": enhancement, "final": final, "same_surface_group_id": None, "consensus_used": False, "source_meta": meta})
            group_same_surfaces(frame_records, config["same_surface"], frame_id)
            Capture.records.extend(frame_records)
            return baseline_frame
    return V2Processor


def _run(scan_id: str, sample_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    scan = VALIDATED / scan_id
    if not scan.exists():
        raise RuntimeError(f"validated scan missing: {scan}")
    stage2, stage3a, _ = stage3b.verify_authorities()
    config = read(CONFIG_PATH)
    camera = stage2_camera_model(stage2)
    output = RESULTS / sample_id / "replay_chain"
    _safe_replace(output)
    import deltar_p4_pipeline.all_object_geometry as aog
    original = aog.AllObjectGeometryProcessor
    Capture.records = []
    aog.AllObjectGeometryProcessor = processor_class(original, camera, stage3a, config)
    try:
        raw, _, _ = stage3b._run_formal_candidate_chain(scan, output, sample_id, stage2, stage3a)
    finally:
        aog.AllObjectGeometryProcessor = original
    result_dir = RESULTS / sample_id
    write(result_dir / "object_records.v2.json", Capture.records)
    write(result_dir / "replay_manifest.v2.json", {"schema_version": "stage3b_all_object_geometry_v2_replay.v1", "sample_id": sample_id, "scan_id": scan_id, "record_count": len(Capture.records), "python_executable": sys.executable, "ground_truth_used_for_fit": False, "ground_truth_used_for_parameter_selection": False, "scene_target_name_special_cases": False, "class_specific_fixed_compensation": False, "formal_runtime_modified": False})
    return raw, Capture.records


def _aggregate_competition(sample_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    sample = read(WORKSPACE / "results" / sample_id / "sample_result.stage3b.json")
    v1 = read(WORKSPACE / "candidate_geometry" / "results" / sample_id / "baseline_vs_candidate.json")
    logical_to_target = {logical: target for target, value in sample["raw"]["targets"].items() for logical in value["logical_objects"]}
    for record in records:
        record["evaluation_target"] = logical_to_target.get(record["logical_object_id"])
    output = {"sample_id": sample_id, "role": sample["acceptance"]["role"], "ground_truth_used_for_fit": False, "ground_truth_used_for_parameter_selection": False, "targets": {}}
    for target, baseline_target in sample["raw"]["targets"].items():
        selected = [r for r in records if r.get("evaluation_target") == target]
        truth = baseline_target["truth"]
        def median(path: tuple[str, str]) -> float:
            return float(np.median([r[path[0]][path[1]] for r in selected]))
        support, top = median(("final", "support_mm")), median(("final", "top_mm"))
        height = top - support
        baseline_support = float(baseline_target["support_height_mm"]["median"])
        baseline_height = float(baseline_target["object_height_mm"]["median"])
        b = {"support_mm": baseline_support, "top_mm": baseline_support + baseline_height, "object_height_mm": baseline_height, "support_source": baseline_target["support_source"]}
        one = v1["targets"][target]["candidate"]
        sources = Counter(r["final"]["support_source"] for r in selected)
        families = Counter(r["router"]["selected_family"] for r in selected)
        group_ids = sorted({str(r["same_surface_group_id"]) for r in selected if r["same_surface_group_id"]})
        v2 = {"support_mm": support, "top_mm": top, "object_height_mm": height, "support_source": sources.most_common(1)[0][0], "support_source_counts": dict(sources), "support_estimator_path": Counter(r["final"]["estimator_path"] for r in selected).most_common(1)[0][0], "selected_shape_family": families.most_common(1)[0][0], "shape_family_counts": dict(families), "family_confidence_median": float(np.median([r["router"]["family_confidence"] for r in selected])), "extent_confidence_median": float(np.median([r["final"]["extent_confidence"] for r in selected])), "frame_count": len(selected), "same_surface_group_ids": group_ids, "consensus_used": any(r["consensus_used"] for r in selected), "support_error_mm": support - float(truth["expected_support_mm"]), "top_error_mm": top - float(truth["diagnostic_expected_top_z_mm"]), "object_height_error_mm": height - float(truth["expected_object_height_mm"]), "object_height_error_percent": 100 * (height - float(truth["expected_object_height_mm"])) / float(truth["expected_object_height_mm"])}
        b.update({"support_error_mm": b["support_mm"] - float(truth["expected_support_mm"]), "object_height_error_mm": b["object_height_mm"] - float(truth["expected_object_height_mm"])})
        output["targets"][target] = {"baseline": b, "candidate_v1": one, "candidate_v2": v2, "truth": truth}
    lower = [output["targets"][t]["candidate_v2"] for t in output["targets"] if output["targets"][t]["candidate_v2"]["consensus_used"]]
    output["same_surface_consistency"] = {"group_discovered_geometrically": bool(lower), "support_delta_between_grouped_target_medians_mm": 0.0 if len(lower) >= 2 else None, "consensus_used": bool(lower), "group_ids": sorted({g for item in lower for g in item["same_surface_group_ids"]})}
    write(RESULTS / sample_id / "baseline_v1_v2_comparison.json", output)
    return output


def replay_competition() -> dict[str, Any]:
    registry = stage3b.load_registry()
    outputs = {}
    for sample_id in ("G001", "G002"):
        sample = next(s for s in registry["samples"] if s["sample_id"] == sample_id)
        _, records = _run(sample["scan_id"], sample_id)
        outputs[sample_id] = _aggregate_competition(sample_id, records)
    write(RESULTS / "G001_G002_SUMMARY.v2.json", outputs)
    return outputs


def heldout_inventory() -> dict[str, Any]:
    config = read(CONFIG_PATH)["heldout"]
    selected = str(config["selected_scan_id"])
    validated_ids = sorted(p.name for p in VALIDATED.iterdir() if p.is_dir())
    artifact_candidates = []
    roots = [Path(r"C:\ec\_codex_replays"), Path(r"C:\ec\_codex_work"), Path(r"C:\ec\_stage8b4b3_replay_20260726_030615")]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("stage8b_all_object_geometry.v1.json"):
            try:
                doc = read(path)
            except Exception:
                continue
            sid = str(doc.get("scan_id", ""))
            if sid in {"scan_20260818_092411_000", "scan_20260818_092729_000"}:
                continue
            classes = Counter(o.get("class_name") for f in doc.get("frames", []) for o in f.get("objects", []) if o.get("geometry_status") == "success")
            artifact_candidates.append({"scan_id": sid, "path": str(path), "frame_count": len(doc.get("frames", [])), "success_object_count": int(sum(classes.values())), "classes": dict(classes), "validated_scan_available": sid in validated_ids, "excluded_calibration_only": False})
    chosen_artifacts = [v for v in artifact_candidates if v["scan_id"] == selected]
    output = {"schema_version": "stage3b_unrelated_heldout_inventory.v1", "searched_validated_root": str(VALIDATED), "validated_scan_count": len(validated_ids), "excluded_scan_ids": ["scan_20260818_092411_000", "scan_20260818_092729_000"], "calibration_exclusion_policy": "exclude known Stage2/Stage3A ChArUco scan IDs and calibration-only artifacts; selected scene has diverse COCO objects", "artifact_candidates": artifact_candidates, "selected": {"scan_id": selected, "scan_path": str(VALIDATED / selected), "artifacts": chosen_artifacts, "selection_reason": config["selection_reason"], "heldout_truth_source": config["truth_source"], "accuracy_claim_allowed": False}}
    write(RESULTS / "UNRELATED_HELDOUT_INVENTORY.json", output)
    return output


def replay_heldout() -> dict[str, Any]:
    inventory = heldout_inventory()
    scan_id = inventory["selected"]["scan_id"]
    raw, records = _run(scan_id, "H001")
    baseline_by_id = {(f["frame_id"], o["logical_object_id"]): o for f in raw["frames"] for o in f["objects"] if o.get("geometry_status") == "success"}
    checks = []
    cfg = read(CONFIG_PATH)["heldout"]
    for r in records:
        baseline = baseline_by_id[(r["sample_frame"], r["logical_object_id"])]
        b, v = baseline["geometry"], r["final"]
        finite = bool(np.isfinite([v["support_mm"], v["top_mm"], v["object_height_mm"]]).all())
        positive = v["object_height_mm"] > 0 and v["top_mm"] > v["support_mm"]
        jump = abs(v["object_height_mm"] - b["object_height_mm"])
        ratio = max(v["object_height_mm"], b["object_height_mm"]) / max(min(v["object_height_mm"], b["object_height_mm"]), 1e-6)
        catastrophic = (not finite) or (not positive) or (jump > float(cfg["catastrophic_height_jump_mm"]) and ratio > float(cfg["catastrophic_ratio"]))
        checks.append({"frame_id": r["sample_frame"], "logical_object_id": r["logical_object_id"], "class_name": r["class_name"], "baseline": {"support_mm": b["support_height_mm"], "top_mm": b["top_height_above_floor_mm"], "object_height_mm": b["object_height_mm"], "support_source": baseline["diagnostics"].get("support_source")}, "candidate_v2": v, "selected_shape_family": r["router"]["selected_family"], "family_confidence": r["router"]["family_confidence"], "same_surface_group_id": r["same_surface_group_id"], "finite": finite, "positive_extent": positive, "height_jump_mm": jump, "height_ratio": ratio, "catastrophic_regression": catastrophic})
    output = {"schema_version": "stage3b_unrelated_heldout_replay.v1", "scan_id": scan_id, "heldout_truth_source": None, "accuracy_claim": False, "baseline_object_count": len(baseline_by_id), "candidate_object_count": len(records), "finite_count": sum(v["finite"] for v in checks), "positive_extent_count": sum(v["positive_extent"] for v in checks), "catastrophic_regression_count": sum(v["catastrophic_regression"] for v in checks), "class_counts": dict(Counter(v["class_name"] for v in checks)), "family_counts": dict(Counter(v["selected_shape_family"] for v in checks)), "support_source_counts": dict(Counter(v["candidate_v2"]["support_source"] for v in checks)), "checks": checks}
    write(RESULTS / "UNRELATED_HELDOUT_REPLAY.json", output)
    return output


def summary() -> None:
    p = RESULTS / "G001_G002_SUMMARY.v2.json"
    if p.exists():
        for sample, doc in read(p).items():
            print(f"[{sample}]")
            for target, item in doc["targets"].items():
                v = item["candidate_v2"]
                print(f"{target}: support={v['support_mm']:.1f} ({v['support_source']}) height={v['object_height_mm']:.1f} family={v['selected_shape_family']} frames={v['frame_count']}")
    h = RESULTS / "UNRELATED_HELDOUT_REPLAY.json"
    if h.exists():
        d = read(h)
        print(f"[H001] objects={d['candidate_object_count']} finite={d['finite_count']} catastrophic={d['catastrophic_regression_count']} families={d['family_counts']}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("replay-competition", help="replay G001/G002 baseline and v2")
    sub.add_parser("replay-heldout", help="inventory and replay unrelated H001")
    sub.add_parser("replay-all", help="replay competition and held-out")
    sub.add_parser("inventory-heldout", help="refresh read-only held-out inventory")
    sub.add_parser("summary", help="print compact v2 results")
    sub.add_parser("status", help="print interpreter/config/results status")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "status":
        print(f"PYTHON_EXECUTABLE = {sys.executable}")
        print(f"EXACT_INTERPRETER = {Path(sys.executable).resolve() == EXPECTED_PYTHON.resolve()}")
        print(f"CONFIG = {CONFIG_PATH}")
        print(f"COMPETITION_RESULTS = {(RESULTS / 'G001_G002_SUMMARY.v2.json').exists()}")
        print(f"HELDOUT_RESULTS = {(RESULTS / 'UNRELATED_HELDOUT_REPLAY.json').exists()}")
        return 0
    if args.command == "summary":
        summary(); return 0
    if Path(sys.executable).resolve() != EXPECTED_PYTHON.resolve():
        raise RuntimeError(f"exact interpreter required: {EXPECTED_PYTHON}")
    if args.command == "inventory-heldout":
        heldout_inventory()
    elif args.command == "replay-competition":
        replay_competition(); summary()
    elif args.command == "replay-heldout":
        replay_heldout(); summary()
    else:
        replay_competition(); replay_heldout(); summary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
