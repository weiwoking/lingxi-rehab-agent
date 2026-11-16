import hashlib
import json
from pathlib import Path

from cloud_interface.score_job_store import CSV_HEADER, ScoreJobStore
from cloud_interface.score_job_worker import ScoreJobWorker


SESSION_ID = "20260714_TASK002"
CSV_NAME = "DATA_SEQ0000000001.csv"


def task_bytes(start_seq=1, end_seq=2, task_name="患侧手臂前抬训练"):
    return json.dumps(
        {
            "task_name": task_name,
            "csv_file": CSV_NAME,
            "start_time": "2026-07-14T16:24:00+08:00",
            "end_time": "2026-07-14T16:25:14+08:00",
            "start_log_seq": start_seq,
            "end_log_seq": end_seq,
            "end_reason": "user_stop",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def csv_bytes(start_seq=1, end_seq=2, header=CSV_HEADER):
    column_count = len(header.split(","))
    rows = []
    for seq in (start_seq, end_seq):
        rows.append(",".join([str(seq)] + ["0"] * (column_count - 1)))
    return (header + "\r\n" + "\r\n".join(rows) + "\r\n").encode("ascii")


def full_result(
    session_id=SESSION_ID,
    task_name="患侧手臂前抬训练",
    task_number=12,
    target_repetition_count=10,
):
    return {
        "schema_version": "cloud_session_result_v3",
        "session_id": session_id,
        "task_name": task_name,
        "task_number": task_number,
        "valid": True,
        "invalid_reason": None,
        "repetition_count": 2,
        "target_repetition_count": target_repetition_count,
        "summary": {
            "average_score": 44.42,
            "max_score": 48.15,
            "min_score": 40.69,
            "consistency_score": 98.05,
            "compensation_action_count": 0,
        },
        "data_quality": {"level": "use_with_caution", "warnings": []},
        "score_version": "v3.0",
        "model_version": "mock_dedicated_score_profile_v1",
        "actions": [
            {
                "index": 1,
                "valid": True,
                "invalid_reason": None,
                "final_score": 40.69,
                "subscores": {
                    "speed_score": 10.0,
                    "ROM_score": 8.0,
                    "smoothness_score": 9.0,
                    "coordination_score": 6.0,
                    "muscle_score": 7.69,
                },
                "compensation": {"detected": False, "types": []},
                "feedback": [],
            },
            {
                "index": 2,
                "valid": True,
                "invalid_reason": None,
                "final_score": 48.15,
                "subscores": {
                    "speed_score": 12.0,
                    "ROM_score": 9.0,
                    "smoothness_score": 10.0,
                    "coordination_score": 7.0,
                    "muscle_score": 10.15,
                },
                "compensation": {"detected": False, "types": []},
                "feedback": ["ok"],
            },
        ],
        "debug_actions": [{"trajectory": [1, 2, 3]}],
        "curve_data": {"points": [1, 2]},
        "comparisons": [1],
    }


def mock_scorer(**kwargs):
    output = Path(kwargs["output_dir"])
    output.mkdir(parents=True)
    task = json.loads((Path(kwargs["task_directory"]) / "task.json").read_text(encoding="utf-8"))
    payload = full_result(
        kwargs.get("session_id") or Path(kwargs["task_directory"]).name,
        task_name=task["task_name"],
    )
    (output / "score_result.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    (output / "session_score_curve.png").write_bytes(b"png")
    (output / "trajectories").mkdir()
    return payload


def make_api(runtime_root, scorer=mock_scorer):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cloud_interface.score_job_api import create_job_router

    store = ScoreJobStore(Path(runtime_root))
    store.ensure_ready()
    worker = ScoreJobWorker(store, scorer)
    app = FastAPI()
    app.router.routes.extend(create_job_router(store, worker).routes)
    return TestClient(app), store, worker


def reservation_headers(csv_data):
    return {
        "content-type": "application/json",
        "x-session-id": SESSION_ID,
        "x-csv-size": str(len(csv_data)),
        "x-csv-sha256": hashlib.sha256(csv_data).hexdigest(),
    }


def reserve_and_upload(client, csv_data=None, task_data=None):
    csv_data = csv_data or csv_bytes()
    task_data = task_data or task_bytes()
    create = client.post(
        "/api/v1/score/jobs", content=task_data, headers=reservation_headers(csv_data)
    )
    upload = client.put(
        f"/api/v1/score/jobs/{SESSION_ID}/csv",
        content=csv_data,
        headers={"content-type": "text/csv"},
    )
    return create, upload
