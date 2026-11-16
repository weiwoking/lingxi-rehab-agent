from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .errors import ErrorCode, PipelineError


def build_official_chair_texture(project_root: Path, output_path: Path) -> dict[str, Any]:
    source = project_root / "samples" / "stage3_official" / "official_000" / "source_rgb.png"
    masks_path = project_root / "outputs_stage3" / "official_000" / "segment_masks.npz"
    object_path = project_root / "outputs_stage3" / "official_000" / "object_depth_results.json"
    if not source.is_file() or not masks_path.is_file() or not object_path.is_file():
        raise PipelineError(ErrorCode.STAGE5_APPEARANCE_ASSET, "frozen official appearance inputs are missing")
    import json
    document = json.loads(object_path.read_text(encoding="utf-8"))
    chair = next((item for item in document["objects"] if item["class_name"] == "chair" and item.get("segment_bbox")), None)
    if chair is None:
        raise PipelineError(ErrorCode.STAGE5_APPEARANCE_ASSET, "official sample has no segmented chair")
    masks = np.load(masks_path, allow_pickle=False)
    candidates = [masks[key] for key in masks.files if "mask" in key.lower()]
    if not candidates:
        candidates = [masks[key] for key in masks.files]
    x1,y1,x2,y2=(int(round(v)) for v in chair["segment_bbox"])
    selected=max(candidates, key=lambda m: int(np.asarray(m,dtype=bool)[max(0,y1):min(360,y2),max(0,x1):min(640,x2)].sum()))
    rgba=np.zeros((max(1,y2-y1),max(1,x2-x1),4),dtype=np.uint8)
    rgb=np.asarray(Image.open(source).convert("RGB")); crop=rgb[y1:y2,x1:x2]; mask=np.asarray(selected,dtype=bool)[y1:y2,x1:x2]
    rgba[:crop.shape[0],:crop.shape[1],:3]=crop; rgba[:mask.shape[0],:mask.shape[1],3]=mask.astype(np.uint8)*255
    output_path.parent.mkdir(parents=True,exist_ok=True); Image.fromarray(rgba,mode="RGBA").save(output_path)
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest(); texture_hash=hashlib.sha256(output_path.read_bytes()).hexdigest()
    return {"asset_id":"official_demo_official_000_chair","source_path":str(source),"source_sha256":source_hash,
            "derived_path":str(output_path),"derived_sha256":texture_hash,"source_dataset":"DELTAR official demo HDF5",
            "license":"upstream DELTAR demo research asset; no independent redistribution conclusion",
            "construction":"YOLO-seg mask alpha cutout; analytic scene supplies all geometry truth","class_name":"chair"}
