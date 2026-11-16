from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .calibration import invert_transform, transform_points
from .errors import ErrorCode, PipelineError
from .rgb565 import encode_rgb565_le, roundtrip_metrics
from .scene_geometry import camera_rays, intersect_scene
from .stage5_contracts import TRUTH_SCHEMA, validate_scene
from .synthetic_geometry import rotation_xyz


def camera_profile(*, height_m: float = 1.0, roll_deg: float = 0.0, pitch_deg: float = 0.0, yaw_deg: float = 0.0) -> dict[str, Any]:
    base = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)
    transform = np.eye(4)
    transform[:3, :3] = rotation_xyz(roll_deg, pitch_deg, yaw_deg) @ base
    transform[:3, 3] = [0.0, 0.0, height_m]
    return {"width": 640, "height": 360, "fx": 520.0, "fy": 520.0, "cx": 319.5, "cy": 179.5,
            "distortion": [], "T_ground_from_camera": transform.tolist()}


def calibration_from_scene(scene: dict[str, Any]) -> dict[str, Any]:
    camera = scene["camera"]
    return {"schema_version": "deltar_geometry_calibration.v1", "profile_id": f"stage5_{scene['scene_id']}",
            "calibration_status": "synthetic", "image": {"width": 640, "height": 360},
            "camera_model": {"model": "pinhole", **{key: camera[key] for key in ("fx", "fy", "cx", "cy", "distortion")}},
            "depth": {"semantics": "axial_z", "semantics_status": "PROVEN", "scale_to_meter": 1.0},
            "transforms": {"T_ground_from_camera": camera["T_ground_from_camera"], "T_camera_from_tof": scene["tof"]["T_camera_from_tof"]},
            "mount_geometry_hint": {"camera_and_tof_coplanar": True, "camera_on_tof_long_axis_extension": True,
                                    "tof_physical_rotation_present": bool(scene["tof"].get("in_plane_rotation_deg", 0))},
            "ground": {"plane_z_m": 0.0}}


def _object(object_id: str, class_name: str, primitive: str, x: float, y: float, bottom: float, width: float, height: float,
            *, depth: float = 0.25, color: list[int] | None = None, slope: float = 0.0, texture: str | None = None) -> dict[str, Any]:
    return {"object_id": object_id, "class_name": class_name, "primitive": primitive,
            "center_bottom_ground_m": [x, y, bottom], "width_m": width, "height_m": height, "depth_m": depth,
            "slant_y_per_x": slope, "color_rgb": color or [190, 90, 45], "texture_path": texture}


