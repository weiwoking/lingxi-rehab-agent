# cloud_interface/raw_log_to_trial.py

import json
import sys
from pathlib import Path
from io import StringIO

import numpy as np
import pandas as pd

from cloud_interface.score_job_store import CSV_HEADER
from feature_extractors.lf_detector import detect_drink_water_lf_from_rows
from feature_extractors.tremor_detector import detect_drink_water_tremor_from_rows


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8020"
EXPECTED_FUSED_COLUMNS = CSV_HEADER.split(",")
EXPECTED_FUSED_COLUMN_COUNT = len(EXPECTED_FUSED_COLUMNS)
MIN_SCORING_ROWS = 3

REQUIRED_BASE_COLUMNS = ["log_seq", "time_ms"]
REQUIRED_IMU_COLUMNS = [
    f"imu{idx}_{field}"
    for idx in range(1, 5)
    for field in ("roll", "pitch", "yaw", "gx", "gy", "gz")
]
REQUIRED_EMG_COLUMNS = [
    f"emg_{field}_{idx}"
    for field in ("latest", "rms", "mav", "peak")
    for idx in range(1, 5)
]
REQUIRED_SCORING_COLUMNS = REQUIRED_BASE_COLUMNS + REQUIRED_IMU_COLUMNS + REQUIRED_EMG_COLUMNS
LINEAR_ACC_GROUPS = [
    ("lax", "lay", "laz"),
    ("linear_ax", "linear_ay", "linear_az"),
]


# =========================
# 1. IMU 映射关系
# =========================
# Current fixed installation mapping:
# imu1 = 上臂
# imu2 = 躯干 / 腰背
# imu3 = 前臂
# imu4 = 手腕

IMU_MAP = {
    "upperarm": "imu1",
    "trunk": "imu2",
    "forearm": "imu3",
    "wrist": "imu4",
}


# =========================
# 2. EMG 映射关系
# =========================
# emg_1 = 指伸肌 Extensor_digitorum
# emg_2 = 肱二头肌 Biceps_brachii
# emg_3 = 肱三头肌 Triceps_brachii
# emg_4 = 尺侧腕伸肌 Extensor_carpi_ulnaris

EMG_MUSCLE_MAP = {
    1: "Extensor_digitorum",
    2: "Biceps_brachii",
    3: "Triceps_brachii",
    4: "Extensor_carpi_ulnaris",
}


# =========================
# 3. ADS / EMG 硬件参数
# =========================
# ADS 有符号 24-bit 数据换算:
# voltage = adc_count / 2^23 * (vref / gain)
ADS_VREF_V = 2.42
ADS_GAIN = 12
ADS_BITS = 24
ADS_SIGNED_FULL_SCALE_COUNTS = 2 ** (ADS_BITS - 1)
ADS_LSB_V = ADS_VREF_V / ADS_GAIN / ADS_SIGNED_FULL_SCALE_COUNTS


# =========================
# 3. V1 速度指标权重
# =========================
# gx / gy / gz：角速度
# ax / ay / az：原始加速度，包含重力分量，单位 g，不用于速度评分
# lax / lay / laz：去除重力后的线性加速度，单位 g，用于动作强度和速度指标
#
# V1 不直接对 lax/lay/laz 积分计算真实线速度，
# 而是用“角速度模长 + 线性加速度模长”构建动作速度指标。
#
# 因为 lax 单位是 g，所以乘以 9.81 转成近似 m/s^2 量级。
ACC_WEIGHT = 9.81


def safe_mean(series, default=0.0):
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) == 0:
        return default
    return float(values.mean())


def safe_std(series, default=0.0):
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) == 0:
        return default
    return float(values.std())


def safe_max(series, default=0.0):
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) == 0:
        return default
    return float(values.max())


def safe_range(series, default=0.0):
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) == 0:
        return default
    return float(values.max() - values.min())


