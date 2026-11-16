from __future__ import annotations

import os
import hashlib
import json
import subprocess
import time
import webbrowser
import re
import threading
import warnings
from functools import lru_cache
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .scan_service_contracts import MAX_OBJECTS, MAX_RESULT_BYTES, ServiceError, utc_now
from .scan_service_storage import ServiceStorage
from .scan_contracts import ScanContractError
from .stage8b4a_contracts import (
    MAX_PACKAGE_BYTES,
    P4_PACKAGE_FILENAME,
    validate_p4_package,
)
from .pipeline_progress import MAX_EVENT_BYTES, SCHEMA_VERSION, validate_progress_event
from .frozen_runtime_contracts import ScanJobRequest
from .frozen_runtime_supervisor import FrozenRuntimeUnavailable
from .scan_profiles import load_profile_bundle
from .scan_validator import validate_scan as validate_scan_input
from .validation_receipt import capture_input_manifest
from .chatgpt_review_bundle import (
    ReviewBundleResult,
    build_chatgpt_review_bundle,
)

WORKER_ERROR_CODE_RE = re.compile(r"^E_[A-Z0-9_]+$")
FORMAL_PIPELINE_TIMEOUT_SECONDS = 300


class PipelineProgressTailer:
    """Binary, newline-only tailer for the child progress stream."""

    def __init__(self, path: Path, on_event: Callable[[dict[str, Any]], None], *,
                 poll_seconds: float = 0.1,
                 on_fallback_poll: Callable[[], None] | None = None) -> None:
        self.path = Path(path)
        self.on_event = on_event
        self.poll_seconds = poll_seconds
        self.on_fallback_poll = on_fallback_poll
        self.stop_event = threading.Event()
        self.child_done = threading.Event()
        self.thread: threading.Thread | None = None
        self.offset = 0
        self.pending = b""
        self.seen: set[tuple[str, str, int]] = set()
        self.last_ids: dict[tuple[str, str], int] = {}
        self.warnings: list[str] = []
        self.saw_event = False
        self.last_event: dict[str, Any] | None = None
        self.started = time.monotonic()
        self.observer_mode = "child_stream"
        self.invalid_stream = False

    def _warning(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
            warnings.warn(message, RuntimeWarning, stacklevel=2)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="pipeline-progress-tail", daemon=True)
        self.thread.start()

    def finish(self) -> None:
        self.child_done.set()
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        self.stop_event.set()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=0.2)

    def _read_once(self) -> bool:
        try:
            size = self.path.stat().st_size
        except OSError:
            return False
        if size < self.offset:
            self.offset = 0
            self.pending = b""
            self._warning("pipeline progress file was truncated; reopening from offset zero")
        if size == self.offset:
            return False
        try:
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
        except OSError:
            return False
        self.offset += len(chunk)
        data = self.pending + chunk
        parts = data.split(b"\n")
        self.pending = parts.pop()
        for raw in parts:
            self._consume(raw)
        return bool(chunk)

    def _consume(self, raw: bytes) -> None:
        try:
            if len(raw) + 1 > MAX_EVENT_BYTES:
                raise ValueError("oversize progress event")
            line = raw.decode("utf-8", errors="strict")
            event = json.loads(line)
            if type(event) is not dict:
                raise ValueError("progress root")
            validate_progress_event(event)
            if event.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("unsupported progress schema")
            child_id = int(event["event_id"])
            stream = (str(event["scan_id"]), str(event["schema_version"]))
            key = (*stream, child_id)
            if key in self.seen:
                return
            previous = self.last_ids.get(stream, 0)
            if previous and child_id != previous + 1:
                self._warning(f"pipeline progress event gap: expected {previous + 1}, got {child_id}")
            if child_id <= previous:
                self._warning(f"pipeline progress non-monotonic event: {child_id} after {previous}")
                return
            self.seen.add(key)
            self.last_ids[stream] = child_id
            self.saw_event = True
            self.last_event = event
            self.observer_mode = "child_stream"
            self.on_event(event)
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.invalid_stream = True
            self._warning(f"skipping malformed pipeline progress event: {type(exc).__name__}")

    def _run(self) -> None:
        quiet_eof = 0
        deadline_after_exit: float | None = None
        while not self.stop_event.is_set():
            changed = self._read_once()
            reporter_failed = self.path.with_suffix(self.path.suffix + ".disabled").is_file()
            if self.invalid_stream or reporter_failed or (
                not self.saw_event and time.monotonic() - self.started >= 1.0
            ):
                self.observer_mode = "artifact_fallback"
                if self.on_fallback_poll is not None:
                    try:
                        self.on_fallback_poll()
                    except Exception:
                        pass
            if self.child_done.is_set():
                if deadline_after_exit is None:
                    deadline_after_exit = time.monotonic() + 0.5
                quiet_eof = 0 if changed else quiet_eof + 1
                if quiet_eof >= 2 or time.monotonic() >= deadline_after_exit:
                    return
            self.stop_event.wait(self.poll_seconds)


class PipelineWorkerFailure(Exception):
    def __init__(self, error: dict[str, Any]):
        super().__init__(str(error.get("message") or "pipeline worker failed"))
        self.error = error


@dataclass(frozen=True)
class WorkerConfig:
    pipeline_root: Path
    worker_python: Path
    profile_bundle: Path
    validate_timeout_seconds: int = 30
    job_timeout_seconds: int = FORMAL_PIPELINE_TIMEOUT_SECONDS
    max_result_bytes: int = MAX_RESULT_BYTES
    max_objects: int = MAX_OBJECTS
    device: str = "cuda:0"
    auto_open_visualization: bool = False
    visualization_timeout_seconds: int = 600
    yolo_runtime_endpoint: str | None = None
    yolo_runtime_token: str | None = None
    frozen_runtime: Any = None


