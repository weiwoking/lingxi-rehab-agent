from __future__ import annotations

from typing import Any

import numpy as np

from .errors import ErrorCode, PipelineError


def summarize_geometry(point_cloud: dict[str, Any], calibration: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    if point_cloud.get("depth_unit") != "meter":
        raise PipelineError(ErrorCode.DEPTH_SCALE, "point cloud depth_unit must be meter", "point_cloud.depth_unit")
    pc = np.asarray(point_cloud["points_camera"], dtype=np.float64)
    pg = np.asarray(point_cloud["points_ground"], dtype=np.float64)
    if pc.shape[0] < int(profile["minimum_point_count"]):
        raise PipelineError(ErrorCode.GEOMETRY_TOO_FEW_POINTS, "too few points for geometry")
    radial = np.linalg.norm(pc, axis=1)
    median_xyz = np.median(pc, axis=0)
    floor_z = float(calibration["ground"]["plane_z_m"])
    z = pg[:, 2] - floor_z
    low_q = float(profile["bottom_quantile"])
    high_q = float(profile["top_quantile"])
    bottom, top = (float(x) for x in np.quantile(z, [low_q, high_q]))
    quantile_methods = {
        "min_max": [float(z.min()), float(z.max())],
        "p02_p98": [float(np.quantile(z, 0.02)), float(np.quantile(z, 0.98))],
        "p05_p95": [float(np.quantile(z, 0.05)), float(np.quantile(z, 0.95))],
        "p10_p90": [float(np.quantile(z, 0.10)), float(np.quantile(z, 0.90))],
    }
    zmed = float(np.median(z))
    zmad = float(np.median(np.abs(z - zmed)))
    zkeep = np.abs(z - zmed) <= max(3.0 * 1.4826 * zmad, 1e-6)
    if zkeep.sum() >= int(profile["minimum_point_count"]):
        quantile_methods["mad_then_p05_p95"] = [float(np.quantile(z[zkeep], 0.05)), float(np.quantile(z[zkeep], 0.95))]
    else:
        quantile_methods["mad_then_p05_p95"] = [None, None]
    height = top - bottom
    if not top > bottom:
        raise PipelineError(ErrorCode.GEOMETRY_TOP_BOTTOM, "top must be greater than bottom", details={"top": top, "bottom": bottom})
    if bottom < -float(profile["below_floor_tolerance_m"]):
        raise PipelineError(ErrorCode.GEOMETRY_BELOW_FLOOR, "object bottom is below floor tolerance", details=bottom)
    min_height, max_height = (float(x) for x in profile["object_height_range_m"])
    if not min_height <= height <= max_height:
        raise PipelineError(ErrorCode.GEOMETRY_TOP_BOTTOM, "object height outside profile range", details=height)
    band = max(1, int(np.ceil(pc.shape[0] * float(profile["vertical_support_fraction"]))))
    order = np.argsort(z)
    fm = point_cloud["filter_metrics"]
    quality = {
        "calibration_status": calibration["calibration_status"],
        "depth_semantics_status": calibration["depth"]["semantics_status"],
        "valid_depth_ratio": fm["valid_depth_ratio"],
        "point_count": int(pc.shape[0]),
        "point_retention_ratio": fm["point_retention_ratio"],
        "depth_mad": fm["depth_mad_m"],
        "depth_iqr": fm["depth_iqr_m"],
        "multimodal": fm["multimodal"],
        "top_support": band,
        "bottom_support": band,
        "quantile_stability_m": abs((quantile_methods["p02_p98"][1] - quantile_methods["p02_p98"][0]) - (quantile_methods["p05_p95"][1] - quantile_methods["p05_p95"][0])),
        "touches_image_border": fm["touches_image_border"],
    }
    return {
        "distance_m_definition": "robust_3d_centroid_distance",
        "distance_m": float(np.linalg.norm(median_xyz)),
        "distance_axial_m": float(np.median(pc[:, 2])),
        "distance_radial_m": float(np.median(radial)),
        "distance_centroid_m": float(np.linalg.norm(median_xyz)),
        "nearest_stable_surface_m": float(np.median(np.sort(radial)[:max(band, int(0.1 * pc.shape[0]))])),
        "top_height_m": top,
        "bottom_height_m": bottom,
        "object_height_m": height,
        "point_count": int(pc.shape[0]),
        "top_support_median_m": float(np.median(z[order[-band:]])),
        "bottom_support_median_m": float(np.median(z[order[:band]])),
        "vertical_estimators": quantile_methods,
        "quality_inputs": quality,
        "warnings": list(point_cloud["warnings"]),
        # Canvas agreement and semantics proof are not known at this layer.
        "automatic_geometry_valid": False,
        "absolute_geometry_valid": False,
    }


def compare_canvases(direct: dict[str, Any] | None, letterbox: dict[str, Any] | None, profile: dict[str, Any]) -> dict[str, Any]:
    if direct is None or letterbox is None:
        return {"agreement_status": "rejected_candidate", "distance_delta_m": None, "height_delta_m": None, "bottom_height_delta_m": None, "point_count_delta": None}
    dd = abs(direct["distance_m"] - letterbox["distance_m"])
    hd = abs(direct["object_height_m"] - letterbox["object_height_m"])
    bd = abs(direct["bottom_height_m"] - letterbox["bottom_height_m"])
    agreement = "agree" if dd <= profile["canvas_distance_tolerance_m"] and hd <= profile["canvas_height_tolerance_m"] and bd <= profile["canvas_bottom_tolerance_m"] else "disagree"
    return {"distance_delta_m": dd, "height_delta_m": hd, "bottom_height_delta_m": bd, "point_count_delta": direct["point_count"] - letterbox["point_count"], "agreement_status": agreement}
