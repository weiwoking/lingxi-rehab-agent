from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


EDGE = Path(__file__).resolve().parents[3]
REPO = EDGE.parents[1]
ENV_COMPONENT = EDGE / "components" / "rehab_environment_scan"
OLD_COMPONENT = EDGE / "components" / "rehab_task_decision"

ENV_SKILL = (
    ENV_COMPONENT / "skills" / "rehab-environment-scan" / "SKILL.md"
).read_text(encoding="utf-8")
OLD_SKILL_PATH = (
    OLD_COMPONENT / "skills" / "rehab-task-decision" / "SKILL.md"
)
OLD_CAPABILITIES = (
    OLD_COMPONENT / "src" / "rehab_task_decision_capabilities.c"
).read_text(encoding="utf-8")
ENV_CAPABILITIES = (
    ENV_COMPONENT / "src" / "rehab_environment_scan_capabilities.c"
).read_text(encoding="utf-8")
SKILL_SYNC = (
    REPO
    / "components"
    / "common"
    / "skill_builder"
    / "tools"
    / "sync_component_skills.py"
).read_text(encoding="utf-8")
CLAW_SKILL = (
    REPO / "components" / "claw_modules" / "claw_skill" / "src" / "claw_skill.c"
).read_text(encoding="utf-8")
WECHAT = (
    REPO
    / "components"
    / "claw_capabilities"
    / "cap_im_platform"
    / "src"
    / "cap_im_wechat.c"
).read_text(encoding="utf-8")


def descriptor_blocks(source: str, prefix: str) -> list[str]:
    return re.findall(
        rf"\{{\s*\.id = \"{re.escape(prefix)}[^\"]+\""
        rf".*?\.execute = [^,]+,\s*\}}",
        source,
        re.S,
    )


def skill_metadata(text: str) -> dict[str, object]:
    match = re.match(r"\A---\s*(\{.*?\})\s*---", text, re.S)
    if match is None:
        raise AssertionError("missing JSON skill frontmatter")
    return json.loads(match.group(1))


class ActiveSkillMigrationModel:
    def __init__(self, device_skill_ids: set[str]) -> None:
        self.device_skill_ids = device_skill_ids

    def hydrate(self, persisted: list[str]) -> list[str]:
        retained: list[str] = []
        for skill_id in persisted:
            if skill_id not in self.device_skill_ids:
                continue
            if skill_id not in retained:
                retained.append(skill_id)
        return retained