def worker_environment(config: WorkerConfig) -> dict[str, str]:
    allowed = (
        "SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP", "PATH", "PATHEXT",
        "CUDA_PATH", "CUDA_VISIBLE_DEVICES", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
        "APPDATA", "LOCALAPPDATA",
    )
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env["PYTHONPATH"] = str((config.pipeline_root / "src").resolve())
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONUTF8"] = "1"
    if config.yolo_runtime_endpoint:
        env["DELTAR_YOLO_RUNTIME_ENDPOINT"] = config.yolo_runtime_endpoint
    if config.yolo_runtime_token:
        env["DELTAR_YOLO_RUNTIME_TOKEN"] = config.yolo_runtime_token
    return env


def worker_boundary_state(config: WorkerConfig) -> dict[str, Any]:
    """Return a side-effect-free readiness view of the subprocess boundary."""

    executable = config.worker_python.resolve()
    pipeline_root = config.pipeline_root.resolve()
    module_entrypoint = pipeline_root / "src" / "deltar_p4_pipeline" / "cli.py"
    persistent_entrypoint = pipeline_root / "workers" / "frozen_scan_runtime_worker.py"
    profile_bundle = config.profile_bundle.resolve()
    checks = {
        "worker_executable": executable.is_file(),
        "pipeline_root": pipeline_root.is_dir(),
        "module_entrypoint": module_entrypoint.is_file(),
        "persistent_entrypoint": persistent_entrypoint.is_file(),
        "profile_bundle": profile_bundle.is_dir(),
    }
    return {
        "schema_version": "deltar_worker_boundary.v1",
        "ready": all(checks.values()),
        "executable": str(executable),
        "cwd": str(pipeline_root),
        "pythonpath": str((pipeline_root / "src").resolve()),
        "module": "deltar_p4_pipeline.cli",
        "one_shot_command_prefix": _command(config, "scan-run"),
        "persistent_command_prefix": [
            str(executable),
            str(persistent_entrypoint.resolve()),
            "--serve",
        ],
        "checks": checks,
    }


def _command(config: WorkerConfig, action: str, *arguments: str) -> list[str]:
    return [str(config.worker_python), "-m", "deltar_p4_pipeline.cli", action, *arguments]


def _tail(raw: bytes | None, limit: int = 4096) -> str:
    return (raw or b"")[-limit:].decode("utf-8", errors="replace")


def structured_worker_error(
    raw: bytes | None,
) -> tuple[str, str] | None:
    for line in reversed(_tail(raw, 16384).splitlines()):
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            continue
        code = error.get("code", error.get("error_code"))
        if not isinstance(code, str) or not WORKER_ERROR_CODE_RE.fullmatch(code):
            continue
        raw_message = error.get("message")
        message = (
            " ".join(raw_message.split())[:160]
            if isinstance(raw_message, str) and raw_message.strip()
            else "pipeline worker reported a structured error"
        )
        return code, message
    return None


def representative_result_error(
    result_dir: Path, raw_stdout: bytes | None
) -> dict[str, Any] | None:
    report_path = result_dir / "job_report.json"
    candidates: list[dict[str, Any]] = []
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        candidates.extend(item for item in report.get("errors", []) if isinstance(item, dict))
        if not candidates:
            for rejected in report.get("rejected_frames", []):
                error = rejected.get("error") if isinstance(rejected, dict) else None
                if isinstance(error, dict):
                    candidates.append(error)
    except (OSError, UnicodeError, ValueError, TypeError):
        pass
    if candidates:
        def identity(item: dict[str, Any]) -> tuple[str, str, str]:
            return (
                str(item.get("code") or item.get("error_code") or "E_PIPELINE_FAILED")[:64],
                str(item.get("phase") or "pipeline")[:80],
                " ".join(str(item.get("message") or "pipeline worker failed").split())[:500],
            )

        def error_type_of(item: dict[str, Any]) -> str:
            details = item.get("details")
            return (
                str(details.get("error_type"))[:120]
                if isinstance(details, dict) and details.get("error_type")
                else "unknown"
            )

        counts = Counter(
            (identity(item)[0], identity(item)[1], error_type_of(item))
            for item in candidates
        )
        code, phase, message = identity(candidates[0])
        if not WORKER_ERROR_CODE_RE.fullmatch(code):
            code = "E_PIPELINE_FAILED"
        error_type_value = error_type_of(candidates[0])
        error_type = None if error_type_value == "unknown" else error_type_value
        failed_frames = max(counts.values())
        return {
            "error_code": code,
            "message": message[:240],
            "phase": phase,
            "error_type": error_type,
            "failed_frame_count": failed_frames,
            "detail": (
                f"phase={phase}; error_type={error_type or 'unknown'}; "
                f"same_error_frames={failed_frames}"
            )[:500],
        }
    fallback = structured_worker_error(raw_stdout)
    if fallback is None:
        return None
    return {
        "error_code": fallback[0],
        "message": fallback[1],
        "phase": "pipeline",
        "error_type": None,
        "failed_frame_count": None,
        "detail": "structured worker stdout error",
    }


