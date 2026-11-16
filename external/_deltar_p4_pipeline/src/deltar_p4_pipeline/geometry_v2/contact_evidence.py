"""Independent physical contact evidence for object/environment pairs."""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy.ndimage import distance_transform_edt

from .environment_surfaces import bottom_contour


def _contact_field(region: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    contour = bottom_contour(region)
    contour_mask = np.zeros(region.shape, dtype=bool)
    xy = np.rint(contour).astype(np.int32)
    if xy.size:
        valid = ((xy[:, 0] >= 0) & (xy[:, 0] < region.shape[1])
                 & (xy[:, 1] >= 0) & (xy[:, 1] < region.shape[0]))
        xy = xy[valid]
        contour_mask[xy[:, 1], xy[:, 0]] = True
    distance, nearest = distance_transform_edt(~contour_mask, return_indices=True)
    return distance, nearest[0], nearest[1], int(xy.shape[0])


def _project_occluded_contact(
    region: np.ndarray,
    selected_valid: np.ndarray,
    axial: np.ndarray,
    world: np.ndarray,
    gravity: dict[str, Any] | None,
    minimum_anchors: int,
) -> dict[str, Any] | None:
    if not isinstance(gravity, dict) or world.ndim != 3:
        return None
    up = np.asarray((gravity.get("gravity_authority") or {}).get("u_up_camera"), dtype=np.float64)
    if up.shape != (3,) or not np.all(np.isfinite(up)):
        return None
    contour = bottom_contour(region)
    if contour.shape[0] < 5:
        return None
    low, high = np.quantile(contour[:, 0], [0.15, 0.85])
    xy = np.rint(contour[(contour[:, 0] >= low) & (contour[:, 0] <= high)]).astype(np.int32)
    valid_xy = ((xy[:, 0] >= 0) & (xy[:, 0] < axial.shape[1])
                & (xy[:, 1] >= 0) & (xy[:, 1] < axial.shape[0]))
    xy = xy[valid_xy]
    object_depth = axial[np.asarray(selected_valid, dtype=bool) & np.isfinite(axial) & (axial > 0.0)]
    # A fixed 24-anchor request is impossible after edge trimming for a real
    # 20-30 px cup/bottle silhouette. Require all available trimmed anchors,
    # with a five-ray geometric floor, instead of rejecting narrow objects by
    # construction.
    required_anchors = min(minimum_anchors, max(5, int(xy.shape[0])))
    if xy.shape[0] < required_anchors or object_depth.size < required_anchors:
        return None
    foreground_axial = float(np.percentile(object_depth, 10.0))
    observed = axial[xy[:, 1], xy[:, 0]]
    wx = world[xy[:, 1], xy[:, 0], 0]
    wy = world[xy[:, 1], xy[:, 0], 1]
    good = np.isfinite(observed) & (observed > 0.0) & np.isfinite(wx) & np.isfinite(wy)
    if np.count_nonzero(good) < required_anchors:
        return None
    rays = np.column_stack((wx[good] / observed[good], wy[good] / observed[good], np.ones(np.count_nonzero(good))))
    values = float(gravity["camera_height_mm"]) + foreground_axial * (rays @ up)
    values = values[np.isfinite(values)]
    if values.size < required_anchors:
        return None
    height = float(np.median(values))
    return {
        "height_mm": height,
        "mad_mm": float(np.median(np.abs(values - height))),
        "anchor_count": int(values.size),
        "foreground_axial_mm": foreground_axial,
    }


def prepare_contact_evidence(
    region: np.ndarray,
    selected_valid: np.ndarray,
    axial_mm: np.ndarray,
    world_points_mm: np.ndarray,
    gravity: dict[str, Any] | None,
    minimum_anchors: int,
) -> dict[str, Any]:
    """Compute the object-invariant contact inputs once per logical object."""
    source = np.asarray(region, dtype=bool)
    axial = np.asarray(axial_mm)
    world = np.asarray(world_points_mm)
    return {
        "contact_field": _contact_field(source),
        "occluded_contact_projection": _project_occluded_contact(
            source,
            np.asarray(selected_valid, dtype=bool),
            axial,
            world,
            gravity,
            minimum_anchors,
        ),
    }


def evaluate_contact(
    surface: dict[str, Any],
    region: np.ndarray,
    bbox: list[float],
    axial_mm: np.ndarray,
    world_points_mm: np.ndarray,
    selected_valid: np.ndarray,
    gravity: dict[str, Any] | None,
    cfg: dict[str, Any],
    *,
    prepared: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mask = np.asarray(surface["_mask"], dtype=bool)
    ys, xs = np.nonzero(mask)
    if prepared is None:
        prepared = prepare_contact_evidence(
            region,
            selected_valid,
            axial_mm,
            world_points_mm,
            gravity,
            int(cfg["minimum_projection_anchors"]),
        )
    distance, nearest_y, nearest_x, contour_count = prepared["contact_field"]
    contact_limit = min(
        float(cfg["maximum_contact_distance_px"]),
        max(4.0, (float(bbox[2]) - float(bbox[0])) * float(cfg["contact_distance_bbox_fraction"])),
    )
    sample = max(1, int(np.ceil(xs.size / 256.0)))
    px, py = xs[::sample], ys[::sample]
    distances = distance[py, px]
    near = distances <= contact_limit
    nearest_flat = nearest_y[py[near]] * mask.shape[1] + nearest_x[py[near]]
    coverage = float(np.unique(nearest_flat).size / contour_count) if contour_count and np.any(near) else 0.0
    local_residual = None
    if np.any(near):
        component_axial = axial_mm[py[near], px[near]]
        object_axial = axial_mm[
            nearest_y[py[near], px[near]],
            nearest_x[py[near], px[near]],
        ]
        good = np.isfinite(component_axial) & (component_axial > 0.0) & np.isfinite(object_axial) & (object_axial > 0.0)
        if np.any(good):
            local_residual = float(np.median(component_axial[good] - object_axial[good]))
    selected_axial = axial_mm[np.asarray(selected_valid, dtype=bool) & np.isfinite(axial_mm) & (axial_mm > 0.0)]
    interior_residual = None if not selected_axial.size else float(surface["median_axial_mm"] - np.median(selected_axial))
    under = (xs >= float(bbox[0])) & (xs <= float(bbox[2]))
    bbox_width = max(1.0, float(bbox[2]) - float(bbox[0]))
    footprint_coverage = float(np.unique(xs[under]).size / bbox_width) if np.any(under) else 0.0
    footprint_fraction = float(np.count_nonzero(under) / max(1, xs.size))
    line_like = bool(
        surface["width_px"] >= float(cfg["line_like_aspect_ratio"]) * max(1, surface["height_px"])
        and surface["surface_thickness_px"] < float(cfg["minimum_surface_thickness_px"])
    )
    direct_contact = bool(
        coverage >= float(cfg["minimum_direct_contact_coverage"])
        and float(np.percentile(distances, 25)) <= contact_limit
        and footprint_coverage >= float(cfg["minimum_footprint_coverage"])
    )
    projected = prepared["occluded_contact_projection"]
    return {
        "direct_contact": direct_contact,
        "contact_limit_px": contact_limit,
        "minimum_distance_px": float(np.min(distances)),
        "p25_distance_px": float(np.percentile(distances, 25)),
        "contact_coverage": coverage,
        "footprint_coverage": min(1.0, footprint_coverage),
        "footprint_fraction": footprint_fraction,
        "local_axial_residual_mm": local_residual,
        "interior_axial_residual_mm": interior_residual,
        "line_like": line_like,
        "occluded_contact_projection": projected,
    }
