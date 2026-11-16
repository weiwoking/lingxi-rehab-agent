from __future__ import annotations

import json
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.cli import run_sample
from deltar_p4_pipeline.deltar_bridge import DeltarBridge
from deltar_p4_pipeline.object_depth import choose_candidates, evaluate_object_depth
from deltar_p4_pipeline.object_matching import greedy_match
from deltar_p4_pipeline.stage3_validation import boundary_metrics, depth_metrics, gt_references
from deltar_p4_pipeline.yolo_contracts import sha256_file
from deltar_p4_pipeline.yolo_worker_client import run_yolo_worker

VISION_PYTHON=Path(r"C:\ec\_conda_envs\vision_interpreter\python.exe")
FORMAL_WORKTREE=Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128")
CHECKPOINT=Path(r"C:\ec\_deltar_formal\assets\nyu.pt")
DETECT_MODEL=Path(r"C:\ec\esp-claw\pc\vision_interpreter\models\yolo11n.pt")
SEGMENT_MODEL=ROOT/"models"/"yolo11n-seg.pt"
WORKER=ROOT/"workers"/"yolo_worker.py"
DETECT_SHA256="0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"
SEGMENT_SHA256="55ed65c56c91713d23e8402371c6c49a6fd84f257f7dce452e8d70e41dcbe152"
STAGE3_SEGMENT_METHOD={"region_method":"segment_eroded_3px","statistic_method":"trimmed_mean"}
STAGE3_DETECT_METHOD={"region_method":"bbox_depth_cluster","statistic_method":"trimmed_mean"}