def circular_angle_range_deg(series, default=0.0):
    values = pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return default
    normalized = np.mod(values, 360.0)
    if len(normalized) == 1:
        return 0.0
    ordered = np.sort(normalized)
    gaps = np.diff(ordered)
    wrap_gap = (ordered[0] + 360.0) - ordered[-1]
    largest_gap = float(max(float(np.max(gaps)) if len(gaps) > 0 else 0.0, wrap_gap))
    return float(360.0 - largest_gap)


def percentile_value(values, percentile, default=0.0):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return default
    return float(np.percentile(values, percentile))


def require_numeric_col(df, col_name, source_label=None):
    if col_name not in df.columns:
        label = f":{source_label}" if source_label else ""
        raise ValueError(f"missing_required_scoring_columns{label}:{col_name}")
    values = pd.to_numeric(df[col_name], errors="coerce")
    finite = np.isfinite(values.to_numpy(dtype=float))
    if not bool(np.any(finite)):
        label = f":{source_label}" if source_label else ""
        raise ValueError(f"invalid_required_scoring_column{label}:{col_name}")
    return values


def validate_fused_scoring_input(df, source_label=None):
    label = f":{source_label}" if source_label else ""
    columns = [str(column).strip().lstrip("\ufeff") for column in df.columns]
    if columns != EXPECTED_FUSED_COLUMNS:
        if len(columns) != EXPECTED_FUSED_COLUMN_COUNT:
            raise ValueError(
                f"invalid_fused_csv_column_count{label}:"
                f"expected={EXPECTED_FUSED_COLUMN_COUNT};actual={len(columns)}"
            )
        missing = [column for column in EXPECTED_FUSED_COLUMNS if column not in columns]
        extra = [column for column in columns if column not in EXPECTED_FUSED_COLUMNS]
        if missing:
            raise ValueError(f"missing_required_scoring_columns{label}:{','.join(missing)}")
        if extra:
            raise ValueError(f"unexpected_fused_csv_columns{label}:{','.join(extra)}")
        raise ValueError(f"invalid_fused_csv_header_order{label}")
    if len(df) < MIN_SCORING_ROWS:
        raise ValueError(f"insufficient_scoring_rows{label}:min={MIN_SCORING_ROWS};actual={len(df)}")
    for column in REQUIRED_SCORING_COLUMNS:
        require_numeric_col(df, column, source_label=source_label)
    for imu_idx in range(1, 5):
        matching_groups = []
        partial_groups = []
        for group in LINEAR_ACC_GROUPS:
            names = [f"imu{imu_idx}_{suffix}" for suffix in group]
            present = [name for name in names if name in df.columns]
            if len(present) == len(names):
                matching_groups.append(names)
            elif present:
                partial_groups.append(names)
        if not matching_groups:
            if partial_groups:
                raise ValueError(f"missing_complete_linear_acc_group{label}:imu{imu_idx}")
            raise ValueError(f"missing_required_scoring_columns{label}:imu{imu_idx}_linear_acc_group")
        for group in matching_groups:
            for column in group:
                require_numeric_col(df, column, source_label=source_label)
    return True


def get_col(df, col_name, default=0.0, required=False):
    """
    安全读取列。
    如果列不存在，返回默认值序列。
    """
    if required:
        return require_numeric_col(df, col_name)
    if col_name in df.columns:
        return pd.to_numeric(df[col_name], errors="coerce").fillna(default)

    return pd.Series([default] * len(df))


