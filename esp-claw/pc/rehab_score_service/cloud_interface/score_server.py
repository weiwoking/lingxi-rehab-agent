# cloud_interface/score_server.py

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoring_assets import TEMPLATE_ROOT

SERVICE_VERSION_FILE = PROJECT_ROOT / "SERVICE_VERSION"
HEALTHY_TEMPLATE_PATH = TEMPLATE_ROOT / "MHH_healthy_template.json"
LATERAL_TEMPLATE_PATH = TEMPLATE_ROOT / "lateral_transfer_healthy_template_v1.json"
TASK_REGISTRY_PATH = PROJECT_ROOT / "config" / "rehab_task_registry_v1.json"
DEFAULT_TRAINING_DATA_ROOT = Path(__file__).resolve().parents[4] / "external" / "_rehab_training_data"
LISTEN_ADDRESS = os.environ.get("REHAB_SCORE_LISTEN_ADDRESS", "0.0.0.0")
try:
    LISTEN_PORT = int(os.environ.get("REHAB_SCORE_PORT", "8020"))
except ValueError:
    LISTEN_PORT = 8020
TRAINING_DATA_ROOT = Path(
    os.environ.get("REHAB_TRAINING_DATA_ROOT", str(DEFAULT_TRAINING_DATA_ROOT))
).resolve()
RUNTIME_DATA_ROOT = TRAINING_DATA_ROOT
from cloud_interface.cloud_score_api import invalid_score_trial_result, score_trial_json
from cloud_interface.client_network_policy import install_job_client_network_policy
from cloud_interface.dedicated_job_score_adapter import process_dedicated_job_directory
from cloud_interface.legacy_loopback_guard import install_legacy_loopback_guard
from cloud_interface.score_job_api import create_job_router
from cloud_interface.score_job_store import ScoreJobStore
from cloud_interface.score_job_worker import ScoreJobWorker


JOB_STORE = ScoreJobStore(RUNTIME_DATA_ROOT)
JOB_WORKER = ScoreJobWorker(JOB_STORE, process_dedicated_job_directory)


@asynccontextmanager
async def lifespan(application: FastAPI):
    JOB_STORE.ensure_ready()
    recovered = JOB_STORE.recover()
    JOB_WORKER.start()
    for job_id in recovered:
        JOB_WORKER.enqueue(job_id, source="recovery")
    try:
        yield
    finally:
        JOB_WORKER.stop()


app = FastAPI(
    title="Stroke Upper Limb Scoring API",
    description="脑卒中上肢康复动作评分接口",
    version="1.3.0",
    lifespan=lifespan,
)
app.router.routes.extend(create_job_router(JOB_STORE, JOB_WORKER).routes)
install_job_client_network_policy(app)
install_legacy_loopback_guard(app)


