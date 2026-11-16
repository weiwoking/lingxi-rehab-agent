from deltar_p4_pipeline.official_depth_metrics import error_record, summarize
from deltar_p4_pipeline.stage5_2a_validation import acceptance


def test_relative_error_uses_gt_denominator():
    row = error_record(2.2, 2.0)
    assert abs(row["relative_error"] - 0.1) < 1e-12
    assert abs(row["signed_error_m"] - 0.2) < 1e-12


def test_ten_percent_acceptance_is_inclusive():
    rows = [{"x_absolute_error_m": 0.2, "x_relative_error": 0.1, "x_rejection_reason": None}]
    assert summarize(rows, "x")["within_10_percent_rate"] == 1.0


def test_acceptance_requires_coverage_and_all_quality_gates():
    old = {"absolute_error_m": {"median": .2, "p90": .4}, "relative_error": {"median": .2}, "within_10_percent_rate": .2}
    new = {"absolute_error_m": {"median": .1, "p90": .3}, "relative_error": {"median": .1}, "within_10_percent_rate": .5}
    assert acceptance(100, 50, 4, old, new, {"new_better": True})["verdict"] == "PASS"
    assert acceptance(100, 49, 4, old, new, {"new_better": True})["verdict"] == "PARTIAL"