def calc_upperarm_gravity_raw_robust_excursion(df, imu_prefix):
    ax = get_col(df, f"{imu_prefix}_ax", required=True).to_numpy(dtype=float)
    ay = get_col(df, f"{imu_prefix}_ay", required=True).to_numpy(dtype=float)
    az = get_col(df, f"{imu_prefix}_az", required=True).to_numpy(dtype=float)
    vectors = np.column_stack([ax, ay, az])
    finite_rows = np.all(np.isfinite(vectors), axis=1)
    vectors = vectors[finite_rows]
    if len(vectors) == 0:
        return 0.0

    norms = np.linalg.norm(vectors, axis=1)
    valid = np.isfinite(norms) & (norms > 0.0)
    vectors = vectors[valid]
    norms = norms[valid]
    if len(vectors) == 0:
        return 0.0

    time_values = get_col(df, "time_ms", required=False).to_numpy(dtype=float)
    finite_time = time_values[np.isfinite(time_values)]
    if len(finite_time) >= 2:
        diffs = np.diff(finite_time)
        positive_diffs = diffs[diffs > 0.0]
        median_dt_ms = float(np.median(positive_diffs)) if len(positive_diffs) > 0 else 0.0
    else:
        median_dt_ms = 0.0
    baseline_samples = 1
    if median_dt_ms > 0.0:
        baseline_samples = max(1, int(round(400.0 / median_dt_ms)))
        if baseline_samples > 1 and baseline_samples % 2 == 0:
            baseline_samples += 1
    baseline_samples = min(baseline_samples, len(vectors))

    baseline_vector = np.mean(vectors[:baseline_samples], axis=0)
    baseline_norm = float(np.linalg.norm(baseline_vector))
    if not np.isfinite(baseline_norm) or baseline_norm <= 0.0:
        return 0.0
    baseline_unit = baseline_vector / baseline_norm
    unit_vectors = vectors / norms[:, None]
    dots = np.clip(unit_vectors @ baseline_unit, -1.0, 1.0)
    theta_deg = np.degrees(np.arccos(dots))
    return percentile_value(theta_deg, 95.0, default=0.0)


def adc_counts_to_volts(values):
    """
    Convert signed ADS ADC counts or count-based features to volts.

    The current ESP32 CSV stores EMG latest/RMS/MAV/peak in ADC-count scale.
    With vref=2.42 V and gain=12, one count is about 24.04 nV.
    """
    return pd.to_numeric(values, errors="coerce").fillna(0.0) * ADS_LSB_V


def find_linear_acc_columns(df, imu_prefix):
    """
    自动寻找去重力线性加速度字段。

    优先级：
    1. imu1_lax / imu1_lay / imu1_laz
    2. imu1_linear_ax / imu1_linear_ay / imu1_linear_az

    注意：
    imu1_ax / imu1_ay / imu1_az 是包含重力的原始加速度，
    不用于速度评分和动作强度计算。
    """

    candidates = [
        (
            f"{imu_prefix}_lax",
            f"{imu_prefix}_lay",
            f"{imu_prefix}_laz",
            "linear_lax",
        ),
        (
            f"{imu_prefix}_linear_ax",
            f"{imu_prefix}_linear_ay",
            f"{imu_prefix}_linear_az",
            "linear_ax",
        ),
    ]

    for ax_col, ay_col, az_col, acc_type in candidates:
        if ax_col in df.columns and ay_col in df.columns and az_col in df.columns:
            return ax_col, ay_col, az_col, acc_type

    return None, None, None, "none"


def calc_imu_angular_speed(df, imu_prefix):
    """
    根据 gx/gy/gz 计算角速度模长。

    gx / gy / gz 是角速度，不是角加速度。
    """
    gx = get_col(df, f"{imu_prefix}_gx", required=True).to_numpy()
    gy = get_col(df, f"{imu_prefix}_gy", required=True).to_numpy()
    gz = get_col(df, f"{imu_prefix}_gz", required=True).to_numpy()

    return np.sqrt(gx ** 2 + gy ** 2 + gz ** 2)


def calc_imu_linear_acc_magnitude(df, imu_prefix):
    """
    计算去重力线性加速度模长。

    只使用：
    imuX_lax / imuX_lay / imuX_laz

    或：
    imuX_linear_ax / imuX_linear_ay / imuX_linear_az

    不使用 imuX_ax / imuX_ay / imuX_az，
    因为 ax/ay/az 包含重力分量。
    """
    ax_col, ay_col, az_col, acc_type = find_linear_acc_columns(df, imu_prefix)

    if acc_type == "none":
        raise ValueError(f"missing_complete_linear_acc_group:{imu_prefix}")

    ax = get_col(df, ax_col, required=True).to_numpy()
    ay = get_col(df, ay_col, required=True).to_numpy()
    az = get_col(df, az_col, required=True).to_numpy()

    acc_mag = np.sqrt(ax ** 2 + ay ** 2 + az ** 2)

    return acc_mag, acc_type


