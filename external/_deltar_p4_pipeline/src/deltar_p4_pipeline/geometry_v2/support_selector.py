"""Short lexicographic policy for the sole environment support authority."""
from __future__ import annotations

from typing import Any


class SupportUnavailable(ValueError):
    def __init__(self, reason: str, diagnostics: dict[str, Any]):
        super().__init__(reason)
        self.reason = reason
        self.diagnostics = diagnostics


def _eligible(surface: dict[str, Any], evidence: dict[str, Any], cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    residual = evidence["interior_axial_residual_mm"]
    projected = evidence.get("occluded_contact_projection")
    projection_stable = bool(
        isinstance(projected, dict)
        and projected["mad_mm"] <= float(cfg["maximum_projection_mad_mm"])
    )
    local_residual = evidence.get("local_axial_residual_mm")
    projected_contact = bool(
        projection_stable
        and evidence["contact_coverage"] >= float(cfg["minimum_direct_contact_coverage"])
        and evidence["p25_distance_px"] <= evidence["contact_limit_px"]
        and local_residual is not None
        and abs(float(local_residual)) <= float(cfg["maximum_direct_axial_residual_mm"])
    )
    accepted_plane = str(surface["origin"]).startswith("accepted_plane:")
    accepted_plane_contact = bool(
        accepted_plane
        and evidence["contact_coverage"] >= 0.50
        and evidence["minimum_distance_px"] <= evidence["contact_limit_px"]
        and evidence["footprint_coverage"] >= 0.10
    )
    if evidence["line_like"]:
        reasons.append("thin_line_or_front_edge")
    if not evidence["direct_contact"] and not accepted_plane_contact and not projected_contact:
        reasons.append("no_direct_footprint_contact")
    if residual is None:
        reasons.append("no_object_surface_depth_relationship")
    elif residual < -float(cfg["maximum_front_axial_offset_mm"]):
        reasons.append("foreground_occluder")
    if reasons:
        return None, reasons

    projection_disagreement = (
        None if not projection_stable
        else abs(float(projected["height_mm"]) - float(surface["height_mm"]))
    )
    if accepted_plane and abs(float(residual)) <= float(cfg["maximum_projected_rear_axial_mm"]):
        return {
            "support_mm": float(surface["height_mm"]),
            "method": "direct_observed_environment_plane",
            "evidence_tier": 3,
            "uncertainty_mm": max(float(surface["height_mad_mm"]), 1.0),
        }, reasons
    if evidence["direct_contact"] and abs(float(residual)) <= float(cfg["maximum_direct_axial_residual_mm"]):
        return {
            "support_mm": float(surface["height_mm"]),
            "method": "direct_observed_environment_surface",
            "evidence_tier": 2,
            "uncertainty_mm": max(float(surface["height_mad_mm"]), 1.0),
        }, reasons

    if (
        projection_stable
        and residual <= float(cfg["maximum_projected_rear_axial_mm"])
        and projection_disagreement is not None
        and projection_disagreement > float(cfg["maximum_surface_projection_disagreement_mm"])
    ):
        return {
            "support_mm": float(projected["height_mm"]),
            "method": "occluded_environment_contact_surface",
            "evidence_tier": 1,
            "uncertainty_mm": max(float(projected["mad_mm"]), float(surface["height_mad_mm"])),
        }, reasons
    if residual > 0.0 and residual <= float(cfg["maximum_projected_rear_axial_mm"]):
        if not isinstance(projected, dict):
            reasons.append("occluded_contact_projection_unavailable")
        elif projected["mad_mm"] > float(cfg["maximum_projection_mad_mm"]):
            reasons.append("occluded_contact_projection_unstable")
        elif projection_stable:
            return {
                "support_mm": float(projected["height_mm"]),
                "method": "occluded_environment_contact_surface",
                "evidence_tier": 1,
                "uncertainty_mm": max(float(projected["mad_mm"]), float(surface["height_mad_mm"])),
            }, reasons
    else:
        reasons.append("rear_background_depth_relationship")
    return None, reasons


def select_support(observations: list[dict[str, Any]], evidence_rows: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for surface, evidence in zip(observations, evidence_rows):
        estimate, reasons = _eligible(surface, evidence, cfg)
        public_surface = {key: value for key, value in surface.items() if not key.startswith("_")}
        public = {"surface": public_surface, "contact": evidence, "rejection_reasons": reasons}
        if estimate is None:
            rejected.append(public)
            continue
        candidates.append({**public, **estimate, "_surface": surface})

    # Authority is lexicographic: observation tier, contact coverage, footprint
    # coverage, proximity, then population. Height is never a preference.
    candidates.sort(key=lambda item: (
        -item["evidence_tier"],
        -item["contact"]["contact_coverage"],
        -item["contact"]["footprint_coverage"],
        item["contact"]["p25_distance_px"],
        -item["surface"]["pixel_count"],
        item["surface"]["surface_id"],
    ))
    diagnostics = {
        "selection_policy": "lexicographic_direct_physical_contact_v1",
        "eligible_candidates": [{key: value for key, value in item.items() if not key.startswith("_")} for item in candidates],
        "rejected_candidates": rejected[:int(cfg["maximum_diagnostic_candidates"])],
    }
    degradation_reason: str | None = None
    if not candidates:
        if not observations:
            raise SupportUnavailable("no_metric_support_candidate", diagnostics)
        # Numeric-first fallback: the environment extractor has already
        # produced finite metric surfaces.  Preserve the same generic,
        # class-blind contact ordering and publish the best available surface
        # at very low confidence instead of turning an estimate into null.
        fallback_candidates: list[dict[str, Any]] = []
        for surface, evidence in zip(observations, evidence_rows):
            fallback_candidates.append({
                "support_mm": float(surface["height_mm"]),
                "method": "best_effort_environment_surface",
                "evidence_tier": 0,
                "uncertainty_mm": max(float(surface["height_mad_mm"]), 1.0),
                "surface": {key: value for key, value in surface.items() if not key.startswith("_")},
                "contact": evidence,
                "rejection_reasons": next(
                    (
                        item["rejection_reasons"]
                        for item in rejected
                        if item["surface"]["surface_id"] == surface["surface_id"]
                    ),
                    [],
                ),
                "_surface": surface,
            })
        fallback_candidates.sort(key=lambda item: (
            bool(item["contact"]["line_like"]),
            not bool(item["contact"]["direct_contact"]),
            -item["contact"]["contact_coverage"],
            -item["contact"]["footprint_coverage"],
            item["contact"]["p25_distance_px"],
            -item["surface"]["pixel_count"],
            item["surface"]["surface_id"],
        ))
        candidates = fallback_candidates
        diagnostics["best_effort_candidates"] = [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in fallback_candidates[:int(cfg["maximum_diagnostic_candidates"])]
        ]
        degradation_reason = "ambiguous_support:no_direct_physical_candidate"
    chosen = candidates[0]
    if len(candidates) > 1:
        other = candidates[1]
        near_tie = (
            chosen["evidence_tier"] == other["evidence_tier"]
            and abs(chosen["contact"]["contact_coverage"] - other["contact"]["contact_coverage"]) < float(cfg["ambiguity_contact_margin"])
            and abs(chosen["contact"]["footprint_coverage"] - other["contact"]["footprint_coverage"]) < float(cfg["ambiguity_footprint_margin"])
        )
        separated = abs(chosen["support_mm"] - other["support_mm"]) > max(chosen["uncertainty_mm"], other["uncertainty_mm"], 20.0)
        if near_tie and separated:
            degradation_reason = "ambiguous_support:indistinguishable_physical_surfaces"
    confidence = 0.42
    confidence += 0.20 * min(1.0, chosen["contact"]["contact_coverage"])
    confidence += 0.18 * min(1.0, chosen["contact"]["footprint_coverage"])
    confidence += 0.10 if chosen["evidence_tier"] >= 2 else 0.0
    confidence -= min(0.15, chosen["uncertainty_mm"] / 200.0)
    # Tier-1 projection depends on an occluded foreground-depth proxy.  This
    # is useful evidence, but transparent/reflective objects can contaminate
    # it; it must never masquerade as a normal-confidence direct observation.
    if chosen["evidence_tier"] == 1:
        confidence = min(confidence, 0.499999)
        degradation_reason = degradation_reason or "weak_projected_support_evidence"
    if chosen["evidence_tier"] == 0:
        confidence = min(confidence, 0.15)
    if degradation_reason and chosen["evidence_tier"] > 0:
        confidence = min(confidence, 0.20)
    surface = chosen["_surface"]
    diagnostics["selected_surface_id"] = surface["surface_id"]
    diagnostics["selected_method"] = chosen["method"]
    return {
        "support_mm": float(chosen["support_mm"]),
        "source": chosen["method"],
        "confidence": float(max(0.0, min(0.95, confidence))),
        "numeric_available": True,
        "quality_status": "degraded" if degradation_reason else "ok",
        "degradation_reason": degradation_reason,
        "fallback_reason": degradation_reason,
        "surface_spread_mm": float(chosen["uncertainty_mm"]),
        "support_surface_id": surface["surface_id"],
        "support_component": {key: value for key, value in surface.items() if not key.startswith("_")},
        "diagnostics": diagnostics,
        "_surface_component_mask": surface["_mask"],
        "_selected_median_axial_mm": float(surface["median_axial_mm"]),
    }
