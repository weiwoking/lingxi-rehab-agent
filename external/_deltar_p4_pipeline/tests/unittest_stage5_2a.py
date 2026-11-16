import unittest

import numpy as np

from deltar_p4_pipeline.official_depth_metrics import error_record
from deltar_p4_pipeline.official_object_benchmark import official_samples, pack_mask, unpack_mask
from deltar_p4_pipeline.official_old_chain_benchmark import estimate_old_distance
from deltar_p4_pipeline.official_zone_mapping import mapping_audit, zones_for_bbox


class Stage52ACompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.fr = np.asarray([[r * 60, c * 80, (r + 1) * 60, (c + 1) * 80] for r in range(8) for c in range(8)])

    def test_official_count(self):
        self.assertEqual(len(official_samples()), 287)

    def test_zone_contract(self):
        self.assertTrue(mapping_audit(self.fr)["identity_mapping_verified"])

    def test_bbox_zone(self):
        self.assertEqual(zones_for_bbox([0, 0, 80, 60], self.fr), [0])

    def test_old_no_gt(self):
        hist = np.column_stack((np.ones(64), np.full(64, .05)))
        row = estimate_old_distance([0, 0, 80, 60], hist, self.fr, np.ones(64, dtype=bool))
        self.assertFalse(row["gt_used"])

    def test_mask_roundtrip(self):
        mask = np.eye(17, dtype=bool)
        self.assertTrue(np.array_equal(mask, unpack_mask(pack_mask(mask))))

    def test_error(self):
        self.assertAlmostEqual(error_record(1.1, 1.0)["relative_error"], .1)


if __name__ == "__main__":
    unittest.main()
