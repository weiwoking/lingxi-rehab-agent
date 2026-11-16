import numpy as np

from deltar_p4_pipeline.official_old_chain_benchmark import estimate_old_distance


def fixture():
    fr = np.asarray([[r * 60, c * 80, (r + 1) * 60, (c + 1) * 80] for r in range(8) for c in range(8)])
    hist = np.column_stack((np.full(64, 2.0), np.full(64, 0.05)))
    return hist, fr, np.ones(64, dtype=bool)


def test_old_chain_median_and_contract():
    hist, fr, mask = fixture()
    hist[1, 0] = 2.1
    row = estimate_old_distance([0, 0, 160, 60], hist, fr, mask)
    assert row["status"] == "valid"
    assert row["old_distance_m"] == 2.05
    assert row["segment_used"] is False and row["gt_used"] is False


def test_old_chain_rejects_official_invalid_mask():
    hist, fr, mask = fixture()
    mask[0] = False
    row = estimate_old_distance([0, 0, 80, 60], hist, fr, mask)
    assert row["status"] == "rejected"
    assert row["rejection_reason"] == "no_valid_tof_zone"


def test_legacy_flip_is_explicit_ablation():
    hist, fr, mask = fixture()
    row = estimate_old_distance([0, 0, 80, 60], hist, fr, mask, legacy_flip=True)
    assert row["orientation"] == "legacy_hardcoded_flip_horizontal_and_vertical"
