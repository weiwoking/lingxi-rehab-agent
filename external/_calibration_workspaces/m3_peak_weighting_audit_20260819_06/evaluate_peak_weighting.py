from __future__ import annotations

import json
from pathlib import Path

import numpy as np

W=Path(__file__).resolve().parent
TRUTH_PATH=Path(r"C:\ec\_codex_reports\geometry_numeric_first_and_full_reaudit_20260819_01\evidence\all_numeric_metrics.json")
CORE={"cup","bottle","sports ball","sports_ball","ball"}
SCHEMES=[f"W{i}" for i in range(9)]
TRUTH={"lower":725.0,"upper":1065.0}
COMPLEXITY={"W0":3,"W1":1,"W2":1,"W3":1,"W4":1,"W5":1,"W6":2,"W7":2,"W8":2}


def klass(v): return str(v).strip().lower().replace("-"," ")


def stats(values,truth):
    a=np.asarray(values,float);err=np.abs(a-truth);median=float(np.median(a))
    return {"count":int(a.size),"min_mm":float(a.min()),"max_mm":float(a.max()),"median_mm":median,"mean_mm":float(a.mean()),"mad_mm":float(np.median(abs(a-median))),"std_mm":float(a.std()),"span_mm":float(np.ptp(a)),"p05_mm":float(np.percentile(a,5)),"p95_mm":float(np.percentile(a,95)),"median_bias_mm":median-truth,"median_abs_error_mm":float(np.median(err)),"within20_count":int(np.count_nonzero(err<=20)),"within30_count":int(np.count_nonzero(err<=30)),"within50_count":int(np.count_nonzero(err<=50)),"gt50_count":int(np.count_nonzero(err>50)),"gt75_count":int(np.count_nonzero(err>75)),"gt100_count":int(np.count_nonzero(err>100))}


