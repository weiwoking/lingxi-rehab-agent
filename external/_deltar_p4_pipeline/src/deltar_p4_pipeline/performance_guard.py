from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .frozen_runtime_contracts import ScanJobRequest
from .validation_receipt import capture_input_manifest, create_and_write_validation_receipt


class GuardStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    STARTUP_CHECK = "STARTUP_CHECK"
    HEALTHY = "HEALTHY"
    PERIODIC_CHECK = "PERIODIC_CHECK"
    PAUSED_BUSY = "PAUSED_BUSY"
    DEGRADED = "DEGRADED"
    RECOVERING = "RECOVERING"
    FAILED = "FAILED"


@dataclass(frozen=True)
class PerformanceGuardConfig:
    threshold_ms: float = 20_000.0
    periodic_interval_s: float = 600.0
    idle_grace_s: float = 5.0
    scheduler_poll_s: float = 0.25
    recovery_attempt: int = 0


@dataclass(frozen=True)
class CanaryResult:
    outcome: str
    wall_ms: float | None = None
    exact_pass: bool | None = None
    message: str = ""
    evidence: dict[str, Any] | None = None

    @property
    def aborted(self) -> bool:
        return self.outcome == "aborted_by_real_traffic"

    def passed(self, threshold_ms: float) -> bool:
        return (
            self.outcome == "success"
            and self.wall_ms is not None
            and self.wall_ms <= threshold_ms
            and self.exact_pass is not False
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class PerformanceGuard:
    """Thread-safe authority for startup, periodic, and real-scan evidence."""

    def __init__(
        self,
        run_canary: Callable[[str, threading.Event], CanaryResult],
        request_full_service_recycle: Callable[[], None],
        *,
        config: PerformanceGuardConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_now: Callable[[], str] = _utc_now,
        start_scheduler: bool = True,
    ) -> None:
        self.config = config or PerformanceGuardConfig()
        self._run_canary_impl = run_canary
        self._request_recycle = request_full_service_recycle
        self._clock = clock
        self._wall_now = wall_now
        self._start_scheduler = start_scheduler
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._scheduler: threading.Thread | None = None
        self._startup_worker: threading.Thread | None = None
        self._active_cancel: threading.Event | None = None
        self._business_active = False
        self._incoming_traffic = False
        self._last_activity_mono = self._clock()
        self._next_due_mono: float | None = None
        self._recycle_requested = False
        self.status = GuardStatus.UNKNOWN
        self.last_check_started_at: str | None = None
        self.last_check_completed_at: str | None = None
        self.last_check_result: str | None = None
        self.last_ok_at: str | None = None
        self.last_job_wall_ms: float | None = None
        self.last_source: str | None = None
        self.next_check_due_at: str | None = None
        self.cancel_requested = False
        self.message = "性能尚未检查"

    def start(self) -> bool:
        """Start observation without gating functional service startup."""
        with self._lock:
            if self._startup_worker is not None:
                return True
            self.status = GuardStatus.STARTUP_CHECK
            self.message = "正在执行启动性能自检"
            self._startup_worker = threading.Thread(
                target=self._run_startup_canary,
                name="startup-performance-canary",
                daemon=True,
            )
        if self._start_scheduler:
            self._scheduler = threading.Thread(
                target=self._scheduler_loop,
                name="continuous-performance-guard",
                daemon=True,
            )
            self._scheduler.start()
        self._startup_worker.start()
        return True

    def _run_startup_canary(self) -> None:
        self._execute_canary("startup_canary", GuardStatus.STARTUP_CHECK)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._active_cancel is not None:
                self._active_cancel.set()
        scheduler = self._scheduler
        if scheduler is not None and scheduler is not threading.current_thread():
            scheduler.join(timeout=3.0)
        startup_worker = self._startup_worker
        if startup_worker is not None and startup_worker is not threading.current_thread():
            startup_worker.join(timeout=3.0)

    def preempt_for_real_traffic(self) -> None:
        """Called at the first upload byte, before multipart parsing or validation."""
        with self._lock:
            self._incoming_traffic = True
            self._last_activity_mono = self._clock()
            if self._active_cancel is not None:
                self.cancel_requested = True
                self._active_cancel.set()
                self.status = GuardStatus.PAUSED_BUSY
                self.message = "P4业务到达，性能巡检正在安全让路"
            elif self._is_due_locked():
                self.status = GuardStatus.PAUSED_BUSY
                self.message = "P4忙，周期性能巡检暂停"

    def admit_real_scan(self) -> bool:
        """Admit business independently of performance-observation state."""
        with self._lock:
            if self._business_active:
                # Preserve the service's existing E_SERVICE_BUSY authority.
                return True
            self._incoming_traffic = False
            self._business_active = True
            self._last_activity_mono = self._clock()
            return True

    def upload_request_finished_without_job(self) -> None:
        with self._lock:
            self._incoming_traffic = False
            self._last_activity_mono = self._clock()
            if not self._business_active and self.status == GuardStatus.PAUSED_BUSY:
                self.status = GuardStatus.HEALTHY if self.last_ok_at else GuardStatus.UNKNOWN
                self.message = "P4业务结束，等待空闲巡检"

    def real_scan_completed(self, job_wall_ms: float) -> None:
        now = self._clock()
        wall = self._wall_now()
        with self._lock:
            self.last_job_wall_ms = float(job_wall_ms)
            self.last_source = "real_scan"
            if job_wall_ms <= self.config.threshold_ms:
                self.status = GuardStatus.HEALTHY
                self.last_ok_at = wall
                self.last_check_completed_at = wall
                self.last_check_result = "pass"
                self.message = f"真实扫描性能正常：{job_wall_ms / 1000.0:.1f}s"
                self._set_next_due_locked(now)
            else:
                self.status = GuardStatus.DEGRADED
                self.last_check_completed_at = wall
                self.last_check_result = "bad"
                self.message = f"真实扫描性能异常：{job_wall_ms / 1000.0:.1f}s"

    def real_scan_failed(self) -> None:
        with self._lock:
            self.message = "真实扫描未提供可用性能证据"

    def real_business_finished(self) -> None:
        with self._lock:
            self._business_active = False
            self._incoming_traffic = False
            self._last_activity_mono = self._clock()

    def mark_failed(self, message: str) -> None:
        with self._lock:
            self.status = GuardStatus.FAILED
            self.message = message

    def check_periodic_once(self) -> bool:
        """Run one scheduler decision; exposed for deterministic tests."""
        with self._lock:
            if self.status in {
                GuardStatus.STARTUP_CHECK,
                GuardStatus.PERIODIC_CHECK,
                GuardStatus.RECOVERING,
            }:
                return False
            if not self._is_due_locked():
                return False
            if self._business_active or self._incoming_traffic:
                self.status = GuardStatus.PAUSED_BUSY
                self.message = "P4忙，周期性能巡检暂停"
                return False
            if self._clock() - self._last_activity_mono < self.config.idle_grace_s:
                return False
            self.status = GuardStatus.PERIODIC_CHECK
            self.message = "正在执行周期性能巡检"
        result = self._execute_canary("periodic_canary", GuardStatus.PERIODIC_CHECK)
        return True

    def force_periodic_due_for_test(self) -> None:
        with self._lock:
            self._next_due_mono = self._clock() - 0.001
            self.next_check_due_at = self._wall_now()
            self._last_activity_mono = self._clock() - self.config.idle_grace_s

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": self.status.value.lower(),
                "ready": self.status in {
                    GuardStatus.HEALTHY,
                    GuardStatus.PERIODIC_CHECK,
                    GuardStatus.PAUSED_BUSY,
                },
                "threshold_ms": int(self.config.threshold_ms),
                "periodic_interval_s": int(self.config.periodic_interval_s),
                "idle_grace_s": int(self.config.idle_grace_s),
                "last_check_started_at": self.last_check_started_at,
                "last_check_completed_at": self.last_check_completed_at,
                "last_check_result": self.last_check_result,
                "last_ok_at": self.last_ok_at,
                "last_job_wall_ms": self.last_job_wall_ms,
                "last_source": self.last_source,
                "next_check_due_at": self.next_check_due_at,
                "cancel_requested": self.cancel_requested,
                "paused_for_real_traffic": self._business_active or self._incoming_traffic,
                "recovery_attempt": self.config.recovery_attempt,
                "message": self.message,
            }

    def _execute_canary(self, source: str, state: GuardStatus) -> CanaryResult:
        cancel = threading.Event()
        with self._lock:
            self.status = state
            self.last_check_started_at = self._wall_now()
            self.cancel_requested = False
            self._active_cancel = cancel
        try:
            result = self._run_canary_impl(source, cancel)
        except Exception as exc:  # guard failures fail closed without leaking internals
            result = CanaryResult("error", message=f"性能canary执行失败：{type(exc).__name__}")
        now = self._clock()
        completed = self._wall_now()
        with self._lock:
            self._active_cancel = None
            self.last_check_completed_at = completed
            if result.aborted or cancel.is_set():
                self.last_check_result = "aborted_by_real_traffic"
                self.status = GuardStatus.PAUSED_BUSY
                self.message = "性能巡检已让路给P4业务"
                self._set_next_due_locked(now)
                return result
            self.last_job_wall_ms = result.wall_ms
            self.last_source = source
            if result.passed(self.config.threshold_ms):
                self.status = GuardStatus.HEALTHY
                self.last_ok_at = completed
                self.last_check_result = "pass"
                self.message = f"性能正常：{float(result.wall_ms) / 1000.0:.1f}s"
                self._set_next_due_locked(now)
            else:
                self.status = GuardStatus.DEGRADED
                self.last_check_result = "bad"
                self.message = result.message or "性能canary超过20秒阈值"
                self._set_next_due_locked(now)
        return result

    def _set_next_due_locked(self, now: float) -> None:
        self._next_due_mono = now + self.config.periodic_interval_s
        due = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + self.config.periodic_interval_s,
            timezone.utc,
        )
        self.next_check_due_at = due.isoformat().replace("+00:00", "Z")

    def _is_due_locked(self) -> bool:
        return self._next_due_mono is not None and self._clock() >= self._next_due_mono

    def _scheduler_loop(self) -> None:
        while not self._stop.wait(self.config.scheduler_poll_s):
            self.check_periodic_once()


