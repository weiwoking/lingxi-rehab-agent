from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import uvicorn

from deltar_p4_pipeline.scan_service import ServiceConfig, create_app


PHASE6_WINNER_ENV = {
    "DELTAR_PHASE6_LAB": "1",
    "DELTAR_PHASE6_GPU_LOOKAHEAD": "1",
    "DELTAR_PHASE6_STAGE7D_LOOKAHEAD": "1",
    "DELTAR_PHASE6_SOURCE_PNG_PARALLEL": "1",
    "DELTAR_PHASE6_STAGE6_REQUIRED_REGION": "1",
    "DELTAR_PHASE6_STAGE7D_RANSAC_BATCH": "1",
    "DELTAR_PHASE6_STAGE7D_PNG_PAIR_PARALLEL": "1",
}

STARTUP_CANARY_BAD_EXIT = 75
RUNTIME_RECYCLE_EXIT = 76
MAX_NEW_SERVICE_SESSIONS = 2


def activate_phase6_winner() -> None:
    os.environ.update(PHASE6_WINNER_ENV)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Run the DELTAR PC Scan Service")
    value.add_argument("--host", default="127.0.0.1")
    value.add_argument("--port", type=int, default=8030)
    value.add_argument("--data-root", type=Path, default=(ROOT.parent / "_deltar_scan_service_data"))
    value.add_argument("--pipeline-root", type=Path, default=ROOT)
    value.add_argument("--worker-python", type=Path, default=(ROOT.parent / "_deltar_formal" / "envs" / "deltar_py311_torch271_cu128" / "python.exe"))
    value.add_argument("--profile-bundle", type=Path)
    value.add_argument("--max-upload-bytes", type=int, default=6 * 1024 * 1024)
    value.add_argument("--max-result-bytes", type=int, default=256 * 1024)
    value.add_argument("--max-frames", type=int, default=11)
    value.add_argument(
        "--job-timeout-seconds", type=int,
        default=ServiceConfig.job_timeout_seconds,
    )
    value.add_argument("--device", default="cuda:0")
    value.add_argument("--require-local-preflight", action="store_true")
    value.add_argument("--disable-yolo-runtime", action="store_true")
    value.add_argument("--disable-frozen-runtime", action="store_true")
    value.add_argument("--disable-performance-guard", action="store_true")
    value.add_argument(
        "--performance-guard-periodic-interval-seconds", type=float, default=600.0
    )
    value.add_argument("--performance-guard-idle-grace-seconds", type=float, default=5.0)
    value.add_argument("--performance-guard-threshold-ms", type=float, default=20_000.0)
    value.add_argument("--service-child", action="store_true", help=argparse.SUPPRESS)
    value.add_argument("--guard-session-attempt", type=int, default=0, help=argparse.SUPPRESS)
    value.add_argument("--auto-open-visualization", action="store_true")
    value.add_argument("--visualization-timeout-seconds", type=int, default=600)
    return value


def _close_handle(handle: int | None) -> None:
    if handle and os.name == "nt":
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


def _assign_kill_on_close_job(process: subprocess.Popen[bytes]) -> int | None:
    """Put the service session and all workers in a kill-on-parent-close Job."""
    if os.name != "nt":
        return None

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_size_t) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class BASIC_LIMITS(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class EXTENDED_LIMITS(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC_LIMITS),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    limits = EXTENDED_LIMITS()
    limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        ctypes.c_void_p(job), 9, ctypes.byref(limits), ctypes.sizeof(limits)
    ):
        _close_handle(job)
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
    if not kernel32.AssignProcessToJobObject(
        ctypes.c_void_p(job), ctypes.c_void_p(int(process._handle))
    ):
        _close_handle(job)
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
    return int(job)


def _child_command(attempt: int) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        *sys.argv[1:],
        "--service-child",
        "--guard-session-attempt",
        str(attempt),
    ]


def _supervise_service_sessions() -> int:
    # The ordinary first session is recovery attempt 0.  A runtime recycle
    # permits exactly two replacement sessions (attempts 1 and 2).  A service
    # that is already bad on its initial startup gets one bounded replacement.
    attempt = 0
    max_attempt = 1
    while True:
        process = subprocess.Popen(_child_command(attempt), cwd=ROOT)
        job_handle: int | None = None
        try:
            job_handle = _assign_kill_on_close_job(process)
            exit_code = process.wait()
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5.0)
            raise
        finally:
            _close_handle(job_handle)
        if exit_code == RUNTIME_RECYCLE_EXIT:
            attempt = 1
            max_attempt = MAX_NEW_SERVICE_SESSIONS
            continue
        return int(exit_code)


def _run_service_child(args: argparse.Namespace) -> int:
    recycle_requested = threading.Event()
    server_holder: dict[str, uvicorn.Server] = {}

    def request_recycle() -> None:
        recycle_requested.set()
        server = server_holder.get("server")
        if server is not None:
            server.should_exit = True

    print(
        json.dumps(
            {
                "schema_version": "deltar_phase6_winner_activation.v1",
                "status": "active",
                "flags": PHASE6_WINNER_ENV,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    config = ServiceConfig(
        data_root=args.data_root, pipeline_root=args.pipeline_root, worker_python=args.worker_python,
        profile_bundle=args.profile_bundle, max_upload_bytes=args.max_upload_bytes,
        max_result_bytes=args.max_result_bytes, max_frames=args.max_frames,
        job_timeout_seconds=args.job_timeout_seconds, device=args.device,
        require_local_preflight=args.require_local_preflight,
        enable_yolo_runtime=not args.disable_yolo_runtime,
        enable_frozen_runtime=not args.disable_frozen_runtime,
        enable_performance_guard=not args.disable_performance_guard,
        performance_threshold_ms=args.performance_guard_threshold_ms,
        performance_periodic_interval_s=args.performance_guard_periodic_interval_seconds,
        performance_idle_grace_s=args.performance_guard_idle_grace_seconds,
        performance_recovery_attempt=args.guard_session_attempt,
        auto_open_visualization=args.auto_open_visualization,
        visualization_timeout_seconds=args.visualization_timeout_seconds,
    )
    app = create_app(config, request_full_service_recycle=request_recycle)
    server = uvicorn.Server(uvicorn.Config(
        app, host=args.host, port=args.port, access_log=False
    ))
    server_holder["server"] = server
    uvicorn_exit = 0
    try:
        server.run()
    except SystemExit as exc:
        uvicorn_exit = int(exc.code or 0)
    if recycle_requested.is_set():
        return RUNTIME_RECYCLE_EXIT
    return uvicorn_exit


def main() -> int:
    activate_phase6_winner()
    args = parser().parse_args()
    if not args.service_child and not args.disable_performance_guard:
        return _supervise_service_sessions()
    return _run_service_child(args)


if __name__ == "__main__":
    raise SystemExit(main())
