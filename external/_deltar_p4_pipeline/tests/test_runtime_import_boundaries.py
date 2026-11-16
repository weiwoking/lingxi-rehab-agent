from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import threading
from pathlib import Path

from fastapi.testclient import TestClient

import deltar_p4_pipeline.scan_service as scan_service
from deltar_p4_pipeline.geometry_contracts import validate_geometry_document
from deltar_p4_pipeline.scan_profiles import load_profile_bundle
from deltar_p4_pipeline.performance_guard import CanaryResult


ROOT = Path(__file__).resolve().parents[1]
PARENT_PYTHON = Path(r"C:\ec\_conda_envs\vision_interpreter\python.exe")


def test_parent_interpreter_imports_service_without_worker_heavy_modules() -> None:
    probe = r'''
import json
import sys
from pathlib import Path
from deltar_p4_pipeline.scan_service import ServiceConfig, create_app

app = create_app(ServiceConfig(
    data_root=Path.cwd() / "tmp" / "parent_import_probe",
    enable_yolo_runtime=False,
    enable_frozen_runtime=False,
    enable_performance_guard=False,
))
forbidden = (
    "scipy", "torch", "deltar_p4_pipeline.geometry_v2",
    "deltar_p4_pipeline.all_object_geometry",
    "deltar_p4_pipeline.stage8b4a_package",
    "deltar_p4_pipeline.scan_job", "deltar_p4_pipeline.deltar_bridge",
    "deltar_p4_pipeline.formal_deltar_d",
    "deltar_p4_pipeline.frozen_runtime_resources",
)
loaded = sorted(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
)
print(json.dumps({"title": app.title, "loaded": loaded}))
raise SystemExit(bool(loaded))
'''
    environment = os.environ.copy()
    environment.update(
        PYTHONPATH=str((ROOT / "src").resolve()),
        PYTHONNOUSERSITE="1",
        PYTHONUTF8="1",
    )
    completed = subprocess.run(
        [str(PARENT_PYTHON), "-c", probe],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["loaded"] == []


def test_geometry_contract_validation_is_estimator_independent() -> None:
    module_names_before = set(sys.modules)
    document = {
        "schema_version": "stage8b_all_object_geometry.v1",
        "frame_count": 11,
        "frames": [],
    }
    assert validate_geometry_document(document) is document
    newly_loaded = set(sys.modules) - module_names_before
    assert not any("geometry_v2" in name for name in newly_loaded)
    assert not any(name == "scipy" or name.startswith("scipy.") for name in newly_loaded)


def test_external_tof_identity_is_not_replaced_by_internal_stage2_authority() -> None:
    bundle = load_profile_bundle(ROOT / "profiles", validate_model_hashes=False)
    assert bundle.ids["tof_profile_id"] == "tof_vl53l5cx_orientation_identity_v1"
    assert bundle.ids["calibration_profile_id"] == "calibration_unverified_stage6_v1"
    assert bundle.calibration["profile_id"] == "formal_stage2_frame_metadata_20260818"


def test_worker_start_failure_keeps_http_health_diagnostic(monkeypatch, tmp_path: Path) -> None:
    class FailedYoloRuntime:
        def __init__(self, *_args, **_kwargs):
            self.endpoint = ""
            self.token = ""

        def start(self) -> None:
            raise RuntimeError("synthetic worker environment failure")

        def stop(self) -> None:
            return None

        def status_payload(self) -> dict[str, object]:
            return {
                "schema_version": "deltar_yolo_runtime_status.v1",
                "state": "FAILED",
                "ready": False,
            }

    monkeypatch.setattr(scan_service, "YoloRuntimeSupervisor", FailedYoloRuntime)
    config = scan_service.ServiceConfig(
        data_root=tmp_path / "data",
        pipeline_root=ROOT,
        worker_python=Path(sys.executable),
        profile_bundle=ROOT / "profiles",
        require_local_preflight=False,
        enable_yolo_runtime=True,
        enable_frozen_runtime=True,
        enable_performance_guard=True,
    )
    with TestClient(scan_service.create_app(config)) as client:
        response = client.get("/api/v1/scan/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["service_alive"] is True
        assert payload["status"] == "runtime_not_ready"
        assert payload["runtime_bootstrap"]["state"] == "FAILED"
        assert payload["runtime_bootstrap"]["errors"][0]["component"] == "yolo_runtime"

        rejected = client.post("/api/v1/scan/jobs?mode=full_pipeline")
        assert rejected.status_code == 503
        assert rejected.json()["error_code"] == "E_WORKER_NOT_READY"


def test_functional_ready_slow_canary_is_operational_with_warning(
    monkeypatch, tmp_path: Path
) -> None:
    canary_started = threading.Event()

    class ReadyYoloRuntime:
        def __init__(self, *_args, **_kwargs):
            self.endpoint = "127.0.0.1:1"
            self.token = "test"

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def status_payload(self) -> dict[str, object]:
            return {"state": "READY", "ready": True, "process_alive": True}

    class ReadyFrozenRuntime:
        def __init__(self, *_args, **_kwargs):
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def status_payload(self) -> dict[str, object]:
            if canary_started.is_set():
                return {"state": "BUSY", "ready": False, "process_alive": True}
            return {"state": "READY", "ready": True, "process_alive": True}

    monkeypatch.setattr(scan_service, "YoloRuntimeSupervisor", ReadyYoloRuntime)
    monkeypatch.setattr(scan_service, "FrozenScanRuntimeSupervisor", ReadyFrozenRuntime)
    canary_release = threading.Event()

    def slow_canary(_source: str, cancel: threading.Event) -> CanaryResult:
        canary_started.set()
        canary_release.wait(timeout=1.0)
        if cancel.is_set():
            return CanaryResult("aborted_by_real_traffic")
        return CanaryResult("success", wall_ms=41_520.8, exact_pass=True)

    config = scan_service.ServiceConfig(
        data_root=tmp_path / "data",
        pipeline_root=ROOT,
        worker_python=Path(sys.executable),
        profile_bundle=ROOT / "profiles",
        enable_yolo_runtime=True,
        enable_frozen_runtime=True,
        enable_performance_guard=True,
    )
    with TestClient(scan_service.create_app(config, canary_runner=slow_canary)) as client:
        checking = client.get("/api/v1/scan/health").json()
        assert checking["status"] == "ok"
        assert checking["runtime_bootstrap"]["state"] == "READY"
        assert checking["performance_guard"]["status"] == "startup_check"
        assert checking["frozen_scan_runtime"]["state"] == "BUSY"
        assert checking["frozen_scan_runtime"]["operational_ready"] is True
        canary_release.set()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            degraded = client.get("/api/v1/scan/health").json()
            if degraded["performance_guard"]["status"] == "degraded":
                break
            time.sleep(0.005)
        assert degraded["status"] == "ok"
        assert degraded["performance_guard"]["ready"] is False
        assert "performance_canary_slow" in degraded["warnings"]
        assert degraded["runtime_bootstrap"]["state"] == "READY"
        assert degraded["runtime_bootstrap"]["errors"] == []