class TrialInput(BaseModel):
    patient_id: Optional[str] = "S001"
    session_id: Optional[str] = "test_session"
    Group: Optional[str] = "Stroke"
    SubjectID: Optional[str] = "S001"
    TaskNumber: int
    TrialID: Optional[int] = 1
    FileName: Optional[str] = "cloud_trial"

    WristMeanSpeed_mm_s: float
    NumSpeedPeaks: float
    PauseCount: float
    DrinkWaterTremorDetected: Optional[bool] = None
    DrinkWaterTremorFraction: Optional[float] = None
    DrinkWaterTremorValidWindowCount: Optional[float] = None
    DrinkWaterTremorPositiveWindowCount: Optional[float] = None
    DrinkWaterTremorStatus: Optional[str] = None
    DrinkWaterTremorVersion: Optional[str] = None
    DrinkWaterLFDetected: Optional[bool] = None
    DrinkWaterLFNeutralRoll_deg: Optional[float] = None
    DrinkWaterLFPeakAbsRoll_deg: Optional[float] = None
    DrinkWaterLFMaxDurationOverThreshold_s: Optional[float] = None
    DrinkWaterLFPositiveDuration_s: Optional[float] = None
    DrinkWaterLFNegativeDuration_s: Optional[float] = None
    DrinkWaterLFRollThreshold_deg: Optional[float] = None
    DrinkWaterLFMinDuration_s: Optional[float] = None
    DrinkWaterLFValidSampleCount: Optional[float] = None
    DrinkWaterLFEstimatedRateHz: Optional[float] = None
    DrinkWaterLFStatus: Optional[str] = None
    DrinkWaterLFVersion: Optional[str] = None

    ForearmRotationRange_deg: Optional[float] = None
    ForearmMeanAngularSpeed_deg_s: Optional[float] = None

    UpperArmROM_mm: float
    UpperArmMeanAngularSpeed_deg_s: Optional[float] = None

    TrunkCompensationRatio: float
    TrunkROM_deg: Optional[float] = None

    Extensor_digitorum_RMS: float
    Extensor_digitorum_MAV: float
    Extensor_digitorum_WL_mean: float
    Extensor_digitorum_IEMG: Optional[float] = None
    Extensor_digitorum_SSI: Optional[float] = None

    Biceps_brachii_RMS: float
    Biceps_brachii_MAV: float
    Biceps_brachii_WL_mean: float
    Biceps_brachii_IEMG: Optional[float] = None
    Biceps_brachii_SSI: Optional[float] = None

    Triceps_brachii_RMS: float
    Triceps_brachii_MAV: float
    Triceps_brachii_WL_mean: float
    Triceps_brachii_IEMG: Optional[float] = None
    Triceps_brachii_SSI: Optional[float] = None

    Extensor_carpi_ulnaris_RMS: float
    Extensor_carpi_ulnaris_MAV: float
    Extensor_carpi_ulnaris_WL_mean: float
    Extensor_carpi_ulnaris_IEMG: Optional[float] = None
    Extensor_carpi_ulnaris_SSI: Optional[float] = None

    hardware: Optional[Dict[str, Any]] = None
    data_quality: Optional[Dict[str, Any]] = None
    capture_context: Optional[Dict[str, Any]] = None


class BaselineRowsInput(BaseModel):
    rows: List[Dict[str, Any]]
    patient_id: Optional[str] = None
    session_id: Optional[str] = None


_fatigue_state: Dict[str, Any] = {
    "state": "正常",
    "need_rest": False,
    "penalty": 0,
    "baseline_row_count": 0,
}


def _model_to_dict(model):
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _public_fatigue_state() -> Dict[str, Any]:
    return {
        "state": str(_fatigue_state.get("state", "正常")),
        "need_rest": bool(_fatigue_state.get("need_rest", False)),
        "penalty": 0,
    }


def _service_version() -> str:
    try:
        version = SERVICE_VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return version or "unknown"


def _load_formal_task_registry() -> dict[str, Any]:
    try:
        payload = json.loads(TASK_REGISTRY_PATH.read_text(encoding="utf-8"))
    except OSError:
        return {"ready": False, "tasks": [], "score_profiles": [], "missing": [str(TASK_REGISTRY_PATH)]}
    except json.JSONDecodeError:
        return {"ready": False, "tasks": [], "score_profiles": [], "missing": ["config/rehab_task_registry_v1.json invalid"]}
    tasks = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(tasks, list):
        return {"ready": False, "tasks": [], "score_profiles": [], "missing": ["task registry tasks"]}
    profiles = sorted(
        {
            str(item.get("score_profile"))
            for item in tasks
            if isinstance(item, dict) and item.get("score_profile")
        }
    )
    return {
        "ready": len(tasks) == 5 and profiles == ["drink_water", "lateral_transfer"],
        "tasks": tasks,
        "score_profiles": profiles,
        "missing": [],
    }


