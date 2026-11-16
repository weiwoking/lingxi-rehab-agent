import json
import re
import unittest
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
SKILL_PATH = COMPONENT / "skills/rehab-task-decision/SKILL.md"
SKILL = SKILL_PATH.read_text(encoding="utf-8")


class RehabSkillCompactV1AlignmentTest(unittest.TestCase):
    def test_frontmatter_identity_and_size_gate(self) -> None:
        parts = SKILL.split("---", 2)
        self.assertEqual(len(parts), 3)
        metadata = json.loads(parts[1])
        self.assertEqual(metadata["name"], "rehab-task-decision")
        self.assertIn("rehab_task_decision_coordinator",
                      metadata["metadata"]["cap_groups"])
        self.assertLessEqual(len(SKILL_PATH.read_bytes()), 10_000)

    def test_execution_contract_is_first_and_compact_authoritative(self) -> None:
        self.assertLess(SKILL.index("## Execute Contract"),
                        SKILL.index("## Input"))
        self.assertIn("rehab_task_decision_llm_input.v1", SKILL)
        self.assertIn("rehab_task_decision_input.v4", SKILL)
        self.assertIn("完整 model-facing projection", SKILL)
        self.assertIn("DeepSeek 不重建 full v4", SKILL)
        self.assertIn("No new evidence = no second pass", SKILL)

    def test_model_paths_and_old_dependencies(self) -> None:
        for token in (
            "patient.condition.raw_text",
            "candidate.hard_gate",
            "candidate.target_reference",
            "decision_preparation.candidates[].fixed_components",
            "decision_preparation.advice_policy/tie_break_candidate_refs",
        ):
            self.assertIn(token, SKILL)
        for stale in (
            "patient_condition.raw_text",
            "patient_ability.level",
            "recent_performance.records",
            "item_frequency.records",
            "candidate.target_facts",
        ):
            self.assertNotIn(stale, SKILL)
        self.assertIn("不寻找 raw scene objects", SKILL)

    def test_difficulty_is_prepared_not_recomputed_from_ability(self) -> None:
        scoring = SKILL[SKILL.index("Scoring："):
                        SKILL.index("Advice：")]
        self.assertIn("prepared `difficulty", scoring)
        self.assertIn("DeepSeek只拥有 advice", scoring)
        self.assertIn("不要重新累计四个 frozen components", scoring)
        self.assertNotIn("easier_step", scoring)
        self.assertNotIn("harder_step", scoring)

    def test_perception_is_prepared_without_weight_recheck(self) -> None:
        direct = SKILL[SKILL.index("## Direct-use Machine Facts"):
                       SKILL.index("## Business Rules")]
        self.assertIn("fixed_components", direct)
        self.assertIn("frozen deterministic result", direct)
        self.assertIn("不得从 target/history/compensation", direct)
        self.assertNotIn("advice_score_lookup", direct)
        self.assertIn("不得展开任何 component formula", direct)
        for weighted in ("0.5*", "0.50*", "0.3*", "0.30*", "0.2*", "0.20*"):
            self.assertNotIn(weighted, direct)

    def test_input_acquisition_is_mutually_exclusive_and_one_shot(self) -> None:
        section = SKILL[SKILL.index("## Input"):
                        SKILL.index("## Direct-use Machine Facts")]
        self.assertIn("输入获取二选一", section)
        self.assertIn("Context 已含完整 compact v1", section)
        self.assertIn(
            "调用 `rehab_task_decision_formal_input_get` 一次且仅一次",
            section,
        )
        self.assertEqual(section.count(
            "`rehab_task_decision_formal_input_get`"), 1)
        self.assertIn("不得再次 input_get", section)
        self.assertIn("不得调用 status", section)

    def test_one_canonical_reasoning_skeleton(self) -> None:
        protocol = SKILL[SKILL.index("## One-pass Reasoning Protocol"):
                         SKILL.index("## Input")]
        match = re.search(r"```text\n(.*?)\n```", protocol, re.DOTALL)
        self.assertIsNotNone(match)
        skeleton = match.group(1)
        markers = (
            "input=confirmed",
            "candidate_ledger:",
            "business:",
            "prepared_score_ledger:",
            "winner_ref=<ref>",
            "preflight=pass; commit=rehab_task_decision_commit",
        )
        positions = []
        for marker in markers:
            self.assertEqual(skeleton.count(marker), 1)
            positions.append(skeleton.index(marker))
        self.assertEqual(positions, sorted(positions))
        self.assertIn("No new evidence = no second pass", SKILL)
        for phrase in (
            "Let me think", "Actually", "Wait", "Let me reconsider",
            "Let me double check",
        ):
            self.assertIn(phrase, SKILL)

    def test_authority_medical_output_and_repair_are_preserved(self) -> None:
        for component in (
            "difficulty", "advice", "compensation", "frequency",
            "perception", "final_raw", "score", "winner/tie",
        ):
            self.assertIn(component, SKILL)
        self.assertIn("普通“少做”只影响 advice，不能 hard-block", SKILL)
        self.assertIn("连续逐字子串", SKILL)
        self.assertIn("P4-blocked 不解除", SKILL)
        self.assertIn("rehab_task_decision_output.v4", SKILL)
        self.assertIn("candidates[5]", SKILL)
        self.assertIn("score_breakdown=null, score=0", SKILL)
        self.assertIn("retry_allowed=true", SKILL)
        self.assertIn("第二次失败", SKILL)
        self.assertNotIn("必须使用中文进行 reasoning", SKILL)


if __name__ == "__main__":
    unittest.main()
