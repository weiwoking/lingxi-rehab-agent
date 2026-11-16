from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from deltar_p4_pipeline.frozen_runtime_contracts import ScanJobRequest
from deltar_p4_pipeline.frozen_runtime_supervisor import FrozenScanRuntimeSupervisor


FAKE_WORKER = r'''
import argparse,json,os,sys
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument("--serve",type=Path,required=True); a=p.parse_args()
c=json.loads(a.serve.read_text(encoding="utf-8")); marker=Path(c["profile_bundle"])/"marker.json"; s=marker.stat()
def emit(v): print(json.dumps(v),flush=True)
emit({"schema_version":"deltar_frozen_scan_runtime_ready.v1","status":"READY","pid":os.getpid(),"generation":c["generation"],"timing":{"runtime_ready_total_ms":1},"runtime_identity":{"test":True},"identity_hints":{str(marker.resolve()):{"size":s.st_size,"mtime_ns":s.st_mtime_ns}},"gpu":{}})
for line in sys.stdin:
    command=json.loads(line)
    if command["command"]=="shutdown": emit({"schema_version":"deltar_frozen_scan_runtime_response.v1","status":"stopped"}); break
    if command["command"]=="ping": emit({"schema_version":"deltar_frozen_scan_runtime_response.v1","status":"pong","identity_unchanged":True,"generation":c["generation"]}); continue
    if command.get("test_injection")=="crash": os._exit(86)
    request=command["request"]
    if request.get("cancel_path") and Path(request["cancel_path"]).is_file():
        emit({"schema_version":"deltar_frozen_scan_runtime_response.v1","status":"aborted","reason":"ABORTED_BY_REAL_TRAFFIC","generation":c["generation"]}); continue
    output=Path(request["output_dir"]); output.mkdir(parents=True); (output/"complete.txt").write_text("ok",encoding="utf-8")
    emit({"schema_version":"deltar_frozen_scan_runtime_response.v1","status":"success","generation":c["generation"],"job_wall_ms":1,"job_processing_ms":1,"job_id":"job_test","scan_id":"scan_test","canonical_package":{"schema_version":"stage8b4a_p4_decision_package.v1","filename":"stage8b4a_p4_decision_package.v1.json","scan_id":"scan_test","generation":c["generation"],"bytes":1,"sha256":"0"*64,"object_count":0}})
'''


def make_supervisor(tmp_path: Path) -> tuple[FrozenScanRuntimeSupervisor, ScanJobRequest]:
    root = tmp_path / "pipeline"
    (root / "workers").mkdir(parents=True)
    (root / "workers" / "frozen_scan_runtime_worker.py").write_text(FAKE_WORKER, encoding="utf-8")
    profiles = root / "profiles"
    profiles.mkdir()
    (profiles / "marker.json").write_text("{}\n", encoding="utf-8")
    scan = tmp_path / "scan"
    scan.mkdir()
    request = ScanJobRequest(scan, tmp_path / "formal", profiles)
    supervisor = FrozenScanRuntimeSupervisor(
        root, tmp_path / "data", Path(sys.executable), profiles,
        startup_timeout_s=10, job_timeout_s=10, testing=True,
    )
    return supervisor, request


