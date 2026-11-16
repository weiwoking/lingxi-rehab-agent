from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from .baseline_old_fusion import evaluate_old_detection
from .errors import PipelineError
from .object_geometry import summarize_geometry
from .point_cloud import bbox_mask, build_point_cloud, erode_mask
from .stage5_matching import match_objects
from .stage5_metrics import depth_metrics, geometry_errors, summarize_rows


def _geometry(depth: np.ndarray, mask: np.ndarray, calibration: dict[str, Any], profile: dict[str, Any], truth: dict[str, Any], source_type: str) -> dict[str, Any]:
    try:
        region=erode_mask(mask,3) if source_type=="segmentation" else mask
        cloud=build_point_cloud(depth,region,calibration,profile,source_type=source_type,point_filter="robust_mad_gate")
        geometry=summarize_geometry(cloud,calibration,profile); errors=geometry_errors(geometry,truth)
        return {"status":"valid","geometry":geometry,**errors,"false_valid":False}
    except PipelineError as exc:
        return {"status":"rejected","reason":exc.code,"message":exc.message,"false_valid":False}


def _predictions(detect: dict[str,Any], segment: dict[str,Any], segment_masks: dict[str,np.ndarray]) -> list[dict[str,Any]]:
    output=[]; matched_detect=set()
    for seg in segment["document"]["instances"]:
        item={**seg,"source_type":"segmentation"}; output.append(item)
        for det in detect["document"]["instances"]:
            if det["class_name"]!=seg["class_name"]:continue
            from .stage5_matching import bbox_iou
            if bbox_iou(det["bbox_xyxy"],seg["bbox_xyxy"])>=.5:matched_detect.add(det["instance_id"])
    for det in detect["document"]["instances"]:
        if det["instance_id"] not in matched_detect:output.append({**det,"source_type":"detection_fallback"})
    return output


def evaluate_scene(parent: Path, deltar_depths: dict[str,np.ndarray], detect: dict[str,Any], segment: dict[str,Any], geometry_profile: dict[str,Any]) -> dict[str,Any]:
    evaluation_started=time.perf_counter()
    truth=json.loads((parent/"ground_truth.json").read_text(encoding="utf-8")); calibration=json.loads((parent/"calibration.json").read_text(encoding="utf-8"))
    tof=json.loads((parent/"tof.json").read_text(encoding="utf-8")); gt_depth=np.load(parent/"ground_truth_depth.npy",allow_pickle=False)
    gt_archive=np.load(parent/"ground_truth_masks.npz",allow_pickle=False); gt_masks={key:gt_archive[key].astype(bool) for key in gt_archive.files}
    segment_masks={key:np.asarray(value,dtype=bool) for key,value in segment["masks"].items()}; predictions=_predictions(detect,segment,segment_masks)
    layer_rows={key:[] for key in ("L0","L1","L2","L3")};ablation={"A2_YOLO_GT":[],"A4_BBOX_DELTAR":[]}
    isolation_started=time.perf_counter()
    for obj in truth["objects"]:
        mask=gt_masks[obj["visible_mask_key"]]
        l0=_geometry(gt_depth,mask,calibration,geometry_profile,obj,"segmentation")
        layer_rows["L0"].append({"scene_id":parent.name,"object_id":obj["object_id"],"class_name":obj["class_name"],"canvas":"oracle",**l0})
        for canvas,depth in deltar_depths.items():
            l1=_geometry(depth,mask,calibration,geometry_profile,obj,"segmentation")
            layer_rows["L1"].append({"scene_id":parent.name,"object_id":obj["object_id"],"class_name":obj["class_name"],"canvas":canvas,
                                     "oracle_region_used":True,"gt_used_for_deltar_inference":False,"depth_metrics":depth_metrics(depth,gt_depth,mask),**l1})
    isolation_ms=(time.perf_counter()-isolation_started)*1000
    new_started=time.perf_counter();matches=match_objects(predictions,truth["objects"],threshold=.3,prediction_masks=segment_masks,truth_masks=gt_masks)
    for match in matches:
        if match["match_status"]!="matched":
            if match["truth_index"] is not None:
                obj=truth["objects"][match["truth_index"]]
                for canvas in deltar_depths:layer_rows["L2"].append({"scene_id":parent.name,"object_id":obj["object_id"],"class_name":obj["class_name"],"canvas":canvas,"status":"rejected","reason":"unmatched_gt","false_valid":False})
            continue
        pred=predictions[match["prediction_index"]]; obj=truth["objects"][match["truth_index"]]
        mask=segment_masks[pred["mask_key"]] if pred["source_type"]=="segmentation" else bbox_mask((360,640),pred["bbox_xyxy"])
        for canvas,depth in deltar_depths.items():
            row=_geometry(depth,mask,calibration,geometry_profile,obj,pred["source_type"])
            layer_rows["L2"].append({"scene_id":parent.name,"object_id":obj["object_id"],"class_name":obj["class_name"],"canvas":canvas,
                                     "prediction_source":pred["source_type"],"match_iou":match["iou"],"gt_used_for_inference":False,**row})
        a2=_geometry(gt_depth,mask,calibration,geometry_profile,obj,pred["source_type"])
        ablation["A2_YOLO_GT"].append({"scene_id":parent.name,"object_id":obj["object_id"],"class_name":obj["class_name"],**a2})
    new_chain_ms=(time.perf_counter()-new_started)*1000
    old_started=time.perf_counter();detect_predictions=detect["document"]["instances"]; old_matches=match_objects(detect_predictions,truth["objects"],threshold=.3,truth_masks=gt_masks)
    for match in old_matches:
        if match["match_status"]!="matched":
            if match["truth_index"] is not None:
                obj=truth["objects"][match["truth_index"]];layer_rows["L3"].append({"scene_id":parent.name,"object_id":obj["object_id"],"class_name":obj["class_name"],"canvas":"same_rgb_tof","status":"rejected","reason":"unmatched_gt","false_valid":False})
            continue
        pred=detect_predictions[match["prediction_index"]]; obj=truth["objects"][match["truth_index"]]; old=evaluate_old_detection(pred,tof,calibration)
        if old["status"]=="valid":old.update(geometry_errors(old["geometry"],obj));old["false_valid"]=False
        layer_rows["L3"].append({"scene_id":parent.name,"object_id":obj["object_id"],"class_name":obj["class_name"],"canvas":"same_rgb_tof","match_iou":match["iou"],"gt_used_for_inference":False,**old})
        a4=_geometry(deltar_depths["direct_640x360"],bbox_mask((360,640),pred["bbox_xyxy"]),calibration,geometry_profile,obj,"detection_fallback")
        ablation["A4_BBOX_DELTAR"].append({"scene_id":parent.name,"object_id":obj["object_id"],"class_name":obj["class_name"],**a4})
    old_baseline_ms=(time.perf_counter()-old_started)*1000
    perception={"truth_count":len(truth["objects"]),"prediction_count":len(predictions),"detect_count":len(detect_predictions),
                "matched_new":sum(x["match_status"]=="matched" for x in matches),"matched_old":sum(x["match_status"]=="matched" for x in old_matches),
                "precision":sum(x["match_status"]=="matched" for x in matches)/max(len(predictions),1),
                "recall":sum(x["match_status"]=="matched" for x in matches)/max(len(truth["objects"]),1)}
    return {"scene_id":parent.name,"scenario_family":json.loads((parent/"profile.json").read_text(encoding="utf-8")).get("scenario_family",parent.name[:2]),
            "layers":layer_rows,"ablation":ablation,"perception":perception,"matches":matches,"old_matches":old_matches,
            "evaluation_timing_ms":{"l0_l1_geometry":isolation_ms,"l2_object_depth_and_geometry":new_chain_ms,"l3_old_baseline":old_baseline_ms,
                                    "evaluation_total":(time.perf_counter()-evaluation_started)*1000}}