def calc_imu_raw_acc_magnitude(df, imu_prefix):
    """
    计算包含重力的原始加速度模长。

    ax / ay / az 只用于状态检查，不用于速度评分。
    静止时 raw_acc_mag 理论上应接近 1g。
    """
    ax_col = f"{imu_prefix}_ax"
    ay_col = f"{imu_prefix}_ay"
    az_col = f"{imu_prefix}_az"

    if ax_col not in df.columns or ay_col not in df.columns or az_col not in df.columns:
        raise ValueError(f"missing_required_scoring_columns:{imu_prefix}_raw_acc_group")

    ax = get_col(df, ax_col, required=True).to_numpy()
    ay = get_col(df, ay_col, required=True).to_numpy()
    az = get_col(df, az_col, required=True).to_numpy()

    raw_acc_mag = np.sqrt(ax ** 2 + ay ** 2 + az ** 2)

    return raw_acc_mag, "raw_with_gravity"


def count_speed_peaks(speed_values):
    """
    简单速度峰值计数：
    当前点大于左右相邻点，并且大于均值。
    """
    speed_values = np.asarray(speed_values, dtype=float)

    if len(speed_values) < 3:
        return 0

    threshold = np.mean(speed_values)

    count = 0
    for i in range(1, len(speed_values) - 1):
        if (
            speed_values[i] > speed_values[i - 1]
            and speed_values[i] > speed_values[i + 1]
            and speed_values[i] > threshold
        ):
            count += 1

    return int(count)


def count_pause(speed_values):
    """
    简单停顿次数：
    速度低于均值 20% 的片段，连续一段算一次停顿。
    """
    speed_values = np.asarray(speed_values, dtype=float)

    if len(speed_values) == 0:
        return 0

    threshold = np.mean(speed_values) * 0.2
    is_pause = speed_values < threshold

    count = 0
    in_pause = False

    for flag in is_pause:
        if flag and not in_pause:
            count += 1
            in_pause = True
        elif not flag:
            in_pause = False

    return int(count)


def read_fused_csv(csv_path):
    """
    读取 fused CSV。

    支持：
    1. 原始 fused CSV
    2. trial_segmenter.py 切出来的 trial CSV
    3. 文件中混入 I (...) REHAB_LINK 调试日志

    同时兼容 utf-8-sig BOM。
    """

    path = Path(csv_path)

    with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
        lines = f.readlines()

    clean_lines = []

    for line in lines:
        # 去掉 BOM 和首尾空白
        line_clean = line.lstrip("\ufeff")
        line_strip = line_clean.strip()

        if not line_strip:
            continue

        # 表头保留
        if line_strip.startswith("log_seq"):
            clean_lines.append(line_clean)
            continue

        # 数据行一般数字开头，或者负号开头
        if line_strip[0].isdigit() or line_strip[0] == "-":
            clean_lines.append(line_clean)

    if len(clean_lines) == 0:
        raise ValueError("CSV 文件中没有有效数据行。")

    df = pd.read_csv(StringIO("".join(clean_lines)))
    df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]

    return df


