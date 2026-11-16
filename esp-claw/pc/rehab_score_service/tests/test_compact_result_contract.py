import json
import unittest

from cloud_interface.compact_score_result import P4_RESULT_FIELDS, build_compact_score_result
from _job_test_support import SESSION_ID, full_result


class CompactResultContractTests(unittest.TestCase):
    def test_projection_has_current_p4_aggregate_fields_only(self):
        compact = build_compact_score_result(
            full_result(), SESSION_ID, "2026-07-14T08:30:00+00:00"
        )
        self.assertEqual(compact["schema_version"], "rehab_score_result_v1")
        self.assertEqual(compact["task_number"], 12)
        self.assertEqual(compact["pc_job_id"], SESSION_ID)
        self.assertEqual(compact["repetition_count"], 2)
        self.assertEqual(tuple(compact.keys()), P4_RESULT_FIELDS)
        self.assertEqual(set(compact.keys()), set(P4_RESULT_FIELDS))
        self.assertEqual(len(compact), 20)
        self.assertNotIn("actions", compact)
        encoded = json.dumps(compact, ensure_ascii=False).encode("utf-8")
        self.assertLessEqual(len(encoded), 64 * 1024)
        serialized = encoded.decode("utf-8")
        for forbidden in (
            "actions",
            "subscores",
            "feedback",
            "trajectory",
            "comparisons",
            "curve_data",
            "base64",
        ):
            self.assertNotIn(forbidden, serialized.lower())

    def test_missing_full_result_field_is_rejected(self):
        full = full_result()
        del full["summary"]
        with self.assertRaisesRegex(ValueError, "missing required fields"):
            build_compact_score_result(full, SESSION_ID)

    def test_missing_actions_do_not_block_aggregate_projection(self):
        full = full_result()
        del full["actions"]
        compact = build_compact_score_result(full, SESSION_ID)
        self.assertEqual(tuple(compact.keys()), P4_RESULT_FIELDS)
        self.assertNotIn("actions", compact)

    def test_lateral_long_score_version_maps_to_device_alias(self):
        full = full_result()
        full["score_version"] = "lateral_transfer_session_scoring_v1_1_reasonable_speed"
        full["model_version"] = "lateral_transfer_session_scoring_v1_1_reasonable_speed"
        compact = build_compact_score_result(full, SESSION_ID)
        self.assertEqual(compact["score_version"], "lateral_transfer_v1_1_reasonable_speed")
        self.assertLess(len(compact["score_version"].encode("utf-8")), 48)
        self.assertEqual(
            compact["model_version"],
            "lateral_transfer_session_scoring_v1_1_reasonable_speed",
        )
        self.assertLess(len(compact["model_version"].encode("utf-8")), 96)

    def test_drink_score_version_is_preserved(self):
        full = full_result()
        full["score_version"] = "v1.2_reasonable_speed"
        full["model_version"] = "drink_water_session_v1_4_reasonable_speed"
        compact = build_compact_score_result(full, SESSION_ID)
        self.assertEqual(compact["score_version"], "v1.2_reasonable_speed")
        self.assertLess(len(compact["score_version"].encode("utf-8")), 48)
        self.assertLess(len(compact["model_version"].encode("utf-8")), 96)

    def test_unknown_overlong_score_version_fails_closed(self):
        full = full_result()
        full["score_version"] = "unknown_score_version_that_is_definitely_too_long_for_p4_limit"
        with self.assertRaisesRegex(ValueError, "score_version"):
            build_compact_score_result(full, SESSION_ID)

    def test_overlong_model_version_fails_closed(self):
        full = full_result()
        full["model_version"] = "m" * 96
        with self.assertRaisesRegex(ValueError, "model_version"):
            build_compact_score_result(full, SESSION_ID)


if __name__ == "__main__":
    unittest.main()