def stage5_scene_family(texture_path: str | None = None) -> list[dict[str, Any]]:
    base_tof = {"resolution": [8, 8], "horizontal_fov_deg": 55.0, "vertical_fov_deg": 45.0,
                "in_plane_rotation_deg": 0, "T_camera_from_tof": np.eye(4).tolist(), "aggregation": "dominant_surface",
                "rays_per_zone": 25, "noise_sigma_mm": 8.0, "distance_sigma_slope_mm_per_m": 2.0, "bias_mm": 0.0,
                "invalid_zone_indices": [], "outlier_probability": 0.0, "approximate_sensor_model": True}
    def scene(sid: str, family: str, objects: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        tof = json.loads(json.dumps(base_tof)); tof.update(kwargs.pop("tof", {}))
        return {"schema_version": "deltar_stage5_scene.v1", "scene_id": sid, "scenario_family": family,
                "camera": kwargs.pop("camera", camera_profile()), "tof": tof, "background_wall_y_m": 6.0,
                "objects": objects, "appearance_kind": kwargs.pop("appearance_kind", "analytic"), **kwargs}
    scenes = [
        scene("F1_floor_boxes", "F1", [_object("box_near", "suitcase", "box", -0.7, 2.1, 0, .55, .55), _object("box_far", "suitcase", "box", .8, 3.4, 0, .8, 1.0, color=[60,130,210])]),
        scene("F2_tabletop", "F2", [_object("table", "dining table", "box", 0, 2.9, 0, 2.2, .75, depth=.8, color=[110,75,45]), _object("cup", "cup", "billboard", -.3, 2.42, .75, .18, .28, color=[220,220,210])]),
        scene("F3_large", "F3", [_object("chair", "chair", "billboard", .1, 2.7, 0, 1.15, 1.65, color=[65,145,75])]),
        scene("F4_background_contamination", "F4", [_object("foreground", "chair", "billboard", 0, 2.3, 0, .75, 1.2, color=[210,80,60])]),
        scene("F5_multidepth", "F5", [_object("near", "chair", "box", -.25, 2.0, 0, .9, 1.3), _object("far", "chair", "box", .3, 3.5, 0, 1.2, 1.8, color=[70,110,210])]),
        scene("F6_occlusion_20", "F6", [_object("target", "chair", "billboard", 0, 3.0, 0, 1.0, 1.6), _object("occluder", "suitcase", "box", -.42, 2.3, 0, .35, 1.1, color=[40,40,45])]),
        scene("F6_occlusion_40", "F6", [_object("target", "chair", "billboard", 0, 3.0, 0, 1.0, 1.6), _object("occluder", "suitcase", "box", -.28, 2.3, 0, .55, 1.25, color=[40,40,45])]),
        scene("F6_occlusion_60", "F6", [_object("target", "chair", "billboard", 0, 3.0, 0, 1.0, 1.6), _object("occluder", "suitcase", "box", -.12, 2.3, 0, .75, 1.4, color=[40,40,45])]),
        scene("F7_small_targets", "F7", [_object("small8", "bottle", "billboard", -.55, 4.4, .75, .07, .16), _object("small16", "bottle", "billboard", 0, 3.7, .7, .12, .25), _object("small32", "bottle", "billboard", .6, 2.8, .6, .20, .42)]),
        scene("F8_border", "F8", [_object("border_chair", "chair", "billboard", -1.95, 2.5, 0, 1.5, 1.6)]),
        scene("F9_slanted", "F9", [_object("slanted", "tv", "slanted_plane", 0, 2.8, .3, 1.8, 1.1, slope=.65, color=[50,70,90])]),
        scene("F10_noisy_tof", "F10", [_object("noisy", "chair", "box", 0, 2.7, 0, 1.0, 1.5)], tof={"noise_sigma_mm": 55.0, "bias_mm": 35.0, "invalid_zone_indices": [0,7,27,36,56,63], "outlier_probability": .08}),
        scene("F11_camera_pose", "F11", [_object("pose", "chair", "box", .3, 3.0, 0, 1.0, 1.5)], camera=camera_profile(height_m=1.25, roll_deg=3, pitch_deg=-3, yaw_deg=3)),
        scene("F12_mount", "F12", [_object("mount", "chair", "box", 0, 2.6, 0, 1.0, 1.5)], tof={"in_plane_rotation_deg": 90, "T_camera_from_tof": [[1,0,0,.06],[0,1,0,0],[0,0,1,0],[0,0,0,1]]}),
    ]
    if texture_path:
        for i, (x, distance, scale) in enumerate(((-.7, 2.4, .8), (0, 2.8, 1.0), (.7, 3.2, 1.2), (0, 2.5, .7), (0, 3.4, 1.1)), start=1):
            scenes.append(scene(f"B{i}_appearance_chair", f"B{i}", [_object("appearance_chair", "chair", "billboard", x, distance, 0, 1.0*scale, 1.55*scale, texture=texture_path)], appearance_kind="official_demo_billboard"))
    return scenes


def render_scene(scene: dict[str, Any]) -> dict[str, Any]:
    validate_scene(scene)
    origin, directions = camera_rays(scene["camera"])
    radial, labels, local = intersect_scene(origin, directions, scene)
    points_ground = origin + radial[..., None] * directions
    flat_ground = points_ground.reshape(-1, 3)
    finite_ground = np.all(np.isfinite(flat_ground), axis=1)
    flat_camera = np.full(flat_ground.shape, np.nan, dtype=np.float64)
    if finite_ground.any():
        flat_camera[finite_ground] = transform_points(flat_ground[finite_ground], invert_transform(scene["camera"]["T_ground_from_camera"]))
    points_camera = flat_camera.reshape(360, 640, 3)
    depth = points_camera[..., 2].astype(np.float32)
    invalid = ~np.isfinite(radial) | (depth <= 0)
    depth[invalid] = 0
    background_path = scene.get("background_image_path")
    if background_path:
        rgb = np.asarray(Image.open(background_path).convert("RGB").resize((640, 360), Image.Resampling.BILINEAR)).copy()
    else:
        rgb = np.zeros((360, 640, 3), dtype=np.uint8)
        rgb[labels == 0] = [115, 105, 95]
        rgb[labels == 1] = [165, 175, 185]
    masks: dict[str, np.ndarray] = {}
    truths: list[dict[str, Any]] = []
    for index, item in enumerate(scene["objects"], start=2):
        mask = labels == index
        masks[item["object_id"]] = mask
        texture_path = item.get("texture_path")
        if texture_path:
            texture = np.asarray(Image.open(texture_path).convert("RGBA"))
            tx = np.clip((local[..., 0] * (texture.shape[1]-1)).astype(int), 0, texture.shape[1]-1)
            ty = np.clip((local[..., 1] * (texture.shape[0]-1)).astype(int), 0, texture.shape[0]-1)
            sampled = texture[ty, tx]
            visible = mask & (sampled[..., 3] > 8)
            rgb[visible] = sampled[visible, :3]
            masks[item["object_id"]] = visible
        else:
            rgb[mask] = np.asarray(item["color_rgb"], dtype=np.uint8)
        single = {**scene, "objects": [item]}
        _, single_labels, _ = intersect_scene(origin, directions, single)
        full = single_labels == 2
        visible = masks[item["object_id"]]
        visible_pixels, full_pixels = int(visible.sum()), int(full.sum())
        ys, xs = np.nonzero(visible)
        x0, y0, bottom = (float(v) for v in item["center_bottom_ground_m"])
        center_ground = np.array([[x0, y0, bottom + float(item["height_m"])/2]])
        center_camera = transform_points(center_ground, invert_transform(scene["camera"]["T_ground_from_camera"]))[0]
        visible_camera = points_camera[visible]
        surface_center = np.median(visible_camera, axis=0) if visible_pixels else center_camera
        visible_z = points_ground[..., 2][visible]
        truths.append({"object_id": item["object_id"], "class_name": item["class_name"], "visible_pixel_count": visible_pixels,
                       "full_projected_pixel_count": full_pixels, "occlusion_ratio": 1-visible_pixels/max(full_pixels,1),
                       "visible_mask_key": item["object_id"], "visible_top_m": float(visible_z.max()) if visible_pixels else None,
                       "visible_bottom_m": float(visible_z.min()) if visible_pixels else None, "physical_top_m": bottom+float(item["height_m"]),
                       "physical_bottom_m": bottom, "true_physical_height_m": float(item["height_m"]),
                       "true_visible_height_m": float(visible_z.max()-visible_z.min()) if visible_pixels else None,
                       "true_distance_m": float(np.linalg.norm(surface_center)), "true_distance_axial_m": float(np.median(visible_camera[:,2])) if visible_pixels else float(center_camera[2]),
                       "geometric_center_distance_m": float(np.linalg.norm(center_camera)),
                       "image_border_contact": bool(visible[0].any() or visible[-1].any() or visible[:,0].any() or visible[:,-1].any()),
                       "bbox_xyxy": [int(xs.min()), int(ys.min()), int(xs.max()+1), int(ys.max()+1)] if visible_pixels else None})
    return {"rgb": rgb, "depth": depth, "masks": masks, "truth": {"schema_version": TRUTH_SCHEMA,
            "scene_id": scene["scene_id"], "distance_definition": "camera_radial_to_object_geometric_center",
            "gt_used_for_inference": False, "objects": truths}}


def write_scene_artifacts(scene: dict[str, Any], output: Path) -> dict[str, Any]:
    rendered = render_scene(scene)
    output.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rendered["rgb"], mode="RGB").save(output / "source_rgb.png")
    payload = encode_rgb565_le(rendered["rgb"])
    (output / "image.rgb565").write_bytes(payload)
    np.save(output / "ground_truth_depth.npy", rendered["depth"], allow_pickle=False)
    np.savez_compressed(output / "ground_truth_masks.npz", **rendered["masks"])
    (output / "ground_truth.json").write_text(json.dumps(rendered["truth"], indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    calibration = calibration_from_scene(scene)
    (output / "calibration.json").write_text(json.dumps(calibration, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    metadata = {"scene": scene, "rgb888_to_rgb565_quantization": roundtrip_metrics(rendered["rgb"]),
                "image_bytes": len(payload), "image_sha256": hashlib.sha256(payload).hexdigest(), "gt_used_for_inference": False}
    return {**rendered, "calibration": calibration, "metadata": metadata}