def build_trial_from_raw_log(
    csv_path,
    patient_id="S001",
    session_id="raw_session_001",
    task_number=12,
    trial_id=1,
):
    """
    从硬件 fused CSV 生成 /score_trial 接口需要的 trial JSON。
    """

    df = read_fused_csv(csv_path)
    validate_fused_scoring_input(df, source_label=str(csv_path))

    emg_hardware = {
        "adc": "ADS_signed_24bit",
        "vref_v": ADS_VREF_V,
        "gain": ADS_GAIN,
        "bits": ADS_BITS,
        "signed_full_scale_counts": ADS_SIGNED_FULL_SCALE_COUNTS,
        "lsb_v": ADS_LSB_V,
        "feature_unit": "V",
    }

    wrist = IMU_MAP["wrist"]
    forearm = IMU_MAP["forearm"]
    upperarm = IMU_MAP["upperarm"]
    trunk = IMU_MAP["trunk"]

    # =========================
    # 1. 手腕 IMU 特征
    # =========================
    wrist_gyro_mag = calc_imu_angular_speed(df, wrist)
    wrist_acc_mag, wrist_acc_type = calc_imu_linear_acc_magnitude(df, wrist)
    wrist_raw_acc_mag, wrist_raw_acc_type = calc_imu_raw_acc_magnitude(df, wrist)

    wrist_gyro_mean = float(np.mean(wrist_gyro_mag))
    wrist_gyro_peak = float(np.max(wrist_gyro_mag))
    wrist_gyro_std = float(np.std(wrist_gyro_mag))

    wrist_acc_mean = float(np.mean(wrist_acc_mag))
    wrist_acc_peak = float(np.max(wrist_acc_mag))
    wrist_acc_std = float(np.std(wrist_acc_mag))

    wrist_raw_acc_mean = float(np.mean(wrist_raw_acc_mag))
    wrist_raw_acc_peak = float(np.max(wrist_raw_acc_mag))
    wrist_raw_acc_std = float(np.std(wrist_raw_acc_mag))

    # V1 动作速度指标：
    # wrist_gyro_mean 来自 gx/gy/gz 角速度模长
    # wrist_acc_mean 来自 lax/lay/laz 去重力线性加速度模长
    # 注意：这不是严格真实 mm/s，而是用于评分的动作速度指标。
    wrist_motion_speed_index = wrist_gyro_mean + ACC_WEIGHT * wrist_acc_mean

    # 用于峰值和停顿检测的速度曲线
    wrist_motion_curve = wrist_gyro_mag + ACC_WEIGHT * wrist_acc_mag

    num_speed_peaks = count_speed_peaks(wrist_motion_curve)
    pause_count = count_pause(wrist_motion_curve)
    tremor_result = detect_drink_water_tremor_from_rows(df)
    lf_result = detect_drink_water_lf_from_rows(df)

    # =========================
    # 2. 前臂 IMU 特征
    # =========================
    if int(task_number) == 12:
        forearm_rotation_range = (
            circular_angle_range_deg(get_col(df, f"{forearm}_roll", required=True))
            + circular_angle_range_deg(get_col(df, f"{forearm}_pitch", required=True))
            + circular_angle_range_deg(get_col(df, f"{forearm}_yaw", required=True))
        )
    else:
        forearm_rotation_range = (
            safe_range(get_col(df, f"{forearm}_roll", required=True))
            + safe_range(get_col(df, f"{forearm}_pitch", required=True))
            + safe_range(get_col(df, f"{forearm}_yaw", required=True))
        )

    forearm_gyro_mag = calc_imu_angular_speed(df, forearm)
    forearm_acc_mag, forearm_acc_type = calc_imu_linear_acc_magnitude(df, forearm)
    forearm_raw_acc_mag, forearm_raw_acc_type = calc_imu_raw_acc_magnitude(df, forearm)

    forearm_mean_angular_speed = float(np.mean(forearm_gyro_mag))
    forearm_acc_mean = float(np.mean(forearm_acc_mag))
    forearm_acc_peak = float(np.max(forearm_acc_mag))
    forearm_raw_acc_mean = float(np.mean(forearm_raw_acc_mag))

    # =========================
    # 3. 上臂 IMU 特征
    # =========================
    # V1 用 roll/pitch/yaw 的总活动范围近似 UpperArmROM
    if int(task_number) == 12:
        upperarm_rom = calc_upperarm_gravity_raw_robust_excursion(df, upperarm)
    else:
        upperarm_rom = (
            safe_range(get_col(df, f"{upperarm}_roll", required=True))
            + safe_range(get_col(df, f"{upperarm}_pitch", required=True))
            + safe_range(get_col(df, f"{upperarm}_yaw", required=True))
        )

    upperarm_gyro_mag = calc_imu_angular_speed(df, upperarm)
    upperarm_acc_mag, upperarm_acc_type = calc_imu_linear_acc_magnitude(df, upperarm)
    upperarm_raw_acc_mag, upperarm_raw_acc_type = calc_imu_raw_acc_magnitude(df, upperarm)

    upperarm_mean_angular_speed = float(np.mean(upperarm_gyro_mag))
    upperarm_acc_mean = float(np.mean(upperarm_acc_mag))
    upperarm_acc_peak = float(np.max(upperarm_acc_mag))
    upperarm_raw_acc_mean = float(np.mean(upperarm_raw_acc_mag))

    # =========================
    # 4. 躯干 IMU 特征
    # =========================
    trunk_rom = (
        safe_range(get_col(df, f"{trunk}_roll", required=True))
        + safe_range(get_col(df, f"{trunk}_pitch", required=True))
        + safe_range(get_col(df, f"{trunk}_yaw", required=True))
    )

    trunk_gyro_mag = calc_imu_angular_speed(df, trunk)
    trunk_acc_mag, trunk_acc_type = calc_imu_linear_acc_magnitude(df, trunk)
    trunk_raw_acc_mag, trunk_raw_acc_type = calc_imu_raw_acc_magnitude(df, trunk)

    trunk_acc_mean = float(np.mean(trunk_acc_mag))
    trunk_acc_peak = float(np.max(trunk_acc_mag))
    trunk_raw_acc_mean = float(np.mean(trunk_raw_acc_mag))

    # V1 躯干代偿比例：
    # 躯干活动量 / (躯干活动量 + 上臂活动量)
    trunk_comp_ratio = trunk_rom / (trunk_rom + upperarm_rom + 1e-8)

    # =========================
    # 5. EMG 分肌肉特征
    # =========================
    emg_features = {}

    for idx, muscle_name in EMG_MUSCLE_MAP.items():
        rms_col = f"emg_rms_{idx}"
        mav_col = f"emg_mav_{idx}"
        peak_col = f"emg_peak_{idx}"
        latest_col = f"emg_latest_{idx}"

        latest_values_v = adc_counts_to_volts(get_col(df, latest_col, required=True)).to_numpy()
        rms = safe_mean(adc_counts_to_volts(get_col(df, rms_col, required=True)))
        mav = safe_mean(adc_counts_to_volts(get_col(df, mav_col, required=True)))
        peak = safe_mean(adc_counts_to_volts(get_col(df, peak_col, required=True)))

        # WL 第一版用连续 latest 差分近似
        if len(latest_values_v) >= 2:
            wl = float(np.sum(np.abs(np.diff(latest_values_v))))
        else:
            wl = 0.0

        iemg = float(np.sum(np.abs(latest_values_v))) if len(latest_values_v) > 0 else 0.0
        ssi = float(np.sum(latest_values_v ** 2)) if len(latest_values_v) > 0 else 0.0

        emg_features[f"{muscle_name}_RMS"] = rms
        emg_features[f"{muscle_name}_MAV"] = mav
        emg_features[f"{muscle_name}_WL_mean"] = wl
        emg_features[f"{muscle_name}_IEMG"] = iemg
        emg_features[f"{muscle_name}_SSI"] = ssi

        # peak 暂不直接参与动作质量评分，但保留下来
        emg_features[f"{muscle_name}_Peak"] = peak

    peak_values = [emg_features[f"{m}_Peak"] for m in EMG_MUSCLE_MAP.values()]
    global_peak = float(np.mean(peak_values)) if peak_values else 0.0

    # =========================
    # 6. 数据质量信息
    # =========================
    data_quality = {}

    if "time_ms" in df.columns:
        time_values = pd.to_numeric(df["time_ms"], errors="coerce").dropna().to_numpy()
        if len(time_values) >= 2:
            dt_ms = np.diff(time_values)
            mean_dt = float(np.mean(dt_ms))
            max_dt = float(np.max(dt_ms))

            data_quality["mean_dt_ms"] = mean_dt
            data_quality["max_dt_ms"] = max_dt
            data_quality["estimated_rate_hz"] = float(1000.0 / mean_dt) if mean_dt > 0 else 0.0

            # 50 Hz 理论间隔 20 ms
            data_quality["large_gap_count_over_40ms"] = int(np.sum(dt_ms > 40))
            data_quality["large_gap_count_over_100ms"] = int(np.sum(dt_ms > 100))
        else:
            data_quality["mean_dt_ms"] = None
            data_quality["max_dt_ms"] = None
            data_quality["estimated_rate_hz"] = None

    if "log_seq" in df.columns:
        seq_values = pd.to_numeric(df["log_seq"], errors="coerce").dropna().to_numpy()
        if len(seq_values) >= 2:
            seq_gap = np.diff(seq_values)
            data_quality["max_log_seq_gap"] = int(np.max(seq_gap))
            data_quality["missing_log_seq_count_est"] = int(
                np.sum(np.maximum(seq_gap - 1, 0))
            )
        else:
            data_quality["max_log_seq_gap"] = None
            data_quality["missing_log_seq_count_est"] = None

    data_quality["row_count"] = int(len(df))

    # =========================
    # 7. 组装 /score_trial JSON
    # =========================
    trial_json = {
        "patient_id": patient_id,
        "session_id": session_id,
        "Group": "Stroke",
        "SubjectID": patient_id,
        "TaskNumber": task_number,
        "TrialID": trial_id,
        "FileName": Path(csv_path).name,

        # 兼容旧评分接口字段名
        # 注意：V1 中这是动作速度指标，不是真实 mm/s
        "WristMeanSpeed_mm_s": wrist_motion_speed_index,

        "NumSpeedPeaks": num_speed_peaks,
        "PauseCount": pause_count,
        "DrinkWaterTremorDetected": bool(tremor_result.get("detected", False)),
        "DrinkWaterTremorFraction": tremor_result.get("tremor_fraction"),
        "DrinkWaterTremorValidWindowCount": tremor_result.get("valid_window_count"),
        "DrinkWaterTremorPositiveWindowCount": tremor_result.get("positive_window_count"),
        "DrinkWaterTremorStatus": tremor_result.get("status"),
        "DrinkWaterTremorVersion": tremor_result.get("version"),
        "DrinkWaterLFDetected": bool(lf_result.get("detected", False)),
        "DrinkWaterLFNeutralRoll_deg": lf_result.get("neutral_roll_deg"),
        "DrinkWaterLFPeakAbsRoll_deg": lf_result.get("peak_abs_roll_deg"),
        "DrinkWaterLFMaxDurationOverThreshold_s": lf_result.get("max_duration_over_threshold_s"),
        "DrinkWaterLFPositiveDuration_s": lf_result.get("positive_duration_over_threshold_s"),
        "DrinkWaterLFNegativeDuration_s": lf_result.get("negative_duration_over_threshold_s"),
        "DrinkWaterLFRollThreshold_deg": lf_result.get("roll_threshold_deg"),
        "DrinkWaterLFMinDuration_s": lf_result.get("min_duration_s"),
        "DrinkWaterLFValidSampleCount": lf_result.get("valid_sample_count"),
        "DrinkWaterLFEstimatedRateHz": lf_result.get("estimated_rate_hz"),
        "DrinkWaterLFStatus": lf_result.get("status"),
        "DrinkWaterLFVersion": lf_result.get("version"),

        "ForearmRotationRange_deg": forearm_rotation_range,
        "ForearmMeanAngularSpeed_deg_s": forearm_mean_angular_speed,

        # 字段名保留 UpperArmROM_mm 是为了兼容旧接口
        # 实际 V1 里是上臂姿态角 ROM 近似，不是真实 mm
        "UpperArmROM_mm": upperarm_rom,
        "UpperArmMeanAngularSpeed_deg_s": upperarm_mean_angular_speed,

        "TrunkCompensationRatio": trunk_comp_ratio,
        "TrunkROM_deg": trunk_rom,

        **emg_features,

        # V1 新增调试/后续机器学习特征
        "WristMotionSpeedIndex": wrist_motion_speed_index,
        "WristMeanAngularSpeed_deg_s": wrist_gyro_mean,
        "WristPeakAngularSpeed_deg_s": wrist_gyro_peak,
        "WristStdAngularSpeed_deg_s": wrist_gyro_std,

        # 去重力线性加速度：用于速度指标和动作强度
        "WristMeanLinearAcc": wrist_acc_mean,
        "WristPeakLinearAcc": wrist_acc_peak,
        "WristStdLinearAcc": wrist_acc_std,
        "WristAccType": wrist_acc_type,

        # 原始含重力加速度：只用于状态检查
        "WristMeanRawAccWithGravity": wrist_raw_acc_mean,
        "WristPeakRawAccWithGravity": wrist_raw_acc_peak,
        "WristStdRawAccWithGravity": wrist_raw_acc_std,
        "WristRawAccType": wrist_raw_acc_type,

        "ForearmMeanLinearAcc": forearm_acc_mean,
        "ForearmPeakLinearAcc": forearm_acc_peak,
        "ForearmAccType": forearm_acc_type,
        "ForearmMeanRawAccWithGravity": forearm_raw_acc_mean,
        "ForearmRawAccType": forearm_raw_acc_type,

        "UpperArmMeanLinearAcc": upperarm_acc_mean,
        "UpperArmPeakLinearAcc": upperarm_acc_peak,
        "UpperArmAccType": upperarm_acc_type,
        "UpperArmMeanRawAccWithGravity": upperarm_raw_acc_mean,
        "UpperArmRawAccType": upperarm_raw_acc_type,

        "TrunkMeanLinearAcc": trunk_acc_mean,
        "TrunkPeakLinearAcc": trunk_acc_peak,
        "TrunkAccType": trunk_acc_type,
        "TrunkMeanRawAccWithGravity": trunk_raw_acc_mean,
        "TrunkRawAccType": trunk_raw_acc_type,

        "GlobalEMGPeak": global_peak,

        "data_quality": data_quality,
        "lf_detection": lf_result,
        "tremor_detection": tremor_result,
        "hardware": {
            "emg": emg_hardware,
        },
        "capture_context": {
            "source": "raw_fused_csv",
            "is_segmented_rehab_action": False,
            "interpretation": "pipeline_check_only",
            "note": "This CSV was converted from raw fused hardware logs. Treat the score as a pipeline/unit-conversion check unless the file is a verified rehabilitation action window.",
        },
    }

    return trial_json


