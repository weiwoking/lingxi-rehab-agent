from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
from PIL import Image


GT_RESIZE_METHODS = ("bilinear", "nearest", "area")


def valid_aware_resize_depth(depth: np.ndarray, method: str, shape: tuple[int, int] = (360, 640)) -> tuple[np.ndarray, np.ndarray]:
    """Resize depth without allowing zero/non-finite pixels to bleed into valid depth."""
    source = np.asarray(depth, dtype=np.float32)
    if source.ndim != 2:
        raise ValueError("depth must be two-dimensional")
    if method not in GT_RESIZE_METHODS:
        raise ValueError(f"unsupported GT resize method: {method}")
    resampling = {"bilinear": Image.Resampling.BILINEAR, "nearest": Image.Resampling.NEAREST, "area": Image.Resampling.BOX}[method]
    valid = np.isfinite(source) & (source > 0)
    numerator = np.where(valid, source, 0.0).astype(np.float32)
    target_size = (shape[1], shape[0])
    resized_numerator = np.asarray(Image.fromarray(numerator, mode="F").resize(target_size, resampling), dtype=np.float32)
    resized_weight = np.asarray(Image.fromarray(valid.astype(np.float32), mode="F").resize(target_size, resampling), dtype=np.float32)
    output_valid = resized_weight > 1e-6
    output = np.zeros(shape, dtype=np.float32)
    output[output_valid] = resized_numerator[output_valid] / resized_weight[output_valid]
    return output, output_valid


def same_region_same_statistic(
    predicted_regions: dict[str, Any], gt_regions: dict[str, Any], region_method: str, statistic_method: str
) -> dict[str, Any]:
    predicted = predicted_regions.get(region_method, {}).get("statistics", {}).get(statistic_method)
    gt = gt_regions.get(region_method, {}).get("statistics", {}).get(statistic_method)
    base = {"region_method": region_method, "statistic_method": statistic_method}
    if not predicted or predicted.get("status") != "valid":
        return {**base, "status": "rejected", "predicted_surface_depth_m": None,
                "gt_surface_depth_m_same_region_same_statistic": None, "absolute_error_m": None,
                "gt_region_pixel_count": 0, "gt_valid_pixel_count": 0, "gt_status": "not_evaluated",
                "gt_rejection_reason": "predicted candidate rejected"}
    gt_region = gt_regions.get(region_method, {}).get("region", {})
    if not gt or gt.get("status") != "valid":
        reason = (gt or {}).get("message") or (gt or {}).get("rejection_reason") or "GT candidate rejected"
        return {**base, "status": "rejected", "predicted_surface_depth_m": predicted["surface_depth_m"],
                "gt_surface_depth_m_same_region_same_statistic": None, "absolute_error_m": None,
                "gt_region_pixel_count": int(gt_region.get("pixel_count", 0)),
                "gt_valid_pixel_count": int(gt_region.get("valid_pixel_count", 0)), "gt_status": "rejected",
                "gt_rejection_reason": reason}
    error = abs(float(predicted["surface_depth_m"]) - float(gt["surface_depth_m"]))
    return {**base, "status": "valid", "predicted_surface_depth_m": float(predicted["surface_depth_m"]),
            "gt_surface_depth_m_same_region_same_statistic": float(gt["surface_depth_m"]),
            "absolute_error_m": error, "gt_region_pixel_count": int(gt_region["pixel_count"]),
            "gt_valid_pixel_count": int(gt_region["valid_pixel_count"]), "gt_status": "valid",
            "gt_rejection_reason": None}


def frozen_candidate(regions: dict[str, Any], method: dict[str, str]) -> dict[str, Any]:
    region_method = method["region_method"]
    statistic_method = method["statistic_method"]
    candidate = regions.get(region_method, {}).get("statistics", {}).get(statistic_method)
    if not candidate or candidate.get("status") != "valid":
        return {"status": "rejected", "region_method": region_method, "statistic_method": statistic_method,
                "rejection_reason": (candidate or {}).get("message", "frozen method unavailable")}
    return {"region_method": region_method, "statistic_method": statistic_method, **candidate,
            "selection_source": "deltar_stage3_1_method_selection.v1"}