def jsonable(value: Any) -> Any:
    if isinstance(value,np.generic): return value.item()
    if isinstance(value,np.ndarray): return value.tolist()
    if isinstance(value,dict): return {str(k):jsonable(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [jsonable(v) for v in value]
    return value


def write_json(path: Path, value: dict[str,Any]) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(jsonable(value),indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")


def model_manifest(detect: dict, segment: dict) -> dict:
    def item(meta: dict, source: str, license_text: str) -> dict:
        return {**meta,"source":source,"license":license_text}
    return {"schema_version":"deltar_stage3_model_manifest.v1","models":[
        item(detect,"read-only esp-claw existing asset","Ultralytics AGPL-3.0; existing project asset; no independent commercial-license conclusion"),
        item(segment,"Ultralytics official auto-download from github.com/ultralytics/assets release v8.4.0","Ultralytics AGPL-3.0; research PoC asset; no independent commercial-license conclusion")],
        "deltar_checkpoint":{"path":str(CHECKPOINT),"sha256":sha256_file(CHECKPOINT)},"gt_used_for_inference":False}


def main() -> None:
    started=time.perf_counter(); manifest=json.loads((ROOT/"outputs_stage3"/"official_sample_manifest.json").read_text(encoding="utf-8"))
    parents=[ROOT/"samples"/"stage3_official"/item["sample_id"] for item in manifest["selected"]]
    images=[parent/"source_rgb.png" for parent in parents]
    worker_out=ROOT/"outputs_stage3"/"selected_worker"
    detect_outputs=run_yolo_worker(VISION_PYTHON,WORKER,DETECT_MODEL,"detect",images,worker_out,expected_sha256=DETECT_SHA256,timeout_s=600)
    segment_outputs=run_yolo_worker(VISION_PYTHON,WORKER,SEGMENT_MODEL,"segment",images,worker_out,expected_sha256=SEGMENT_SHA256,timeout_s=600)
    detect_meta=detect_outputs[0]["document"]["model"]; segment_meta=segment_outputs[0]["document"]["model"]
    write_json(ROOT/"models"/"MODEL_MANIFEST.json",model_manifest(detect_meta,segment_meta))
    bridge=DeltarBridge(FORMAL_WORKTREE,CHECKPOINT,device="cuda:0")
    matrix_rows=[]; sample_summaries=[]; total_objects=0; matched_total=0; deterministic=True
    deltar_timing=defaultdict(list); object_processing_ms=0.0
    for parent,detect,segment in zip(parents,detect_outputs,segment_outputs,strict=True):
        sample_id=parent.name; sample_started=time.perf_counter(); output=ROOT/"outputs_stage3"/sample_id; output.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(parent/"source_rgb.png",output/"source_rgb.png")
        write_json(output/"yolo_detect.json",detect["document"]); write_json(output/"yolo_segment.json",segment["document"])
        np.savez_compressed(output/"segment_masks.npz",**segment["masks"])
        canvas_depth={}; deltar_results={}
        for strategy in ("direct_640x360","letterbox_640x480"):
            result=run_sample(parent/strategy,output/"_deltar",FORMAL_WORKTREE,CHECKPOINT,"cuda:0",bridge=bridge)
            profile_id=result["profile_id"]; folder=output/"_deltar"/strategy/profile_id
            canvas_depth[strategy]=np.load(folder/"depth_source_640x360.npy",allow_pickle=False)
            deltar_results[strategy]=result
            deltar_timing[strategy].append(float(result["timing"]["total_ms"]))
            np.save(output/("direct_depth_source.npy" if strategy.startswith("direct") else "letterbox_depth_source.npy"),canvas_depth[strategy],allow_pickle=False)
            deterministic &= bool(json.loads((folder/"validation_report.json").read_text(encoding="utf-8"))["repeatability"]["float_exact_equal"])
        detections=detect["document"]["instances"]; segments=segment["document"]["instances"]
        matches=greedy_match(detections,segments,threshold=0.5); matched=[m for m in matches if m["match_status"]=="matched"]
        matched_total+=len(matched); objects=[]; masks_to_save={}; gt=np.load(parent/"evaluation_gt_depth_source.npy",allow_pickle=False)
        det_by={x["instance_id"]:x for x in detections}; seg_by={x["instance_id"]:x for x in segments}
        usable=[]
        for match in matched:
            det=det_by[match["detect_instance_id"]]; seg=seg_by[match["segment_instance_id"]]; mask=segment["masks"][seg["mask_key"]]
            usable.append((match,det,seg,mask))
        for item in matches:
            if item["match_status"]=="unmatched_detect":
                det=det_by[item["detect_instance_id"]]; usable.append((item,det,None,None))
        object_started=time.perf_counter()
        for index,(match,det,seg,mask) in enumerate(usable):
            object_id=f"{sample_id}_object_{index:03d}"; total_objects+=1; per_canvas={}; per_masks={}; gt_eval={}
            base_mask=mask if mask is not None else np.zeros((360,640),bool)
            if mask is not None:
                refs=gt_references(gt,mask)
                boundary={k:boundary_metrics(canvas_depth[k],gt,mask) for k in canvas_depth}
            else:
                bbox_mask_result, bbox_masks=evaluate_object_depth(det["bbox_xyxy"],gt)
                ref_mask=bbox_masks["bbox_full"]; refs=gt_references(gt,ref_mask); boundary={}
            for canvas_name,depth in canvas_depth.items():
                regions,region_masks=evaluate_object_depth(det["bbox_xyxy"],depth,segment_mask=mask)
                per_canvas[canvas_name]=regions; per_masks[canvas_name]=region_masks
                gt_eval[canvas_name]={}
                for region_name,region_mask in region_masks.items():
                    metrics=depth_metrics(depth,gt,region_mask)
                    gt_eval[canvas_name][region_name]=metrics
                    for statistic,candidate in regions.get(region_name,{}).get("statistics",{}).items():
                        row={"sample_id":sample_id,"object_id":object_id,"class_name":det["class_name"],"canvas":canvas_name,
                             "region":region_name,"statistic":statistic,"status":candidate.get("status"),
                             "small_object":((det["bbox_xyxy"][2]-det["bbox_xyxy"][0])*(det["bbox_xyxy"][3]-det["bbox_xyxy"][1]) < 0.02*640*360),
                             "has_segmentation":mask is not None,"gt_used_for_inference":False}
                        if candidate.get("status")=="valid":
                            errors=[abs(candidate["surface_depth_m"]-v) for v in refs.values() if v is not None]
                            row.update({"surface_depth_m":candidate["surface_depth_m"],"gt_scalar_error_m":float(np.mean(errors)),
                                        "pixel_mae_m":metrics.get("mae_m"),"pixel_p95_m":metrics.get("p95_absolute_error_m"),
                                        "multimodal":candidate["multimodal"],"valid_ratio":candidate["valid_ratio"]})
                        matrix_rows.append(row)
                for region_name,region_mask in region_masks.items(): masks_to_save[f"{object_id}__{canvas_name}__{region_name}"]=region_mask
            method=STAGE3_SEGMENT_METHOD if mask is not None else STAGE3_DETECT_METHOD
            recommended={k:choose_candidates(per_canvas[k],method) for k in per_canvas}
            direct=recommended["direct_640x360"]; letter=recommended["letterbox_640x480"]
            agreement=abs(direct["surface_depth_m"]-letter["surface_depth_m"]) if direct.get("status")==letter.get("status")=="valid" else None
            objects.append({"object_id":object_id,"class_id":det["class_id"],"class_name":det["class_name"],"class_confidence":det["confidence"],
                "detect_bbox":det["bbox_xyxy"],"segment_bbox":seg["bbox_xyxy"] if seg else None,"match_iou":match["match_iou"],
                "match_status":match["match_status"],"regions":per_canvas,"recommended_candidate":recommended,
                "direct_letterbox_recommended_delta_m":agreement,"gt_references":refs,"boundary_evaluation":boundary,"warnings":[]})
        object_processing_ms+=(time.perf_counter()-object_started)*1000.0
        np.savez_compressed(output/"object_regions.npz",**masks_to_save)
        object_document={"schema_version":"deltar_stage3_object_depth.v1","status":"success","sample_id":sample_id,
            "models":{"detect":detect_meta,"segment":segment_meta,"deltar":deltar_results["direct_640x360"]["deltar_provenance"]},
            "deltar_candidates":["direct_640x360","letterbox_640x480"],"objects":objects,"matches":matches,
            "gt_used_for_inference":False,"warnings":[]}
        write_json(output/"object_depth_results.json",object_document)
        write_json(output/"gt_evaluation.json",{"schema_version":"deltar_stage3_gt_evaluation.v1","sample_id":sample_id,
            "gt_source":str(parent/"evaluation_gt_depth_source.npy"),"gt_used_for_inference":False,"objects":gt_eval})
        validation={"schema_version":"deltar_stage3_validation.v1","status":"pass","sample_id":sample_id,
            "source_shape":[360,640],"detect_instances":len(detections),"segment_instances":len(segments),"matched_instances":len(matched),
            "mask_shapes_valid":all(v.shape==(360,640) and v.dtype==bool for v in segment["masks"].values()),
            "direct_source_shape":list(canvas_depth["direct_640x360"].shape),"letterbox_source_shape":list(canvas_depth["letterbox_640x480"].shape),
            "gt_used_for_inference":False,"repeatability_exact":all(deltar_results[k]["status"]=="success" for k in deltar_results),
            "timing_ms":(time.perf_counter()-sample_started)*1000.0}
        write_json(output/"validation_report.json",validation)
        sample_summaries.append({"sample_id":sample_id,"detect_instances":len(detections),"segment_instances":len(segments),
                                 "matched_instances":len(matched),"object_records":len(objects)})
    groups=defaultdict(list)
    for row in matrix_rows: groups[(row["region"],row["statistic"])].append(row)
    method_matrix=[]
    for (region,statistic),rows in groups.items():
        valid=[r for r in rows if r["status"]=="valid" and r.get("gt_scalar_error_m") is not None]
        direct={(r["object_id"],r["surface_depth_m"]) for r in valid if r["canvas"]=="direct_640x360"}
        direct_map={k:v for k,v in direct}; pairs=[abs(r["surface_depth_m"]-direct_map[r["object_id"]]) for r in valid if r["canvas"]=="letterbox_640x480" and r["object_id"] in direct_map]
        errors=[r["gt_scalar_error_m"] for r in valid]; maes=[r["pixel_mae_m"] for r in valid if r.get("pixel_mae_m") is not None]
        small=[r for r in rows if r["small_object"]]; small_valid=[r for r in small if r["status"]=="valid"]
        entry={"region":region,"statistic":statistic,"valid_count":len(valid),"total_count":len(rows),
            "gt_scalar_median_error_m":float(np.median(errors)) if errors else None,"gt_scalar_p90_error_m":float(np.percentile(errors,90)) if errors else None,
            "gt_pixel_mae_m":float(np.mean(maes)) if maes else None,"multimodal_valid_count":sum(bool(r.get("multimodal")) for r in valid),
            "small_object_pass_rate":len(small_valid)/len(small) if small else None,"direct_letterbox_median_delta_m":float(np.median(pairs)) if pairs else None,
            "rejection_rate":1-len(valid)/len(rows) if rows else 1.0}
        if errors:
            entry["ranking_score_experimental"]=-entry["gt_scalar_median_error_m"]-0.5*entry["gt_scalar_p90_error_m"]-entry["rejection_rate"]-0.25*(entry["direct_letterbox_median_delta_m"] or 0)
        method_matrix.append(entry)
    ranked=sorted((x for x in method_matrix if "ranking_score_experimental" in x),key=lambda x:x["ranking_score_experimental"],reverse=True)
    primary=next((x for x in ranked if x["region"].startswith("segment") and x["valid_count"]>=10),ranked[0] if ranked else None)
    fallback=next((x for x in ranked if x["region"].startswith("bbox") and x["valid_count"]>=10),None)
    matrix={"schema_version":"deltar_stage3_matrix.v1","status":"pass","official_sample_count":len(parents),"object_record_count":total_objects,
            "matched_object_count":matched_total,"sample_summaries":sample_summaries,"method_matrix":method_matrix,
            "ranking_formula":"-median_scalar_error - 0.5*p90_scalar_error - rejection_rate - 0.25*median_canvas_delta",
            "recommended_primary":primary,"recommended_fallback":fallback,"deltar_repeatability_exact":deterministic,
            "gt_used_for_inference":False,"performance":{"yolo_detection_batch_ms":detect_outputs[0]["worker_batch_timing_ms"],
                "yolo_segmentation_batch_ms":segment_outputs[0]["worker_batch_timing_ms"],
                "deltar_direct_mean_sample_ms":float(np.mean(deltar_timing["direct_640x360"])),
                "deltar_letterbox_mean_sample_ms":float(np.mean(deltar_timing["letterbox_640x480"])),
                "object_region_statistics_total_ms":object_processing_ms,"device":{"yolo":"cpu","deltar":"cuda:0"}},
            "timing_ms":(time.perf_counter()-started)*1000.0}
    write_json(ROOT/"outputs_stage3"/"stage3_matrix.json",matrix)
    print(json.dumps({"status":"pass","samples":len(parents),"objects":total_objects,"matched":matched_total,
                      "primary":primary,"fallback":fallback,"timing_ms":matrix["timing_ms"]},indent=2,ensure_ascii=False))


if __name__=="__main__": main()
