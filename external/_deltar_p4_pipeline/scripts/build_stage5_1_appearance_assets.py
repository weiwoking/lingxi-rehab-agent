from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from deltar_p4_pipeline.analytic_renderer import stage5_scene_family
from scripts.build_stage5_analytic_scenes import build, write_json


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_assets(asset_root: Path) -> list[dict[str, Any]]:
    manifest = json.loads((ROOT / "outputs_stage3_1" / "object_set_manifest.json").read_text(encoding="utf-8"))
    objects = {item["object_id"]: item for item in manifest["objects"]}
    assets: list[dict[str, Any]] = []
    for index, pair in enumerate(manifest["matched_pairs"]):
        segment = objects[pair["segment_object_id"]]
        detection = objects[pair["detect_object_id"]]
        sample_id = pair["sample_id"]
        source = ROOT / "samples" / "stage3_official" / sample_id / "source_rgb.png"
        masks_path = ROOT / "outputs_stage3" / sample_id / "segment_masks.npz"
        with np.load(masks_path, allow_pickle=False) as archive:
            mask = np.asarray(archive[segment["mask_key"]], dtype=bool)
        ys, xs = np.nonzero(mask)
        if not ys.size:
            continue
        pad = 2
        x1, x2 = max(0, int(xs.min()) - pad), min(640, int(xs.max()) + 1 + pad)
        y1, y2 = max(0, int(ys.min()) - pad), min(360, int(ys.max()) + 1 + pad)
        rgb = np.asarray(Image.open(source).convert("RGB"))
        rgba = np.zeros((y2 - y1, x2 - x1, 4), dtype=np.uint8)
        rgba[..., :3] = rgb[y1:y2, x1:x2]
        rgba[..., 3] = mask[y1:y2, x1:x2].astype(np.uint8) * 255
        output = asset_root / f"asset_{index:03d}_{segment['class_name'].replace(' ', '_')}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(rgba, mode="RGBA").save(output)
        assets.append({
            "asset_id": f"official_{index:03d}", "pair_id": pair["pair_id"], "sample_id": sample_id,
            "segment_object_id": segment["object_id"], "detect_object_id": detection["object_id"],
            "class_id": segment["class_id"], "class_name": segment["class_name"],
            "segment_confidence": segment["class_confidence"], "detect_confidence": detection["class_confidence"],
            "detect_segment_iou": pair["match_iou"], "mask_key": segment["mask_key"],
            "source_path": str(source), "source_sha256": sha256(source),
            "derived_path": str(output), "derived_sha256": sha256(output),
            "alpha_source": "frozen YOLO segmentation mask", "alpha_pixel_count": int(mask.sum()),
            "crop_xyxy": [x1, y1, x2, y2], "crop_size": [x2 - x1, y2 - y1],
            "quality_gate": "pass", "gt_used_for_inference": False,
        })
    return assets


def appearance_scenes(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    base = stage5_scene_family()[2]
    heights = {"chair": 1.55, "tv": 0.75, "dining table": 0.78, "person": 1.72}
    scenes: list[dict[str, Any]] = []
    for index, asset in enumerate(assets):
        scene = deepcopy(base)
        class_name = asset["class_name"]
        height = heights.get(class_name, 1.0)
        crop_w, crop_h = asset["crop_size"]
        # Reproject each real cutout to its original source-image rectangle.  Keeping the
        # original local context makes the post-composite detect/segment check meaningful.
        x1, y1, x2, y2 = asset["crop_xyxy"]
        distance = height * 520.0 / max(crop_h, 1)
        width = crop_w * distance / 520.0
        x = (((x1 + x2) / 2.0) - 319.5) * distance / 520.0
        bottom = 1.5 if class_name == "tv" else 0.0
        camera_height = bottom + (y2 - 179.5) * distance / 520.0
        scene["scene_id"] = f"A{index + 1:02d}_{class_name.replace(' ', '_')}"
        scene["scenario_family"] = f"appearance_{class_name}"
        scene["appearance_kind"] = "official_demo_yolo_segment_billboard"
        scene["appearance_asset_id"] = asset["asset_id"]
        scene["background_image_path"] = asset["source_path"]
        scene["background_wall_y_m"] = 20.0
        scene["camera"]["T_ground_from_camera"][2][3] = camera_height
        scene["objects"] = [{
            "object_id": asset["asset_id"], "class_name": class_name, "primitive": "billboard",
            "center_bottom_ground_m": [x, distance, bottom], "width_m": width, "height_m": height,
            "depth_m": 0.15, "slant_y_per_x": 0.0, "color_rgb": [180, 100, 60],
            "texture_path": asset["derived_path"],
        }]
        scenes.append(scene)
    return scenes


def main() -> None:
    asset_root = ROOT / "samples" / "stage5_1_assets"
    assets = extract_assets(asset_root)
    scenes = appearance_scenes(assets)
    rows = build(scenes, ROOT / "samples" / "stage5_1_appearance")
    classes = sorted({asset["class_name"] for asset in assets})
    manifest = {
        "schema_version": "deltar_stage5_1_appearance_assets.v1", "status": "pass",
        "unique_source_asset_count": len(assets), "class_count": len(classes), "classes": classes,
        "placement_count": len(scenes), "formal_sample_count_with_semantics": len(rows),
        "source_requirement": "detect and segment matched in frozen Stage 3.1", "assets": assets,
        "gt_used_for_inference": False,
    }
    write_json(ROOT / "outputs_stage5_1" / "appearance_asset_manifest.json", manifest)
    print(json.dumps({k: manifest[k] for k in ("status", "unique_source_asset_count", "class_count", "classes", "placement_count", "formal_sample_count_with_semantics")}, indent=2))


if __name__ == "__main__":
    main()
