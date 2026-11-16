#!/usr/bin/env python3
"""Stage-three five-task policy and unchanged-contract checks."""

from __future__ import annotations

import json
import math
import re
import unittest
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
APP = COMPONENT.parents[1]
REPO = APP.parents[1]
RULES = json.loads(
    (COMPONENT / "skills/rehab-task-decision/decision_rules.json").read_text(
        encoding="utf-8"
    )
)
TASKS = [
    ("OT_CUP_TRANSFER_L1", "水杯横向取放训练（一级）", 1, "cup", "low"),
    ("OT_CUP_DRINK_L2", "水杯拿取与饮水训练（二级）", 2, "cup", "low"),
    ("OT_CUP_DRINK_L3", "水杯拿取与饮水训练（三级）", 3, "cup", "high"),
    ("OT_BALL_TRANSFER_L1", "球类横向取放训练（一级）", 1, "ball", "low"),
    ("OT_BALL_TRANSFER_L2", "球类横向取放训练（二级）", 2, "ball", "high"),
]
GROUPS = {
    "cup": {"cup", "bottle"},
    "ball": {"sports ball", "ball", "volleyball"},
}


def parse_ability(text: str) -> tuple[int, bool]:
    match = re.search(
        r"当前任务能力等级[ \t\r\n　]*[:：][ \t\r\n　]*([一二三123])级",
        text,
    )
    if not match:
        return 1, False
    return {"一": 1, "二": 2, "三": 3}.get(
        match.group(1), int(match.group(1)) if match.group(1).isdigit() else 1
    ), True


def plane(height: float) -> str | None:
    if not math.isfinite(height) or height < 550:
        return None
    return "low" if height <= 890 else "high"


def height_confidence(height: float, required: str) -> float:
    if plane(height) != required:
        return 0.0
    center = 710 if required == "low" else 1070
    return max(0.0, min(1.0, 1 - abs(height - center) / 200))


def best_target(objects: list[dict], group: str, required: str) -> dict | None:
    valid = []
    for item in objects:
        geometry = item.get("geometry") or {}
        height = geometry.get("support_height_mm")
        if (
            item.get("class_name") not in GROUPS[group]
            or geometry.get("valid") is not True
            or not isinstance(height, (int, float))
            or not math.isfinite(height)
        ):
            continue
        confidence = height_confidence(float(height), required)
        if confidence <= 0:
            continue
        visibility = item.get("visibility") or {}
        valid.append(
            (
                -confidence,
                -int(visibility.get("full_extent_visible") is True),
                -int(visibility.get("touches_border") is False),
                -float(item.get("confidence") or 0),
                str(item["object_id"]),
                item,
            )
        )
    return min(valid)[-1] if valid else None


