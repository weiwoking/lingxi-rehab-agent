"""Offline Drink Water V1 dataset indexing and segmentation tools.

This module is intentionally offline-only. It writes derived artifacts under
the scoring-assets authority and never writes to the standard dataset.
It reuses the existing drinking segmentation code in cloud_interface instead
of introducing a competing online/session scoring path.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from cloud_interface import trial_segmenter
from current_drink_segmentation_core import (
    ActionSegment,
    segment_drink_complete_cycles,
)
from scoring_assets import DERIVED_ROOT as ASSET_DERIVED_ROOT, STANDARD_ROOT


TOOL_SCHEMA_VERSION = "drink_water_v1.offline_pipeline.v1"
TOOL_VERSION = "2026.08.07-hybrid-v2.1.1-device-boundary-fix"
STANDARD_DATASET_NAME = "喝水标准动作数据集_裁剪版_校正版_20260805"
STANDARD_DATASET_ROOT = STANDARD_ROOT / STANDARD_DATASET_NAME
DERIVED_ROOT = ASSET_DERIVED_ROOT / "drink_water_v1"
MANIFEST_DIR = DERIVED_ROOT / "manifests"
BOOTSTRAP_DIR = DERIVED_ROOT / "bootstrap_template"
SEGMENTATION_DIR = DERIVED_ROOT / "segmentation_results"
EXTRACTED_REPS_DIR = DERIVED_ROOT / "extracted_repetitions"
NORMALIZED_TEMPLATE_DIR = DERIVED_ROOT / "normalized_templates"
VALIDATION_REPORT_DIR = DERIVED_ROOT / "validation_reports"
SCHEMA_DIR = DERIVED_ROOT / "schemas"

CSV_ENCODING = "utf-8-sig"
EXPECTED_SOURCE_FILE_COUNT = 22
REPETITION_INDEX_BASE = 1
END_INDEX_CONVENTION = "inclusive"
SOURCE_ROW_NUMBER_CONVENTION = (
    "zero_based_data_row_index_excluding_header; original CSV line number is index + 2"
)

ACTION_CSV_CATEGORY_PREFIXES = ("01_", "02_", "03_", "04_", "05_")
NORMAL_SINGLE_CATEGORY_PREFIX = "01_"


@dataclass(frozen=True)
class DrinkWaterV1Config:
    """Central parameters for offline Drink Water V1 derivation."""

    rest_window_seconds: float = 0.75
    min_action_duration_ms: int = 450
    max_action_duration_ms: int = 15000
    min_action_gap_ms: int = 300
    strong_quiet_min_ms: int = 450
    weak_quiet_min_ms: int = 180
    motion_valley_prominence: float = 0.28
    pose_return_threshold: float = 0.35
    merged_duration_factor: float = 1.55
    boundary_cluster_window_factor: float = 0.16
    boundary_cluster_window_min_ms: int = 550
    boundary_cluster_window_max_ms: int = 1300
    full_cycle_valid_threshold: float = 0.62
    fallback_prior_median_duration_ms: int = 7350
    fallback_prior_q1_duration_ms: int = 6800
    fallback_prior_q3_duration_ms: int = 8000
    min_cycle_duration_ms: int = 1400
    max_cycle_duration_ms: int = 9500
    min_peak_distance_ms: int = 1200
    bootstrap_normalized_length: int = 160
    bootstrap_quantile_low: float = 0.10
    bootstrap_quantile_high: float = 0.90
    motion_threshold_source: str = "hybrid_v2 upperarm_forearm_wrist composite envelope"
    valley_threshold_source: str = "existing segmenter diagnostics"
    confidence_rule: str = "hybrid v2.1 full-cycle gate score; short phases are invalid incomplete candidates"


CONFIG = DrinkWaterV1Config()

BOOTSTRAP_FIELDS = [
    "imu1_roll",
    "imu1_pitch",
    "imu1_yaw",
    "imu2_roll",
    "imu2_pitch",
    "imu2_yaw",
    "imu3_roll",
    "imu3_pitch",
    "imu3_yaw",
    "imu4_roll",
    "imu4_pitch",
    "imu4_yaw",
    "emg_rms_1",
    "emg_rms_2",
    "emg_rms_3",
    "emg_rms_4",
    "emg_mav_1",
    "emg_mav_2",
    "emg_mav_3",
    "emg_mav_4",
]

DRINK_WATER_V1_IMU_SEGMENTS = {
    "upperarm": "imu1",
    "trunk": "imu2",
    "forearm": "imu3",
    "hand_wrist": "imu4",
}

ANGLE_FIELDS = {
    f"imu{idx}_{axis}"
    for idx in range(1, 5)
    for axis in ("roll", "pitch", "yaw")
}

EMG_BASELINE_FIELDS = [
    f"emg_{feature}_{idx}"
    for feature in ("rms", "mav")
    for idx in range(1, 5)
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_output_dirs() -> None:
    for path in (
        MANIFEST_DIR,
        BOOTSTRAP_DIR,
        SEGMENTATION_DIR / "per_file",
        EXTRACTED_REPS_DIR,
        NORMALIZED_TEMPLATE_DIR,
        VALIDATION_REPORT_DIR,
        SCHEMA_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def resolve_standard_dataset_root(path: str | None = None) -> Path:
    if path:
        root = Path(path).resolve()
    else:
        root = STANDARD_DATASET_ROOT
    if not root.exists():
        raise FileNotFoundError(f"standard dataset root not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"standard dataset root is not a directory: {root}")
    return root


def json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def action_csv_files(dataset_root: Path) -> list[Path]:
    files = [
        path
        for path in dataset_root.rglob("*.csv")
        if path.parent.name.startswith(ACTION_CSV_CATEGORY_PREFIXES)
    ]
    return sorted(files, key=lambda path: path.relative_to(dataset_root).as_posix())


def category_expected_label(category: str) -> dict[str, Any]:
    if category.startswith("01_"):
        return {"category": category, "expected_label": "normal_single", "expected_repetitions": 1}
    if category.startswith("02_"):
        return {"category": category, "expected_label": "normal_three_continuous", "expected_repetitions": 3}
    if category.startswith("03_"):
        return {"category": category, "expected_label": "normal_five_continuous", "expected_repetitions": 5}
    if category.startswith("04_"):
        return {"category": category, "expected_label": "forward_compensation_single", "expected_repetitions": 1}
    if category.startswith("05_"):
        return {"category": category, "expected_label": "normal_compensation_normal", "expected_repetitions": 3}
    return {"category": category, "expected_label": "unknown", "expected_repetitions": None}


def read_standard_csv(path: Path) -> pd.DataFrame:
    return trial_segmenter.read_fused_csv(path)


def safe_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def first_last_numeric(df: pd.DataFrame, column: str) -> tuple[Any, Any]:
    if column not in df.columns or len(df) == 0:
        return None, None
    values = safe_numeric(df[column]).dropna()
    if values.empty:
        return None, None
    return value_to_json(values.iloc[0]), value_to_json(values.iloc[-1])


def value_to_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    return value


def validity_rate(df: pd.DataFrame, columns: Iterable[str]) -> float | None:
    present = [column for column in columns if column in df.columns]
    if not present or len(df) == 0:
        return None
    valid = pd.Series(False, index=df.index)
    for column in present:
        valid = valid | (safe_numeric(df[column]).fillna(0) > 0)
    return round(float(valid.mean()), 6)


def single_valid_rate(df: pd.DataFrame, column: str) -> float | None:
    if column not in df.columns or len(df) == 0:
        return None
    return round(float((safe_numeric(df[column]).fillna(0) > 0).mean()), 6)


def all_valid_rate(df: pd.DataFrame, columns: Iterable[str]) -> float | None:
    expected = list(columns)
    present = [column for column in expected if column in df.columns]
    if len(present) != len(expected) or len(df) == 0:
        return None
    valid = pd.Series(True, index=df.index)
    for column in present:
        valid = valid & (safe_numeric(df[column]).fillna(0) > 0)
    return round(float(valid.mean()), 6)


def strictly_increasing_rate(series: pd.Series) -> float | None:
    values = safe_numeric(series).dropna().to_numpy(dtype=float)
    if len(values) < 2:
        return None
    return round(float(np.mean(np.diff(values) > 0)), 6)


def sequence_continuity_metrics(df: pd.DataFrame) -> dict[str, Any]:
    metrics = {
        "log_seq_exact_continuity_rate": None,
        "log_seq_gap_event_count": None,
        "log_seq_total_gap": None,
        "log_seq_max_gap": None,
        "time_ms_nonpositive_diff_count": None,
        "time_ms_median_step_ms": None,
        "time_ms_max_step_ms": None,
    }
    if "log_seq" in df.columns:
        log_seq = safe_numeric(df["log_seq"]).dropna().to_numpy(dtype=float)
        if len(log_seq) >= 2:
            diffs = np.diff(log_seq)
            exact = diffs == 1
            positive_gaps = diffs[diffs > 1] - 1
            metrics["log_seq_exact_continuity_rate"] = round(float(np.mean(exact)), 6)
            metrics["log_seq_gap_event_count"] = int(len(positive_gaps))
            metrics["log_seq_total_gap"] = int(np.sum(positive_gaps)) if len(positive_gaps) else 0
            metrics["log_seq_max_gap"] = int(np.max(positive_gaps)) if len(positive_gaps) else 0
    if "time_ms" in df.columns:
        time_ms = safe_numeric(df["time_ms"]).dropna().to_numpy(dtype=float)
        if len(time_ms) >= 2:
            diffs = np.diff(time_ms)
            metrics["time_ms_nonpositive_diff_count"] = int(np.sum(diffs <= 0))
            metrics["time_ms_median_step_ms"] = value_to_json(round(float(np.median(diffs)), 6))
            metrics["time_ms_max_step_ms"] = value_to_json(round(float(np.max(diffs)), 6))
    return metrics


def longest_freeze_ms(df: pd.DataFrame, value_columns: list[str]) -> float | None:
    if "time_ms" not in df.columns or len(df) < 2:
        return None
    columns = [column for column in value_columns if column in df.columns]
    if not columns:
        return None
    time_ms = safe_numeric(df["time_ms"]).to_numpy(dtype=float)
    values = df[columns].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    frozen_start: int | None = None
    longest = 0.0
    for idx in range(1, len(df)):
        same = bool(np.allclose(values[idx], values[idx - 1], equal_nan=True))
        if same and frozen_start is None:
            frozen_start = idx - 1
        elif not same and frozen_start is not None:
            longest = max(longest, float(time_ms[idx - 1] - time_ms[frozen_start]))
            frozen_start = None
    if frozen_start is not None:
        longest = max(longest, float(time_ms[-1] - time_ms[frozen_start]))
    return round(longest, 3)


def quality_summary(df: pd.DataFrame) -> dict[str, Any]:
    row_count = int(len(df))
    columns = list(df.columns)
    time_start, time_end = first_last_numeric(df, "time_ms")
    log_start, log_end = first_last_numeric(df, "log_seq")
    duration_ms = None
    if isinstance(time_start, (int, float)) and isinstance(time_end, (int, float)):
        duration_ms = max(0.0, float(time_end) - float(time_start))
    numeric = df.apply(pd.to_numeric, errors="coerce")
    nan_count = int(numeric.isna().sum().sum())
    inf_count = int(np.isinf(numeric.to_numpy(dtype=float, copy=False)).sum()) if row_count else 0
    imu_rate_columns = [f"imu{i}_valid" for i in range(1, 5)]
    emg_rate_columns = ["emg_valid"]
    continuity = sequence_continuity_metrics(df)
    imu_refresh = {}
    for idx in range(1, 5):
        candidates = [f"imu{idx}_{axis}" for axis in ("roll", "pitch", "yaw", "gx", "gy", "gz")]
        present = [column for column in candidates if column in df.columns]
        if "time_ms" in df.columns and present:
            changed = df[present].apply(pd.to_numeric, errors="coerce").diff().abs().sum(axis=1) > 0
            elapsed_s = (duration_ms or 0.0) / 1000.0
            imu_refresh[f"imu{idx}_actual_refresh_hz"] = round(float(changed.sum()) / elapsed_s, 4) if elapsed_s > 0 else None
    emg_present = [f"emg_rms_{idx}" for idx in range(1, 5) if f"emg_rms_{idx}" in df.columns]
    emg_refresh_hz = None
    if "time_ms" in df.columns and emg_present:
        changed = df[emg_present].apply(pd.to_numeric, errors="coerce").diff().abs().sum(axis=1) > 0
        elapsed_s = (duration_ms or 0.0) / 1000.0
        emg_refresh_hz = round(float(changed.sum()) / elapsed_s, 4) if elapsed_s > 0 else None
    return {
        "row_count": row_count,
        "column_count": len(columns),
        "columns": columns,
        "log_seq_start": log_start,
        "log_seq_end": log_end,
        "time_ms_start": time_start,
        "time_ms_end": time_end,
        "duration_ms": round(duration_ms, 3) if duration_ms is not None else None,
        "imu1_valid_rate": single_valid_rate(df, "imu1_valid"),
        "imu2_valid_rate": single_valid_rate(df, "imu2_valid"),
        "imu3_valid_rate": single_valid_rate(df, "imu3_valid"),
        "imu4_valid_rate": single_valid_rate(df, "imu4_valid"),
        "imu_all_valid_rate": all_valid_rate(df, imu_rate_columns),
        "emg_valid_rate": validity_rate(df, emg_rate_columns),
        "time_ms_strictly_increasing_rate": strictly_increasing_rate(df["time_ms"]) if "time_ms" in df.columns else None,
        "log_seq_strictly_increasing_rate": strictly_increasing_rate(df["log_seq"]) if "log_seq" in df.columns else None,
        **continuity,
        "imu_actual_refresh": imu_refresh,
        "emg_feature_actual_refresh_hz": emg_refresh_hz,
        "max_age_ms": {
            column: value_to_json(safe_numeric(df[column]).max())
            for column in df.columns
            if column.endswith("_age_ms")
        },
        "longest_imu_freeze_ms": longest_freeze_ms(
            df,
            [f"imu{i}_{axis}" for i in range(1, 5) for axis in ("roll", "pitch", "yaw", "gx", "gy", "gz")],
        ),
        "longest_emg_freeze_ms": longest_freeze_ms(df, [f"emg_rms_{idx}" for idx in range(1, 5)]),
        "nan_count": nan_count,
        "inf_count": inf_count,
        "empty_row_count": int(df.isna().all(axis=1).sum()),
    }


def manifest_record(dataset_root: Path, csv_path: Path, reference_columns: list[str] | None) -> dict[str, Any]:
    df = read_standard_csv(csv_path)
    quality = quality_summary(df)
    label = category_expected_label(csv_path.parent.name)
    columns_match = reference_columns is None or list(df.columns) == reference_columns
    return {
        "schema_version": TOOL_SCHEMA_VERSION,
        "relative_path": csv_path.relative_to(dataset_root).as_posix(),
        "category": csv_path.parent.name,
        "filename": csv_path.name,
        "expected_label": label["expected_label"],
        "expected_repetitions": label["expected_repetitions"],
        "row_count": quality["row_count"],
        "column_count": quality["column_count"],
        "header_matches_reference": columns_match,
        "log_seq_start": quality["log_seq_start"],
        "log_seq_end": quality["log_seq_end"],
        "time_ms_start": quality["time_ms_start"],
        "time_ms_end": quality["time_ms_end"],
        "duration_ms": quality["duration_ms"],
        "sha256": sha256_file(csv_path),
        "imu1_valid_rate": quality["imu1_valid_rate"],
        "imu2_valid_rate": quality["imu2_valid_rate"],
        "imu3_valid_rate": quality["imu3_valid_rate"],
        "imu4_valid_rate": quality["imu4_valid_rate"],
        "imu_all_valid_rate": quality["imu_all_valid_rate"],
        "emg_valid_rate": quality["emg_valid_rate"],
        "quality_summary": quality,
    }


def scan_dataset(args: argparse.Namespace) -> dict[str, Any]:
    ensure_output_dirs()
    dataset_root = resolve_standard_dataset_root(args.dataset_root)
    csv_files = action_csv_files(dataset_root)
    reference_columns: list[str] | None = None
    records = []
    for csv_path in csv_files:
        df = read_standard_csv(csv_path)
        if reference_columns is None:
            reference_columns = list(df.columns)
        record = manifest_record(dataset_root, csv_path, reference_columns)
        records.append(record)
    payload = {
        "schema_version": TOOL_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "generated_at": utc_now_iso(),
        "standard_dataset_root": str(dataset_root),
        "expected_source_file_count": EXPECTED_SOURCE_FILE_COUNT,
        "source_file_count": len(records),
        "header_reference": reference_columns or [],
        "records": records,
        "notes": [
            "log_seq gaps are reported as transport ordering diagnostics only; they are not treated as independent IMU/EMG sample loss.",
            "derived artifacts are written outside the frozen standard dataset tree.",
        ],
    }
    json_dump(MANIFEST_DIR / "dataset_manifest.json", payload)
    write_manifest_csv(MANIFEST_DIR / "dataset_manifest.csv", records)
    return payload


def write_manifest_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "relative_path",
        "category",
        "filename",
        "expected_label",
        "expected_repetitions",
        "row_count",
        "column_count",
        "header_matches_reference",
        "log_seq_start",
        "log_seq_end",
        "time_ms_start",
        "time_ms_end",
        "duration_ms",
        "sha256",
        "imu1_valid_rate",
        "imu2_valid_rate",
        "imu3_valid_rate",
        "imu4_valid_rate",
        "imu_all_valid_rate",
        "emg_valid_rate",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field) for field in fields})


def load_or_scan_manifest(dataset_root: Path) -> dict[str, Any]:
    # Avoid stale manifests before first production use: each command refreshes
    # the current read-only dataset view and overwrites only derived artifacts.
    args = argparse.Namespace(dataset_root=str(dataset_root))
    return scan_dataset(args)


def time_ms_for_index(df: pd.DataFrame, index: int) -> float | None:
    if "time_ms" not in df.columns or len(df) == 0:
        return None
    value = safe_numeric(df["time_ms"]).iloc[index]
    return value_to_json(value)


def time_ms_array(df: pd.DataFrame) -> np.ndarray:
    if "time_ms" in df.columns and len(df):
        values = safe_numeric(df["time_ms"]).interpolate(limit_direction="both").to_numpy(dtype=float)
        if not np.all(np.isnan(values)):
            return values
    return np.arange(len(df), dtype=float) * 20.0


def robust_normalize(values: np.ndarray) -> np.ndarray:
    if len(values) == 0:
        return values
    clean = values[np.isfinite(values)]
    if len(clean) == 0:
        return np.zeros_like(values, dtype=float)
    low = float(np.percentile(clean, 10))
    high = float(np.percentile(clean, 90))
    spread = max(high - low, 1e-6)
    return np.clip((values - low) / spread, 0.0, 2.0)


def composite_motion_envelope(df: pd.DataFrame) -> np.ndarray:
    """Upper-arm, forearm and wrist envelope; IMU2 trunk is intentionally excluded."""

    signals: list[np.ndarray] = []
    for imu in ("imu1", "imu3", "imu4"):
        gyro_cols = [f"{imu}_g{axis}" for axis in ("x", "y", "z") if f"{imu}_g{axis}" in df.columns]
        angle_cols = [f"{imu}_{axis}" for axis in ("roll", "pitch", "yaw") if f"{imu}_{axis}" in df.columns]
        if gyro_cols:
            gyro = df[gyro_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(dtype=float)
            signals.append(np.linalg.norm(gyro, axis=1))
        if angle_cols:
            angles = np.column_stack([unwrap_degrees(df[col]) for col in angle_cols])
            delta = np.vstack([np.zeros((1, angles.shape[1])), np.diff(angles, axis=0)])
            signals.append(np.linalg.norm(delta, axis=1))
    if not signals:
        return np.zeros(len(df), dtype=float)
    envelope = np.mean([robust_normalize(signal) for signal in signals], axis=0)
    window = max(3, min(15, int(round(len(envelope) * 0.015)) or 3))
    if window % 2 == 0:
        window += 1
    kernel = np.ones(window, dtype=float) / float(window)
    return np.convolve(envelope, kernel, mode="same")


def pose_return_scores(df: pd.DataFrame, start_index: int, end_index: int) -> np.ndarray:
    fields = [
        f"imu{idx}_{axis}"
        for idx in (1, 3, 4)
        for axis in ("roll", "pitch", "yaw")
        if f"imu{idx}_{axis}" in df.columns
    ]
    if not fields or end_index < start_index:
        return np.zeros(max(0, end_index - start_index + 1), dtype=float)
    segment = df.iloc[start_index : end_index + 1]
    baseline_end = min(len(segment), max(2, int(round(CONFIG.rest_window_seconds * 50))))
    distances = []
    for field in fields:
        values = unwrap_degrees(segment[field])
        baseline = float(np.nanmedian(values[:baseline_end]))
        distances.append(np.abs(values - baseline))
    distance = np.mean(distances, axis=0)
    normalized = robust_normalize(distance)
    return 1.0 - np.clip(normalized, 0.0, 1.0)


def quiet_runs(
    envelope: np.ndarray,
    time_ms: np.ndarray,
    start_index: int,
    end_index: int,
    threshold: float,
    min_duration_ms: int,
) -> list[tuple[int, int, float]]:
    runs = []
    run_start: int | None = None
    for index in range(start_index, end_index + 1):
        is_quiet = envelope[index] <= threshold
        if is_quiet and run_start is None:
            run_start = index
        elif not is_quiet and run_start is not None:
            run_end = index - 1
            duration = float(time_ms[run_end] - time_ms[run_start]) if run_end > run_start else 0.0
            if duration >= min_duration_ms:
                runs.append((run_start, run_end, duration))
            run_start = None
    if run_start is not None:
        run_end = end_index
        duration = float(time_ms[run_end] - time_ms[run_start]) if run_end > run_start else 0.0
        if duration >= min_duration_ms:
            runs.append((run_start, run_end, duration))
    return runs


def local_peak_indices(envelope: np.ndarray, time_ms: np.ndarray, start_index: int, end_index: int) -> list[int]:
    if end_index - start_index < 3:
        return []
    high = float(np.percentile(envelope[start_index : end_index + 1], 70))
    peaks: list[int] = []
    last_peak_time = -1e12
    for index in range(start_index + 1, end_index):
        if envelope[index] < high:
            continue
        if envelope[index] >= envelope[index - 1] and envelope[index] >= envelope[index + 1]:
            if float(time_ms[index] - last_peak_time) >= CONFIG.min_peak_distance_ms:
                peaks.append(index)
                last_peak_time = float(time_ms[index])
            elif peaks and envelope[index] > envelope[peaks[-1]]:
                peaks[-1] = index
                last_peak_time = float(time_ms[index])
    return peaks


def boundary_payload(
    df: pd.DataFrame,
    index: int,
    sources: list[str],
    quiet_duration_ms: float | None,
    motion_valley_score: float | None,
    pose_return_score: float | None,
    confidence: float,
) -> dict[str, Any]:
    return {
        "boundary_index": int(index),
        "boundary_time_ms": time_ms_for_index(df, index),
        "boundary_sources": sources,
        "quiet_duration_ms": value_to_json(round(float(quiet_duration_ms), 3)) if quiet_duration_ms is not None else None,
        "motion_valley_score": value_to_json(round(float(motion_valley_score), 6)) if motion_valley_score is not None else None,
        "pose_return_score": value_to_json(round(float(pose_return_score), 6)) if pose_return_score is not None else None,
        "confidence": round(float(confidence), 6),
    }


def normalize_repetition_boundary(
    df: pd.DataFrame,
    boundary: dict[str, Any] | None,
    index: int,
    quality: float,
    edge_kind: str,
) -> dict[str, Any]:
    """Return a non-null boundary object without changing repetition indexes."""

    if boundary is not None:
        normalized = dict(boundary)
        sources = list(normalized.get("boundary_sources", []))
        if "boundary_origin" not in normalized:
            if "file_start" in sources or "file_end" in sources:
                normalized["boundary_origin"] = "file_edge"
                normalized["detected_from_signal_candidate"] = False
            elif "coarse_segment_start" in sources or "coarse_segment_end" in sources:
                normalized["boundary_origin"] = "coarse_segment_edge"
                normalized["detected_from_signal_candidate"] = False
            else:
                normalized["boundary_origin"] = "detected_candidate"
                normalized["detected_from_signal_candidate"] = True
        if "detected_from_signal_candidate" not in normalized:
            normalized["detected_from_signal_candidate"] = normalized["boundary_origin"] == "detected_candidate"
        if "selection_reason" not in normalized and normalized["boundary_origin"] in {"coarse_segment_edge", "file_edge"}:
            normalized["selection_reason"] = (
                f"accepted file {edge_kind}; no explicit local boundary candidate"
                if normalized["boundary_origin"] == "file_edge"
                else f"accepted coarse segment {edge_kind}; no explicit local boundary candidate"
            )
        return make_json_safe(normalized)

    file_edge = index <= 0 if edge_kind == "start" else index >= len(df) - 1
    source = f"file_{edge_kind}" if file_edge else f"coarse_segment_{edge_kind}"
    origin = "file_edge" if file_edge else "coarse_segment_edge"
    return {
        "boundary_index": int(index),
        "boundary_time_ms": time_ms_for_index(df, index),
        "boundary_sources": [source],
        "boundary_origin": origin,
        "detected_from_signal_candidate": False,
        "quiet_duration_ms": None,
        "motion_valley_score": None,
        "pose_return_score": None,
        "confidence": round(float(quality), 6),
        "selection_reason": (
            f"accepted file {edge_kind}; no explicit local boundary candidate"
            if file_edge
            else f"accepted coarse segment {edge_kind}; no explicit local boundary candidate"
        ),
    }


def load_cycle_duration_prior() -> dict[str, Any]:
    durations: list[float] = []
    manifest_path = BOOTSTRAP_DIR / "bootstrap_template_manifest.json"
    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            for source in payload.get("source_files", []):
                start = source.get("start_time_ms")
                end = source.get("end_time_ms")
                if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
                    durations.append(float(end) - float(start))
        except (OSError, json.JSONDecodeError, TypeError):
            durations = []
    if durations:
        q1 = float(np.percentile(durations, 25))
        median = float(np.median(durations))
        q3 = float(np.percentile(durations, 75))
        source = "bootstrap_template_manifest"
    else:
        q1 = float(CONFIG.fallback_prior_q1_duration_ms)
        median = float(CONFIG.fallback_prior_median_duration_ms)
        q3 = float(CONFIG.fallback_prior_q3_duration_ms)
        source = "config_fallback_until_bootstrap_is_generated"
    iqr = max(q3 - q1, 1.0)
    robust_min = max(1200.0, q1 - 1.5 * iqr)
    robust_max = q3 + 1.5 * iqr
    return {
        "source": source,
        "sample_count": len(durations),
        "median_duration_ms": round(median, 3),
        "q1": round(q1, 3),
        "q3": round(q3, 3),
        "iqr": round(iqr, 3),
        "robust_min_cycle_duration_ms": round(robust_min, 3),
        "robust_max_cycle_duration_ms": round(robust_max, 3),
        "merged_duration_threshold_ms": round(max(robust_max * 1.15, median * CONFIG.merged_duration_factor), 3),
        "boundary_cluster_window_ms": round(
            min(
                CONFIG.boundary_cluster_window_max_ms,
                max(CONFIG.boundary_cluster_window_min_ms, median * CONFIG.boundary_cluster_window_factor),
            ),
            3,
        ),
    }


def boundary_priority(boundary: dict[str, Any]) -> int:
    sources = set(boundary.get("boundary_sources", []))
    if "strong_quiet" in sources:
        return 4
    if "weak_quiet" in sources and "pose_return" in sources:
        return 3
    if "motion_valley_between_peaks" in sources and "pose_return" in sources:
        return 2
    if "motion_valley_between_peaks" in sources:
        return 1
    return 0


def cluster_candidate_boundaries(
    candidates: list[dict[str, Any]],
    time_ms: np.ndarray,
    prior: dict[str, Any],
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    window_ms = float(prior["boundary_cluster_window_ms"])
    sorted_candidates = sorted(candidates, key=lambda item: int(item["boundary_index"]))
    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for candidate in sorted_candidates:
        if not current:
            current = [candidate]
            continue
        last_index = int(current[-1]["boundary_index"])
        if float(time_ms[int(candidate["boundary_index"])] - time_ms[last_index]) <= window_ms:
            current.append(candidate)
        else:
            clusters.append(current)
            current = [candidate]
    if current:
        clusters.append(current)

    result = []
    for cluster in clusters:
        selected = sorted(
            cluster,
            key=lambda item: (
                -boundary_priority(item),
                -float(item.get("confidence") or 0.0),
                int(item["boundary_index"]),
            ),
        )[0]
        combined_sources = sorted({source for item in cluster for source in item.get("boundary_sources", [])})
        result.append(
            {
                "candidate_indexes": [int(item["boundary_index"]) for item in cluster],
                "candidate_payloads": make_json_safe(cluster),
                "selected_index": int(selected["boundary_index"]),
                "selected_time_ms": selected.get("boundary_time_ms"),
                "combined_sources": combined_sources,
                "quiet_duration_ms": max(
                    [float(item["quiet_duration_ms"]) for item in cluster if item.get("quiet_duration_ms") is not None],
                    default=None,
                ),
                "motion_valley_score": max(
                    [float(item["motion_valley_score"]) for item in cluster if item.get("motion_valley_score") is not None],
                    default=None,
                ),
                "pose_return_score": max(
                    [float(item["pose_return_score"]) for item in cluster if item.get("pose_return_score") is not None],
                    default=None,
                ),
                "confidence": round(max(float(item.get("confidence") or 0.0) for item in cluster), 6),
                "selection_reason": "strong_quiet_priority"
                if "strong_quiet" in selected.get("boundary_sources", [])
                else "highest_priority_then_confidence",
            }
        )
    return result


def cluster_to_boundary(cluster: dict[str, Any]) -> dict[str, Any]:
    return {
        "boundary_index": int(cluster["selected_index"]),
        "boundary_time_ms": cluster.get("selected_time_ms"),
        "boundary_sources": list(cluster.get("combined_sources", [])),
        "quiet_duration_ms": cluster.get("quiet_duration_ms"),
        "motion_valley_score": cluster.get("motion_valley_score"),
        "pose_return_score": cluster.get("pose_return_score"),
        "confidence": cluster.get("confidence"),
        "candidate_cluster": cluster,
    }


def boundary_has_reliable_cycle_split_evidence(boundary: dict[str, Any]) -> bool:
    sources = set(boundary.get("boundary_sources", []))
    if "strong_quiet" in sources or "weak_quiet" in sources:
        return True
    pose_score = boundary.get("pose_return_score")
    if "pose_return" in sources and pose_score is not None:
        try:
            return float(pose_score) >= CONFIG.pose_return_threshold
        except (TypeError, ValueError):
            return False
    return False


def internal_boundaries_for_segment(
    df: pd.DataFrame,
    start_index: int,
    end_index: int,
    envelope: np.ndarray,
    time_ms: np.ndarray,
) -> list[dict[str, Any]]:
    prior = load_cycle_duration_prior()
    duration_ms = float(time_ms[end_index] - time_ms[start_index]) if end_index > start_index else 0.0
    if duration_ms <= float(prior["merged_duration_threshold_ms"]):
        return []
    inner_start = start_index + 1
    inner_end = end_index - 1
    if inner_end <= inner_start:
        return []
    segment_envelope = envelope[start_index : end_index + 1]
    quiet_threshold = float(np.percentile(segment_envelope, 28))
    weak_threshold = float(np.percentile(segment_envelope, 40))
    boundaries: dict[int, dict[str, Any]] = {}
    pose_scores = pose_return_scores(df, start_index, end_index)

    for run_start, run_end, quiet_duration in quiet_runs(
        envelope,
        time_ms,
        inner_start,
        inner_end,
        quiet_threshold,
        CONFIG.strong_quiet_min_ms,
    ):
        boundary_index = int(round((run_start + run_end) / 2))
        local_pose = float(pose_scores[boundary_index - start_index]) if len(pose_scores) else None
        boundaries[boundary_index] = boundary_payload(
            df,
            boundary_index,
            ["strong_quiet"],
            quiet_duration,
            1.0 - float(envelope[boundary_index]),
            local_pose,
            0.92,
        )

    peaks = local_peak_indices(envelope, time_ms, start_index, end_index)
    for left, right in zip(peaks, peaks[1:]):
        if float(time_ms[right] - time_ms[left]) < CONFIG.min_peak_distance_ms:
            continue
        valley_slice = envelope[left : right + 1]
        valley_offset = int(np.argmin(valley_slice))
        boundary_index = left + valley_offset
        if boundary_index <= inner_start or boundary_index >= inner_end:
            continue
        peak_level = max(float(envelope[left]), float(envelope[right]), 1e-6)
        valley_score = max(0.0, (peak_level - float(envelope[boundary_index])) / peak_level)
        if valley_score < CONFIG.motion_valley_prominence:
            continue
        local_pose = float(pose_scores[boundary_index - start_index]) if len(pose_scores) else None
        sources = ["motion_valley_between_peaks"]
        confidence = 0.72
        if local_pose is not None and local_pose >= CONFIG.pose_return_threshold:
            sources.append("pose_return")
            confidence = 0.82
        boundaries[boundary_index] = boundary_payload(
            df,
            boundary_index,
            sources,
            None,
            valley_score,
            local_pose,
            confidence,
        )

    for run_start, run_end, quiet_duration in quiet_runs(
        envelope,
        time_ms,
        inner_start,
        inner_end,
        weak_threshold,
        CONFIG.weak_quiet_min_ms,
    ):
        boundary_index = int(round((run_start + run_end) / 2))
        if boundary_index in boundaries:
            continue
        local_pose = float(pose_scores[boundary_index - start_index]) if len(pose_scores) else None
        if local_pose is None or local_pose < CONFIG.pose_return_threshold:
            continue
        boundaries[boundary_index] = boundary_payload(
            df,
            boundary_index,
            ["weak_quiet", "pose_return"],
            quiet_duration,
            1.0 - float(envelope[boundary_index]),
            local_pose,
            0.76,
        )

    candidates = sorted(boundaries.values(), key=lambda item: item["boundary_index"])
    clusters = cluster_candidate_boundaries(candidates, time_ms, prior)
    clustered = [cluster_to_boundary(cluster) for cluster in clusters]
    return [boundary for boundary in clustered if boundary_has_reliable_cycle_split_evidence(boundary)]


def split_segment_by_boundaries(
    segment: ActionSegment,
    boundaries: list[dict[str, Any]],
    time_ms: np.ndarray,
) -> list[ActionSegment]:
    if not boundaries:
        return [segment]
    indices = [segment.start_index] + [int(item["boundary_index"]) for item in boundaries] + [segment.end_index]
    segments = []
    action_index = segment.action_index
    for left, right in zip(indices, indices[1:]):
        start = int(left)
        end = int(right)
        if start != segment.start_index:
            start += 1
        if end <= start:
            continue
        duration = float(time_ms[end] - time_ms[start])
        if duration < CONFIG.min_cycle_duration_ms:
            continue
        segments.append(
            ActionSegment(
                action_index=action_index,
                start_index=start,
                end_index=end,
                start_time_s=float(time_ms[start]) / 1000.0,
                end_time_s=float(time_ms[end]) / 1000.0,
            )
        )
        action_index += 1
    return segments or [segment]


def score_duration(duration_ms: float, prior: dict[str, Any]) -> float:
    median = float(prior["median_duration_ms"])
    robust_min = float(prior["robust_min_cycle_duration_ms"])
    robust_max = float(prior["robust_max_cycle_duration_ms"])
    if duration_ms <= 0:
        return 0.0
    if robust_min <= duration_ms <= robust_max:
        return 1.0 - min(0.45, abs(duration_ms - median) / max(median, 1.0) * 0.65)
    if duration_ms < robust_min:
        return max(0.0, duration_ms / max(robust_min, 1.0) * 0.45)
    return max(0.0, 1.0 - (duration_ms - robust_max) / max(robust_max, 1.0))


def boundary_quality(boundary: dict[str, Any] | None) -> float:
    if boundary is None:
        return 0.35
    return float(boundary.get("confidence") or 0.0)


def full_cycle_quality(
    df: pd.DataFrame,
    start_index: int,
    end_index: int,
    envelope: np.ndarray,
    time_ms: np.ndarray,
    start_boundary: dict[str, Any] | None,
    end_boundary: dict[str, Any] | None,
    prior: dict[str, Any],
) -> dict[str, Any]:
    duration_ms = float(time_ms[end_index] - time_ms[start_index]) if end_index > start_index else 0.0
    local_envelope = envelope[start_index : end_index + 1]
    if len(local_envelope) == 0:
        local_envelope = np.asarray([0.0])
    baseline_level = float(np.percentile(envelope, 20)) if len(envelope) else 0.0
    peak_level = float(np.max(local_envelope))
    departure_score = float(np.clip((peak_level - baseline_level) / max(float(np.percentile(envelope, 90)) - baseline_level, 1e-6), 0.0, 1.0))
    peak_score = float(np.clip(peak_level / max(float(np.percentile(envelope, 90)), 1e-6), 0.0, 1.0))
    pose_scores = pose_return_scores(df, start_index, end_index)
    return_score = float(pose_scores[-1]) if len(pose_scores) else 0.0
    active_threshold = baseline_level + 0.25 * max(float(np.percentile(envelope, 90)) - baseline_level, 1e-6)
    activity_coverage = float(np.mean(local_envelope > active_threshold))
    duration_score = score_duration(duration_ms, prior)
    start_boundary_quality = boundary_quality(start_boundary)
    end_boundary_quality = boundary_quality(end_boundary)
    full_score = (
        0.24 * duration_score
        + 0.17 * departure_score
        + 0.17 * peak_score
        + 0.16 * return_score
        + 0.11 * start_boundary_quality
        + 0.11 * end_boundary_quality
        + 0.04 * activity_coverage
    )
    valid = (
        full_score >= CONFIG.full_cycle_valid_threshold
        and duration_score >= 0.55
        and departure_score >= 0.25
        and peak_score >= 0.30
    )
    invalid_reason = None if valid else "not_a_complete_drink_cycle"
    return {
        "full_cycle_score": round(float(full_score), 6),
        "duration_score": round(float(duration_score), 6),
        "departure_score": round(float(departure_score), 6),
        "peak_score": round(float(peak_score), 6),
        "return_score": round(float(return_score), 6),
        "start_boundary_quality": round(float(start_boundary_quality), 6),
        "end_boundary_quality": round(float(end_boundary_quality), 6),
        "activity_coverage": round(float(activity_coverage), 6),
        "valid": bool(valid),
        "invalid_reason": invalid_reason,
        "incomplete_candidate": not valid and duration_ms < float(prior["robust_min_cycle_duration_ms"]),
        "merged_candidate": not valid and duration_ms > float(prior["robust_max_cycle_duration_ms"]),
        "confidence": round(float(full_score), 6),
        "duration_ms": round(float(duration_ms), 3),
    }


def choose_complete_cycles_global(
    df: pd.DataFrame,
    base_segment: ActionSegment,
    boundaries: list[dict[str, Any]],
    envelope: np.ndarray,
    time_ms: np.ndarray,
    prior: dict[str, Any],
) -> tuple[list[ActionSegment], dict[tuple[int, int], dict[str, Any]], list[dict[str, Any]]]:
    points = [
        boundary_payload(df, base_segment.start_index, ["coarse_segment_start"], None, None, None, 0.55),
        *boundaries,
        boundary_payload(df, base_segment.end_index, ["coarse_segment_end"], None, None, None, 0.55),
    ]
    points = sorted(points, key=lambda item: int(item["boundary_index"]))
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for left_idx, left in enumerate(points[:-1]):
        for right_idx in range(left_idx + 1, len(points)):
            right = points[right_idx]
            start = int(left["boundary_index"])
            end = int(right["boundary_index"])
            if start != base_segment.start_index:
                start += 1
            if end <= start:
                continue
            quality = full_cycle_quality(df, start, end, envelope, time_ms, left, right, prior)
            item = {
                "start_index": start,
                "end_index": end,
                "start_boundary": left,
                "end_boundary": right,
                "full_cycle_quality": quality,
            }
            if quality["valid"]:
                candidates.append(item)
            else:
                rejected.append({**item, "reject_reason": quality["invalid_reason"]})

    candidates = sorted(candidates, key=lambda item: (item["end_index"], item["start_index"]))
    n = len(candidates)
    best_score = [0.0] * (n + 1)
    keep = [False] * n
    previous = [0] * n
    for i, candidate in enumerate(candidates):
        prev = 0
        for j in range(i - 1, -1, -1):
            if candidates[j]["end_index"] < candidate["start_index"]:
                prev = j + 1
                break
        previous[i] = prev
        score_with = best_score[prev] + float(candidate["full_cycle_quality"]["full_cycle_score"])
        score_without = best_score[i]
        if score_with > score_without:
            best_score[i + 1] = score_with
            keep[i] = True
        else:
            best_score[i + 1] = score_without

    selected: list[dict[str, Any]] = []
    i = n
    while i > 0:
        if keep[i - 1] and best_score[i] > best_score[i - 1]:
            selected.append(candidates[i - 1])
            i = previous[i - 1]
        else:
            i -= 1
    selected.reverse()

    if not selected:
        quality = full_cycle_quality(
            df,
            base_segment.start_index,
            base_segment.end_index,
            envelope,
            time_ms,
            points[0],
            points[-1],
            prior,
        )
        quality["valid"] = False
        quality["invalid_reason"] = "merged_segment_without_trustworthy_complete_cycle_split"
        quality["merged_candidate"] = True
        segment = ActionSegment(
            action_index=base_segment.action_index,
            start_index=base_segment.start_index,
            end_index=base_segment.end_index,
            start_time_s=base_segment.start_time_s,
            end_time_s=base_segment.end_time_s,
        )
        return [segment], {
            (segment.start_index, segment.end_index): {
                "start_boundary": points[0],
                "end_boundary": points[-1],
                "local_supporting_candidates": boundaries,
                "full_cycle_quality": quality,
            }
        }, rejected

    segments = []
    metadata: dict[tuple[int, int], dict[str, Any]] = {}
    for index, item in enumerate(selected, start=base_segment.action_index):
        segment = ActionSegment(
            action_index=index,
            start_index=int(item["start_index"]),
            end_index=int(item["end_index"]),
            start_time_s=float(time_ms[int(item["start_index"])]) / 1000.0,
            end_time_s=float(time_ms[int(item["end_index"])]) / 1000.0,
        )
        segments.append(segment)
        metadata[(segment.start_index, segment.end_index)] = {
            "start_boundary": item["start_boundary"],
            "end_boundary": item["end_boundary"],
            "local_supporting_candidates": [
                boundary for boundary in boundaries
                if segment.start_index <= int(boundary["boundary_index"]) <= segment.end_index
            ],
            "full_cycle_quality": item["full_cycle_quality"],
        }
    return segments, metadata, rejected


def hybrid_segment_drink_v2(
    df: pd.DataFrame,
    base_segments: list[ActionSegment],
    base_diagnostic: dict[str, Any],
) -> tuple[list[ActionSegment], dict[str, Any], dict[tuple[int, int], dict[str, Any]]]:
    time_ms = time_ms_array(df)
    envelope = composite_motion_envelope(df)
    prior = load_cycle_duration_prior()
    final_segments: list[ActionSegment] = []
    metadata_by_segment_key: dict[tuple[int, int], dict[str, Any]] = {}
    all_boundaries: list[dict[str, Any]] = []
    selected_cycle_candidates: list[dict[str, Any]] = []
    rejected_candidates: list[dict[str, Any]] = []
    for base_segment in base_segments:
        boundaries = internal_boundaries_for_segment(
            df,
            base_segment.start_index,
            base_segment.end_index,
            envelope,
            time_ms,
        )
        if boundaries:
            split_segments, segment_metadata, rejected = choose_complete_cycles_global(
                df,
                base_segment,
                boundaries,
                envelope,
                time_ms,
                prior,
            )
            metadata_by_segment_key.update(segment_metadata)
            selected_cycle_candidates.extend(
                {
                    "start_index": key[0],
                    "end_index": key[1],
                    "start_boundary": value.get("start_boundary"),
                    "end_boundary": value.get("end_boundary"),
                    "full_cycle_quality": value.get("full_cycle_quality"),
                }
                for key, value in segment_metadata.items()
            )
            rejected_candidates.extend(rejected)
        else:
            split_segments = [base_segment]
            quality = full_cycle_quality(
                df,
                base_segment.start_index,
                base_segment.end_index,
                envelope,
                time_ms,
                None,
                None,
                prior,
            )
            metadata_by_segment_key[(base_segment.start_index, base_segment.end_index)] = {
                "start_boundary": None,
                "end_boundary": None,
                "local_supporting_candidates": [],
                "full_cycle_quality": quality,
            }
            selected_cycle_candidates.append(
                {
                    "start_index": base_segment.start_index,
                    "end_index": base_segment.end_index,
                    "start_boundary": None,
                    "end_boundary": None,
                    "full_cycle_quality": quality,
                }
            )
        final_segments.extend(split_segments)
        all_boundaries.extend(boundaries)
    for index, segment in enumerate(final_segments, start=1):
        segment.action_index = index
    diagnostic = {
        **make_json_safe(base_diagnostic),
        "method": "drink_hybrid_segmentation_v2_1_1",
        "base_method": base_diagnostic.get("method"),
        "base_repetition_count": len(base_segments),
        "base_segments": [
            {
                "action_index": int(segment.action_index),
                "start_index": int(segment.start_index),
                "end_index": int(segment.end_index),
                "start_time_s": round(float(segment.start_time_s), 6),
                "end_time_s": round(float(segment.end_time_s), 6),
            }
            for segment in base_segments
        ],
        "repetition_count": len(final_segments),
        "hybrid_v2_enabled": True,
        "imu_mapping": DRINK_WATER_V1_IMU_SEGMENTS,
        "motion_envelope_sources": ["imu1_upperarm", "imu3_forearm", "imu4_wrist"],
        "trunk_imu_role": "imu2_trunk_quality_only_not_primary_cycle_signal",
        "boundary_count": len(all_boundaries),
        "boundaries": all_boundaries,
        "boundary_clusters": [
            boundary.get("candidate_cluster")
            for boundary in all_boundaries
            if boundary.get("candidate_cluster") is not None
        ],
        "cycle_candidates": make_json_safe(selected_cycle_candidates + rejected_candidates),
        "global_selection": make_json_safe(selected_cycle_candidates),
        "cycle_duration_prior": prior,
        "rejected_cycle_candidates": make_json_safe(rejected_candidates),
        "config": {
            "strong_quiet_min_ms": CONFIG.strong_quiet_min_ms,
            "weak_quiet_min_ms": CONFIG.weak_quiet_min_ms,
            "motion_valley_prominence": CONFIG.motion_valley_prominence,
            "pose_return_threshold": CONFIG.pose_return_threshold,
            "merged_duration_factor": CONFIG.merged_duration_factor,
            "min_cycle_duration_ms": CONFIG.min_cycle_duration_ms,
            "max_cycle_duration_ms": CONFIG.max_cycle_duration_ms,
            "min_peak_distance_ms": CONFIG.min_peak_distance_ms,
            "boundary_cluster_window_factor": CONFIG.boundary_cluster_window_factor,
            "full_cycle_valid_threshold": CONFIG.full_cycle_valid_threshold,
        },
    }
    return final_segments, diagnostic, metadata_by_segment_key


def convert_action_segment(
    segment: ActionSegment,
    df: pd.DataFrame,
    source: str,
    method: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    start_time = time_ms_for_index(df, segment.start_index)
    end_time = time_ms_for_index(df, segment.end_index)
    duration = None
    if isinstance(start_time, (int, float)) and isinstance(end_time, (int, float)):
        duration = max(0.0, float(end_time) - float(start_time))
    metadata = metadata or {}
    full_cycle = metadata.get("full_cycle_quality")
    if not isinstance(full_cycle, dict):
        time_ms = time_ms_array(df)
        envelope = composite_motion_envelope(df)
        full_cycle = full_cycle_quality(
            df,
            segment.start_index,
            segment.end_index,
            envelope,
            time_ms,
            metadata.get("start_boundary"),
            metadata.get("end_boundary"),
            load_cycle_duration_prior(),
        )
    valid = bool(full_cycle.get("valid"))
    start_boundary = normalize_repetition_boundary(
        df,
        metadata.get("start_boundary"),
        segment.start_index,
        float(full_cycle.get("start_boundary_quality") or boundary_quality(metadata.get("start_boundary"))),
        "start",
    )
    end_boundary = normalize_repetition_boundary(
        df,
        metadata.get("end_boundary"),
        segment.end_index,
        float(full_cycle.get("end_boundary_quality") or boundary_quality(metadata.get("end_boundary"))),
        "end",
    )
    return {
        "rep_index": int(segment.action_index),
        "start_index": int(segment.start_index),
        "end_index": int(segment.end_index),
        "index_convention": END_INDEX_CONVENTION,
        "source_row_number_convention": SOURCE_ROW_NUMBER_CONVENTION,
        "start_time_ms": start_time,
        "end_time_ms": end_time,
        "duration_ms": round(float(duration), 3) if duration is not None else None,
        "valid": bool(valid),
        "invalid_reason": full_cycle.get("invalid_reason"),
        "confidence": round(float(full_cycle.get("confidence") or 0.0), 6),
        "action_source": source,
        "split_method": method,
        "merged_candidate": full_cycle.get("merged_candidate"),
        "incomplete_candidate": full_cycle.get("incomplete_candidate"),
        "candidate_assessment": "full_cycle_gate_v2_1",
        "start_boundary": start_boundary,
        "end_boundary": end_boundary,
        "local_supporting_candidates": make_json_safe(metadata.get("local_supporting_candidates", [])),
        "full_cycle_quality": make_json_safe(full_cycle),
        "quality": quality_summary(df.iloc[segment.start_index : segment.end_index + 1].copy()),
    }


def segment_file(csv_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any], pd.DataFrame]:
    df = read_standard_csv(csv_path)
    segments, diagnostic = segment_drink_complete_cycles(df, DRINK_WATER_V1_IMU_SEGMENTS)
    source = "complete_cycle"
    method = str(diagnostic.get("method") or "drink_complete_cycle_dynamic_rest_v1")
    if not segments:
        legacy_trials, legacy_rejected, legacy_info = trial_segmenter.segment_trials(df)
        segments = [
            ActionSegment(
                action_index=index + 1,
                start_index=int(trial["start_idx"]),
                end_index=int(trial["end_idx"]),
                start_time_s=float(trial["start_time_ms"]) / 1000.0,
                end_time_s=float(trial["end_time_ms"]) / 1000.0,
            )
            for index, trial in enumerate(legacy_trials)
        ]
        diagnostic = {
            "method": "trial_segmenter.segment_trials",
            "fallback_used": False,
            "legacy_rejected": legacy_rejected,
            "legacy_info": legacy_info,
            "repetition_count": len(segments),
        }
        source = "legacy_threshold"
        method = "threshold"
    segments, diagnostic, boundaries_by_segment_key = hybrid_segment_drink_v2(df, segments, diagnostic)
    method = "drink_hybrid_segmentation_v2_1_1"
    repetitions = []
    for segment in segments:
        metadata = boundaries_by_segment_key.get((segment.start_index, segment.end_index), {})
        rep = convert_action_segment(segment, df, source=source, method=method, metadata=metadata)
        repetitions.append(rep)
    return repetitions, diagnostic, df


def detect_compensation_for_repetition(df: pd.DataFrame, rep: dict[str, Any]) -> dict[str, Any]:
    """Phase-1 placeholder for compensation metadata.

    The first offline pipeline pass must not construct a self-referential
    template from the current action and present that as a compensation result.
    """

    return {
        "status": "not_evaluated_in_phase_1",
        "reason": "baseline-backed compensation validation is deferred",
    }


def segment_dataset(args: argparse.Namespace) -> dict[str, Any]:
    ensure_output_dirs()
    dataset_root = resolve_standard_dataset_root(args.dataset_root)
    manifest = load_or_scan_manifest(dataset_root)
    summary_records = []
    focus_records = []
    per_file_dir = SEGMENTATION_DIR / "per_file"
    for record in manifest["records"]:
        csv_path = dataset_root / record["relative_path"]
        repetitions, diagnostic, df = segment_file(csv_path)
        for rep in repetitions:
            rep["compensation"] = detect_compensation_for_repetition(df, rep)
        candidate_count = len(repetitions)
        valid_count = sum(1 for rep in repetitions if rep["valid"])
        invalid_count = candidate_count - valid_count
        expected_count = record.get("expected_repetitions")
        per_file_payload = {
            "schema_version": TOOL_SCHEMA_VERSION,
            "tool_version": TOOL_VERSION,
            "generated_at": utc_now_iso(),
            "source": {
                "relative_path": record["relative_path"],
                "filename": record["filename"],
                "sha256": record["sha256"],
                "expected_label": record["expected_label"],
                "expected_repetitions": expected_count,
            },
            "candidate_segment_count": candidate_count,
            "valid_repetition_count": valid_count,
            "invalid_segment_count": invalid_count,
            "detected_repetition_count": valid_count,
            "repetitions": repetitions,
            "boundaries": make_json_safe(diagnostic.get("boundaries", [])),
            "diagnostic": make_json_safe(diagnostic),
            "quality_summary": record.get("quality_summary", {}),
            "matches_expected_count": expected_count is None or valid_count == expected_count,
        }
        json_dump(per_file_dir / f"{Path(record['filename']).stem}.json", per_file_payload)
        if not per_file_payload["matches_expected_count"]:
            focus_records.append(
                {
                    "relative_path": record["relative_path"],
                    "expected_repetitions": expected_count,
                    "final_valid_repetition_count": valid_count,
                    "candidate_segment_count": candidate_count,
                    "coarse_segments": diagnostic.get("base_segments", []),
                    "all_candidate_boundaries": make_json_safe(diagnostic.get("boundaries", [])),
                    "boundary_clusters": make_json_safe(diagnostic.get("boundary_clusters", [])),
                    "complete_cycle_candidates": make_json_safe(diagnostic.get("cycle_candidates", [])),
                    "global_selection": make_json_safe(diagnostic.get("global_selection", [])),
                    "rejected_candidates": make_json_safe(diagnostic.get("rejected_cycle_candidates", [])),
                    "final_repetitions": make_json_safe(repetitions),
                }
            )
        summary_records.append(
            {
                "relative_path": record["relative_path"],
                "expected_label": record["expected_label"],
                "expected_repetitions": expected_count,
                "candidate_segment_count": candidate_count,
                "valid_repetition_count": valid_count,
                "invalid_segment_count": invalid_count,
                "detected_repetition_count": valid_count,
                "matches_expected_count": per_file_payload["matches_expected_count"],
                "split_method": repetitions[0]["split_method"] if repetitions else diagnostic.get("method"),
                "fallback_used": bool(diagnostic.get("fallback_used", False)),
            }
        )
    payload = {
        "schema_version": TOOL_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "generated_at": utc_now_iso(),
        "standard_dataset_root": str(dataset_root),
        "records": summary_records,
        "expected_total_repetitions": 40,
        "candidate_total": sum(int(item["candidate_segment_count"]) for item in summary_records),
        "valid_total": sum(int(item["valid_repetition_count"]) for item in summary_records),
        "invalid_total": sum(int(item["invalid_segment_count"]) for item in summary_records),
        "detected_total_repetitions": sum(int(item["detected_repetition_count"]) for item in summary_records),
    }
    json_dump(SEGMENTATION_DIR / "segmentation_summary.json", payload)
    write_segmentation_report(payload)
    write_hybrid_v2_focus_diagnostics(payload, focus_records)
    write_repetition_schema()
    return payload


def make_json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): make_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [make_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [make_json_safe(item) for item in value]
    return value_to_json(value)


def write_segmentation_report(payload: dict[str, Any]) -> None:
    lines = [
        "# Drink Water V1 Segmentation Report",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- expected_total_repetitions: {payload['expected_total_repetitions']}",
        f"- candidate_total: {payload['candidate_total']}",
        f"- valid_total: {payload['valid_total']}",
        f"- invalid_total: {payload['invalid_total']}",
        f"- detected_total_repetitions: {payload['detected_total_repetitions']}",
        "",
        "| file | expected | candidates | valid | invalid | detected | match | method | fallback |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for item in payload["records"]:
        lines.append(
            f"| {item['relative_path']} | {item['expected_repetitions']} | "
            f"{item['candidate_segment_count']} | {item['valid_repetition_count']} | "
            f"{item['invalid_segment_count']} | "
            f"{item['detected_repetition_count']} | {item['matches_expected_count']} | "
            f"{item['split_method']} | {item['fallback_used']} |"
        )
    (SEGMENTATION_DIR / "segmentation_report.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def write_hybrid_v2_focus_diagnostics(
    payload: dict[str, Any],
    focus_records: list[dict[str, Any]] | None = None,
) -> None:
    focus_records = focus_records or []
    diagnostic = {
        "schema_version": TOOL_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "generated_at": payload["generated_at"],
        "purpose": "hybrid_v2_1_focus_diagnostics_for_mismatched_files",
        "selection_rule": "records whose valid_repetition_count does not match expected_repetitions after hybrid v2.1",
        "failure_count": len(focus_records),
        "records": focus_records,
        "notes": [
            "This file is always generated; when all files match it contains an empty records array and failure_count=0.",
            "No folder name, expected_repetitions value, or total duration is used to force segmentation boundaries.",
        ],
    }
    json_dump(SEGMENTATION_DIR / "hybrid_v2_focus_diagnostics.json", diagnostic)


def write_repetition_schema() -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "rehab.score.repetition.v1",
        "title": "Drink Water V1 Repetition",
        "type": "object",
        "required": [
            "rep_index",
            "start_index",
            "end_index",
            "start_time_ms",
            "end_time_ms",
            "duration_ms",
            "valid",
            "invalid_reason",
            "confidence",
            "action_source",
            "split_method",
            "merged_candidate",
            "incomplete_candidate",
            "candidate_assessment",
            "start_boundary",
            "end_boundary",
            "local_supporting_candidates",
            "full_cycle_quality",
            "quality",
        ],
        "properties": {
            "rep_index": {"type": "integer", "minimum": 1},
            "start_index": {"type": "integer", "minimum": 0},
            "end_index": {"type": "integer", "minimum": 0, "description": END_INDEX_CONVENTION},
            "start_time_ms": {"type": ["number", "null"]},
            "end_time_ms": {"type": ["number", "null"]},
            "duration_ms": {"type": ["number", "null"]},
            "valid": {"type": "boolean"},
            "invalid_reason": {"type": ["string", "null"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "action_source": {"enum": ["complete_cycle", "legacy_threshold", "fallback", "manual"]},
            "split_method": {"type": "string"},
            "merged_candidate": {"type": ["boolean", "null"]},
            "incomplete_candidate": {"type": ["boolean", "null"]},
            "candidate_assessment": {"type": "string"},
            "start_boundary": {
                "type": "object",
                "required": [
                    "boundary_index",
                    "boundary_time_ms",
                    "boundary_sources",
                    "boundary_origin",
                    "detected_from_signal_candidate",
                    "quiet_duration_ms",
                    "motion_valley_score",
                    "pose_return_score",
                    "confidence",
                ],
                "properties": {
                    "boundary_index": {"type": "integer", "minimum": 0},
                    "boundary_time_ms": {"type": ["number", "null"]},
                    "boundary_sources": {"type": "array", "items": {"type": "string"}},
                    "boundary_origin": {
                        "enum": ["detected_candidate", "coarse_segment_edge", "file_edge"],
                    },
                    "detected_from_signal_candidate": {"type": "boolean"},
                    "quiet_duration_ms": {"type": ["number", "null"]},
                    "motion_valley_score": {"type": ["number", "null"]},
                    "pose_return_score": {"type": ["number", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "selection_reason": {"type": "string"},
                },
            },
            "end_boundary": {
                "type": "object",
                "required": [
                    "boundary_index",
                    "boundary_time_ms",
                    "boundary_sources",
                    "boundary_origin",
                    "detected_from_signal_candidate",
                    "quiet_duration_ms",
                    "motion_valley_score",
                    "pose_return_score",
                    "confidence",
                ],
                "properties": {
                    "boundary_index": {"type": "integer", "minimum": 0},
                    "boundary_time_ms": {"type": ["number", "null"]},
                    "boundary_sources": {"type": "array", "items": {"type": "string"}},
                    "boundary_origin": {
                        "enum": ["detected_candidate", "coarse_segment_edge", "file_edge"],
                    },
                    "detected_from_signal_candidate": {"type": "boolean"},
                    "quiet_duration_ms": {"type": ["number", "null"]},
                    "motion_valley_score": {"type": ["number", "null"]},
                    "pose_return_score": {"type": ["number", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "selection_reason": {"type": "string"},
                },
            },
            "local_supporting_candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "boundary_index",
                        "boundary_time_ms",
                        "boundary_sources",
                        "quiet_duration_ms",
                        "motion_valley_score",
                        "pose_return_score",
                        "confidence",
                    ],
                },
            },
            "full_cycle_quality": {
                "type": "object",
                "required": [
                    "full_cycle_score",
                    "duration_score",
                    "departure_score",
                    "peak_score",
                    "return_score",
                    "valid",
                    "invalid_reason",
                    "incomplete_candidate",
                    "merged_candidate",
                    "confidence",
                ],
            },
            "quality": {"type": "object"},
        },
    }
    json_dump(SCHEMA_DIR / "repetition.schema.json", schema)


def unwrap_degrees(values: Iterable[Any]) -> np.ndarray:
    numeric = pd.Series(values).pipe(safe_numeric).interpolate(limit_direction="both").to_numpy(dtype=float)
    if np.all(np.isnan(numeric)):
        return numeric
    return np.rad2deg(np.unwrap(np.deg2rad(numeric)))


def normalize_frame(df: pd.DataFrame, fields: list[str], length: int) -> dict[str, list[float | None]]:
    if len(df) == 0:
        return {field: [] for field in fields}
    x_old = np.linspace(0.0, 1.0, num=len(df))
    x_new = np.linspace(0.0, 1.0, num=length)
    normalized: dict[str, list[float | None]] = {}
    for field in fields:
        if field not in df.columns:
            normalized[field] = [None] * length
            continue
        if field in ANGLE_FIELDS:
            values = unwrap_degrees(df[field])
        else:
            values = safe_numeric(df[field]).interpolate(limit_direction="both").to_numpy(dtype=float)
        if np.all(np.isnan(values)):
            normalized[field] = [None] * length
            continue
        normalized[field] = [round(float(value), 6) for value in np.interp(x_new, x_old, values)]
    return normalized


def baseline_window_for_rep(df: pd.DataFrame, rep: dict[str, Any]) -> pd.DataFrame:
    start_index = int(rep["start_index"])
    if start_index <= 0:
        return df.iloc[:0].copy()
    start_time = rep.get("start_time_ms")
    if isinstance(start_time, (int, float)) and "time_ms" in df.columns:
        lower = float(start_time) - CONFIG.rest_window_seconds * 1000.0
        time_ms = safe_numeric(df["time_ms"])
        window = df.loc[(time_ms >= lower) & (time_ms < float(start_time))].copy()
        if len(window) > 0:
            return window
    return df.iloc[:start_index].tail(max(1, int(round(CONFIG.rest_window_seconds * 50)))).copy()


def baseline_profile(df: pd.DataFrame, rep: dict[str, Any]) -> dict[str, Any]:
    rest_df = baseline_window_for_rep(df, rep)
    profile: dict[str, Any] = {
        "rows": int(len(rest_df)),
        "time_ms_start": time_ms_for_index(rest_df.reset_index(drop=True), 0) if len(rest_df) else None,
        "time_ms_end": time_ms_for_index(rest_df.reset_index(drop=True), len(rest_df) - 1) if len(rest_df) else None,
        "imu_angle_unit": "degree",
        "angle_processing": "unwrap_degrees_then_median_baseline",
        "imu_angle_median": {},
        "emg_rest_median": {},
    }
    for field in sorted(ANGLE_FIELDS):
        if field in rest_df.columns and len(rest_df):
            profile["imu_angle_median"][field] = value_to_json(round(float(np.nanmedian(unwrap_degrees(rest_df[field]))), 6))
    for field in EMG_BASELINE_FIELDS:
        if field in rest_df.columns and len(rest_df):
            profile["emg_rest_median"][field] = value_to_json(round(float(np.nanmedian(safe_numeric(rest_df[field]))), 6))
    return profile


def baseline_relative_action_frame(action_df: pd.DataFrame, baseline: dict[str, Any]) -> pd.DataFrame:
    relative = action_df.copy()
    angle_median = baseline.get("imu_angle_median", {})
    emg_median = baseline.get("emg_rest_median", {})
    for field in BOOTSTRAP_FIELDS:
        if field not in relative.columns:
            continue
        if field in ANGLE_FIELDS:
            baseline_value = angle_median.get(field)
            unwrapped = unwrap_degrees(relative[field])
            relative[field] = unwrapped - float(baseline_value or 0.0)
        elif field in EMG_BASELINE_FIELDS:
            baseline_value = emg_median.get(field)
            relative[field] = safe_numeric(relative[field]) - float(baseline_value or 0.0)
    return relative


def bootstrap_failure_report(reason: str, failures: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "schema_version": TOOL_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "generated_at": utc_now_iso(),
        "template_status": "bootstrap_failed",
        "reason": reason,
        "failures": failures,
        "formal_template_replacement": False,
    }
    json_dump(BOOTSTRAP_DIR / "bootstrap_template_failure.json", payload)
    write_bootstrap_report(payload)
    return payload


def generate_bootstrap_template(args: argparse.Namespace) -> dict[str, Any]:
    ensure_output_dirs()
    dataset_root = resolve_standard_dataset_root(args.dataset_root)
    manifest = load_or_scan_manifest(dataset_root)
    source_records = [
        record for record in manifest["records"]
        if record["category"].startswith(NORMAL_SINGLE_CATEGORY_PREFIX)
    ]
    failures: list[dict[str, Any]] = []
    if len(source_records) != 10:
        return bootstrap_failure_report(
            "normal_single_source_file_count_not_10",
            [{"source_file_count": len(source_records), "expected": 10}],
        )
    raw_normalized_by_field: dict[str, list[list[float | None]]] = {field: [] for field in BOOTSTRAP_FIELDS}
    baseline_relative_by_field: dict[str, list[list[float | None]]] = {field: [] for field in BOOTSTRAP_FIELDS}
    sources = []
    action_duration_ms = []
    baseline_profiles = []
    for record in source_records:
        csv_path = dataset_root / record["relative_path"]
        repetitions, diagnostic, df = segment_file(csv_path)
        valid_reps = [rep for rep in repetitions if rep["valid"]]
        if len(valid_reps) != 1:
            failures.append(
                {
                    "relative_path": record["relative_path"],
                    "valid_repetition_count": len(valid_reps),
                    "candidate_segment_count": len(repetitions),
                    "reason": "normal_single_must_have_exactly_one_valid_repetition",
                    "diagnostic": make_json_safe(diagnostic),
                }
            )
            continue
        rep = valid_reps[0]
        action_df = df.iloc[int(rep["start_index"]) : int(rep["end_index"]) + 1].copy()
        baseline = baseline_profile(df, rep)
        if baseline["rows"] <= 0:
            failures.append(
                {
                    "relative_path": record["relative_path"],
                    "valid_repetition_count": len(valid_reps),
                    "reason": "missing_pre_action_baseline_window",
                }
            )
            continue
        raw_normalized = normalize_frame(action_df, BOOTSTRAP_FIELDS, CONFIG.bootstrap_normalized_length)
        relative_df = baseline_relative_action_frame(action_df, baseline)
        relative_normalized = normalize_frame(relative_df, BOOTSTRAP_FIELDS, CONFIG.bootstrap_normalized_length)
        for field, values in raw_normalized.items():
            raw_normalized_by_field[field].append(values)
        for field, values in relative_normalized.items():
            baseline_relative_by_field[field].append(values)
        action_duration_ms.append(rep["duration_ms"])
        baseline_profiles.append({"relative_path": record["relative_path"], **baseline})
        sources.append(
            {
                "relative_path": record["relative_path"],
                "sha256": record["sha256"],
                "selected_rep_index": rep["rep_index"],
                "start_index": rep["start_index"],
                "end_index": rep["end_index"],
                "start_time_ms": rep["start_time_ms"],
                "end_time_ms": rep["end_time_ms"],
                "segmentation_diagnostic": make_json_safe(diagnostic),
            }
        )
    if failures:
        return bootstrap_failure_report("normal_single_repetition_validation_failed", failures)
    raw_median_template: dict[str, list[float | None]] = {}
    relative_median_template: dict[str, list[float | None]] = {}
    raw_bounds_template: dict[str, dict[str, list[float | None]]] = {}
    relative_bounds_template: dict[str, dict[str, list[float | None]]] = {}

    def fill_templates(
        source: dict[str, list[list[float | None]]],
        median_template: dict[str, list[float | None]],
        bounds_template: dict[str, dict[str, list[float | None]]],
    ) -> None:
        for field, samples in source.items():
            if not samples:
                median_template[field] = []
                bounds_template[field] = {"low": [], "high": []}
                continue
            values = np.asarray(samples, dtype=float)
            median_template[field] = [value_to_json(round(float(value), 6)) for value in np.nanmedian(values, axis=0)]
            bounds_template[field] = {
                "low": [
                    value_to_json(round(float(value), 6))
                    for value in np.nanquantile(values, CONFIG.bootstrap_quantile_low, axis=0)
                ],
                "high": [
                    value_to_json(round(float(value), 6))
                    for value in np.nanquantile(values, CONFIG.bootstrap_quantile_high, axis=0)
                ],
            }

    fill_templates(raw_normalized_by_field, raw_median_template, raw_bounds_template)
    fill_templates(baseline_relative_by_field, relative_median_template, relative_bounds_template)

    median_template = {
        "raw_normalized_template": raw_median_template,
        "baseline_relative_template": relative_median_template,
    }
    bounds_template = {
        "raw_normalized_template": raw_bounds_template,
        "baseline_relative_template": relative_bounds_template,
    }
    manifest_payload = {
        "schema_version": TOOL_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "generated_at": utc_now_iso(),
        "template_status": "bootstrap_not_production",
        "source_policy": "normal_single_10_files_only",
        "standard_dataset_root": str(dataset_root),
        "config": asdict(CONFIG),
        "source_files": sources,
        "fields": BOOTSTRAP_FIELDS,
        "normalized_length": CONFIG.bootstrap_normalized_length,
        "angle_unit": "degree",
        "angle_processing": "unwrap_degrees_before_interpolation; baseline_relative subtracts per-file pre-action median",
        "emg_processing": "raw values retained and baseline-relative values subtract per-file resting median",
        "duration_ms_range": {
            "min": value_to_json(min(action_duration_ms) if action_duration_ms else None),
            "max": value_to_json(max(action_duration_ms) if action_duration_ms else None),
        },
        "baseline_profiles": baseline_profiles,
        "formal_template_replacement": False,
    }
    json_dump(BOOTSTRAP_DIR / "bootstrap_template_manifest.json", manifest_payload)
    json_dump(
        BOOTSTRAP_DIR / "drink_water_normal_median.json",
        {
            "schema_version": TOOL_SCHEMA_VERSION,
            "tool_version": TOOL_VERSION,
            "template_status": "bootstrap_not_production",
            "fields": BOOTSTRAP_FIELDS,
            "normalized_length": CONFIG.bootstrap_normalized_length,
            "median_trajectory": median_template,
        },
    )
    json_dump(
        BOOTSTRAP_DIR / "drink_water_normal_bounds.json",
        {
            "schema_version": TOOL_SCHEMA_VERSION,
            "tool_version": TOOL_VERSION,
            "template_status": "bootstrap_not_production",
            "quantiles": [CONFIG.bootstrap_quantile_low, CONFIG.bootstrap_quantile_high],
            "fields": BOOTSTRAP_FIELDS,
            "normalized_length": CONFIG.bootstrap_normalized_length,
            "bounds": bounds_template,
        },
    )
    write_bootstrap_report(manifest_payload)
    return manifest_payload


def write_bootstrap_report(payload: dict[str, Any]) -> None:
    if payload.get("template_status") == "bootstrap_failed":
        lines = [
            "# Drink Water V1 Bootstrap Template Report",
            "",
            f"- generated_at: {payload['generated_at']}",
            "- template_status: bootstrap_failed",
            f"- reason: {payload.get('reason')}",
            f"- failure_count: {len(payload.get('failures', []))}",
            f"- formal_template_replacement: {payload['formal_template_replacement']}",
            "",
            "Bootstrap template artifacts were not generated because the normal single-action source set failed validation.",
        ]
        (BOOTSTRAP_DIR / "bootstrap_template_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    lines = [
        "# Drink Water V1 Bootstrap Template Report",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- source_policy: {payload['source_policy']}",
        f"- source_file_count: {len(payload['source_files'])}",
        f"- normalized_length: {payload['normalized_length']}",
        f"- formal_template_replacement: {payload['formal_template_replacement']}",
        "",
        "This bootstrap template is derived from real source rows and normalized only for comparison.",
        "It must not be treated as raw sensor data and does not replace the formal DTW templates.",
    ]
    (BOOTSTRAP_DIR / "bootstrap_template_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_repetitions(args: argparse.Namespace) -> dict[str, Any]:
    ensure_output_dirs()
    dataset_root = resolve_standard_dataset_root(args.dataset_root)
    manifest = load_or_scan_manifest(dataset_root)
    exported = []
    for record in manifest["records"]:
        csv_path = dataset_root / record["relative_path"]
        repetitions, diagnostic, df = segment_file(csv_path)
        out_dir = EXTRACTED_REPS_DIR / Path(record["filename"]).stem
        out_dir.mkdir(parents=True, exist_ok=True)
        for rep in repetitions:
            rep_index = int(rep["rep_index"])
            rep_df = df.iloc[int(rep["start_index"]) : int(rep["end_index"]) + 1].copy()
            csv_out = out_dir / f"rep_{rep_index:02d}.csv"
            metadata_out = out_dir / f"rep_{rep_index:02d}.metadata.json"
            rep_df.to_csv(csv_out, index=False, encoding=CSV_ENCODING)
            metadata = {
                "schema_version": TOOL_SCHEMA_VERSION,
                "tool_version": TOOL_VERSION,
                "source_file": record["relative_path"],
                "source_sha256": record["sha256"],
                "rep": rep,
                "source_header_preserved": list(rep_df.columns) == record["quality_summary"]["columns"],
                "original_row_count": int(len(df)),
                "exported_row_count": int(len(rep_df)),
                "segmentation_diagnostic": make_json_safe(diagnostic),
            }
            json_dump(metadata_out, metadata)
            exported.append(
                {
                    "source_file": record["relative_path"],
                    "rep_index": rep_index,
                    "csv": str(csv_out.relative_to(DERIVED_ROOT)),
                    "metadata": str(metadata_out.relative_to(DERIVED_ROOT)),
                    "valid": rep["valid"],
                }
            )
    payload = {
        "schema_version": TOOL_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "generated_at": utc_now_iso(),
        "exported_count": len(exported),
        "exports": exported,
        "policy": "copies original CSV rows only; no resampling, zero padding, interpolation, or smoothing is written to extracted repetitions",
    }
    json_dump(EXTRACTED_REPS_DIR / "extracted_repetitions_manifest.json", payload)
    return payload


def write_validation_report(args: argparse.Namespace) -> dict[str, Any]:
    ensure_output_dirs()
    dataset_root = resolve_standard_dataset_root(args.dataset_root)
    manifest = load_or_scan_manifest(dataset_root)
    segmentation_path = SEGMENTATION_DIR / "segmentation_summary.json"
    segmentation = json.loads(segmentation_path.read_text(encoding="utf-8")) if segmentation_path.exists() else None
    payload = {
        "schema_version": TOOL_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "generated_at": utc_now_iso(),
        "standard_dataset_root": str(dataset_root),
        "manifest_exists": (MANIFEST_DIR / "dataset_manifest.json").exists(),
        "bootstrap_exists": (BOOTSTRAP_DIR / "bootstrap_template_manifest.json").exists(),
        "segmentation_exists": segmentation is not None,
        "source_file_count": manifest.get("source_file_count"),
        "expected_source_file_count": EXPECTED_SOURCE_FILE_COUNT,
        "segmentation_total": segmentation.get("detected_total_repetitions") if segmentation else None,
        "expected_total_repetitions": 40,
        "original_dataset_write_policy": "read_only; all generated artifacts are under REHAB_SCORING_ASSETS_ROOT/derived/drink_water_v1",
        "formal_template_replacement": False,
        "online_scoring_behavior_changed": False,
    }
    json_dump(VALIDATION_REPORT_DIR / "drink_water_v1_validation_report.json", payload)
    markdown = [
        "# Drink Water V1 Validation Report",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- source_file_count: {payload['source_file_count']}",
        f"- expected_source_file_count: {payload['expected_source_file_count']}",
        f"- segmentation_total: {payload['segmentation_total']}",
        f"- expected_total_repetitions: {payload['expected_total_repetitions']}",
        f"- formal_template_replacement: {payload['formal_template_replacement']}",
        f"- online_scoring_behavior_changed: {payload['online_scoring_behavior_changed']}",
    ]
    (VALIDATION_REPORT_DIR / "drink_water_v1_validation_report.md").write_text(
        "\n".join(markdown) + "\n",
        encoding="utf-8",
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Drink Water V1 offline dataset indexing, bootstrap template, and segmentation validation pipeline.",
    )
    parser.add_argument(
        "--dataset-root",
        default=str(STANDARD_DATASET_ROOT),
        help="Read-only standard dataset root.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("scan", help="Create dataset_manifest.json/csv.")
    subparsers.add_parser("bootstrap-template", help="Create bootstrap single-action normal template artifacts.")
    subparsers.add_parser("segment", help="Run existing segmentation code against all standard CSV files.")
    subparsers.add_parser("export-repetitions", help="Export detected repetition row slices and metadata.")
    subparsers.add_parser("report", help="Generate validation summary report from derived artifacts.")
    subparsers.add_parser("write-schema", help="Write repetition JSON schema only.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "scan":
        scan_dataset(args)
    elif args.command == "bootstrap-template":
        generate_bootstrap_template(args)
    elif args.command == "segment":
        segment_dataset(args)
    elif args.command == "export-repetitions":
        export_repetitions(args)
    elif args.command == "report":
        write_validation_report(args)
    elif args.command == "write-schema":
        ensure_output_dirs()
        write_repetition_schema()
    else:
        parser.error(f"unknown command: {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
