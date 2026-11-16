from __future__ import annotations

import asyncio
import inspect
import threading
import uuid
import hashlib
import json
import queue
import re
import urllib.parse
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .scan_service_contracts import (
    IMAGE_BYTES,
    MAX_FRAMES,
    MAX_OBJECTS,
    MAX_RESULT_BYTES,
    MAX_UPLOAD_BYTES,
    ServiceError,
    expected_fields,
    field_spec,
    health_payload,
    load_json_bytes,
    public_job,
    scan_identity,
    upload_only_receipt,
    utc_now,
    validate_content_type,
    validate_filename,
)
from .scan_service_storage import ServiceStorage
from .scan_progress import ProgressStore, ScanTrafficProgressMiddleware
from .scan_service_worker import (
    FORMAL_PIPELINE_TIMEOUT_SECONDS,
    WorkerConfig,
    _visualization_html,
    run_pipeline,
    validate_decision_package,
    validate_result_files,
    validate_scan,
    worker_boundary_state,
)
from .vision_console import ERROR_GUIDANCE, VISION_CONSOLE_HTML, vision_health
from .yolo_runtime_supervisor import YoloRuntimeSupervisor
from .frozen_runtime_supervisor import FrozenScanRuntimeSupervisor
from .performance_guard import (
    CanaryResult,
    IsolatedH0Canary,
    PerformanceGuard,
    PerformanceGuardConfig,
)
from .scan_contracts import ScanContractError
from .validation_receipt import create_and_write_validation_receipt


def _validate_recorded_decision_package(
    worker_config: WorkerConfig,
    storage: ServiceStorage,
    record: dict[str, Any],
) -> tuple[dict[str, Any], bytes, str]:
    runtime = record.get("frozen_runtime") if isinstance(record.get("frozen_runtime"), dict) else {}
    return validate_decision_package(
        worker_config,
        storage,
        str(record["scan_id"]),
        expected_length=(int(record["package_length"]) if record.get("package_length") is not None else None),
        expected_sha256=(str(record["package_sha256"]) if record.get("package_sha256") else None),
        expected_generation=(int(record["package_generation"]) if record.get("package_generation") is not None else None),
        actual_generation=(int(runtime["generation"]) if runtime.get("generation") is not None else None),
    )


@dataclass(frozen=True)
class ServiceConfig:
    data_root: Path = (Path(__file__).resolve().parents[3] / "_deltar_scan_service_data")
    pipeline_root: Path = (Path(__file__).resolve().parents[3] / "_deltar_p4_pipeline")
    worker_python: Path = (Path(__file__).resolve().parents[3] / "_deltar_formal" / "envs" / "deltar_py311_torch271_cu128" / "python.exe")
    profile_bundle: Path | None = None
    max_upload_bytes: int = MAX_UPLOAD_BYTES
    max_result_bytes: int = MAX_RESULT_BYTES
    max_frames: int = MAX_FRAMES
    max_objects: int = MAX_OBJECTS
    validate_timeout_seconds: int = 30
    job_timeout_seconds: int = FORMAL_PIPELINE_TIMEOUT_SECONDS
    device: str = "cuda:0"
    auto_open_visualization: bool = False
    visualization_timeout_seconds: int = 600
    pipeline_version: str = "deltar_pc_pipeline.stage6.2"
    require_local_preflight: bool = False
    enable_yolo_runtime: bool = False
    enable_frozen_runtime: bool = False
    enable_performance_guard: bool = True
    performance_threshold_ms: float = 20_000.0
    performance_periodic_interval_s: float = 600.0
    performance_idle_grace_s: float = 5.0
    performance_recovery_attempt: int = 0

    def worker(self) -> WorkerConfig:
        return WorkerConfig(
            pipeline_root=self.pipeline_root.resolve(),
            worker_python=self.worker_python.resolve(),
            profile_bundle=(self.profile_bundle or self.pipeline_root / "profiles").resolve(),
            validate_timeout_seconds=self.validate_timeout_seconds,
            job_timeout_seconds=self.job_timeout_seconds,
            max_result_bytes=self.max_result_bytes,
            max_objects=self.max_objects,
            device=self.device,
            auto_open_visualization=self.auto_open_visualization,
            visualization_timeout_seconds=self.visualization_timeout_seconds,
        )


def local_runtime_state(config: ServiceConfig) -> dict[str, Any]:
    path = (config.data_root / "progress" / "offline_preflight.json").resolve()
    fallback = {
        "schema_version": "deltar_local_runtime_preflight.v1",
        "status": "not_checked",
        "ready": False,
        "processing_mode": "local_only",
        "runtime_network_required": False,
        "required": config.require_local_preflight,
        "checked_at": None,
        "error": {"message": "local model preflight has not run"},
    }
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return fallback
    if not isinstance(value, dict) or value.get("schema_version") != fallback["schema_version"]:
        return fallback
    return {
        **value,
        "ready": bool(value.get("ready")) and value.get("status") == "ready",
        "required": config.require_local_preflight,
    }


