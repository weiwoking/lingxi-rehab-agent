from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

from deltar_p4_pipeline.scan_service_worker import WorkerConfig, worker_environment
from deltar_p4_pipeline.yolo_runtime_supervisor import YoloRuntimeSupervisor


FAKE_WORKER = r'''
import argparse, json, os, sys
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument("--serve",type=Path,required=True);args=p.parse_args()
startup=json.loads(args.serve.read_text(encoding="utf8"))
identity={}
for task,item in startup["models"].items():
 path=Path(item["path"]); stat=path.stat()
 identity[task]={"path":str(path.resolve()),"size":stat.st_size,"mtime_ns":stat.st_mtime_ns,"sha256":item["sha256"]}
print(json.dumps({"schema_version":"deltar_yolo_runtime_ready.v1","status":"READY","pid":os.getpid(),"timing":{},"model_identity":identity}),flush=True)
for line in sys.stdin:
 command=json.loads(line)
 if command["command"]=="shutdown":
  print(json.dumps({"status":"stopped"}),flush=True);break
 if command["command"]=="job":
  request=command["request"]
  if request.get("test_injection") in {"crash_detect","crash_segment"}: os._exit(97)
  for path in request["output_dirs"].values(): Path(path).mkdir(parents=True,exist_ok=True)
  print(json.dumps({"schema_version":"deltar_yolo_combined_response.v1","status":"success","tasks":{},"runtime":{"pid":os.getpid()}}),flush=True)
'''


def fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "pipeline"
    (root / "profiles").mkdir(parents=True)
    (root / "workers").mkdir()
    models = {}
    for task in ("detect", "segment"):
        path = root / f"{task}.pt"
        path.write_bytes(task.encode("ascii"))
        models[task] = {"task": task, "path": str(path), "sha256": task[0] * 64}
    (root / "profiles" / "stage8b1_yolo_runtime_profile.json").write_text(
        json.dumps(
            {
                "worker_python": sys.executable,
                "device": "cuda:0",
                "imgsz": 640,
                "detect_batch_size": 11,
                "half": False,
            }
        ),
        encoding="utf-8",
    )
    (root / "profiles" / "algorithm_profile.json").write_text(
        json.dumps({"yolo_confidence": 0.2}), encoding="utf-8"
    )
    (root / "profiles" / "model_manifest.json").write_text(
        json.dumps({"models": list(models.values())}), encoding="utf-8"
    )
    (root / "workers" / "yolo_runtime_worker.py").write_text(FAKE_WORKER, encoding="utf-8")
    return root


def request(tmp_path: Path, injection: str | None = None) -> dict[str, object]:
    root = tmp_path / "job"
    return {
        "output_root": str(root),
        "output_dirs": {"detect": str(root / "detect"), "segment": str(root / "segment")},
        "test_injection": injection,
    }


def test_supervisor_restarts_and_retries_once(tmp_path: Path) -> None:
    supervisor = YoloRuntimeSupervisor(fake_root(tmp_path), tmp_path / "data", startup_timeout_s=10, job_timeout_s=10)
    try:
        supervisor.start()
        first_generation = supervisor.generation
        response = supervisor._run_job(request(tmp_path, "crash_once_detect"))
        assert response["status"] == "success"
        assert response["runtime"]["attempt"] == 2
        assert supervisor.generation == first_generation + 1
        assert supervisor.recoveries == 1
    finally:
        supervisor.stop()


def test_supervisor_fails_after_second_runtime_crash(tmp_path: Path) -> None:
    supervisor = YoloRuntimeSupervisor(fake_root(tmp_path), tmp_path / "data", startup_timeout_s=10, job_timeout_s=10)
    try:
        supervisor.start()
        response = supervisor._run_job(request(tmp_path, "crash_detect"))
        assert response["status"] == "rejected"
        assert response["error_code"] == "E_YOLO_RUNTIME_FAILED"
        assert supervisor.state == "FAILED"
    finally:
        supervisor.stop()


def test_broker_authentication_and_worker_environment(tmp_path: Path) -> None:
    supervisor = YoloRuntimeSupervisor(fake_root(tmp_path), tmp_path / "data", startup_timeout_s=10, job_timeout_s=10)
    try:
        supervisor.start()
        host, port_text = supervisor.endpoint.rsplit(":", 1)
        with socket.create_connection((host, int(port_text)), timeout=5) as connection:
            connection.sendall(
                json.dumps(
                    {
                        "schema_version": "deltar_yolo_runtime_broker_request.v1",
                        "token": "wrong",
                        "command": "ping",
                    }
                ).encode("utf-8")
                + b"\n"
            )
            response = json.loads(connection.makefile("rb").readline())
        assert response["status"] == "rejected"
        config = WorkerConfig(
            pipeline_root=tmp_path,
            worker_python=Path(sys.executable),
            profile_bundle=tmp_path,
            yolo_runtime_endpoint=supervisor.endpoint,
            yolo_runtime_token=supervisor.token,
        )
        environment = worker_environment(config)
        assert environment["DELTAR_YOLO_RUNTIME_ENDPOINT"] == supervisor.endpoint
        assert environment["DELTAR_YOLO_RUNTIME_TOKEN"] == supervisor.token
    finally:
        supervisor.stop()
