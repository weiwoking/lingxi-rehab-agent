from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from deltar_p4_pipeline.geometry_v2.shape_router import route_shape
from deltar_p4_pipeline.geometry_v2.contact_evidence import (
    evaluate_contact,
    prepare_contact_evidence,
)
from deltar_p4_pipeline.geometry_v2.support_selector import SupportUnavailable, select_support
from deltar_p4_pipeline.geometry_v2.surface_identity import associate_same_surfaces


ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads(
    (ROOT / "profiles/formal_geometry_v2/all_object_geometry_v2.json").read_text(encoding="utf-8")
)


def _surface(surface_id: str, height: float, *, pixels: int = 100) -> dict:
    mask = np.zeros((40, 60), dtype=bool)
    mask[25:30, 10:50] = True
    return {
        "surface_id": surface_id,
        "origin": "environment_component",
        "height_mm": height,
        "height_mad_mm": 2.0,
        "pixel_count": pixels,
        "median_axial_mm": 1800.0,
        "_mask": mask,
    }


def _evidence(*, contact: float = 0.8, footprint: float = 0.7, residual: float = 0.0, height: float = 700.0) -> dict:
    return {
        "line_like": False,
        "direct_contact": True,
        "contact_coverage": contact,
        "footprint_coverage": footprint,
        "minimum_distance_px": 0.0,
        "p25_distance_px": 1.0,
        "contact_limit_px": 10.0,
        "interior_axial_residual_mm": residual,
        "local_axial_residual_mm": 0.0,
        "occluded_contact_projection": {"height_mm": height, "mad_mm": 2.0},
    }


def test_config_contains_no_weights_truth_fit_or_class_compensation() -> None:
    encoded = json.dumps(CONFIG, sort_keys=True).lower()
    for forbidden in ("weight", 'ground_truth_used_for_fit": true'):
        assert forbidden not in encoded
    assert CONFIG["class_specific_fixed_compensation"] is False
    assert CONFIG["scene_target_name_special_cases"] is False


def test_selector_uses_physical_contact_not_high_or_low_height_preference() -> None:
    cfg = CONFIG["generic_support"]
    lower = _surface("lower", 500.0, pixels=120)
    upper = _surface("upper", 900.0, pixels=90)
    result = select_support(
        [lower, upper],
        [_evidence(contact=0.92, footprint=0.8, height=500.0), _evidence(contact=0.45, footprint=0.5, height=900.0)],
        cfg,
    )
    assert result["support_mm"] == 500.0
    assert result["diagnostics"]["selection_policy"] == "lexicographic_direct_physical_contact_v1"

    result = select_support(
        [lower, upper],
        [_evidence(contact=0.45, footprint=0.5, height=500.0), _evidence(contact=0.92, footprint=0.8, height=900.0)],
        cfg,
    )
    assert result["support_mm"] == 900.0


def test_line_like_surface_is_low_confidence_best_effort_support() -> None:
    cfg = CONFIG["generic_support"]
    evidence = _evidence()
    evidence["line_like"] = True
    result = select_support([_surface("rail", 820.0)], [evidence], cfg)
    assert result["support_mm"] == 820.0
    assert result["quality_status"] == "degraded"
    assert result["confidence"] <= 0.15
    assert result["degradation_reason"] == "ambiguous_support:no_direct_physical_candidate"


def test_indistinguishable_separated_surfaces_return_degraded_numeric() -> None:
    cfg = CONFIG["generic_support"]
    result = select_support(
        [_surface("a", 500.0), _surface("b", 800.0)],
        [_evidence(contact=0.80, height=500.0), _evidence(contact=0.79, height=800.0)],
        cfg,
    )
    assert result["support_mm"] == 500.0
    assert result["quality_status"] == "degraded"
    assert result["degradation_reason"] == "ambiguous_support:indistinguishable_physical_surfaces"


