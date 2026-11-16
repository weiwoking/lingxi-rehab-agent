#!/usr/bin/env python3
"""Final five-component scoring tests against the production C validator."""

from __future__ import annotations

import copy
import json
import os
import random
import subprocess
import tempfile
import unittest
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
SRC = COMPONENT / "src"
TASKS = [
    ("OT_CUP_TRANSFER_L1", "水杯横向取放训练（一级）", 1, "cup", "low",
     "lateral_transfer"),
    ("OT_CUP_DRINK_L2", "水杯拿取与饮水训练（二级）", 2, "cup", "low",
     "drink_water"),
    ("OT_CUP_DRINK_L3", "水杯拿取与饮水训练（三级）", 3, "cup", "high",
     "drink_water"),
    ("OT_BALL_TRANSFER_L1", "球类横向取放训练（一级）", 1, "ball", "low",
     "lateral_transfer"),
    ("OT_BALL_TRANSFER_L2", "球类横向取放训练（二级）", 2, "ball", "high",
     "lateral_transfer"),
]
EVIDENCE = "建议加强杯子训练"
SCORING_PARAMETERS = {
    "difficulty_max": 35.0,
    "difficulty_easier_step": 8.0,
    "difficulty_harder_step": 21.0,
    "compensation_max": 20.0,
    "relatedness_exact_task": 1.0,
    "relatedness_same_action_family": 0.8,
    "relatedness_same_object_group": 0.6,
    "frequency_0": 10.0,
    "frequency_1": 6.0,
    "frequency_2": 5.0,
    "frequency_3": 0.0,
    "perception_min": 6.0,
    "perception_span": 4.0,
    "perception_geometry_weight": 0.5,
    "perception_detection_weight": 0.3,
    "perception_height_weight": 0.2,
    "fallback_detection_weight": 0.6,
    "fallback_height_weight": 0.4,
}


def make_input(
    *,
    ability: int = 2,
    tendency: int = 2,
    advice: str = EVIDENCE,
    cup_count: int = 1,
    ball_count: int = 2,
    previous_task: int | None = None,
    compensation_count: int = 0,
    geometry_confidence: float | None = 0.8,
    detection_confidence: float = 0.9,
    height_confidence: float = 1.0,
    blocked_refs: set[int] | None = None,
) -> dict:
    blocked_refs = blocked_refs or set()
    candidates = []
    for ref, (task_id, task_name, level, group, plane, family) in enumerate(TASKS):
        blocked_by_ability = level > ability
        blocked_by_plane = ref in blocked_refs
        blocked = blocked_by_ability or blocked_by_plane
        blocked_reason = ("ability_exceeded" if blocked_by_ability else
                          "no_matching_plane_target" if blocked_by_plane else "none")
        facts = {
            "target_object_id": None if blocked_by_plane else f"object-{ref}",
            "raw_class": "bottle" if group == "cup" else "volleyball",
            "display_group_name": "杯子" if group == "cup" else "排球",
            "support_height_mm": 710 if plane == "low" else 1070,
            "height_confidence": height_confidence,
            "geometry_confidence": geometry_confidence,
            "geometry_confidence_status": (
                "available" if geometry_confidence is not None
                else "unavailable_fallback"
            ),
            "perception_mode": (
                "geometry" if geometry_confidence is not None else "fallback"
            ),
            "geometry_valid": not blocked_by_plane,
            "detection_confidence": detection_confidence,
        }
        candidates.append({
            "candidate_ref": ref,
            "task_id": task_id,
            "task_name": task_name,
            "level": level,
            "semantic_group": group,
            "action_family": family,
            "required_plane": plane,
            "availability": "blocked" if blocked else "available",
            "hard_gates": {
                "allowed_by_ability": not blocked_by_ability,
                "ability_allowed": not blocked_by_ability,
                "group_object_count": 1,
                "feasible_target": not blocked_by_plane,
                "geometry_height_valid": not blocked_by_plane,
                "blocked": blocked,
                "blocked_reason": blocked_reason,
                "score_must_be_zero": blocked,
            },
            "target_facts": facts,
        })
    previous = TASKS[previous_task] if previous_task is not None else None
    return {
        "schema_version": "rehab_task_decision_input.v4",
        "decision_binding": {
            "decision_id": "decision-test", "generation": 7,
            "source_scan_id": "scan-test", "source_scene_sha256": "a" * 64,
        },
        "patient_condition": {
            "source": "user_condition.txt", "status": "ok",
            "raw_text": f"当前任务能力等级：{ability}级\n{advice}",
            "source_bytes": len(advice.encode()),
            "sent_bytes": len(advice.encode()), "truncated": False,
        },
        "patient_ability": {
            "level": ability, "parse_status": "parsed",
            "matched_line": f"当前任务能力等级：{ability}级",
            "source": "user_condition.txt",
        },
        "recent_performance": {
            "window_limit": 3, "record_count": 1,
            "records": [{"task_name": TASKS[0][1], "average_score": 76}],
            "tendency_level": f"level_{tendency}", "status": "available",
        },
        "item_frequency": {
            "cup_count": cup_count, "ball_count": ball_count,
            "records": [], "status": "available",
        },
        "latest_compensation": {
            "status": "available" if previous else "none",
            "task_name": previous[1] if previous else None,
            "exact_task_id": previous[0] if previous else None,
            "action_family": previous[5] if previous else None,
            "mapped_group": previous[3] if previous else None,
            "mapping_status": "mapped" if previous else "none",
            "compensation_action_count": compensation_count,
            "severity": [0.0, 0.3, 0.9, 1.0][min(compensation_count, 3)],
        },
        "scoring_policy": {
            "scoring_authority": "split",
            "decision_rules_version":
                "rehab.stage4.final_business_alignment.rules.v4",
            "score_rounding": "round_half_away_to_3_decimals",
            "parameters": copy.deepcopy(SCORING_PARAMETERS),
        },
        "candidates": candidates,
    }


