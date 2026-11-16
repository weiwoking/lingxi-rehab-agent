from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from deltar_p4_pipeline.analytic_renderer import stage5_scene_family
from deltar_p4_pipeline.stage5_metrics import summarize_rows
from deltar_p4_pipeline.synthetic_geometry import semantics_roundtrip_error
from deltar_p4_pipeline.tof_simulator import simulate_tof
from deltar_p4_pipeline.stage5_validation import assess_stage5


def write(name:str,value:object)->None:(ROOT/"outputs_stage5"/name).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")


def main()->None:
    matrix=json.loads((ROOT/"outputs_stage5"/"full_matrix.json").read_text(encoding="utf-8"));rows=matrix["rows"]
    a2=[r for scene in matrix["scenes"] for r in scene["ablation"]["A2_YOLO_GT"]];a4=[r for scene in matrix["scenes"] for r in scene["ablation"]["A4_BBOX_DELTAR"]]
    a6=json.loads((ROOT/"outputs_stage5"/"canvas_summary.json").read_text(encoding="utf-8"))
    ablation={"schema_version":"deltar_stage5_ablation.v1","A0_Oracle":summarize_rows(rows["L0"]),
              "A1_DELTAR_only":summarize_rows([r for r in rows["L1"] if r.get("canvas")=="direct_640x360"]),"A2_YOLO_only":summarize_rows(a2),
              "A3_Full_new":summarize_rows([r for r in rows["L2"] if r.get("canvas")=="direct_640x360"]),"A4_Detection_fallback":summarize_rows(a4),
              "A5_Old_baseline":summarize_rows(rows["L3"]),"A6_Direct_vs_Letterbox":a6,
              "A7_Axial_vs_Radial":{"axial_truth":semantics_roundtrip_error("axial_z"),"radial_truth":semantics_roundtrip_error("radial_range")}}
    base=next(s for s in stage5_scene_family() if s["scenario_family"]=="F12");sensitivity=[]
    for baseline_mm in (0,20,40,60):
        for rotation in (0,90,180,270):
            for error_deg in (0,-1,1,-3,3):
                scene=copy.deepcopy(base);scene["tof"]["T_camera_from_tof"][0][3]=baseline_mm/1000;scene["tof"]["in_plane_rotation_deg"]=rotation+error_deg
                tof,_=simulate_tof(scene,seed=baseline_mm+rotation+error_deg+10);valid=[d for d,c in zip(tof["distance_mm"],tof["nb_target_detected"],strict=True) if c]
                sensitivity.append({"baseline_long_axis_mm":baseline_mm,"rotation_deg":rotation,"extra_rotation_error_deg":error_deg,
                                    "valid_zone_count":len(valid),"median_range_m":float(np.median(valid))/1000 if valid else None})
    reference=next(r["median_range_m"] for r in sensitivity if r["baseline_long_axis_mm"]==0 and r["rotation_deg"]==0 and r["extra_rotation_error_deg"]==0)
    for row in sensitivity:row["median_delta_from_reference_m"]=abs(row["median_range_m"]-reference) if row["median_range_m"] is not None else None
    alignment={"schema_version":"deltar_stage5_alignment_sensitivity.v1","case_count":len(sensitivity),"simulation_values_not_real_mount":True,"cases":sensitivity}
    ablation["A8_Alignment_perturbation"]={"case_count":len(sensitivity),"max_median_range_delta_m":max(r["median_delta_from_reference_m"] for r in sensitivity if r["median_delta_from_reference_m"] is not None)}
    write("ablation_matrix.json",ablation);write("alignment_sensitivity.json",alignment)
    comparison=json.loads((ROOT/"outputs_stage5"/"old_vs_new_summary.json").read_text(encoding="utf-8"));write("stage5_acceptance.json",assess_stage5(matrix,comparison,ablation))
    print(json.dumps({"status":"pass","alignment_cases":len(sensitivity)},indent=2))


if __name__=="__main__":main()