class JobManager:
    def __init__(
        self,
        config: ServiceConfig,
        progress: ProgressStore | None = None,
        performance_callback: Callable[[str, float | None], None] | None = None,
    ):
        self.config = config
        self.progress = progress or ProgressStore(config.data_root / "progress")
        self.worker_config = config.worker()
        self.storage = ServiceStorage(config.data_root)
        self.lock = threading.RLock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.scan_jobs: dict[str, str] = {}
        self.active_service_job_id: str | None = None
        self.reserved_scan_id: str | None = None
        self.reserved_job_role: str | None = None
        self.performance_callback = performance_callback
        self._performance_recorded_jobs: set[str] = set()
        self.storage.recover_uploads()
        self._reconstruct()

    def _load_metadata(self, scan_id: str, *directories: Path) -> dict[str, Any] | None:
        for directory in directories:
            path = directory / "service_job.json"
            if path.is_file():
                try:
                    record = self.storage.read_json(path)
                except (OSError, UnicodeError, ValueError):
                    continue
                if record.get("scan_id") == scan_id and type(record.get("service_job_id")) is str:
                    return record
        return None

    def _reconstruct(self) -> None:
        completed_scans: set[str] = set()
        for directory in sorted(self.storage.results.iterdir()):
            if not directory.is_dir():
                continue
            scan_id = directory.name
            record = self._load_metadata(scan_id, directory, self.storage.validated_dir(scan_id))
            if record is None:
                continue
            try:
                result, _ = validate_result_files(self.worker_config, self.storage, scan_id)
            except ServiceError:
                record.update({"status": "failed", "pipeline_result_status": None,
                               "error": {"error_code": "E_RESULT_SCHEMA", "message": "stored result failed restart validation"},
                               "finished_at": record.get("finished_at") or utc_now()})
            else:
                try:
                    _, package_raw, package_sha256 = _validate_recorded_decision_package(
                        self.worker_config, self.storage, record
                    )
                except ServiceError:
                    record.update({
                        "status": "failed",
                        "pipeline_result_status": result["status"],
                        "error": {
                            "error_code": "E_DECISION_SCHEMA",
                            "message": "stored decision package failed restart validation",
                        },
                        "finished_at": record.get("finished_at") or utc_now(),
                    })
                else:
                    record.update({
                        "status": "decision_ready",
                        "pipeline_job_id": result["job_id"],
                        "pipeline_result_status": result["status"],
                        "error": None,
                        "package_ready": True,
                        "package_length": len(package_raw),
                        "package_sha256": package_sha256,
                    })
                    try:
                        html = _visualization_html(self.storage.result_dir(scan_id))
                    except ServiceError:
                        record.update({
                            "full_scene_status": "failed",
                            "full_scene_ready": False,
                            "full_scene_error_code": "E_FULL_SCENE_INTERRUPTED",
                            "full_scene_error_message": (
                                "service restarted before cached visualization completed"
                            ),
                            "finished_at": record.get("finished_at") or utc_now(),
                        })
                    else:
                        record.update({
                            "status": "completed",
                            "full_scene_status": "ready",
                            "full_scene_ready": True,
                            "full_scene_error_code": None,
                            "full_scene_error_message": None,
                            "visualization_html_path": str(html),
                            "finished_at": record.get("finished_at") or utc_now(),
                        })
                    completed_scans.add(scan_id)
            self._index(record)
            self._persist(record)
        for directory in sorted(self.storage.validated.iterdir()):
            if not directory.is_dir() or directory.name in completed_scans:
                continue
            record = self._load_metadata(directory.name, directory)
            if record is None:
                continue
            if record.get("mode") == "upload_only":
                record.update({
                    "status": "completed",
                    "pipeline_result_status": None,
                    "finished_at": record.get("finished_at") or utc_now(),
                    "error": None,
                })
                self._index(record)
                self._persist(record)
                continue
            record.update({"status": "failed", "pipeline_result_status": None, "finished_at": utc_now(),
                           "error": {"error_code": "E_PIPELINE_FAILED", "message": "stale_failed_no_resume"}})
            self._index(record)
            self._persist(record)

    def _index(self, record: dict[str, Any]) -> None:
        self.jobs[record["service_job_id"]] = record
        self.scan_jobs[record["scan_id"]] = record["service_job_id"]

    def _persist(self, record: dict[str, Any]) -> None:
        scan_id = record["scan_id"]
        result_dir = self.storage.result_dir(scan_id)
        validated_dir = self.storage.validated_dir(scan_id)
        if result_dir.is_dir():
            self.storage.atomic_json(result_dir / "service_job.json", record)
            if validated_dir.is_dir():
                self.storage.atomic_json(
                    validated_dir / "service_job.json", record
                )
        elif validated_dir.is_dir():
            self.storage.atomic_json(validated_dir / "service_job.json", record)

    def reserve(
        self, scan_id: str, *, job_role: str = "user"
    ) -> tuple[str, dict[str, Any] | None]:
        with self.lock:
            existing_id = self.scan_jobs.get(scan_id)
            if existing_id is not None:
                return "existing", dict(self.jobs[existing_id])
            if self.active_service_job_id is not None or self.reserved_scan_id is not None:
                active_scan = self.reserved_scan_id
                if self.active_service_job_id is not None:
                    active_scan = self.jobs[self.active_service_job_id]["scan_id"]
                if active_scan == scan_id and self.active_service_job_id is not None:
                    return "existing", dict(self.jobs[self.active_service_job_id])
                raise ServiceError("E_SERVICE_BUSY", "another scan is active", 503, scan_id=scan_id)
            if self.storage.validated_dir(scan_id).exists() or self.storage.result_dir(scan_id).exists():
                raise ServiceError("E_SCAN_CONFLICT", "scan_id already exists and cannot be overwritten", 409, scan_id=scan_id)
            self.reserved_scan_id = scan_id
            self.reserved_job_role = job_role
            return "reserved", None

    def release_reservation(self, scan_id: str) -> None:
        with self.lock:
            if self.reserved_scan_id == scan_id:
                self.reserved_scan_id = None
                self.reserved_job_role = None

    def accept(self, record: dict[str, Any]) -> None:
        with self.lock:
            self._index(record)
            self.active_service_job_id = record["service_job_id"]
            self.reserved_scan_id = None
            self.reserved_job_role = None
            self._persist(record)

    def start(self, record: dict[str, Any]) -> None:
        record.update({"status": "running", "started_at": utc_now()})
        self._persist(record)
        runner_args: tuple[Any, ...] = (
            self.worker_config, self.storage, record, self.transition
        )
        # Existing host tests and deployments may inject the frozen four-arg
        # runner.  The optional observer is passed only when explicitly
        # supported, preserving that compatibility seam.
        if "progress_emit" in inspect.signature(run_pipeline).parameters:
            runner_args += (self.progress.emit,)
        thread = threading.Thread(
            target=run_pipeline,
            args=runner_args,
            name=f"scan-service-{record['service_job_id']}", daemon=True,
        )
        thread.start()

    def transition(self, record: dict[str, Any]) -> None:
        performance_event: tuple[str, float | None] | None = None
        with self.lock:
            self.jobs[record["service_job_id"]] = record
            self._persist(record)
            if self.active_service_job_id == record["service_job_id"]:
                self.active_service_job_id = None
            service_job_id = str(record["service_job_id"])
            if service_job_id not in self._performance_recorded_jobs:
                runtime = record.get("frozen_runtime")
                wall_ms = runtime.get("job_wall_ms") if isinstance(runtime, dict) else None
                if (
                    record.get("job_role", "user") == "user"
                    and record.get("status") in {"decision_ready", "completed"}
                    and isinstance(wall_ms, (int, float))
                ):
                    self._performance_recorded_jobs.add(service_job_id)
                    performance_event = ("completed", float(wall_ms))
                elif record.get("job_role", "user") == "user" and record.get("status") == "failed":
                    self._performance_recorded_jobs.add(service_job_id)
                    performance_event = ("failed", None)
        if performance_event is not None and self.performance_callback is not None:
            self.performance_callback(*performance_event)

    def get(self, service_job_id: str) -> dict[str, Any]:
        with self.lock:
            record = self.jobs.get(service_job_id)
            if record is None:
                raise ServiceError("E_JOB_NOT_FOUND", "service job was not found", 404, service_job_id=service_job_id)
            return dict(record)

    def get_by_scan(self, scan_id: str) -> dict[str, Any] | None:
        with self.lock:
            service_job_id = self.scan_jobs.get(scan_id)
            return (
                None
                if service_job_id is None
                else dict(self.jobs[service_job_id])
            )

    def observer_context(self) -> tuple[str | None, list[dict[str, Any]]]:
        with self.lock:
            active_scan_id: str | None = None
            if (
                self.active_service_job_id is not None
                and self.active_service_job_id in self.jobs
            ):
                active_record = self.jobs[self.active_service_job_id]
                if active_record.get("job_role", "user") == "user":
                    active_scan_id = str(active_record["scan_id"])
            elif self.reserved_scan_id is not None and self.reserved_job_role == "user":
                active_scan_id = self.reserved_scan_id
            return active_scan_id, [dict(record) for record in self.jobs.values()]


