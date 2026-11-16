from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project_system_fatfs import (
    APP,
    BUILD,
    MINIMUM_MARGIN,
    REPOSITORY,
    module_index,
    project,
    skill_sync,
)
from lua_sync_common import collect_build_component_sources


PROTECTED_SKILLS = {
    "person-follow",
    "target-follow",
    "rehab-environment-scan",
    "rehab-task-decision",
    "rehab-control",
}


class SystemPackagingRulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill_sources = skill_sync.collect_skill_sources(BUILD)
        cls.all_component_sources = collect_build_component_sources(BUILD)
        cls.lua_sources = collect_build_component_sources(
            BUILD,
            name_prefix=("lua_module_", "lua_driver_"),
        )

    def test_device_profile_excludes_only_requested_skills(self) -> None:
        development = skill_sync.collect_component_skills(
            self.skill_sources
        )
        device = skill_sync.collect_component_skills(
            self.skill_sources,
            excluded_skill_ids=skill_sync.DEVICE_RUNTIME_EXCLUDED_SKILLS,
        )
        development_ids = {Path(name).parts[0] for name in development}
        device_ids = {Path(name).parts[0] for name in device}
        self.assertTrue(
            skill_sync.DEVICE_RUNTIME_EXCLUDED_SKILLS
            <= development_ids
        )
        self.assertEqual(
            development_ids - device_ids,
            set(skill_sync.DEVICE_RUNTIME_EXCLUDED_SKILLS),
        )
        self.assertTrue(PROTECTED_SKILLS <= device_ids)
        self.assertIn("builtin_lua_modules", device_ids)

    def test_device_index_keeps_docs_without_test_paths(self) -> None:
        entries = module_index.collect_lua_module_skill_entries(
            self.lua_sources,
            include_tests=False,
        )
        rendered = module_index.render_generated_skill(
            entries,
            include_tests=False,
        )
        self.assertEqual(len(entries), 25)
        self.assertNotIn("scripts/builtin/test", rendered)
        self.assertNotIn("Test script path", rendered)
        self.assertIn("scripts/docs", rendered)

    def test_all_43_component_test_sources_are_retained(self) -> None:
        tests = module_index.collect_lua_module_test_script_entries(
            self.all_component_sources
        )
        self.assertEqual(len(tests), 43)
        self.assertTrue(all(Path(item["script_path"]).is_file() for item in tests))

    def test_cmake_uses_device_runtime_profile(self) -> None:
        lua_cmake = (
            REPOSITORY
            / "components/common/lua_module_builder/cmake/lua_sync.cmake"
        ).read_text(encoding="utf-8")
        skill_cmake = (
            REPOSITORY
            / "components/common/skill_builder/cmake/skill_sync.cmake"
        ).read_text(encoding="utf-8")
        self.assertIn("--profile device-runtime", lua_cmake)
        self.assertIn("--profile device-runtime", skill_cmake)

    def test_clean_projection_meets_all_system_boundaries(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="stage8a3_system_projection_"
        ) as temporary:
            output = Path(temporary) / "system"
            result = project(output)
            self.assertEqual(result["lua_source_test_count"], 43)
            self.assertEqual(result["packaged_lua_test_count"], 0)
            self.assertEqual(result["lua_doc_count"], 25)
            self.assertEqual(result["lua_runtime_lib_file_count"], 6)
            self.assertEqual(
                set(result["excluded_skills"]),
                {
                    "http_server_lua_demo",
                    "skill_creator",
                },
            )
            self.assertGreaterEqual(
                int(result["estimated_remaining_bytes"]),
                MINIMUM_MARGIN,
            )
            runtime_fonts = output / "ui" / "fonts"
            self.assertEqual(
                sorted(path.name for path in runtime_fonts.iterdir()),
                ["rehab_cn_20.bin"],
            )
            index = (
                output / "skills/builtin_lua_modules/SKILL.md"
            ).read_text(encoding="utf-8")
            self.assertNotIn("scripts/builtin/test", index)
            all_skill_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (output / "skills").rglob("*")
                if path.is_file()
                and path.suffix.lower() in {".md", ".json", ".lua"}
            )
            self.assertNotIn(
                "/system/skills/builtin_lua_modules/scripts/builtin/test",
                all_skill_text,
            )
            self.assertNotIn("http_server_lua_demo", all_skill_text)
            self.assertNotIn("skill_creator", all_skill_text)
            for skill_id in PROTECTED_SKILLS:
                self.assertTrue((output / "skills" / skill_id).is_dir())
            self.assertTrue(
                (
                    output
                    / "skills"
                    / "rehab-task-decision"
                    / "SKILL.md"
                ).is_file()
            )


if __name__ == "__main__":
    unittest.main()
