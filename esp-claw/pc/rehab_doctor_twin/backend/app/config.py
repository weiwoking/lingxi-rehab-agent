import os
import sys
from pathlib import Path

APP_VERSION = "0.1.0"
SERVICE_NAME = "lingxi-rehab-doctor-twin-gateway"
DEVICE_ID = "P4_REHAB_01"
PATIENT_ID = "P-0217"
SOURCE_MODE = os.getenv("DOCTOR_SOURCE_MODE", "device").strip().lower() or "device"
SCHEMA_VERSION = "1.0"
BACKEND_HOST = os.getenv("DOCTOR_BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.getenv("DOCTOR_BACKEND_PORT", "8001"))
VISION_BASE_URL = os.getenv("VISION_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
SCORE_BASE_URL = os.getenv("SCORE_BASE_URL", "http://127.0.0.1:8020").rstrip("/")
P4_BASE_URL = os.getenv("P4_BASE_URL", "").rstrip("/")
LIVE_POLL_INTERVAL_SECONDS = max(1.0, float(os.getenv("LIVE_POLL_INTERVAL_SECONDS", "1")))
P4_RUNTIME_POLL_INTERVAL_SECONDS = max(
    0.1, float(os.getenv("P4_RUNTIME_POLL_INTERVAL_SECONDS", "0.1"))
)
P4_DEVICE_OFFLINE_TIMEOUT_SECONDS = max(
    0.5, float(os.getenv("P4_DEVICE_OFFLINE_TIMEOUT_SECONDS", "2"))
)
PC_STREAM_UDP_HOST = os.getenv("PC_STREAM_UDP_HOST", "0.0.0.0")
PC_STREAM_UDP_PORT = int(os.getenv("PC_STREAM_UDP_PORT", "6006"))
PC_STREAM_STALE_SECONDS = max(
    0.5, float(os.getenv("PC_STREAM_STALE_SECONDS", "3.0"))
)
PC_SESSION_WATCH_INTERVAL_SECONDS = max(
    0.25, float(os.getenv("PC_SESSION_WATCH_INTERVAL_SECONDS", "0.5"))
)
PC_AUTO_START_WINDOW_SECONDS = max(
    0.1, float(os.getenv("PC_AUTO_START_WINDOW_SECONDS", "1.0"))
)
PC_AUTO_START_MIN_VALID_ROWS = max(
    1, int(os.getenv("PC_AUTO_START_MIN_VALID_ROWS", "3"))
)
PC_AUTO_STOP_STALE_SECONDS = max(
    0.5, float(os.getenv("PC_AUTO_STOP_STALE_SECONDS", "3.0"))
)
_PC_STREAM_SOURCE_MODE = os.getenv("PC_STREAM_SOURCE_MODE", "udp").strip().lower()
PC_STREAM_SOURCE_MODE = (
    _PC_STREAM_SOURCE_MODE
    if _PC_STREAM_SOURCE_MODE in {"udp", "csv_tail", "csv_replay"}
    else "udp"
)
PC_STREAM_CSV_PATH = os.getenv("PC_STREAM_CSV_PATH", "").strip()
try:
    _PC_STREAM_CSV_REPLAY_SPEED = float(
        os.getenv("PC_STREAM_CSV_REPLAY_SPEED", "1.0")
    )
except ValueError:
    _PC_STREAM_CSV_REPLAY_SPEED = 1.0
PC_STREAM_CSV_REPLAY_SPEED = (
    _PC_STREAM_CSV_REPLAY_SPEED
    if _PC_STREAM_CSV_REPLAY_SPEED in {0.5, 1.0, 2.0}
    else 1.0
)
PC_NODE_STALE_MS = max(0, int(os.getenv("PC_NODE_STALE_MS", "1000")))
TRAINING_MIN_SESSION_SECONDS = max(
    0.0, float(os.getenv("TRAINING_MIN_SESSION_SECONDS", "2.0"))
)
TRAINING_MIN_VALID_ROWS = max(
    1, int(os.getenv("TRAINING_MIN_VALID_ROWS", "20"))
)
TRAINING_SCORE_POLL_INTERVAL_SECONDS = max(
    0.2, float(os.getenv("TRAINING_SCORE_POLL_INTERVAL_SECONDS", "1.0"))
)
TRAINING_SCORE_TIMEOUT_SECONDS = max(
    5.0, float(os.getenv("TRAINING_SCORE_TIMEOUT_SECONDS", "180"))
)
SCORE_FULL_RESULT_WAIT_SECONDS = max(
    0.0, float(os.getenv("SCORE_FULL_RESULT_WAIT_SECONDS", "5.0"))
)
SCORE_FULL_RESULT_POLL_INTERVAL_SECONDS = max(
    0.01, float(os.getenv("SCORE_FULL_RESULT_POLL_INTERVAL_SECONDS", "0.10"))
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PC_ROOT = PROJECT_ROOT.parent
DEMO_DIR = PROJECT_ROOT / "demo"
REPORT_DATA_DIR = PROJECT_ROOT / "backend" / "runtime_reports"
DATA_DIR = PROJECT_ROOT / "backend" / "data"
PATIENTS_FILE = DATA_DIR / "patients.json"
TRAINING_DATA_ROOT = Path(
    os.getenv("REHAB_TRAINING_DATA_ROOT", str(Path(__file__).resolve().parents[5] / "external" / "_rehab_training_data"))
).resolve()
if str(DEFAULT_PC_ROOT) not in sys.path:
    sys.path.insert(0, str(DEFAULT_PC_ROOT))

from rehab_training_data.resolver import TrainingDataResolver

TRAINING_DATA_RESOLVER = TrainingDataResolver(
    training_root=TRAINING_DATA_ROOT,
)