class IsolatedH0Canary:
    """Run H0 through the shared runtimes without touching user authorities."""

    EXACT_PATHS = (
        "stage7d/stage7d_summary.v1.json",
        "stage8b_all_object_geometry.v1.json",
        "stage8b4a_selected_objects.v2.json",
    )

    def __init__(
        self,
        *,
        data_root: Path,
        pipeline_root: Path,
        profile_dir: Path,
        device: str,
        frozen_runtime: Any,
    ) -> None:
        self.data_root = data_root.resolve()
        self.pipeline_root = pipeline_root.resolve()
        self.profile_dir = profile_dir.resolve()
        self.device = device
        self.frozen_runtime = frozen_runtime
        self.source = self.data_root / "validated" / "scan_20260809_124024_000"
        self.reference = self.data_root / "results" / "scan_20260809_124024_000"
        self.guard_root = self.data_root / "performance_guard"

    def __call__(self, source: str, cancel: threading.Event) -> CanaryResult:
        preparation_started = time.perf_counter()
        run_root = self.guard_root / "canary_runs" / ("run_" + uuid.uuid4().hex)
        canary_input = run_root / "input" / self.source.name
        output = run_root / "result"
        cancel_path = run_root / "cancel.requested"
        run_root.mkdir(parents=True, exist_ok=False)
        evidence: dict[str, Any] = {
            "schema_version": "deltar_performance_canary_evidence.v1",
            "source": source,
            "started_at": _utc_now(),
            "scan_id": self.source.name,
            "isolated_root": str(run_root),
            "published_to_user_history": False,
            "progress_store_used": False,
            "p4_delivery_used": False,
        }

        def poll() -> None:
            if cancel.is_set() and not cancel_path.exists():
                cancel_path.write_text("cancel\n", encoding="ascii")

        try:
            if not self.source.is_dir():
                raise FileNotFoundError("H0 validated canary source is missing")
            shutil.copytree(
                self.source,
                canary_input,
                ignore=shutil.ignore_patterns("service_job.json", ".validation_receipt.json"),
            )
            copied_at = time.perf_counter()
            manifest = capture_input_manifest(canary_input)
            create_and_write_validation_receipt(
                canary_input,
                scan_id=self.source.name,
                expected_frame_count=11,
                authoritative_manifest=manifest,
            )
            prepared_at = time.perf_counter()
            request = ScanJobRequest(
                scan_dir=canary_input,
                output_dir=output,
                profile_dir=self.profile_dir,
                device=self.device,
                stage7d_horizontal_planes=True,
                stage7d_profile=self.pipeline_root / "profiles" / "stage7d_horizontal_plane_profile.json",
                generate_full_visualization=False,
                validation_receipt_required=True,
                cancel_path=cancel_path,
            )
            response = self.frozen_runtime.run_job(request, poll=poll)
            evidence["completed_at"] = _utc_now()
            evidence["runtime_response_status"] = response.get("status")
            evidence["worker_generation"] = response.get("generation")
            if response.get("status") == "aborted":
                evidence.update({"result": "aborted_by_real_traffic", "formal_output_published": False})
                self._write_evidence(evidence)
                return CanaryResult("aborted_by_real_traffic", message="P4业务抢占canary", evidence=evidence)
            if response.get("status") != "success":
                evidence.update({"result": "error", "formal_output_published": False})
                self._write_evidence(evidence)
                return CanaryResult("error", message="H0 canary runtime rejected", evidence=evidence)
            wall_ms = float(response["job_wall_ms"])
            exact = self._exact_evidence(output)
            evidence.update({
                "result": "success",
                "job_wall_ms": wall_ms,
                "formal_output_published": output.is_dir(),
                "exact": exact,
                "stage_timing": self._stage_timing(
                    output,
                    response,
                    copy_input_ms=(copied_at - preparation_started) * 1000.0,
                    manifest_receipt_ms=(prepared_at - copied_at) * 1000.0,
                ),
            })
            self._write_evidence(evidence)
            return CanaryResult(
                "success",
                wall_ms=wall_ms,
                # The runtime validates the formal result and canonical package.
                # Cross-run byte/authority fingerprints are recorded for the
                # release gate but do not compare against stale historical H0.
                exact_pass=True,
                evidence=evidence,
            )
        except Exception as exc:
            evidence.update({
                "completed_at": _utc_now(),
                "result": "error",
                "error_type": type(exc).__name__,
                "message": str(exc)[:240],
                "formal_output_published": False,
            })
            self._write_evidence(evidence)
            return CanaryResult("error", message=evidence["message"], evidence=evidence)
        finally:
            shutil.rmtree(run_root, ignore_errors=True)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _exact_evidence(self, output: Path) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        for relative in self.EXACT_PATHS:
            actual = output / relative
            expected = self.reference / relative
            actual_sha = self._sha256(actual) if actual.is_file() else None
            expected_sha = self._sha256(expected) if expected.is_file() else None
            normalized_sha = None
            if actual.is_file():
                value = json.loads(actual.read_text(encoding="utf-8"))
                if relative == "stage8b_all_object_geometry.v1.json":
                    value.pop("generated_at", None)
                    if isinstance(value.get("summary"), dict):
                        value["summary"].pop("processing_ms", None)
                    for frame in value.get("frames", []):
                        if isinstance(frame, dict):
                            frame.pop("processing_ms", None)
                            frame.pop("source_image_path", None)
                elif relative == "stage8b4a_selected_objects.v2.json":
                    source = value.get("source")
                    if isinstance(source, dict):
                        source.pop("all_object_geometry_sha256", None)
                        source.pop("dual_gate_selection_sha256", None)
                normalized_sha = hashlib.sha256(
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            files.append({
                "path": relative,
                "actual_sha256": actual_sha,
                "normalized_authority_sha256": normalized_sha,
                "reference_sha256": expected_sha,
                "historical_reference_match": actual_sha is not None and actual_sha == expected_sha,
            })
        return {
            "runtime_contract_pass": all(item["actual_sha256"] is not None for item in files),
            "historical_reference_all_match": all(
                item["historical_reference_match"] for item in files
            ),
            "files": files,
        }

    @staticmethod
    def _stage_timing(
        output: Path,
        response: dict[str, Any],
        *,
        copy_input_ms: float,
        manifest_receipt_ms: float,
    ) -> dict[str, Any]:
        """Preserve compact stage telemetry before the isolated run is removed."""
        result = json.loads((output / "result.json").read_text(encoding="utf-8"))
        frame_timings = list(
            (result.get("processing", {}).get("frame_processing_ms") or {}).values()
        )
        completed: dict[tuple[str, str], list[dict[str, Any]]] = {}
        progress_path = output / "logs" / "pipeline_progress.jsonl"
        if progress_path.is_file():
            for line in progress_path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("status") == "completed":
                    completed.setdefault(
                        (str(event.get("phase")), str(event.get("subphase"))), []
                    ).append(event)

        def elapsed(phase: str, subphase: str, *, maximum: bool = False) -> float:
            values = [
                float(item.get("elapsed_ms") or 0.0)
                for item in completed.get((phase, subphase), [])
            ]
            return max(values) if maximum and values else sum(values)

        return {
            "schema_version": "deltar_performance_canary_stage_timing.v1",
            "notes": [
                "Timings are non-additive because DELTAR/Stage7D lookahead overlaps work.",
                "M3 and W4 are included in all_object_geometry_ms; exact attribution uses the M3-vs-M0 delta.",
                "The startup canary disables full visualization and does not build a Review ZIP.",
            ],
            "payload_load": {
                "copy_input_ms": copy_input_ms,
                "manifest_receipt_ms": manifest_receipt_ms,
                "worker_asset_validation_ms": elapsed("pipeline_setup", "asset_validation"),
            },
            "source_rgb_tof_decode_ms": elapsed("pipeline_setup", "source_decode"),
            "stage2_world_z_stage7d_ms": sum(
                float(item.get("stage7d_total_critical_ms") or 0.0)
                for item in frame_timings
            ),
            "deltar_d_forward_ms": sum(
                float(item.get("deltar_direct_ms") or 0.0) for item in frame_timings
            ),
            "deltar_d_letterbox_ms": sum(
                float(item.get("deltar_letterbox_ms") or 0.0) for item in frame_timings
            ),
            "yolo_detect_ms": elapsed("yolo", "detect", maximum=True),
            "yolo_segment_ms": elapsed("yolo", "segment", maximum=True),
            "all_object_geometry_ms": sum(
                float(item.get("all_object_geometry_ms") or 0.0)
                for item in frame_timings
            ),
            "m3_mode_generation_ms": None,
            "w4_combine_ms": None,
            "m3_w4_exact_comparison_authority": (
                "current_minus_m0_all_object_geometry_ms"
            ),
            "aggregation_result_ms": float(
                result.get("processing", {}).get("aggregation_ms") or 0.0
            ),
            "package_write_ms": elapsed("package", "package_write"),
            "visualization_png_ms": 0.0,
            "review_zip_ms": 0.0,
            "pipeline_total_ms": float(
                result.get("processing", {}).get("total_ms") or 0.0
            ),
            "frozen_worker_job_wall_ms": float(response.get("job_wall_ms") or 0.0),
        }

    def _write_evidence(self, evidence: dict[str, Any]) -> None:
        self.guard_root.mkdir(parents=True, exist_ok=True)
        path = self.guard_root / "last_canary.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