def _upload_source_identity(uploads: dict[str, UploadFile]) -> str:
    digest = hashlib.sha256()
    for name in sorted(uploads):
        digest.update(len(name).to_bytes(2, "big"))
        digest.update(name.encode("ascii"))
        upload = uploads[name]
        upload.file.seek(0)
        while True:
            block = upload.file.read(64 * 1024)
            if not block:
                break
            digest.update(len(block).to_bytes(4, "big"))
            digest.update(block)
        upload.file.seek(0)
    return digest.hexdigest()


def _error_response(error: ServiceError) -> JSONResponse:
    return JSONResponse(error.payload(), status_code=error.status_code)


FILE_PNG_URI_RE = re.compile(r"file:///[^\"'<>\s]+\.png", re.IGNORECASE)
CANONICAL_FRAME_IDS = frozenset(f"frame_{index:06d}" for index in range(11))
PORTABLE_VIEWER_MARKER = b'<meta name="viewer-asset-mode" content="portable_relative_v1">'


def _file_uri_path(uri: str) -> Path:
    parsed = urllib.parse.urlsplit(uri)
    raw_path = urllib.parse.unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:/", raw_path):
        raw_path = raw_path[1:]
    return Path(raw_path.replace("/", "\\"))


def _visualization_http_html(html_path: Path, result_dir: Path, scan_id: str) -> str:
    html = html_path.read_text(encoding="utf-8")
    root = result_dir.resolve()

    def replace_uri(match: re.Match[str]) -> str:
        candidate = _file_uri_path(match.group(0)).resolve()
        frame_id = candidate.parent.name
        published_source = root / "logs" / frame_id / "source_rgb.png"
        if (
            candidate.name.lower() == "source_rgb.png"
            and candidate.parent.parent.name.lower() == "logs"
            and frame_id in CANONICAL_FRAME_IDS
            and published_source.is_file()
        ):
            return (
                f"/api/v1/vision-console/scans/{urllib.parse.quote(scan_id, safe='')}"
                f"/scene-assets/{frame_id}/source.png"
            )
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return match.group(0)
        if candidate.suffix.lower() != ".png" or not candidate.is_file():
            return match.group(0)
        encoded = "/".join(urllib.parse.quote(part, safe="") for part in relative.parts)
        return (
            f"/api/v1/vision-console/scans/{urllib.parse.quote(scan_id, safe='')}"
            f"/assets/{encoded}"
        )

    return FILE_PNG_URI_RE.sub(replace_uri, html)


