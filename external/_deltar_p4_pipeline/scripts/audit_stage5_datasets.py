from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/"outputs_stage5"


def sha(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):h.update(block)
    return h.hexdigest()


def write(name:str,value:object)->None:OUT.mkdir(parents=True,exist_ok=True);(OUT/name).write_text(json.dumps(value,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")


def main()->None:
    now=datetime.now(timezone.utc).isoformat();demo=Path(r"C:\ec\_deltar_formal\assets\demo.zip")
    audit={"schema_version":"deltar_stage5_dataset_audit.v1","audited_at_utc":now,"new_download_bytes":0,"download_performed":False,
      "existing_official_deltar":{"path":str(demo),"size_bytes":demo.stat().st_size,"sha256":sha(demo),"hdf5_count":287,
        "use":"official DELTAR regression, RGB appearance source, dense-depth evaluation","license":"upstream research/demo asset; no independent redistribution conclusion"},
      "candidates":[
        {"name":"NYU Depth V2","official_url":"https://cs.nyu.edu/~fergus/datasets/nyu_depth_v2.html","size":"~2.8 GB labeled","rgb_depth_intrinsics":True,"instances":True,"license_explicit_on_page":False,"decision":"not_downloaded_over_1GB_and_license_not_explicit"},
        {"name":"SUN RGB-D","official_url":"https://rgbd.cs.princeton.edu/","size":"6.4 GB image archive","rgb_depth_intrinsics":True,"instances_and_3d_boxes":True,"license_explicit_on_page":False,"decision":"not_downloaded_over_1GB"},
        {"name":"Hypersim","official_url":"https://github.com/apple/ml-hypersim","size":"~1.9 TB; individual official ZIPs 1-20 GB","rgb_depth_intrinsics":True,"instances_and_geometry":True,"license":"CC BY-SA 3.0","decision":"not_downloaded_no_official_sub_1GB_bundle"}],
      "public_data_limitations":"No new source met all of official provenance, <=1 GB, direct small-subset download, and sufficiently explicit license. Stage 5 is not blocked; A+B+existing official DELTAR data are used.",
      "tof_simulation_geometry":{"analytic":"true_tof_viewpoint_ray_scene_intersection","public_rgbd":"co_located_approximation_only_if_later_used"}}
    assets={"schema_version":"deltar_stage5_asset_manifest.v1","assets":[{"asset_id":"deltar_demo_zip","path":str(demo),"size_bytes":demo.stat().st_size,"sha256":sha(demo),"source":"frozen official DELTAR asset"}]}
    appearance=ROOT/"samples"/"stage5_appearance"/"asset_manifest.json"
    if appearance.is_file():assets["assets"].append(json.loads(appearance.read_text(encoding="utf-8")))
    scenes=[]
    for folder in (ROOT/"samples"/"stage5_analytic",ROOT/"samples"/"stage5_appearance"):
        if folder.is_dir():
            for parent in sorted(p for p in folder.iterdir() if p.is_dir() and (p/"ground_truth.json").is_file()):
                truth=json.loads((parent/"ground_truth.json").read_text(encoding="utf-8"));profile=json.loads((parent/"profile.json").read_text(encoding="utf-8"))
                scenes.append({"scene_id":parent.name,"scenario_family":profile["scenario_family"],"appearance_kind":profile["appearance_kind"],"object_count":len(truth["objects"]),"path":str(parent)})
    tof_profiles=[{"scene_id":row["scene_id"],**json.loads((Path(row["path"])/"profile.json").read_text(encoding="utf-8"))} for row in scenes]
    write("dataset_audit.json",audit);write("asset_manifest.json",assets);write("scene_manifest.json",{"scene_count":len(scenes),"object_count":sum(x["object_count"] for x in scenes),"scenes":scenes});write("tof_simulation_profiles.json",{"approximate_sensor_model":True,"profiles":tof_profiles})
    print(json.dumps({"status":"pass","scene_count":len(scenes),"object_count":sum(x["object_count"] for x in scenes),"new_download_bytes":0},indent=2))


if __name__=="__main__":main()
