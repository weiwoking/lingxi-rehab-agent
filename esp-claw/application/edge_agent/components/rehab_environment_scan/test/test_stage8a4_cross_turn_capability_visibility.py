from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import unittest


EDGE = Path(__file__).resolve().parents[3]
REPO = EDGE.parents[1]
CLAW_CAP = (
    REPO / "components" / "claw_modules" / "claw_cap" / "src" / "claw_cap.c"
).read_text(encoding="utf-8")
CLAW_CAP_HEADER = (
    REPO / "components" / "claw_modules" / "claw_cap" / "include" / "claw_cap.h"
).read_text(encoding="utf-8")
CAP_SKILL_MGR = (
    REPO
    / "components"
    / "claw_capabilities"
    / "cap_skill_mgr"
    / "src"
    / "cap_skill_mgr.c"
).read_text(encoding="utf-8")
APP_CLAW = (
    REPO / "components" / "common" / "app_claw" / "app_claw.c"
).read_text(encoding="utf-8")
WECHAT = (
    REPO
    / "components"
    / "claw_capabilities"
    / "cap_im_platform"
    / "src"
    / "cap_im_wechat.c"
).read_text(encoding="utf-8")

SKILL_PATHS = {
    "rehab-environment-scan": (
        EDGE
        / "components"
        / "rehab_environment_scan"
        / "skills"
        / "rehab-environment-scan"
        / "SKILL.md"
    ),
    "person-follow": (
        EDGE
        / "components"
        / "rehab_person_follow"
        / "skills"
        / "person-follow"
        / "SKILL.md"
    ),
    "target-follow": (
        EDGE
        / "components"
        / "rehab_target_follow"
        / "skills"
        / "target-follow"
        / "SKILL.md"
    ),
    "rehab-control": (
        EDGE
        / "components"
        / "rehab_integration"
        / "skills"
        / "rehab-control"
        / "SKILL.md"
    ),
    "rehab-task-decision": (
        EDGE
        / "components"
        / "rehab_task_decision"
        / "skills"
        / "rehab-task-decision"
        / "SKILL.md"
    ),
}

GROUP_TO_TOOLS = {
    "rehab_environment_scan": {
        "rehab_environment_scan_start",
        "rehab_environment_scan_status",
        "rehab_environment_scan_cancel",
    },
    "rehab_task_decision_coordinator": {
        "rehab_task_decision_run",
        "rehab_task_decision_status",
    },
    "rehab_person_follow_safe": {"person_follow_status"},
    "rehab_person_follow_start": {"person_follow_start"},
    "rehab_target_follow_safe": {"target_follow_status"},
    "rehab_target_follow_start": {"target_follow_start"},
    "rehab_integration": {"rehab_training_status"},
}


def function_slice(source: str, name: str, next_name: str) -> str:
    start = source.index(name)
    end = source.index(next_name, start)
    return source[start:end]


def load_skill_groups(path: Path) -> tuple[str, ...]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\s*(\{.*?\})\s*---", text, re.S)
    if match is None:
        raise AssertionError(f"missing JSON frontmatter: {path}")
    metadata = json.loads(match.group(1))["metadata"]
    return tuple(metadata["cap_groups"])


SKILL_GROUPS = {
    skill_id: load_skill_groups(path) for skill_id, path in SKILL_PATHS.items()
}
DEVICE_SKILL_IDS = set(SKILL_PATHS)


class PersistentActiveSkills:
    """Host model of claw_skill's per-session DATA state files."""

    def __init__(self) -> None:
        self._sessions: dict[str, list[str]] = {}

    def activate(self, session_id: str, skill_id: str) -> None:
        active = self._sessions.setdefault(session_id, [])
        if skill_id not in active:
            active.append(skill_id)

    def groups(self, session_id: str) -> tuple[str, ...]:
        active = [
            skill_id
            for skill_id in self._sessions.get(session_id, [])
            if skill_id in DEVICE_SKILL_IDS
        ]
        self._sessions[session_id] = active
        groups: list[str] = []
        for skill_id in active:
            for group_id in SKILL_GROUPS[skill_id]:
                if group_id not in groups:
                    groups.append(group_id)
        return tuple(groups)


class RebuiltAgentRuntime:
    """Fresh RAM visibility for one new Agent run."""

    def __init__(self, state: PersistentActiveSkills) -> None:
        self.state = state
        self.visible_by_session: dict[str, tuple[str, ...]] = {}

    def build_tools(self, session_id: str) -> set[str]:
        groups = self.state.groups(session_id)
        self.visible_by_session[session_id] = groups
        return {
            tool
            for group_id in groups
            for tool in GROUP_TO_TOOLS.get(group_id, set())
        }