def wait_generation(supervisor: FrozenScanRuntimeSupervisor, generation: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if supervisor.generation >= generation and supervisor.status_payload()["ready"]:
            return
        time.sleep(0.05)
    raise AssertionError(supervisor.status_payload())


def test_phase6_source_png_flag_is_allowlisted(
    tmp_path: Path, monkeypatch
) -> None:
    supervisor, _ = make_supervisor(tmp_path)
    monkeypatch.setenv("DELTAR_PHASE6_SOURCE_PNG_PARALLEL", "1")

    assert supervisor._environment()["DELTAR_PHASE6_SOURCE_PNG_PARALLEL"] == "1"


def test_phase6_stage6_required_region_flag_is_allowlisted(
    tmp_path: Path, monkeypatch
) -> None:
    supervisor, _ = make_supervisor(tmp_path)
    monkeypatch.setenv("DELTAR_PHASE6_STAGE6_REQUIRED_REGION", "1")

    assert supervisor._environment()["DELTAR_PHASE6_STAGE6_REQUIRED_REGION"] == "1"


def test_phase6_stage7d_ransac_batch_flag_is_allowlisted(
    tmp_path: Path, monkeypatch
) -> None:
    supervisor, _ = make_supervisor(tmp_path)
    monkeypatch.setenv("DELTAR_PHASE6_STAGE7D_RANSAC_BATCH", "1")

    assert supervisor._environment()["DELTAR_PHASE6_STAGE7D_RANSAC_BATCH"] == "1"


def test_phase6_stage7d_png_pair_flag_is_allowlisted(
    tmp_path: Path, monkeypatch
) -> None:
    supervisor, _ = make_supervisor(tmp_path)
    monkeypatch.setenv("DELTAR_PHASE6_STAGE7D_PNG_PAIR_PARALLEL", "1")

    assert supervisor._environment()["DELTAR_PHASE6_STAGE7D_PNG_PAIR_PARALLEL"] == "1"


def test_runtime_reuses_one_worker_and_promotes_only_complete_attempt(tmp_path: Path) -> None:
    supervisor, request = make_supervisor(tmp_path)
    try:
        supervisor.start()
        pid = supervisor.worker_pid
        response = supervisor.run_job(request)
        assert response["status"] == "success"
        assert response["runtime"]["attempt"] == 1
        assert supervisor.worker_pid == pid
        assert supervisor.generation == 1
        assert (request.output_dir / "complete.txt").read_text(encoding="utf-8") == "ok"
    finally:
        supervisor.stop()
    assert supervisor.process is None


def test_cooperative_abort_preserves_worker_generation_and_publishes_nothing(tmp_path: Path) -> None:
    supervisor, request = make_supervisor(tmp_path)
    cancel = tmp_path / "cancel.requested"
    cancel.write_text("cancel\n", encoding="ascii")
    request = ScanJobRequest(**{**request.__dict__, "cancel_path": cancel})
    try:
        supervisor.start()
        pid = supervisor.worker_pid
        generation = supervisor.generation
        response = supervisor.run_job(request)
        assert response["status"] == "aborted"
        assert supervisor.worker_pid == pid
        assert supervisor.generation == generation
        assert supervisor.recoveries == 0
        assert not request.output_dir.exists()
    finally:
        supervisor.stop()


def test_idle_kill_recovers_with_new_generation(tmp_path: Path) -> None:
    supervisor, request = make_supervisor(tmp_path)
    try:
        supervisor.start()
        assert supervisor.worker_pid is not None
        os.kill(supervisor.worker_pid, 9)
        wait_generation(supervisor, 2)
        assert supervisor.run_job(request)["status"] == "success"
    finally:
        supervisor.stop()


def test_job_crash_once_restarts_and_retries_same_frozen_request(tmp_path: Path) -> None:
    supervisor, request = make_supervisor(tmp_path)
    try:
        supervisor.start()
        response = supervisor.run_job(request, test_injection="crash_once")
        assert response["status"] == "success"
        assert response["runtime"]["attempt"] == 2
        assert response["runtime"]["generation"] == 2
        assert (request.output_dir / "complete.txt").is_file()
    finally:
        supervisor.stop()


def test_job_crash_twice_fails_without_formal_publication(tmp_path: Path) -> None:
    supervisor, request = make_supervisor(tmp_path)
    try:
        supervisor.start()
        response = supervisor.run_job(request, test_injection="crash_twice")
        assert response["status"] == "rejected"
        assert response["error_code"] == "E_FROZEN_RUNTIME_FAILED"
        assert not request.output_dir.exists()
        next_request = ScanJobRequest(request.scan_dir, tmp_path / "formal_after_failure", request.profile_dir)
        assert supervisor.run_job(next_request)["status"] == "success"
        assert (next_request.output_dir / "complete.txt").is_file()
    finally:
        supervisor.stop()


def test_identity_hint_change_reloads_and_increments_generation(tmp_path: Path) -> None:
    supervisor, request = make_supervisor(tmp_path)
    try:
        supervisor.start()
        marker = supervisor.profile_bundle / "marker.json"
        stat = marker.stat()
        os.utime(marker, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        response = supervisor.run_job(request)
        assert response["status"] == "success"
        assert response["runtime"]["generation"] == 2
        assert supervisor.recoveries == 1
    finally:
        supervisor.stop()
