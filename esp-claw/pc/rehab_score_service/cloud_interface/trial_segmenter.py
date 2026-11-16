# cloud_interface/trial_segmenter.py

import sys
from pathlib import Path
from io import StringIO

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]


# =========================
# 基本配置
# =========================

ACC_WEIGHT = 9.81

# trial 分割参数
START_STD_FACTOR = 1.5        # 启动阈值 = 静息均值 + 1.5 * 静息标准差
STOP_STD_FACTOR = 0.8         # 结束阈值 = 静息均值 + 0.8 * 静息标准差

MIN_START_DURATION_MS = 120   # 连续超过启动阈值至少 120ms 才认为动作开始
MIN_STOP_DURATION_MS = 350    # 连续低于结束阈值至少 350ms 才认为动作结束
COOLDOWN_MS = 500             # 一个 trial 结束后 500ms 内不触发下一个 trial

MIN_TRIAL_DURATION_MS = 800   # 有效 trial 最短 0.8s
MAX_TRIAL_DURATION_MS = 8000  # 有效 trial 最长 8s

MIN_PEAK_FACTOR = 2.0         # trial 峰值至少要超过静息均值 + 2 * 静息标准差
MIN_ROM_DEG = 20.0       # 上肢角度变化太小，不算有效动作


def read_fused_csv(csv_path):
    """
    读取 fused CSV。
    如果文件中混入 ESP 日志，会自动过滤非 CSV 数据行。
    """

    path = Path(csv_path)

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    clean_lines = []

    for line in lines:
        line_strip = line.strip()

        if not line_strip:
            continue

        if line_strip.startswith("log_seq"):
            clean_lines.append(line)
            continue

        if line_strip[0].isdigit():
            clean_lines.append(line)

    if not clean_lines:
        raise ValueError("CSV 文件中没有有效数据。")

    df = pd.read_csv(StringIO("".join(clean_lines)))
    df.columns = [c.strip() for c in df.columns]

    return df


def get_col(df, col_name, default=0.0):
    if col_name in df.columns:
        return pd.to_numeric(df[col_name], errors="coerce").fillna(default)
    return pd.Series([default] * len(df))


def calc_mag(x, y, z):
    return np.sqrt(x ** 2 + y ** 2 + z ** 2)


def build_time_ms(df):
    """
    优先使用 time_ms。
    如果没有 time_ms，就按 50Hz 估计。
    """
    if "time_ms" in df.columns:
        t = pd.to_numeric(df["time_ms"], errors="coerce").ffill().fillna(0)
        return t.to_numpy(dtype=float)

    return np.arange(len(df), dtype=float) * 20.0


def build_motion_curve(df):
    """
    使用手腕 imu1：
    gx/gy/gz：角速度
    lax/lay/laz：去重力线性加速度

    motion_curve = gyro_mag + 9.81 * linear_acc_mag
    """

    gx = get_col(df, "imu1_gx").to_numpy()
    gy = get_col(df, "imu1_gy").to_numpy()
    gz = get_col(df, "imu1_gz").to_numpy()

    gyro_mag = calc_mag(gx, gy, gz)

    if all(col in df.columns for col in ["imu1_lax", "imu1_lay", "imu1_laz"]):
        lax = get_col(df, "imu1_lax").to_numpy()
        lay = get_col(df, "imu1_lay").to_numpy()
        laz = get_col(df, "imu1_laz").to_numpy()
        acc_mag = calc_mag(lax, lay, laz)
        acc_type = "linear_lax"

    elif all(col in df.columns for col in ["imu1_linear_ax", "imu1_linear_ay", "imu1_linear_az"]):
        lax = get_col(df, "imu1_linear_ax").to_numpy()
        lay = get_col(df, "imu1_linear_ay").to_numpy()
        laz = get_col(df, "imu1_linear_az").to_numpy()
        acc_mag = calc_mag(lax, lay, laz)
        acc_type = "linear_ax"

    else:
        acc_mag = np.zeros(len(df))
        acc_type = "none"

    motion_curve = gyro_mag + ACC_WEIGHT * acc_mag

    return motion_curve, gyro_mag, acc_mag, acc_type


def smooth_signal(x, window=5):
    """
    简单滑动平均，减少尖峰误触发。
    """
    x = np.asarray(x, dtype=float)

    if len(x) < window:
        return x

    kernel = np.ones(window) / window
    return np.convolve(x, kernel, mode="same")