def post_score_trial(trial_json):
    """
    把转换后的 trial_json 发给评分服务器。
    """
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError(
            "requests is required only when posting to the local score server. "
            "Install dependencies from cloud_interface/requirements.txt or call "
            "build_trial_from_raw_log() directly for offline conversion/testing."
        ) from exc

    response = requests.post(
        f"{BASE_URL}/score_trial",
        json=trial_json,
        timeout=10,
    )

    print(f"POST /score_trial -> {response.status_code}")

    try:
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    except Exception:
        print(response.text)


def main():
    if len(sys.argv) < 2:
        print("用法：")
        print("python cloud_interface/raw_log_to_trial.py data/raw/fused_20260526_205718.csv")
        return

    csv_path = sys.argv[1]

    trial_json = build_trial_from_raw_log(
        csv_path=csv_path,
        patient_id="S001",
        session_id="raw_log_session_001",
        task_number=12,
        trial_id=1,
    )

    print("转换后的 trial JSON：")
    print(json.dumps(trial_json, ensure_ascii=False, indent=2))

    # 保存一份转换结果，方便检查
    out_path = PROJECT_ROOT / "data" / "mock_trials" / "generated_from_raw_log.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(trial_json, f, ensure_ascii=False, indent=2)

    print(f"\n已保存到：{out_path}")

    # 直接发送给评分服务器
    post_score_trial(trial_json)


if __name__ == "__main__":
    main()
