from __future__ import annotations

from typing import Any

import numpy as np


def distribution(values: list[float]) -> dict[str, float | None]:
    if not values:return {key:None for key in ("mean","median","p75","p90","p95","max")}
    data=np.asarray(values,dtype=np.float64)
    return {"mean":float(data.mean()),"median":float(np.median(data)),"p75":float(np.percentile(data,75)),
            "p90":float(np.percentile(data,90)),"p95":float(np.percentile(data,95)),"max":float(data.max())}


def depth_metrics(predicted: np.ndarray, truth: np.ndarray, mask: np.ndarray) -> dict[str, float | None]:
    valid=np.asarray(mask,dtype=bool)&np.isfinite(predicted)&np.isfinite(truth)&(predicted>0)&(truth>0)
    if not valid.any():return {"pixel_mae_m":None,"median_ae_m":None,"rmse_m":None,"p90_m":None,"p95_m":None,"valid_pixels":0}
    error=np.abs(np.asarray(predicted)[valid]-np.asarray(truth)[valid]); signed=np.asarray(predicted)[valid]-np.asarray(truth)[valid]
    return {"pixel_mae_m":float(error.mean()),"median_ae_m":float(np.median(error)),"rmse_m":float(np.sqrt(np.mean(signed*signed))),
            "p90_m":float(np.percentile(error,90)),"p95_m":float(np.percentile(error,95)),"valid_pixels":int(valid.sum())}


def physical_height_applicability(truth: dict[str, Any], *, occlusion_threshold: float = .10,
                                  minimum_mask_coverage: float = .85) -> dict[str, Any]:
    coverage = float(truth.get("visible_pixel_count", 0)) / max(float(truth.get("full_projected_pixel_count", 0)), 1.0)
    reasons = []
    if float(truth.get("occlusion_ratio", 1.0)) > occlusion_threshold: reasons.append("occluded")
    if bool(truth.get("image_border_contact")): reasons.append("touches_image_border")
    if not bool(truth.get("full_geometric_extent_inside_image", not truth.get("image_border_contact", False))): reasons.append("full_extent_outside_image")
    if coverage < minimum_mask_coverage: reasons.append("insufficient_mask_coverage")
    return {"physical_height_metric_status": "applicable" if not reasons else "not_applicable",
            "physical_height_not_applicable_reasons": reasons, "mask_coverage": coverage,
            "occlusion_threshold": occlusion_threshold, "minimum_mask_coverage": minimum_mask_coverage}


def geometry_errors(geometry: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    applicability = physical_height_applicability(truth)
    physical_error = abs(float(geometry["object_height_m"])-float(truth["true_physical_height_m"])) if applicability["physical_height_metric_status"] == "applicable" else None
    return {"distance_absolute_error_m":abs(float(geometry["distance_m"])-float(truth["true_distance_m"])),
            "height_absolute_error_m":physical_error,
            "physical_height_absolute_error_m":physical_error,
            "bottom_height_absolute_error_m":abs(float(geometry["bottom_height_m"])-float(truth["physical_bottom_m"])),
            "visible_bottom_height_absolute_error_m":abs(float(geometry["bottom_height_m"])-float(truth["visible_bottom_m"])),
            "visible_height_absolute_error_m":abs(float(geometry["object_height_m"])-float(truth["true_visible_height_m"])),
            **applicability}


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total=len(rows); valid=[row for row in rows if row.get("status")=="valid"]
    output={"sample_count":total,"valid_count":len(valid),"failure_rate":sum(row.get("status")=="failure" for row in rows)/max(total,1),
            "rejection_rate":sum(row.get("status")=="rejected" for row in rows)/max(total,1),
            "false_valid_rate":sum(bool(row.get("false_valid")) for row in rows)/max(total,1)}
    for key in ("distance_absolute_error_m","height_absolute_error_m","bottom_height_absolute_error_m","visible_height_absolute_error_m"):
        output[key]=distribution([float(row[key]) for row in valid if row.get(key) is not None])
    return output
