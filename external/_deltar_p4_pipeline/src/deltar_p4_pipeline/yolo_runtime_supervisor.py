from __future__ import annotations

import json
import os
import queue
import secrets
import signal
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable


BROKER_REQUEST_SCHEMA = "deltar_yolo_runtime_broker_request.v1"
BROKER_RESPONSE_SCHEMA = "deltar_yolo_runtime_broker_response.v1"
WORKER_COMMAND_SCHEMA = "deltar_yolo_runtime_command.v1"
WORKER_READY_SCHEMA = "deltar_yolo_runtime_ready.v1"
MAX_MESSAGE_BYTES = 1024 * 1024
EXPECTED_GPU_NAME = "RTX 5070 Ti"
BOOK_CLASS_ID = 73


class RuntimeUnavailable(RuntimeError):
    pass


class YoloRuntimeSupervisor:
    def __init__(
        self,
        pipeline_root: Path,
        data_root: Path,
        *,
        startup_timeout_s: float = 180.0,
        job_timeout_s: float = 600.0,
        progress_emit: Callable[..., Any] | None = None,
    ) -> None:
        self.pipeline_root = pipeline_root.resolve()
        self.data_root = data_root.resolve()
        self.startup_timeout_s = startup_timeout_s
        self.job_timeout_s = job_timeout_s
        self.progress_emit = progress_emit
        self.state = "STARTING"
        self.generation = 0
        self.process: subprocess.Popen[str] | None = None
        self.launcher_pid: int | None = None
        self.worker_pid: int | None = None
        self.ready_payload: dict[str, Any] | None = None
        self.last_error: dict[str, Any] | None = None
        self.jobs_completed = 0
        self.recoveries = 0
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._worker_lock = threading.RLock()
        self._stop = threading.Event()
        self._server: socket.socket | None = None
        self._server_thread: threading.Thread | None = None
        self._token = secrets.token_urlsafe(32)
        self._last_ping_at = 0.0
        self.endpoint: str | None = None
        self._startup_config = self._build_startup_config()
        self._startup_path = self.data_root / "progress" / "yolo_runtime_startup.json"
        self._stdout_log = self.data_root / "progress" / "yolo_runtime.stdout.log"
        self._stderr_log = self.data_root / "progress" / "yolo_runtime.stderr.log"

    @property
    def token(self) -> str:
        return self._token

    def _build_startup_config(self) -> dict[str, Any]:
        profile = json.loads(
            (self.pipeline_root / "profiles" / "stage8b1_yolo_runtime_profile.json").read_text(encoding="utf-8")
        )
        algorithm = json.loads(
            (self.pipeline_root / "profiles" / "algorithm_profile.json").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (self.pipeline_root / "profiles" / "model_manifest.json").read_text(encoding="utf-8")
        )
        profile_root = self.pipeline_root / "profiles"
        worker_python = Path(profile["worker_python"])
        if not worker_python.is_absolute():
            worker_python = profile_root / worker_python
        for model in manifest["models"]:
            model_path = Path(model["path"])
            if not model_path.is_absolute():
                model_path = profile_root / model_path
            model["path"] = str(model_path.resolve())
        models = {item["task"]: item for item in manifest["models"] if item.get("task") in {"detect", "segment"}}
        if set(models) != {"detect", "segment"}:
            raise RuntimeUnavailable("formal model manifest does not contain detect and segment")
        return {
            "schema_version": "deltar_yolo_runtime_startup.v1",
            "worker_python": str(worker_python.resolve()),
            "models": {
                task: {"path": models[task]["path"], "sha256": models[task]["sha256"]}
                for task in ("detect", "segment")
            },
            "device": profile["device"],
            "confidence": float(algorithm["yolo_confidence"]),
            "imgsz": int(profile["imgsz"]),
            "batch_size": int(profile["detect_batch_size"]),
            "half": bool(profile["half"]),
            "require_cuda": True,
            "expected_gpu_name": EXPECTED_GPU_NAME,
            "required_class_ids": [BOOK_CLASS_ID],
        }

    def _emit(self, state: str, *, error: BaseException | None = None) -> None:
        self.state = state
        if error is not None:
            self.last_error = {"type": type(error).__name__, "message": str(error)}
        if self.progress_emit is not None:
            try:
                self.progress_emit(
                    f"runtime.yolo.{state.casefold()}",
                    status="error" if state == "FAILED" else "info",
                    message=f"YOLO Runtime {state}",
                    error_code="E_YOLO_RUNTIME" if error is not None else None,
                    error_detail=str(error) if error is not None else None,
                    details={"generation": self.generation, "worker_pid": self.worker_pid},
                )
            except Exception:
                pass

    def status_payload(self) -> dict[str, Any]:
        process_alive = self.process is not None and self.process.poll() is None
        return {
            "schema_version": "deltar_yolo_runtime_status.v1",
            "state": self.state,
            "ready": self.state == "READY" and process_alive,
            "generation": self.generation,
            "worker_pid": self.worker_pid,
            "launcher_pid": self.launcher_pid,
            "process_alive": process_alive,
            "endpoint": self.endpoint,
            "jobs_completed": self.jobs_completed,
            "recoveries": self.recoveries,
            "startup_timing": (self.ready_payload or {}).get("timing"),
            "model_identity": (self.ready_payload or {}).get("model_identity"),
            "last_error": self.last_error,
        }

    def start(self) -> None:
        with self._worker_lock:
            if self._server is not None:
                return
            self.data_root.mkdir(parents=True, exist_ok=True)
            self._startup_path.parent.mkdir(parents=True, exist_ok=True)
            self._startup_path.write_text(
                json.dumps(self._startup_config, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen(4)
            server.settimeout(0.5)
            self._server = server
            host, port = server.getsockname()
            self.endpoint = f"{host}:{port}"
            self._spawn_worker(recovering=False)
            self._server_thread = threading.Thread(
                target=self._broker_loop,
                name="yolo-runtime-broker",
                daemon=True,
            )
            self._server_thread.start()

    def _worker_environment(self) -> dict[str, str]:
        allowed = (
            "SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP", "PATH", "PATHEXT",
            "CUDA_PATH", "CUDA_VISIBLE_DEVICES", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
            "APPDATA", "LOCALAPPDATA",
        )
        env = {key: os.environ[key] for key in allowed if key in os.environ}
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONUTF8"] = "1"
        return env

    def _pipe_reader(
        self,
        stream: Any,
        *,
        stderr: bool,
        message_queue: queue.Queue[dict[str, Any]],
    ) -> None:
        log_path = self._stderr_log if stderr else self._stdout_log
        try:
            with log_path.open("a", encoding="utf-8", newline="\n") as log:
                for line in iter(stream.readline, ""):
                    log.write(line)
                    log.flush()
                    if not stderr:
                        try:
                            message_queue.put(json.loads(line))
                        except json.JSONDecodeError:
                            message_queue.put({"status": "protocol_error", "raw": line[-2000:]})
        finally:
            if not stderr:
                message_queue.put({"status": "eof"})

    def _spawn_worker(self, *, recovering: bool) -> None:
        self._emit("RECOVERING" if recovering else "STARTING")
        self._terminate_worker()
        self._messages = queue.Queue()
        message_queue = self._messages
        worker_python = Path(self._startup_config["worker_python"])
        worker = self.pipeline_root / "workers" / "yolo_runtime_worker.py"
        if not worker_python.is_file() or not worker.is_file():
            raise RuntimeUnavailable("YOLO runtime executable or worker is missing")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            [str(worker_python), str(worker), "--serve", str(self._startup_path)],
            cwd=self.pipeline_root,
            env=self._worker_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        self.launcher_pid = self.process.pid
        self.worker_pid = None
        threading.Thread(
            target=self._pipe_reader,
            args=(self.process.stdout,),
            kwargs={"stderr": False, "message_queue": message_queue},
            name="yolo-runtime-stdout",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._pipe_reader,
            args=(self.process.stderr,),
            kwargs={"stderr": True, "message_queue": message_queue},
            name="yolo-runtime-stderr",
            daemon=True,
        ).start()
        try:
            ready = self._messages.get(timeout=self.startup_timeout_s)
        except queue.Empty as exc:
            self._terminate_worker()
            self._emit("FAILED", error=exc)
            raise RuntimeUnavailable("YOLO runtime startup timed out") from exc
        if ready.get("schema_version") != WORKER_READY_SCHEMA or ready.get("status") != "READY":
            error = RuntimeUnavailable(f"YOLO runtime startup failed: {ready}")
            self._terminate_worker()
            self._emit("FAILED", error=error)
            raise error
        self.worker_pid = int(ready["pid"])
        self.generation += 1
        if recovering:
            self.recoveries += 1
        self.ready_payload = ready
        self.last_error = None
        # A successful READY handshake is itself a liveness proof.  Start the
        # idle-health interval here so the broker cannot immediately consume
        # the worker's command channel before serving its first connection.
        self._last_ping_at = time.monotonic()
        self._emit("READY")

    def _terminate_worker(self) -> None:
        process = self.process
        runtime_pid = self.worker_pid
        self.process = None
        self.worker_pid = None
        self.launcher_pid = None
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin is not None:
                process.stdin.write(
                    json.dumps({"schema_version": WORKER_COMMAND_SCHEMA, "command": "shutdown"}) + "\n"
                )
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

    def _broker_loop(self) -> None:
        server = self._server
        if server is None:
            return
        while not self._stop.is_set():
            try:
                if self.process is not None and self.process.poll() is not None and self.state == "READY":
                    with self._worker_lock:
                        self._spawn_worker(recovering=True)
                if self.state == "READY" and time.monotonic() - self._last_ping_at >= 5.0:
                    with self._worker_lock:
                        try:
                            self._ping_worker()
                        except RuntimeUnavailable:
                            self._spawn_worker(recovering=True)
                connection, _ = server.accept()
            except socket.timeout:
                continue
            except (OSError, RuntimeUnavailable):
                if not self._stop.is_set():
                    self._emit("FAILED")
                continue
            threading.Thread(
                target=self._serve_connection,
                args=(connection,),
                name="yolo-runtime-request",
                daemon=True,
            ).start()

    @staticmethod
    def _read_line(connection: socket.socket) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_MESSAGE_BYTES:
                raise RuntimeUnavailable("runtime request exceeds size limit")
            if b"\n" in chunk:
                break
        return b"".join(chunks).split(b"\n", 1)[0]

    def _serve_connection(self, connection: socket.socket) -> None:
        with connection:
            connection.settimeout(self.job_timeout_s + self.startup_timeout_s)
            try:
                request = json.loads(self._read_line(connection).decode("utf-8"))
                if request.get("schema_version") != BROKER_REQUEST_SCHEMA:
                    raise RuntimeUnavailable("unsupported broker request schema")
                if not secrets.compare_digest(str(request.get("token", "")), self._token):
                    raise RuntimeUnavailable("runtime broker authentication failed")
                if request.get("command") == "ping":
                    response = {"schema_version": BROKER_RESPONSE_SCHEMA, "status": "success", "runtime": self.status_payload()}
                elif request.get("command") == "job":
                    response = self._run_job(request["request"])
                else:
                    raise RuntimeUnavailable("unknown broker command")
            except BaseException as exc:
                response = {
                    "schema_version": BROKER_RESPONSE_SCHEMA,
                    "status": "rejected",
                    "error_code": "E_YOLO_RUNTIME",
                    "message": f"{type(exc).__name__}: {exc}",
                    "runtime": self.status_payload(),
                }
            connection.sendall(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")

    def _model_files_unchanged(self) -> bool:
        identities = (self.ready_payload or {}).get("model_identity") or {}
        for task in ("detect", "segment"):
            identity = identities.get(task) or {}
            path = Path(identity.get("path", ""))
            if not path.is_file():
                return False
            stat = path.stat()
            if stat.st_size != identity.get("size") or stat.st_mtime_ns != identity.get("mtime_ns"):
                return False
        return True

    @staticmethod
    def _cleanup_attempt(request: dict[str, Any]) -> None:
        output_dirs = request.get("output_dirs") or {}
        paths = {task: Path(output_dirs.get(task, "")).resolve() for task in ("detect", "segment")}
        if paths["detect"].name != "detect" or paths["segment"].name != "segment":
            raise RuntimeUnavailable("refusing to clean unexpected runtime output names")
        if paths["detect"].parent != paths["segment"].parent:
            raise RuntimeUnavailable("refusing to clean runtime outputs outside one job root")
        for path in paths.values():
            if path.is_dir():
                shutil.rmtree(path)

    def _send_worker_job(self, request: dict[str, Any]) -> dict[str, Any]:
        process = self.process
        if process is None or process.poll() is not None or process.stdin is None:
            raise RuntimeUnavailable("YOLO runtime worker is not alive")
        command = {"schema_version": WORKER_COMMAND_SCHEMA, "command": "job", "request": request}
        try:
            process.stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
            process.stdin.flush()
            response = self._messages.get(timeout=self.job_timeout_s)
        except (OSError, queue.Empty) as exc:
            raise RuntimeUnavailable(f"YOLO runtime IPC failed: {exc}") from exc
        if response.get("status") == "eof":
            raise RuntimeUnavailable("YOLO runtime worker exited during job")
        return response

    def _ping_worker(self) -> None:
        process = self.process
        if process is None or process.poll() is not None or process.stdin is None:
            raise RuntimeUnavailable("YOLO runtime worker is not alive")
        try:
            process.stdin.write(
                json.dumps({"schema_version": WORKER_COMMAND_SCHEMA, "command": "ping"}) + "\n"
            )
            process.stdin.flush()
            response = self._messages.get(timeout=5.0)
        except (OSError, queue.Empty) as exc:
            raise RuntimeUnavailable(f"YOLO runtime ping failed: {exc}") from exc
        if response.get("status") != "pong":
            raise RuntimeUnavailable(f"YOLO runtime ping response invalid: {response}")
        self._last_ping_at = time.monotonic()

    def _run_job(self, request: dict[str, Any]) -> dict[str, Any]:
        queued_at = time.perf_counter()
        with self._worker_lock:
            queue_wait_ms = (time.perf_counter() - queued_at) * 1000.0
            health_started = time.perf_counter()
            if not self._model_files_unchanged():
                self._spawn_worker(recovering=True)
            health_check_ms = (time.perf_counter() - health_started) * 1000.0
            if self.state != "READY":
                raise RuntimeUnavailable(f"YOLO runtime is {self.state}")
            last_error: BaseException | None = None
            for attempt in range(2):
                attempt_request = dict(request)
                if attempt_request.get("test_injection") in {"crash_once_detect", "crash_once_segment"}:
                    attempt_request["test_injection"] = (
                        attempt_request["test_injection"].replace("crash_once_", "crash_")
                        if attempt == 0
                        else None
                    )
                self._emit("BUSY")
                try:
                    response = self._send_worker_job(attempt_request)
                    if response.get("status") == "success":
                        self.jobs_completed += 1
                        response["runtime"] = {
                            **(response.get("runtime") or {}),
                            "generation": self.generation,
                            "attempt": attempt + 1,
                            "supervisor_state": "READY",
                            "runtime_queue_wait_ms": queue_wait_ms,
                            "runtime_health_check_ms": health_check_ms,
                            "yolo_job_total_ms": (time.perf_counter() - queued_at) * 1000.0,
                        }
                        self._emit("READY")
                        return response
                    code = str(response.get("error_code", ""))
                    if code not in {"E_YOLO_WORKER", "E_YOLO_OOM", "E_YOLO_CUDA_UNAVAILABLE"}:
                        self._emit("READY")
                        return response
                    raise RuntimeUnavailable(f"worker fatal response: {response}")
                except RuntimeUnavailable as exc:
                    last_error = exc
                    self._cleanup_attempt(request)
                    if attempt == 1:
                        break
                    self._spawn_worker(recovering=True)
            self._emit("FAILED", error=last_error)
            return {
                "schema_version": BROKER_RESPONSE_SCHEMA,
                "status": "rejected",
                "error_code": "E_YOLO_RUNTIME_FAILED",
                "message": str(last_error or "YOLO runtime failed"),
                "runtime": self.status_payload(),
            }

    def stop(self) -> None:
        self._stop.set()
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        with self._worker_lock:
            self._terminate_worker()
            self.state = "FAILED"
        if self._server_thread is not None:
            self._server_thread.join(timeout=5.0)
