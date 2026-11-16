from __future__ import annotations

import copy
import json
import re
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


COMPONENT = Path(__file__).resolve().parents[1]
TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR))

from llm_compact_reference import adapt_formal_input  # noqa: E402


COORDINATOR = (
    COMPONENT / "src/rehab_task_decision_coordinator.c"
).read_text(encoding="utf-8")
CAPABILITIES = (
    COMPONENT / "src/rehab_task_decision_coordinator_capabilities.c"
).read_text(encoding="utf-8")
ADAPTER = (
    COMPONENT / "src/rehab_task_decision_llm_input_adapter.c"
).read_text(encoding="utf-8")
PREPARATION = (
    COMPONENT / "src/rehab_task_decision_preparation.c"
).read_text(encoding="utf-8")
PREPARATION_HEADER = (
    COMPONENT / "src/rehab_task_decision_preparation.h"
).read_text(encoding="utf-8")
ARTIFACT = (
    COMPONENT / "src/rehab_task_decision_llm_input_artifact.c"
).read_text(encoding="utf-8")
SCORING = (
    COMPONENT / "src/rehab_task_decision_scoring.c"
).read_text(encoding="utf-8")
CMAKE = (COMPONENT / "CMakeLists.txt").read_text(encoding="utf-8")
SCHEMA_PATH = (
    COMPONENT
    / "test/fixtures/llm_compact/rehab_task_decision_llm_input.v1.schema.json"
)


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*\([^;]*?\)\s*\{{", source, re.S)
    if match is None:
        raise AssertionError(f"function not found: {name}")
    brace = source.index("{", match.start())
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace : index + 1]
    raise AssertionError(f"unterminated function: {name}")


def baseline_dir() -> Path:
    return Path.home() / "Downloads/baseline"


def latest_formal_path() -> Path:
    downloads = Path.home() / "Downloads"
    candidates = list(downloads.glob("latest_formal_decision_input*.json"))
    candidates += list((downloads / "baseline").glob(
        "latest_formal_decision_input*.json"
    ))
    if not candidates:
        return downloads / "latest_formal_decision_input.json"
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


class CompactInputIntegrationTest(unittest.TestCase):
    def test_adapter_is_after_full_freeze_and_model_only(self) -> None:
        builder = function_body(COORDINATOR, "build_llm_input")
        validator = function_body(COORDINATOR, "validate_output")
        commit = function_body(COORDINATOR,
                               "rehab_task_decision_formal_commit")
        worker = function_body(COORDINATOR, "coordinator_worker")
        self.assertNotIn("llm_input_compact", builder)
        self.assertNotIn("llm_input_compact", validator)
        self.assertNotIn("llm_input_compact", commit)
        self.assertLess(worker.index("build_llm_input("),
                        worker.index("llm_input_compact("))
        self.assertIn("decision_id, model_input", worker)
        self.assertIn("build_repair_prompt(model_input", worker)
        self.assertEqual(worker.count("validate_output(handle, &scene, prompt"), 2)
        self.assertNotIn("llm_input", SCORING)

    def test_root_fast_and_direct_paths_use_compact_with_full_fallback(self) -> None:
        fast = function_body(CAPABILITIES, "formal_fast_context_collect")
        root = function_body(CAPABILITIES, "formal_input_execute")
        helper = function_body(CAPABILITIES, "model_input_prepare")
        worker = function_body(COORDINATOR, "coordinator_worker")
        self.assertIn("model_input_prepare(formal_input", fast)
        self.assertIn("FORMAL_FAST_INPUT_PREFIX, model_input", fast)
        self.assertIn("rehab_task_decision_formal_input_render", root)
        self.assertIn("model_input_prepare(formal_input", root)
        self.assertIn("memcpy(output, model_input", root)
        self.assertIn("return formal_input;", helper)
        self.assertIn("llm_input != NULL ? llm_input : prompt", worker)
        self.assertIn("LLM_COMPACT_INPUT_FAILED", helper)
        self.assertIn("LLM_COMPACT_INPUT_FAILED", worker)

    def test_full_and_compact_latest_artifacts_are_separate(self) -> None:
        self.assertIn('"rehab/latest_decision_llm_input.json"', ARTIFACT)
        self.assertIn('"rehab/latest_decision_llm_input.json.next"', ARTIFACT)
        self.assertIn('"rehab/latest_decision_llm_input.json.rollback"', ARTIFACT)
        self.assertIn("rename(final_path, rollback_path)", ARTIFACT)
        self.assertIn("rename(next_path, final_path)", ARTIFACT)
        self.assertIn("rehab_task_decision_llm_input_adapter.c", CMAKE)
        self.assertIn("rehab_task_decision_preparation.c", CMAKE)
        self.assertIn("rehab_task_decision_llm_input_artifact.c", CMAKE)
        self.assertIn("formal_input_artifact_save_latest", COORDINATOR)

    def test_projection_omits_only_model_redundancy(self) -> None:
        for forbidden in (
            '"scene"',
            '"semantic_groups"',
            '"bbox_xyxy"',
            '"source_frame_id"',
            '"source_frame_sequence"',
            '"warnings"',
            '"image_reference_path"',
            '"component_rules"',
        ):
            self.assertNotIn(forbidden, ADAPTER)
        for required in (
            '"decision_binding"',
            '"patient"',
            '"history"',
            '"candidates"',
            '"targets"',
            '"latest_compensation"',
        ):
            self.assertIn(required, ADAPTER)
        self.assertIn("rehab_task_decision_preparation_add", ADAPTER)
        self.assertIn('"decision_preparation"', PREPARATION)
        self.assertIn('"advice_policy"', PREPARATION)
        self.assertIn('"tie_break_candidate_refs"', PREPARATION)
        self.assertNotIn('"advice_score_lookup"', PREPARATION)
        self.assertNotIn("add_scoring_policy", ADAPTER)

    def test_schema_is_valid(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)

    def test_schema_expansion_accepts_pre_completion_v1_artifact(self) -> None:
        downloads = Path.home() / "Downloads"
        candidates = list(downloads.glob("latest_decision_llm_input*.json"))
        if not candidates:
            self.skipTest("pre-completion compact artifact is not installed")
        compact = None
        for artifact in sorted(
            candidates, key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        ):
            candidate = json.loads(artifact.read_text(encoding="utf-8-sig"))
            if "decision_preparation" not in candidate:
                compact = candidate
                break
        if compact is None:
            self.skipTest("pre-preparation compact v1 artifact is not installed")
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(compact)
        self.assertNotIn("decision_preparation", compact)

    def test_baseline_projection_preserves_decision_inputs(self) -> None:
        formal_path = latest_formal_path()
        if not formal_path.exists():
            self.skipTest("local replay baseline is not installed")
        formal = json.loads(formal_path.read_text(encoding="utf-8"))
        formal_before = copy.deepcopy(formal)
        compact = adapt_formal_input(formal)
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(compact)
        self.assertEqual(formal, formal_before)
        self.assertEqual(len(compact["candidates"]), 5)
        self.assertEqual(
            compact["decision_preparation"]["schema_version"],
            "rehab_task_decision_preparation.v1.1",
        )
        self.assertEqual(
            [candidate["candidate_ref"] for candidate in compact["candidates"]],
            [0, 1, 2, 3, 4],
        )
        target_map = {
            target["target_ref"]: target for target in compact["targets"]
        }
        for source, projected in zip(formal["candidates"],
                                     compact["candidates"], strict=True):
            self.assertEqual(projected["task_id"], source["task_id"])
            self.assertEqual(projected["availability"], source["availability"])
            self.assertEqual(projected["hard_gate"]["blocked_reason"],
                             source["hard_gates"]["blocked_reason"])
            if projected["availability"] == "available":
                self.assertIn(projected["target_reference"], target_map)

    def test_difficulty_is_frozen_from_tendency_not_ability(self) -> None:
        formal_path = latest_formal_path()
        if not formal_path.exists():
            self.skipTest("local replay baseline is not installed")
        formal = json.loads(formal_path.read_text(encoding="utf-8"))
        synthetic = copy.deepcopy(formal)
        synthetic["patient_ability"]["level"] = 2
        synthetic["recent_performance"]["tendency_level"] = "level_1"
        compact = adapt_formal_input(synthetic)
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(compact)

        prepared = compact["decision_preparation"]["candidates"]
        self.assertEqual(prepared[0]["fixed_components"]["difficulty"], 35)
        self.assertEqual(prepared[1]["fixed_components"]["difficulty"], 14)
        self.assertNotEqual(
            compact["patient"]["ability"]["level"],
            int(compact["history"]["recent_tendency"].split("_")[1]),
        )
        self.assertNotIn("scoring_policy", compact)

    def test_projected_perception_quality_is_preserved(self) -> None:
        formal_path = latest_formal_path()
        if not formal_path.exists():
            self.skipTest("local replay baseline is not installed")
        formal = json.loads(formal_path.read_text(encoding="utf-8"))
        compact = adapt_formal_input(formal)
        targets = {target["target_ref"]: target
                   for target in compact["targets"]}
        for source, candidate in zip(formal["candidates"],
                                     compact["candidates"], strict=True):
            target_ref = candidate["target_reference"]
            if target_ref is None:
                continue
            self.assertEqual(
                targets[target_ref]["perception"]["quality"],
                source["target_facts"]["perception_quality"],
            )

    def test_preparation_synthetic_matrix(self) -> None:
        formal_path = latest_formal_path()
        if not formal_path.exists():
            self.skipTest("local replay baseline is not installed")
        formal = json.loads(formal_path.read_text(encoding="utf-8"))
        synthetic = copy.deepcopy(formal)
        synthetic["patient_ability"]["level"] = 3
        synthetic["recent_performance"]["tendency_level"] = "level_1"
        synthetic["item_frequency"]["cup_count"] = 3
        synthetic["item_frequency"]["ball_count"] = 0
        synthetic["latest_compensation"].update({
            "status": "available",
            "exact_task_id": "OT_CUP_DRINK_L2",
            "action_family": "drink_water",
            "mapped_group": "cup",
            "compensation_action_count": 2,
            "severity": 0.9,
        })
        fallback_facts = synthetic["candidates"][1]["target_facts"]
        fallback_facts["geometry_confidence"] = None
        fallback_facts["geometry_confidence_status"] = "unavailable_fallback"
        fallback_facts["perception_mode"] = "fallback"
        fallback_facts["detection_confidence"] = 0.9
        fallback_facts["height_confidence"] = 1.0
        fallback_facts["perception_quality"] = 0.94

        compact = adapt_formal_input(synthetic)
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(compact)
        prepared = compact["decision_preparation"]["candidates"]

        self.assertEqual(prepared[0]["fixed_components"]["difficulty"], 35)
        self.assertEqual(prepared[1]["fixed_components"]["difficulty"], 14)
        self.assertEqual(prepared[0]["fixed_components"]["frequency"], 0)
        self.assertEqual(prepared[3]["fixed_components"]["frequency"], 10)
        self.assertAlmostEqual(
            prepared[1]["fixed_components"]["compensation"], 2.0
        )
        self.assertAlmostEqual(
            prepared[1]["fixed_components"]["perception"], 9.76
        )
        self.assertIsNone(prepared[2]["fixed_components"])
        self.assertEqual(
            compact["decision_preparation"]["advice_policy"],
            {
                "range": [16, 25],
                "positive_range": [21, 25],
                "negative_range": [16, 19],
                "neutral": 20,
            },
        )
        self.assertNotIn("advice_score_lookup", prepared[1])
        self.assertEqual(
            compact["decision_preparation"]["tie_break_candidate_refs"],
            synthetic["scoring_policy"]["tie_break_candidate_refs"],
        )

        self.assertIn('"tendency_level"', PREPARATION)
        self.assertIn("fixed_component_authority", PREPARATION)
        self.assertIn("PREPARATION_SCHEMA_VERSION", PREPARATION_HEADER)


if __name__ == "__main__":
    unittest.main()
