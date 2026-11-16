"""Host-only Stage 8A-1 orchestration and source-boundary checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import unittest


EDGE = Path(__file__).resolve().parents[3]
COMPONENT = EDGE / "components" / "rehab_environment_scan"
HEADER = (COMPONENT / "include" / "rehab_environment_scan.h").read_text(
    encoding="utf-8"
)
COORDINATOR = (COMPONENT / "src" / "rehab_environment_scan.c").read_text(
    encoding="utf-8"
)
CAPTURE_HEADER = (
    EDGE / "components" / "rehab_scan_capture" / "include" /
    "rehab_scan_capture.h"
).read_text(encoding="utf-8")
CAPTURE_SOURCE = (
    EDGE / "components" / "rehab_scan_capture" / "src" /
    "rehab_scan_capture.c"
).read_text(encoding="utf-8")
CAPTURE_PRIVATE = (
    EDGE / "components" / "rehab_scan_capture" / "src" /
    "rehab_scan_capture_priv.h"
).read_text(encoding="utf-8")
CAPTURE_CONSOLE = (
    EDGE / "components" / "rehab_scan_capture" / "src" /
    "rehab_scan_capture_console.c"
).read_text(encoding="utf-8")
MAIN = (EDGE / "main" / "main.c").read_text(encoding="utf-8")


@dataclass
class Stage8A1Simulation:
    state: str = "IDLE"
    scan_counter: int = 0
    worker_count: int = 0
    lease_acquire_count: int = 0
    events: list[str] = field(default_factory=list)
    final_scan_ids: set[str] = field(default_factory=set)

    def start(self, fault: str | None = None) -> tuple[bool, str]:
        if self.state not in {"IDLE", "COMPLETED", "CANCELLED", "FAILED"}:
            return True, self.current_scan_id
        self.scan_counter += 1
        self.worker_count += 1
        self.lease_acquire_count += 1
        self.current_scan_id = f"scan_stage8a1_{self.scan_counter:03d}"
        self.state = "ACQUIRING_LEASE"
        persisted: list[int] = []
        try:
            self.events.append("lease_acquire")
            self.state = "WARMING_CAMERA"
            for index in range(5):
                self.events.append(f"warmup_acquire:{index}")
                if fault == "warmup_failure" and index == 2:
                    raise RuntimeError(fault)
                self.events.append(f"warmup_release_discard:{index}")

            for sequence in range(11):
                if sequence > 0:
                    self.state = "ROTATING"
                    self.events.append(f"rotate_ccw:3:500:{sequence}")
                    if fault in {"rotation_failure", "cancel_rotate"} and sequence == 3:
                        raise RuntimeError(fault)
                    self.state = "STOPPING"
                    self.events.append(f"explicit_stop:{sequence}")
                    self.state = "SETTLING"
                    self.events.append(f"settle:200:{sequence}")
                    if fault == "cancel_settle" and sequence == 3:
                        raise RuntimeError(fault)

                self.state = "CAPTURING"
                self.events.append(f"capture_pair:{sequence}")
                if fault in {
                    "camera_failure", "tof_pair_failure", "cancel_capture"
                } and sequence == 4:
                    raise RuntimeError(fault)
                self.state = "WRITING"
                self.events.append(f"write_sd:{sequence}")
                if fault in {"sd_failure", "cancel_write"} and sequence == 4:
                    raise RuntimeError(fault)
                persisted.append(sequence)

            if persisted != list(range(11)):
                raise RuntimeError("manifest_incomplete")
            self.events.append("manifest_validate:11")
            self.state = "COMMITTING_SCAN"
            self.events.append("commit_final")
            self.final_scan_ids.add(self.current_scan_id)
            self.state = "COMPLETED"
        except RuntimeError as exc:
            name = str(exc)
            self.state = "CANCELLED" if name.startswith("cancel_") else "FAILED"
        finally:
            self.events.append("cleanup_explicit_stop")
            self.events.append("lease_release")
        return False, self.current_scan_id


class Stage8A1HostTests(unittest.TestCase):
    def test_frozen_capture_contract_and_trace(self) -> None:
        sim = Stage8A1Simulation()
        busy, scan_id = sim.start()
        self.assertFalse(busy)
        self.assertEqual(scan_id, "scan_stage8a1_001")
        self.assertEqual(
            len([e for e in sim.events if e.startswith("warmup_acquire:")]), 5
        )
        self.assertFalse(
            any(e.startswith("write_sd:") for e in sim.events[:11])
        )
        self.assertEqual(
            [int(e.rsplit(":", 1)[1]) for e in sim.events
             if e.startswith("capture_pair:")],
            list(range(11)),
        )
        self.assertEqual(
            len([e for e in sim.events if e.startswith("rotate_ccw:3:500:")]),
            10,
        )
        self.assertEqual(
            len([e for e in sim.events if e.startswith("explicit_stop:")]), 10
        )
        self.assertEqual(
            len([e for e in sim.events if e.startswith("settle:200:")]), 10
        )
        self.assertNotIn("rotate_ccw:3:500:11", sim.events)
        self.assertLess(
            sim.events.index("manifest_validate:11"),
            sim.events.index("commit_final"),
        )
        self.assertIn(scan_id, sim.final_scan_ids)

    def test_consecutive_completed_scans_use_new_worker_id_and_lease(self) -> None:
        sim = Stage8A1Simulation()
        busy_a, scan_a = sim.start()
        busy_b, scan_b = sim.start()
        self.assertFalse(busy_a or busy_b)
        self.assertNotEqual(scan_a, scan_b)
        self.assertEqual(sim.worker_count, 2)
        self.assertEqual(sim.lease_acquire_count, 2)

    def test_faults_stop_release_and_never_commit_final(self) -> None:
        faults = (
            "warmup_failure",
            "camera_failure",
            "tof_pair_failure",
            "sd_failure",
            "rotation_failure",
            "cancel_rotate",
            "cancel_settle",
            "cancel_capture",
            "cancel_write",
        )
        for fault in faults:
            with self.subTest(fault=fault):
                sim = Stage8A1Simulation()
                _, scan_id = sim.start(fault)
                self.assertEqual(sim.events[-2:],
                                 ["cleanup_explicit_stop", "lease_release"])
                self.assertNotIn("commit_final", sim.events)
                self.assertNotIn(scan_id, sim.final_scan_ids)

    def test_source_constants_guided_capture_and_manifest_gate(self) -> None:
        for declaration in (
            "REHAB_ENVIRONMENT_SCAN_DIRECTIONS        11U",
            "REHAB_ENVIRONMENT_SCAN_WARMUP_FRAMES      5U",
            "REHAB_ENVIRONMENT_SCAN_ROTATE_SPEED        3U",
            "REHAB_ENVIRONMENT_SCAN_ROTATE_MS         500U",
            "REHAB_ENVIRONMENT_SCAN_SETTLE_MS         200U",
        ):
            self.assertIn(declaration, HEADER)
        self.assertIn("rehab_scan_capture_start_guided", CAPTURE_HEADER)
        self.assertIn("before_commit", CAPTURE_HEADER)
        self.assertIn("manifest_complete", CAPTURE_SOURCE)
        self.assertIn("rehab_scan_capture_commit_directory", CAPTURE_SOURCE)
        self.assertIn("#define REHAB_SCAN_IMAGE_BYTES       460800U",
                      CAPTURE_PRIVATE)

    def test_legacy_capture_start_and_console_remain_unguided(self) -> None:
        legacy_start = CAPTURE_SOURCE[
            CAPTURE_SOURCE.index("esp_err_t rehab_scan_capture_start("):
            CAPTURE_SOURCE.index("esp_err_t rehab_scan_capture_start_guided(")
        ]
        self.assertIn("start_capture(handle, frames, interval_ms, NULL,",
                      legacy_start)
        self.assertIn("rehab_scan_capture_start(handle, &request,",
                      CAPTURE_CONSOLE)
        self.assertIn("before_frame_cb == NULL", CAPTURE_SOURCE)
        for reset in (
            "handle->prepare_cb = NULL;",
            "handle->before_frame_cb = NULL;",
            "handle->before_write_cb = NULL;",
            "handle->after_frame_cb = NULL;",
            "handle->before_commit_cb = NULL;",
            "handle->guided_ctx = NULL;",
        ):
            self.assertIn(reset, CAPTURE_SOURCE)

    def test_source_vehicle_path_warmup_and_console(self) -> None:
        self.assertIn("rehab_car_control_acquire(", COORDINATOR)
        self.assertIn("REHAB_CAR_CONTROL_OWNER_ENVIRONMENT_SCAN", COORDINATOR)
        self.assertIn("REHAB_CAR_DIRECTION_ROTATE_CCW", COORDINATOR)
        self.assertIn("rehab_car_control_stop(", COORDINATOR)
        self.assertIn("rehab_car_control_release(", COORDINATOR)
        self.assertIn("rehab_follow_camera_raw_snapshot_acquire(", COORDINATOR)
        self.assertIn("rehab_follow_camera_raw_snapshot_release(", COORDINATOR)
        self.assertIn('.command = "rehab_env_scan"', COORDINATOR)
        self.assertIn("<start|status|cancel>", COORDINATOR)
        self.assertIn("rehab_environment_scan_console_register(", MAIN)

    def test_stack_fix_and_high_water_points(self) -> None:
        self.assertIn("REHAB_ENVIRONMENT_SCAN_WORKER_STACK_SIZE 8192U", HEADER)
        self.assertIn("rehab_environment_scan_worker_context_t", COORDINATOR)
        self.assertIn("calloc(1, sizeof(*worker_context))", COORDINATOR)
        context_start = COORDINATOR.index("typedef struct {")
        context_end = COORDINATOR.index(
            "} rehab_environment_scan_worker_context_t;"
        )
        context = COORDINATOR[context_start:context_end]
        self.assertIn("rehab_scan_capture_status_t capture_status;", context)
        self.assertIn("char scan_id[REHAB_SCAN_CAPTURE_SCAN_ID_MAX];", context)
        for point in (
            "task start",
            "after warmup",
            "after formal frame %u",
            "before final commit",
            "before upload submit",
            "after upload completion",
            "before worker exit",
        ):
            self.assertIn(point, COORDINATOR)
        self.assertIn("uxTaskGetStackHighWaterMark(worker)", COORDINATOR)
        self.assertIn("remaining_bytes=", COORDINATOR)
        self.assertNotIn("remaining_words=", COORDINATOR)
        self.assertEqual(COORDINATOR.count("static void environment_worker("), 1)
        self.assertNotIn("environment_worker(ctx", COORDINATOR)

    def test_terminal_states_are_not_active(self) -> None:
        active = COORDINATOR[
            COORDINATOR.index("static bool state_active"):
            COORDINATOR.index("const char *rehab_environment_scan_state_name")
        ]
        false_cases = active[active.index(
            "case REHAB_ENVIRONMENT_SCAN_STATE_IDLE"
        ):]
        for state in ("IDLE", "DECISION_READY", "CANCELLED", "FAILED"):
            self.assertIn(
                f"case REHAB_ENVIRONMENT_SCAN_STATE_{state}", false_cases
            )

    def test_stage8a2_coordinator_has_only_narrow_upload_integration(self) -> None:
        coordinator_text = COORDINATOR.lower()
        for forbidden in (
            "rehab_task_decision",
            "latest_successful_scene_result",
            "selected_task",
            "deepseek",
            "wechat",
            "claw_cap",
            "environment_scan_register_capabilities",
        ):
            self.assertNotIn(forbidden, coordinator_text)
        self.assertIn(
            "rehab_scan_transfer_start_auto_decision", coordinator_text
        )
        self.assertIn("rehab_environment_scan_state_analyzing", coordinator_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