def summarize_matrix(scenes: list[dict[str,Any]]) -> dict[str,Any]:
    summaries={}; all_rows={layer:[] for layer in ("L0","L1","L2","L3")}
    for scene in scenes:
        for layer in all_rows:all_rows[layer].extend(scene["layers"][layer])
    for layer,rows in all_rows.items():summaries[layer]=summarize_rows(rows)
    scenario={}
    for family in sorted({scene["scenario_family"] for scene in scenes}):
        scenario[family]={layer:summarize_rows([row for scene in scenes if scene["scenario_family"]==family for row in scene["layers"][layer]]) for layer in all_rows}
    return {"layer_summaries":summaries,"scenario_summaries":scenario,"rows":all_rows}


def compare_new_old(matrix: dict[str,Any]) -> dict[str,Any]:
    new={(r["scene_id"],r["object_id"]):r for r in matrix["rows"]["L2"] if r.get("canvas")=="direct_640x360" and r.get("status")=="valid"}
    old={(r["scene_id"],r["object_id"]):r for r in matrix["rows"]["L3"] if r.get("status")=="valid"}; pairs=[]
    for key in sorted(new.keys()&old.keys()):
        pairs.append({"scene_id":key[0],"object_id":key[1],**{metric+"_improvement_m":old[key][metric]-new[key][metric] for metric in
                      ("distance_absolute_error_m","height_absolute_error_m","bottom_height_absolute_error_m")}})
    result={"paired_object_count":len(pairs),"pairs":pairs}
    for metric in ("distance_absolute_error_m","height_absolute_error_m","bottom_height_absolute_error_m"):
        values=[row[metric+"_improvement_m"] for row in pairs]
        result[metric]={"median_improvement_m":float(np.median(values)) if values else None,"p90_error_improvement_m":None,
                        "percent_median_improvement":None}
        if pairs:
            nv=[new[(p["scene_id"],p["object_id"])][metric] for p in pairs];ov=[old[(p["scene_id"],p["object_id"])][metric] for p in pairs]
            result[metric]["p90_error_improvement_m"]=float(np.percentile(ov,90)-np.percentile(nv,90))
            result[metric]["percent_median_improvement"]=float(100*(np.median(ov)-np.median(nv))/max(np.median(ov),1e-12))
    return result