@dataclass(frozen=True)
class ScanSnapshot:
    state: str
    scan_id: str
    accepted: bool
    busy: bool


class MockScanCoordinator:
    """Coordinator seam; no Camera, ToF, car, SD, upload, or worker code."""

    def __init__(self) -> None:
        self.state = "IDLE"
        self.scan_id = ""
        self.sequence = 0
        self.worker_count = 0

    def start(self) -> ScanSnapshot:
        if self.state == "ACTIVE":
            return ScanSnapshot("ACTIVE", self.scan_id, False, True)
        self.sequence += 1
        self.scan_id = f"scan_host_{self.sequence:03d}"
        self.state = "ACTIVE"
        self.worker_count += 1
        return ScanSnapshot("ACTIVE", self.scan_id, True, False)

    def complete(self) -> None:
        if self.state != "ACTIVE":
            raise AssertionError("only an active scan can complete")
        self.state = "COMPLETED"

    def status(self) -> ScanSnapshot:
        return ScanSnapshot(
            self.state,
            self.scan_id,
            False,
            self.state == "ACTIVE",
        )

    def cancel(self) -> ScanSnapshot:
        if self.state == "ACTIVE":
            self.state = "CANCELLED"
            return ScanSnapshot(self.state, self.scan_id, True, False)
        return ScanSnapshot(self.state, self.scan_id, False, False)


