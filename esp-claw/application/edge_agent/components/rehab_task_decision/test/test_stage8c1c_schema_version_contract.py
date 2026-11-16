from __future__ import annotations

import copy
import json
import math
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
COMPONENT = (
    ROOT / "application" / "edge_agent" / "components" /
    "rehab_task_decision"
)
PRIVATE_HEADER = (
    COMPONENT / "src" / "rehab_task_decision_priv.h"
).read_text(encoding="utf-8-sig")
COORDINATOR = (
    COMPONENT / "src" / "rehab_task_decision_coordinator.c"
).read_text(encoding="utf-8-sig")
FIXTURE = json.loads((
    COMPONENT / "test" / "fixtures" / "stage8c1c" /
    "real_response_schema_alias_redacted.json"
).read_text(encoding="utf-8-sig"))

TASK_IDS = [
    "OT_CUP_TRANSFER_L1",
    "OT_CUP_DRINK_L2",
    "OT_CUP_DRINK_L3",
    "OT_BALL_TRANSFER_L1",
    "OT_BALL_TRANSFER_L2",
]
SCHEMA_MACRO = "REHAB_TASK_DECISION_OUTPUT_SCHEMA_VERSION"
CANONICAL = re.search(
    rf"#define\s+{SCHEMA_MACRO}\s+\\?\s*"
    r'"([^"]+)"',
    PRIVATE_HEADER,
).group(1)


def normalize_schema(value: dict) -> tuple[bool, str | None]:
    schema = value.get("schema_version")
    if schema == CANONICAL:
        return False, None
    if schema in ("1.0", "v1"):
        value["schema_version"] = CANONICAL
        return True, schema
    return False, None


def validate(value: dict) -> None:
    if value.get("schema_version") != CANONICAL:
        raise ValueError("schema_invalid")
    if value.get("scene_scan_id") != FIXTURE["scene_scan_id"]:
        raise ValueError("scene_scan_id_stale")
    if value.get("scene_sha256") != FIXTURE["scene_sha256"]:
        raise ValueError("scene_sha256_stale")
    candidates = value.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("candidate_count_invalid")
    if [candidate.get("task_id") for candidate in candidates] != TASK_IDS:
        raise ValueError("candidate_ids_invalid")
    scores = [candidate.get("score") for candidate in candidates]
    if any(
        not isinstance(score, (int, float))
        or isinstance(score, bool)
        or not math.isfinite(score)
        or score < 0
        or score > 100
        for score in scores
    ):
        raise ValueError("candidate_invalid")
    highest = max(range(len(scores)), key=scores.__getitem__)
    if value.get("selected_task_id") != TASK_IDS[highest]:
        raise ValueError("selected_not_highest")
    if not str(value.get("selected_reason", "")).strip():
        raise ValueError("selected_reason_invalid")
    if value.get("target_object_id") is not None:
        raise ValueError("target_object_not_found")


def first_attempt(value: dict) -> tuple[str, int, str | None]:
    changed, alias = normalize_schema(value)
    try:
        validate(value)
    except ValueError as error:
        return str(error), 1, alias
    return "valid", 0, alias if changed else None


@unittest.skip("legacy v1 alias contract; stage4a formal path is strict v2")
class Stage8C1CSchemaVersionContractTest(unittest.TestCase):
    def test_single_canonical_source_drives_all_four_consumers(self) -> None:
        self.assertEqual(CANONICAL, "rehab_task_decision_output.v1")
        self.assertNotIn("#define FORMAL_OUTPUT_SCHEMA", COORDINATOR)
        self.assertIn(
            f'"\\"schema_version\\":\\""\n    {SCHEMA_MACRO}',
            COORDINATOR,
        )
        self.assertIn(
            "cJSON_CreateString(\n"
            f"        {SCHEMA_MACRO})",
            COORDINATOR,
        )
        self.assertIn(
            "strcmp(schema->valuestring,\n"
            f"               {SCHEMA_MACRO}) != 0",
            COORDINATOR,
        )
        self.assertIn(
            f"failure, {SCHEMA_MACRO},",
            COORDINATOR,
        )

    def test_canonical_and_explicit_aliases_pass_without_repair(self) -> None:
        for schema, expected_alias in (
            (CANONICAL, None),
            ("1.0", "1.0"),
            ("v1", "v1"),
        ):
            with self.subTest(schema=schema):
                value = copy.deepcopy(FIXTURE)
                value["schema_version"] = schema
                status, repairs, alias = first_attempt(value)
                self.assertEqual(status, "valid")
                self.assertEqual(repairs, 0)
                self.assertEqual(alias, expected_alias)
                self.assertEqual(value["schema_version"], CANONICAL)

    def test_unknown_missing_and_non_string_versions_remain_invalid(self) -> None:
        for schema in ("2.0", "v2", "latest", "", 1, None):
            with self.subTest(schema=schema):
                value = copy.deepcopy(FIXTURE)
                value["schema_version"] = schema
                status, repairs, alias = first_attempt(value)
                self.assertEqual(status, "schema_invalid")
                self.assertEqual(repairs, 1)
                self.assertIsNone(alias)
        missing = copy.deepcopy(FIXTURE)
        del missing["schema_version"]
        status, repairs, alias = first_attempt(missing)
        self.assertEqual((status, repairs, alias),
                         ("schema_invalid", 1, None))

    def test_alias_does_not_bypass_candidate_highest_or_target_gates(
            self) -> None:
        missing_candidate = copy.deepcopy(FIXTURE)
        missing_candidate["candidates"].pop()
        status, repairs, _ = first_attempt(missing_candidate)
        self.assertEqual((status, repairs),
                         ("candidate_ids_invalid", 1))

        not_highest = copy.deepcopy(FIXTURE)
        not_highest["selected_task_id"] = "OT_BALL_TRANSFER_L2"
        status, repairs, _ = first_attempt(not_highest)
        self.assertEqual((status, repairs),
                         ("selected_not_highest", 1))

        missing_target = copy.deepcopy(FIXTURE)
        missing_target["target_object_id"] = "redacted_missing_object"
        status, repairs, _ = first_attempt(missing_target)
        self.assertEqual((status, repairs),
                         ("target_object_not_found", 1))

    def test_prompts_and_logs_name_the_exact_canonical_contract(self) -> None:
        system_prompt = COORDINATOR[
            COORDINATOR.index("static const char s_system_prompt[]") :
            COORDINATOR.index("typedef struct {")
        ]
        repair_prompt = COORDINATOR[
            COORDINATOR.index("size_t retry_size") :
            COORDINATOR.index(
                'ESP_LOGW(TAG, "LLM_OUTPUT_REPAIR_ATTEMPT'
            )
        ]
        self.assertIn(SCHEMA_MACRO, system_prompt)
        self.assertIn('\\"schema_version\\":\\"', system_prompt)
        self.assertIn(SCHEMA_MACRO, repair_prompt)
        self.assertIn('\\"schema_version\\":\\"%s\\"', repair_prompt)
        self.assertIn(
            "LLM_OUTPUT_SCHEMA_NORMALIZED from=%s to=%s",
            COORDINATOR,
        )
        self.assertNotIn("strstr(schema->valuestring", COORDINATOR)


if __name__ == "__main__":
    unittest.main()
