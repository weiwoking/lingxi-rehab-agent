from pathlib import Path
import unittest


APP = Path(__file__).resolve().parents[3]
UI = APP / "fatfs_image" / "system" / "ui" / "rehab"
STATE = (UI / "rehab_ui_state.lua").read_text(encoding="utf-8")
CONTROLLER = (UI / "esp_claw_rehab_ui.lua").read_text(encoding="utf-8")
LUA_TEST = (Path(__file__).with_name("test_rehab_ui_state.lua")).read_text(
    encoding="utf-8"
)


class TaskDecisionUiObserverContractTest(unittest.TestCase):
    def test_generation_state_is_centralized_and_polled_at_bounded_rate(self):
        for field in (
            "task_decision_last_seen_generation",
            "task_decision_pending_generation",
            "task_decision_pending_target",
            "task_decision_presented_generation",
        ):
            self.assertIn(field, STATE)
            self.assertNotIn(field, CONTROLLER)
        self.assertIn("TASK_DECISION_ACTIVE_PERIOD_MS = 500", STATE)
        self.assertIn("TASK_DECISION_IDLE_PERIOD_MS = 1000", STATE)

    def test_navigation_is_observer_owned_and_adapters_do_not_navigate(self):
        self.assertIn("mark_task_decision_presented", STATE)
        self.assertIn("mark_task_decision_presented(app.state, page_name)", CONTROLLER)
        queued = CONTROLLER[
            CONTROLLER.index("function app.queue_task_decision_start") :
            CONTROLLER.index("function app.queue_task_redecision")
        ]
        self.assertNotIn('navigate("task_decision_progress")', queued)
        self.assertIn("TASK_DECISION_UI_SYNC", STATE)

    def test_lua_behavior_vectors_cover_external_and_protected_pages(self):
        for text in (
            "Home plus external PREPARING",
            "generation 2 must be presented normally",
            "external decision must remain pending on protected page",
            "end-confirm modal must not be preempted",
            "pre-start rejection must stay on prepare",
        ):
            self.assertIn(text, LUA_TEST)


if __name__ == "__main__":
    unittest.main()
