from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scoring_assets import CALIBRATION_SESSIONS_ROOT, DERIVED_ROOT as ASSET_DERIVED_ROOT, TEMPLATE_ROOT


MOTION_FAMILY = "lateral_transfer"
SENSOR_MAPPING_VERSION = "rehab_sensor_mapping_20260806"

SEGMENTATION_VERSION = "lateral_transfer_segmentation_drink_reuse_v1"
SEGMENTATION_CORE_VERSION = "drink_hybrid_segmentation_v2_1_1"
TEMPLATE_VERSION = "lateral_transfer_healthy_template_v1"
SESSION_SCORING_VERSION = "lateral_transfer_session_scoring_v1_1_reasonable_speed"
LF_VERSION = "drink_water_lf_v2"
TREMOR_VERSION = "drink_water_tremor_v1_1"

SERVICE_ROOT = Path(__file__).resolve().parents[1]
SESSION_ROOT = CALIBRATION_SESSIONS_ROOT
DERIVED_ROOT = ASSET_DERIVED_ROOT / "lateral_transfer_v1"
TEMPLATE_PATH = TEMPLATE_ROOT / "lateral_transfer_healthy_template_v1.json"

IMU_MAP = {
    "upperarm": "imu1",
    "trunk": "imu2",
    "forearm": "imu3",
    "wrist": "imu4",
}

EMG_MAP = {
    "emg1": "Extensor_digitorum",
    "emg2": "Biceps_brachii",
    "emg3": "Triceps_brachii",
    "emg4": "Extensor_carpi_ulnaris",
}


@dataclass(frozen=True)
class LFParameters:
    axis: str = "roll"
    neutral_window_s: float = 0.75
    median_window_s: float = 0.50
    threshold_deg: float = 10.0
    min_duration_s: float = 1.0
    max_gap_factor: float = 3.0


@dataclass(frozen=True)
class TremorParameters:
    gyro_source: str = "imu4"
    frequency_band_hz: tuple[float, float] = (3.5, 6.0)
    window_seconds: float = 2.0
    step_seconds: float = 0.5
    band_ratio_threshold: float = 0.18
    band_rms_threshold: float = 5.0
    fraction_threshold: float = 0.35
    max_gap_factor: float = 3.0


LF = LFParameters()
TREMOR = TremorParameters()

LF_EXTRA_PENALTY = 10.0
TREMOR_PENALTY = 15.0

EXPECTED_SESSIONS: dict[str, list[str]] = {
    **{f"20260807_TASK{number:03d}": ["normal"] for number in range(24, 40)},
    **{f"20260807_TASK{number:03d}": ["normal", "normal", "normal"] for number in range(40, 44)},
    "20260807_TASK045": ["normal", "normal", "normal", "normal", "normal"],
    "20260807_TASK046": ["normal", "normal", "normal", "normal", "normal"],
    **{
        f"20260807_TASK{number:03d}": ["normal", "lf", "normal"]
        for number in (47, 48, 49, 50, 52, 53)
    },
    **{f"20260807_TASK{number:03d}": ["tremor"] for number in range(54, 59)},
}

EXCLUDED_TASK_IDS = {"20260807_TASK036", "20260807_TASK039"}


def expected_session_count() -> int:
    return len(EXPECTED_SESSIONS)


def expected_rep_count() -> int:
    return sum(len(labels) for labels in EXPECTED_SESSIONS.values())


def expected_label_counts() -> dict[str, int]:
    counts = {"normal": 0, "lf": 0, "tremor": 0}
    for labels in EXPECTED_SESSIONS.values():
        for label in labels:
            counts[label] = counts.get(label, 0) + 1
    return counts


def expected_source_path(session_id: str) -> Path:
    return SESSION_ROOT / session_id / "raw_session.csv"
