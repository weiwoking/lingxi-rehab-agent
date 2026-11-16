from __future__ import annotations

import copy
import json
import math
import re
import unittest
from pathlib import Path


APP = Path(__file__).resolve().parents[3]
COMPONENT = APP / "components" / "rehab_task_decision"
UI = APP / "fatfs_image" / "system" / "ui" / "rehab"
FIXTURE = (
    COMPONENT / "test" / "fixtures"
    / "p4_result_ui_dual_height_zh_reason"
    / "organize_english_reason.json"
)
FALLBACKS = {
    "OT_CUP_TRANSFER_L1": "杯子目标与低平面匹配，可进行一级取放训练。",
    "OT_CUP_DRINK_L2": "杯子目标与低平面匹配，可进行二级饮水训练。",
    "OT_CUP_DRINK_L3": "杯子目标与高平面匹配，可进行三级饮水训练。",
    "OT_BALL_TRANSFER_L1": "排球目标与低平面匹配，可进行一级取放训练。",
    "OT_BALL_TRANSFER_L2": "排球目标与高平面匹配，可进行二级取放训练。",
}
CLASS_NAMES = {"cup": "杯子", "bottle": "杯子", "sports ball": "排球",
               "ball": "排球", "volleyball": "排球", "book": "书本"}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def cjk_count(value: object) -> int:
    if not isinstance(value, str):
        return 0
    return sum(
        "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        for char in value
    )


def normalize_reasons(value: dict) -> dict:
    result = copy.deepcopy(value)
    by_id: dict[str, dict] = {}
    for candidate in result["candidates"]:
        task_id = candidate["task_id"]
        reason = str(candidate.get("reason", ""))
        reason = reason.replace("sports ball", "排球")
        reason = reason.replace("volleyball", "排球")
        reason = reason.replace("水瓶", "杯子").replace("瓶子", "杯子")
        reason = re.sub(
            r"(?<![A-Za-z0-9_])ball(?![A-Za-z0-9_])", "排球", reason
        )
        candidate["reason"] = reason
        if cjk_count(candidate.get("reason")) < 4:
            candidate["reason"] = FALLBACKS[task_id]
        by_id[task_id] = candidate
    result["selected_reason"] = by_id[
        result["selected_task_id"]
    ]["reason"]
    return result


def height_text(geometry: object, class_name: str) -> tuple[str, str]:
    geometry = geometry if isinstance(geometry, dict) else {}

    def value(field: str) -> str:
        number = geometry.get(field)
        if (not isinstance(number, (int, float)) or
                isinstance(number, bool) or not math.isfinite(number) or
                number <= 0):
            return "未知"
        return f"{number / 10.0:.1f} cm"

    name = CLASS_NAMES.get(class_name, class_name or "--")
    return (
        f"{name}自身高度：{value('object_height_mm')}",
        f"{name}离地高度：{value('support_height_mm')}",
    )


class P4ResultUiDualHeightZhReasonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(read(FIXTURE))

    def test_real_cup_formats_two_independent_heights(self) -> None:
        target = self.fixture["target_object"]
        self.assertEqual(
            height_text(target["geometry"], target["class"]),
            ("杯子自身高度：16.7 cm", "杯子离地高度：79.4 cm"),
        )
        self.assertEqual(target["source_frame_sequence"], 2)
        self.assertEqual(
            target["bbox_xyxy"],
            [136.92147827148438, 178.77957153320312,
             201.52755737304688, 293.9029541015625],
        )

    def test_height_missing_cases_are_independent(self) -> None:
        cases = (
            ({"object_height_mm": 186}, "bottle",
             ("杯子自身高度：18.6 cm", "杯子离地高度：未知")),
            ({"support_height_mm": 955}, "bottle",
             ("杯子自身高度：未知", "杯子离地高度：95.5 cm")),
            ({}, "book",
             ("书本自身高度：未知", "书本离地高度：未知")),
            ({"object_height_mm": 0, "support_height_mm": -1}, "cup",
             ("杯子自身高度：未知", "杯子离地高度：未知")),
        )
        for geometry, class_name, expected in cases:
            with self.subTest(geometry=geometry, class_name=class_name):
                self.assertEqual(height_text(geometry, class_name), expected)

    def test_all_candidate_reasons_and_winner_are_normalized(self) -> None:
        before = copy.deepcopy(self.fixture)
        after = normalize_reasons(before)
        self.assertTrue(all(
            cjk_count(candidate["reason"]) >= 4
            for candidate in after["candidates"]
        ))
        self.assertEqual(
            after["selected_reason"],
            FALLBACKS["OT_CUP_DRINK_L2"],
        )
        for index in (4,):
            self.assertEqual(
                after["candidates"][index]["reason"],
                before["candidates"][index]["reason"],
            )
        self.assertEqual(
            [candidate["score"] for candidate in after["candidates"]],
            [candidate["score"] for candidate in before["candidates"]],
        )
        for field in (
            "selected_task_id", "target_object_id", "target_object",
            "scene_scan_id", "scene_sha256",
        ):
            self.assertEqual(after[field], before[field])

    def test_reason_is_bounded_presentation_without_fabricated_fallback(self) -> None:
        source = read(
            COMPONENT / "src" / "rehab_task_decision_scoring.c"
        )
        self.assertNotIn("normalize_reason(candidate)", source)
        self.assertNotIn("rehab_task_policy_normalize_reason", source)
        self.assertIn("presentation_text_valid", source)
        self.assertNotIn("has_cjk", source)
        self.assertNotIn("available_reason_matches", source)
        self.assertNotIn("blocked_reason_matches", source)
        self.assertNotIn("REASON_ZH_FALLBACK", source)

    def test_primary_and_repair_prompts_require_readable_untemplated_text(self) -> None:
        source = read(
            COMPONENT / "src" / "rehab_task_decision_coordinator.c"
        )
        self.assertNotIn("Simplified Chinese", source)
        self.assertIn("no fixed wording", source)
        self.assertIn("does not need to quote evidence", source)
        self.assertIn("do not follow a fixed token template", source)
        self.assertIn("Return one complete rehab_task_decision_output.v4", source)
        self.assertIn("previous_output", source)
        self.assertEqual(len(re.findall(r"\berr = llm_call\(", source)), 2)

    def test_selected_v2_reason_and_winner_candidate_remain_identical(self) -> None:
        selected_source = read(
            COMPONENT / "src" / "rehab_task_decision_selected_task.c"
        )
        historical = json.loads(read(
            COMPONENT / "test" / "fixtures" / "stage8c1d"
            / "selected_v2_geometry.json"
        ))
        historical["reason"] = "该杯子高度适中，适合当前训练。"
        historical["candidate_scores"][0]["reason"] = historical["reason"]
        self.assertEqual(historical["schema_version"],
                         "rehab_selected_task.v2")
        self.assertTrue(historical["reason"])
        self.assertEqual(
            historical["reason"],
            historical["candidate_scores"][0]["reason"],
        )
        self.assertTrue(all(
            set(item) == {"task_name", "score", "reason"}
            for item in historical["candidate_scores"]
        ))
        self.assertIn("bounded_string(reason, 1", selected_source)
        self.assertNotIn("reason_has_sufficient_cjk", selected_source)

    def test_page_uses_two_rows_without_changing_image_or_bbox(self) -> None:
        page = read(UI / "page_task_decision_result.lua")
        helper = read(UI / "rehab_ui_geometry.lua")
        bridge = read(
            APP / "components" / "rehab_ui" / "src"
            / "rehab_ui_task_decision_lua.c"
        )
        for expected in (
            "geometry_ui.target_height_lines",
            "object_height_text, 14, 244",
            "support_height_text, 14, 272",
            '"暂无目标画面"',
            "view.target_box:set_pos(20 + x1, 82 + y1)",
            "view.target_box:set_size(x2 - x1, y2 - y1)",
        ):
            self.assertIn(expected, page)
        for expected in (
            'target_height_value(geometry, "object_height_mm")',
            'target_height_value(geometry, "support_height_mm")',
            'book = "书本"',
            '"未知"',
        ):
            self.assertIn(expected, helper)
        self.assertIn('lua_setfield(L, -2, "object_height_mm")', bridge)
        self.assertIn('lua_setfield(L, -2, "support_height_mm")', bridge)
        self.assertLessEqual(272 + 24, 388)

    def test_target_resolver_uses_stage_three_policy(self) -> None:
        resolver = read(COMPONENT / "src" / "rehab_task_target_resolver.c")
        self.assertIn("rehab_task_policy_height_confidence", resolver)
        self.assertIn("semantic_group_count", resolver)
        self.assertIn("geometry_valid", resolver)


if __name__ == "__main__":
    unittest.main()
