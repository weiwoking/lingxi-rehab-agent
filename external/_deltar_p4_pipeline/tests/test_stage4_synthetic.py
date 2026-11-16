import json
from pathlib import Path

import pytest

from scripts.run_stage4_synthetic_matrix import run

ROOT = Path(__file__).resolve().parents[1]


def test_ten_analytic_scenes_pass():
    result = run()
    assert result["summary"]["scene_count"] == result["summary"]["passed"] == 10
    # The worst case deliberately erodes three source pixels; its residual is pixel-scale.
    assert result["summary"]["max_height_absolute_error_m"] < 0.04
    assert result["summary"]["max_bottom_height_absolute_error_m"] < 0.03


def test_direct_letterbox_equivalence_is_exact():
    result = json.loads((ROOT / "outputs_stage4" / "synthetic_matrix.json").read_text())
    row = next(s for s in result["scenes"] if s["scene_id"].startswith("S9"))
    assert max(row["direct_letterbox_equivalence"].values()) == 0


def test_axial_and_radial_paths_are_independently_exact():
    result = json.loads((ROOT / "outputs_stage4" / "synthetic_matrix.json").read_text())
    row = next(s for s in result["scenes"] if s["scene_id"].startswith("S10"))
    for item in row["semantics_validation"].values():
        assert item["correct_max_error_m"] < 1e-12 and item["wrong_semantics_median_point_error_m"] > 0.02


def test_contamination_and_dual_peak_cases_report_support():
    result = json.loads((ROOT / "outputs_stage4" / "synthetic_matrix.json").read_text())
    for prefix in ("S6", "S7"):
        row = next(s for s in result["scenes"] if s["scene_id"].startswith(prefix))
        assert row["filter_metrics"]["point_count"] >= 40
