from __future__ import annotations

import copy
import importlib.util
import json
import math
import re
import unittest
from pathlib import Path


APP = Path(__file__).resolve().parents[3]
REPOSITORY = APP.parents[1]
COMPONENT = APP / "components" / "rehab_task_decision"
UI = APP / "fatfs_image" / "system" / "ui" / "rehab"
FONT = APP / "tools" / "rehab_ui_font"
FIXTURE = (
    COMPONENT / "test" / "fixtures" / "stage8c1d"
    / "selected_v2_geometry.json"
)
FIXED_REASON = (
    "该杯子高度适中，属于一级任务，与患者能力匹配，"
    "其余杯子高度过高，超出安全阈值"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def ui_height(geometry: object, field: str) -> str:
    if not isinstance(geometry, dict) or geometry.get("valid") is not True:
        return "高度不可用"
    value = geometry.get(field)
    if not isinstance(value, (int, float)) or not math.isfinite(value) or \
            value <= 0:
        return "--"
    return f"{value / 10.0:.1f} cm"


def load_production_text():
    path = FONT / "scripts" / "production_text.py"
    spec = importlib.util.spec_from_file_location("production_text", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("production_text module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Stage8C1DGeometryFontReasonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(read(FIXTURE))

    def test_geometry_contract_formats_expected_values(self) -> None:
        geometry = self.fixture["target_object"]["geometry"]
        self.assertEqual(ui_height(geometry, "object_height_mm"), "26.4 cm")
        self.assertEqual(ui_height(geometry, "support_height_mm"), "76.5 cm")
        self.assertEqual(
            ui_height(geometry, "top_height_above_floor_mm"), "102.9 cm"
        )
        self.assertEqual(self.fixture["suggested_distance_cm"], 0)

    def test_geometry_invalid_and_partial_values_never_render_zero_cm(
        self,
    ) -> None:
        self.assertEqual(ui_height(None, "object_height_mm"), "高度不可用")
        self.assertEqual(
            ui_height({"valid": False}, "object_height_mm"), "高度不可用"
        )
        for value in (None, 0, -1, math.nan, math.inf, -math.inf):
            self.assertEqual(
                ui_height({"valid": True, "object_height_mm": value},
                          "object_height_mm"),
                "--",
            )
        partial = {"valid": True, "support_height_mm": 765.412}
        self.assertEqual(ui_height(partial, "object_height_mm"), "--")
        self.assertEqual(ui_height(partial, "support_height_mm"), "76.5 cm")

    def test_typed_reader_and_capability_preserve_geometry(self) -> None:
        header = read(COMPONENT / "include" / "rehab_task_decision.h")
        selected = read(
            COMPONENT / "src" / "rehab_task_decision_selected_task.c"
        )
        bridge = read(
            APP / "components" / "rehab_ui" / "src"
            / "rehab_ui_task_decision_lua.c"
        )
        for field in (
            "target_geometry_available",
            "target_geometry_valid",
            "target_object_height_mm",
            "target_support_height_mm",
            "target_top_height_above_floor_mm",
            "target_distance_status",
        ):
            self.assertIn(field, header)
            self.assertIn(field, selected)
        self.assertIn("selected_geometry_from_target(target, value)", selected)
        self.assertIn('"target_geometry"', selected)
        self.assertIn('"target_geometry"', bridge)

    def test_home_and_result_use_shared_geometry_helper(self) -> None:
        helper = read(UI / "rehab_ui_geometry.lua")
        home = read(UI / "page_home.lua")
        result = read(UI / "page_task_decision_result.lua")
        self.assertIn('string.format("%.1f cm", millimetres / 10.0)', helper)
        self.assertIn('string.find(status, "unverified"', helper)
        self.assertIn("geometry_ui.format_height(", home)
        self.assertNotRegex(
            home,
            r"suggested_distance_cm.*(?:height|高度)|"
            r"(?:height|高度).*suggested_distance_cm",
        )
        self.assertIn("geometry_ui.target_height_lines(", result)
        for label in (
            "高度不可用",
            "目标物体高度：",
            "支撑面离地高度：",
            "物体顶部离地高度：",
        ):
            self.assertNotIn(label, result)
        self.assertIn("function M.target_height_lines(", helper)

    def test_historical_fixed_reason_remains_readable_without_new_override(
        self,
    ) -> None:
        self.assertEqual(self.fixture["schema_version"],
                         "rehab_selected_task.v2")
        self.assertEqual(self.fixture["reason"], FIXED_REASON)

        coordinator = read(
            COMPONENT / "src" / "rehab_task_decision_coordinator.c"
        )
        selected = read(
            COMPONENT / "src" / "rehab_task_decision_selected_task.c"
        )
        self.assertNotIn(FIXED_REASON, coordinator)
        self.assertNotIn("fixed_drink_reason", coordinator)
        self.assertIn("bounded_string(reason, 1", selected)

    def test_production_font_coverage_is_complete(self) -> None:
        production_text = load_production_text()
        manifest = json.loads(
            read(FONT / "glyphs" / "rehab_ui_glyph_manifest.json")
        )
        ranges: set[int] = set()
        for item in manifest["ranges"]:
            parts = item.split("-", 1)
            start = int(parts[0], 16)
            end = int(parts[-1], 16)
            ranges.update(range(start, end + 1))
        production, _ = production_text.production_codepoints()
        self.assertFalse(production - ranges)
        self.assertEqual(
            len(production_text.gb2312_level1_codepoints()), 3755
        )
        self.assertEqual(manifest["gb2312_level1_count"], 3755)
        self.assertEqual(manifest["font_bpp"], 1)
        self.assertTrue(
            {ord(character) for character in FIXED_REASON}.issubset(ranges)
        )
        self.assertTrue(
            {ord(character) for character in
             "未检测到完整喝水动作周期动作完整性得分已降低"}.issubset(ranges)
        )

    def test_generated_sanitizer_uses_visible_placeholder(self) -> None:
        helper = read(UI / "rehab_ui_text.lua")
        manifest = json.loads(
            read(FONT / "glyphs" / "rehab_ui_glyph_manifest.json")
        )
        count = re.search(r"M\.supported_glyph_count = (\d+)", helper)
        self.assertIsNotNone(count)
        self.assertEqual(int(count.group(1)), manifest["glyph_count"])
        self.assertIn('replacement = "？"', helper)


if __name__ == "__main__":
    unittest.main()
