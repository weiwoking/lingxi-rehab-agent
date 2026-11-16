"""Class-blind extraction of immutable environment surface observations."""
from __future__ import annotations

import hashlib
import math
from typing import Any

import cv2
import numpy as np


def bbox_mask(bbox: list[float], shape: tuple[int, int]) -> np.ndarray:
    x1, y1, x2, y2 = (float(value) for value in bbox)
    result = np.zeros(shape, dtype=bool)
    ix1, iy1 = max(0, int(math.floor(x1))), max(0, int(math.floor(y1)))
    ix2, iy2 = min(shape[1], int(math.ceil(x2))), min(shape[0], int(math.ceil(y2)))
    if ix2 > ix1 and iy2 > iy1:
        result[iy1:iy2, ix1:ix2] = True
    return result


def bottom_contour(region: np.ndarray) -> np.ndarray:
    """Return a locally filtered bottom silhouette as x/y image coordinates."""
    source = np.asarray(region, dtype=bool)
    xs = np.flatnonzero(np.any(source, axis=0))
    if not xs.size:
        return np.empty((0, 2), dtype=np.float64)
    ys = np.asarray([np.flatnonzero(source[:, x])[-1] for x in xs], dtype=np.float64)
    keep = np.ones(xs.size, dtype=bool)
    for index in range(xs.size):
        local = ys[max(0, index - 2):min(xs.size, index + 3)]
        if local.size >= 3:
            keep[index] = abs(ys[index] - np.median(local)) <= 3.0
    return np.column_stack((xs[keep], ys[keep]))


def contact_context(region: np.ndarray, bbox: list[float], cfg: dict[str, Any]) -> np.ndarray:
    """Environment pixels near the footprint, excluding the object itself."""
    contour = bottom_contour(region)
    result = np.zeros(region.shape, dtype=bool)
    down = int(cfg["context_down_px"])
    side = int(cfg["context_side_px"])
    above = int(cfg["context_above_px"])
    for x_value, y_value in contour:
        x, y = int(round(x_value)), int(round(y_value))
        result[max(0, y - above):min(result.shape[0], y + down + 1),
               max(0, x - 2):min(result.shape[1], x + 3)] = True
    if contour.size:
        x1, _, x2, y2 = (float(value) for value in bbox)
        result[max(0, int(y2) - above):min(result.shape[0], int(y2) + down + 1),
               max(0, int(x1) - side):min(result.shape[1], int(x2) + side + 1)] = True
    excluded = cv2.dilate(np.asarray(region, dtype=np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    return result & ~excluded


def _surface_id(mask: np.ndarray, height_mm: float) -> str:
    digest = hashlib.sha256()
    digest.update(np.packbits(mask, bitorder="little").tobytes())
    digest.update(f"{height_mm:.3f}".encode("ascii"))
    return f"environment_component:{digest.hexdigest()[:16]}"


def _describe(component: np.ndarray, world_z: np.ndarray, axial: np.ndarray, origin: str) -> dict[str, Any]:
    ys, xs = np.nonzero(component)
    heights = np.asarray(world_z[ys, xs], dtype=np.float64)
    height = float(np.median(heights))
    mad = float(np.median(np.abs(heights - height)))
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    width, rows = x1 - x0 + 1, y1 - y0 + 1
    return {
        "surface_id": _surface_id(component, height),
        "height_mm": height,
        "height_mad_mm": mad,
        "median_axial_mm": float(np.median(axial[ys, xs])),
        "pixel_count": int(xs.size),
        "bbox_xywh": [x0, y0, width, rows],
        "width_px": width,
        "height_px": rows,
        "span_px": max(width, rows),
        "surface_thickness_px": float(xs.size / max(width, 1)),
        "origin": origin,
        "_mask": component,
    }


def _is_duplicate(candidate: dict[str, Any], accepted: list[dict[str, Any]]) -> bool:
    for old in accepted:
        if abs(float(candidate["height_mm"]) - float(old["height_mm"])) > 8.0:
            continue
        overlap = int(np.count_nonzero(candidate["_mask"] & old["_mask"]))
        if overlap / max(1, min(candidate["pixel_count"], old["pixel_count"])) >= 0.70:
            return True
    return False


def extract_environment_surfaces(
    region: np.ndarray,
    bbox: list[float],
    axial_mm: np.ndarray,
    world_z_mm: np.ndarray,
    support_planes: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract local iso-height components without selecting or scoring them."""
    axial = np.asarray(axial_mm, dtype=np.float64)
    world_z = np.asarray(world_z_mm, dtype=np.float64)
    finite = np.isfinite(world_z) & np.isfinite(axial) & (axial > 0.0)
    context = contact_context(region, bbox, cfg) & finite
    minimum_pixels = int(cfg["minimum_component_pixels"])
    minimum_span = int(cfg["minimum_component_span_px"])
    maximum_mad = float(cfg["maximum_component_z_mad_mm"])
    observations: list[dict[str, Any]] = []

    for plane in support_planes:
        mask = plane.get("_mask")
        if plane.get("label") != "support_surface" or not isinstance(mask, np.ndarray):
            continue
        local = np.asarray(mask, dtype=bool) & context & finite
        count, labels, stats, _ = cv2.connectedComponentsWithStats(local.astype(np.uint8), 8)
        for label in range(1, count):
            if int(stats[label, cv2.CC_STAT_AREA]) < minimum_pixels:
                continue
            item = _describe(labels == label, world_z, axial, f"accepted_plane:{plane.get('plane_id', 'unknown')}")
            # The accepted Stage3A plane fit is the immutable surface
            # observation; a small local crop must not re-estimate its height.
            item["height_mm"] = float(plane["median_height_mm"])
            item["height_mad_mm"] = float(plane.get("residual_rms_mm", item["height_mad_mm"]))
            item["surface_id"] = f"accepted_environment_plane:{plane.get('plane_id', 'unknown')}"
            if item["span_px"] >= minimum_span and item["height_mad_mm"] <= maximum_mad:
                observations.append(item)

    values = world_z[context]
    if values.size >= minimum_pixels:
        width = float(cfg["z_histogram_bin_mm"])
        low, high = (float(value) for value in np.percentile(values, [1.0, 99.0]))
        edges = np.arange(math.floor(low / width) * width, high + 2.0 * width, width)
        counts, edges = np.histogram(values, bins=edges)
        for bin_index in np.flatnonzero(counts >= minimum_pixels):
            centre = 0.5 * float(edges[bin_index] + edges[bin_index + 1])
            band = context & (np.abs(world_z - centre) <= float(cfg["z_cluster_half_width_mm"]))
            count, labels, stats, _ = cv2.connectedComponentsWithStats(band.astype(np.uint8), 8)
            for label in range(1, count):
                pixels = int(stats[label, cv2.CC_STAT_AREA])
                span = int(max(stats[label, cv2.CC_STAT_WIDTH], stats[label, cv2.CC_STAT_HEIGHT]))
                if pixels < minimum_pixels or span < minimum_span:
                    continue
                item = _describe(labels == label, world_z, axial, "local_height_component")
                if item["height_mad_mm"] <= maximum_mad and not _is_duplicate(item, observations):
                    observations.append(item)

    observations.sort(key=lambda item: (item["surface_id"], item["origin"]))
    return observations, {
        "context_pixel_count": int(np.count_nonzero(context)),
        "observed_surface_count": len(observations),
        "accepted_plane_count": sum(item["origin"].startswith("accepted_plane:") for item in observations),
    }
