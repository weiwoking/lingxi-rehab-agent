from __future__ import annotations

import threading
import time

from deltar_p4_pipeline.performance_guard import (
    CanaryResult,
    GuardStatus,
    PerformanceGuard,
    PerformanceGuardConfig,
)
import scripts.run_scan_service as service_runner


def guard_with(
    runner,
    recoveries: list[str] | None = None,
) -> PerformanceGuard:
    recoveries = recoveries if recoveries is not None else []
    return PerformanceGuard(
        runner,
        lambda: recoveries.append("recycle"),
        config=PerformanceGuardConfig(
            threshold_ms=20_000,
            periodic_interval_s=600,
            idle_grace_s=5,
            scheduler_poll_s=0.01,
        ),
        start_scheduler=False,
    )


def wait_for_canary(guard: PerformanceGuard) -> dict:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        snapshot = guard.snapshot()
        if snapshot["status"] != "startup_check":
            return snapshot
        time.sleep(0.005)
    raise AssertionError("startup canary did not finish")


def test_startup_and_periodic_canary_refresh_health() -> None:
    calls: list[str] = []

    def runner(source: str, _cancel: threading.Event) -> CanaryResult:
        calls.append(source)
        return CanaryResult("success", wall_ms=11_200, exact_pass=True)

    guard = guard_with(runner)
    assert guard.start() is True
    assert wait_for_canary(guard)["status"] == "healthy"
    guard.force_periodic_due_for_test()
    assert guard.check_periodic_once() is True
    assert calls == ["startup_canary", "periodic_canary"]
    assert guard.snapshot()["last_source"] == "periodic_canary"


def test_upload_due_prevents_canary_and_fast_real_scan_replaces_it() -> None:
    calls: list[str] = []

    def runner(source: str, _cancel: threading.Event) -> CanaryResult:
        calls.append(source)
        return CanaryResult("success", wall_ms=11_000, exact_pass=True)

    guard = guard_with(runner)
    assert guard.start()
    wait_for_canary(guard)
    guard.force_periodic_due_for_test()
    guard.preempt_for_real_traffic()
    assert guard.snapshot()["status"] == "paused_busy"
    assert guard.check_periodic_once() is False
    assert guard.admit_real_scan() is True
    guard.real_scan_completed(11_500)
    guard.real_business_finished()
    assert guard.snapshot()["status"] == "healthy"
    assert guard.snapshot()["last_source"] == "real_scan"
    assert calls == ["startup_canary"]


def test_upload_preempts_running_canary_without_worker_recycle() -> None:
    periodic_started = threading.Event()
    generation = {"value": 7}

    def runner(source: str, cancel: threading.Event) -> CanaryResult:
        if source == "startup_canary":
            return CanaryResult("success", wall_ms=11_000, exact_pass=True)
        periodic_started.set()
        assert cancel.wait(timeout=2)
        return CanaryResult("aborted_by_real_traffic")

    guard = guard_with(runner)
    assert guard.start()
    wait_for_canary(guard)
    guard.force_periodic_due_for_test()
    thread = threading.Thread(target=guard.check_periodic_once)
    thread.start()
    assert periodic_started.wait(timeout=2)
    guard.preempt_for_real_traffic()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert guard.snapshot()["last_check_result"] == "aborted_by_real_traffic"
    assert guard.snapshot()["status"] == "paused_busy"
    assert generation["value"] == 7


def test_slow_real_scan_is_visible_but_never_recycles_service() -> None:
    recoveries: list[str] = []
    guard = guard_with(
        lambda *_: CanaryResult("success", wall_ms=11_000, exact_pass=True),
        recoveries,
    )
    assert guard.start()
    wait_for_canary(guard)
    guard.preempt_for_real_traffic()
    assert guard.admit_real_scan()
    guard.real_scan_completed(52_000)
    assert guard.status == GuardStatus.DEGRADED
    assert recoveries == []
    guard.real_business_finished()
    assert guard.status == GuardStatus.DEGRADED
    assert recoveries == []


def test_performance_bad_exit_never_restarts_supervisor(monkeypatch) -> None:
    codes = iter([service_runner.STARTUP_CANARY_BAD_EXIT])
    launches: list[int] = []

    class FakeProcess:
        _handle = 1

        def __init__(self) -> None:
            self._code = next(codes)

        def wait(self, timeout=None):
            return self._code

        def poll(self):
            return self._code

    monkeypatch.setattr(service_runner.subprocess, "Popen", lambda *a, **k: FakeProcess())
    monkeypatch.setattr(service_runner, "_assign_kill_on_close_job", lambda _p: None)
    monkeypatch.setattr(
        service_runner,
        "_child_command",
        lambda attempt: launches.append(attempt) or [str(attempt)],
    )
    assert service_runner._supervise_service_sessions() == service_runner.STARTUP_CANARY_BAD_EXIT
    assert launches == [0]


def test_startup_bad_is_degraded_telemetry_not_admission_gate() -> None:
    guard = guard_with(
        lambda *_: CanaryResult("success", wall_ms=55_000, exact_pass=True)
    )
    assert guard.start() is True
    snapshot = wait_for_canary(guard)
    assert snapshot["status"] == "degraded"
    assert snapshot["ready"] is False
    assert guard.admit_real_scan() is True


def test_startup_exception_is_nonfatal_and_periodic_retry_remains_available() -> None:
    guard = guard_with(lambda *_: (_ for _ in ()).throw(RuntimeError("synthetic")))
    started = time.monotonic()
    assert guard.start() is True
    assert time.monotonic() - started < 0.25
    snapshot = wait_for_canary(guard)
    assert snapshot["status"] == "degraded"
    assert snapshot["last_check_result"] == "bad"
    assert guard.admit_real_scan() is True
    guard.real_business_finished()
    guard.force_periodic_due_for_test()
    assert guard.check_periodic_once() is True


def test_real_traffic_preempts_background_startup_canary() -> None:
    started = threading.Event()

    def runner(_source: str, cancel: threading.Event) -> CanaryResult:
        started.set()
        assert cancel.wait(timeout=2.0)
        return CanaryResult("aborted_by_real_traffic")

    guard = guard_with(runner)
    assert guard.start() is True
    assert started.wait(timeout=1.0)
    guard.preempt_for_real_traffic()
    assert guard.admit_real_scan() is True
    deadline = time.monotonic() + 2.0
    while guard.snapshot()["last_check_result"] is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert guard.snapshot()["last_check_result"] == "aborted_by_real_traffic"