def build_health_payload() -> Dict[str, Any]:
    """Return lightweight service identity and asset readiness without scoring."""
    drink_template = HEALTHY_TEMPLATE_PATH.is_file()
    lateral_template = LATERAL_TEMPLATE_PATH.is_file()
    task_registry = _load_formal_task_registry()
    missing = list(task_registry["missing"])
    if not drink_template:
        missing.append("template/MHH_healthy_template.json")
    if not lateral_template:
        missing.append("template/lateral_transfer_healthy_template_v1.json")

    assets_ready = drink_template and lateral_template and bool(task_registry["ready"])
    return {
        "service": "rehab_score_service",
        "service_version": _service_version(),
        "status": "ready" if assets_ready else "degraded",
        "python_required": "3.10.x",
        "listen_host": LISTEN_ADDRESS,
        "listen_port": LISTEN_PORT,
        "runtime_data": {
            "root": str(RUNTIME_DATA_ROOT),
            "ready": JOB_STORE.ready,
            "staging_root": str(JOB_STORE.staging_root),
            "sessions_root": str(JOB_STORE.sessions_root),
        },
        "job_worker": JOB_WORKER.snapshot(),
        "compatibility_endpoints_available": True,
        "assets": {
            "ready": assets_ready,
            "formal_task_registry": bool(task_registry["ready"]),
            "formal_task_count": len(task_registry["tasks"]),
            "score_profiles": task_registry["score_profiles"],
            "drink_water": {
                "ready": drink_template,
                "template": "template/MHH_healthy_template.json",
            },
            "lateral_transfer": {
                "ready": lateral_template,
                "template": "template/lateral_transfer_healthy_template_v1.json",
            },
            "missing": missing,
        },
        "formal_task_count": len(task_registry["tasks"]),
        "score_profiles": task_registry["score_profiles"],
        "capabilities": {
            "feature_scoring": True,
            "persistent_score_jobs": True,
            "score_profiles": task_registry["score_profiles"],
            "fatigue": {
                "available": False,
                "reason": (
                    "compatibility state only; no validated patient-specific "
                    "fatigue inference"
                ),
            },
            "brs": {
                "available": False,
                "reason": "no implemented inference model",
            },
        },
    }


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path == "/score_trial":
        missing = []
        invalid = []
        for error in exc.errors():
            loc = error.get("loc", [])
            field = str(loc[-1]) if loc else "request_body"
            error_type = str(error.get("type", ""))
            if "missing" in error_type:
                missing.append(field)
            else:
                invalid.append(field)
        if missing:
            reason = "missing_required_fields: " + ",".join(missing)
        elif invalid:
            reason = "invalid_numeric_fields: " + ",".join(invalid)
        else:
            reason = "invalid_request_body"
        return JSONResponse(invalid_score_trial_result(reason), status_code=200)
    return JSONResponse({"detail": exc.errors()}, status_code=422)


@app.get("/")
def root():
    return {
        "message": "Stroke Upper Limb Scoring API is running.",
        "usage": "POST /score_trial",
        "outputs": [
            "final_score",
            "subscores",
            "compensation",
            "fatigue",
            "brs",
            "feedback",
        ],
    }


@app.get("/api/v1/score/health")
def score_health():
    return build_health_payload()


