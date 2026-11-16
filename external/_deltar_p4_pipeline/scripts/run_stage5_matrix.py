from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from deltar_p4_pipeline.cli import run_sample
from deltar_p4_pipeline.deltar_bridge import DeltarBridge
from deltar_p4_pipeline.stage5_evaluator import compare_new_old,evaluate_scene,summarize_matrix
from deltar_p4_pipeline.yolo_worker_client import run_yolo_worker

FORMAL=Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128");CHECKPOINT=Path(r"C:\ec\_deltar_formal\assets\nyu.pt")
VISION_PYTHON=Path(r"C:\ec\_conda_envs\vision_interpreter\python.exe");WORKER=ROOT/"workers"/"yolo_worker.py"
DETECT_MODEL=Path(r"C:\ec\esp-claw\pc\vision_interpreter\models\yolo11n.pt");SEGMENT_MODEL=ROOT/"models"/"yolo11n-seg.pt"
DETECT_SHA="0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1";SEGMENT_SHA="55ed65c56c91713d23e8402371c6c49a6fd84f257f7dce452e8d70e41dcbe152"


def write_json(path:Path,value:object)->None:path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")


def main()->None:
    started=time.perf_counter();output=ROOT/"outputs_stage5";output.mkdir(parents=True,exist_ok=True)
    parents=sorted([p for root in (ROOT/"samples"/"stage5_analytic",ROOT/"samples"/"stage5_appearance") if root.is_dir() for p in root.iterdir() if p.is_dir() and (p/"source_rgb.png").is_file()])
    images=[p/"source_rgb.png" for p in parents];worker_out=output/"runtime"/"yolo"
    detect=run_yolo_worker(VISION_PYTHON,WORKER,DETECT_MODEL,"detect",images,worker_out,expected_sha256=DETECT_SHA,timeout_s=900)
    segment=run_yolo_worker(VISION_PYTHON,WORKER,SEGMENT_MODEL,"segment",images,worker_out,expected_sha256=SEGMENT_SHA,timeout_s=900)
    bridge=DeltarBridge(FORMAL,CHECKPOINT,device="cuda:0");profile=json.loads((ROOT/"geometry_profile.json").read_text(encoding="utf-8"));scenes=[];timings=[];deltar_timings=[]
    for parent,det,seg in zip(parents,detect,segment,strict=True):
        depth={};scene_started=time.perf_counter()
        for canvas in ("direct_640x360","letterbox_640x480"):
            result=run_sample(parent/canvas,output/"runtime"/parent.name/"deltar",FORMAL,CHECKPOINT,"cuda:0",bridge=bridge)
            deltar_timings.append(result["timing"])
            folder=output/"runtime"/parent.name/"deltar"/canvas/result["profile_id"]
            depth[canvas]=np.load(folder/"depth_source_640x360.npy",allow_pickle=False)
        row=evaluate_scene(parent,depth,det,seg,profile);row["performance_ms"]={"total":(time.perf_counter()-scene_started)*1000};scenes.append(row);timings.append(row["performance_ms"]["total"])
    matrix=summarize_matrix(scenes);matrix.update({"schema_version":"deltar_stage5_full_matrix.v1","status":"success","scene_count":len(scenes),
                  "object_count":sum(s["perception"]["truth_count"] for s in scenes),"scenes":scenes,"gt_used_for_inference":False,
                  "performance":{"total_ms":(time.perf_counter()-started)*1000,"scene_mean_ms":float(np.mean(timings)),
                    "yolo_detect_batch":detect[0]["worker_batch_timing_ms"],"yolo_segment_batch":segment[0]["worker_batch_timing_ms"],
                    "deltar_sample_mean_total_ms":float(np.mean([x["total_ms"] for x in deltar_timings])),
                    "deltar_forward_mean_ms":float(np.mean([x["forward_first_ms"] for x in deltar_timings])),
                    "adapter_mean_ms":float(np.mean([x["adapter_ms"] for x in deltar_timings])),
                    "l0_l1_geometry_total_ms":float(sum(s["evaluation_timing_ms"]["l0_l1_geometry"] for s in scenes)),
                    "l2_object_depth_geometry_total_ms":float(sum(s["evaluation_timing_ms"]["l2_object_depth_and_geometry"] for s in scenes)),
                    "old_baseline_total_ms":float(sum(s["evaluation_timing_ms"]["l3_old_baseline"] for s in scenes))}})
    comparison=compare_new_old(matrix);write_json(output/"full_matrix.json",matrix);write_json(output/"old_baseline_results.json",{"rows":matrix["rows"]["L3"]})
    write_json(output/"old_vs_new_summary.json",comparison);write_json(output/"scenario_summary.json",matrix["scenario_summaries"])
    write_json(output/"class_summary.json",{"note":"class records are retained in full_matrix rows","classes":sorted({r["class_name"] for layer in matrix["rows"].values() for r in layer})})
    direct=[r for r in matrix["rows"]["L2"] if r.get("canvas")=="direct_640x360" and r.get("status")=="valid"]
    letter={(r["scene_id"],r["object_id"]):r for r in matrix["rows"]["L2"] if r.get("canvas")=="letterbox_640x480" and r.get("status")=="valid"}
    deltas=[abs(r["distance_absolute_error_m"]-letter[(r["scene_id"],r["object_id"])]["distance_absolute_error_m"]) for r in direct if (r["scene_id"],r["object_id"]) in letter]
    write_json(output/"canvas_summary.json",{"paired_count":len(deltas),"median_distance_error_delta_m":float(np.median(deltas)) if deltas else None,"canvases":["direct_640x360","letterbox_640x480"]})
    print(json.dumps({"status":"success","scenes":matrix["scene_count"],"objects":matrix["object_count"],"paired_new_old":comparison["paired_object_count"],"timing_ms":matrix["performance"]["total_ms"]},indent=2))


if __name__=="__main__":main()
