from __future__ import annotations

from typing import Any

import numpy as np


def bbox_iou(a: list[float], b: list[float]) -> float:
    x1,y1=max(a[0],b[0]),max(a[1],b[1]); x2,y2=min(a[2],b[2]),min(a[3],b[3])
    inter=max(0.0,x2-x1)*max(0.0,y2-y1); area_a=max(0,a[2]-a[0])*max(0,a[3]-a[1]); area_b=max(0,b[2]-b[0])*max(0,b[3]-b[1])
    return inter/max(area_a+area_b-inter,1e-12)


def mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    a=np.asarray(a,dtype=bool); b=np.asarray(b,dtype=bool); union=np.logical_or(a,b).sum()
    return float(np.logical_and(a,b).sum()/union) if union else 0.0


def match_objects(predictions: list[dict[str, Any]], truths: list[dict[str, Any]], *, threshold: float = .3,
                  prediction_masks: dict[str,np.ndarray] | None = None, truth_masks: dict[str,np.ndarray] | None = None) -> list[dict[str, Any]]:
    candidates=[]
    for pi,pred in enumerate(predictions):
        for ti,truth in enumerate(truths):
            if pred.get("class_name")!=truth.get("class_name"):continue
            score=bbox_iou(pred["bbox_xyxy"],truth["bbox_xyxy"])
            if prediction_masks and truth_masks and pred.get("mask_key") in prediction_masks:
                score=mask_iou(prediction_masks[pred["mask_key"]],truth_masks[truth["visible_mask_key"]])
            candidates.append((score,pi,ti))
    used_p=set();used_t=set();rows=[]
    for score,pi,ti in sorted(candidates,reverse=True):
        if score<threshold or pi in used_p or ti in used_t:continue
        used_p.add(pi);used_t.add(ti);rows.append({"match_status":"matched","prediction_index":pi,"truth_index":ti,"iou":score})
    rows.extend({"match_status":"fp","prediction_index":i,"truth_index":None,"iou":0.0} for i in range(len(predictions)) if i not in used_p)
    rows.extend({"match_status":"fn","prediction_index":None,"truth_index":i,"iou":0.0} for i in range(len(truths)) if i not in used_t)
    return rows


def classify_prediction_universe(predictions: list[dict[str, Any]], truths: list[dict[str, Any]], *, threshold: float = .3,
                                 prediction_masks: dict[str,np.ndarray] | None = None,
                                 truth_masks: dict[str,np.ndarray] | None = None) -> list[dict[str, Any]]:
    """One-to-one truth matching plus explicit wrong-class/duplicate/FP/FN taxonomy."""
    scores: dict[tuple[int, int], float] = {}
    for pi, pred in enumerate(predictions):
        for ti, truth in enumerate(truths):
            if truth.get("bbox_xyxy") is None:
                continue
            score = bbox_iou(pred["bbox_xyxy"], truth["bbox_xyxy"])
            if prediction_masks and truth_masks and pred.get("mask_key") in prediction_masks:
                score = mask_iou(prediction_masks[pred["mask_key"]], truth_masks[truth["visible_mask_key"]])
            scores[pi, ti] = score
    candidates = sorted(((score, pi, ti) for (pi, ti), score in scores.items()
                         if predictions[pi].get("class_name") == truths[ti].get("class_name")), reverse=True)
    used_p: set[int] = set(); used_t: set[int] = set(); rows: list[dict[str, Any]] = []
    for score, pi, ti in candidates:
        if score < threshold or pi in used_p or ti in used_t:
            continue
        used_p.add(pi); used_t.add(ti)
        rows.append({"match_status": "tp", "prediction_index": pi, "truth_index": ti, "iou": float(score)})
    for pi, pred in enumerate(predictions):
        if pi in used_p:
            continue
        overlaps = sorted(((score, ti) for (ppi, ti), score in scores.items() if ppi == pi), reverse=True)
        best_score, best_ti = overlaps[0] if overlaps else (0.0, None)
        if best_ti is not None and best_score >= threshold and pred.get("class_name") != truths[best_ti].get("class_name"):
            status = "wrong_class"
        elif best_ti is not None and best_score >= threshold and best_ti in used_t:
            status = "duplicate"
        else:
            status = "fp"
            best_ti = None
        rows.append({"match_status": status, "prediction_index": pi, "truth_index": best_ti, "iou": float(best_score)})
    rows.extend({"match_status": "fn", "prediction_index": None, "truth_index": ti, "iou": 0.0}
                for ti in range(len(truths)) if ti not in used_t)
    return rows


def perception_counts(rows: list[dict[str, Any]], geometry_valid_by_prediction: dict[int, bool]) -> dict[str, Any]:
    predictions = [row for row in rows if row["prediction_index"] is not None]
    valid = [row for row in predictions if geometry_valid_by_prediction.get(int(row["prediction_index"]), False)]
    false_valid = [row for row in valid if row["match_status"] != "tp"]
    return {
        "TP": sum(row["match_status"] == "tp" for row in rows),
        "FP": sum(row["match_status"] in {"fp", "wrong_class", "duplicate"} for row in rows),
        "FN": sum(row["match_status"] == "fn" for row in rows),
        "wrong_class": sum(row["match_status"] == "wrong_class" for row in rows),
        "duplicate": sum(row["match_status"] == "duplicate" for row in rows),
        "valid_TP": sum(row["match_status"] == "tp" for row in valid),
        "valid_FP": len(false_valid),
        "rejected_TP": sum(row["match_status"] == "tp" and not geometry_valid_by_prediction.get(int(row["prediction_index"]), False) for row in predictions),
        "false_valid_count": len(false_valid),
        "false_valid_rate_among_valid_predictions": len(false_valid) / max(len(valid), 1),
        "valid_prediction_count": len(valid),
        "prediction_count": len(predictions),
    }