def main():
    freeze=json.loads((W/"PEAK_WEIGHTING_FREEZE.json").read_text(encoding="utf-8"))
    truth_doc=json.loads(TRUTH_PATH.read_text(encoding="utf-8"));truth_by_id={(r["scan_label"],r["logical_object_id"]):r for r in truth_doc["rows"]}
    values={scope:{s:{} for s in SCHEMES} for scope in ("CORE_OBJECTS","ALL_OBJECTS")}
    object_rows=[]
    for c in freeze["contexts"]:
        for oid,cl in zip(c["member_object_ids"],c["member_classes"]):
            metric=truth_by_id[(c["scan"],oid)];layer=metric["layer"]
            for scope in values:
                if scope=="CORE_OBJECTS" and klass(cl) not in CORE: continue
                for scheme in SCHEMES:
                    values[scope][scheme].setdefault(c["scan"],{"lower":[],"upper":[]})[layer].append(c["outputs_mm"][scheme])
            object_rows.append({"scan":c["scan"],"frame_id":c["frame_id"],"context_id":c["context_id"],"object_id":oid,"class_name":cl,"layer":layer,"truth_mm":TRUTH[layer],"outputs_mm":c["outputs_mm"]})
    scopes={}
    for scope in values:
        scopes[scope]={}
        for scheme in SCHEMES:
            per={};aggregate={"lower":[],"upper":[]}
            for scan in sorted(values[scope][scheme]):
                per[scan]={}
                for layer in ("lower","upper"):
                    v=values[scope][scheme][scan][layer];aggregate[layer]+=v;per[scan][layer]=stats(v,TRUTH[layer]) if v else {"count":0}
            lower=[per[s]["lower"] for s in per if per[s]["lower"].get("count")]
            agg={layer:stats(aggregate[layer],TRUTH[layer]) if aggregate[layer] else {"count":0} for layer in ("lower","upper")}
            scopes[scope][scheme]={"per_scan":per,"aggregate":agg,"numeric_count":sum(x["count"] for x in agg.values()),"mean_scan_lower_mad_mm":float(np.mean([x["mad_mm"] for x in lower])),"mean_scan_lower_std_mm":float(np.mean([x["std_mm"] for x in lower])),"mean_scan_lower_span_mm":float(np.mean([x["span_mm"] for x in lower])),"gt50_count":sum(x["gt50_count"] for x in agg.values()),"gt75_count":sum(x["gt75_count"] for x in agg.values()),"gt100_count":sum(x["gt100_count"] for x in agg.values()),"complexity_score":COMPLEXITY[scheme]}
    focus=[]
    for c in freeze["contexts"]:
        if c["scan"]=="SCAN_085223" and "sports ball" in c["member_classes"]:
            focus.append({**c,"truth_mm":725.0,"errors_mm":{k:c["outputs_mm"][k]-725.0 for k in SCHEMES}})
    focus.sort(key=lambda x:x["frame_id"])
    focus_stats={};distribution={}
    bins=[("<700",lambda v:v<700),("700-749.9",lambda v:700<=v<750),("750-799.9",lambda v:750<=v<800),("800-849.9",lambda v:800<=v<850),("850-899.9",lambda v:850<=v<900),(">=900",lambda v:v>=900)]
    for scheme in SCHEMES:
        v=[r["outputs_mm"][scheme] for r in focus];focus_stats[scheme]=stats(v,725.0);distribution[scheme]={name:sum(fn(x) for x in v) for name,fn in bins}
    single=[r for r in focus if r["mode_count"]==1];multi=[r for r in focus if r["mode_count"]>1]
    single_error_gt50=sum(abs(r["outputs_mm"]["W0"]-725)>50 for r in single)
    multi_effect={}
    for scheme in SCHEMES[1:]:
        improved=sum(abs(r["outputs_mm"][scheme]-725)<abs(r["outputs_mm"]["W0"]-725)-1e-9 for r in multi)
        worsened=sum(abs(r["outputs_mm"][scheme]-725)>abs(r["outputs_mm"]["W0"]-725)+1e-9 for r in multi)
        multi_effect[scheme]={"improved_frames":improved,"worsened_frames":worsened,"unchanged_frames":len(multi)-improved-worsened}
    core=scopes["CORE_OBJECTS"]
    base=core["W0"]
    repeatability_score={s:0.5*(core[s]["mean_scan_lower_span_mm"]/base["mean_scan_lower_span_mm"]+core[s]["mean_scan_lower_mad_mm"]/base["mean_scan_lower_mad_mm"]) for s in SCHEMES}
    ranking=sorted(SCHEMES[1:],key=lambda s:(core[s]["gt100_count"],repeatability_score[s],core[s]["gt75_count"],core[s]["gt50_count"],abs(core[s]["aggregate"]["lower"]["median_bias_mm"]),COMPLEXITY[s]))
    criteria={s:{"core_catastrophic_not_increased":core[s]["gt100_count"]<=base["gt100_count"],"mean_lower_span_improved":core[s]["mean_scan_lower_span_mm"]<base["mean_scan_lower_span_mm"]-1e-9,"mean_lower_mad_improved":core[s]["mean_scan_lower_mad_mm"]<base["mean_scan_lower_mad_mm"]-1e-9,"scan_085223_not_worse":(focus_stats[s]["gt100_count"],focus_stats[s]["span_mm"],focus_stats[s]["mad_mm"])<=(focus_stats["W0"]["gt100_count"],focus_stats["W0"]["span_mm"],focus_stats["W0"]["mad_mm"]),"no_truth_fit":True,"complexity_le_m3":COMPLEXITY[s]<=COMPLEXITY["W0"]} for s in SCHEMES[1:]}
    clearly=[s for s in SCHEMES[1:] if all(criteria[s].values())]
    best=next((s for s in ranking if s in clearly),ranking[0])
    out={"schema_version":"m3_peak_weighting_evaluation.v1","truth_loaded_after_freeze":True,"truth_mm":TRUTH,"freeze_outputs_sha256":freeze["outputs_sha256"],"scopes":scopes,"scan_085223_lower_focus":{"definition":"one context per frame containing sports ball; this is the shared core lower support context","frames":focus,"single_mode_frame_count":len(single),"multi_mode_frame_count":len(multi),"single_mode_error_gt50_count":single_error_gt50,"single_mode_gt100_count":sum(abs(r["outputs_mm"]["W0"]-725)>100 for r in single),"multi_mode_effect_vs_W0":multi_effect,"stats":focus_stats,"distribution_bins":distribution},"competition":{"priority":["CORE gt100","equal-weight normalized pair: mean lower span and MAD","CORE gt75","CORE gt50","aggregate lower abs median bias","complexity"],"repeatability_pair_score_vs_W0":repeatability_score,"variant_ranking":ranking,"best_variant":best,"clearly_better_criteria":criteria,"variants_meeting_all_clear_better_criteria":clearly,"final_decision":f"PEAK_WEIGHTING_VARIANT_{best}_IS_CLEARLY_BETTER_THAN_M3" if best in clearly else "KEEP_FROZEN_M3"},"object_rows":object_rows}
    (W/"PEAK_WEIGHTING_TRUTH_EVALUATION.json").write_text(json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"focus_counts":{"single":len(single),"multi":len(multi),"single_gt50":single_error_gt50},"focus_W0":focus_stats["W0"],"best_variant":best,"best_focus":focus_stats[best],"core_W0":{k:base[k] for k in ("mean_scan_lower_mad_mm","mean_scan_lower_span_mm","gt50_count","gt75_count","gt100_count")},"core_best":{k:core[best][k] for k in ("mean_scan_lower_mad_mm","mean_scan_lower_span_mm","gt50_count","gt75_count","gt100_count")},"clearly_better":clearly,"decision":out["competition"]["final_decision"]},indent=2))


if __name__=="__main__": main()
