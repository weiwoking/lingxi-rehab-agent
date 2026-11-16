from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from deltar_p4_pipeline.synthetic_geometry import rectangle_depth, synthetic_calibration

ROOT = Path(__file__).resolve().parents[1]


def build_scenes() -> list[dict]:
    definitions = [
        ("S1_front_rectangle_on_floor", {}, {"distance_m": 2.4, "bottom_m": 0.0, "height_m": 1.0}),
        ("S2_elevated_rectangle", {}, {"distance_m": 2.2, "bottom_m": 0.35, "height_m": 0.7}),
        ("S3_camera_pitched_down", {"pitch_deg": -5.0}, {"distance_m": 2.7, "bottom_m": 0.0, "height_m": 0.9}),
        ("S4_camera_yaw_roll", {"yaw_deg": 5.0, "roll_deg": 3.0}, {"distance_m": 2.8, "bottom_m": 0.2, "height_m": 0.75}),
        ("S5_slanted_planar_object", {}, {"distance_m": 2.6, "bottom_m": 0.1, "height_m": 0.9, "slant_m": 0.2}),
    ]
    scenes = []
    for name, calibration_args, rectangle_args in definitions:
        calibration = synthetic_calibration(**calibration_args)
        depth, mask, truth = rectangle_depth(calibration, **rectangle_args)
        scenes.append({"scene_id": name, "calibration": calibration, "depth": depth, "mask": mask, "truth": truth})
    base_cal = synthetic_calibration()
    depth, mask, truth = rectangle_depth(base_cal, distance_m=2.3, bottom_m=0.0, height_m=0.9)
    contaminated = depth.copy()
    ring = np.zeros_like(mask)
    ys, xs = np.where(mask)
    ring[max(0, ys.min()-3):min(360, ys.max()+4), max(0, xs.min()-3):min(640, xs.max()+4)] = True
    ring &= ~mask
    contaminated[ring] = 4.5
    scenes.append({"scene_id": "S6_foreground_background_contamination", "calibration": base_cal, "depth": contaminated, "mask": mask | ring, "truth": truth, "erode_pixels": 3})
    dual = depth.copy()
    dual_mask = mask.copy()
    valid = np.argwhere(mask)
    chosen = valid[::5]
    dual[chosen[:, 0], chosen[:, 1]] = 4.0
    scenes.append({"scene_id": "S7_occlusion_dual_depth_peaks", "calibration": base_cal, "depth": dual, "mask": dual_mask, "truth": truth, "surface_depth_m": 2.3})
    bbox = np.zeros_like(mask)
    bbox[max(0, ys.min()-10):min(360, ys.max()+11), max(0, xs.min()-10):min(640, xs.max()+11)] = True
    fallback = depth.copy(); fallback[bbox & ~mask] = 4.0
    scenes.append({"scene_id": "S8_detection_bbox_fallback", "calibration": base_cal, "depth": fallback, "mask": bbox, "truth": truth, "source_type": "detection_fallback", "surface_depth_m": 2.3})
    scenes.append({"scene_id": "S9_direct_letterbox_equivalent", "calibration": base_cal, "depth": depth, "mask": mask, "truth": truth, "equivalent_pair": True})
    scenes.append({"scene_id": "S10_axial_z_vs_radial_range", "calibration": base_cal, "depth": depth, "mask": mask, "truth": truth, "semantics_case": True})
    return scenes


if __name__ == "__main__":
    print(json.dumps({"scene_count": len(build_scenes()), "scene_ids": [s["scene_id"] for s in build_scenes()]}, indent=2))
