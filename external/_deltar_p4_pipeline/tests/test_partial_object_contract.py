from deltar_p4_pipeline.partial_object import SCHEMA_VERSION, border_flags, border_group, visibility_contract


BASE = dict(
    mask_touches_border=False, bbox_touches_border=False, top_visible=True, bottom_visible=True,
    obvious_occlusion=False, depth_continuous=True, large_truncation=False, point_support_top=True,
    point_support_bottom=True, distance_valid=True, visible_mask_valid=True, ground_transform_valid=True,
    calibration_measured=True,
)


def test_complete_requires_every_extent_condition():
    result = visibility_contract(**BASE)
    assert result["schema_version"] == SCHEMA_VERSION
    assert result["visibility_status"] == "complete"
    assert result["physical_height_valid"] is True
    assert result["bottom_height_valid"] is True


def test_partial_keeps_surface_distance_and_visible_height_but_nulls_physical_height():
    result = visibility_contract(**{**BASE, "top_visible": False})
    assert result["visibility_status"] == "partial"
    assert result["distance_valid"] is True
    assert result["distance_semantics"] == "visible_surface_distance"
    assert result["visible_height_valid"] is True
    assert result["physical_height_valid"] is False
    assert result["physical_height_m"] is None


def test_bottom_height_null_when_bottom_hidden_or_unmeasured():
    hidden = visibility_contract(**{**BASE, "bottom_visible": False})
    unmeasured = visibility_contract(**{**BASE, "calibration_measured": False})
    assert hidden["bottom_height_valid"] is False and hidden["bottom_height_m"] is None
    assert unmeasured["bottom_height_valid"] is False and unmeasured["bottom_height_m"] is None


def test_border_flags_and_multiple_group():
    flags = border_flags([0, 10, 639, 200], 640, 480)
    assert flags["touches_left"] and flags["touches_right"]
    assert not flags["touches_top"] and not flags["touches_bottom"]
    assert border_group(flags) == "multiple_borders"
