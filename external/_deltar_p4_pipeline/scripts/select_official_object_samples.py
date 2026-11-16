from __future__ import annotations

import json
import sys
from pathlib import Path

import h5py
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.yolo_worker_client import run_yolo_worker

WORKTREE = Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128")
VISION_PYTHON = Path(r"C:\ec\_conda_envs\vision_interpreter\python.exe")
DETECT_MODEL = Path(r"C:\ec\esp-claw\pc\vision_interpreter\models\yolo11n.pt")
DETECT_SHA256 = "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"
WORKER = ROOT / "workers" / "yolo_worker.py"
PRIORITY = ("cup", "bottle", "book", "chair", "dining table", "person")


def main() -> None:
    listing = json.loads((WORKTREE / "data" / "demo" / "data.json").read_text(encoding="utf-8"))["test"]
    scan = ROOT / "outputs_stage3" / "official_scan_rgb"; scan.mkdir(parents=True, exist_ok=True)
    images = []
    for index, item in enumerate(listing):
        target = scan / f"official_{index:03d}.png"
        if not target.is_file():
            with h5py.File(WORKTREE / "data" / "demo" / item["filename"], "r") as handle:
                rgb = handle["rgb"][:].astype(np.uint8)
            source = np.asarray(Image.fromarray(rgb, mode="RGB").resize((640, 360), Image.Resampling.BILINEAR))
            Image.fromarray(source, mode="RGB").save(target)
        images.append(target)
    raw = run_yolo_worker(VISION_PYTHON, WORKER, DETECT_MODEL, "detect", images,
                          ROOT / "outputs_stage3" / "official_detection_scan", expected_sha256=DETECT_SHA256, timeout_s=1200.0)
    candidates = []
    for index, (item, result) in enumerate(zip(listing, raw, strict=True)):
        instances = result["document"]["instances"]
        if instances:
            candidates.append({"index": index, "sample_id": f"official_{index:03d}", "h5_relative": item["filename"],
                               "source_rgb": str(images[index]), "detections": instances})
    selected = []; used: set[int] = set()
    # Frozen policy: earliest detected frame for each available priority class, then earliest remaining detected frames.
    for class_name in PRIORITY:
        match = next((item for item in candidates if item["index"] not in used and any(x["class_name"] == class_name for x in item["detections"])), None)
        if match:
            selected.append(match); used.add(match["index"])
    for item in candidates:
        if len(selected) >= 15: break
        if item["index"] not in used:
            selected.append(item); used.add(item["index"])
    manifest = {"schema_version": "deltar_stage3_sample_selection.v1", "selection_policy": "earliest per priority class, then earliest detected frames; no depth or GT consulted",
                "priority_classes": list(PRIORITY), "scanned_frame_count": len(listing), "detected_frame_count": len(candidates),
                "selected_count": len(selected), "selected": selected, "gt_consulted_for_selection": False}
    path = ROOT / "outputs_stage3" / "official_sample_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps({"manifest": str(path), "selected": [(x["sample_id"], sorted({d["class_name"] for d in x["detections"]})) for x in selected]}, indent=2, ensure_ascii=False))


if __name__ == "__main__": main()