def frozen_canvas_delta(direct_regions: dict[str, Any], letterbox_regions: dict[str, Any], method: dict[str, str]) -> dict[str, Any]:
    direct = frozen_candidate(direct_regions, method)
    letterbox = frozen_candidate(letterbox_regions, method)
    if direct.get("status") != "valid" or letterbox.get("status") != "valid":
        return {"direct_candidate": direct, "letterbox_candidate": letterbox, "absolute_delta_m": None,
                "relative_delta": None, "agreement_status": "rejected", "rejection_reason": "frozen candidate unavailable on one or both canvases"}
    absolute = abs(direct["surface_depth_m"] - letterbox["surface_depth_m"])
    denominator = max(1e-9, (abs(direct["surface_depth_m"]) + abs(letterbox["surface_depth_m"])) / 2.0)
    relative = absolute / denominator
    status = "high" if relative <= 0.05 else ("moderate" if relative <= 0.15 else "low")
    return {"direct_candidate": direct, "letterbox_candidate": letterbox, "absolute_delta_m": float(absolute),
            "relative_delta": float(relative), "agreement_status": status, "rejection_reason": None}


def aggregate_method_rows(rows: list[dict[str, Any]], object_count: int) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["region_method"], row["statistic_method"])].append(row)
    output = []
    expected = object_count * 2
    for (region, statistic), group in groups.items():
        valid = [row for row in group if row["status"] == "valid"]
        errors = [row["absolute_error_m"] for row in valid]
        pixel_mae = [row["pixel_mae_m"] for row in valid if row.get("pixel_mae_m") is not None]
        small_errors = [row["absolute_error_m"] for row in valid if row.get("small_object")]
        multimodal_errors = [row["absolute_error_m"] for row in valid if row.get("multimodal")]
        by_object: dict[str, dict[str, float]] = defaultdict(dict)
        for row in valid:
            by_object[row["object_id"]][row["canvas_strategy"]] = row["predicted_surface_depth_m"]
        deltas = [abs(pair["direct_640x360"] - pair["letterbox_640x480"]) for pair in by_object.values()
                  if set(pair) == {"direct_640x360", "letterbox_640x480"}]
        coverage = len(valid) / expected if expected else 0.0
        entry = {"region_method": region, "statistic_method": statistic, "object_count": object_count,
                 "expected_canvas_cases": expected, "valid_count": len(valid), "rejected_count": expected - len(valid),
                 "coverage": coverage, "rejection_rate": 1.0 - coverage,
                 "median_gt_scalar_error_m": float(np.median(errors)) if errors else None,
                 "p90_gt_scalar_error_m": float(np.percentile(errors, 90)) if errors else None,
                 "p95_gt_scalar_error_m": float(np.percentile(errors, 95)) if errors else None,
                 "mean_pixel_mae_m": float(np.mean(pixel_mae)) if pixel_mae else None,
                 "p95_pixel_mae_m": float(np.percentile(pixel_mae, 95)) if pixel_mae else None,
                 "direct_letterbox_median_delta_m": float(np.median(deltas)) if deltas else None,
                 "direct_letterbox_p90_delta_m": float(np.percentile(deltas, 90)) if deltas else None,
                 "small_object_valid_count": sum(bool(row.get("small_object")) for row in valid),
                 "small_object_median_gt_error_m": float(np.median(small_errors)) if small_errors else None,
                 "multimodal_valid_count": sum(bool(row.get("multimodal")) for row in valid),
                 "multimodal_median_gt_error_m": float(np.median(multimodal_errors)) if multimodal_errors else None}
        if errors:
            entry["ranking_score_experimental"] = (
                2.0 * coverage - entry["median_gt_scalar_error_m"] - 0.5 * entry["p90_gt_scalar_error_m"]
                - 0.25 * entry["p95_gt_scalar_error_m"] - 0.25 * (entry["direct_letterbox_median_delta_m"] or 0.0)
                - 0.25 * (entry["small_object_median_gt_error_m"] or 0.0)
                - 0.25 * (entry["multimodal_median_gt_error_m"] or 0.0)
            )
        output.append(entry)
    return sorted(output, key=lambda item: item.get("ranking_score_experimental", -1e9), reverse=True)


def choose_frozen_method(matrix: list[dict[str, Any]]) -> dict[str, str]:
    eligible = [row for row in matrix if row.get("coverage", 0.0) >= 0.90 and row.get("ranking_score_experimental") is not None]
    if not eligible:
        eligible = [row for row in matrix if row.get("ranking_score_experimental") is not None]
    if not eligible:
        raise ValueError("no valid method can be frozen")
    winner = eligible[0]
    return {"region_method": winner["region_method"], "statistic_method": winner["statistic_method"]}