class Stage8A4LegacyDecisionIsolationTests(unittest.TestCase):
    def test_formal_decision_skill_is_in_device_profile(self):
        self.assertTrue(OLD_SKILL_PATH.is_file())
        exclusion = re.search(
            r"DEVICE_RUNTIME_EXCLUDED_SKILLS\s*=\s*frozenset\(\{(.*?)\}\)",
            SKILL_SYNC,
            re.S,
        )
        self.assertIsNotNone(exclusion)
        self.assertNotIn("'rehab-task-decision'", exclusion.group(1))
        self.assertNotIn("'rehab-environment-scan'", exclusion.group(1))

    def test_all_eight_legacy_descriptors_are_internal_only(self):
        blocks = descriptor_blocks(
            OLD_CAPABILITIES, "rehab_task_decision_"
        )
        self.assertEqual(len(blocks), 8)
        for block in blocks:
            self.assertNotIn("CLAW_CAP_FLAG_CALLABLE_BY_LLM", block)
        registration = OLD_CAPABILITIES[
            OLD_CAPABILITIES.index(
                "esp_err_t rehab_task_decision_register_capabilities"
            ):
        ]
        self.assertNotIn("claw_cap_add_llm_visible_group", registration)
        self.assertIn("visibility=internal_only", registration)

    def test_only_environment_scan_descriptors_remain_llm_callable(self):
        blocks = descriptor_blocks(
            ENV_CAPABILITIES, "rehab_environment_scan_"
        )
        self.assertEqual(len(blocks), 3)
        self.assertEqual(
            {
                re.search(r'\.id = "([^"]+)"', block).group(1)
                for block in blocks
            },
            {
                "rehab_environment_scan_start",
                "rehab_environment_scan_status",
                "rehab_environment_scan_cancel",
            },
        )
        for block in blocks:
            self.assertIn("CLAW_CAP_FLAG_CALLABLE_BY_LLM", block)

    def test_missing_active_skill_is_filtered_and_persistently_cleaned(self):
        persisted = [
            "rehab-task-decision",
            "rehab-environment-scan",
            "person-follow",
        ]
        model = ActiveSkillMigrationModel(
            {"rehab-task-decision", "rehab-environment-scan", "person-follow"}
        )
        self.assertEqual(
            model.hydrate(persisted),
            ["rehab-task-decision", "rehab-environment-scan", "person-follow"],
        )
        self.assertIn(
            "if (!claw_skill_find_entry(item->valuestring))", CLAW_SKILL
        )
        self.assertIn("sanitized = true", CLAW_SKILL)
        self.assertIn("save_active_skill_ids_to_disk(", CLAW_SKILL)
        self.assertIn("build_session_state_path_dup(session_id)", CLAW_SKILL)

    def test_migration_is_session_scoped_and_deduplicates_groups(self):
        model = ActiveSkillMigrationModel(
            {"rehab-task-decision", "rehab-environment-scan", "person-follow"}
        )
        session_a = model.hydrate(
            [
                "rehab-task-decision",
                "rehab-environment-scan",
                "person-follow",
                "person-follow",
            ]
        )
        session_b = model.hydrate(["person-follow"])
        self.assertEqual(
            session_a,
            ["rehab-task-decision", "rehab-environment-scan", "person-follow"],
        )
        self.assertEqual(session_b, ["person-follow"])

    def test_environment_skill_has_single_phrase_owner_contract(self):
        metadata = skill_metadata(ENV_SKILL)
        self.assertEqual(metadata["name"], "rehab-environment-scan")
        self.assertEqual(
            metadata["metadata"]["cap_groups"],
            ["rehab_environment_scan"],
        )
        for phrase in (
            "扫描环境",
            "开始环境扫描",
            "开始扫描",
            "帮我扫描一下环境",
            "帮我看看周围环境",
            "扫描状态",
            "停止扫描",
            "取消环境扫描",
        ):
            self.assertIn(phrase, ENV_SKILL)
        self.assertNotIn("rehab_task_decision_", ENV_SKILL)

    def test_decision_requests_do_not_start_scan_or_use_legacy_chain(self):
        for phrase in (
            "决策任务",
            "请帮我决定一个任务",
            "根据刚才的环境安排任务",
            "给我推荐康复任务",
        ):
            self.assertIn(phrase, ENV_SKILL)
        self.assertIn("不是环境扫描启动或状态意图", ENV_SKILL)
        self.assertIn("不得调用 scan start/status", ENV_SKILL)
        self.assertIn("应交给独立的正式任务决策 Skill", ENV_SKILL)

    def test_scan_completion_never_suggests_or_triggers_decision(self):
        self.assertIn("5 帧预热", ENV_SKILL)
        self.assertIn("11 方向 RGB+ToF", ENV_SKILL)
        self.assertIn("SD 完整提交", ENV_SKILL)
        self.assertIn("Upload-Only", ENV_SKILL)
        self.assertIn(
            "原始环境数据已完成采集并上传至云端。"
            "场景识别将在云端继续处理。",
            ENV_SKILL,
        )
        self.assertIn(
            "正在进行5帧预热和11方向RGB+ToF采集，"
            "完成后将自动上传至云端。",
            ENV_SKILL,
        )
        self.assertIn(
            "11组采集已完成，正在上传至云端分析服务。",
            ENV_SKILL,
        )
        self.assertNotIn("公网服务器已收到", ENV_SKILL)
        for forbidden in (
            "自动决策触发",
            "需要我做任务决策吗",
            "rehab_task_decision",
        ):
            self.assertNotIn(forbidden, ENV_SKILL)

    def test_wechat_inbound_has_no_scan_or_decision_shortcut(self):
        for forbidden in (
            "rehab_environment_scan_start",
            "rehab_environment_scan_status",
            "rehab_environment_scan_cancel",
            "rehab_task_decision_scan_start",
        ):
            self.assertNotIn(forbidden, WECHAT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