@app.get("/demo", response_class=HTMLResponse)
def demo_page():
    return """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>脑卒中上肢康复评分系统 Demo</title>
  <style>
    body { font-family: "Microsoft YaHei", Arial, sans-serif; background: #f4f6f8; margin: 0; padding: 30px; color: #222; }
    .container { max-width: 1100px; margin: auto; }
    .header { background: #2364aa; color: white; padding: 28px; border-radius: 10px; }
    .section { background: white; margin-top: 18px; padding: 22px; border-radius: 8px; box-shadow: 0 4px 14px rgba(0,0,0,0.08); }
    button { background: #2364aa; color: white; border: none; padding: 12px 22px; border-radius: 8px; font-size: 16px; cursor: pointer; }
    .cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-top: 18px; }
    .card { background: #f8fafc; border-left: 5px solid #2364aa; padding: 16px; border-radius: 8px; }
    .card h3 { margin: 0 0 8px 0; font-size: 14px; color: #555; }
    .value { font-size: 26px; font-weight: bold; }
    pre { background: #111827; color: #e5e7eb; padding: 16px; border-radius: 8px; overflow-x: auto; }
    li { margin: 6px 0; }
  </style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>脑卒中上肢康复评分系统 Demo</h1>
    <p>4 IMU + 4 EMG 输入，输出动作质量评分、代偿纠正提醒和可解释评分证据。</p>
  </div>

  <div class="section">
    <button onclick="scoreTrial()">开始评分</button>
    <div id="result"></div>
  </div>

  <div class="section">
    <h2>接口输入示例</h2>
    <pre id="inputJson"></pre>
  </div>
</div>

<script>
const trialData = {
  "patient_id": "S001",
  "session_id": "demo_session_4imu",
  "Group": "Stroke",
  "SubjectID": "S001",
  "TaskNumber": 12,
  "TrialID": 1,
  "FileName": "demo_4imu_trial",
  "WristMeanSpeed_mm_s": 85.2,
  "NumSpeedPeaks": 4,
  "PauseCount": 1,
  "ForearmRotationRange_deg": 42.3,
  "ForearmMeanAngularSpeed_deg_s": 35.1,
  "UpperArmROM_mm": 92.5,
  "UpperArmMeanAngularSpeed_deg_s": 28.4,
  "TrunkCompensationRatio": 0.25,
  "TrunkROM_deg": 12.5,
  "Extensor_digitorum_RMS": 0.000012,
  "Extensor_digitorum_MAV": 0.000009,
  "Extensor_digitorum_WL_mean": 0.23,
  "Biceps_brachii_RMS": 0.000018,
  "Biceps_brachii_MAV": 0.000014,
  "Biceps_brachii_WL_mean": 0.31,
  "Triceps_brachii_RMS": 0.000015,
  "Triceps_brachii_MAV": 0.000011,
  "Triceps_brachii_WL_mean": 0.27,
  "Extensor_carpi_ulnaris_RMS": 0.000013,
  "Extensor_carpi_ulnaris_MAV": 0.000010,
  "Extensor_carpi_ulnaris_WL_mean": 0.25
};

document.getElementById("inputJson").textContent = JSON.stringify(trialData, null, 2);

async function scoreTrial() {
  const resultDiv = document.getElementById("result");
  resultDiv.innerHTML = "<p>正在评分...</p>";

  const response = await fetch("/score_trial", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(trialData)
  });
  const data = await response.json();
  const s = data.subscores || {};
  const compensation = data.compensation || {};

  resultDiv.innerHTML = `
    <div class="cards">
      <div class="card"><h3>动作质量总分</h3><div class="value">${data.final_score ?? "无"}</div></div>
      <div class="card"><h3>评分版本</h3><div class="value">${data.score_version ?? "无"}</div></div>
      <div class="card"><h3>代偿状态</h3><div class="value">${compensation.detected ? "需纠正" : "正常"}</div></div>
      <div class="card"><h3>速度评分</h3><div class="value">${s.speed_score ?? "无"}</div></div>
      <div class="card"><h3>ROM 评分</h3><div class="value">${s.ROM_score ?? "无"}</div></div>
      <div class="card"><h3>平滑性评分</h3><div class="value">${s.smoothness_score ?? "无"}</div></div>
      <div class="card"><h3>协调性评分</h3><div class="value">${s.coordination_score ?? "无"}</div></div>
      <div class="card"><h3>肌肉激活评分</h3><div class="value">${s.muscle_score ?? "无"}</div></div>
    </div>
    <h3>康复反馈</h3>
    <ul>${(data.feedback || []).map(item => `<li>${item}</li>`).join("")}</ul>
    <h3>接口返回 JSON</h3>
    <pre>${JSON.stringify(data, null, 2)}</pre>
  `;
}
</script>
</body>
</html>
    """


@app.post("/score_trial")
def score_trial(trial: TrialInput):
    return score_trial_json(_model_to_dict(trial))


@app.post("/reset_fatigue_state")
def reset_fatigue_state():
    _fatigue_state.update({
        "state": "正常",
        "need_rest": False,
        "penalty": 0,
        "baseline_row_count": 0,
    })
    return {
        "valid": True,
        "fatigue": _public_fatigue_state(),
    }


@app.post("/set_baseline_from_rows")
def set_baseline_from_rows(payload: BaselineRowsInput):
    data = _model_to_dict(payload)
    rows = data.get("rows", [])
    if not isinstance(rows, list) or len(rows) == 0:
        return {
            "valid": False,
            "invalid_reason": "baseline_rows_empty",
        }

    _fatigue_state.update({
        "state": "正常",
        "need_rest": False,
        "penalty": 0,
        "baseline_row_count": len(rows),
    })
    return {
        "valid": True,
        "baseline_row_count": len(rows),
        "fatigue": _public_fatigue_state(),
    }


if __name__ == "__main__":
    uvicorn.run(
        "cloud_interface.score_server:app",
        host=LISTEN_ADDRESS,
        port=LISTEN_PORT,
        workers=1,
    )