def estimate_rest_baseline(motion_curve):
    """
    用最低 20% 的运动强度估计静息水平。
    这样即使 session 里有动作，也能粗略估计静息背景。
    """

    values = np.asarray(motion_curve, dtype=float)

    if len(values) == 0:
        return 0.0, 1.0

    sorted_values = np.sort(values)
    n = max(5, int(len(sorted_values) * 0.2))
    rest_values = sorted_values[:n]

    rest_mean = float(np.mean(rest_values))
    rest_std = float(np.std(rest_values))

    if rest_std < 1e-6:
        rest_std = 1.0

    return rest_mean, rest_std


def calc_segment_rom(df, start_idx, end_idx):
    """
    计算该片段内的粗略上肢角度变化。
    用 imu1/imu2/imu3 的 roll/pitch/yaw range 求和。
    """

    segment = df.iloc[start_idx:end_idx + 1]

    rom = 0.0

    for imu in ["imu1", "imu2", "imu3"]:
        for axis in ["roll", "pitch", "yaw"]:
            col = f"{imu}_{axis}"
            if col in segment.columns:
                values = pd.to_numeric(segment[col], errors="coerce").dropna()
                if len(values) > 0:
                    rom += float(values.max() - values.min())

    return rom


def is_valid_trial(df, motion_curve, time_ms, start_idx, end_idx, rest_mean, rest_std):
    """
    判断一个候选 trial 是否有效。
    防止休息、轻微抖动、姿势调整被误识别为 trial。
    """

    if end_idx <= start_idx:
        return False, "empty segment"

    duration_ms = time_ms[end_idx] - time_ms[start_idx]

    if duration_ms < MIN_TRIAL_DURATION_MS:
        return False, f"too short: {duration_ms:.1f} ms"

    if duration_ms > MAX_TRIAL_DURATION_MS:
        return False, f"too long: {duration_ms:.1f} ms"

    segment_curve = motion_curve[start_idx:end_idx + 1]

    peak_value = float(np.max(segment_curve))
    peak_threshold = rest_mean + MIN_PEAK_FACTOR * rest_std

    if peak_value < peak_threshold:
        return False, f"peak too low: {peak_value:.2f} < {peak_threshold:.2f}"

    area = float(np.trapz(np.maximum(segment_curve - rest_mean, 0), time_ms[start_idx:end_idx + 1]))
    min_area = peak_threshold * 100.0

    if area < min_area:
        return False, f"area too low: {area:.2f} < {min_area:.2f}"

    rom = calc_segment_rom(df, start_idx, end_idx)

    if rom < MIN_ROM_DEG:
        return False, f"ROM too small: {rom:.2f} deg"

    return True, "valid"

def split_long_segment_by_time(df, time_ms, start_idx, end_idx, target_duration_ms=3000):
    """
    当一整段 MOVING 太长时，先按固定时长粗切成多个候选 trial。
    这是 V1 调试版，用来避免连续动作被识别成一个超长 trial。
    后续可以替换成基于局部低谷/峰值的精细切分。
    """
    sub_trials = []

    segment_start_time = time_ms[start_idx]
    segment_end_time = time_ms[end_idx]

    current_start_idx = start_idx
    current_start_time = segment_start_time

    while current_start_time + target_duration_ms <= segment_end_time:
        target_end_time = current_start_time + target_duration_ms

        candidate_indices = np.where(time_ms >= target_end_time)[0]
        if len(candidate_indices) == 0:
            break

        current_end_idx = int(candidate_indices[0])

        sub_trials.append(
            {
                "start_idx": int(current_start_idx),
                "end_idx": int(current_end_idx),
            }
        )

        current_start_idx = current_end_idx + 1
        if current_start_idx >= end_idx:
            break

        current_start_time = time_ms[current_start_idx]

    # 最后一段如果够长，也保留
    if current_start_idx < end_idx:
        remain_duration = time_ms[end_idx] - time_ms[current_start_idx]
        if remain_duration >= MIN_TRIAL_DURATION_MS:
            sub_trials.append(
                {
                    "start_idx": int(current_start_idx),
                    "end_idx": int(end_idx),
                }
            )

    return sub_trials