def _append_worker_phase(
    result_dir: Path,
    record: dict[str, Any],
    *,
    phase: str,
    phase_start: str,
    phase_end: str,
    elapsed_seconds: float,
    process_id: int | None,
    command: list[str] | str,
    exit_code: int | None,
    extra: dict[str, Any] | None = None,
) -> None:
    logs = result_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    payload = {
        "scan_id": record["scan_id"],
        "service_job_id": record["service_job_id"],
        "phase": phase,
        "phase_start": phase_start,
        "phase_end": phase_end,
        "elapsed_seconds": elapsed_seconds,
        "process_id": process_id,
        "command": command if isinstance(command, str) else " ".join(command),
        "exit_code": exit_code,
    }
    if extra:
        payload.update(extra)
    with (logs / "service_phases.jsonl").open(
        "a", encoding="utf-8", newline="\n"
    ) as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _run_process(
    config: WorkerConfig,
    result_dir: Path,
    record: dict[str, Any],
    *,
    phase: str,
    command: list[str],
    timeout_seconds: int,
    log_prefix: str,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    fallback_poll: Callable[[], None] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    phase_start = utc_now()
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        cwd=config.pipeline_root,
        env=worker_environment(config),
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    tailer = None
    if progress_callback is not None:
        tailer = PipelineProgressTailer(
            result_dir / "logs" / "pipeline_progress.jsonl",
            progress_callback,
            poll_seconds=0.1,
            on_fallback_poll=fallback_poll,
        )
        tailer.start()
    timed_out = False
    terminated = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        terminated = True
        stdout, stderr = process.communicate()
    finally:
        if tailer is not None:
            tailer.finish()
            record["_pipeline_progress_state"] = {
                "observer_mode": tailer.observer_mode,
                "last_event": tailer.last_event,
                "warnings": list(tailer.warnings),
                "saw_event": tailer.saw_event,
            }
    elapsed = time.perf_counter() - started
    logs = result_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"{log_prefix}.stdout.log").write_bytes(stdout)
    (logs / f"{log_prefix}.stderr.log").write_bytes(stderr)
    artifacts = []
    if result_dir.is_dir():
        artifacts = sorted(
            str(path.relative_to(result_dir)).replace("\\", "/")
            for path in result_dir.rglob("*")
            if path.is_file()
        )
    _append_worker_phase(
        result_dir,
        record,
        phase=phase,
        phase_start=phase_start,
        phase_end=utc_now(),
        elapsed_seconds=elapsed,
        process_id=process.pid,
        command=command,
        exit_code=process.returncode,
        extra={
            "timed_out": timed_out,
            "terminated": terminated,
            "stdout_tail": _tail(stdout),
            "stderr_tail": _tail(stderr),
            "intermediate_artifacts": artifacts,
        },
    )
    if timed_out:
        raise subprocess.TimeoutExpired(
            command, timeout_seconds, output=stdout, stderr=stderr
        )
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _visualization_html(result_dir: Path) -> Path:
    manifest_path = result_dir / "visualization" / "visualization_manifest.v1.json"
    if not manifest_path.is_file():
        raise ServiceError(
            "E_FULL_SCENE_MISSING",
            "visualization manifest is missing",
            409,
            scan_id=result_dir.name,
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("visualization_complete") is not True:
        raise ServiceError(
            "E_FULL_SCENE_INCOMPLETE",
            "visualization manifest is not complete",
            409,
            scan_id=result_dir.name,
        )
    relative = manifest.get("html", {}).get("path")
    if type(relative) is not str or not relative:
        raise ServiceError(
            "E_FULL_SCENE_HTML",
            "visualization manifest has no HTML path",
            409,
            scan_id=result_dir.name,
        )
    html = (result_dir / relative).resolve()
    try:
        html.relative_to(result_dir.resolve())
    except ValueError as exc:
        raise ServiceError(
            "E_FULL_SCENE_HTML",
            "visualization HTML path escapes the result directory",
            409,
            scan_id=result_dir.name,
        ) from exc
    if not html.is_file() or html.stat().st_size <= 0:
        raise ServiceError(
            "E_FULL_SCENE_HTML",
            "visualization HTML is missing or empty",
            409,
            scan_id=result_dir.name,
        )
    return html


def open_visualization(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    if not webbrowser.open(path.as_uri(), new=2):
        raise OSError("default browser rejected the local visualization URL")


def publish_terminal_review_bundle(
    config: WorkerConfig,
    storage: ServiceStorage,
    record: dict[str, Any],
    progress_emit: Callable[..., Any] | None = None,
) -> ReviewBundleResult | None:
    """Best-effort post-finalization hook; never changes scan authority."""

    scan_id = str(record["scan_id"])
    result_dir = storage.result_dir(scan_id)
    if not result_dir.is_dir():
        return None
    error = record.get("error")
    failure_reason = None
    if isinstance(error, dict):
        failure_reason = str(
            error.get("message") or error.get("error_code") or ""
        )[:500] or None
    try:
        outcome = build_chatgpt_review_bundle(
            result_dir,
            scan_id=scan_id,
            scan_terminal_status=str(record.get("status") or "unknown"),
            terminal_failure_reason=failure_reason,
        )
    except Exception as exc:
        warnings.warn(
            "ChatGPT review bundle generation failed for "
            f"{scan_id}; scan result remains unchanged: {type(exc).__name__}: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
        if progress_emit is not None:
            try:
                progress_emit(
                    "review_bundle_failed",
                    scan_id=scan_id,
                    job_role=record.get("job_role", "user"),
                    visibility=record.get("visibility", "user"),
                    status="warn",
                    message="ChatGPT Review ZIP 生成失败；扫描结果不受影响",
                    error_code="W_REVIEW_BUNDLE_FAILED",
                    error_detail=f"{type(exc).__name__}: {exc}"[:500],
                )
            except Exception:
                pass
        return None
    if progress_emit is not None:
        try:
            progress_emit(
                "review_bundle_ready",
                scan_id=scan_id,
                job_role=record.get("job_role", "user"),
                visibility=record.get("visibility", "user"),
                status="success",
                message="ChatGPT Review ZIP 已生成",
                details={
                    "path": str(outcome.zip_path),
                    "bundle_status": outcome.manifest.get("bundle_status"),
                },
            )
        except Exception:
            pass
    return outcome


def _finish_full_scene(
    config: WorkerConfig,
    storage: ServiceStorage,
    record: dict[str, Any],
    transition: Callable[[dict[str, Any]], None],
    *,
    allow_auto_open: bool,
) -> None:
    scan_id = record["scan_id"]
    result_dir = storage.result_dir(scan_id)
    package_path = result_dir / P4_PACKAGE_FILENAME
    expected_length = int(record["package_length"])
    expected_sha256 = str(record["package_sha256"])
    record.update(
        {
            "full_scene_status": "running",
            "full_scene_ready": False,
            "full_scene_error_code": None,
            "full_scene_error_message": None,
        }
    )
    transition(record)
    try:
        command = [
            str(config.worker_python),
            str(config.pipeline_root / "tools" / "stage8b3_render.py"),
            str(result_dir),
        ]
        completed = _run_process(
            config,
            result_dir,
            record,
            phase="stage8b3_full_visualization",
            command=command,
            timeout_seconds=config.visualization_timeout_seconds,
            log_prefix="stage8b3",
        )
        if completed.returncode != 0:
            raise ServiceError(
                "E_FULL_SCENE_FAILED",
                "Stage 8B-3 cached visualization failed",
                409,
                scan_id=scan_id,
            )
        html = _visualization_html(result_dir)
        package_raw = package_path.read_bytes()
        if (
            len(package_raw) != expected_length
            or hashlib.sha256(package_raw).hexdigest() != expected_sha256
        ):
            raise ServiceError(
                "E_DECISION_MUTATED",
                "published decision package changed during visualization",
                409,
                scan_id=scan_id,
            )
        record.update(
            {
                "status": "completed",
                "finished_at": utc_now(),
                "full_scene_status": "ready",
                "full_scene_ready": True,
                "full_scene_error_code": None,
                "full_scene_error_message": None,
                "visualization_html_path": str(html),
                "visualization_ready_at": utc_now(),
            }
        )
        transition(record)
    except (subprocess.TimeoutExpired, ServiceError, OSError, ValueError) as exc:
        code = (
            "E_FULL_SCENE_TIMEOUT"
            if isinstance(exc, subprocess.TimeoutExpired)
            else exc.code
            if isinstance(exc, ServiceError)
            else "E_FULL_SCENE_FAILED"
        )
        message = (
            "Stage 8B-3 cached visualization timed out"
            if isinstance(exc, subprocess.TimeoutExpired)
            else exc.message
            if isinstance(exc, ServiceError)
            else f"{type(exc).__name__}: {exc}"
        )
        record.update(
            {
                "status": "decision_ready",
                "finished_at": utc_now(),
                "full_scene_status": "failed",
                "full_scene_ready": False,
                "full_scene_error_code": code,
                "full_scene_error_message": str(message)[:500],
            }
        )
        transition(record)
        return
    if not config.auto_open_visualization or not allow_auto_open:
        return
    if record.get("visualization_open_attempted_at") is not None:
        return
    record["visualization_open_attempted_at"] = utc_now()
    record["visualization_open_attempts"] = int(
        record.get("visualization_open_attempts", 0)
    ) + 1
    transition(record)
    try:
        open_visualization(Path(record["visualization_html_path"]))
    except Exception as exc:
        record["visualization_open_error"] = f"{type(exc).__name__}: {exc}"[:500]
        transition(record)
        print(
            "[提示] 环境可视化已生成，但自动打开失败："
            f"{scan_id}，path={record['visualization_html_path']}，"
            f"error={record['visualization_open_error']}",
            flush=True,
        )
    else:
        record["visualization_opened_at"] = utc_now()
        record["visualization_open_error"] = None
        transition(record)
        print(
            f"[完成] 环境可视化已生成并自动打开：{scan_id}",
            flush=True,
        )


@lru_cache(maxsize=4)
def _validation_bundle(profile_bundle: str) -> Any:
    return load_profile_bundle(Path(profile_bundle), validate_model_hashes=False)


def validate_scan(
    config: WorkerConfig,
    scan_dir: Path,
    known_file_identities: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    scan_id = scan_dir.name.split(".", 1)[0]
    try:
        bundle_started = time.perf_counter()
        bundle = _validation_bundle(str(config.profile_bundle.resolve()))
        bundle_seconds = time.perf_counter() - bundle_started
        limits = bundle.algorithm["limits"]
        compute_started = time.perf_counter()
        validated = validate_scan_input(
            scan_dir,
            bundle,
            max_frames=int(limits["max_frames_per_scan"]),
            max_scan_bytes=int(limits["max_scan_bytes"]),
            expected_scan_id=scan_id,
        )
        compute_seconds = time.perf_counter() - compute_started
        identity_started = time.perf_counter()
        manifest = capture_input_manifest(scan_dir, known_file_identities)
        identity_seconds = time.perf_counter() - identity_started
    except ScanContractError as exc:
        raise ServiceError("E_SCAN_VALIDATE", "scan validation rejected the upload", 422) from exc
    return {
        "elapsed_seconds": time.perf_counter() - started,
        "returncode": 0,
        "scan_id": validated.scan["scan_id"],
        "frame_count": validated.scan["frame_count"],
        "bundle_seconds": bundle_seconds,
        "compute_seconds": compute_seconds,
        "identity_seconds": identity_seconds,
        "subprocess_startup": False,
        "authoritative_manifest": manifest,
    }


def validate_result_files(config: WorkerConfig, storage: ServiceStorage, scan_id: str) -> tuple[dict[str, Any], bytes]:
    result_dir = storage.result_dir(scan_id)
    result_path = result_dir / "result.json"
    report_path = result_dir / "job_report.json"
    if not result_path.is_file() or not report_path.is_file():
        raise ServiceError("E_RESULT_MISSING", "pipeline result files are missing", 409, scan_id=scan_id)
    raw = result_path.read_bytes()
    if len(raw) > config.max_result_bytes:
        raise ServiceError("E_RESULT_TOO_LARGE", "result exceeds the publication limit", 409, scan_id=scan_id)
    try:
        result = storage.read_json(result_path)
        report = storage.read_json(report_path)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ServiceError("E_RESULT_SCHEMA", "pipeline result is not strict UTF-8 JSON", 409, scan_id=scan_id) from exc
    if result.get("schema_version") != "deltar_scan_result.v1.1" or report.get("schema_version") != "deltar_pc_job_report.v1.1":
        raise ServiceError("E_RESULT_SCHEMA", "pipeline result schema is not v1.1", 409, scan_id=scan_id)
    if result.get("scan_id") != scan_id or report.get("scan_id") != scan_id or result.get("job_id") != report.get("job_id"):
        raise ServiceError("E_RESULT_ID_MISMATCH", "result identifiers do not match", 409, scan_id=scan_id)
    status = result.get("status")
    if status not in {"success", "partial_success", "rejected"} or report.get("result_status") != status:
        raise ServiceError("E_RESULT_SCHEMA", "result and job status are incompatible", 409, scan_id=scan_id)
    objects = result.get("objects")
    if type(objects) is not list:
        raise ServiceError("E_RESULT_SCHEMA", "result objects must be an array", 409, scan_id=scan_id)
    if len(objects) > config.max_objects:
        raise ServiceError("E_RESULT_OBJECT_LIMIT", "result object count exceeds the publication limit", 409, scan_id=scan_id)
    return result, raw


def validate_decision_package(
    config: WorkerConfig,
    storage: ServiceStorage,
    scan_id: str,
    *,
    expected_length: int | None = None,
    expected_sha256: str | None = None,
    expected_generation: int | None = None,
    actual_generation: int | None = None,
) -> tuple[dict[str, Any], bytes, str]:
    package_path = storage.result_dir(scan_id) / P4_PACKAGE_FILENAME
    if not package_path.is_file():
        raise ServiceError(
            "E_DECISION_MISSING", "decision package is missing", 409,
            scan_id=scan_id,
        )
    try:
        size = package_path.stat().st_size
    except OSError as exc:
        raise ServiceError("E_DECISION_MISSING", "decision package is missing", 409, scan_id=scan_id) from exc
    if size < 1 or size > MAX_PACKAGE_BYTES:
        raise ServiceError(
            "E_DECISION_TOO_LARGE", "decision package exceeds 64 KiB",
            409, scan_id=scan_id,
        )
    with package_path.open("rb") as handle:
        raw = handle.read(MAX_PACKAGE_BYTES + 1)
    if len(raw) != size:
        raise ServiceError("E_DECISION_IDENTITY", "decision package changed during bounded read", 409, scan_id=scan_id)
    try:
        package = json.loads(
            raw.decode("utf-8", errors="strict"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(value)
            ),
        )
        if type(package) is not dict:
            raise ValueError("package root")
        validate_p4_package(package)
    except (UnicodeError, ValueError, json.JSONDecodeError, ScanContractError) as exc:
        raise ServiceError(
            "E_DECISION_SCHEMA", "decision package failed validation",
            409, scan_id=scan_id,
        ) from exc
    if package.get("scan_id") != scan_id:
        raise ServiceError(
            "E_DECISION_ID_MISMATCH",
            "decision package scan_id does not match", 409,
            scan_id=scan_id,
        )
    package_sha256 = hashlib.sha256(raw).hexdigest()
    sidecar_path = package_path.with_name(package_path.name + ".sha256")
    try:
        sidecar_raw = sidecar_path.read_bytes()
        sidecar_sha256 = sidecar_raw.decode("ascii", errors="strict").strip()
    except (OSError, UnicodeError) as exc:
        raise ServiceError("E_DECISION_IDENTITY", "canonical package SHA sidecar is invalid", 409, scan_id=scan_id) from exc
    if len(sidecar_raw) > 66 or sidecar_sha256 != package_sha256:
        raise ServiceError("E_DECISION_IDENTITY", "canonical package SHA sidecar does not match", 409, scan_id=scan_id)
    if expected_length is not None and len(raw) != expected_length:
        raise ServiceError("E_DECISION_IDENTITY", "canonical package length does not match runtime identity", 409, scan_id=scan_id)
    if expected_sha256 is not None and package_sha256 != expected_sha256:
        raise ServiceError("E_DECISION_IDENTITY", "canonical package SHA does not match runtime identity", 409, scan_id=scan_id)
    if expected_generation is not None and (
        actual_generation is None or expected_generation != actual_generation
    ):
        raise ServiceError("E_DECISION_GENERATION", "canonical package runtime generation does not match", 409, scan_id=scan_id)
    return package, raw, package_sha256


def run_pipeline(config: WorkerConfig, storage: ServiceStorage, record: dict[str, Any],
                 transition: Callable[[dict[str, Any]], None],
                 progress_emit: Callable[..., Any] | None = None) -> None:
    scan_id = record["scan_id"]
    result_dir = storage.result_dir(scan_id)
    started = time.perf_counter()
    package_published = False
    canonical_package_identity: dict[str, Any] | None = None
    child_last: dict[str, Any] | None = None
    frame_durations_ms: list[float] = []
    artifact_seen: set[str] = set()
    def observe(stage: str, **values: Any) -> None:
        if (
            (record.get("_pipeline_progress_state") or {}).get("saw_event")
            and stage in {"detection_completed", "geometry_started", "geometry_progress",
                          "geometry_completed", "selection_completed"}
            and not values.get("details")
        ):
            return
        if progress_emit is None:
            return
        try:
            progress_emit(
                stage,
                scan_id=scan_id,
                job_role=record.get("job_role", "user"),
                visibility=record.get("visibility", "user"),
                **values,
            )
        except Exception:
            pass

    def child_progress(event: dict[str, Any]) -> None:
        nonlocal child_last
        child_last = dict(event)
        if (
            event.get("phase") == "frame_processing" and event.get("subphase") == "frame"
            and event.get("status") in {"completed", "failed"}
            and isinstance(event.get("elapsed_ms"), (int, float))
        ):
            frame_durations_ms.append(float(event["elapsed_ms"]))
            del frame_durations_ms[:-5]
        error = event.get("error") if isinstance(event.get("error"), dict) else {}
        metrics = event.get("metrics") if isinstance(event.get("metrics"), dict) else {}
        details = {
            "pipeline_event_id": event.get("event_id"),
            "pipeline_schema_version": event.get("schema_version"),
            "phase": event.get("phase"), "subphase": event.get("subphase"),
            "frame_id": event.get("frame_id"), "frame_index": event.get("frame_index"),
            "frame_total": event.get("frame_total"), "elapsed_ms": event.get("elapsed_ms"),
            "metrics": metrics, "observer_mode": "child_stream",
            "pipeline_timestamp": event.get("timestamp"),
            "pipeline_status": event.get("status"),
            "timeout_seconds": config.job_timeout_seconds,
        }
        for key in ("completed_frames", "successful_frames", "rejected_frames", "failed_frames"):
            if key in metrics:
                details[key] = metrics[key]
        observe(
            "pipeline_progress",
            status={"started": "running", "progress": "info", "completed": "success",
                    "failed": "error"}.get(str(event.get("status")), "info"),
            current=event.get("current"), total=event.get("total"),
            elapsed_ms=event.get("elapsed_ms"), message=str(event.get("message") or "pipeline progress")[:240],
            error_code=error.get("code"), error_detail=error.get("message"), details=details,
        )

    def artifact_fallback_poll() -> None:
        if "mode" not in artifact_seen:
            artifact_seen.add("mode")
            observe(
                "pipeline_progress", status="warn",
                message="细粒度进度不可用，正在使用兼容进度",
                details={"observer_mode": "artifact_fallback", "fine_progress_available": False},
            )
        exact = [
            ("yolo", result_dir / "stage8b1_yolo_batch_manifest.json", "yolo", "segment"),
            ("geometry", result_dir / "stage8b_all_object_geometry.v1.json", "all_object_geometry", "geometry_document_write"),
            ("selected", result_dir / "stage8b4a_selected_objects.v2.json", "selection", "selected_write"),
            ("package", result_dir / P4_PACKAGE_FILENAME, "package", "package_write"),
            ("visualization", result_dir / "visualization" / "visualization_manifest.v1.json", "visualization", "render"),
        ]
        for marker, path, phase_name, subphase_name in exact:
            if marker not in artifact_seen and path.is_file():
                artifact_seen.add(marker)
                observe(
                    "pipeline_progress", status="success", message=f"兼容进度：{marker}产物已完成",
                    details={"observer_mode": "artifact_fallback", "fine_progress_available": False,
                             "phase": phase_name, "subphase": subphase_name, "artifact": marker},
                )
        frame_dir = result_dir / "frame_results"
        if frame_dir.is_dir():
            for frame_path in sorted(frame_dir.glob("*.json")):
                marker = f"frame:{frame_path.name}"
                if marker in artifact_seen:
                    continue
                try:
                    frame_doc = storage.read_json(frame_path)
                    frame_id = str(frame_doc.get("frame_id") or frame_path.stem)
                    frame_index = int(frame_doc.get("sequence")) + 1
                except (OSError, ValueError, TypeError):
                    continue
                artifact_seen.add(marker)
                completed_count = sum(item.startswith("frame:") for item in artifact_seen)
                observe(
                    "pipeline_progress", status="success", current=completed_count,
                    total=int(record.get("frame_count", 0)) or None,
                    message=f"兼容进度：第{frame_index}帧结果已写入",
                    details={"observer_mode": "artifact_fallback", "fine_progress_available": False,
                             "phase": "frame_processing", "subphase": "frame_result_write",
                             "frame_id": frame_id, "frame_index": frame_index,
                             "frame_total": int(record.get("frame_count", 0)) or None,
                             "completed_frames": completed_count},
                )

    try:
        observe("processing_started", message="PC 开始处理扫描任务")
        observe(
            "detection_started", message="开始批量目标检测",
            details={"frame_count": int(record.get("frame_count", 0))},
        )
        command = _command(
            config, "scan-run",
            "--scan-dir", str(storage.validated_dir(scan_id)),
            "--output-dir", str(result_dir),
            "--profile-bundle", str(config.profile_bundle),
            "--device", config.device,
            "--require-validation-receipt",
            "--defer-visualization", "--stage7d-horizontal-planes",
            "--stage7d-profile", str(config.pipeline_root / "profiles" / "stage7d_horizontal_plane_profile.json"),
        )
        if config.frozen_runtime is None:
            completed = _run_process(
                config, result_dir, record, phase="shared_inference_geometry",
                command=command, timeout_seconds=config.job_timeout_seconds,
                log_prefix="service_worker", progress_callback=child_progress,
                fallback_poll=artifact_fallback_poll,
            )
            if completed.returncode != 0:
                worker_error = representative_result_error(result_dir, completed.stdout)
                if worker_error is not None:
                    raise PipelineWorkerFailure(worker_error)
                raise ServiceError("E_PIPELINE_FAILED", "pipeline worker failed", 409, scan_id=scan_id)
        else:
            request = ScanJobRequest(
                scan_dir=storage.validated_dir(scan_id), output_dir=result_dir,
                profile_dir=config.profile_bundle, device=config.device,
                stage7d_horizontal_planes=True,
                stage7d_profile=config.pipeline_root / "profiles" / "stage7d_horizontal_plane_profile.json",
                generate_full_visualization=False,
                validation_receipt_required=True,
            )
            active_tailer: PipelineProgressTailer | None = None

            def on_attempt_started(attempt_root: Path) -> None:
                nonlocal active_tailer
                if active_tailer is not None:
                    active_tailer.finish()
                active_tailer = PipelineProgressTailer(
                    attempt_root / "logs" / "pipeline_progress.jsonl",
                    child_progress, poll_seconds=0.1,
                )
                active_tailer.start()

            def on_attempt_completed(_attempt_root: Path) -> None:
                if active_tailer is not None:
                    active_tailer.finish()

            runtime_started_at = utc_now()
            runtime_started = time.perf_counter()
            try:
                runtime_response = config.frozen_runtime.run_job(
                    request,
                    on_attempt_started=on_attempt_started,
                    on_attempt_completed=on_attempt_completed,
                )
            except FrozenRuntimeUnavailable as exc:
                raise ServiceError("E_FROZEN_RUNTIME_FAILED", str(exc), 409, scan_id=scan_id) from exc
            finally:
                if active_tailer is not None:
                    active_tailer.finish()
                    record["_pipeline_progress_state"] = {
                        "observer_mode": active_tailer.observer_mode,
                        "last_event": active_tailer.last_event,
                        "warnings": list(active_tailer.warnings),
                        "saw_event": active_tailer.saw_event,
                    }
            if runtime_response.get("status") != "success":
                raise PipelineWorkerFailure({
                    "error_code": str(runtime_response.get("error_code") or "E_FROZEN_RUNTIME_FAILED"),
                    "message": str(runtime_response.get("message") or "frozen runtime job failed"),
                    "phase": "shared_inference_geometry",
                    "error_type": "FrozenRuntimeFailure",
                    "runtime": runtime_response.get("runtime"),
                })
            record["frozen_runtime"] = {
                **(runtime_response.get("runtime") or {}),
                "job_wall_ms": runtime_response.get("job_wall_ms"),
            }
            canonical_package_identity = runtime_response.get("canonical_package")
            _append_worker_phase(
                result_dir, record, phase="shared_inference_geometry",
                phase_start=runtime_started_at, phase_end=utc_now(),
                elapsed_seconds=time.perf_counter() - runtime_started,
                process_id=config.frozen_runtime.worker_pid,
                command="FrozenScanRuntime.run_job",
                exit_code=0,
                extra={"runtime": runtime_response.get("runtime"), "job_wall_ms": runtime_response.get("job_wall_ms")},
            )
        record["run_seconds"] = time.perf_counter() - started
        result, _ = validate_result_files(config, storage, scan_id)
        observe(
            "detection_completed", status="success",
            current=int(record.get("frame_count", 0)),
            total=int(record.get("frame_count", 0)),
            message="批量目标检测完成",
            details={"frame_count": int(record.get("frame_count", 0))},
        )
        observe("geometry_started", message="开始汇总全对象几何")
        selected_count = int((canonical_package_identity or {}).get("object_count", 0))
        geometry_metrics: dict[str, Any] = {}
        try:
            geometry_document = storage.read_json(
                result_dir / "stage8b_all_object_geometry.v1.json"
            )
            summary = geometry_document.get("summary")
            if isinstance(summary, dict):
                for key in (
                    "object_count", "geometry_success_count", "geometry_failure_count"
                ):
                    if isinstance(summary.get(key), (int, float)):
                        geometry_metrics[key] = summary[key]
        except (OSError, ValueError, TypeError):
            pass
        observe(
            "geometry_completed",
            status="success",
            message="全对象几何处理完成",
            details=geometry_metrics,
        )
        observe(
            "selection_completed", status="success", message="双门选择完成",
            details={"final_selected_count": selected_count},
        )
        observe("package_build_started", message="开始验证 canonical decision package")
        package_validation_started_at = utc_now()
        package_validation_started = time.perf_counter()
        expected_generation = (
            int(canonical_package_identity["generation"])
            if canonical_package_identity is not None
            else None
        )
        actual_generation = (
            int((record.get("frozen_runtime") or {})["generation"])
            if isinstance(record.get("frozen_runtime"), dict)
            and (record.get("frozen_runtime") or {}).get("generation") is not None
            else None
        )
        _, package_raw, package_sha256 = validate_decision_package(
            config,
            storage,
            scan_id,
            expected_length=(
                int(canonical_package_identity["bytes"])
                if canonical_package_identity is not None
                else None
            ),
            expected_sha256=(
                str(canonical_package_identity["sha256"])
                if canonical_package_identity is not None
                else None
            ),
            expected_generation=expected_generation,
            actual_generation=actual_generation,
        )
        package_validation_seconds = time.perf_counter() - package_validation_started
        _append_worker_phase(
            result_dir,
            record,
            phase="canonical_package_validation",
            phase_start=package_validation_started_at,
            phase_end=utc_now(),
            elapsed_seconds=package_validation_seconds,
            process_id=os.getpid(),
            command="bounded read + schema + sidecar SHA + scan_id + runtime generation",
            exit_code=0,
        )
        observe(
            "package_ready", status="success", message="decision package 已就绪",
            details={
                "package_size_bytes": len(package_raw),
                "sha256": package_sha256,
                "selected_count": selected_count,
            },
        )
        publication_started_at = utc_now()
        publication_started = time.perf_counter()
        record.update({
            "status": "decision_ready", "pipeline_job_id": result["job_id"],
            "pipeline_result_status": result["status"], "finished_at": None, "error": None,
            "package_length": len(package_raw),
            "package_sha256": package_sha256,
            "package_generation": expected_generation,
            "package_schema_version": (canonical_package_identity or {}).get("schema_version"),
            "package_validation_seconds": package_validation_seconds,
            "package_ready": True,
            "decision_ready_at": utc_now(),
            "full_scene_status": "pending",
            "full_scene_ready": False,
            "full_scene_error_code": None,
            "full_scene_error_message": None,
            "visualization_open_attempts": 0,
        })
        transition(record)
        package_published = True
        _append_worker_phase(
            result_dir,
            record,
            phase="package_atomic_publication",
            phase_start=publication_started_at,
            phase_end=utc_now(),
            elapsed_seconds=time.perf_counter() - publication_started,
            process_id=os.getpid(),
            command="atomically promoted canonical result tree + decision_ready transition",
            exit_code=0,
        )
    except subprocess.TimeoutExpired as exc:
        progress_state = record.get("_pipeline_progress_state") or {}
        last = progress_state.get("last_event") or child_last or {}
        last_metrics = last.get("metrics") if isinstance(last.get("metrics"), dict) else {}
        last_at = last.get("timestamp")
        seconds_since = None
        if isinstance(last_at, str):
            try:
                from datetime import datetime, timezone
                seconds_since = max(
                    0.0,
                    (datetime.now(timezone.utc) - datetime.fromisoformat(last_at.replace("Z", "+00:00"))).total_seconds(),
                )
            except ValueError:
                pass
        rolling_ms = None
        if frame_durations_ms:
            ordered = sorted(frame_durations_ms)
            middle = len(ordered) // 2
            rolling_ms = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0
        stalled_threshold = max(15.0, 3.0 * float(rolling_ms or 0.0) / 1000.0)
        stalled = seconds_since is None or seconds_since > stalled_threshold
        frame_text = (
            f"第{last.get('frame_index')}/{last.get('frame_total') or record.get('frame_count')}帧"
            if last.get("frame_index") else "尚无逐帧进度"
        )
        detail = (
            f"最后进度：{frame_text} · {last.get('subphase') or last.get('phase') or '未知阶段'}；"
            f"已完成{last_metrics.get('completed_frames', 0)}帧；"
            + (f"最近活动{seconds_since:.1f}秒前；" if seconds_since is not None else "无可用活动时间；")
            + ("最近没有新进度" if stalled else "仍有进度但整体较慢")
        )
        record.update({"status": "failed", "finished_at": utc_now(),
                       "error": {"error_code": "E_PIPELINE_TIMEOUT", "message": "PC处理超时", "detail": detail[:500],
                                 "phase": "shared_inference_geometry",
                                 "timeout_seconds": exc.timeout,
                                 "stdout_tail": _tail(exc.output),
                                 "stderr_tail": _tail(exc.stderr),
                                 "last_phase": last.get("phase"),
                                 "last_subphase": last.get("subphase"),
                                 "last_frame_id": last.get("frame_id"),
                                 "last_frame_index": last.get("frame_index"),
                                 "frame_total": last.get("frame_total") or record.get("frame_count"),
                                 "completed_frames": last_metrics.get("completed_frames", 0),
                                 "successful_frames": last_metrics.get("successful_frames", 0),
                                 "rejected_frames": last_metrics.get("rejected_frames", 0),
                                 "last_progress_at": last_at,
                                 "seconds_since_last_progress": seconds_since,
                                 "elapsed_seconds": time.perf_counter() - started,
                                 "rolling_frame_median_ms": rolling_ms,
                                 "suspected_no_progress": stalled,
                                 "observer_mode": progress_state.get("observer_mode", "artifact_fallback")}})
    except PipelineWorkerFailure as exc:
        record.update({
            "status": "failed",
            "finished_at": utc_now(),
            "error": dict(exc.error),
        })
    except ServiceError as exc:
        record.update({"status": "failed", "finished_at": utc_now(),
                       "error": {"error_code": exc.code, "message": exc.message}})
    except Exception as exc:
        record.update({"status": "failed", "finished_at": utc_now(),
                       "error": {"error_code": "E_SERVICE_INTERNAL",
                                 "message": f"internal worker failure: {type(exc).__name__}"}})
    if not package_published:
        error = record.get("error") or {}
        observe(
            "failed", status="error",
            message=str(error.get("message") or "PC pipeline 失败")[:240],
            error_code=str(error.get("error_code") or "E_PIPELINE_FAILED")[:64],
            error_detail=str(error.get("detail") or "")[:500] or None,
            details={
                key: error[key]
                for key in (
                    "phase", "error_type", "failed_frame_count", "last_phase", "last_subphase",
                    "last_frame_id", "last_frame_index", "frame_total", "completed_frames",
                    "successful_frames", "rejected_frames", "last_progress_at",
                    "seconds_since_last_progress", "elapsed_seconds", "observer_mode",
                    "rolling_frame_median_ms", "suspected_no_progress",
                )
                if error.get(key) is not None
            },
        )
        transition(record)
        publish_terminal_review_bundle(config, storage, record, progress_emit)
        return
    _finish_full_scene(
        config,
        storage,
        record,
        transition,
        allow_auto_open=True,
    )
    publish_terminal_review_bundle(config, storage, record, progress_emit)
