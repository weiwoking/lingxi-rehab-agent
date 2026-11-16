import numpy as np

from deltar_p4_pipeline.official_zone_mapping import hardcoded_flip_index, mapping_audit, zones_for_bbox


def grid():
    rows = []
    for row in range(8):
        for col in range(8):
            rows.append([row * 60, col * 80, (row + 1) * 60, (col + 1) * 80])
    return np.asarray(rows)


def test_official_row_major_identity():
    assert mapping_audit(grid())["identity_mapping_verified"] is True


def test_bbox_to_zone_uses_fr_centres():
    assert zones_for_bbox([80, 60, 160, 120], grid()) == [9]


def test_bbox_without_centre_uses_nearest_zone():
    assert zones_for_bbox([0, 0, 5, 5], grid()) == [0]


def test_legacy_flip_is_both_axes():
    assert hardcoded_flip_index(0) == 63
    assert hardcoded_flip_index(8) == 55
    assert hardcoded_flip_index(63) == 0
