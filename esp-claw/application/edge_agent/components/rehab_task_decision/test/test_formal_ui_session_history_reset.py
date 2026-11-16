from __future__ import annotations

import re
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
COMPONENT = ROOT / "application/edge_agent/components/rehab_task_decision"
COORDINATOR_PATH = COMPONENT / "src/rehab_task_decision_coordinator.c"
CMAKE_PATH = COMPONENT / "CMakeLists.txt"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*\([^;]*?\)\s*\{{", source, re.S)
    if match is None:
        raise AssertionError(f"function not found: {name}")
    brace = source.index("{", match.start())
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace : index + 1]
    raise AssertionError(f"unterminated function: {name}")


COORDINATOR = read(COORDINATOR_PATH)
CMAKE = read(CMAKE_PATH)
RESERVE = function_body(COORDINATOR, "coordinator_reserve_formal")


class FormalUiSessionHistoryResetSourceContractTest(unittest.TestCase):
    def test_public_owner_api_and_dependency_are_used(self) -> None:
        self.assertIn('#include "claw_memory.h"', COORDINATOR)
        self.assertRegex(CMAKE, r"(?m)^\s*claw_memory\s*$")
        self.assertEqual(
            RESERVE.count("claw_memory_delete_session_history("), 1
        )
        self.assertIn(
            "REHAB_TASK_DECISION_FORMAL_UI_SESSION_ID, &deleted_any",
            RESERVE,
        )

    def test_reset_is_local_ui_only_and_requires_idle_root(self) -> None:
        local_branch = RESERVE.index(
            "source == REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI"
        )
        root_get = RESERVE.index("claw_agent_mgr_get_root_core()", local_branch)
        root_null = RESERVE.index("root == NULL", root_get)
        phase_get = RESERVE.index("claw_core_get_agent_loop_phase(root)", root_null)
        idle_guard = RESERVE.index(
            "root_phase != CLAW_CORE_AGENT_LOOP_PHASE_IDLE", phase_get
        )
        delete = RESERVE.index(
            "claw_memory_delete_session_history(", idle_guard
        )
        self.assertLess(local_branch, root_get)
        self.assertLess(root_get, root_null)
        self.assertLess(root_null, phase_get)
        self.assertLess(phase_get, idle_guard)
        self.assertLess(idle_guard, delete)
        self.assertIn("root_core_unavailable", RESERVE[root_null:phase_get])
        self.assertIn("root_not_idle", RESERVE[idle_guard:delete])

    def test_reset_precedes_all_transaction_reservation(self) -> None:
        second_busy = RESERVE.index(
            "handle->coordinator_status.active ||",
            RESERVE.index("formal_scene_load"),
        )
        reset = RESERVE.index("claw_memory_delete_session_history(")
        detach = RESERVE.index("handle->coordinator_formal_package", reset)
        generation = RESERVE.index("++handle->coordinator_generation", reset)
        active = RESERVE.index("coordinator_status.active = true", reset)
        decision_id = RESERVE.index('"decision-%08"', reset)
        selected_begin = RESERVE.index(
            "rehab_task_decision_selected_begin(", reset
        )
        self.assertLess(second_busy, reset)
        self.assertLess(reset, detach)
        self.assertLess(reset, generation)
        self.assertLess(reset, active)
        self.assertLess(reset, decision_id)
        self.assertLess(reset, selected_begin)

    def test_reset_failure_is_fail_closed(self) -> None:
        failure = RESERVE[
            RESERVE.index("if (err != ESP_OK)", RESERVE.index("deleted_any")) :
            RESERVE.index("FORMAL_UI_SESSION_HISTORY_RESET deleted_any")
        ]
        self.assertIn("session_history_reset_failed", failure)
        self.assertIn("coordinator_unlock(handle)", failure)
        self.assertIn("return err", failure)
        for forbidden in (
            "++handle->coordinator_generation",
            "coordinator_status.active = true",
            "rehab_task_decision_selected_begin",
            "claw_agent_mgr_submit_root_text",
        ):
            self.assertNotIn(forbidden, failure)

    def test_no_direct_storage_or_skill_state_deletion(self) -> None:
        component_sources = "\n".join(
            read(path)
            for path in (COMPONENT / "src").glob("*.c")
        )
        for forbidden in (
            "db415fd6",
            "/sdcard/sessions",
            "claw_skill_delete_session_state(",
        ):
            self.assertNotIn(forbidden, component_sources)
        self.assertNotRegex(RESERVE, r"\bunlink\s*\(")
        self.assertNotRegex(RESERVE, r"\bremove\s*\(")


@dataclass
class FakeOwnerApi:
    result: int = 0
    deleted_any: bool = False
    calls: int = 0
    skill_state_present: bool = True

    def delete_history(self, session_id: str) -> tuple[int, bool]:
        self.calls += 1
        if session_id != "rehab-formal-ui":
            raise AssertionError("wrong logical session")
        return self.result, self.deleted_any


@dataclass
class FakeReservation:
    owner: FakeOwnerApi
    root_available: bool = True
    root_idle: bool = True
    busy: bool = False
    generation: int = 0
    selected_begin_calls: int = 0
    submit_calls: int = 0
    selected_state: str = "previous-selection"

    def reserve(self, source: str) -> bool:
        if self.busy:
            return False
        if source == "LOCAL_UI":
            if not self.root_available or not self.root_idle:
                return False
            err, _deleted_any = self.owner.delete_history("rehab-formal-ui")
            if err != 0:
                return False
        self.generation += 1
        self.selected_begin_calls += 1
        self.submit_calls += 1
        return True


class FormalUiSessionHistoryResetFakeBehaviorTest(unittest.TestCase):
    def assert_not_reserved(self, fake: FakeReservation) -> None:
        self.assertEqual(fake.generation, 0)
        self.assertEqual(fake.selected_begin_calls, 0)
        self.assertEqual(fake.submit_calls, 0)

    def test_idle_with_old_history_deletes_once_and_continues(self) -> None:
        owner = FakeOwnerApi(deleted_any=True)
        fake = FakeReservation(owner)
        self.assertTrue(fake.reserve("LOCAL_UI"))
        self.assertEqual(owner.calls, 1)
        self.assertEqual((fake.generation, fake.selected_begin_calls), (1, 1))

    def test_idle_with_no_history_is_success(self) -> None:
        owner = FakeOwnerApi(deleted_any=False)
        fake = FakeReservation(owner)
        self.assertTrue(fake.reserve("LOCAL_UI"))
        self.assertEqual(owner.calls, 1)

    def test_missing_log_or_idx_and_blocked_are_owner_api_semantics(self) -> None:
        for _case in ("log_missing", "idx_missing", "blocked_present"):
            owner = FakeOwnerApi(result=0, deleted_any=True)
            fake = FakeReservation(owner)
            self.assertTrue(fake.reserve("LOCAL_UI"))
            self.assertEqual(owner.calls, 1)

    def test_skill_state_is_untouched(self) -> None:
        owner = FakeOwnerApi(deleted_any=True, skill_state_present=True)
        fake = FakeReservation(owner)
        self.assertTrue(fake.reserve("LOCAL_UI"))
        self.assertTrue(owner.skill_state_present)

    def test_root_not_idle_does_not_delete_or_reserve(self) -> None:
        owner = FakeOwnerApi()
        fake = FakeReservation(owner, root_idle=False)
        self.assertFalse(fake.reserve("LOCAL_UI"))
        self.assertEqual(owner.calls, 0)
        self.assert_not_reserved(fake)

    def test_root_unavailable_fails_closed(self) -> None:
        owner = FakeOwnerApi()
        fake = FakeReservation(owner, root_available=False)
        self.assertFalse(fake.reserve("LOCAL_UI"))
        self.assertEqual(owner.calls, 0)
        self.assert_not_reserved(fake)

    def test_delete_failure_does_not_reserve_or_submit(self) -> None:
        owner = FakeOwnerApi(result=1)
        fake = FakeReservation(owner)
        self.assertFalse(fake.reserve("LOCAL_UI"))
        self.assertEqual(owner.calls, 1)
        self.assert_not_reserved(fake)
        self.assertEqual(fake.selected_state, "previous-selection")

        owner.result = 0
        self.assertTrue(fake.reserve("LOCAL_UI"))
        self.assertEqual(owner.calls, 2)

    def test_ordinary_wechat_does_not_delete(self) -> None:
        owner = FakeOwnerApi()
        fake = FakeReservation(owner)
        self.assertTrue(fake.reserve("WECHAT"))
        self.assertEqual(owner.calls, 0)

    def test_existing_busy_path_does_not_delete(self) -> None:
        owner = FakeOwnerApi()
        fake = FakeReservation(owner, busy=True)
        self.assertFalse(fake.reserve("LOCAL_UI"))
        self.assertEqual(owner.calls, 0)
        self.assert_not_reserved(fake)


if __name__ == "__main__":
    unittest.main()
