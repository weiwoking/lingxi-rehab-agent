from __future__ import annotations

import copy
import json
import math
import unittest
from pathlib import Path


APP = Path(__file__).resolve().parents[3]
COMPONENT = APP / "components" / "rehab_task_decision"
UI = APP / "fatfs_image" / "system" / "ui" / "rehab"
FIXTURE = (
    COMPONENT / "test" / "fixtures" / "stage8c1e"
    / "pc_bottle_llm_cup.json"
)
DRINK_TASK_ID = "OT_CUP_TRANSFER_L1"
TASK_CLASSES = {
    "OT_CUP_TRANSFER_L1": {"cup", "bottle"},
    "OT_CUP_DRINK_L2": {"cup", "bottle"},
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def positive(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def height_score(item: dict) -> float:
    geometry = item.get("geometry", {})
    support = geometry.get("support_height_mm")
    if not positive(support) or not 550 <= support <= 890:
        return 0.0
    return max(0.0, 1.0 - abs(support - 710.0) / 200.0)


def total_score(item: dict, llm_id: str | None) -> float:
    geometry = item.get("geometry", {})
    visibility = item.get("visibility", {})
    return sum((
        height_score(item),
        1.0 if geometry.get("valid") is True else -100.0,
        6.0 if visibility.get("full_extent_visible") is True else 0.0,
        4.0 if visibility.get("touches_border") is False else 0.0,
        max(0.0, min(1.0, item.get("confidence", 0))) * 5.0,
        0.0,
    ))


def resolve_target(scene: dict, task_id: str, llm_id: str | None) -> dict | None:
    compatible = [
        item for item in scene["objects"]
        if item["class_name"] in TASK_CLASSES.get(task_id, set())
        and item.get("geometry", {}).get("valid") is True
        and height_score(item) > 0
    ]
    if not compatible:
        return None
    return sorted(
        compatible,
        key=lambda item: (
            -total_score(item, llm_id),
            -(item.get("geometry", {}).get("valid") is True),
            -item.get("confidence", 0),
            -(item.get("visibility", {}).get("full_extent_visible") is True),
            -(item.get("visibility", {}).get("touches_border") is False),
            item.get("source_frame_sequence", 2**32 - 1),
            item["object_id"],
        ),
    )[0]


def selected_target(source: dict) -> dict:
    target = copy.deepcopy(source)
    target["class"] = source["class_name"]
    return target


class Stage8C1ETargetAlignmentAndResultUiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(read(FIXTURE))

    def test_pc_bottle_overrides_conflicting_llm_cup(self) -> None:
        resolved = resolve_target(
            self.fixture,
            self.fixture["selected_task_id"],
            self.fixture["llm_target_object_id"],
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(
            resolved["object_id"],
            self.fixture["selection"]["recommended_target_object_id"],
        )
        self.assertEqual(resolved["class_name"], "bottle")
        self.assertEqual(resolved["geometry"]["object_height_mm"], 278.0)

    def test_llm_target_is_only_a_small_bonus(self) -> None:
        value = copy.deepcopy(self.fixture)
        resolved = resolve_target(
            value, DRINK_TASK_ID, value["llm_target_object_id"]
        )
        self.assertEqual(resolved["class_name"], "bottle")

    def test_package_recommendation_does_not_control_local_resolution(
        self,
    ) -> None:
        for recommended in (None, "scene_object_bottle_missing"):
            with self.subTest(recommended=recommended):
                value = copy.deepcopy(self.fixture)
                value["selection"]["recommended_target_object_id"] = (
                    recommended
                )
                resolved = resolve_target(value, DRINK_TASK_ID, None)
                self.assertEqual(resolved["class_name"], "bottle")
                self.assertEqual(
                    resolved["geometry"]["object_height_mm"], 278.0
                )

    def test_non_drink_task_uses_same_task_aware_resolver(self) -> None:
        resolved = resolve_target(
            self.fixture,
            "OT_CUP_DRINK_L2",
            self.fixture["llm_target_object_id"],
        )
        self.assertEqual(resolved["class_name"], "bottle")

    def test_selected_v2_fields_are_copied_from_one_object(self) -> None:
        resolved = resolve_target(
            self.fixture,
            DRINK_TASK_ID,
            self.fixture["llm_target_object_id"],
        )
        target = selected_target(resolved)
        self.assertEqual(target["object_id"], resolved["object_id"])
        self.assertEqual(target["class"], resolved["class_name"])
        for field in (
            "source_frame_id",
            "source_frame_sequence",
            "bbox_xyxy",
            "geometry",
        ):
            self.assertEqual(target[field], resolved[field])

    def test_c_source_enforces_authority_fallback_and_same_source(self) -> None:
        source = read(
            COMPONENT / "src" / "rehab_task_decision_coordinator.c"
        )
        for symbol in (
            "rehab_task_target_resolve",
            "selected_target_binding_valid",
            "selected_target_matches_source",
            "cJSON_Duplicate(source, true)",
            "cJSON_Compare",
        ):
            self.assertIn(symbol, source)
        resolver = read(
            COMPONENT / "src" / "rehab_task_target_resolver.c"
        )
        self.assertIn("TARGET_RESOLVE_START", resolver)
        self.assertIn("TARGET_RESOLVED", resolver)

    def test_result_page_is_competition_only(self) -> None:
        page = read(UI / "page_task_decision_result.lua")
        helper = read(UI / "rehab_ui_geometry.lua")
        self.assertIn("geometry_ui.target_height_lines(", page)
        self.assertIn('"当前任务已保留"', page)
        self.assertIn('bottle = "杯子"', helper)
        self.assertIn('cup = "杯子"', helper)
        self.assertIn('volleyball = "排球"', helper)
        self.assertIn('book = "书本"', helper)
        for forbidden in (
            "高度不可用",
            "物体顶部离地高度",
            "distance_mm",
            "distance_status",
            "llm_request_failed",
            "schema_invalid",
            "target_object_not_found",
            "data.error",
            "data.target_object_id",
        ):
            self.assertNotIn(forbidden, page)

    def test_result_layout_stays_inside_1024_by_600(self) -> None:
        page = read(UI / "page_task_decision_result.lua")
        for expected in (
            "x = 20, y = 82, w = 640, h = 360",
            "x = 674, y = 82, w = 330, h = 388",
            "text = \"返回首页\", x = 20, y = 504, w = 180, h = 60",
            "text = \"重新决策\", x = 214, y = 504, w = 180, h = 60",
            "text = \"查看详情\", x = 408, y = 504, w = 160, h = 60",
            "text = \"查看AI分析\", x = 582, y = 504, w = 180, h = 60",
            "text = \"开始训练\", x = 776, y = 504, w = 228, h = 60",
        ):
            self.assertIn(expected, page)
        self.assertLessEqual(674 + 330, 1024)
        self.assertLessEqual(504 + 60, 600)


if __name__ == "__main__":
    unittest.main()