class CrossTurnCapabilityVisibilityTests(unittest.TestCase):
    def assert_tools_visible(self, tools: set[str]) -> None:
        self.assertTrue(GROUP_TO_TOOLS["rehab_environment_scan"] <= tools)

    def test_six_new_agent_runs_restore_scan_tools_and_coordinator_identity(self) -> None:
        session = "chat:wechat:host-room:default_01"
        active = PersistentActiveSkills()
        coordinator = MockScanCoordinator()

        # Turn 1: activation persists; start is visible and accepted.
        active.activate(session, "rehab-environment-scan")
        turn_1 = RebuiltAgentRuntime(active)
        self.assert_tools_visible(turn_1.build_tools(session))
        first = coordinator.start()
        self.assertTrue(first.accepted)
        self.assertFalse(first.busy)
        self.assertEqual(coordinator.worker_count, 1)

        # Turn 2: a fresh Agent run has no RAM carry-over but restores status.
        turn_2 = RebuiltAgentRuntime(active)
        self.assert_tools_visible(turn_2.build_tools(session))
        self.assertEqual(coordinator.status().scan_id, first.scan_id)

        # Turn 3: completed snapshot remains accessible, then a new scan starts.
        coordinator.complete()
        turn_3 = RebuiltAgentRuntime(active)
        self.assert_tools_visible(turn_3.build_tools(session))
        self.assertEqual(coordinator.status().state, "COMPLETED")
        second = coordinator.start()
        self.assertTrue(second.accepted)
        self.assertNotEqual(second.scan_id, first.scan_id)
        self.assertEqual(coordinator.worker_count, 2)

        # Turn 4: active start is busy and does not create another worker.
        turn_4 = RebuiltAgentRuntime(active)
        self.assert_tools_visible(turn_4.build_tools(session))
        busy = coordinator.start()
        self.assertTrue(busy.busy)
        self.assertFalse(busy.accepted)
        self.assertEqual(busy.scan_id, second.scan_id)
        self.assertEqual(coordinator.worker_count, 2)

        # Turn 5: cancel reaches the same coordinator and scan.
        turn_5 = RebuiltAgentRuntime(active)
        self.assert_tools_visible(turn_5.build_tools(session))
        cancelled = coordinator.cancel()
        self.assertTrue(cancelled.accepted)
        self.assertEqual(cancelled.scan_id, second.scan_id)

        # Turn 6: another fresh run restores status and observes CANCELLED.
        turn_6 = RebuiltAgentRuntime(active)
        self.assert_tools_visible(turn_6.build_tools(session))
        final = coordinator.status()
        self.assertEqual(final.state, "CANCELLED")
        self.assertEqual(final.scan_id, second.scan_id)

    def test_active_skill_groups_are_unique_and_session_scoped(self) -> None:
        active = PersistentActiveSkills()
        session_a = "chat:wechat:a:default_01"
        session_b = "chat:wechat:b:default_01"

        active.activate(session_a, "rehab-environment-scan")
        active.activate(session_a, "rehab-task-decision")
        active.activate(session_a, "person-follow")
        active.activate(session_a, "person-follow")
        active.activate(session_b, "person-follow")

        rebuilt = RebuiltAgentRuntime(active)
        tools_a = rebuilt.build_tools(session_a)
        tools_b = rebuilt.build_tools(session_b)

        self.assertEqual(
            rebuilt.visible_by_session[session_a],
            (
                "rehab_environment_scan",
                "rehab_task_decision_coordinator",
                "rehab_person_follow_safe",
                "rehab_person_follow_start",
            ),
        )
        self.assertIn("person_follow_status", tools_a)
        self.assertIn("rehab_task_decision_run", tools_a)
        self.assertIn("rehab_task_decision_status", tools_a)
        self.assertNotIn("rehab_task_decision_context_get", tools_a)
        self.assertNotIn("rehab_environment_scan_start", tools_b)
        self.assertIn("person_follow_status", tools_b)
        self.assertIn(
            "rehab-task-decision",
            active._sessions[session_a],
        )

    def test_all_compared_dynamic_skills_declare_unique_known_groups(self) -> None:
        for skill_id in (
            "rehab-environment-scan",
            "person-follow",
            "target-follow",
            "rehab-control",
            "rehab-task-decision",
        ):
            with self.subTest(skill_id=skill_id):
                groups = SKILL_GROUPS[skill_id]
                self.assertGreater(len(groups), 0)
                self.assertEqual(len(groups), len(set(groups)))
                self.assertTrue(set(groups) <= GROUP_TO_TOOLS.keys())
        self.assertTrue(SKILL_PATHS["rehab-task-decision"].is_file())
        self.assertIn("rehab-task-decision", DEVICE_SKILL_IDS)

    def test_tool_builder_restores_before_visibility_filter(self) -> None:
        builder = function_slice(
            CLAW_CAP,
            "char *claw_cap_build_llm_tools_json",
            "esp_err_t claw_cap_call_from_core",
        )
        self.assertLess(
            builder.index("claw_cap_restore_session_skill_groups(session_id)"),
            builder.index("claw_cap_lock();"),
        )
        self.assertLess(
            builder.index("claw_cap_restore_session_skill_groups(session_id)"),
            builder.index("claw_cap_is_llm_visible("),
        )

    def test_restore_replaces_ram_groups_from_persistent_active_skills(self) -> None:
        restore = function_slice(
            CLAW_CAP,
            "esp_err_t claw_cap_restore_session_skill_groups",
            "esp_err_t claw_cap_register",
        )
        self.assertIn("claw_skill_load_active_cap_groups(", restore)
        self.assertIn("err == ESP_ERR_NOT_FOUND", restore)
        self.assertIn(
            "claw_cap_set_session_llm_visible_groups(session_id, NULL, 0)",
            restore,
        )
        self.assertIn("(const char *const *)group_ids", restore)
        self.assertIn("claw_cap_free_group_ids(group_ids, group_count)", restore)
        self.assertIn(
            "claw_cap_restore_session_skill_groups(const char *session_id)",
            CLAW_CAP_HEADER,
        )

    def test_activation_uses_the_same_restore_path(self) -> None:
        activation = function_slice(
            CAP_SKILL_MGR,
            "static esp_err_t cap_skill_activate_execute",
            "static esp_err_t cap_skill_list_execute",
        )
        self.assertLess(
            activation.index("claw_skill_activate_for_session("),
            activation.index("claw_cap_restore_session_skill_groups("),
        )
        self.assertNotIn("cap_skill_sync_session_visible_groups", CAP_SKILL_MGR)

    def test_dispatcher_not_exposed_source_and_root_only_guard_remain(self) -> None:
        self.assertIn(
            "Error: cap '%s' is not exposed to the LLM.",
            CLAW_CAP,
        )
        self.assertGreaterEqual(CLAW_CAP.count("claw_cap_is_llm_visible("), 3)
        self.assertIn("CLAW_CAP_CALLER_SUB_AGENT", CLAW_CAP)
        self.assertIn("CLAW_CAP_FLAG_ROOT_AGENT_ONLY", CLAW_CAP)

    def test_session_deletion_clears_persistent_and_ram_skill_state(self) -> None:
        deletion = function_slice(
            APP_CLAW,
            "static esp_err_t app_claw_delete_session_history",
            "esp_err_t app_claw_ui_start",
        )
        self.assertLess(
            deletion.index("claw_skill_delete_session_state("),
            deletion.index("claw_cap_set_session_llm_visible_groups("),
        )
        self.assertIn("session_id, NULL, 0", deletion)

    def test_wechat_layer_has_no_environment_scan_shortcut(self) -> None:
        for forbidden in (
            "rehab_environment_scan_start",
            "rehab_environment_scan_status",
            "rehab_environment_scan_cancel",
        ):
            self.assertNotIn(forbidden, WECHAT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
