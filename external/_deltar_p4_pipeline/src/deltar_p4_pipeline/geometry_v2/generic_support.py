"""Public façade for the sole class-independent environment support authority."""
from __future__ import annotations

from typing import Any

import numpy as np

from .contact_evidence import evaluate_contact, prepare_contact_evidence
from .environment_surfaces import bbox_mask, bottom_contour, extract_environment_surfaces
from .multimodal_support import build_object_distribution
from .support_selector import SupportUnavailable, select_support
from .surface_identity import associate_same_surfaces


def estimate_support(
    region: np.ndarray,
    bbox: list[float],
    axial_mm: np.ndarray,
    world_points_mm: np.ndarray,
    selected_valid: np.ndarray,
    support_planes: list[dict[str, Any]],
    cfg: dict[str, Any],
    gravity: dict[str, Any] | None = None,
    *,
    object_id: str = "",
    class_name: str = "unknown",
) -> dict[str, Any]:
    world = np.asarray(world_points_mm)
    world_z = np.asarray(world[..., 2] if world.ndim == 3 else world, dtype=np.float64)
    observations, extraction = extract_environment_surfaces(
        np.asarray(region, dtype=bool), bbox, axial_mm, world_z, support_planes, cfg
    )
    if observations:
        prepared_contact = prepare_contact_evidence(
            np.asarray(region, dtype=bool),
            np.asarray(selected_valid, dtype=bool),
            np.asarray(axial_mm),
            world,
            gravity,
            int(cfg["minimum_projection_anchors"]),
        )
        evidence = [
            evaluate_contact(
                surface, np.asarray(region, dtype=bool), bbox, np.asarray(axial_mm), world,
                np.asarray(selected_valid, dtype=bool), gravity, cfg,
                prepared=prepared_contact,
            )
            for surface in observations
        ]
    else:
        evidence = []
    try:
        result = select_support(observations, evidence, cfg)
    except SupportUnavailable as exc:
        exc.diagnostics["surface_extraction"] = extraction
        raise
    result["diagnostics"]["surface_extraction"] = extraction
    result["_m3_object_distribution"] = build_object_distribution(
        observations, evidence, cfg, object_id, class_name
    )
    return result


def group_same_surfaces(objects: list[dict[str, Any]], cfg: dict[str, Any], frame_id: str) -> None:
    associate_same_surfaces(objects, cfg, frame_id)


__all__ = [
    "SupportUnavailable", "bbox_mask", "bottom_contour", "estimate_support", "group_same_surfaces"
]
