"""Offline per-repetition compensation feature diagnosis for drink-water V2.1.2.

This tool is intentionally read-only for source data, scoring templates, and
formal runtime artifacts. It produces diagnostic tables that explain why the
current forward-trunk compensation detector does or does not fire for each
exported repetition.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from scoring_assets import DERIVED_ROOT as ASSET_DERIVED_ROOT, STANDARD_ROOT


DIAGNOSIS_VERSION = "drink_water_compensation_diagnosis_v1_1"
BASELINE_VERSION = "drink_water_v2.1.2"
SEGMENTATION_VERSION = "drink_hybrid_segmentation_v2_1"
SENSOR_MAPPING_VERSION = "rehab_sensor_mapping_20260806"

DERIVED_ROOT = ASSET_DERIVED_ROOT / "drink_water_v1"
DEFAULT_REP_ROOT = DERIVED_ROOT / "extracted_repetitions"
DEFAULT_BASELINE_DIR = DERIVED_ROOT / "baseline_v2_1_2_sensor_mapping_fixed"
DEFAULT_SESSION_DIR = DERIVED_ROOT / "session_scoring_v2_1_2" / "USER_V212_MIXED_20260806_115813"
DEFAULT_OUTPUT_DIR = DERIVED_ROOT / "compensation_feature_diagnosis_v1_1"


def discover_default_source_root() -> Path:
    if STANDARD_ROOT.exists():
        matches = sorted(path for path in STANDARD_ROOT.iterdir() if path.is_dir() and "20260805" in path.name)
        if matches:
            return matches[0]
    return STANDARD_ROOT / "喝水标准动作数据集_裁剪版_校正版_20260805"


DEFAULT_SOURCE_ROOT = discover_default_source_root()

if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from cloud_interface.raw_log_to_trial import (  # noqa: E402
    build_trial_from_raw_log,
    read_fused_csv,
    validate_fused_scoring_input,
)
from cloud_interface.cloud_score_api import json_to_score_row  # noqa: E402
from feature_extractors.compensation_detector import detect_compensation_4imu_4emg  # noqa: E402
from feature_extractors.extract_features import (  # noqa: E402
    MUSCLES,
    extract_emg_features,
    extract_forearm_features,
    extract_trunk_features,
    extract_upperarm_features,
    extract_wrist_features,
)


SENSOR_MAPPING = {
    "imu1": "upper_arm",
    "imu2": "trunk_waist_back",
    "imu3": "forearm",
    "imu4": "wrist",
    "emg1": "finger_extensors",
    "emg2": "biceps",
    "emg3": "triceps",
    "emg4": "extensor_carpi_ulnaris",
}

MIXED_EXPECTED_SCORES = {
    1: 75.04,
    2: 74.02,
    3: 86.20,
}

SUBSCORE_FIELDS = [
    "speed_score",
    "ROM_score",
    "smoothness_score",
    "coordination_score",
    "muscle_score",
]

TRIAL_FEATURE_MAP = {
    "WristMeanSpeed": "WristMeanSpeed_mm_s",
    "WristMotionSpeedIndex": "WristMotionSpeedIndex",
    "NumSpeedPeaks": "NumSpeedPeaks",
    "PauseCount": "PauseCount",
    "ForearmRotation": "ForearmRotationRange_deg",
    "ForearmMeanAngularSpeed": "ForearmMeanAngularSpeed_deg_s",
    "UpperArmROM": "UpperArmROM_mm",
    "UpperArmMeanAngularSpeed": "UpperArmMeanAngularSpeed_deg_s",
    "TrunkCompensationRatio": "TrunkCompensationRatio",
    "TrunkROM": "TrunkROM_deg",
}

SCORING_FEATURE_FIELDS = list(TRIAL_FEATURE_MAP.keys())

EMG_TRIAL_FEATURES = [
    f"{muscle}_{suffix}"
    for muscle in MUSCLES
    for suffix in ("RMS", "MAV", "WL_mean", "IEMG", "SSI", "Peak")
]

DIAGNOSTIC_FEATURE_SOURCES = {
    **TRIAL_FEATURE_MAP,
    **{field: field for field in EMG_TRIAL_FEATURES},
}

REQUIRED_TRIAL_DIAGNOSTIC_FIELDS = [
    *TRIAL_FEATURE_MAP.values(),
    *EMG_TRIAL_FEATURES,
]

IMU2_DIAGNOSTIC_COLUMNS = [
    "imu2_roll",
    "imu2_pitch",
    "imu2_yaw",
    "imu2_gx",
    "imu2_gy",
    "imu2_gz",
]

IMU2_DISTRIBUTION_FIELDS = [
    "imu2_pitch_start_median",
    "imu2_pitch_end_median",
    "imu2_pitch_min",
    "imu2_pitch_max",
    "imu2_pitch_range",
    "imu2_pitch_mean",
    "imu2_pitch_median",
    "imu2_pitch_p05",
    "imu2_pitch_p95",
    "imu2_roll_range",
    "imu2_yaw_range",
    "imu2_gyro_norm_mean",
    "imu2_gyro_norm_max",
    "imu2_gyro_norm_p95",
    "imu2_pitch_positive_excursion",
    "imu2_pitch_negative_excursion",
    "imu2_pitch_absolute_excursion",
    "imu2_positive_excursion_duration_ms",
    "imu2_negative_excursion_duration_ms",
    "imu2_absolute_excursion_duration_ms",
    "imu2_absolute_excursion_ratio",
    "imu2_absolute_excursion_peak_time_ms",
]

BOUNDARY_DISTRIBUTION_FIELDS = [
    "first_500ms_motion_mean",
    "last_500ms_motion_mean",
    "first_500ms_motion_level",
    "last_500ms_motion_level",
    "first_500ms_static_ratio",
    "last_500ms_static_ratio",
    "candidate_start_index",
    "candidate_end_index",
    "rep_start_index",
    "rep_end_index",
    "start_boundary_margin_rows",
    "end_boundary_margin_rows",
    "pre_boundary_margin_rows",
    "post_boundary_margin_rows",
]

DETECTOR_CONTRACT = {
    "detector_function": "feature_extractors.compensation_detector.detect_compensation_4imu_4emg",
    "diagnosis_version": DIAGNOSIS_VERSION,
    "has_compensation_rule": "len(compensation_types) > 0",
    "compensation_type_branches": [
        {
            "type": "LF",
            "input_feature": "TrunkForwardLean_deg",
            "threshold": 15.0,
            "operator": ">=",
            "affects_has_compensation": True,
        },
        {
            "type": "TR",
            "input_feature": "TrunkRotation_deg",
            "threshold": 15.0,
            "operator": ">=",
            "affects_has_compensation": True,
        },
    ],
    "quality_only_branches": [
        "upper_arm_rom_below_template_mean_minus_1_5_std",
        "forearm_rotation_outside_template_mean_plus_minus_2_std",
        "wrist_speed_peaks_at_least_8",
        "wrist_pause_count_at_least_3",
        "emg_rms_z_score_greater_than_2",
    ],
    "static_contract_note": (
        "Only LF/TR branches append compensation types. Other branches create "
        "quality findings and do not make compensation.detected true."
    ),
}


@dataclass(frozen=True)
class RepRecord:
    source_csv: str
    source_filename: str
    source_relative_path: str
    source_absolute_path: Path
    source_file_size_bytes: int
    expected_label: str
    rep_index: int
    rep_csv: Path
    metadata_json: Path | None
    source_sha256: str
    manifest_entry: dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(data), ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        ordered: list[str] = []
        for row in rows:
            for key in row:
                if key not in ordered:
                    ordered.append(key)
        fieldnames = ordered
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: serialize_cell(row.get(key)) for key in fieldnames})


def serialize_cell(value: Any) -> Any:
    safe = json_safe(value)
    if isinstance(safe, (dict, list)):
        return json.dumps(safe, ensure_ascii=False, sort_keys=True)
    if safe is None:
        return ""
    return safe


def load_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some historical session artifacts contain raw Windows backslashes in
        # paths. Read them leniently for diagnosis without rewriting inputs.
        sanitized = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", text)
        return json.loads(sanitized)


def load_task12_template(path: Path) -> dict[str, Any]:
    template = load_json(path)
    if set(template.keys()) == {"12"}:
        task = template["12"]
    elif "12" in template:
        task = template["12"]
    else:
        raise ValueError("missing_task_12_template")
    if not isinstance(task, dict):
        raise ValueError("invalid_task_12_template")
    baseline_version = task.get("baseline_version") or template.get("baseline_version")
    sensor_mapping_version = task.get("sensor_mapping_version") or template.get("sensor_mapping_version")
    if baseline_version != BASELINE_VERSION:
        raise ValueError(f"invalid_baseline_version:{baseline_version}")
    if sensor_mapping_version != SENSOR_MAPPING_VERSION:
        raise ValueError(f"invalid_sensor_mapping_version:{sensor_mapping_version}")
    required = [
        "ROM_mean",
        "ROM_std",
        "forearm_rotation_mean",
        "forearm_rotation_std",
        *[f"{muscle}_RMS_mean" for muscle in MUSCLES],
        *[f"{muscle}_RMS_std" for muscle in MUSCLES],
    ]
    missing = [field for field in required if field not in task]
    if missing:
        raise ValueError(f"missing_task_12_detector_template_fields:{','.join(missing)}")
    return task


def validate_output_dir(output_dir: Path) -> Path:
    resolved = output_dir.resolve()
    derived = DERIVED_ROOT.resolve()
    if resolved.parent != derived:
        raise ValueError(f"output_dir_must_be_direct_child_of_derived_root:{output_dir}")
    if any(part in {".", ".."} for part in output_dir.parts):
        raise ValueError(f"output_dir_contains_path_traversal:{output_dir}")
    if not re.fullmatch(r"compensation_feature_diagnosis_v1_1(?:_[A-Za-z0-9_-]+)?", resolved.name):
        raise ValueError(f"output_dir_name_not_allowed:{output_dir}")
    if any(name in resolved.name.lower() for name in ("baseline", "session", "extracted_repetitions", "segmentation")):
        raise ValueError(f"output_dir_name_forbidden:{output_dir}")
    return resolved


def prepare_output_dir(output_dir: Path, overwrite: bool) -> Path:
    output_dir = validate_output_dir(output_dir)
    if output_dir.exists() and not overwrite:
        raise FileExistsError(f"output_dir_exists_without_overwrite:{output_dir}")
    tmp = output_dir.with_name(f"{output_dir.name}.tmp_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    if tmp.exists():
        raise FileExistsError(f"temporary_output_dir_exists:{tmp}")
    tmp.mkdir(parents=True)
    return tmp


def finalize_output_dir(tmp_dir: Path, output_dir: Path, overwrite: bool) -> None:
    output_dir = validate_output_dir(output_dir)
    rollback_dir = output_dir.with_name(f"{output_dir.name}.rollback_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    if rollback_dir.exists():
        raise FileExistsError(f"rollback_output_dir_exists:{rollback_dir}")
    if output_dir.exists():
        if not overwrite:
            raise FileExistsError(f"output_dir_exists_without_overwrite:{output_dir}")
        output_dir.replace(rollback_dir)
    try:
        tmp_dir.replace(output_dir)
    except Exception:
        if rollback_dir.exists() and not output_dir.exists():
            rollback_dir.replace(output_dir)
        raise
    if rollback_dir.exists():
        try:
            shutil.rmtree(rollback_dir)
        except Exception as exc:
            raise RuntimeError(f"rollback_cleanup_failed:{rollback_dir}") from exc


def classify_expected_label(expected_label: str, rep_index: int) -> tuple[str, str, bool, str | None]:
    label = (expected_label or "").lower()
    mixed_markers = ("normal_compensation_normal", "normal-compensation-normal", "mixed", "05_")
    if any(marker in label for marker in mixed_markers):
        if rep_index == 2:
            return "mixed", "forward_trunk_compensation", True, "forward_trunk_compensation"
        return "mixed", "normal", False, None
    if "compensation" in label or "forward" in label or "前倾" in expected_label:
        return "compensation", "forward_trunk_compensation", True, "forward_trunk_compensation"
    return "normal", "normal", False, None


def resolve_rep_csv(rep_root: Path, manifest_entry: dict[str, Any]) -> Path:
    rep_csv_text = str(manifest_entry.get("rep_csv") or "")
    if rep_csv_text:
        candidate = Path(rep_csv_text)
        if candidate.is_absolute() and candidate.exists():
            return candidate
        derived_candidate = rep_root.parent / rep_csv_text
        if derived_candidate.exists():
            return derived_candidate
        root_candidate = rep_root / rep_csv_text
        if root_candidate.exists():
            return root_candidate
    source_stem = Path(str(manifest_entry.get("source_csv") or manifest_entry.get("source_filename"))).stem
    rep_index = int(manifest_entry.get("rep_index") or 1)
    return rep_root / source_stem / f"rep_{rep_index:02d}.csv"


def ensure_under_root(path: Path, root: Path, error_code: str) -> None:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"{error_code}:{path}")


def resolve_source_csv(source_root: Path, manifest_entry: dict[str, Any]) -> tuple[str, Path, int, str]:
    source_csv = str(manifest_entry.get("source_csv") or "")
    source_filename = str(manifest_entry.get("source_filename") or Path(source_csv).name)
    if not source_csv and not source_filename:
        raise ValueError("missing_source_csv_in_manifest")
    direct = source_root / source_csv
    candidates: list[Path] = []
    if direct.exists():
        candidates.append(direct)
    else:
        candidates = sorted(path for path in source_root.rglob(source_filename) if path.is_file())
    if len(candidates) != 1:
        raise FileNotFoundError(f"source_csv_resolution_failed:{source_csv}:{source_filename}:{len(candidates)}")
    source_path = candidates[0]
    ensure_under_root(source_path, source_root, "source_csv_outside_source_root")
    if source_path.name != source_filename:
        raise ValueError(f"source_filename_mismatch:{source_filename}:{source_path.name}")
    relative = source_path.relative_to(source_root).as_posix()
    size = source_path.stat().st_size
    if size <= 0:
        raise ValueError(f"empty_source_csv:{source_path}")
    return relative, source_path, size, sha256_file(source_path)


def rep_metadata_path(rep_csv: Path) -> Path | None:
    candidates = [
        rep_csv.with_suffix(".metadata.json"),
        rep_csv.with_name(f"{rep_csv.stem}_metadata.json"),
        rep_csv.with_name(f"{rep_csv.stem}.json"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_manifest_records(manifest_path: Path, rep_root: Path, source_root: Path) -> list[RepRecord]:
    manifest = load_json(manifest_path)
    entries = list(manifest.get("included_reps") or []) + list(manifest.get("excluded_reps") or [])
    records: list[RepRecord] = []
    for item in entries:
        rep_csv = resolve_rep_csv(rep_root, item)
        source_relative, source_absolute, source_size, source_sha = resolve_source_csv(source_root, item)
        records.append(
            RepRecord(
                source_csv=str(item.get("source_csv") or item.get("source_file") or ""),
                source_filename=str(item.get("source_filename") or Path(str(item.get("source_csv") or "")).name),
                source_relative_path=source_relative,
                source_absolute_path=source_absolute,
                source_file_size_bytes=source_size,
                expected_label=str(item.get("expected_label") or item.get("source_category") or ""),
                rep_index=int(item.get("rep_index") or 1),
                rep_csv=rep_csv,
                metadata_json=rep_metadata_path(rep_csv),
                source_sha256=source_sha,
                manifest_entry=item,
            )
        )
    return records


def source_csv_manifest(records: list[RepRecord]) -> list[dict[str, Any]]:
    unique: dict[str, RepRecord] = {}
    for record in records:
        unique[record.source_sha256] = record
    files = [
        {
            "relative_path": record.source_relative_path,
            "absolute_path": str(record.source_absolute_path),
            "size_bytes": record.source_file_size_bytes,
            "sha256": record.source_sha256,
        }
        for record in sorted(unique.values(), key=lambda r: r.source_relative_path)
    ]
    if len(files) != 22:
        raise ValueError(f"unique_source_csv_count_mismatch:expected=22:actual={len(files)}")
    return files


def load_validation_scores(validation_path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    data = load_json(validation_path)
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for session in data.get("sessions") or []:
        filename = str(session.get("filename") or Path(str(session.get("source") or "")).name)
        rep_index = int(session.get("rep_index") or 1)
        result[(filename, rep_index)] = session
    return result


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def require_trial_feature(trial: dict[str, Any], field: str, source_label: str) -> float:
    if field not in trial:
        raise ValueError(f"missing_required_trial_feature:{source_label}:{field}")
    value = finite_number(trial.get(field))
    if value is None:
        raise ValueError(f"invalid_required_trial_feature:{source_label}:{field}")
    return value


def require_all_trial_features(trial: dict[str, Any], source_label: str) -> None:
    for field in REQUIRED_TRIAL_DIAGNOSTIC_FIELDS:
        require_trial_feature(trial, field, source_label)


def require_finite_col(df: pd.DataFrame, col: str, rep_csv: Path) -> pd.Series:
    if col not in df.columns:
        raise ValueError(f"missing_required_diagnostic_column:{rep_csv}:{col}")
    series = pd.to_numeric(df[col], errors="coerce")
    if series.isna().any() or not np.isfinite(series.to_numpy(dtype=float)).all():
        raise ValueError(f"invalid_required_diagnostic_column:{rep_csv}:{col}")
    return series


def numeric_stats(values: Iterable[Any]) -> dict[str, float | int | None]:
    nums = [float(v) for v in values if finite_number(v) is not None]
    if not nums:
        return {
            "count": 0,
            "mean": None,
            "std": None,
            "median": None,
            "q1": None,
            "q3": None,
            "min": None,
            "max": None,
        }
    arr = np.asarray(nums, dtype=float)
    return {
        "count": int(arr.size),
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr, ddof=1)) if arr.size > 1 else 0.0,
        "median": float(np.median(arr)),
        "q1": float(np.percentile(arr, 25)),
        "q3": float(np.percentile(arr, 75)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
    }


def safe_z(value: Any, mean: Any, std: Any) -> float | None:
    v = finite_number(value)
    m = finite_number(mean)
    s = finite_number(std)
    if v is None or m is None or s is None or abs(s) < 1e-12:
        return None
    return (v - m) / s


def robust_z(value: Any, median: Any, q1: Any, q3: Any) -> float | None:
    v = finite_number(value)
    med = finite_number(median)
    low = finite_number(q1)
    high = finite_number(q3)
    if v is None or med is None or low is None or high is None:
        return None
    iqr = high - low
    if abs(iqr) < 1e-12:
        return None
    return (v - med) / (iqr / 1.349)


def cohen_d(group_a: list[float], group_b: list[float]) -> float | None:
    if len(group_a) < 2 or len(group_b) < 2:
        return None
    a = np.asarray(group_a, dtype=float)
    b = np.asarray(group_b, dtype=float)
    pooled = math.sqrt(((len(a) - 1) * np.var(a, ddof=1) + (len(b) - 1) * np.var(b, ddof=1)) / (len(a) + len(b) - 2))
    if pooled < 1e-12:
        return None
    return float((np.mean(b) - np.mean(a)) / pooled)


def cliffs_delta(group_a: list[float], group_b: list[float]) -> float | None:
    if not group_a or not group_b:
        return None
    greater = 0
    less = 0
    for a in group_a:
        for b in group_b:
            if b > a:
                greater += 1
            elif b < a:
                less += 1
    return (greater - less) / (len(group_a) * len(group_b))


def feature_physical_meaning(feature: str) -> str:
    meanings = {
        "final_score": "Frozen final score, used for diagnostic correlation only.",
        "TrunkCompensationRatio": "Existing scoring-chain trunk compensation ratio.",
        "TrunkROM": "Existing scoring-chain trunk range of motion.",
        "imu2_pitch_range": "Read-only trunk IMU2 pitch motion range.",
        "imu2_pitch_absolute_excursion": "Read-only IMU2 pitch absolute excursion from initial window.",
        "imu2_gyro_norm_p95": "Read-only IMU2 gyro norm 95th percentile.",
    }
    return meanings.get(feature, "Existing scoring-chain or read-only diagnostic feature.")


def separation_direction(normal_median: Any, compensation_median: Any) -> str | None:
    n = finite_number(normal_median)
    c = finite_number(compensation_median)
    if n is None or c is None:
        return None
    if abs(c - n) < 1e-12:
        return "similar"
    return "higher_in_compensation" if c > n else "lower_in_compensation"


def empirical_percentile(normal_values: list[float], value: Any) -> float | None:
    v = finite_number(value)
    if v is None or not normal_values:
        return None
    return 100.0 * sum(item <= v for item in normal_values) / len(normal_values)


def window_mask(time_ms: pd.Series, start_ms: float, end_ms: float) -> pd.Series:
    return (time_ms >= start_ms) & (time_ms <= end_ms)


def compute_imu2_diagnostics(df: pd.DataFrame, rep_csv: Path) -> dict[str, Any]:
    time_ms = require_finite_col(df, "time_ms", rep_csv)
    pitch = require_finite_col(df, "imu2_pitch", rep_csv)
    roll = require_finite_col(df, "imu2_roll", rep_csv)
    yaw = require_finite_col(df, "imu2_yaw", rep_csv)
    gx = require_finite_col(df, "imu2_gx", rep_csv)
    gy = require_finite_col(df, "imu2_gy", rep_csv)
    gz = require_finite_col(df, "imu2_gz", rep_csv)

    start_t = float(time_ms.iloc[0])
    end_t = float(time_ms.iloc[-1])
    duration = max(0.0, end_t - start_t)
    edge_window = min(500.0, max(1.0, duration * 0.1))
    start_mask = window_mask(time_ms, start_t, start_t + edge_window)
    end_mask = window_mask(time_ms, end_t - edge_window, end_t)
    start_pitch = float(np.median(pitch[start_mask])) if start_mask.any() else float(pitch.iloc[0])
    end_pitch = float(np.median(pitch[end_mask])) if end_mask.any() else float(pitch.iloc[-1])
    pitch_delta = pitch.to_numpy(dtype=float) - start_pitch
    abs_excursion = float(np.max(np.abs(pitch_delta))) if pitch_delta.size else 0.0
    gyro_norm = np.sqrt(gx.to_numpy(dtype=float) ** 2 + gy.to_numpy(dtype=float) ** 2 + gz.to_numpy(dtype=float) ** 2)
    if time_ms.size > 1:
        dt = np.diff(time_ms.to_numpy(dtype=float), prepend=float(time_ms.iloc[0]))
        total_dt = float(np.sum(np.maximum(dt, 0.0))) or 1.0
        abs_active = np.abs(pitch_delta) >= max(1e-9, abs_excursion * 0.5)
        positive_peak = float(np.max(pitch_delta)) if pitch_delta.size else 0.0
        negative_peak = float(np.min(pitch_delta)) if pitch_delta.size else 0.0
        positive_active = pitch_delta >= max(1e-9, positive_peak * 0.5) if positive_peak > 0 else np.zeros_like(pitch_delta, dtype=bool)
        negative_active = pitch_delta <= min(-1e-9, negative_peak * 0.5) if negative_peak < 0 else np.zeros_like(pitch_delta, dtype=bool)
        absolute_excursion_duration_ms = float(np.sum(np.maximum(dt, 0.0) * abs_active))
        positive_excursion_duration_ms = float(np.sum(np.maximum(dt, 0.0) * positive_active))
        negative_excursion_duration_ms = float(np.sum(np.maximum(dt, 0.0) * negative_active))
        duration_ratio = float(absolute_excursion_duration_ms / total_dt)
    else:
        absolute_excursion_duration_ms = 0.0
        positive_excursion_duration_ms = 0.0
        negative_excursion_duration_ms = 0.0
        duration_ratio = 0.0
    peak_idx = int(np.argmax(np.abs(pitch_delta))) if pitch_delta.size else 0

    return {
        "imu2_pitch_start_median": start_pitch,
        "imu2_pitch_end_median": end_pitch,
        "imu2_pitch_min": float(np.min(pitch)),
        "imu2_pitch_max": float(np.max(pitch)),
        "imu2_pitch_range": float(np.max(pitch) - np.min(pitch)),
        "imu2_pitch_mean": float(np.mean(pitch)),
        "imu2_pitch_median": float(np.median(pitch)),
        "imu2_pitch_p05": float(np.percentile(pitch, 5)),
        "imu2_pitch_p95": float(np.percentile(pitch, 95)),
        "imu2_roll_range": float(np.max(roll) - np.min(roll)),
        "imu2_yaw_range": float(np.max(yaw) - np.min(yaw)),
        "imu2_gyro_norm_mean": float(np.mean(gyro_norm)),
        "imu2_gyro_norm_max": float(np.max(gyro_norm)),
        "imu2_gyro_norm_p95": float(np.percentile(gyro_norm, 95)),
        "imu2_pitch_positive_excursion": float(np.max(pitch_delta)),
        "imu2_pitch_negative_excursion": float(np.min(pitch_delta)),
        "imu2_pitch_absolute_excursion": abs_excursion,
        "imu2_positive_excursion_duration_ms": positive_excursion_duration_ms,
        "imu2_negative_excursion_duration_ms": negative_excursion_duration_ms,
        "imu2_absolute_excursion_duration_ms": absolute_excursion_duration_ms,
        "imu2_absolute_excursion_ratio": duration_ratio,
        "imu2_absolute_excursion_peak_time_ms": float(time_ms.iloc[peak_idx]) if len(time_ms) else None,
        "candidate_forward_direction": None,
        "candidate_forward_excursion": None,
        "candidate_forward_direction_exploratory_only": True,
    }


def motion_envelope(df: pd.DataFrame, rep_csv: Path) -> np.ndarray:
    norms: list[np.ndarray] = []
    for imu in (1, 3, 4):
        gx = require_finite_col(df, f"imu{imu}_gx", rep_csv).to_numpy(dtype=float)
        gy = require_finite_col(df, f"imu{imu}_gy", rep_csv).to_numpy(dtype=float)
        gz = require_finite_col(df, f"imu{imu}_gz", rep_csv).to_numpy(dtype=float)
        norms.append(np.sqrt(gx * gx + gy * gy + gz * gz))
    return np.mean(np.vstack(norms), axis=0)


def boundary_window_metrics(df: pd.DataFrame, rep_csv: Path) -> dict[str, Any]:
    time_ms = require_finite_col(df, "time_ms", rep_csv)
    env = motion_envelope(df, rep_csv)
    start_t = float(time_ms.iloc[0])
    end_t = float(time_ms.iloc[-1])
    first = window_mask(time_ms, start_t, start_t + 500.0).to_numpy()
    last = window_mask(time_ms, end_t - 500.0, end_t).to_numpy()
    quiet_threshold = float(np.percentile(env, 25)) if env.size else 0.0
    return {
        "first_500ms_motion_mean": float(np.mean(env[first])) if first.any() else None,
        "first_500ms_static_ratio": float(np.mean(env[first] <= quiet_threshold)) if first.any() else None,
        "last_500ms_motion_mean": float(np.mean(env[last])) if last.any() else None,
        "last_500ms_static_ratio": float(np.mean(env[last] <= quiet_threshold)) if last.any() else None,
    }


def normalize_boundary(boundary: Any) -> dict[str, Any] | None:
    if not isinstance(boundary, dict):
        return None
    return {
        "boundary_index": boundary.get("boundary_index"),
        "boundary_time_ms": boundary.get("boundary_time_ms"),
        "boundary_sources": boundary.get("boundary_sources") or [],
        "boundary_origin": boundary.get("boundary_origin"),
        "detected_from_signal_candidate": boundary.get("detected_from_signal_candidate"),
        "quiet_duration_ms": boundary.get("quiet_duration_ms"),
        "motion_valley_score": boundary.get("motion_valley_score"),
        "pose_return_score": boundary.get("pose_return_score"),
        "confidence": boundary.get("confidence"),
        "selection_reason": boundary.get("selection_reason"),
    }


def read_rep_metadata(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    data = load_json(path)
    if isinstance(data, dict) and isinstance(data.get("repetition"), dict):
        return data["repetition"]
    if isinstance(data, dict) and isinstance(data.get("rep"), dict):
        return data["rep"]
    return data if isinstance(data, dict) else {}


def detector_condition_trace(trial: dict[str, Any], template_task: dict[str, Any]) -> tuple[list[dict[str, Any]], str, bool, list[str], dict[str, Any], dict[str, Any]]:
    score_row = json_to_score_row(trial)
    wrist_f = extract_wrist_features(score_row)
    forearm_f = extract_forearm_features(score_row)
    upper_f = extract_upperarm_features(score_row)
    trunk_f = extract_trunk_features(score_row)
    emg_f = extract_emg_features(score_row, MUSCLES)
    actual_result = detect_compensation_4imu_4emg(wrist_f, forearm_f, upper_f, trunk_f, emg_f, template_task, MUSCLES)

    actual_inputs = {
        "wrist": wrist_f,
        "forearm": forearm_f,
        "upperarm": upper_f,
        "trunk": trunk_f,
        "emg": emg_f,
    }

    conditions: list[dict[str, Any]] = []

    def add_condition(
        branch: str,
        input_feature: str,
        feature_value: Any,
        operator: str,
        threshold_low: Any = None,
        threshold_high: Any = None,
        template_mean: Any = None,
        template_std: Any = None,
        affects_detector_detected: bool = False,
        quality_finding_name: str | None = None,
    ) -> bool:
        value = finite_number(feature_value)
        low = finite_number(threshold_low)
        high = finite_number(threshold_high)
        if operator == ">=":
            passed = value is not None and low is not None and value >= low
            signed_margin = None if value is None or low is None else value - low
            margin_to_pass = signed_margin
        elif operator == "<":
            passed = value is not None and high is not None and value < high
            signed_margin = None if value is None or high is None else high - value
            margin_to_pass = signed_margin
        elif operator == "outside":
            passed = value is not None and low is not None and high is not None and (value < low or value > high)
            if value is None or low is None or high is None:
                signed_margin = None
                margin_to_pass = None
            else:
                center = (low + high) / 2.0
                half_width = (high - low) / 2.0
                signed_margin = abs(value - center) - half_width
                margin_to_pass = signed_margin
        else:
            raise ValueError(f"unsupported_detector_operator:{operator}")
        conditions.append(
            {
                "branch": branch,
                "input_feature": input_feature,
                "feature_value": value,
                "template_mean": finite_number(template_mean),
                "template_std": finite_number(template_std),
                "operator": operator,
                "threshold_low": low,
                "threshold_high": high,
                "margin": margin_to_pass,
                "margin_to_pass": margin_to_pass,
                "signed_margin": signed_margin,
                "passed": passed,
                "affects_detector_detected": affects_detector_detected,
                "quality_finding_name": quality_finding_name,
            }
        )
        return passed

    lf = add_condition("LF", "TrunkForwardLean_deg", trunk_f.get("TrunkForwardLean_deg"), ">=", threshold_low=15.0, affects_detector_detected=True)
    tr = add_condition("TR", "TrunkRotation_deg", trunk_f.get("TrunkRotation_deg"), ">=", threshold_low=15.0, affects_detector_detected=True)
    rom_mean = finite_number(template_task.get("ROM_mean"))
    rom_std = finite_number(template_task.get("ROM_std"))
    if rom_mean is not None and rom_std is not None:
        add_condition(
            "quality_upper_arm_rom_low",
            "UpperArmROM_mm",
            upper_f.get("ROM"),
            "<",
            threshold_high=rom_mean - 1.5 * max(abs(rom_std), 1e-8),
            template_mean=rom_mean,
            template_std=rom_std,
            quality_finding_name="insufficient_upperarm_ROM",
        )
    forearm_mean = finite_number(template_task.get("forearm_rotation_mean"))
    forearm_std = finite_number(template_task.get("forearm_rotation_std"))
    if forearm_mean is not None and forearm_std is not None:
        std = max(abs(forearm_std), 1e-8)
        add_condition(
            "quality_forearm_rotation_deviation",
            "ForearmRotationRange_deg",
            forearm_f.get("rotation_range"),
            "outside",
            threshold_low=forearm_mean - 2.0 * std,
            threshold_high=forearm_mean + 2.0 * std,
            template_mean=forearm_mean,
            template_std=forearm_std,
            quality_finding_name="forearm_coordination_abnormal",
        )
    add_condition("quality_wrist_speed_peaks", "NumSpeedPeaks", wrist_f.get("num_speed_peaks"), ">=", threshold_low=8.0, quality_finding_name="unsmooth_motion")
    add_condition("quality_wrist_pause_count", "PauseCount", wrist_f.get("pause_count"), ">=", threshold_low=3.0, quality_finding_name="excessive_pauses")

    for muscle in MUSCLES:
        rms = finite_number(emg_f.get(f"{muscle}_RMS"))
        rms_mean = finite_number(template_task.get(f"{muscle}_RMS_mean"))
        rms_std = finite_number(template_task.get(f"{muscle}_RMS_std"))
        if rms_mean is None or rms_std is None:
            continue
        std = max(abs(rms_std), 1e-8)
        z = None if rms is None else (rms - rms_mean) / std
        margin = None if z is None else z - 2.0
        conditions.append(
            {
                "branch": f"quality_emg_{muscle}_rms_z",
                "input_feature": f"{muscle}_RMS",
                "feature_value": rms,
                "template_mean": rms_mean,
                "template_std": rms_std,
                "operator": ">",
                "threshold_low": 2.0,
                "threshold_high": None,
                "margin": margin,
                "margin_to_pass": margin,
                "signed_margin": margin,
                "passed": z is not None and z > 2.0,
                "affects_detector_detected": False,
                "quality_finding_name": "abnormal_muscle_recruitment",
            }
        )

    expected_types = sorted([branch for branch, passed in (("LF", lf), ("TR", tr)) if passed])
    expected_quality = sorted(
        {
            str(condition["quality_finding_name"])
            for condition in conditions
            if condition.get("quality_finding_name") and condition.get("passed")
        }
    )
    actual_types = list(actual_result.get("compensation_types") or [])
    if sorted(actual_types) != expected_types:
        raise ValueError(f"detector_trace_mismatch:expected={expected_types}:actual={actual_types}")
    actual_quality = sorted(actual_result.get("quality_findings") or [])
    if actual_quality != expected_quality:
        raise ValueError(f"detector_trace_mismatch:quality_expected={expected_quality}:quality_actual={actual_quality}")
    expected_has = bool(expected_types)
    if bool(actual_result.get("has_compensation")) != expected_has:
        raise ValueError(f"detector_trace_mismatch:has_compensation_expected={expected_has}:actual={actual_result.get('has_compensation')}")
    expected_score = min(100, len(set(expected_types)) * 20)
    if actual_result.get("compensation_score") != expected_score:
        raise ValueError(f"detector_trace_mismatch:score_expected={expected_score}:actual={actual_result.get('compensation_score')}")

    detector_detected = bool(actual_result.get("has_compensation"))
    if detector_detected:
        reason = "detected"
    elif "TrunkForwardLean_deg" not in trial or "TrunkRotation_deg" not in trial:
        reason = "lf_tr_required_inputs_absent_from_trial_and_default_to_zero_in_current_score_row"
    else:
        reason = "lf_tr_thresholds_not_reached"

    return conditions, reason, detector_detected, actual_types, actual_inputs, actual_result


def score_value(session_score: dict[str, Any], field: str) -> Any:
    score = session_score.get("score") if isinstance(session_score.get("score"), dict) else session_score
    if field == "final_score":
        return score.get("final_score", score.get("total_score"))
    if field == "score_valid":
        return score.get("valid")
    subscores = session_score.get("subscores") or score.get("subscores") or {}
    return subscores.get(field)


def build_rep_diagnosis(record: RepRecord, score: dict[str, Any] | None, template_task: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not record.rep_csv.exists():
        raise FileNotFoundError(f"missing_rep_csv:{record.rep_csv}")
    df = read_fused_csv(record.rep_csv)
    validate_fused_scoring_input(df)
    for col in IMU2_DIAGNOSTIC_COLUMNS:
        require_finite_col(df, col, record.rep_csv)
    require_finite_col(df, "log_seq", record.rep_csv)
    require_finite_col(df, "time_ms", record.rep_csv)

    trial = build_trial_from_raw_log(
        record.rep_csv,
        patient_id="diagnosis",
        session_id=Path(record.source_filename).stem or record.rep_csv.parent.name,
        task_number=12,
        trial_id=record.rep_index,
    )
    require_all_trial_features(trial, str(record.rep_csv))
    metadata = read_rep_metadata(record.metadata_json)
    source_category, rep_label, has_comp, comp_type = classify_expected_label(record.expected_label, record.rep_index)
    conditions, non_hit, derived_detected, derived_types, actual_inputs, actual_detector = detector_condition_trace(trial, template_task)

    start_boundary = normalize_boundary(metadata.get("start_boundary"))
    end_boundary = normalize_boundary(metadata.get("end_boundary"))
    time_col = pd.to_numeric(df["time_ms"], errors="coerce")
    log_col = pd.to_numeric(df["log_seq"], errors="coerce")
    row: dict[str, Any] = {
        "diagnosis_version": DIAGNOSIS_VERSION,
        "baseline_version": BASELINE_VERSION,
        "segmentation_version": SEGMENTATION_VERSION,
        "sensor_mapping_version": SENSOR_MAPPING_VERSION,
        "source_file": record.source_filename,
        "source_relative_path": record.source_relative_path,
        "source_absolute_path": str(record.source_absolute_path),
        "source_file_exists": record.source_absolute_path.exists(),
        "source_file_size_bytes": record.source_file_size_bytes,
        "source_file_sha256": record.source_sha256,
        "rep_csv": str(record.rep_csv),
        "rep_csv_sha256": sha256_file(record.rep_csv),
        "source_category": source_category,
        "ground_truth_rep_label": rep_label,
        "ground_truth_has_compensation": has_comp,
        "ground_truth_compensation_type": comp_type,
        "rep_index": record.rep_index,
        "row_count": int(len(df)),
        "start_log_seq": int(log_col.iloc[0]) if len(log_col) else None,
        "end_log_seq": int(log_col.iloc[-1]) if len(log_col) else None,
        "start_time_ms": float(time_col.iloc[0]) if len(time_col) else None,
        "end_time_ms": float(time_col.iloc[-1]) if len(time_col) else None,
        "duration_ms": float(time_col.iloc[-1] - time_col.iloc[0]) if len(time_col) > 1 else 0.0,
        "score_valid": score_value(score or {}, "score_valid"),
        "final_score": score_value(score or {}, "final_score"),
        "detector_detected": derived_detected,
        "detector_types": derived_types,
        "detector_actual_input_features": actual_inputs,
        "detector_thresholds": {"TrunkForwardLean_deg": 15.0, "TrunkRotation_deg": 15.0},
        "detector_branch_result": conditions,
        "detector_non_hit_reason": None if derived_detected else non_hit,
        "actual_detector_has_compensation": actual_detector.get("has_compensation"),
        "actual_detector_compensation_types": actual_detector.get("compensation_types"),
        "actual_detector_quality_findings": actual_detector.get("quality_findings"),
        "actual_detector_details": actual_detector.get("details"),
        "start_boundary": start_boundary,
        "end_boundary": end_boundary,
        "start_boundary_origin": (start_boundary or {}).get("boundary_origin"),
        "end_boundary_origin": (end_boundary or {}).get("boundary_origin"),
        "boundary_origin_start": (start_boundary or {}).get("boundary_origin"),
        "boundary_origin_end": (end_boundary or {}).get("boundary_origin"),
        "start_boundary_detected_from_signal_candidate": (start_boundary or {}).get("detected_from_signal_candidate"),
        "end_boundary_detected_from_signal_candidate": (end_boundary or {}).get("detected_from_signal_candidate"),
        "candidate_start_index": (start_boundary or {}).get("boundary_index"),
        "candidate_end_index": (end_boundary or {}).get("boundary_index"),
        "rep_start_index": metadata.get("start_index"),
        "rep_end_index": metadata.get("end_index"),
        "start_boundary_margin_rows": (
            None
            if metadata.get("start_index") is None or (start_boundary or {}).get("boundary_index") is None
            else int(metadata.get("start_index")) - int((start_boundary or {}).get("boundary_index"))
        ),
        "end_boundary_margin_rows": (
            None
            if metadata.get("end_index") is None or (end_boundary or {}).get("boundary_index") is None
            else int(metadata.get("end_index")) - int((end_boundary or {}).get("boundary_index"))
        ),
        "pre_boundary_margin_rows": (
            None
            if metadata.get("start_index") is None or (start_boundary or {}).get("boundary_index") is None
            else int(metadata.get("start_index")) - int((start_boundary or {}).get("boundary_index"))
        ),
        "post_boundary_margin_rows": (
            None
            if metadata.get("end_index") is None or (end_boundary or {}).get("boundary_index") is None
            else int(metadata.get("end_index")) - int((end_boundary or {}).get("boundary_index"))
        ),
    }
    for field in SUBSCORE_FIELDS:
        row[field] = score_value(score or {}, field)
    for canonical, source_field in DIAGNOSTIC_FEATURE_SOURCES.items():
        row[canonical] = require_trial_feature(trial, source_field, str(record.rep_csv))
        row[f"{canonical}__canonical_feature_name"] = canonical
        row[f"{canonical}__source_trial_field_name"] = source_field
    for condition in conditions:
        branch = str(condition.get("branch"))
        row[f"detector_{branch}_feature_value"] = condition.get("feature_value")
        row[f"detector_{branch}_margin"] = condition.get("margin")
        row[f"detector_{branch}_passed"] = condition.get("passed")
    row.update(compute_imu2_diagnostics(df, record.rep_csv))
    row.update(boundary_window_metrics(df, record.rep_csv))
    row["first_500ms_motion_level"] = row.get("first_500ms_motion_mean")
    row["last_500ms_motion_level"] = row.get("last_500ms_motion_mean")
    trace = {
        "source_file": record.source_filename,
        "rep_index": record.rep_index,
        "rep_csv": str(record.rep_csv),
        "ground_truth_rep_label": rep_label,
        "detector_detected_from_contract_conditions": derived_detected,
        "detector_types_from_contract_conditions": derived_types,
        "non_hit_reason_from_contract_conditions": None if derived_detected else non_hit,
        "actual_input_features": actual_inputs,
        "conditions": conditions,
        "actual_detector_has_compensation": actual_detector.get("has_compensation"),
        "actual_detector_compensation_types": actual_detector.get("compensation_types"),
        "actual_detector_quality_findings": actual_detector.get("quality_findings"),
        "actual_detector_details": actual_detector.get("details"),
        "quality_only_note": "Quality-only branches do not append compensation types.",
    }
    return row, trace


def feature_distribution_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    base_features = [
        "final_score",
        *SUBSCORE_FIELDS,
        *SCORING_FEATURE_FIELDS,
        *IMU2_DISTRIBUTION_FIELDS,
        *BOUNDARY_DISTRIBUTION_FIELDS,
        *EMG_TRIAL_FEATURES,
    ]
    detector_features = sorted(
        {
            key
            for row in rows
            for key in row
            if key.startswith("detector_") and (key.endswith("_margin") or key.endswith("_feature_value"))
        }
    )
    features = [*base_features, *detector_features]
    pure_normal = [r for r in rows if r.get("source_category") == "normal"]
    pure_compensation = [r for r in rows if r.get("source_category") == "compensation"]
    mixed_normal = [r for r in rows if r.get("source_category") == "mixed" and r.get("ground_truth_rep_label") == "normal"]
    mixed_compensation = [r for r in rows if r.get("source_category") == "mixed" and r.get("ground_truth_has_compensation") is True]
    all_ground_truth_normal = [r for r in rows if r.get("ground_truth_has_compensation") is not True]
    all_ground_truth_compensation = [r for r in rows if r.get("ground_truth_has_compensation") is True]
    output: list[dict[str, Any]] = []
    for feature in features:
        values_all = [finite_number(r.get(feature)) for r in rows]
        finite_count = sum(v is not None for v in values_all)
        missing_count = len(rows) - finite_count
        pure_normal_nums = [v for v in (finite_number(r.get(feature)) for r in pure_normal) if v is not None]
        pure_comp_nums = [v for v in (finite_number(r.get(feature)) for r in pure_compensation) if v is not None]
        mixed_normal_nums = [v for v in (finite_number(r.get(feature)) for r in mixed_normal) if v is not None]
        mixed_comp_nums = [v for v in (finite_number(r.get(feature)) for r in mixed_compensation) if v is not None]
        all_normal_nums = [v for v in (finite_number(r.get(feature)) for r in all_ground_truth_normal) if v is not None]
        all_comp_nums = [v for v in (finite_number(r.get(feature)) for r in all_ground_truth_compensation) if v is not None]
        nstats = numeric_stats(pure_normal_nums)
        cstats = numeric_stats(pure_comp_nums)
        normal_overlap_count = None
        compensation_overlap_count = None
        if all_comp_nums:
            cmin = min(all_comp_nums)
            cmax = max(all_comp_nums)
            normal_overlap_count = sum(cmin <= value <= cmax for value in all_normal_nums)
        if all_normal_nums:
            nmin = min(all_normal_nums)
            nmax = max(all_normal_nums)
            compensation_overlap_count = sum(nmin <= value <= nmax for value in all_comp_nums)
        output.append(
            {
                "feature": feature,
                "feature_name": feature,
                "source": "diagnosis_table",
                "physical_meaning": feature_physical_meaning(feature),
                "pure_normal_distribution": nstats,
                "pure_compensation_distribution": cstats,
                "mixed_normal_distribution": numeric_stats(mixed_normal_nums),
                "mixed_compensation_distribution": numeric_stats(mixed_comp_nums),
                "all_ground_truth_normal_distribution": numeric_stats(all_normal_nums),
                "all_ground_truth_compensation_distribution": numeric_stats(all_comp_nums),
                "normal_count": nstats["count"],
                "normal_mean": nstats["mean"],
                "normal_std": nstats["std"],
                "normal_median": nstats["median"],
                "normal_q1": nstats["q1"],
                "normal_q3": nstats["q3"],
                "normal_min": nstats["min"],
                "normal_max": nstats["max"],
                "compensation_count": cstats["count"],
                "compensation_mean": cstats["mean"],
                "compensation_std": cstats["std"],
                "compensation_median": cstats["median"],
                "compensation_q1": cstats["q1"],
                "compensation_q3": cstats["q3"],
                "compensation_min": cstats["min"],
                "compensation_max": cstats["max"],
                "cohens_d_comp_minus_normal": cohen_d(pure_normal_nums, pure_comp_nums),
                "effect_size": cohen_d(pure_normal_nums, pure_comp_nums),
                "cliffs_delta_comp_vs_normal": cliffs_delta(pure_normal_nums, pure_comp_nums),
                "separation_direction": separation_direction(nstats.get("median"), cstats.get("median")),
                "mean_difference": (
                    None
                    if nstats.get("mean") is None or cstats.get("mean") is None
                    else cstats["mean"] - nstats["mean"]
                ),
                "median_difference": (
                    None
                    if nstats.get("median") is None or cstats.get("median") is None
                    else cstats["median"] - nstats["median"]
                ),
                "compensation_values_inside_normal_minmax": compensation_overlap_count,
                "normal_overlap_count": normal_overlap_count,
                "compensation_overlap_count": compensation_overlap_count,
                "missing_count": missing_count,
                "finite_count": finite_count,
                "recommended_for_scan": feature in {
                    "TrunkCompensationRatio",
                    "TrunkROM",
                    "imu2_pitch_range",
                    "imu2_pitch_absolute_excursion",
                    "imu2_gyro_norm_p95",
                },
                "reason": "exploratory separation diagnostic; no production threshold emitted",
                "exploratory_only": True,
                "current_dataset_only": True,
                "not_clinically_validated": True,
            }
        )
    return output


def add_normal_distribution_fields(rows: list[dict[str, Any]], summary_rows: list[dict[str, Any]]) -> None:
    by_feature = {row["feature"]: row for row in summary_rows}
    normal_values_by_feature = {
        feature: [
            value
            for value in (finite_number(row.get(feature)) for row in rows if row.get("source_category") == "normal")
            if value is not None
        ]
        for feature in by_feature
    }
    for row in rows:
        for feature, summary in by_feature.items():
            value = row.get(feature)
            row[f"{feature}_normal_z_score"] = safe_z(value, summary.get("normal_mean"), summary.get("normal_std"))
            row[f"{feature}_normal_robust_z_score"] = robust_z(value, summary.get("normal_median"), summary.get("normal_q1"), summary.get("normal_q3"))
            row[f"{feature}_normal_percentile"] = empirical_percentile(normal_values_by_feature.get(feature, []), value)


def load_mixed_session_summary(mixed_session_dir: Path) -> dict[str, Any]:
    return load_mixed_session_contract(mixed_session_dir)


def csv_cells_equal(left: Path, right: Path) -> bool:
    left_df = pd.read_csv(left, dtype=str, keep_default_na=False)
    right_df = pd.read_csv(right, dtype=str, keep_default_na=False)
    return list(left_df.columns) == list(right_df.columns) and left_df.shape == right_df.shape and left_df.equals(right_df)


def load_mixed_session_contract(mixed_session_dir: Path) -> dict[str, Any]:
    required = [
        mixed_session_dir / "session_summary.json",
        mixed_session_dir / "session_manifest.json",
        *[mixed_session_dir / "repetitions" / f"rep_{idx:02d}.csv" for idx in (1, 2, 3)],
        *[mixed_session_dir / "per_rep_scores" / f"rep_{idx:02d}_score.json" for idx in (1, 2, 3)],
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"missing_mixed_session_artifacts:{missing}")
    summary = load_json(mixed_session_dir / "session_summary.json")
    manifest = load_json(mixed_session_dir / "session_manifest.json")
    checks = {
        "segmented_candidate_count": 3,
        "score_valid_repetition_count": 3,
        "score_invalid_repetition_count": 0,
        "baseline_version": BASELINE_VERSION,
        "sensor_mapping_version": SENSOR_MAPPING_VERSION,
    }
    for key, expected in checks.items():
        actual = summary.get(key, manifest.get(key))
        if actual != expected:
            raise ValueError(f"mixed_session_contract_mismatch:{key}:expected={expected}:actual={actual}")
    if summary.get("source_sha256") != manifest.get("source_sha256"):
        raise ValueError("mixed_session_summary_manifest_source_sha_mismatch")
    scores: dict[int, dict[str, Any]] = {}
    for rep_index, expected_score in MIXED_EXPECTED_SCORES.items():
        score_path = mixed_session_dir / "per_rep_scores" / f"rep_{rep_index:02d}_score.json"
        score = load_json(score_path)
        actual = finite_number(score.get("final_score"))
        if actual is None or round(actual, 2) != round(expected_score, 2):
            raise ValueError(f"mixed_session_score_mismatch:rep_{rep_index:02d}:expected={expected_score}:actual={actual}")
        scores[rep_index] = score
    return {
        "available": True,
        "path": str(mixed_session_dir / "session_summary.json"),
        "session_id": summary.get("session_id"),
        "summary": summary,
        "manifest": manifest,
        "source_sha256": summary.get("source_sha256") or manifest.get("source_sha256"),
        "source_session_path": summary.get("source_session_path") or manifest.get("source_session_path"),
        "scores": scores,
    }


def validate_mixed_source(mixed_source: str, source_root: Path, session_info: dict[str, Any]) -> dict[str, Any]:
    if not mixed_source:
        raise ValueError("missing_required_mixed_source")
    mixed_path = Path(mixed_source)
    if not mixed_path.exists() or not mixed_path.is_file():
        raise FileNotFoundError(f"mixed_source_not_found:{mixed_source}")
    ensure_under_root(mixed_path, source_root, "mixed_source_outside_source_root")
    mixed_sha = sha256_file(mixed_path)
    expected_sha = session_info.get("source_sha256")
    if not expected_sha or mixed_sha != expected_sha:
        raise ValueError(f"mixed_source_sha_mismatch:expected={expected_sha}:actual={mixed_sha}")
    session_source_name = Path(str(session_info.get("source_session_path") or "")).name
    if session_source_name and session_source_name != mixed_path.name:
        raise ValueError(f"mixed_source_filename_mismatch:session={session_source_name}:arg={mixed_path.name}")
    if session_info.get("session_id") != "USER_V212_MIXED_20260806_115813":
        raise ValueError(f"mixed_session_id_mismatch:{session_info.get('session_id')}")
    return {
        "path": str(mixed_path),
        "name": mixed_path.name,
        "sha256": mixed_sha,
        "size_bytes": mixed_path.stat().st_size,
    }


def select_exact_mixed_rows(rows: list[dict[str, Any]], mixed_source: str, source_root: Path, session_info: dict[str, Any], mixed_session_dir: Path) -> list[dict[str, Any]]:
    mixed_info = validate_mixed_source(mixed_source, source_root, session_info)
    source_sha = mixed_info["sha256"]
    mixed_rows = sorted([
        row
        for row in rows
        if row.get("source_category") == "mixed"
        and row.get("source_file_sha256") == source_sha
    ], key=lambda r: int(r.get("rep_index") or 0))
    if len(mixed_rows) != 3 or {int(r.get("rep_index") or 0) for r in mixed_rows} != {1, 2, 3}:
        raise ValueError("mixed_session_exact_match_failed")
    for row in mixed_rows:
        rep_index = int(row.get("rep_index") or 0)
        session_rep = mixed_session_dir / "repetitions" / f"rep_{rep_index:02d}.csv"
        fixed_rep = Path(str(row.get("rep_csv")))
        if sha256_file(session_rep) != sha256_file(fixed_rep) and not csv_cells_equal(session_rep, fixed_rep):
            raise ValueError(f"mixed_session_rep_cell_mismatch:rep_{rep_index:02d}")
    return mixed_rows


def mixed_comparison_rows(rows: list[dict[str, Any]], mixed_source: str | None, source_root: Path, session_info: dict[str, Any], distribution: list[dict[str, Any]], mixed_session_dir: Path) -> list[dict[str, Any]]:
    mixed_rows = select_exact_mixed_rows(rows, mixed_source or "", source_root, session_info, mixed_session_dir)
    output: list[dict[str, Any]] = []
    by_rep = {int(r.get("rep_index") or 0): r for r in mixed_rows}
    feature_names = [row["feature"] for row in distribution]
    dist_by_feature = {row["feature"]: row for row in distribution}
    for feature in feature_names:
        values = {idx: finite_number(by_rep[idx].get(feature)) for idx in (1, 2, 3)}
        dist = dist_by_feature[feature]
        output.append(
            {
                "feature_name": feature,
                "rep_01_value": values[1],
                "rep_02_value": values[2],
                "rep_03_value": values[3],
                "rep_01_minus_rep_03": None if values[1] is None or values[3] is None else values[1] - values[3],
                "rep_02_minus_rep_01": None if values[2] is None or values[1] is None else values[2] - values[1],
                "rep_02_minus_rep_03": None if values[2] is None or values[3] is None else values[2] - values[3],
                "pure_normal_mean": dist.get("normal_mean"),
                "pure_normal_std": dist.get("normal_std"),
                "pure_normal_median": dist.get("normal_median"),
                "pure_normal_q1": dist.get("normal_q1"),
                "pure_normal_q3": dist.get("normal_q3"),
                "rep_01_z_score": safe_z(values[1], dist.get("normal_mean"), dist.get("normal_std")),
                "rep_02_z_score": safe_z(values[2], dist.get("normal_mean"), dist.get("normal_std")),
                "rep_03_z_score": safe_z(values[3], dist.get("normal_mean"), dist.get("normal_std")),
                "rep_01_robust_z_score": robust_z(values[1], dist.get("normal_median"), dist.get("normal_q1"), dist.get("normal_q3")),
                "rep_02_robust_z_score": robust_z(values[2], dist.get("normal_median"), dist.get("normal_q1"), dist.get("normal_q3")),
                "rep_03_robust_z_score": robust_z(values[3], dist.get("normal_median"), dist.get("normal_q1"), dist.get("normal_q3")),
                "rep_01_normal_percentile": by_rep[1].get(f"{feature}_normal_percentile"),
                "rep_02_normal_percentile": by_rep[2].get(f"{feature}_normal_percentile"),
                "rep_03_normal_percentile": by_rep[3].get(f"{feature}_normal_percentile"),
            }
        )
    return output


def low_score_decomposition(rows: list[dict[str, Any]], mixed_rows: list[dict[str, Any]], distribution: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dist_by_feature = {row["feature"]: row for row in distribution}
    by_rep = {int(row.get("rep_index") or 0): row for row in mixed_rows}
    output: list[dict[str, Any]] = []
    for rep_index in (1, 2, 3):
        row = by_rep[rep_index]
        ranked = []
        for field in SUBSCORE_FIELDS:
            value = finite_number(row.get(field))
            dist = dist_by_feature.get(field, {})
            ranked.append(
                {
                    "name": field,
                    "value": value,
                    "pure_normal_mean": dist.get("normal_mean"),
                    "pure_normal_median": dist.get("normal_median"),
                    "difference_from_normal_mean": None if value is None or dist.get("normal_mean") is None else value - dist["normal_mean"],
                    "difference_from_normal_median": None if value is None or dist.get("normal_median") is None else value - dist["normal_median"],
                    "z_score": safe_z(value, dist.get("normal_mean"), dist.get("normal_std")),
                    "robust_z_score": robust_z(value, dist.get("normal_median"), dist.get("normal_q1"), dist.get("normal_q3")),
                    "normal_percentile": row.get(f"{field}_normal_percentile"),
                    "interpretation": "associated_with_lower_score; not_causal_proof",
                }
            )
        ranked.sort(key=lambda item: (float("inf") if item["value"] is None else item["value"]))
        output.append(
            {
                "rep_index": rep_index,
                "final_score": row.get("final_score"),
                "ground_truth_rep_label": row.get("ground_truth_rep_label"),
                "subscores_ranked_low_to_high": ranked,
                "lowest_three_subscores": ranked[:3],
                "associated_with_lower_score": True,
                "not_causal_proof": True,
            }
        )
    return output


def write_markdown_reports(
    out: Path,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    exact_mixed_rep_rows: list[dict[str, Any]],
    mixed_feature_comparison_rows: list[dict[str, Any]],
    low_score_decomposition_rows: list[dict[str, Any]],
) -> None:
    audit = [
        "# Current Detector Static Audit",
        "",
        f"- Detector: `{DETECTOR_CONTRACT['detector_function']}`",
        "- `LF` uses `TrunkForwardLean_deg >= 15` and appends a compensation type.",
        "- `TR` uses `TrunkRotation_deg >= 15` and appends a compensation type.",
        "- Upper-arm ROM, forearm rotation, wrist peaks, pauses, and EMG RMS z-score create quality findings only.",
        "- Static diagnosis: when V2.1.2 trial rows do not provide `TrunkForwardLean_deg` / `TrunkRotation_deg`, the current score-row adapter can pass zero defaults, so LF/TR cannot fire.",
    ]
    (out / "current_detector_static_audit.md").write_text("\n".join(audit) + "\n", encoding="utf-8")

    mixed_md = [
        "# Mixed Session Rep Diagnosis",
        "",
        "This report compares mixed-session repetitions without using scores to assign labels.",
        "",
        "| rep | expected label | final score | detector | non-hit reason |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for row in exact_mixed_rep_rows:
        mixed_md.append(
            f"| {row.get('rep_index')} | {row.get('ground_truth_rep_label')} | {row.get('final_score')} | {row.get('detector_detected')} | {row.get('detector_non_hit_reason')} |"
        )
    mixed_md.extend(["", "## Lowest Three Subscores", ""])
    for item in low_score_decomposition_rows:
        mixed_md.append(f"### rep_{int(item.get('rep_index')):02d}")
        for sub in item.get("lowest_three_subscores") or []:
            mixed_md.append(
                f"- {sub.get('name')}: value={sub.get('value')}, normal_mean={sub.get('pure_normal_mean')}, "
                f"normal_median={sub.get('pure_normal_median')}, z={sub.get('z_score')}, "
                f"robust_z={sub.get('robust_z_score')}, percentile={sub.get('normal_percentile')}"
            )
    mixed_md.extend(["", "## IMU2 Key Comparison", ""])
    key_features = {
        "imu2_pitch_range",
        "imu2_pitch_positive_excursion",
        "imu2_pitch_negative_excursion",
        "imu2_pitch_absolute_excursion",
        "imu2_positive_excursion_duration_ms",
        "imu2_negative_excursion_duration_ms",
        "imu2_gyro_norm_p95",
        "imu2_roll_range",
        "imu2_yaw_range",
    }
    for row in mixed_feature_comparison_rows:
        if row.get("feature_name") in key_features:
            mixed_md.append(
                f"- {row.get('feature_name')}: rep01={row.get('rep_01_value')}, rep02={row.get('rep_02_value')}, rep03={row.get('rep_03_value')}"
            )
    mixed_md.extend(["", "## Detector Conditions", ""])
    detector_rows = [row for row in mixed_feature_comparison_rows if str(row.get("feature_name", "")).startswith("detector_")]
    for row in detector_rows:
        mixed_md.append(
            f"- {row.get('feature_name')}: rep01={row.get('rep_01_value')}, rep02={row.get('rep_02_value')}, rep03={row.get('rep_03_value')}"
        )
    (out / "05_mixed_session_rep_diagnosis.md").write_text("\n".join(mixed_md) + "\n", encoding="utf-8")

    definition_md = [
        "# Feature Definition Audit",
        "",
        "- IMU1: upper arm.",
        "- IMU2: trunk / waist-back. Used here for compensation diagnostics, not as the main repetition-count signal.",
        "- IMU3: forearm.",
        "- IMU4: wrist.",
        "- EMG1: finger extensors.",
        "- EMG2: biceps.",
        "- EMG3: triceps.",
        "- EMG4: extensor carpi ulnaris.",
        "",
        "Candidate threshold-scan fields are exploratory only and are not clinical or production thresholds.",
    ]
    (out / "10_feature_definition_audit.md").write_text("\n".join(definition_md) + "\n", encoding="utf-8")

    report_md = [
        "# Compensation Feature Diagnosis V1",
        "",
        f"- Diagnosis version: `{DIAGNOSIS_VERSION}`",
        f"- Total reps: {summary.get('total_rep_count')}",
        f"- Normal reps: {summary.get('normal_count')}",
        f"- Forward-compensation reps: {summary.get('compensation_count')}",
        f"- Mixed reps: {summary.get('mixed_count')}",
        f"- Detector hit count: {summary.get('detector_hit_count')}",
        "",
        "The report is diagnostic only. It does not modify baseline files, formal templates, detector thresholds, or scoring interfaces.",
    ]
    (out / "13_diagnosis_report.md").write_text("\n".join(report_md) + "\n", encoding="utf-8")


def sha_manifest(root: Path) -> None:
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.name == "SHA256SUMS.txt":
            continue
        rel = path.relative_to(root).as_posix()
        rows.append(f"{sha256_file(path)}  {rel}")
    (root / "SHA256SUMS.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")


def assert_diagnosis_contracts(rows: list[dict[str, Any]], mixed_rows: list[dict[str, Any]]) -> None:
    counts = {
        "total_rep_count": len(rows),
        "pure_normal": sum(1 for r in rows if r.get("source_category") == "normal"),
        "pure_compensation": sum(1 for r in rows if r.get("source_category") == "compensation"),
        "mixed": sum(1 for r in rows if r.get("source_category") == "mixed"),
        "ground_truth_normal": sum(1 for r in rows if r.get("ground_truth_has_compensation") is not True),
        "ground_truth_compensation": sum(1 for r in rows if r.get("ground_truth_has_compensation") is True),
        "mixed_session_rep_count": len(mixed_rows),
        "detector_hit_count": sum(1 for r in rows if r.get("detector_detected") is True),
    }
    expected = {
        "total_rep_count": 40,
        "pure_normal": 29,
        "pure_compensation": 5,
        "mixed": 6,
        "ground_truth_normal": 33,
        "ground_truth_compensation": 7,
        "mixed_session_rep_count": 3,
        "detector_hit_count": 0,
    }
    for key, value in expected.items():
        if counts[key] != value:
            raise ValueError(f"diagnosis_contract_count_mismatch:{key}:expected={value}:actual={counts[key]}")
    by_rep = {int(r.get("rep_index") or 0): r for r in mixed_rows}
    for rep_index, expected_score in MIXED_EXPECTED_SCORES.items():
        actual = finite_number(by_rep[rep_index].get("final_score"))
        if actual is None or round(actual, 2) != round(expected_score, 2):
            raise ValueError(f"mixed_session_score_contract_mismatch:rep_{rep_index:02d}:expected={expected_score}:actual={actual}")


def generate_diagnosis(args: argparse.Namespace) -> dict[str, Any]:
    rep_root = Path(args.rep_root)
    source_root = Path(args.source_root)
    manifest_path = Path(args.manifest)
    validation_path = Path(args.validation)
    baseline_template_path = Path(args.baseline_template)
    mixed_session_dir = Path(args.mixed_session_dir)
    output_dir = Path(args.output_dir)

    tmp_dir = prepare_output_dir(output_dir, args.overwrite)
    try:
        records = load_manifest_records(manifest_path, rep_root, source_root)
        source_csv_files = source_csv_manifest(records)
        validation_scores = load_validation_scores(validation_path)
        template_task = load_task12_template(baseline_template_path)
        rows: list[dict[str, Any]] = []
        traces: list[dict[str, Any]] = []
        for record in records:
            score = validation_scores.get((record.source_filename, record.rep_index))
            row, trace = build_rep_diagnosis(record, score, template_task)
            rows.append(row)
            traces.append(trace)

        distribution = feature_distribution_summary(rows)
        add_normal_distribution_fields(rows, distribution)
        mixed_session_info = load_mixed_session_summary(mixed_session_dir)
        exact_mixed_rows = select_exact_mixed_rows(rows, args.mixed_source, source_root, mixed_session_info, mixed_session_dir)
        assert_diagnosis_contracts(rows, exact_mixed_rows)
        mixed_rows = mixed_comparison_rows(rows, args.mixed_source, source_root, mixed_session_info, distribution, mixed_session_dir)
        low_score_rows = low_score_decomposition(rows, exact_mixed_rows, distribution)
        summary = {
            "diagnosis_version": DIAGNOSIS_VERSION,
            "baseline_version": BASELINE_VERSION,
            "segmentation_version": SEGMENTATION_VERSION,
            "sensor_mapping_version": SENSOR_MAPPING_VERSION,
            "generated_at": utc_now_iso(),
            "total_rep_count": len(rows),
            "unique_source_csv_count": len(source_csv_files),
            "normal_count": sum(1 for r in rows if r.get("source_category") == "normal"),
            "compensation_count": sum(1 for r in rows if r.get("source_category") == "compensation"),
            "mixed_count": sum(1 for r in rows if r.get("source_category") == "mixed"),
            "ground_truth_normal_rep_count": sum(1 for r in rows if r.get("ground_truth_has_compensation") is not True),
            "ground_truth_compensation_rep_count": sum(1 for r in rows if r.get("ground_truth_has_compensation") is True),
            "detector_hit_count": sum(1 for r in rows if r.get("detector_detected") is True),
            "mixed_session": {
                "session_id": mixed_session_info.get("session_id"),
                "rep_01_score": MIXED_EXPECTED_SCORES[1],
                "rep_02_score": MIXED_EXPECTED_SCORES[2],
                "rep_03_score": MIXED_EXPECTED_SCORES[3],
                "integrity_match": True,
                "mixed_source_sha256": mixed_session_info.get("source_sha256"),
            },
            "threshold_modified": False,
            "production_detector_modified": False,
            "segmentation_modified": False,
            "v2_1_2_baseline_modified": False,
            "no_final_thresholds_generated": True,
            "formal_baseline_modified": False,
            "scoring_algorithm_modified": False,
        }
        integrity = {
            "manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
            "validation": {"path": str(validation_path), "sha256": sha256_file(validation_path)},
            "baseline_template": {"path": str(baseline_template_path), "sha256": sha256_file(baseline_template_path)},
            "rep_csv_count": len(rows),
            "rep_csv_sha256": [{"rep_csv": r["rep_csv"], "sha256": r["rep_csv_sha256"]} for r in rows],
            "unique_source_csv_count": len(source_csv_files),
            "source_csv_files": source_csv_files,
        }
        candidate_features = {
            "diagnosis_version": DIAGNOSIS_VERSION,
            "exploratory_only": True,
            "current_dataset_only": True,
            "not_clinically_validated": True,
            "no_final_thresholds": True,
            "features": distribution,
        }

        write_csv(tmp_dir / "01_all_40_rep_feature_diagnosis.csv", rows)
        write_json(tmp_dir / "02_all_40_rep_feature_diagnosis.json", rows)
        write_csv(tmp_dir / "03_mixed_session_rep_comparison.csv", mixed_rows)
        write_json(tmp_dir / "04_mixed_session_rep_comparison.json", mixed_rows)
        write_json(tmp_dir / "05_mixed_session_low_score_decomposition.json", low_score_rows)
        write_json(tmp_dir / "06_current_detector_contract.json", DETECTOR_CONTRACT)
        write_json(tmp_dir / "current_detector_contract.json", DETECTOR_CONTRACT)
        write_json(tmp_dir / "07_detector_condition_trace.json", traces)
        write_json(tmp_dir / "08_candidate_features_for_threshold_scan.json", candidate_features)
        write_csv(tmp_dir / "09_feature_distribution_summary.csv", distribution)
        write_json(tmp_dir / "11_input_integrity_manifest.json", integrity)
        write_json(tmp_dir / "12_diagnosis_summary.json", summary)
        write_markdown_reports(tmp_dir, rows, summary, exact_mixed_rows, mixed_rows, low_score_rows)
        sha_manifest(tmp_dir)
        finalize_output_dir(tmp_dir, output_dir, args.overwrite)
        return summary
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose drink-water compensation features per V2.1.1 repetition.")
    parser.add_argument("--rep-root", default=str(DEFAULT_REP_ROOT), help="Directory containing extracted repetition CSV files.")
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT), help="Directory containing the 22 original standard source CSV files.")
    parser.add_argument("--manifest", default=str(DEFAULT_BASELINE_DIR / "baseline_source_manifest.json"))
    parser.add_argument("--validation", default=str(DEFAULT_BASELINE_DIR / "session_score_validation_v2_1_2.json"))
    parser.add_argument("--baseline-template", default=str(DEFAULT_BASELINE_DIR / "drink_water_baseline_v2_1_2_scoring_template.json"))
    parser.add_argument("--mixed-session-dir", default=str(DEFAULT_SESSION_DIR))
    parser.add_argument("--mixed-source", default="", help="Optional source CSV path for the mixed normal-compensation-normal session.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing diagnosis output directory.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = generate_diagnosis(args)
    print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
