from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from estimate_system_fatfs import RUNTIME_METADATA, estimate
from project_system_fatfs import project


APP = Path(__file__).resolve().parents[2]
SYSTEM = APP / "fatfs_image" / "system"
RUNTIME_FONTS = SYSTEM / "ui" / "fonts"
GENERATED = APP / "tools" / "rehab_ui_font" / "generated"
GLYPHS = APP / "tools" / "rehab_ui_font" / "glyphs"
MINIMUM_MARGIN = 0x20000


class SystemFatfsCapacityTest(unittest.TestCase):
    def test_runtime_font_directory_contains_only_binfont(self) -> None:
        self.assertEqual(
            sorted(path.name for path in RUNTIME_FONTS.iterdir()),
            ["rehab_cn_20.bin"],
        )
        for relative in RUNTIME_METADATA:
            self.assertFalse((SYSTEM / relative).exists())

    def test_font_metadata_is_outside_fatfs_image(self) -> None:
        expected = {
            "rehab_ui_glyphs.txt",
            "rehab_ui_codepoints.txt",
            "rehab_ui_codepoint_ranges.txt",
            "rehab_ui_glyph_manifest.json",
        }
        self.assertEqual(
            {path.name for path in GLYPHS.iterdir() if path.is_file()},
            expected,
        )
        self.assertEqual(
            {path.name for path in GENERATED.iterdir() if path.is_file()},
            {"rehab_cn_20.bin"},
        )
        self.assertNotIn("fatfs_image", GLYPHS.parts)

    def test_system_source_has_minimum_margin(self) -> None:
        result = estimate(SYSTEM)
        self.assertGreaterEqual(
            int(result["estimated_remaining_bytes"]),
            MINIMUM_MARGIN,
        )

    def test_clean_device_runtime_projection_has_minimum_margin(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="stage8a3_capacity_projection_"
        ) as temporary:
            result = project(Path(temporary) / "system")
            self.assertGreaterEqual(
                int(result["estimated_remaining_bytes"]),
                MINIMUM_MARGIN,
            )


if __name__ == "__main__":
    unittest.main()
