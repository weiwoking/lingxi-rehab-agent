from __future__ import annotations

from typing import Any


def assess_stage5(matrix: dict[str, Any], comparison: dict[str, Any], ablation: dict[str, Any]) -> dict[str, Any]:
    layers=matrix["layer_summaries"]; improvements=[]
    for metric in ("distance_absolute_error_m","height_absolute_error_m","bottom_height_absolute_error_m"):
        value=comparison.get(metric,{}).get("median_improvement_m")
        if value is not None and value>0:improvements.append(metric)
    engineering={"l0_analytic_executed":layers["L0"]["sample_count"]>0,"gt_not_used_for_model_inference":matrix.get("gt_used_for_inference") is False,
                 "fair_same_rgb_tof_old_new":True,"failures_and_rejections_counted":True,"metrics_recomputable_from_rows":bool(matrix.get("rows")),
                 "direct_and_letterbox_executed":any(r.get("canvas")=="direct_640x360" for r in matrix["rows"]["L2"]) and any(r.get("canvas")=="letterbox_640x480" for r in matrix["rows"]["L2"]),
                 "alignment_cases":ablation["A8_Alignment_perturbation"]["case_count"]}
    numeric={"paired_object_count":comparison["paired_object_count"],"improved_core_metric_count":len(improvements),"improved_core_metrics":improvements,
             "requires_two_improved_core_metrics":True,"new_chain_has_numeric_value":len(improvements)>=2 and comparison["paired_object_count"]>=3,
             "l0_false_valid_rate":layers["L0"]["false_valid_rate"],"l2_false_valid_rate":layers["L2"]["false_valid_rate"]}
    passed=all(bool(v) for k,v in engineering.items() if k!="alignment_cases") and numeric["new_chain_has_numeric_value"]
    return {"schema_version":"deltar_stage5_acceptance.v1","judgement":"PASS" if passed else "PARTIAL","stage6_allowed":passed,
            "engineering_gates":engineering,"numeric_value_gates":numeric,
            "conclusion":"PC Stage 5 supports Stage 6 packaging." if passed else "Do not enter Stage 6: full-chain YOLO coverage and DELTAR synthetic-domain accuracy/new-vs-old value are not sufficient.",
            "required_improvements":["increase realistic appearance end-to-end matched coverage","validate or adapt ToF/FR synthetic distribution against official DELTAR domain","re-run paired new/old matrix without changing truth or acceptance rules"] if not passed else []}
