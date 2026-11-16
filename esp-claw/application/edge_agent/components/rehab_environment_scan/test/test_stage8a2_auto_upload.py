from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
SOURCE = (COMPONENT / "src" / "rehab_environment_scan.c").read_text(
    encoding="utf-8"
)
HEADER = (COMPONENT / "include" / "rehab_environment_scan.h").read_text(
    encoding="utf-8"
)


@dataclass
class AutoUploadSimulation:
    state: str = "IDLE"
    scan_id: str = "scan_stage8a2_001"
    final_path: str = ""
    retryable: bool = False
    events: list[str] = field(default_factory=list)

    def run(self, outcome: str) -> None:
        for index in range(5):
            self.events.append(f"warmup:{index}")
        for sequence in range(11):
            if sequence:
                self.events.extend([
                    f"rotate_ccw:3:500:{sequence}",
                    f"stop:{sequence}",
                    f"settle:200:{sequence}",
                ])
            self.events.append(f"capture:{sequence}")
        self.final_path = f"/sdcard/rehab/scans/{self.scan_id}"
        self.events.extend([
            "commit_final",
            "vehicle_stop_release",
            f"upload_only:{self.scan_id}",
        ])
        self.state = "UPLOADING"
        if outcome == "success":
            self.state = "COMPLETED"
            return
        self.retryable = True
        self.state = "CANCELLED" if outcome == "cancel" else "FAILED"


def test_success_trace_preserves_stage8a1_and_binds_exact_scan_id() -> None:
    sim = AutoUploadSimulation()
    sim.run("success")
    assert len([event for event in sim.events if event.startswith("warmup:")]) == 5
    assert len([
        event for event in sim.events
        if event.startswith("rotate_ccw:3:500:")
    ]) == 10
    assert len([event for event in sim.events if event.startswith("stop:")]) == 10
    assert len([
        event for event in sim.events if event.startswith("settle:200:")
    ]) == 10
    assert [event for event in sim.events if event.startswith("capture:")] == [
        f"capture:{index}" for index in range(11)
    ]
    assert sim.events.index("commit_final") < sim.events.index(
        f"upload_only:{sim.scan_id}"
    )
    assert "upload_only:latest" not in sim.events
    assert sim.state == "COMPLETED"


def test_failure_and_cancel_retain_final_for_same_id_retry() -> None:
    for outcome, expected in (
        ("connection_refused", "FAILED"),
        ("timeout", "FAILED"),
        ("http_4xx", "FAILED"),
        ("http_5xx", "FAILED"),
        ("validation_failure", "FAILED"),
        ("cancel", "CANCELLED"),
    ):
        sim = AutoUploadSimulation()
        sim.run(outcome)
        assert sim.state == expected
        assert sim.final_path.endswith(sim.scan_id)
        assert sim.retryable
        assert "demote_final_to_incomplete" not in sim.events
        assert len([
            event for event in sim.events if event.startswith("capture:")
        ]) == 11


def test_source_uses_transfer_worker_and_preserves_final_path() -> None:
    assert "REHAB_ENVIRONMENT_SCAN_STATE_UPLOADING" in HEADER
    assert "rehab_scan_transfer_start_auto_decision(" in SOURCE
    assert "REHAB_SCAN_TRANSFER_MODE_UPLOAD_ONLY" in SOURCE
    assert "worker_context->scan_id" in SOURCE
    assert "REHAB_SCAN_TRANSFER_STATE_COMPLETED" in SOURCE
    assert "REHAB_ENVIRONMENT_SCAN_STATE_ANALYZING" in SOURCE
    assert "REHAB_ENVIRONMENT_SCAN_STATE_DECISION_READY" in SOURCE
    assert "REHAB_SCAN_TRANSFER_STATE_FAILED" in SOURCE
    assert "worker_context->capture_status.final_path" in SOURCE
    assert "rehab_scan_capture_demote_final" not in SOURCE
    assert "rehab_scan_transfer_status_t transfer_status;" in SOURCE


def test_cancel_routes_to_capture_or_transfer_by_phase() -> None:
    cancel = SOURCE[
        SOURCE.index("esp_err_t rehab_environment_scan_cancel"):
        SOURCE.index("static int environment_console_handler")
    ]
    assert "was_uploading" in cancel
    assert "rehab_scan_transfer_abort" in cancel
    assert "rehab_scan_capture_abort" in cancel
    assert "rehab_car_control_stop" in cancel


def test_stack_boundary_keeps_transfer_state_off_worker_stack() -> None:
    worker = SOURCE[
        SOURCE.index("static void environment_worker("):
        SOURCE.index("esp_err_t rehab_environment_scan_create")
    ]
    assert "rehab_scan_transfer_status_t transfer_status" not in worker
    assert "rehab_scan_transfer_http_" not in SOURCE
    assert "before upload submit" in SOURCE
    assert "after upload completion" in SOURCE
    assert "remaining_bytes=" in SOURCE
    assert "remaining_words=" not in SOURCE
