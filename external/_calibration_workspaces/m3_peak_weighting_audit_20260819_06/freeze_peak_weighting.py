from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

W = Path(__file__).resolve().parent
COMPACT = Path(r"C:\ec\_calibration_workspaces\single_frame_candidate_information_audit_20260819_02\round2_compact_candidate_data.json")
SOURCE = Path(r"C:\ec\_calibration_workspaces\single_frame_candidate_information_audit_20260819_02\extract_and_counterfactual.py")
P_THRESHOLD = 0.68
R_THRESHOLD = 1.85
HUBER_DELTA_MM = 45.0


SCHEMES = {
    "W0": "Exact frozen M3: top if p_top>=0.68 or p_top/p_second>=1.85, else retained-mode probability expectation.",
    "W1": "All retained modes, original-probability expectation: sum(p_i*center_i).",
    "W2": "Top two retained modes only, probabilities normalized within top two; single mode unchanged.",
    "W3": "All modes, sqrt(probability)-normalized expectation; exponent fixed at 0.5.",
    "W4": "All modes, squared-probability-normalized expectation; exponent fixed at 2.",
    "W5": "Equal retained-mode mean.",
    "W6": "Dominant probability capped at 0.60; remaining 0.40 distributed over other modes in original relative proportions; unchanged if top<=0.60; single mode unchanged.",
    "W7": "Probability-weighted Huber location on mode centers; delta fixed to frozen M3 source huber_delta_mm=45.",
    "W8": "Continuous top-vs-W1 blend: s=max(p_top/0.68,(p_top/p_second)/1.85), alpha=s/(1+s), output=alpha*top+(1-alpha)*W1; single mode unchanged.",
}


def canonical(v: Any) -> bytes:
    return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expectation(centers, weights):
    return float(np.average(np.asarray(centers,float),weights=np.asarray(weights,float)))


def weighted_median(values, weights):
    values=np.asarray(values,float);weights=np.asarray(weights,float);order=np.argsort(values,kind="stable")
    return float(values[order][np.searchsorted(np.cumsum(weights[order]),0.5*weights.sum(),side="left")])


def huber(values, weights):
    values=np.asarray(values,float);weights=np.asarray(weights,float);location=weighted_median(values,weights)
    for _ in range(32):
        residual=values-location; influence=np.minimum(1.0,HUBER_DELTA_MM/np.maximum(np.abs(residual),1e-12))
        updated=float(np.average(values,weights=weights*influence))
        if abs(updated-location)<1e-8: break
        location=updated
    return location


def calculate(modes):
    c=np.asarray([m["center_mm"] for m in modes],float);p=np.asarray([m["probability"] for m in modes],float);p=p/p.sum()
    top=float(c[0]); ratio=math.inf if len(c)==1 else float(p[0]/max(p[1],1e-12)); w1=expectation(c,p)
    if len(c)==1:
        return {f"W{i}":top for i in range(9)},ratio
    w2=expectation(c[:2],p[:2]);w3=expectation(c,np.sqrt(p));w4=expectation(c,p**2);w5=float(c.mean())
    if p[0] <= 0.60:
        cap=p.copy()
    else:
        cap=np.empty_like(p);cap[0]=0.60;cap[1:]=0.40*p[1:]/p[1:].sum()
    w6=expectation(c,cap);w7=huber(c,p)
    s=max(float(p[0]/P_THRESHOLD),float(ratio/R_THRESHOLD));alpha=s/(1.0+s);w8=float(alpha*top+(1.0-alpha)*w1)
    w0=top if (p[0]>=P_THRESHOLD or ratio>=R_THRESHOLD) else w1
    return {"W0":float(w0),"W1":w1,"W2":w2,"W3":w3,"W4":w4,"W5":w5,"W6":w6,"W7":w7,"W8":w8},ratio


def main():
    compact=json.loads(COMPACT.read_text(encoding="utf-8"));rows=[];max_w0_diff=0.0
    for scan,sd in compact["scans"].items():
        for frame in sd["frames"]:
            for context in frame["contexts"]:
                est=context["p1_p2_p3_estimators"];modes=est["modes"];outputs,ratio=calculate(modes);top_p=float(modes[0]["probability"])
                max_w0_diff=max(max_w0_diff,abs(outputs["W0"]-float(est["late_multimodal_soft"])))
                rows.append({"scan":scan,"frame_id":frame["frame_id"],"context_id":context["context_id"],"member_object_ids":context["member_object_ids"],"member_classes":context["member_classes"],"bbox_bottom_y_center":context["bbox_bottom_y_center"],"modes":[{k:m[k] for k in ("center_mm","probability","spread_mm","mass","candidate_count","grid_peak_mm")} for m in modes],"mode_count":len(modes),"top_probability":top_p,"top_second_ratio":None if math.isinf(ratio) else ratio,"probability_margin_to_0_68":top_p-P_THRESHOLD,"ratio_margin_to_1_85":None if math.isinf(ratio) else ratio-R_THRESHOLD,"W0_branch":"HARD_SINGLE" if len(modes)==1 else ("HARD_TOP" if top_p>=P_THRESHOLD or ratio>=R_THRESHOLD else "SOFT_EXPECTATION"),"outputs_mm":outputs})
    output_hash=hashlib.sha256(canonical(rows)).hexdigest()
    doc={"schema_version":"m3_peak_weighting_freeze.v1","truth_loaded":False,"runtime_uses_cross_frame_information":False,"source":{"compact_path":str(COMPACT),"compact_sha256":sha(COMPACT),"m3_source_path":str(SOURCE),"m3_source_sha256":sha(SOURCE),"current_script_path":str(Path(__file__).resolve()),"current_script_sha256":sha(Path(__file__).resolve())},"frozen_m3":{"late_maximum_modes":4,"late_dominant_probability":P_THRESHOLD,"late_dominant_ratio":R_THRESHOLD,"huber_delta_mm":HUBER_DELTA_MM,"exact_reproduction_max_abs_mm":max_w0_diff},"scheme_definitions":SCHEMES,"contexts":rows,"outputs_sha256":output_hash}
    (W/"PEAK_WEIGHTING_FREEZE.json").write_text(json.dumps(doc,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"contexts":len(rows),"max_w0_reproduction_diff_mm":max_w0_diff,"outputs_sha256":output_hash,"script_sha256":doc["source"]["current_script_sha256"]},indent=2))


if __name__=="__main__": main()
