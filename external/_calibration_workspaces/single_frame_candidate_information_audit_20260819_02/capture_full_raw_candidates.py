from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np


WORKSPACE = Path(__file__).resolve().parent
PIPELINE = Path(r"C:\ec\_deltar_p4_pipeline")
sys.path.insert(0, str(PIPELINE / "src"))

import deltar_p4_pipeline.all_object_geometry as all_geometry  # noqa: E402
from deltar_p4_pipeline.geometry_v2.contact_evidence import evaluate_contact  # noqa: E402
from deltar_p4_pipeline.geometry_v2.environment_surfaces import contact_context, extract_environment_surfaces  # noqa: E402
from deltar_p4_pipeline.geometry_v2.support_selector import _eligible, select_support  # noqa: E402


REPLAYS = {
    "G001": "scan_20260818_092411_numericfirst01",
    "G002": "scan_20260818_092729_numericfirst01",
    "SCAN_161718": "scan_20260818_161718_numericfirst01",
    "SCAN_165011": "scan_20260818_165011_numericfirst01",
    "SCAN_070604": "scan_20260819_070604_numericfirst01",
    "SCAN_085223": "scan_20260819_085223_numericfirst01",
}
RESULT_ROOT = Path(r"C:\ec\_deltar_scan_service_data\results")


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items() if not str(key).startswith("_")}
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, tuple):
        return [clean(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def point_summary(region: np.ndarray, bbox: list[float], axial: np.ndarray, world_z: np.ndarray, cfg: dict[str, Any]) -> dict[str, Any]:
    finite = np.isfinite(world_z) & np.isfinite(axial) & (axial > 0.0)
    context = contact_context(np.asarray(region, dtype=bool), bbox, cfg) & finite
    values = np.asarray(world_z[context], dtype=np.float64)
    if not values.size:
        return {"count": 0, "percentiles_mm": None, "histogram": []}
    low = math.floor(float(np.percentile(values, 1)) / 4.0) * 4.0
    high = math.ceil(float(np.percentile(values, 99)) / 4.0) * 4.0
    edges = np.arange(low, high + 8.0, 4.0)
    counts, edges = np.histogram(values, bins=edges)
    histogram = [
        {"center_mm": float((edges[index] + edges[index + 1]) / 2.0), "count": int(count)}
        for index, count in enumerate(counts) if count
    ]
    return {
        "count": int(values.size),
        "percentiles_mm": {str(q): float(np.percentile(values, q)) for q in (1, 5, 25, 50, 75, 95, 99)},
        "mean_mm": float(np.mean(values)),
        "std_mm": float(np.std(values)),
        "histogram_bin_mm": 4.0,
        "histogram": histogram,
    }


def main() -> int:
    captures: list[dict[str, Any]] = []
    current_frame: dict[str, Any] = {"frame_id": None, "sequence": None}
    original_process_frame = all_geometry.AllObjectGeometryProcessor.process_frame

    def wrapped_process_frame(self, frame_id, sequence, *args, **kwargs):
        current_frame["frame_id"] = str(frame_id)
        current_frame["sequence"] = int(sequence)
        try:
            return original_process_frame(self, frame_id, sequence, *args, **kwargs)
        finally:
            current_frame["frame_id"] = None
            current_frame["sequence"] = None

    def instrumented_estimate_support(region, bbox, axial_mm, world_points_mm, selected_valid, support_planes, cfg, gravity=None):
        world = np.asarray(world_points_mm)
        world_z = np.asarray(world[..., 2] if world.ndim == 3 else world, dtype=np.float64)
        axial = np.asarray(axial_mm, dtype=np.float64)
        observations, extraction = extract_environment_surfaces(
            np.asarray(region, dtype=bool), bbox, axial, world_z, support_planes, cfg
        )
        evidence = [
            evaluate_contact(
                surface, np.asarray(region, dtype=bool), bbox, axial, world,
                np.asarray(selected_valid, dtype=bool), gravity, cfg,
            )
            for surface in observations
        ]
        result = select_support(observations, evidence, cfg)
        result["diagnostics"]["surface_extraction"] = extraction
        captures.append({
            "frame_id": current_frame["frame_id"],
            "sequence": current_frame["sequence"],
            "bbox_xyxy": [float(value) for value in bbox],
            "raw_environment_points": point_summary(np.asarray(region, dtype=bool), bbox, axial, world_z, cfg),
            "extractor_output_count": len(observations),
            "observations": [],
            "selected_support": clean(result),
        })
        for index, (surface, row) in enumerate(zip(observations, evidence)):
            estimate, reasons = _eligible(surface, row, cfg)
            captures[-1]["observations"].append({
                "surface": clean(surface),
                "contact": clean(row),
                "observation_index": index,
                "selector_candidate": clean(estimate),
                "selector_rejection_reasons": reasons,
            })
        return result

    all_geometry.AllObjectGeometryProcessor.process_frame = wrapped_process_frame
    all_geometry.estimate_support = instrumented_estimate_support
    output: dict[str, Any] = {
        "schema_version": "full_raw_candidate_capture.v1",
        "production_modified": False,
        "capture_point": "selector input after production environment-surface extractor; before eligibility/diagnostic truncation",
        "scans": {},
    }
    try:
        for scan_label, replay_id in REPLAYS.items():
            captures.clear()
            root = RESULT_ROOT / replay_id
            destination = WORKSPACE / "replay_outputs" / f"{scan_label.lower()}_instrumented_geometry.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            document, _ = all_geometry.rebuild_all_object_geometry_from_cache(
                root,
                all_geometry.load_geometry_profile(PIPELINE / "profiles/formal_geometry_region_profile.json"),
                destination=destination,
            )
            pending: dict[tuple[str, tuple[float, ...]], list[dict[str, Any]]] = {}
            for item in captures:
                key = (item["frame_id"], tuple(round(value, 4) for value in item["bbox_xyxy"]))
                pending.setdefault(key, []).append(item)
            matched = []
            for frame in document["frames"]:
                for object_index, obj in enumerate(frame["objects"]):
                    if not obj.get("numeric_available"):
                        continue
                    key = (frame["frame_id"], tuple(round(float(value), 4) for value in obj["bbox_xyxy"]))
                    rows = pending.get(key) or []
                    if not rows:
                        raise RuntimeError(f"capture match missing: {scan_label} {key}")
                    capture = rows.pop(0)
                    capture.update({
                        "object_index": object_index,
                        "object_id": obj["logical_object_id"],
                        "class_name": obj["class_name"],
                        "current_support_mm": obj["geometry"]["support_height_mm"],
                        "current_status": obj["geometry_status"],
                        "region_source": obj.get("diagnostics", {}).get("region_source"),
                        "distance_mm": obj.get("geometry", {}).get("distance_mm"),
                    })
                    matched.append(capture)
            leftovers = sum(len(rows) for rows in pending.values())
            if leftovers:
                raise RuntimeError(f"unmatched captures for {scan_label}: {leftovers}")
            output["scans"][scan_label] = {
                "replay_id": replay_id,
                "object_count": len(matched),
                "objects": matched,
            }
    finally:
        all_geometry.AllObjectGeometryProcessor.process_frame = original_process_frame
    (WORKSPACE / "full_raw_candidate_capture.json").write_text(
        json.dumps(clean(output), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        scan: {
            "objects": item["object_count"],
            "observations": sum(obj["extractor_output_count"] for obj in item["objects"]),
        }
        for scan, item in output["scans"].items()
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