def exact_components(source: dict, candidate: dict,
                     advice_value: str | int = 20) -> dict:
    level = candidate["level"]
    tendency = int(source["recent_performance"]["tendency_level"][-1])
    delta = level - tendency
    difficulty = max(0.0, min(35.0, 35.0 - (8.0 * -delta if delta < 0
                                             else 21.0 * delta)))
    group = candidate["semantic_group"]
    if isinstance(advice_value, str):
        advice = 20 if advice_value == "neutral" else (
            25 if advice_value == group else 16)
    else:
        advice = advice_value
    compensation = 20.0
    previous = source["latest_compensation"]
    if previous["status"] == "available":
        count = previous["compensation_action_count"]
        severity = [0.0, 0.3, 0.9, 1.0][min(count, 3)]
        if previous["exact_task_id"] == candidate["task_id"]:
            relatedness = 1.0
        elif previous["action_family"] == candidate["action_family"]:
            relatedness = 0.8
        elif previous["mapped_group"] == group:
            relatedness = 0.6
        else:
            relatedness = 0.0
        compensation = 20.0 * (1.0 - severity * relatedness)
    count = source["item_frequency"][f"{group}_count"]
    frequency = [10.0, 6.0, 5.0, 0.0][count]
    facts = candidate["target_facts"]
    detection = max(0.0, min(1.0, facts["detection_confidence"]))
    height = max(0.0, min(1.0, facts["height_confidence"]))
    geometry = facts["geometry_confidence"]
    if isinstance(geometry, (int, float)):
        q = (0.5 * max(0.0, min(1.0, geometry)) +
             0.3 * detection + 0.2 * height)
    else:
        q = 0.6 * detection + 0.4 * height
    perception = 6.0 + 4.0 * max(0.0, min(1.0, q))
    return {
        "difficulty": difficulty, "advice": advice,
        "compensation": compensation, "frequency": frequency,
        "perception": perception,
    }


def blocked_reason(candidate: dict) -> str:
    if candidate["hard_gates"]["blocked_reason"] == "ability_exceeded":
        return "任务等级超出患者能力，当前不允许执行。"
    plane = "低平面" if candidate["required_plane"] == "low" else "高平面"
    return f"{plane}没有符合高度或几何要求的有效目标。"


def make_output(source: dict, direction: str = "cup",
                evidence: str = EVIDENCE,
                *, advice_scores: dict[int, int] | None = None,
                evidences: dict[int, str] | None = None,
                medical_blocks: set[int] | None = None) -> dict:
    advice_scores = advice_scores or {}
    evidences = evidences or {}
    medical_blocks = medical_blocks or set()
    outputs = []
    winner_ref = None
    for candidate in source["candidates"]:
        ref = candidate["candidate_ref"]
        candidate_evidence = evidences.get(ref, evidence)
        group_name = "杯子" if candidate["semantic_group"] == "cup" else "排球"
        if candidate["availability"] == "blocked":
            outputs.append({
                "candidate_ref": ref, "availability": "blocked",
                "advice_evidence": candidate_evidence,
                "score_breakdown": None, "score": 0,
                "reason": blocked_reason(candidate),
            })
            continue
        if ref in medical_blocks:
            outputs.append({
                "candidate_ref": ref, "availability": "blocked",
                "advice_evidence": candidate_evidence,
                "score_breakdown": None, "score": 0,
                "reason": f"{candidate_evidence}，因此明确禁止该{group_name}任务。",
            })
            continue
        default_advice = (20 if direction == "neutral" else
                          25 if direction == candidate["semantic_group"] else 16)
        candidate_advice = advice_scores.get(ref, default_advice)
        breakdown = exact_components(source, candidate, candidate_advice)
        raw = sum(breakdown.values())
        breakdown["final_raw"] = raw
        score = round(max(0.0, min(100.0, raw)), 3)
        tendency = ("一级倾向" if source["recent_performance"]["tendency_level"]
                    == "level_1" else "二级倾向")
        count = source["item_frequency"][f"{candidate['semantic_group']}_count"]
        plane = "低平面" if candidate["required_plane"] == "low" else "高平面"
        height = round(candidate["target_facts"]["support_height_mm"])
        reason = (
            f"能力允许；{tendency}；医嘱分{candidate_advice}：{candidate_evidence}；"
            f"{group_name}最近{count}次；代偿历史已按相关性计分；"
            f"目标为{group_name}；支撑高度{height}毫米；{plane}；"
            f"高度与感知有效；得到得分{score:.3f}。"
        )
        outputs.append({
            "candidate_ref": ref, "availability": "available",
            "advice_evidence": candidate_evidence,
            "score_breakdown": breakdown, "score": score, "reason": reason,
        })
        if winner_ref is None or score > outputs[winner_ref]["score"]:
            winner_ref = ref
    winner = source["candidates"][winner_ref]
    group_name = "杯子" if winner["semantic_group"] == "cup" else "排球"
    tendency = ("一级倾向" if source["recent_performance"]["tendency_level"]
                == "level_1" else "二级倾向")
    height = round(winner["target_facts"]["support_height_mm"])
    summary = (
        f"最终选择{winner['task_name']}；得分{outputs[winner_ref]['score']:.3f}；"
        f"患者能力{source['patient_ability']['level']}级；{tendency}；"
        f"医嘱逐任务评分；目标为{group_name}；支撑高度{height}毫米。"
    )
    return {
        "schema_version": "rehab_task_decision_output.v4",
        "decision_id": source["decision_binding"]["decision_id"],
        "advice_basis": {"direction": direction, "evidence": evidence},
        "candidates": outputs, "winner_ref": winner_ref, "summary": summary,
    }


class FinalScoringValidatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="final_scoring_")
        cls.exe = Path(cls.temp.name) / "validator.exe"
        cjson = Path(r"C:\esp\v5.5.4\frameworks\esp-idf-v5.5.4\components\json\cJSON")
        env = os.environ.copy()
        env["PATH"] = r"C:\msys64\ucrt64\bin;" + env["PATH"]
        subprocess.run([
            r"C:\msys64\ucrt64\bin\gcc.exe", "-std=gnu11", "-Wall",
            "-Wextra", "-Werror", "-I", str(SRC), "-I",
            str(COMPONENT / "test/stage4a_host_stubs"), "-I", str(cjson),
            str(COMPONENT / "test/stage4a_scoring_validator_host.c"),
            str(SRC / "rehab_task_decision_scoring.c"),
            str(SRC / "rehab_task_decision_preparation.c"),
            str(SRC / "rehab_task_decision_policy.c"), str(cjson / "cJSON.c"),
            "-lm", "-o", str(cls.exe),
        ], check=True, capture_output=True, text=True, env=env)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def validate(self, source: dict, output: dict) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="final_scoring_case_") as folder:
            ip = Path(folder) / "input.json"
            op = Path(folder) / "output.json"
            ip.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
            op.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
            return subprocess.run([str(self.exe), str(ip), str(op)],
                                  capture_output=True, text=True, encoding="utf-8")

    def assert_valid(self, source: dict, output: dict) -> None:
        result = self.validate(source, output)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_invalid(self, source: dict, output: dict, token: str) -> None:
        result = self.validate(source, output)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(token, result.stdout)

    def test_prepared_values_reject_deterministic_divergence(self) -> None:
        source = make_input(previous_task=3, compensation_count=3)
        output = make_output(source)
        first = output["candidates"][0]
        self.assertAlmostEqual(first["score_breakdown"]["compensation"], 4)
        first["score_breakdown"].update({
            "difficulty": 12.5,
            "compensation": 15.2,
            "frequency": 7.5,
            "perception": 6.5,
            "final_raw": 77,
        })
        first["score"] = 90
        output["candidates"][1]["score"] = 70
        output["winner_ref"] = 1
        self.assert_invalid(source, output, "prepared_mismatch")

        semantic_choice = make_output(
            source, advice_scores={0: 17, 1: 24, 3: 21, 4: 23}
        )
        self.assert_valid(source, semantic_choice)

    def test_validator_does_not_reselect_deepseek_winner(self) -> None:
        source = make_input()
        output = make_output(source)
        available_refs = [
            candidate["candidate_ref"]
            for candidate in output["candidates"]
            if candidate["availability"] == "available"
        ]
        lower_ref = next(ref for ref in available_refs
                         if ref != output["winner_ref"])
        output["winner_ref"] = lower_ref
        self.assert_valid(source, output)

    def test_numeric_contract_ranges_remain_strict(self) -> None:
        source = make_input()
        cases = {
            "difficulty_low": ("difficulty", -0.1, "difficulty_range"),
            "difficulty_high": ("difficulty", 35.1, "difficulty_range"),
            "compensation_low": ("compensation", -0.1, "compensation_range"),
            "compensation_high": ("compensation", 20.1, "compensation_range"),
            "frequency_low": ("frequency", -0.1, "frequency_range"),
            "frequency_high": ("frequency", 10.1, "frequency_range"),
            "perception_low": ("perception", 5.9, "perception_range"),
            "perception_high": ("perception", 10.1, "perception_range"),
        }
        for name, (field, value, token) in cases.items():
            with self.subTest(name=name):
                output = make_output(source)
                output["candidates"][0]["score_breakdown"][field] = value
                self.assert_invalid(source, output, token)
        for field, value in (("final_raw", -100.1), ("final_raw", 200.1)):
            with self.subTest(field=field, value=value):
                output = make_output(source)
                output["candidates"][0]["score_breakdown"][field] = value
                self.assert_invalid(source, output, "score_breakdown.range")
        for value in (-0.1, 100.1):
            with self.subTest(score=value):
                output = make_output(source)
                output["candidates"][0]["score"] = value
                self.assert_invalid(source, output, "score_breakdown.range")

    def test_scenarios_1_2a_2b_3(self) -> None:
        scenarios = [
            ("scenario_1", make_input(
                tendency=2, advice="无专项医嘱", cup_count=0, ball_count=3),
             "neutral", "无专项医嘱", 1),
            ("scenario_2a", make_input(
                tendency=2, advice="无专项医嘱", cup_count=1, ball_count=2,
                previous_task=1, compensation_count=2),
             "neutral", "无专项医嘱", 4),
            ("scenario_2b", make_input(
                tendency=2, advice="无专项医嘱", cup_count=1, ball_count=2,
                previous_task=1, compensation_count=2, blocked_refs={4}),
             "neutral", "无专项医嘱", 3),
            ("scenario_3", make_input(
                tendency=2, advice="训练侧重腕部", cup_count=1, ball_count=1),
             "cup", "训练侧重腕部", 1),
        ]
        for name, source, direction, evidence, winner in scenarios:
            with self.subTest(name=name):
                output = make_output(source, direction, evidence)
                self.assertEqual(output["winner_ref"], winner)
                self.assert_valid(source, output)

    def test_r1_to_r6_business_properties(self) -> None:
        # R1: explicit cup advice can be overcome by severe exact-task
        # compensation plus frequency; advice is important, not a command.
        r1 = make_input(tendency=2, cup_count=3, ball_count=0,
                        previous_task=1, compensation_count=3)
        self.assertEqual(make_output(r1, "cup", EVIDENCE)["winner_ref"], 4)

        # R2: under ordinary equal facts, the nine-point advice gap is visible.
        r2 = make_input(tendency=2, cup_count=1, ball_count=1)
        r2o = make_output(r2, "cup", EVIDENCE)
        self.assertEqual(r2o["winner_ref"], 1)
        self.assertEqual(
            r2o["candidates"][1]["score_breakdown"]["advice"] -
            r2o["candidates"][4]["score_breakdown"]["advice"], 9)

        # R3: the entire perception range is four points and cannot erase an
        # eight-point easier-level difficulty gap.
        high = exact_components(make_input(geometry_confidence=1.0),
                                make_input(geometry_confidence=1.0)["candidates"][1],
                                "neutral")
        low_source = make_input(geometry_confidence=0.0,
                                detection_confidence=0.0,
                                height_confidence=0.0)
        low = exact_components(low_source, low_source["candidates"][0], "neutral")
        self.assertLessEqual(high["perception"] - low["perception"], 4.0)
        self.assertGreater(high["difficulty"] - low["difficulty"], 4.0)

        # R4: frozen tendency deltas have strong, asymmetric separation.
        r4 = make_input(ability=3, tendency=2)
        self.assertEqual([exact_components(r4, r4["candidates"][i], "neutral")
                          ["difficulty"] for i in (0, 1, 2)], [27, 35, 14])

        # R5: a high-scoring candidate blocked by a hard gate remains 0/null.
        r5 = make_input(blocked_refs={4}, previous_task=1,
                        compensation_count=2)
        r5o = make_output(r5, "neutral", "无专项医嘱")
        self.assertEqual(r5o["candidates"][4]["score"], 0)
        self.assertIsNone(r5o["candidates"][4]["score_breakdown"])

        # R6: nearby confidence facts yield nearby, continuous scores.
        a = make_input(geometry_confidence=0.80)
        b = make_input(geometry_confidence=0.79)
        sa = exact_components(a, a["candidates"][1], "neutral")["perception"]
        sb = exact_components(b, b["candidates"][1], "neutral")["perception"]
        self.assertAlmostEqual(sa - sb, 0.02, places=9)

    def test_parameter_grids_and_compatibility_fallback(self) -> None:
        severity_scores = {0: 20.0, 1: 14.0, 2: 2.0, 3: 0.0, 7: 0.0}
        for count, expected in severity_scores.items():
            source = make_input(previous_task=1, compensation_count=count)
            self.assertAlmostEqual(
                exact_components(source, source["candidates"][1], "neutral")
                ["compensation"], expected)

        source = make_input(previous_task=0, compensation_count=3)
        # exact, same action, same object, unrelated
        self.assertEqual([round(value, 9) for value in [
            exact_components(source, source["candidates"][i], "neutral")
            ["compensation"] for i in (0, 3, 1, 2)
        ]], [0.0, 4.0, 8.0, 8.0])
        self.assertAlmostEqual(
            exact_components(source, source["candidates"][4], "neutral")
            ["compensation"], 4.0)
        unrelated = make_input(previous_task=1, compensation_count=3)
        self.assertEqual(
            exact_components(unrelated, unrelated["candidates"][3], "neutral")
            ["compensation"], 20.0)
        unknown = make_input(previous_task=1, compensation_count=3)
        unknown["latest_compensation"].update({
            "exact_task_id": None, "action_family": None,
            "mapped_group": None, "mapping_status": "unknown_task",
        })
        self.assertEqual(
            exact_components(unknown, unknown["candidates"][1], "neutral")
            ["compensation"], 20.0)
        self.assert_valid(unknown, make_output(unknown))

        for count, expected in enumerate((10, 6, 5, 0)):
            source = make_input(cup_count=count)
            self.assertEqual(
                exact_components(source, source["candidates"][1], "neutral")
                ["frequency"], expected)

        for tendency in (1, 2):
            source = make_input(ability=3, tendency=tendency)
            self.assert_valid(source, make_output(source))
        for direction, evidence in (("cup", EVIDENCE),
                                    ("ball", "建议加强排球训练"),
                                    ("neutral", "无专项医嘱")):
            source = make_input(advice=evidence)
            self.assert_valid(source, make_output(source, direction, evidence))
        for ability in (1, 2, 3):
            source = make_input(ability=ability)
            self.assert_valid(source, make_output(source))

        present = make_input(geometry_confidence=0.8)
        missing = make_input(geometry_confidence=None)
        malformed = make_input(geometry_confidence=None)
        malformed["candidates"][1]["target_facts"]["geometry_confidence"] = "bad"
        clamped = make_input(geometry_confidence=2.0,
                             detection_confidence=-1.0,
                             height_confidence=3.0)
        self.assertAlmostEqual(
            exact_components(present, present["candidates"][1], "neutral")
            ["perception"], 9.48)
        self.assertAlmostEqual(
            exact_components(missing, missing["candidates"][1], "neutral")
            ["perception"], 9.76)
        self.assertAlmostEqual(
            exact_components(malformed, malformed["candidates"][1], "neutral")
            ["perception"], 9.76)
        self.assertAlmostEqual(
            exact_components(clamped, clamped["candidates"][1], "neutral")
            ["perception"], 8.8)
        for source in (present, missing, malformed, clamped):
            self.assert_valid(source, make_output(source))

    def test_schema_blocking_tie_and_prompt_contracts(self) -> None:
        source = make_input(ability=1, advice="无专项医嘱",
                            cup_count=0, ball_count=0)
        output = make_output(source, "neutral", "无专项医嘱")
        self.assertEqual(output["winner_ref"], 0)
        self.assert_valid(source, output)
        reordered = copy.deepcopy(output)
        reordered["candidates"].reverse()
        self.assert_invalid(source, reordered, "reordered")
        forbidden = copy.deepcopy(output)
        forbidden["candidates"][0]["base"] = 55
        self.assert_invalid(source, forbidden, "forbidden")

        coordinator = (SRC / "rehab_task_decision_coordinator.c").read_text(
            encoding="utf-8")
        scoring = (SRC / "rehab_task_decision_scoring.c").read_text(
            encoding="utf-8")
        self.assertIn("decision_preparation", coordinator)
        self.assertNotIn("difficulty starts at 35", coordinator)
        self.assertNotIn("compensation=20*(1-severity*relatedness)", coordinator)
        self.assertIn("geometry_confidence is unavailable or malformed", scoring)
        self.assertNotIn("base=55 exactly", coordinator)
        self.assertNotIn('"height":{"type":"number"', coordinator)
        self.assertNotIn("apply_local_constraints", coordinator)
        self.assertNotIn("expected_components", scoring)
        self.assertIn('"component.compensation_range"', scoring)
        self.assertNotIn('"winner_ref.not_highest"', scoring)

    def test_formal_input_authority_text_matches_machine_boundary(self) -> None:
        scoring = (SRC / "rehab_task_decision_scoring.c").read_text(
            encoding="utf-8")
        header = (SRC / "rehab_task_decision_scoring.h").read_text(
            encoding="utf-8")
        skill = (
            COMPONENT / "skills/rehab-task-decision/SKILL.md"
        ).read_text(encoding="utf-8")

        self.assertIn("P4 is the authority", scoring)
        self.assertIn("deterministic preparation", scoring)
        self.assertIn("DeepSeek remains the business authority", scoring)
        self.assertNotIn('add_number(policy, "validation_tolerance"', scoring)
        self.assertIn("validator independently recomputes", scoring)

        self.assertIn("Validate output structure/schema", header)
        self.assertIn("prepared-value consistency", header)
        self.assertIn("P4 preparation 不生成 advice", skill)
        self.assertIn("不要重新累计四个 frozen components", skill)

    def test_latest_unknown_does_not_hide_previous_mapped_compensation(self) -> None:
        context = (SRC / "rehab_task_decision_context.c").read_text(
            encoding="utf-8")
        self.assertIn(
            "if (mapped_task != NULL &&\n"
            "                !recent->compensation_available)",
            context,
        )
        self.assertNotIn(
            "if (!recent->compensation_available)",
            context,
        )
        for field in (
            "compensation_task_id", "compensation_action_family",
            "compensation_group", "compensation_action_count",
        ):
            self.assertIn(field, context)

        task_map = {
            "legacy unknown task": None,
            TASKS[1][1]: {
                "task_id": TASKS[1][0],
                "action_family": TASKS[1][5],
                "group": TASKS[1][3],
            },
        }
        newest_first = [
            ("legacy unknown task", 3),
            (TASKS[1][1], 2),
        ]
        authority = next(
            (task_map[name], count) for name, count in newest_first
            if task_map[name] is not None
        )
        self.assertEqual(authority[0]["task_id"], "OT_CUP_DRINK_L2")
        self.assertEqual(authority[0]["action_family"], "drink_water")
        self.assertEqual(authority[0]["group"], "cup")
        self.assertEqual(authority[1], 2)

    def test_production_prompt_locks_per_candidate_advice_semantics(self) -> None:
        coordinator = (SRC / "rehab_task_decision_coordinator.c").read_text(
            encoding="utf-8")
        scoring = (SRC / "rehab_task_decision_scoring.c").read_text(
            encoding="utf-8")
        self.assertIn("Interpret meaning rather than matching a", coordinator)
        self.assertIn("Use 20 when the prescription has no", coordinator)
        self.assertIn("21..25 only for actual positive", coordinator)
        self.assertIn("16..19", coordinator)
        self.assertIn("Lack of positive support is not negative evidence",
                      coordinator)
        self.assertIn("encouraging A must not", coordinator)
        self.assertIn("automatically lower an unrelated B", coordinator)
        self.assertIn("A soft negative preference must not hard-block",
                      coordinator)
        self.assertIn("not implement a second natural-language classifier",
                      coordinator)
        self.assertIn(
            '"not output advice_interpretation. " '
            "LLM_ADVICE_SEMANTICS_TEXT",
            coordinator,
        )
        self.assertIn(
            'root, "advice_semantics", LLM_ADVICE_SEMANTICS_TEXT',
            coordinator,
        )
        self.assertIn("independent integer advice score in [16,25]", scoring)
        self.assertNotIn('add_number(parameters, "advice_match"', scoring)
        self.assertNotIn('add_number(parameters, "advice_neutral"', scoring)
        self.assertNotIn('add_number(parameters, "advice_mismatch"', scoring)
        self.assertIn("hard_block_evidence_has_provenance", scoring)
        self.assertNotIn("explicit_prohibition_evidence", scoring)
        self.assertNotIn("medical_prohibition_matches", scoring)
        self.assertNotIn("available_reason_matches", scoring)
        self.assertNotIn("summary_matches", scoring)
        self.assertNotIn("blocked_reason_matches", scoring)
        self.assertIn("input-blocked candidate can never become available",
                      coordinator)
        self.assertNotIn("maps matching/neutral/mismatching group exactly",
                         coordinator)

    def test_generic_advice_calibration_scale_c1_through_c7(self) -> None:
        """Calibrate model-owned semantics without implementing a word parser."""
        cases = [
            # C1: only A positive; unrelated B remains neutral.
            {"name": "C1", "scores": {3: 24, 0: 20}},
            # C2: only A soft-negative; unrelated B remains neutral.
            {"name": "C2", "scores": {1: 17, 4: 20}},
            # C3: A positive, B negative, and unrelated C neutral.
            {"name": "C3", "scores": {3: 23, 1: 18, 0: 20}},
            # C4: A hard-prohibited, B positive, and unrelated C neutral.
            {"name": "C4", "scores": {1: 24, 0: 20}, "blocked": {3}},
            # C5: no candidate-specific direction; all available are neutral.
            {"name": "C5", "scores": {}},
            # C6: same object group, but only the target action is positive.
            {"name": "C6", "scores": {1: 23, 0: 20}},
            # C7: same object group, but only the target action is negative.
            {"name": "C7", "scores": {1: 17, 0: 20}},
        ]
        for case in cases:
            with self.subTest(case=case["name"]):
                prescription = f"abstract semantic fixture {case['name']}"
                source = make_input(ability=3, advice=prescription)
                blocked = case.get("blocked", set())
                output = make_output(
                    source,
                    "neutral",
                    prescription,
                    advice_scores=case["scores"],
                    evidences={ref: prescription for ref in blocked},
                    medical_blocks=blocked,
                )
                self.assert_valid(source, output)
                available_advice = {
                    item["candidate_ref"]: item["score_breakdown"]["advice"]
                    for item in output["candidates"]
                    if item["availability"] == "available"
                }
                for ref, expected in case["scores"].items():
                    self.assertEqual(available_advice[ref], expected)

                if case["name"] == "C1":
                    self.assertGreater(available_advice[3], 20)
                    self.assertEqual(available_advice[0], 20)
                elif case["name"] == "C2":
                    self.assertLess(available_advice[1], 20)
                    self.assertEqual(available_advice[4], 20)
                elif case["name"] == "C3":
                    self.assertGreater(available_advice[3], 20)
                    self.assertLess(available_advice[1], 20)
                    self.assertEqual(available_advice[0], 20)
                elif case["name"] == "C4":
                    blocked_item = output["candidates"][3]
                    self.assertEqual(blocked_item["availability"], "blocked")
                    self.assertEqual(blocked_item["score"], 0)
                    self.assertIsNone(blocked_item["score_breakdown"])
                    self.assertGreater(available_advice[1], 20)
                    self.assertEqual(available_advice[0], 20)
                elif case["name"] == "C5":
                    self.assertTrue(available_advice)
                    self.assertTrue(all(value == 20
                                        for value in available_advice.values()))
                elif case["name"] == "C6":
                    self.assertGreater(available_advice[1], 20)
                    self.assertEqual(available_advice[0], 20)
                elif case["name"] == "C7":
                    self.assertLess(available_advice[1], 20)
                    self.assertEqual(available_advice[0], 20)

    def test_per_candidate_positive_negative_and_mixed_advice(self) -> None:
        cases = [
            ("多锻炼喝水动作", {0: 19, 1: 25, 3: 20, 4: 20}, 1),
            ("少做喝水动作", {0: 19, 1: 17, 3: 20, 4: 20}, 4),
            ("多做球类运动", {0: 20, 1: 20, 3: 24, 4: 25}, 4),
            ("少做球类运动", {0: 20, 1: 20, 3: 17, 4: 16}, 1),
            ("多做球类运动，但少做喝水动作",
             {0: 19, 1: 16, 3: 24, 4: 25}, 4),
        ]
        for advice, scores, winner in cases:
            with self.subTest(advice=advice):
                source = make_input(advice=advice)
                output = make_output(
                    source, "mixed" if "但" in advice else "neutral",
                    advice, advice_scores=scores,
                )
                self.assertEqual(output["winner_ref"], winner)
                self.assert_valid(source, output)
        mixed = make_output(
            make_input(advice=cases[-1][0]), "mixed", cases[-1][0],
            advice_scores=cases[-1][1],
        )
        self.assertGreater(mixed["candidates"][3]["score_breakdown"]["advice"],
                           mixed["candidates"][1]["score_breakdown"]["advice"])

    def test_hard_block_is_one_way_and_semantics_are_llm_owned(self) -> None:
        advice = "禁止球类训练，建议多练喝水动作"
        source = make_input(advice=advice)
        output = make_output(
            source, "mixed", advice,
            advice_scores={0: 20, 1: 25},
            evidences={3: "禁止球类训练", 4: "禁止球类训练"},
            medical_blocks={3, 4},
        )
        self.assert_valid(source, output)
        self.assertEqual(output["candidates"][3]["score"], 0)
        self.assertEqual(output["candidates"][1]["score_breakdown"]["advice"], 25)

        # Validator deliberately does not infer prohibition strength from words.
        # Given a structurally valid LLM hard-block decision with source
        # provenance, arbitrary source wording is accepted.
        semantic_source = make_input(advice="Clinical directive alpha")
        semantic_output = make_output(
            semantic_source, "neutral", "Clinical directive alpha",
            medical_blocks={3}, evidences={3: "Clinical directive alpha"},
        )
        semantic_output["candidates"][3]["reason"] = "Handled by model semantics."
        self.assert_valid(semantic_source, semantic_output)

        unblocked = make_output(source, "mixed", advice)
        unblocked["candidates"][2] = copy.deepcopy(unblocked["candidates"][1])
        unblocked["candidates"][2]["candidate_ref"] = 2
        self.assert_invalid(source, unblocked, "availability")

    def test_hard_block_provenance_and_advice_range_are_strict(self) -> None:
        advice = "source span alpha"
        source = make_input(advice=advice)
        valid = make_output(source, "mixed", advice,
                            medical_blocks={3}, evidences={3: advice})
        self.assert_valid(source, valid)

        forged = copy.deepcopy(valid)
        forged["candidates"][3]["advice_evidence"] = "forged source span"
        forged["candidates"][3]["reason"] = "Independent explanation."
        self.assert_invalid(source, forged, "provenance")

        no_keyword_source = make_input(advice="opaque clinical span")
        no_keyword = make_output(
            no_keyword_source, "neutral", "opaque clinical span",
            medical_blocks={3}, evidences={3: "opaque clinical span"},
        )
        no_keyword["candidates"][3]["reason"] = "No canonical tokens here."
        self.assert_valid(no_keyword_source, no_keyword)

        for value in (15, 26, 20.5):
            with self.subTest(value=value):
                output = make_output(source, "neutral", advice)
                item = output["candidates"][0]
                old = item["score_breakdown"]["advice"]
                delta = value - old
                item["score_breakdown"]["advice"] = value
                item["score_breakdown"]["final_raw"] += delta
                item["score"] += delta
                self.assert_invalid(source, output, "advice_range")

    def test_presentation_invariance_accepts_24_text_variants(self) -> None:
        source = make_input(ability=2, advice="source advice text")
        base_variants = [
            "简短说明",
            "这是较长的中文说明，语序与旧模板完全不同，也不复述任何结构化数值。",
            "Readable explanation only.",
            "中英 mixed explanation — concise.",
            "No legacy vocabulary appears here!",
            "标点；可以：不同？！",
            "先说结论，再补充解释。",
            "Explanation reordered; still valid UTF-8.",
        ]
        rng = random.Random(20260811)
        generated = [
            f"variant-{index}-{rng.randrange(100000, 999999)}-自由文本"
            for index in range(16)
        ]
        variants = base_variants + generated
        self.assertGreaterEqual(len(variants), 20)
        for index, text in enumerate(variants):
            with self.subTest(index=index, text=text):
                output = make_output(source, "mixed", "source advice text",
                                     advice_scores={0: 18, 1: 24, 3: 21, 4: 22})
                output["summary"] = text
                output["advice_basis"]["evidence"] = f"audit {text}"
                for ref, candidate in enumerate(output["candidates"]):
                    candidate["reason"] = f"{text} / reason {ref}"
                    candidate["advice_evidence"] = f"soft audit {index}-{ref}"
                self.assert_valid(source, output)

    def test_machine_invariant_single_field_mutations_reject(self) -> None:
        base_source = make_input(ability=1, blocked_refs={4})

        def force_available(source: dict, output: dict, ref: int) -> None:
            candidate = source["candidates"][ref]
            breakdown = exact_components(source, candidate, 20)
            breakdown["final_raw"] = sum(breakdown.values())
            output["candidates"][ref] = {
                "candidate_ref": ref,
                "availability": "available",
                "advice_evidence": "presentation",
                "score_breakdown": breakdown,
                "score": round(min(100.0, breakdown["final_raw"]), 3),
                "reason": "Readable presentation.",
            }

        mutations = [
            "decision_id", "missing_candidate_ref", "duplicate_candidate_ref",
            "input_blocked_to_available", "blocked_score", "blocked_breakdown",
            "scene_binding", "advice_15", "advice_26", "advice_fraction",
        ]
        for name in mutations:
            with self.subTest(name=name):
                source = copy.deepcopy(base_source)
                output = make_output(source)
                if name == "decision_id":
                    output["decision_id"] = "wrong-decision"
                elif name == "missing_candidate_ref":
                    del output["candidates"][0]["candidate_ref"]
                elif name == "duplicate_candidate_ref":
                    output["candidates"][1]["candidate_ref"] = 0
                elif name == "input_blocked_to_available":
                    force_available(source, output, 1)
                elif name == "blocked_score":
                    output["candidates"][1]["score"] = 1
                elif name == "blocked_breakdown":
                    output["candidates"][1]["score_breakdown"] = {}
                elif name == "scene_binding":
                    source["decision_binding"]["decision_id"] = "other-scene-binding"
                else:
                    value = {"advice_15": 15, "advice_26": 26,
                             "advice_fraction": 20.5}[name]
                    item = output["candidates"][0]
                    delta = value - item["score_breakdown"]["advice"]
                    item["score_breakdown"]["advice"] = value
                    item["score_breakdown"]["final_raw"] += delta
                    item["score"] += delta
                result = self.validate(source, output)
                self.assertNotEqual(result.returncode, 0,
                                    f"{name} unexpectedly accepted: {result.stdout}")

    def test_hard_medical_block_structural_contract_h1_to_h8(self) -> None:
        evidence = "clinical source fragment"
        source = make_input(advice=f"prefix {evidence} suffix")
        h1 = make_output(source, "mixed", "audit metadata",
                         medical_blocks={3}, evidences={3: evidence})
        h1["candidates"][3]["reason"] = "任意非空说明。"
        self.assert_valid(source, h1)  # H1

        h2 = copy.deepcopy(h1)
        h2["candidates"][3]["reason"] = "This does not repeat source evidence."
        self.assert_valid(source, h2)  # H2

        h3 = copy.deepcopy(h1)
        h3["candidates"][3]["reason"] = "Scope handled semantically."
        self.assert_valid(source, h3)  # H3: no canonical group token

        h4 = copy.deepcopy(h1)
        h4["candidates"][3]["advice_evidence"] = "not in original raw text"
        self.assert_invalid(source, h4, "provenance")

        h5_source = make_input(ability=1)
        h5 = make_output(h5_source)
        candidate = h5_source["candidates"][1]
        breakdown = exact_components(h5_source, candidate, 20)
        breakdown["final_raw"] = sum(breakdown.values())
        h5["candidates"][1] = {
            "candidate_ref": 1, "availability": "available",
            "advice_evidence": "anything", "score_breakdown": breakdown,
            "score": round(breakdown["final_raw"], 3), "reason": "Readable.",
        }
        self.assert_invalid(h5_source, h5, "availability")

        h6 = copy.deepcopy(h1)
        h6["candidates"][3]["score"] = 1
        self.assert_invalid(source, h6, "blocked_contract")

        h7 = copy.deepcopy(h1)
        h7["candidates"][3]["score_breakdown"] = {}
        self.assert_invalid(source, h7, "blocked_contract")

        h8 = copy.deepcopy(h1)
        h8["winner_ref"] = 3
        self.assert_invalid(source, h8, "winner_ref.invalid")

    def test_business_advice_varies_while_prepared_values_remain_exact(self) -> None:
        source = make_input(advice="建议多喝水")
        output = make_output(source, "neutral", "建议多喝水",
                             advice_scores={0: 19, 1: 25})
        self.assert_valid(source, output)
        self.assertEqual(output["candidates"][2]["availability"], "blocked")
        self.assertEqual(output["candidates"][2]["score"], 0)

        authority = copy.deepcopy(output)
        authority["candidates"][0]["score_breakdown"].update({
            "difficulty": 0, "compensation": 15.2, "frequency": 0,
            "perception": 6, "final_raw": 9,
        })
        authority["candidates"][0]["score"] = 90
        authority["candidates"][1]["score"] = 70
        authority["winner_ref"] = 1
        self.assert_invalid(source, authority, "prepared_mismatch")

        selected_source = (SRC / "rehab_task_decision_selected_task.c").read_text(
            encoding="utf-8")
        self.assertIn('"rehab_selected_task.v2"', selected_source)
        self.assertNotIn("highest_index", selected_source)
        self.assertIn("return selected_seen;", selected_source)
        self.assertIn("score->valuedouble - selected_candidate_score",
                      selected_source)


if __name__ == "__main__":
    unittest.main()
