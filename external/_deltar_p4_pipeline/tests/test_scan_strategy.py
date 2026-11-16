import math

import pytest

from deltar_p4_pipeline.scan_strategy import coverage_table, distance_band, fov_coverage, scan_recommendation


def test_fov_coverage_formula_and_zone_size():
    row = fov_coverage(1.0)
    expected = 2 * math.tan(math.radians(45) / 2)
    assert row["coverage_width_m"] == pytest.approx(expected)
    assert row["coverage_height_m"] == pytest.approx(expected)
    assert row["zone_width_m"] == pytest.approx(expected / 8)
    assert row["zone_height_m"] == pytest.approx(expected / 8)


@pytest.mark.parametrize(
    ("distance", "expected"),
    [(0.0, "0.0-0.75_m"), (0.749, "0.0-0.75_m"), (0.75, "0.75-1.0_m"),
     (1.0, "1.0-1.25_m"), (4.0, ">4.0_m"), (4.1, ">4.0_m")],
)
def test_distance_band_boundaries(distance, expected):
    assert distance_band(distance) == expected


def test_scan_recommendation_schema_and_ordering():
    value = scan_recommendation()
    assert value["schema_version"] == "deltar_scan_distance_recommendation.v1"
    assert value["recommended_range_m"][0] <= value["default_scan_distance_m"] <= value["recommended_range_m"][1]
    assert value["small_object_range"] == [0.8, 1.2]
    assert value["full_person_policy"]["multi_frame_scan_recommended"] is True


def test_coverage_table_contains_required_distances_and_limitations():
    value = coverage_table()
    assert [row["distance_m"] for row in value["rows"]] == [0.5, 0.8, 1.0, 1.2, 1.3, 1.5, 1.8, 2.0, 2.5, 3.0, 4.0]
    assert any("not a guarantee" in item for item in value["limitations"])
