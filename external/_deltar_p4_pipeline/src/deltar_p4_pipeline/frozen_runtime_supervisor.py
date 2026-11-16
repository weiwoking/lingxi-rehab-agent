from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .frozen_runtime_contracts import (
    DEFAULT_DELTAR_CHECKPOINT,
    DEFAULT_DELTAR_WORKTREE,
    ScanJobRequest,
    identity_hints_unchanged,
)

READY_SCHEMA = "deltar_frozen_scan_runtime_ready.v1"
COMMAND_SCHEMA = "deltar_frozen_scan_runtime_command.v1"
RESPONSE_SCHEMA = "deltar_frozen_scan_runtime_response.v1"


class FrozenRuntimeUnavailable(RuntimeError):
    pass


class FrozenScanRuntimeSupervisor:
    def __init__(
        self,
        pipeline_root: Path,
        data_root: Path,
        worker_python: Path,
        profile_bundle: Path,
        *,
        device: str = "cuda:0",
        calibration_paths: list[Path] | None = None,
        yolo_runtime_endpoint: str | None = None,
        yolo_runtime_token: str | None = None,
        startup_timeout_s: float = 180.0,
        job_timeout_s: float = 600.0,
        progress_emit: Callable[..., Any] | None = None,
        testing: bool = False,
        worktree: Path = DEFAULT_DELTAR_WORKTREE,
        checkpoint: Path = DEFAULT_DELTAR_CHECKPOINT,
    ) -> None:
        self.pipeline_root = pipeline_root.resolve()
        self.data_root = data_root.resolve()
        self.worker_python = worker_python.resolve()
        self.profile_bundle = profile_bundle.resolve()
        self.device = device
        self.calibration_paths = [path.resolve() for path in (calibration_paths or [])]
        self.yolo_runtime_endpoint = yolo_runtime_endpoint
        self.yolo_runtime_token = yolo_runtime_token
        self.startup_timeout_s = startup_timeout_s
        self.job_timeout_s = job_timeout_s
        self.progress_emit = progress_emit
        self.testing = testing
        self.worktree = worktree.resolve()
        self.checkpoint = checkpoint.resolve()
        self.state = "STARTING"
        self.generation = 0
        self.process: subprocess.Popen[str] | None = None
        self.worker_pid: int | None = None
        self.ready_payload: dict[str, Any] | None = None
        self.last_error: dict[str, Any] | None = None
        self.jobs_completed = 0
        self.recoveries = 0
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._reader_threads: list[threading.Thread] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        self._last_ping_at = 0.0
        progress = self.data_root / "progress"
        self._startup_path = progress / "frozen_scan_runtime_startup.json"
        self._stdout_log = progress / "frozen_scan_runtime.stdout.log"
        self._stderr_log = progress / "frozen_scan_runtime.stderr.log"

    def _emit(self, state: str, *, error: BaseException | None = None) -> None:
        self.state = state
        if error is not None:
            self.last_error = {"type": type(error).__name__, "message": str(error)}
        if self.progress_emit is not None:
            try:
                self.progress_emit(
                    f"runtime.frozen_scan.{state.casefold()}",
                    status="error" if state == "FAILED" else "info",
                    message=f"Frozen Scan Runtime {state}",
                    error_code="E_FROZEN_RUNTIME" if error is not None else None,
                    error_detail=str(error) if error is not None else None,
                    details={"generation": self.generation, "worker_pid": self.worker_pid},
                )
            except Exception:
                pass

    def status_payload(self) -> dict[str, Any]:
        process_alive = self.process is not None and self.process.poll() is None
        return {
            "schema_version": "deltar_frozen_scan_runtime_status.v1",
            "state": self.state,
            "ready": self.state == "READY" and process_alive,
            "generation": self.generation,
            "worker_pid": self.worker_pid,
            "process_alive": process_alive,
            "jobs_completed": self.jobs_completed,
            "recoveries": self.recoveries,
            "startup_timing": (self.ready_payload or {}).get("timing"),
            "runtime_identity": (self.ready_payload or {}).get("runtime_identity"),
            "gpu": (self.ready_payload or {}).get("gpu"),
            "last_error": self.last_error,
        }

    def _environment(self) -> dict[str, str]:
        allowed = (
            "SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP", "PATH", "PATHEXT",
            "CUDA_PATH", "CUDA_VISIBLE_DEVICES", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
            "APPDATA", "LOCALAPPDATA",
            "DELTAR_PHASE6_LAB", "DELTAR_PHASE6_GPU_LOOKAHEAD",
            "DELTAR_PHASE6_STAGE7D_LOOKAHEAD",
            "DELTAR_PHASE6_SOURCE_PNG_PARALLEL",
            "DELTAR_PHASE6_STAGE6_REQUIRED_REGION",
            "DELTAR_PHASE6_STAGE7D_RANSAC_BATCH",
            "DELTAR_PHASE6_STAGE7D_PNG_PAIR_PARALLEL",
        )
        env = {key: os.environ[key] for key in allowed if key in os.environ}
        env["PYTHONPATH"] = str((self.pipeline_root / "src").resolve())
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONUTF8"] = "1"
        if self.yolo_runtime_endpoint:
            env["DELTAR_YOLO_RUNTIME_ENDPOINT"] = self.yolo_runtime_endpoint
        if self.yolo_runtime_token:
            env["DELTAR_YOLO_RUNTIME_TOKEN"] = self.yolo_runtime_token
        if self.testing:
            env["DELTAR_FROZEN_RUNTIME_TESTING"] = "1"
        return env

    def _pipe_reader(self, stream: Any, *, stderr: bool, messages: queue.Queue[dict[str, Any]]) -> None:
        log_path = self._stderr_log if stderr else self._stdout_log
        try:
            with log_path.open("a", encoding="utf-8", newline="\n") as log:
                for line in iter(stream.readline, ""):
                    log.write(line)
                    log.flush()
                    if not stderr:
                        try:
                            messages.put(json.loads(line))
                        except json.JSONDecodeError:
                            messages.put({"status": "protocol_error", "raw": line[-2000:]})
        except OSError:
            pass
        finally:
            if not stderr:
                messages.put({"status": "eof"})

    def _write_startup_config(self, generation: int) -> None:
        self._startup_path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "schema_version": "deltar_frozen_scan_runtime_startup.v1",
            "profile_bundle": str(self.profile_bundle),
            "device": self.device,
            "generation": generation,
            "calibration_paths": [str(path) for path in self.calibration_paths],
            "worktree": str(self.worktree),
            "checkpoint": str(self.checkpoint),
        }
        temporary = self._startup_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, self._startup_path)

    def _spawn_worker(self, *, recovering: bool) -> None:
        self._emit("RECOVERING" if recovering else "STARTING")
        self._terminate_worker()
        next_generation = self.generation + 1
        self._write_startup_config(next_generation)
        self._messages = queue.Queue()
        messages = self._messages
        worker = self.pipeline_root / "workers" / "frozen_scan_runtime_worker.py"
        if not self.worker_python.is_file() or not worker.is_file():
            raise FrozenRuntimeUnavailable("frozen runtime executable or worker is missing")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            [str(self.worker_python), str(worker), "--serve", str(self._startup_path)],
            cwd=self.pipeline_root,
            env=self._environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        self.worker_pid = self.process.pid
        stdout_thread = threading.Thread(target=self._pipe_reader, args=(self.process.stdout,), kwargs={"stderr": False, "messages": messages}, name="frozen-runtime-stdout", daemon=True)
        stderr_thread = threading.Thread(target=self._pipe_reader, args=(self.process.stderr,), kwargs={"stderr": True, "messages": messages}, name="frozen-runtime-stderr", daemon=True)
        self._reader_threads = [stdout_thread, stderr_thread]
        stdout_thread.start()
        stderr_thread.start()
        try:
            ready = messages.get(timeout=self.startup_timeout_s)
        except queue.Empty as exc:
            self._terminate_worker()
            self._emit("FAILED", error=exc)
            raise FrozenRuntimeUnavailable("frozen runtime startup timed out") from exc
        if ready.get("schema_version") != READY_SCHEMA or ready.get("status") != "READY":
            error = FrozenRuntimeUnavailable(f"frozen runtime startup failed: {ready}")
            self._terminate_worker()
            self._emit("FAILED", error=error)
            raise error
        self.generation = next_generation
        self.worker_pid = int(ready["pid"])
        self.ready_payload = ready
        if recovering:
            self.recoveries += 1
        self.last_error = None
        self._last_ping_at = time.monotonic()
        self._emit("READY")

    def start(self) -> None:
        with self._lock:
            if self.state == "READY" and self.process is not None and self.process.poll() is None:
                return
            self._stop.clear()
            self.data_root.mkdir(parents=True, exist_ok=True)
            self._spawn_worker(recovering=self.generation > 0)
            self._monitor = threading.Thread(target=self._monitor_loop, name="frozen-runtime-monitor", daemon=True)
            self._monitor.start()

    def _monitor_loop(self) -> None:
        while not self._stop.wait(1.0):
            if not self._lock.acquire(blocking=False):
                continue
            try:
                if self.state == "READY" and (self.process is None or self.process.poll() is not None):
                    try:
                        self._spawn_worker(recovering=True)
                    except FrozenRuntimeUnavailable as exc:
                        self._emit("FAILED", error=exc)
                elif self.state == "READY" and time.monotonic() - self._last_ping_at >= 5.0:
                    try:
                        response = self._send(
                            {"schema_version": COMMAND_SCHEMA, "command": "ping"},
                            timeout_s=5.0,
                        )
                        if response.get("status") != "pong" or not response.get("identity_unchanged"):
                            raise FrozenRuntimeUnavailable(f"frozen runtime ping invalid: {response}")
                        self._last_ping_at = time.monotonic()
                        if isinstance(response.get("gpu"), dict) and self.ready_payload is not None:
                            self.ready_payload["gpu"] = response["gpu"]
                    except FrozenRuntimeUnavailable:
                        self._spawn_worker(recovering=True)
            finally:
                self._lock.release()

    def _terminate_worker(self) -> None:
        process = self.process
        runtime_pid = self.worker_pid
        self.process = None
        self.worker_pid = None
        if process is None:
            return
        if process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.write(json.dumps({"schema_version": COMMAND_SCHEMA, "command": "shutdown"}) + "\n")
                    process.stdin.flush()
                process.wait(timeout=5.0)
            except (OSError, subprocess.TimeoutExpired):
                if runtime_pid is not None:
                    try:
                        os.kill(runtime_pid, signal.SIGTERM)
                    except OSError:
                        pass
                process.terminate()
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5.0)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        for thread in self._reader_threads:
            thread.join(timeout=1.0)
        self._reader_threads = []

    def _send(self, command: dict[str, Any], *, timeout_s: float, poll: Callable[[], None] | None = None) -> dict[str, Any]:
        process = self.process
        if process is None or process.poll() is not None or process.stdin is None:
            raise FrozenRuntimeUnavailable("frozen runtime worker is not alive")
        try:
            process.stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except OSError as exc:
            raise FrozenRuntimeUnavailable(f"frozen runtime IPC write failed: {exc}") from exc
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FrozenRuntimeUnavailable("frozen runtime IPC timed out")
            try:
                response = self._messages.get(timeout=min(0.1, remaining))
            except queue.Empty:
                if poll is not None:
                    poll()
                continue
            if response.get("status") == "eof":
                raise FrozenRuntimeUnavailable("frozen runtime worker exited during command")
            return response

    def ping(self) -> dict[str, Any]:
        with self._lock:
            response = self._send({"schema_version": COMMAND_SCHEMA, "command": "ping"}, timeout_s=5.0)
            if response.get("status") != "pong" or not response.get("identity_unchanged"):
                raise FrozenRuntimeUnavailable(f"frozen runtime ping invalid: {response}")
            self._last_ping_at = time.monotonic()
            return response

    def _identity_unchanged(self) -> bool:
        hints = (self.ready_payload or {}).get("identity_hints")
        return isinstance(hints, dict) and identity_hints_unchanged(hints)

    def _attempt_root(self, formal_output: Path, attempt: int) -> Path:
        root = formal_output.parent / ".frozen_attempts" / formal_output.name
        candidate = root / f"generation_{self.generation:04d}_attempt_{attempt}"
        resolved_root = root.resolve()
        resolved_candidate = candidate.resolve()
        if resolved_root not in resolved_candidate.parents:
            raise FrozenRuntimeUnavailable("attempt root escaped the frozen attempt directory")
        if candidate.exists():
            shutil.rmtree(candidate)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate

    def run_job(
        self,
        request: ScanJobRequest,
        *,
        on_attempt_started: Callable[[Path], None] | None = None,
        on_attempt_completed: Callable[[Path], None] | None = None,
        poll: Callable[[], None] | None = None,
        test_injection: str | None = None,
    ) -> dict[str, Any]:
        queued_at = time.perf_counter()
        with self._lock:
            queue_wait_ms = (time.perf_counter() - queued_at) * 1000.0
            if self.state != "READY" or self.process is None or self.process.poll() is not None:
                self._spawn_worker(recovering=True)
            if not self._identity_unchanged():
                self._spawn_worker(recovering=True)
            if self.state != "READY":
                raise FrozenRuntimeUnavailable(f"frozen runtime is {self.state}")
            formal_output = request.output_dir.resolve()
            if formal_output.exists():
                raise FrozenRuntimeUnavailable("formal output already exists")
            last_error: BaseException | None = None
            for attempt in (1, 2):
                attempt_root = self._attempt_root(formal_output, attempt)
                attempt_request = ScanJobRequest(**{**request.__dict__, "output_dir": attempt_root})
                injection = None
                if test_injection == "crash_once":
                    injection = "crash" if attempt == 1 else None
                elif test_injection == "crash_twice":
                    injection = "crash"
                self._emit("BUSY")
                if on_attempt_started is not None:
                    on_attempt_started(attempt_root)
                try:
                    response = self._send(
                        {"schema_version": COMMAND_SCHEMA, "command": "job", "request": attempt_request.as_dict(), "test_injection": injection},
                        timeout_s=self.job_timeout_s,
                        poll=poll,
                    )
                    if response.get("status") == "success":
                        canonical_package = response.get("canonical_package")
                        if (
                            response.get("generation") != self.generation
                            or not isinstance(canonical_package, dict)
                            or canonical_package.get("generation") != self.generation
                            or canonical_package.get("scan_id") != response.get("scan_id")
                        ):
                            raise FrozenRuntimeUnavailable("canonical package generation or scan identity mismatch")
                        if on_attempt_completed is not None:
                            on_attempt_completed(attempt_root)
                        os.replace(attempt_root, formal_output)
                        self.jobs_completed += 1
                        response["runtime"] = {
                            "generation": self.generation,
                            "attempt": attempt,
                            "supervisor_state": "READY",
                            "runtime_queue_wait_ms": queue_wait_ms,
                            "runtime_job_total_ms": (time.perf_counter() - queued_at) * 1000.0,
                            "attempt_output_root": str(attempt_root),
                            "formal_output_root": str(formal_output),
                        }
                        self._emit("READY")
                        return response
                    if response.get("status") == "aborted":
                        shutil.rmtree(attempt_root, ignore_errors=True)
                        response["runtime"] = {
                            "generation": self.generation,
                            "attempt": attempt,
                            "supervisor_state": "READY",
                            "runtime_queue_wait_ms": queue_wait_ms,
                            "runtime_job_total_ms": (time.perf_counter() - queued_at) * 1000.0,
                            "formal_output_root": str(formal_output),
                        }
                        self._emit("READY")
                        return response
                    if response.get("status") == "rejected" and not response.get("fatal"):
                        self._emit("READY")
                        return response
                    raise FrozenRuntimeUnavailable(f"fatal worker response: {response}")
                except (FrozenRuntimeUnavailable, OSError) as exc:
                    last_error = exc
                    if attempt == 2:
                        break
                    self._spawn_worker(recovering=True)
            self._emit("FAILED", error=last_error)
            return {
                "schema_version": RESPONSE_SCHEMA,
                "status": "rejected",
                "error_code": "E_FROZEN_RUNTIME_FAILED",
                "message": str(last_error or "frozen runtime failed"),
                "fatal": True,
                "runtime": self.status_payload(),
            }

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._terminate_worker()
            self.state = "FAILED"
        if self._monitor is not None:
            self._monitor.join(timeout=5.0)