def segment_trials(df):
    """
    REST / MOVING / COOLDOWN 状态机分割 trial。
    """

    time_ms = build_time_ms(df)
    motion_curve, gyro_mag, acc_mag, acc_type = build_motion_curve(df)
    motion_curve_smooth = smooth_signal(motion_curve, window=5)

    rest_mean, rest_std = estimate_rest_baseline(motion_curve_smooth)

    start_threshold = rest_mean + START_STD_FACTOR * rest_std
    stop_threshold = rest_mean + STOP_STD_FACTOR * rest_std

    state = "REST"

    trials = []
    rejected = []

    start_candidate_idx = None
    moving_start_idx = None
    below_stop_start_idx = None
    cooldown_until_ms = -1

    for i in range(len(df)):
        t = time_ms[i]
        value = motion_curve_smooth[i]

        if state == "REST":
            if t < cooldown_until_ms:
                continue

            if value >= start_threshold:
                if start_candidate_idx is None:
                    start_candidate_idx = i

                start_duration = time_ms[i] - time_ms[start_candidate_idx]

                if start_duration >= MIN_START_DURATION_MS:
                    moving_start_idx = start_candidate_idx
                    below_stop_start_idx = None
                    state = "MOVING"
            else:
                start_candidate_idx = None

        elif state == "MOVING":
            if value <= stop_threshold:
                if below_stop_start_idx is None:
                    below_stop_start_idx = i

                stop_duration = time_ms[i] - time_ms[below_stop_start_idx]

                if stop_duration >= MIN_STOP_DURATION_MS:
                    end_idx = below_stop_start_idx

                    valid, reason = is_valid_trial(
                        df=df,
                        motion_curve=motion_curve_smooth,
                        time_ms=time_ms,
                        start_idx=moving_start_idx,
                        end_idx=end_idx,
                        rest_mean=rest_mean,
                        rest_std=rest_std,
                    )

                    if valid:
                        trials.append(
                            {
                                "start_idx": int(moving_start_idx),
                                "end_idx": int(end_idx),
                                "start_time_ms": float(time_ms[moving_start_idx]),
                                "end_time_ms": float(time_ms[end_idx]),
                                "duration_ms": float(time_ms[end_idx] - time_ms[moving_start_idx]),
                                "peak_motion": float(np.max(motion_curve_smooth[moving_start_idx:end_idx + 1])),
                                "rom_deg": float(calc_segment_rom(df, moving_start_idx, end_idx)),
                            }
                        )
                    else:
                        duration_ms = time_ms[end_idx] - time_ms[moving_start_idx]

                        if duration_ms > MAX_TRIAL_DURATION_MS:
                            sub_segments = split_long_segment_by_time(
                                df=df,
                                time_ms=time_ms,
                                start_idx=moving_start_idx,
                                end_idx=end_idx,
                                target_duration_ms=3000,
                            )

                            for sub in sub_segments:
                                s = sub["start_idx"]
                                e = sub["end_idx"]

                                sub_valid, sub_reason = is_valid_trial(
                                    df=df,
                                    motion_curve=motion_curve_smooth,
                                    time_ms=time_ms,
                                    start_idx=s,
                                    end_idx=e,
                                    rest_mean=rest_mean,
                                    rest_std=rest_std,
                                )

                                if sub_valid:
                                    trials.append(
                                        {
                                            "start_idx": int(s),
                                            "end_idx": int(e),
                                            "start_time_ms": float(time_ms[s]),
                                            "end_time_ms": float(time_ms[e]),
                                            "duration_ms": float(time_ms[e] - time_ms[s]),
                                            "peak_motion": float(np.max(motion_curve_smooth[s:e + 1])),
                                            "rom_deg": float(calc_segment_rom(df, s, e)),
                                        }
                                    )
                                else:
                                    rejected.append(
                                        {
                                            "start_idx": int(s),
                                            "end_idx": int(e),
                                            "reason": sub_reason,
                                        }
                                    )
                        else:
                            rejected.append(
                                {
                                    "start_idx": int(moving_start_idx),
                                    "end_idx": int(end_idx),
                                    "reason": reason,
                                }
                            )

                    cooldown_until_ms = time_ms[i] + COOLDOWN_MS

                    state = "COOLDOWN"
                    start_candidate_idx = None
                    moving_start_idx = None
                    below_stop_start_idx = None

            else:
                below_stop_start_idx = None

        elif state == "COOLDOWN":
            if t >= cooldown_until_ms:
                state = "REST"
                start_candidate_idx = None

    # 如果文件结束时还在 MOVING，尝试收尾
    if state == "MOVING" and moving_start_idx is not None:
        end_idx = len(df) - 1

        valid, reason = is_valid_trial(
            df=df,
            motion_curve=motion_curve_smooth,
            time_ms=time_ms,
            start_idx=moving_start_idx,
            end_idx=end_idx,
            rest_mean=rest_mean,
            rest_std=rest_std,
        )

        if valid:
            trials.append(
                {
                    "start_idx": int(moving_start_idx),
                    "end_idx": int(end_idx),
                    "start_time_ms": float(time_ms[moving_start_idx]),
                    "end_time_ms": float(time_ms[end_idx]),
                    "duration_ms": float(time_ms[end_idx] - time_ms[moving_start_idx]),
                    "peak_motion": float(np.max(motion_curve_smooth[moving_start_idx:end_idx + 1])),
                    "rom_deg": float(calc_segment_rom(df, moving_start_idx, end_idx)),
                }
            )
        else:
            rejected.append(
                {
                    "start_idx": int(moving_start_idx),
                    "end_idx": int(end_idx),
                    "reason": reason,
                }
            )

    info = {
        "acc_type": acc_type,
        "rest_mean": rest_mean,
        "rest_std": rest_std,
        "start_threshold": start_threshold,
        "stop_threshold": stop_threshold,
        "trial_count": len(trials),
        "rejected_count": len(rejected),
    }

    return trials, rejected, info


