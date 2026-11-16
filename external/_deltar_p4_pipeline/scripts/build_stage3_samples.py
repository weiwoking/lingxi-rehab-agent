from __future__ import annotations

import json
import sys
from pathlib import Path

import h5py
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from deltar_p4_pipeline.rgb565 import encode_rgb565_le, roundtrip_metrics

WORKTREE = Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128")


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")


def profile(sample_id: str, strategy: str, mask: np.ndarray, fr_source: np.ndarray) -> dict:
    fr = fr_source.copy()
    if strategy == "letterbox_640x480": fr[:,[0,2]] += 60.0
    return {"schema_version":"deltar_p4_profile.v1", "profile_id":f"stage3_{strategy}_v1", "sample_kind":"official_reverse_stage3",
            "image":{"width":640,"height":360,"stride":1280,"pixel_format":"RGB565_LE"},
            "canvas":{"strategy":strategy,"fill_rgb":[0,0,0]}, "zone_mapping":{"strategy":"identity"},
            "histogram":{"mu_strategy":"distance_mm_to_m","sigma_strategy":"range_sigma_mm_to_m"},
            "mask":{"strategy":"explicit_bool64","values":mask.astype(bool).tolist()},
            "fr":{"strategy":"explicit_model_canvas","values":fr.astype(float).tolist()}, "seed":0,
            "test_intrinsics":{"fx":611.2,"fy":457.2,"cx":323.4,"cy":138.675}, "stage3_sample_id":sample_id,
            "gt_used_for_inference":False}


def main() -> None:
    manifest_path = ROOT / "outputs_stage3" / "official_sample_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output = ROOT / "samples" / "stage3_official"; built=[]
    for item in manifest["selected"]:
        h5_path = WORKTREE / "data" / "demo" / item["h5_relative"]
        with h5py.File(h5_path,"r") as handle:
            rgb=handle["rgb"][:].astype(np.uint8); depth=handle["depth"][:].astype(np.float32)
            hist=handle["hist_data"][:].astype(np.float64); fr=handle["fr"][:].astype(np.float64); mask=handle["mask"][:].astype(bool)
        source=np.asarray(Image.fromarray(rgb,mode="RGB").resize((640,360),Image.Resampling.BILINEAR))
        gt=np.asarray(Image.fromarray(depth,mode="F").resize((640,360),Image.Resampling.BILINEAR),dtype=np.float32)
        parent=output/item["sample_id"]; parent.mkdir(parents=True,exist_ok=True)
        Image.fromarray(source,mode="RGB").save(parent/"source_rgb.png")
        np.save(parent/"evaluation_gt_depth_source.npy",gt,allow_pickle=False)
        fr_source=fr.copy(); fr_source[:,[0,2]]*=0.75
        tof={"schema_version":"rehab_tof_raw.v1","sequence":item["index"]+1,"timestamp_us":0,
             "distance_mm":np.rint(hist[:,0]*1000).astype(int).tolist(),"range_sigma_mm":np.rint(hist[:,1]*1000).astype(int).tolist(),
             "target_status":[0]*64,"nb_target_detected":[1]*64}
        for strategy in ("direct_640x360","letterbox_640x480"):
            folder=parent/strategy; folder.mkdir(parents=True,exist_ok=True)
            (folder/"image.rgb565").write_bytes(encode_rgb565_le(source)); write_json(folder/"tof.json",tof)
            write_json(folder/"profile.json",profile(item["sample_id"],strategy,mask,fr_source))
            write_json(folder/"source_metadata.json",{"source_h5":str(h5_path),"construction":"official RGB resized to common 640x360 source",
                "rgb888_to_rgb565_quantization":roundtrip_metrics(source),"gt_path_separate_from_inference":str(parent/"evaluation_gt_depth_source.npy"),"gt_used_for_inference":False})
        built.append(str(parent))
    print(json.dumps({"built_count":len(built),"built":built},indent=2,ensure_ascii=False))


if __name__ == "__main__": main()