def _safe_visualization_asset(result_dir: Path, asset_path: str) -> Path | None:
    if not asset_path or "%" in asset_path or "\\" in asset_path:
        return None
    relative = Path(asset_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        return None
    root = result_dir.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.suffix.lower() != ".png" or not candidate.is_file():
        return None
    return candidate


def create_app(
    config: ServiceConfig | None = None,
    *,
    request_full_service_recycle: Callable[[], None] | None = None,
    canary_runner: Callable[[str, threading.Event], CanaryResult] | None = None,
) -> FastAPI:
    service_config = config or ServiceConfig()
    progress = ProgressStore(service_config.data_root / "progress")
    frozen_runtime: FrozenScanRuntimeSupervisor | None = None
    guard_active = bool(
        service_config.enable_performance_guard
        and service_config.enable_yolo_runtime
        and service_config.enable_frozen_runtime
    )

    def run_canary(source: str, cancel: threading.Event) -> CanaryResult:
        if canary_runner is not None:
            return canary_runner(source, cancel)
        if frozen_runtime is None:
            return CanaryResult("error", message="Frozen runtime is not ready")
        runner = IsolatedH0Canary(
            data_root=service_config.data_root,
            pipeline_root=service_config.pipeline_root,
            profile_dir=service_config.profile_bundle or service_config.pipeline_root / "profiles",
            device=service_config.device,
            frozen_runtime=frozen_runtime,
        )
        return runner(source, cancel)

    guard = PerformanceGuard(
        run_canary,
        request_full_service_recycle or (lambda: None),
        config=PerformanceGuardConfig(
            threshold_ms=service_config.performance_threshold_ms,
            periodic_interval_s=service_config.performance_periodic_interval_s,
            idle_grace_s=service_config.performance_idle_grace_s,
            recovery_attempt=service_config.performance_recovery_attempt,
        ),
    )

    def performance_event(kind: str, wall_ms: float | None) -> None:
        if not guard_active:
            return
        if kind == "completed" and wall_ms is not None:
            guard.real_scan_completed(wall_ms)
        elif kind == "failed":
            guard.real_scan_failed()
            guard.real_business_finished()

    manager = JobManager(service_config, progress, performance_event)
    app = FastAPI(title="DELTAR PC Scan Service", version="1.0")
    app.state.scan_manager = manager
    app.state.scan_progress = progress
    app.state.performance_guard = guard
    app.state.performance_guard_active = guard_active
    runtime_bootstrap: dict[str, Any] = {
        "schema_version": "deltar_scan_runtime_bootstrap.v1",
        "state": "NOT_STARTED",
        "ready": False,
        "started_at": None,
        "finished_at": None,
        "errors": [],
        "warnings": [],
        "performance_guard_started": False,
    }
    runtime_bootstrap_lock = threading.RLock()
    app.state.runtime_bootstrap = runtime_bootstrap
    yolo_runtime = (
        YoloRuntimeSupervisor(
            service_config.pipeline_root,
            service_config.data_root,
            job_timeout_s=float(service_config.job_timeout_seconds),
            progress_emit=progress.emit,
        )
        if service_config.enable_yolo_runtime
        else None
    )
    app.state.yolo_runtime = yolo_runtime
    app.state.frozen_scan_runtime = frozen_runtime

    def upload_finished_without_new_job() -> None:
        with manager.lock:
            has_active_job = (
                manager.active_service_job_id is not None
                or manager.reserved_scan_id is not None
            )
        if has_active_job:
            guard.upload_request_finished_without_job()
        else:
            guard.real_business_finished()

    app.add_middleware(
        ScanTrafficProgressMiddleware,
        store=progress,
        on_real_traffic_start=(guard.preempt_for_real_traffic if guard_active else None),
        on_upload_without_job=(upload_finished_without_new_job if guard_active else None),
        on_package_completed=(guard.real_business_finished if guard_active else None),
    )

    @app.on_event("startup")
    async def start_scan_runtimes() -> None:
        nonlocal frozen_runtime
        with runtime_bootstrap_lock:
            runtime_bootstrap.update(
                state="STARTING", ready=False, started_at=utc_now(), finished_at=None
            )
            runtime_bootstrap["errors"] = []

        def record_startup_error(component: str, exc: BaseException) -> None:
            with runtime_bootstrap_lock:
                runtime_bootstrap["errors"].append(
                    {
                        "component": component,
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:1000],
                    }
                )

        if service_config.enable_frozen_runtime and yolo_runtime is None:
            record_startup_error(
                "configuration",
                RuntimeError(
                    "Frozen Scan Runtime requires the persistent YOLO Runtime"
                ),
            )
        if yolo_runtime is not None:
            try:
                await run_in_threadpool(yolo_runtime.start)
            except Exception as exc:
                record_startup_error("yolo_runtime", exc)
        yolo_ready = (
            yolo_runtime is None
            or bool(yolo_runtime.status_payload().get("ready"))
        )
        if service_config.enable_frozen_runtime and yolo_ready:
            frozen_runtime = FrozenScanRuntimeSupervisor(
                service_config.pipeline_root,
                service_config.data_root,
                service_config.worker_python,
                service_config.profile_bundle or service_config.pipeline_root / "profiles",
                device=service_config.device,
                calibration_paths=[],
                yolo_runtime_endpoint=(yolo_runtime.endpoint if yolo_runtime is not None else None),
                yolo_runtime_token=(yolo_runtime.token if yolo_runtime is not None else None),
                job_timeout_s=float(service_config.job_timeout_seconds),
                progress_emit=progress.emit,
            )
            try:
                await run_in_threadpool(frozen_runtime.start)
            except Exception as exc:
                record_startup_error("frozen_scan_runtime", exc)
            app.state.frozen_scan_runtime = frozen_runtime
        elif service_config.enable_frozen_runtime:
            record_startup_error(
                "frozen_scan_runtime",
                RuntimeError("persistent YOLO Runtime is not ready"),
            )
        manager.worker_config = replace(
            manager.worker_config,
            yolo_runtime_endpoint=(yolo_runtime.endpoint if yolo_runtime is not None else None),
            yolo_runtime_token=(yolo_runtime.token if yolo_runtime is not None else None),
            frozen_runtime=frozen_runtime,
        )
        frozen_ready = (
            frozen_runtime is None
            or bool(frozen_runtime.status_payload().get("ready"))
        )
        if guard_active and yolo_ready and frozen_ready:
            try:
                await run_in_threadpool(guard.start)
                runtime_bootstrap["performance_guard_started"] = True
            except Exception as exc:
                guard.mark_failed(
                    f"performance guard background startup failed: {type(exc).__name__}"
                )
                runtime_bootstrap["warnings"].append(
                    {
                        "component": "performance_guard",
                        "message": "performance guard background startup failed",
                    }
                )
        worker_boundary = worker_boundary_state(manager.worker_config)
        with runtime_bootstrap_lock:
            runtime_bootstrap["finished_at"] = utc_now()
            runtime_bootstrap["ready"] = bool(
                worker_boundary["ready"]
                and (not service_config.enable_yolo_runtime or yolo_ready)
                and (not service_config.enable_frozen_runtime or frozen_ready)
                and not runtime_bootstrap["errors"]
            )
            runtime_bootstrap["state"] = (
                "READY" if runtime_bootstrap["ready"] else "FAILED"
            )

    @app.on_event("shutdown")
    async def stop_scan_runtimes() -> None:
        if runtime_bootstrap.get("performance_guard_started"):
            guard.stop()
        if frozen_runtime is not None:
            await run_in_threadpool(frozen_runtime.stop)
        if yolo_runtime is not None:
            await run_in_threadpool(yolo_runtime.stop)

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
        if request.method == "POST" and request.url.path == "/api/v1/scan/jobs":
            progress.emit(
                "failed",
                request_id=getattr(request.state, "progress_request_id", None),
                scan_id=exc.scan_id,
                status="error",
                message=exc.message,
                error_code=exc.code,
                error_detail=exc.field,
                job_role=getattr(request.state, "visionpc_job_role", "user"),
                visibility=getattr(request.state, "visionpc_visibility", "user"),
            )
        return _error_response(exc)

    @app.exception_handler(Exception)
    async def internal_error_handler(_: Request, __: Exception) -> JSONResponse:
        return _error_response(ServiceError("E_SERVICE_INTERNAL", "internal service error", 500))

    @app.get("/api/v1/scan/health")
    async def health() -> dict[str, Any]:
        with manager.lock:
            active = manager.active_service_job_id
            busy = active is not None or manager.reserved_scan_id is not None
            ordered_jobs = sorted(
                manager.jobs.values(),
                key=lambda item: str(item.get("created_at") or ""),
                reverse=True,
            )
            last_job = ordered_jobs[0] if ordered_jobs else None
        runtime = local_runtime_state(service_config)
        worker_boundary = worker_boundary_state(manager.worker_config)
        payload = health_payload(
            busy=busy, active_service_job_id=active, pipeline_version=service_config.pipeline_version,
            max_upload_bytes=service_config.max_upload_bytes, max_frames=service_config.max_frames,
            max_result_bytes=service_config.max_result_bytes, max_objects=service_config.max_objects,
        )
        payload["local_runtime"] = runtime
        payload["service_alive"] = True
        payload["authority_config_ready"] = bool(runtime.get("ready"))
        payload["worker_boundary"] = worker_boundary
        with runtime_bootstrap_lock:
            bootstrap_payload = {
                **runtime_bootstrap,
                "errors": list(runtime_bootstrap["errors"]),
                "warnings": list(runtime_bootstrap["warnings"]),
            }
            payload["runtime_bootstrap"] = bootstrap_payload
        payload["last_job"] = (
            {
                "service_job_id": last_job.get("service_job_id"),
                "scan_id": last_job.get("scan_id"),
                "status": last_job.get("status"),
                "created_at": last_job.get("created_at"),
                "finished_at": last_job.get("finished_at"),
                "error": last_job.get("error"),
            }
            if last_job is not None
            else None
        )
        payload["yolo_runtime"] = (
            yolo_runtime.status_payload()
            if yolo_runtime is not None
            else {"state": "DISABLED", "ready": False}
        )
        payload["frozen_scan_runtime"] = (
            frozen_runtime.status_payload()
            if frozen_runtime is not None
            else {"state": "DISABLED", "ready": False}
        )
        yolo_operational = bool(payload["yolo_runtime"].get("ready")) or bool(
            payload["yolo_runtime"].get("state") == "BUSY"
            and payload["yolo_runtime"].get("process_alive")
        )
        frozen_operational = bool(
            payload["frozen_scan_runtime"].get("ready")
        ) or bool(
            payload["frozen_scan_runtime"].get("state") == "BUSY"
            and payload["frozen_scan_runtime"].get("process_alive")
        )
        payload["yolo_runtime"]["operational_ready"] = yolo_operational
        payload["frozen_scan_runtime"]["operational_ready"] = frozen_operational
        payload["performance_guard"] = guard.snapshot()
        payload["warnings"] = []
        guard_status = payload["performance_guard"]["status"]
        if guard_status == "startup_check":
            payload["warnings"].append("performance_canary_checking")
        elif guard_status in {"degraded", "failed"}:
            wall_ms = payload["performance_guard"].get("last_job_wall_ms")
            warning = (
                "performance_canary_slow"
                if wall_ms is not None
                and float(wall_ms) > float(payload["performance_guard"]["threshold_ms"])
                else "performance_guard_failed"
            )
            payload["warnings"].append(warning)
            bootstrap_payload["warnings"].append(
                {"component": "performance_guard", "message": warning}
            )
        if not worker_boundary["ready"]:
            payload["status"] = "worker_boundary_not_ready"
        if yolo_runtime is not None and not yolo_operational:
            payload["status"] = "runtime_not_ready"
        if frozen_runtime is not None and not frozen_operational:
            payload["status"] = "runtime_not_ready"
        if service_config.require_local_preflight and not runtime["ready"]:
            payload["status"] = "preflight_failed"
        if (
            (service_config.enable_yolo_runtime or service_config.enable_frozen_runtime)
            and not runtime_bootstrap.get("ready")
        ):
            payload["status"] = "runtime_not_ready"
        return payload

    @app.post("/api/v1/scan/jobs")
    async def create_job(
        request: Request, mode: str = "full_pipeline"
    ) -> JSONResponse:
        request_id = getattr(request.state, "progress_request_id", "req_" + uuid.uuid4().hex)
        job_role = getattr(request.state, "visionpc_job_role", "user")
        visibility = getattr(request.state, "visionpc_visibility", "user")
        if job_role == "user" and guard_active and runtime_bootstrap.get("ready"):
            guard.admit_real_scan()
        if mode not in {"full_pipeline", "upload_only"}:
            raise ServiceError(
                "E_UPLOAD_MODE",
                "mode must be full_pipeline or upload_only",
                422,
                "mode",
            )
        if mode == "full_pipeline":
            boundary = worker_boundary_state(manager.worker_config)
            runtime_errors = list(runtime_bootstrap.get("errors") or [])
            runtime_ready = bool(runtime_bootstrap.get("ready"))
            if not boundary["ready"] or (
                (service_config.enable_yolo_runtime or service_config.enable_frozen_runtime)
                and not runtime_ready
            ):
                detail = (
                    str(runtime_errors[0].get("message"))
                    if runtime_errors
                    else "pipeline worker boundary is not ready"
                )
                raise ServiceError(
                    "E_WORKER_NOT_READY",
                    detail[:160],
                    503,
                    "worker_boundary",
                )
        if mode == "full_pipeline" and service_config.require_local_preflight:
            runtime = local_runtime_state(service_config)
            if not runtime["ready"]:
                message = str((runtime.get("error") or {}).get("message") or "local model preflight failed")
                raise ServiceError(
                    "E_DELTAR_PREFLIGHT",
                    message[:160],
                    503,
                    "local_runtime",
                )
        transfer_encoding = request.headers.get("transfer-encoding", "").lower()
        if "chunked" in transfer_encoding:
            raise ServiceError("E_REQUEST_CONTENT_LENGTH", "chunked requests are not accepted", 411, "Content-Length")
        length_header = request.headers.get("content-length")
        try:
            length = int(length_header) if length_header is not None else 0
        except ValueError as exc:
            raise ServiceError("E_REQUEST_CONTENT_LENGTH", "Content-Length must be a positive integer", 411, "Content-Length") from exc
        if length <= 0:
            raise ServiceError("E_REQUEST_CONTENT_LENGTH", "Content-Length is required", 411, "Content-Length")
        if length > service_config.max_upload_bytes:
            raise ServiceError("E_UPLOAD_TOO_LARGE", "request exceeds the upload limit", 413, "Content-Length")
        content_type = request.headers.get("content-type", "")
        if not content_type.lower().startswith("multipart/form-data;") or "boundary=" not in content_type.lower():
            raise ServiceError("E_REQUEST_CONTENT_TYPE", "multipart/form-data with a boundary is required", 415, "Content-Type")

        try:
            async with request.form(max_files=1 + 3 * service_config.max_frames, max_fields=0,
                                    max_part_size=service_config.max_upload_bytes) as form:
                items = list(form.multi_items())
                names = [name for name, _ in items]
                if len(names) != len(set(names)):
                    duplicate = next(name for name in names if names.count(name) > 1)
                    raise ServiceError("E_MULTIPART_DUPLICATE", "multipart fields must be unique", 422, duplicate)
                uploads: dict[str, UploadFile] = {}
                for name, value in items:
                    if not isinstance(value, UploadFile):
                        raise ServiceError("E_MULTIPART_FIELD", "all multipart parts must be files", 422, name)
                    field_spec(name)
                    validate_filename(name, value.filename)
                    validate_content_type(name, value.content_type)
                    uploads[name] = value
                scan_upload = uploads.get("scan_json")
                if scan_upload is None:
                    raise ServiceError("E_MULTIPART_MISSING", "scan_json part is required", 422, "scan_json")
                raw_scan = await scan_upload.read(64 * 1024 + 1)
                await scan_upload.seek(0)
                if len(raw_scan) > 64 * 1024:
                    raise ServiceError("E_PART_TOO_LARGE", "scan_json exceeds 64 KiB", 413, "scan_json")
                scan = load_json_bytes(raw_scan)
                scan_id, frame_count = scan_identity(scan, max_frames=service_config.max_frames)
                progress.bind_request(request_id, scan_id)
                required = expected_fields(frame_count)
                missing = sorted(required - set(uploads))
                extra = sorted(set(uploads) - required)
                if missing:
                    rgb_count = sum(name.endswith("_image_rgb565") for name in uploads)
                    tof_count = sum(name.endswith("_tof_json") for name in uploads)
                    metadata_count = sum(name.endswith("_frame_json") for name in uploads)
                    progress.emit(
                        "failed", request_id=request_id, scan_id=scan_id,
                        status="error", message=(
                            f"11帧文件缺失：RGB {rgb_count}/{frame_count}，"
                            f"ToF {tof_count}/{frame_count}，frame.json {metadata_count}/{frame_count}"
                        ),
                        error_code="E_MULTIPART_MISSING", error_detail=missing[0],
                        details={
                            "rgb_file_count": rgb_count,
                            "tof_file_count": tof_count,
                            "metadata_count": metadata_count,
                            "frame_count": frame_count,
                        },
                    )
                    raise ServiceError("E_MULTIPART_MISSING", "required multipart fields are missing", 422, missing[0], scan_id=scan_id)
                if extra:
                    raise ServiceError("E_MULTIPART_FIELD", "extra multipart fields are not accepted", 422, extra[0], scan_id=scan_id)
                source_identity = await run_in_threadpool(
                    _upload_source_identity, uploads
                )
                disposition, existing = manager.reserve(scan_id, job_role=job_role)
                if disposition == "existing" and existing is not None:
                    existing_mode = existing.get("mode", "full_pipeline")
                    if existing_mode != mode:
                        raise ServiceError(
                            "E_SCAN_CONFLICT",
                            "scan_id already exists under a different mode",
                            409,
                            scan_id=scan_id,
                        )
                    if existing.get("source_identity") not in {
                        None, source_identity
                    }:
                        raise ServiceError(
                            "E_SCAN_CONFLICT",
                            "scan_id already exists with different source bytes",
                            409,
                            scan_id=scan_id,
                        )
                    if mode == "upload_only" and existing["status"] == "completed":
                        return JSONResponse(
                            upload_only_receipt(existing, idempotent=True),
                            status_code=200,
                        )
                    code = (
                        200
                        if existing["status"] in {"decision_ready", "completed"}
                        else 202
                    )
                    return JSONResponse(public_job(existing, idempotent=True), status_code=code)

                service_job_id = "svc_" + uuid.uuid4().hex
                uploading = manager.storage.uploading_dir(scan_id, service_job_id)
                validated: Path | None = None
                try:
                    uploading.mkdir(parents=True, exist_ok=False)
                    uploaded_file_identities: dict[str, dict[str, Any]] = {}
                    for field in sorted(required):
                        relative, _, maximum, category = field_spec(field)
                        upload = uploads[field]
                        await upload.seek(0)
                        exact = IMAGE_BYTES if category == "image" else None
                        file_identity: dict[str, Any] = {}
                        try:
                            manager.storage.write_stream(
                                upload.file,
                                uploading / Path(relative),
                                maximum=maximum,
                                exact=exact,
                                identity=file_identity,
                            )
                            uploaded_file_identities[Path(relative).as_posix()] = file_identity
                        except ServiceError as exc:
                            if exc.field is None:
                                raise ServiceError(exc.code, exc.message, exc.status_code, field, scan_id=scan_id) from exc
                            raise
                    progress.emit(
                        "validation_started", request_id=request_id, scan_id=scan_id,
                        message="开始校验扫描文件",
                    )
                    validation = await run_in_threadpool(
                        validate_scan,
                        manager.worker_config,
                        uploading,
                        uploaded_file_identities,
                    )
                    progress.emit(
                        "validation_completed", request_id=request_id, scan_id=scan_id,
                        status="success", current=frame_count, total=service_config.max_frames,
                        elapsed_ms=int(float(validation["elapsed_seconds"]) * 1000),
                        message=f"{frame_count} / {service_config.max_frames} 帧校验通过",
                        details={
                            "frame_count": frame_count,
                            "rgb_file_count": frame_count,
                            "tof_file_count": frame_count,
                            "metadata_count": frame_count,
                        },
                    )
                    validated = manager.storage.commit_validated(uploading, scan_id)
                    try:
                        receipt = create_and_write_validation_receipt(
                            validated,
                            scan_id=scan_id,
                            expected_frame_count=frame_count,
                            authoritative_manifest=validation["authoritative_manifest"],
                        )
                    except ScanContractError as exc:
                        raise ServiceError(
                            "E_SCAN_VALIDATE", "validated input receipt publication failed", 422, scan_id=scan_id
                        ) from exc
                    record = {
                        "schema_version": "deltar_scan_service_job_internal.v1",
                        "service_job_id": service_job_id, "pipeline_job_id": None, "scan_id": scan_id,
                        "status": "accepted", "pipeline_result_status": None, "created_at": utc_now(),
                        "started_at": None, "finished_at": None, "error": None,
                        "mode": mode, "frame_count": frame_count,
                        "source_identity": source_identity,
                        "upload_bytes": length, "validate_seconds": validation["elapsed_seconds"],
                        "validation_compute_seconds": validation["compute_seconds"],
                        "validation_identity_seconds": validation["identity_seconds"],
                        "validation_subprocess_startup": validation["subprocess_startup"],
                        "validation_receipt_sha256": receipt["receipt_sha256"],
                        "package_ready": False,
                        "full_scene_status": "pending",
                        "full_scene_ready": False,
                        "full_scene_error_code": None,
                        "full_scene_error_message": None,
                        "job_role": job_role,
                        "visibility": visibility,
                    }
                    if mode == "upload_only":
                        record.update({
                            "status": "completed",
                            "started_at": record["created_at"],
                            "finished_at": utc_now(),
                        })
                        manager.accept(record)
                        manager.transition(record)
                        return JSONResponse(
                            upload_only_receipt(record, idempotent=False),
                            status_code=200,
                        )
                    manager.accept(record)
                    progress.emit(
                        "job_queued", request_id=request_id, scan_id=scan_id,
                        message="扫描任务已进入 PC pipeline 队列",
                        details={"service_job_id": service_job_id},
                    )
                    accepted = public_job(dict(record), idempotent=False)
                    manager.start(record)
                    return JSONResponse(accepted, status_code=202)
                except ServiceError as exc:
                    error = exc.payload()
                    if uploading.exists():
                        manager.storage.fail_upload(uploading, scan_id, service_job_id, error)
                    elif validated is not None and validated.exists():
                        manager.storage.fail_validated(validated, scan_id, service_job_id, error)
                    manager.release_reservation(scan_id)
                    raise
                except Exception as exc:
                    error = ServiceError("E_SERVICE_INTERNAL", "failed to receive upload", 500, scan_id=scan_id)
                    if uploading.exists():
                        manager.storage.fail_upload(uploading, scan_id, service_job_id, error.payload())
                    manager.release_reservation(scan_id)
                    raise error from exc
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError("E_MULTIPART_FIELD", "malformed multipart request", 422) from exc

    @app.get("/api/v1/scan/jobs/{service_job_id}")
    async def job_status(service_job_id: str) -> dict[str, Any]:
        return public_job(manager.get(service_job_id))

    @app.get("/api/v1/scan/jobs/{service_job_id}/result")
    async def job_result(service_job_id: str) -> Response:
        record = manager.get(service_job_id)
        if record["status"] not in {"decision_ready", "completed"}:
            message = "service job failed" if record["status"] == "failed" else "service job is not complete"
            code = record.get("error", {}).get("error_code", "E_JOB_NOT_COMPLETE") if record["status"] == "failed" else "E_JOB_NOT_COMPLETE"
            raise ServiceError(code, message, 409, service_job_id=service_job_id, scan_id=record["scan_id"])
        _, raw = validate_result_files(manager.worker_config, manager.storage, record["scan_id"])
        return Response(content=raw, media_type="application/json")

    @app.get("/api/v1/scans/{scan_id}/decision-status")
    async def decision_status(scan_id: str) -> dict[str, Any]:
        record = manager.get_by_scan(scan_id)
        if record is None:
            payload = {
                "scan_id": scan_id, "status": "not_found",
                "package_ready": False, "package_length": None,
                "package_sha256": None, "error_code": None,
                "error_message": None, "full_scene_ready": False,
            }
            progress.emit(
                "p4_status_polled", scan_id=scan_id,
                message="P4 查询 decision-status：not_found",
                details={"last_status_returned": "not_found"},
            )
            return payload
        if record["status"] in {"accepted", "running"}:
            status = "queued" if record["status"] == "accepted" else "processing"
            payload = {
                "scan_id": scan_id, "status": status,
                "package_ready": False, "package_length": None,
                "package_sha256": None, "error_code": None,
                "error_message": None, "full_scene_ready": False,
            }
            progress.emit(
                "p4_status_polled", scan_id=scan_id,
                message=f"P4 查询 decision-status：{status}",
                details={"last_status_returned": status},
            )
            return payload
        if record["status"] == "failed":
            error = record.get("error") or {}
            payload = {
                "scan_id": scan_id, "status": "failed",
                "package_ready": False, "package_length": None,
                "package_sha256": None,
                "error_code": str(error.get("error_code") or "E_PIPELINE_FAILED")[:64],
                "error_message": str(error.get("message") or "analysis failed")[:160],
                "full_scene_ready": False,
            }
            progress.emit(
                "p4_status_polled", scan_id=scan_id,
                status="warn", message="P4 查询 decision-status：failed",
                details={"last_status_returned": "failed"},
            )
            return payload
        try:
            _, raw, package_sha256 = _validate_recorded_decision_package(
                manager.worker_config, manager.storage, record
            )
        except ServiceError as exc:
            payload = {
                "scan_id": scan_id, "status": "failed",
                "package_ready": False, "package_length": None,
                "package_sha256": None, "error_code": exc.code,
                "error_message": exc.message[:160],
                "full_scene_ready": False,
            }
            progress.emit(
                "p4_status_polled", scan_id=scan_id,
                status="warn", message="P4 查询 decision-status：failed",
                details={"last_status_returned": "failed"},
            )
            return payload
        payload = {
            "scan_id": scan_id, "status": "decision_ready",
            "package_ready": True, "package_length": len(raw),
            "package_sha256": package_sha256, "error_code": None,
            "error_message": None,
            "full_scene_ready": bool(record.get("full_scene_ready", False)),
            "full_scene_status": record.get("full_scene_status", "pending"),
            "full_scene_error_code": record.get("full_scene_error_code"),
            "full_scene_error_message": record.get("full_scene_error_message"),
            "visualization_html_path": record.get("visualization_html_path"),
        }
        progress.emit(
            "p4_status_polled", scan_id=scan_id,
            message="P4 查询 decision-status：decision_ready",
            details={"last_status_returned": "decision_ready"},
        )
        return payload

    @app.get("/api/v1/scans/{scan_id}/decision-package")
    async def decision_package(scan_id: str) -> Response:
        progress.emit(
            "p4_package_requested", scan_id=scan_id,
            message="P4 请求 decision-package",
        )
        record = manager.get_by_scan(scan_id)
        if record is None:
            raise ServiceError(
                "E_SCAN_NOT_FOUND", "scan was not found", 404,
                scan_id=scan_id,
            )
        if record["status"] == "failed":
            error = record.get("error") or {}
            raise ServiceError(
                str(error.get("error_code") or "E_PIPELINE_FAILED"),
                "analysis failed", 409, scan_id=scan_id,
            )
        if record["status"] not in {"decision_ready", "completed"}:
            raise ServiceError(
                "E_DECISION_NOT_READY", "decision package is not ready",
                425, scan_id=scan_id,
            )
        _, raw, _ = _validate_recorded_decision_package(
            manager.worker_config, manager.storage, record
        )
        return Response(
            content=raw, media_type="application/json",
            headers={"Content-Length": str(len(raw))},
        )

    @app.get("/vision-console", response_class=HTMLResponse, include_in_schema=False)
    async def vision_console() -> HTMLResponse:
        return HTMLResponse(VISION_CONSOLE_HTML)

    @app.get("/api/v1/vision-console/state")
    async def vision_console_state() -> dict[str, Any]:
        active_user_scan_id, records = manager.observer_context()
        state = progress.snapshot(
            active_user_scan_id=active_user_scan_id,
            records=records,
        )
        state["services"] = {
            "vision": await run_in_threadpool(vision_health),
            "scan": {"online": True, "port": 8030},
            "local_runtime": local_runtime_state(service_config),
        }
        state["performance_guard"] = guard.snapshot()
        state["links"] = {
            "person_follow": "http://127.0.0.1:8000/person-follow-view",
            "vision_docs": "http://127.0.0.1:8000/docs",
            "scan_docs": "/docs",
        }
        state["error_guidance"] = {
            code: {"summary": value[0], "check": value[1]}
            for code, value in ERROR_GUIDANCE.items()
        }
        return state

    @app.get("/api/v1/vision-console/scans")
    async def vision_console_scans() -> dict[str, Any]:
        active_user_scan_id, records = manager.observer_context()
        snapshot = progress.snapshot(
            active_user_scan_id=active_user_scan_id,
            records=records,
        )
        return {"schema_version": "vision_console_scans.v1", "scans": snapshot["scans"]}

    @app.get("/api/v1/vision-console/scans/{scan_id}")
    async def vision_console_scan(scan_id: str) -> dict[str, Any]:
        detail = progress.scan_detail(scan_id)
        if detail is None:
            raise ServiceError("E_SCAN_NOT_FOUND", "scan was not found", 404, scan_id=scan_id)
        return {"schema_version": "vision_console_scan.v1", **detail}

    @app.get("/api/v1/vision-console/scans/{scan_id}/events.jsonl")
    async def vision_console_scan_events(scan_id: str) -> Response:
        detail = progress.scan_detail(scan_id)
        if detail is None:
            raise ServiceError("E_SCAN_NOT_FOUND", "scan was not found", 404, scan_id=scan_id)
        raw = "".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            for event in detail["events"]
        ).encode("utf-8")
        return Response(
            raw, media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{scan_id}.events.jsonl"'},
        )

    @app.get(
        "/api/v1/vision-console/scans/{scan_id}/visualization",
        include_in_schema=False,
    )
    async def vision_console_visualization_redirect(scan_id: str) -> RedirectResponse:
        record = manager.get_by_scan(scan_id)
        path = None if record is None else record.get("visualization_html_path")
        if not path or not Path(path).is_file():
            raise ServiceError(
                "E_FULL_SCENE_MISSING", "visualization is not ready", 404,
                scan_id=scan_id,
            )
        encoded_scan = urllib.parse.quote(scan_id, safe="")
        return RedirectResponse(
            f"/api/v1/vision-console/scans/{encoded_scan}/visualization/",
            status_code=307,
        )

    @app.get(
        "/api/v1/vision-console/scans/{scan_id}/visualization/",
        include_in_schema=False,
    )
    async def vision_console_visualization(scan_id: str) -> Response:
        record = manager.get_by_scan(scan_id)
        path = None if record is None else record.get("visualization_html_path")
        if not path or not Path(path).is_file():
            raise ServiceError(
                "E_FULL_SCENE_MISSING", "visualization is not ready", 404,
                scan_id=scan_id,
            )
        result_dir = manager.storage.result_dir(scan_id)
        raw = Path(path).read_bytes()
        if PORTABLE_VIEWER_MARKER in raw:
            body = raw
            legacy_rewrite_used = "false"
        else:
            body = _visualization_http_html(Path(path), result_dir, scan_id).encode("utf-8")
            legacy_rewrite_used = "true"
        return Response(
            body,
            media_type="text/html",
            headers={
                "Cache-Control": "no-store",
                "X-VisionPC-Legacy-Rewrite": legacy_rewrite_used,
            },
        )

    @app.get(
        "/api/v1/vision-console/scans/{scan_id}/assets/{asset_path:path}",
        include_in_schema=False,
    )
    async def vision_console_asset(
        scan_id: str, asset_path: str, request: Request
    ) -> FileResponse:
        record = manager.get_by_scan(scan_id)
        result_dir = manager.storage.result_dir(scan_id)
        raw_path = bytes(request.scope.get("raw_path") or b"").decode("ascii", "ignore")
        if record is None or not result_dir.is_dir() or "%25" in raw_path.lower():
            raise ServiceError("E_SCAN_NOT_FOUND", "scan asset was not found", 404, scan_id=scan_id)
        candidate = _safe_visualization_asset(result_dir, asset_path)
        if candidate is None:
            raise ServiceError("E_SCAN_NOT_FOUND", "scan asset was not found", 404, scan_id=scan_id)
        return FileResponse(
            candidate,
            media_type="image/png",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get(
        "/api/v1/vision-console/scans/{scan_id}/logs/{frame_id}/source_rgb.png",
        include_in_schema=False,
    )
    async def vision_console_canonical_source_image(
        scan_id: str, frame_id: str
    ) -> FileResponse:
        record = manager.get_by_scan(scan_id)
        result_dir = manager.storage.result_dir(scan_id).resolve()
        if record is None or frame_id not in CANONICAL_FRAME_IDS or not result_dir.is_dir():
            raise ServiceError(
                "E_SCAN_NOT_FOUND", "scan source image was not found", 404,
                scan_id=scan_id,
            )
        frame_dir = (result_dir / "logs" / frame_id).resolve()
        candidate = (frame_dir / "source_rgb.png").resolve()
        if (
            frame_dir.parent != (result_dir / "logs").resolve()
            or candidate.parent != frame_dir
            or not candidate.is_file()
        ):
            raise ServiceError(
                "E_SCAN_NOT_FOUND", "scan source image was not found", 404,
                scan_id=scan_id,
            )
        return FileResponse(
            candidate,
            media_type="image/png",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get(
        "/api/v1/vision-console/scans/{scan_id}/scene-assets/{frame_id}/source.png",
        include_in_schema=False,
    )
    async def vision_console_source_image(scan_id: str, frame_id: str) -> FileResponse:
        record = manager.get_by_scan(scan_id)
        result_dir = manager.storage.result_dir(scan_id)
        if record is None or frame_id not in CANONICAL_FRAME_IDS:
            raise ServiceError(
                "E_SCAN_NOT_FOUND", "scan source image was not found", 404,
                scan_id=scan_id,
            )
        candidate = result_dir / "logs" / frame_id / "source_rgb.png"
        if not candidate.is_file():
            raise ServiceError(
                "E_SCAN_NOT_FOUND", "scan source image was not found", 404,
                scan_id=scan_id,
            )
        return FileResponse(
            candidate,
            media_type="image/png",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get("/api/v1/vision-console/events")
    async def vision_console_events(request: Request) -> StreamingResponse:
        raw_last = request.headers.get("last-event-id")
        try:
            last_id = int(raw_last) if raw_last else None
        except ValueError:
            last_id = None

        async def stream() -> Any:
            for event in progress.events_since(last_id, user_only=True):
                yield (
                    f"id: {event['event_id']}\n"
                    "event: progress\n"
                    f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
                )
            subscriber = progress.subscribe()
            try:
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.to_thread(subscriber.get, True, 15.0)
                    except queue.Empty:
                        yield ": heartbeat\n\n"
                        continue
                    if not progress.event_visible_to_user(event):
                        continue
                    yield (
                        f"id: {event['event_id']}\n"
                        "event: progress\n"
                        f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
                    )
            finally:
                progress.unsubscribe(subscriber)

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