def save_trials(df, trials, input_path):
    """
    保存每个 trial 为单独 CSV。
    """

    input_name = Path(input_path).stem
    out_dir = PROJECT_ROOT / "data" / "segmented_trials" / input_name
    out_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []

    for idx, trial in enumerate(trials, start=1):
        start_idx = trial["start_idx"]
        end_idx = trial["end_idx"]

        trial_df = df.iloc[start_idx:end_idx + 1].copy()

        out_path = out_dir / f"trial_{idx:03d}.csv"
        trial_df.to_csv(out_path, index=False, encoding="utf-8")

        saved_files.append(out_path)

    return saved_files, out_dir


def main():
    if len(sys.argv) < 2:
        print("用法：")
        print("python cloud_interface/trial_segmenter.py data/raw/fused_20260526_205718.csv")
        return

    csv_path = sys.argv[1]

    df = read_fused_csv(csv_path)

    trials, rejected, info = segment_trials(df)

    saved_files, out_dir = save_trials(df, trials, csv_path)

    print("\n==============================")
    print("Trial 自动分割结果")
    print("==============================")
    print(f"输入文件：{csv_path}")
    print(f"输出目录：{out_dir}")
    print(f"加速度类型：{info['acc_type']}")
    print(f"静息均值：{info['rest_mean']:.3f}")
    print(f"静息标准差：{info['rest_std']:.3f}")
    print(f"启动阈值：{info['start_threshold']:.3f}")
    print(f"结束阈值：{info['stop_threshold']:.3f}")
    print(f"有效 trial 数量：{info['trial_count']}")
    print(f"被拒绝片段数量：{info['rejected_count']}")

    if trials:
        print("\n有效 trial：")
        for idx, trial in enumerate(trials, start=1):
            print(
                f"trial_{idx:03d}: "
                f"idx {trial['start_idx']}~{trial['end_idx']}, "
                f"time {trial['start_time_ms']:.0f}~{trial['end_time_ms']:.0f} ms, "
                f"duration {trial['duration_ms'] / 1000:.2f} s, "
                f"peak {trial['peak_motion']:.2f}, "
                f"ROM {trial['rom_deg']:.2f}"
            )

    if rejected:
        print("\n被拒绝片段：")
        for item in rejected[:10]:
            print(
                f"idx {item['start_idx']}~{item['end_idx']}: {item['reason']}"
            )

        if len(rejected) > 10:
            print(f"... 还有 {len(rejected) - 10} 个被拒绝片段未显示")

    print("\n已保存 trial 文件：")
    for path in saved_files:
        print(path)

    print("\n下一步可以运行：")
    if saved_files:
        print(f"python cloud_interface/raw_log_to_trial.py {saved_files[0]}")
    else:
        print("没有识别到有效 trial，需要调低阈值或检查数据。")


if __name__ == "__main__":
    main()