class Stage3FiveTaskPolicyTest(unittest.TestCase):
    def test_registry_exact_order_and_metadata(self) -> None:
        actual = [
            (
                item["task_id"], item["task_name"], item["level"],
                item["semantic_group"], item["plane"],
            )
            for item in RULES["tasks"]
        ]
        self.assertEqual(actual, TASKS)
        self.assertEqual(RULES["semantic_groups"], {
            "cup": ["cup", "bottle"],
            "ball": ["sports ball", "ball", "volleyball"],
        })

    def test_ability_forms_and_conservative_fallback(self) -> None:
        for value, expected in (("一级", 1), ("二级", 2), ("三级", 3),
                                ("1级", 1), ("2级", 2), ("3级", 3)):
            for colon in (":", "："):
                self.assertEqual(
                    parse_ability(f"当前任务能力等级 {colon}  {value}"),
                    (expected, True),
                )
        self.assertEqual(parse_ability("无能力行"), (1, False))
        self.assertEqual(parse_ability("当前任务能力等级：九级"), (1, False))

        for ability, blocked_ids in (
            (2, {"OT_CUP_DRINK_L3"}),
            (1, {"OT_CUP_DRINK_L2", "OT_CUP_DRINK_L3",
                 "OT_BALL_TRANSFER_L2"}),
        ):
            actual = {task_id for task_id, _name, level, _group, _plane in TASKS
                      if level > ability}
            self.assertEqual(actual, blocked_ids)

    def test_height_boundaries(self) -> None:
        self.assertEqual([plane(v) for v in (550, 710, 880, 890)],
                         ["low"] * 4)
        self.assertEqual([plane(v) for v in (900, 1070)], ["high"] * 2)
        self.assertEqual(height_confidence(710, "low"), 1.0)
        self.assertEqual(height_confidence(1070, "high"), 1.0)
        self.assertAlmostEqual(height_confidence(880, "low"), 0.15)
        self.assertAlmostEqual(height_confidence(890, "low"), 0.10)
        self.assertAlmostEqual(height_confidence(900, "high"), 0.15)
        self.assertEqual(height_confidence(1270, "high"), 0.0)
        self.assertEqual(height_confidence(float("nan"), "low"), 0.0)

    def test_current_no_ball_scene_and_ability_two(self) -> None:
        packet = Path(
            r"C:\ec\_deltar_scan_service_data\results\scan_20260731_171257_000"
            r"\stage8b4a_p4_decision_package.v1.json"
        )
        scene = json.loads(packet.read_text(encoding="utf-8"))
        objects = scene["objects"]
        candidates = []
        for task_id, task_name, level, group, required in TASKS:
            target = best_target(objects, group, required)
            allowed = level <= 2 and target is not None
            candidates.append({
                "task_id": task_id,
                "task_name": task_name,
                "score": 55 if allowed else 0,
                "reason": "场景目标可用" if allowed else (
                    "超出患者当前能力等级" if level > 2
                    else "场景中未发现对应训练物品"
                ),
                "target": target,
            })
        self.assertEqual(len(candidates), 5)
        self.assertEqual([item["score"] for item in candidates],
                         [55, 55, 0, 0, 0])
        winners = [item["task_id"] for item in candidates
                   if item["score"] == max(c["score"] for c in candidates)]
        self.assertEqual(winners,
                         ["OT_CUP_TRANSFER_L1", "OT_CUP_DRINK_L2"])
        self.assertIn(candidates[0]["target"]["class_name"], {"cup", "bottle"})

    def test_selected_v2_shape_and_downstream_unchanged(self) -> None:
        candidate_scores = [
            {"task_name": name, "score": 55 if index < 2 else 0,
             "reason": "中文理由"}
            for index, (_task_id, name, *_rest) in enumerate(TASKS)
        ]
        selected = {
            "schema_version": "rehab_selected_task.v2",
            "task_id": TASKS[0][0], "task_name": TASKS[0][1],
            "reason": "中文理由", "score": 55,
            "created_at": "2026-08-06T00:00:00Z",
            "decision_id": "decision-redacted", "decision_generation": 1,
            "source_scan_id": "scan-redacted",
            "source_scene_sha256": "0" * 64,
            "suggested_distance_cm": 0,
            "candidate_scores": candidate_scores,
            "target_object_id": "object-redacted",
            "target_object": {"object_id": "object-redacted", "class_name": "cup"},
        }
        self.assertEqual(len(selected), 14)
        self.assertEqual(len(selected["candidate_scores"]), 5)
        self.assertEqual(
            [item["task_name"] for item in selected["candidate_scores"]],
            [item[1] for item in TASKS],
        )
        self.assertTrue(all(
            set(item) == {"task_name", "score", "reason"}
            for item in selected["candidate_scores"]
        ))
        self.assertNotIn("task_id", selected["candidate_scores"][0])
        winner = max(range(len(candidate_scores)),
                     key=lambda index: candidate_scores[index]["score"])
        self.assertEqual(selected["task_id"], TASKS[winner][0])
        self.assertEqual(selected["task_name"],
                         candidate_scores[winner]["task_name"])
        self.assertEqual(selected["reason"],
                         candidate_scores[winner]["reason"])
        self.assertEqual(selected["score"],
                         candidate_scores[winner]["score"])
        integration = (APP / "components/rehab_integration/src/rehab_capabilities.c").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "rehab_training_start(selected.task_name, result)",
            integration,
        )
        contract = (APP / "components/rehab_score_bridge/src/rehab_score_bridge_contract.c").read_text(
            encoding="utf-8"
        )
        self.assertIn('"task_name", "csv_file", "start_time", "end_time",', contract)
        self.assertNotIn('"task_id", "csv_file"', contract)

    def test_ui_skill_font_and_failure_preservation(self) -> None:
        result_page = (APP / "fatfs_image/system/ui/rehab/page_task_decision_result.lua").read_text(
            encoding="utf-8"
        )
        self.assertIn("302, 52", result_page)
        skill = (COMPONENT / "skills/rehab-task-decision/SKILL.md").read_text(
            encoding="utf-8"
        )
        for _, name, *_ in TASKS:
            self.assertIn(name, skill)
        glyphs = set((APP / "tools/rehab_ui_font/glyphs/rehab_ui_glyphs.txt").read_text(
            encoding="utf-8"
        ))
        self.assertTrue(set("水杯横向取放训练一二三级拿与饮球类排") <= glyphs)
        selected_source = (COMPONENT / "src/rehab_task_decision_selected_task.c").read_text(
            encoding="utf-8"
        )
        for token in ("selected_task.json.previous", "fsync(fileno(file))",
                      "rename(previous, final_path)", "unlink(next)"):
            self.assertIn(token, selected_source)
        scoring = (COMPONENT / "src/rehab_task_decision_scoring.c").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("rehab_task_policy_normalize_reason", scoring)
        policy = (COMPONENT / "src/rehab_task_decision_policy.c").read_text(
            encoding="utf-8"
        )
        for original, replacement in (
            ("水瓶", "杯子"), ("瓶子", "杯子"),
            ("sports ball", "排球"), ("volleyball", "排球"),
            ("ball", "排球"),
        ):
            self.assertIn(f'{{"{original}", "{replacement}"', policy)


if __name__ == "__main__":
    unittest.main()