def test_occluded_environment_contact_is_explicit_and_not_shape_derived() -> None:
    cfg = CONFIG["generic_support"]
    evidence = _evidence(residual=300.0)
    evidence["direct_contact"] = False
    evidence["footprint_coverage"] = 0.02
    evidence["occluded_contact_projection"] = {"height_mm": 620.0, "mad_mm": 3.0}
    result = select_support([_surface("rear", 700.0)], [evidence], cfg)
    assert result["support_mm"] == 620.0
    assert result["source"] == "occluded_environment_contact_surface"
    assert result["confidence"] < 0.5


def test_same_surface_association_never_mutates_support_height() -> None:
    mask = np.zeros((30, 30), dtype=bool)
    mask[20:24, 4:26] = True
    records = []
    for value in (700.0, 724.0):
        records.append({
            "generic_support": {
                "support_surface_id": "physical_surface_a",
                "_surface_component_mask": mask.copy(),
                "_selected_median_axial_mm": 1800.0,
            },
            "final": {"support_mm": value},
        })
    before = [item["final"]["support_mm"] for item in records]
    associate_same_surfaces(records, CONFIG["same_surface"], "frame_000000")
    assert [item["final"]["support_mm"] for item in records] == before
    assert all(item["consensus_used"] is False for item in records)
    assert all(item["same_surface_audit"]["policy"] == "identity_only_no_height_mutation" for item in records)


@pytest.mark.parametrize(
    ("class_name", "family"),
    [
        ("bottle", "upright_container"),
        ("cup", "upright_container"),
        ("ball", "sports_ball"),
        ("sports ball", "sports_ball"),
        ("sports_ball", "sports_ball"),
        ("person", "generic"),
        ("chair", "generic"),
    ],
)
def test_shape_router_is_narrow_and_class_independent_for_support(class_name: str, family: str) -> None:
    routed = route_shape(class_name, None, [10, 10, 40, 80], 0.8, 20.0, {})
    assert routed["selected_family"] == family
    assert "support_mm" not in routed


def test_specialized_modules_declare_read_only_support_authority() -> None:
    upright = (ROOT / "src/deltar_p4_pipeline/geometry_v2/upright_container.py").read_text(encoding="utf-8")
    ball = (ROOT / "src/deltar_p4_pipeline/geometry_v2/ball_extent.py").read_text(encoding="utf-8")
    assert "read_only_environment_support" in upright
    assert "read_only_environment_support" in ball
    assert "support_mm =" not in upright
    assert "support_mm =" not in ball


def test_prepared_contact_evidence_is_exactly_reusable_across_surfaces() -> None:
    shape = (40, 60)
    region = np.zeros(shape, dtype=bool)
    region[10:25, 20:35] = True
    selected_valid = region.copy()
    axial = np.full(shape, 1800.0, dtype=np.float64)
    world = np.zeros((*shape, 3), dtype=np.float64)
    world[..., 2] = 700.0
    surface_mask = np.zeros(shape, dtype=bool)
    surface_mask[25:30, 10:50] = True
    surface = {
        "surface_id": "surface-a",
        "origin": "local_height_component",
        "height_mm": 700.0,
        "height_mad_mm": 2.0,
        "median_axial_mm": 1800.0,
        "pixel_count": int(np.count_nonzero(surface_mask)),
        "bbox_xywh": [10, 25, 40, 5],
        "width_px": 40,
        "height_px": 5,
        "span_px": 40,
        "surface_thickness_px": 5.0,
        "_mask": surface_mask,
    }
    bbox = [20.0, 10.0, 35.0, 25.0]
    cfg = CONFIG["generic_support"]
    direct = evaluate_contact(
        surface, region, bbox, axial, world, selected_valid, None, cfg
    )
    prepared = prepare_contact_evidence(
        region,
        selected_valid,
        axial,
        world,
        None,
        int(cfg["minimum_projection_anchors"]),
    )
    reused = evaluate_contact(
        surface,
        region,
        bbox,
        axial,
        world,
        selected_valid,
        None,
        cfg,
        prepared=prepared,
    )
    assert reused == direct
