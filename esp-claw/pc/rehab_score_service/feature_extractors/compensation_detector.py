# feature_extractors/compensation_detector.py

import numpy as np


DRINK_WATER_TRUNK_COMPENSATION_RATIO_THRESHOLD = 0.09885350515694372
DRINK_WATER_TRUNK_COMPENSATION_RATIO_RULE_VERSION = "drink_water_ratio_lf_v1"
DRINK_WATER_LF_RULE_VERSION = "drink_water_lf_v2"
DRINK_WATER_TREMOR_RULE_VERSION = "drink_water_tremor_v1_1"


def _safe_float(x, default=0.0):
    try:
        if x is None:
            return default
        x = float(x)
        if np.isnan(x):
            return default
        return x
    except Exception:
        return default


def _finite_float_or_none(x):
    try:
        if x is None:
            return None
        x = float(x)
        if np.isnan(x) or np.isinf(x):
            return None
        return x
    except Exception:
        return None


def _bool_or_false(x):
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        return x.strip().lower() in {"true", "1", "yes"}
    try:
        return bool(x)
    except Exception:
        return False


def detect_compensation_4imu_4emg(
    wristF: dict,
    forearmF: dict,
    upperF: dict,
    trunkF: dict,
    emgF: dict,
    template_task: dict,
    muscles: list,
) -> dict:
    """
    代偿检测：
    1. IMU：躯干前倾/旋转、ROM不足、平滑性差、协调性差
    2. EMG：某块肌肉异常激活
    """

    compensation_types = []
    quality_findings = []
    details = {}

    # =========================
    # 1. 躯干代偿：LF / TR
    # =========================
    trunk_tr = _safe_float(trunkF.get("TrunkRotation_deg", 0.0))
    trunk_ratio = _finite_float_or_none(trunkF.get("TrunkCompensationRatio"))
    trunk_ratio_passed = (
        trunk_ratio is not None
        and trunk_ratio >= DRINK_WATER_TRUNK_COMPENSATION_RATIO_THRESHOLD
    )

    details["trunk_compensation_ratio_rule"] = {
        "trunk_compensation_ratio": None if trunk_ratio is None else round(float(trunk_ratio), 12),
        "trunk_compensation_ratio_threshold": DRINK_WATER_TRUNK_COMPENSATION_RATIO_THRESHOLD,
        "trunk_compensation_ratio_rule_passed": bool(trunk_ratio_passed),
        "trunk_compensation_ratio_rule_version": DRINK_WATER_TRUNK_COMPENSATION_RATIO_RULE_VERSION,
        "diagnostic_only": True,
    }

    lf_detected = _bool_or_false(trunkF.get("DrinkWaterLFDetected"))
    lf_neutral_roll = _finite_float_or_none(trunkF.get("DrinkWaterLFNeutralRoll_deg"))
    lf_peak_abs_roll = _finite_float_or_none(trunkF.get("DrinkWaterLFPeakAbsRoll_deg"))
    lf_max_duration = _finite_float_or_none(trunkF.get("DrinkWaterLFMaxDurationOverThreshold_s"))
    lf_positive_duration = _finite_float_or_none(trunkF.get("DrinkWaterLFPositiveDuration_s"))
    lf_negative_duration = _finite_float_or_none(trunkF.get("DrinkWaterLFNegativeDuration_s"))
    lf_roll_threshold = _finite_float_or_none(trunkF.get("DrinkWaterLFRollThreshold_deg"))
    lf_min_duration = _finite_float_or_none(trunkF.get("DrinkWaterLFMinDuration_s"))
    lf_valid_samples = _finite_float_or_none(trunkF.get("DrinkWaterLFValidSampleCount"))
    lf_rate_hz = _finite_float_or_none(trunkF.get("DrinkWaterLFEstimatedRateHz"))
    lf_status = trunkF.get("DrinkWaterLFStatus")
    lf_version = trunkF.get("DrinkWaterLFVersion") or DRINK_WATER_LF_RULE_VERSION
    details["LF"] = {
        "detected": bool(lf_detected),
        "neutral_roll_deg": lf_neutral_roll,
        "peak_abs_roll_deg": lf_peak_abs_roll,
        "max_duration_over_threshold_s": lf_max_duration,
        "positive_duration_over_threshold_s": lf_positive_duration,
        "negative_duration_over_threshold_s": lf_negative_duration,
        "roll_threshold_deg": lf_roll_threshold,
        "min_duration_s": lf_min_duration,
        "valid_sample_count": lf_valid_samples,
        "estimated_rate_hz": lf_rate_hz,
        "status": lf_status,
        "rule_version": str(lf_version),
        "reason": "IMU2 roll 相对中立位偏离持续达到喝水前倾门禁",
    }

    if lf_detected:
        compensation_types.append("LF")

    if trunk_tr >= 15:
        compensation_types.append("TR")
        details["TR"] = {
            "yaw_offset_deg": round(trunk_tr, 3),
            "reason": "躯干旋转角超过阈值"
        }

    tremor_detected = _bool_or_false(wristF.get("DrinkWaterTremorDetected"))
    tremor_fraction = _finite_float_or_none(wristF.get("DrinkWaterTremorFraction"))
    tremor_valid_windows = _finite_float_or_none(wristF.get("DrinkWaterTremorValidWindowCount"))
    tremor_positive_windows = _finite_float_or_none(wristF.get("DrinkWaterTremorPositiveWindowCount"))
    tremor_status = wristF.get("DrinkWaterTremorStatus")
    details["TREMOR"] = {
        "detected": bool(tremor_detected),
        "tremor_fraction": tremor_fraction,
        "valid_window_count": tremor_valid_windows,
        "positive_window_count": tremor_positive_windows,
        "status": tremor_status,
        "rule_version": DRINK_WATER_TREMOR_RULE_VERSION,
        "reason": "手腕 IMU4 在 3.5-6.0Hz 窄带存在持续振荡"
    }
    if tremor_detected:
        compensation_types.append("TREMOR")

    # =========================
    # 2. 上肢 ROM 代偿
    # =========================
    upper_rom = _safe_float(upperF.get("ROM", 0.0))
    rom_mean = template_task.get("ROM_mean", None)
    rom_std = template_task.get("ROM_std", None)

    if rom_mean is not None and rom_std is not None:
        rom_mean = _safe_float(rom_mean)
        rom_std = max(abs(_safe_float(rom_std)), 1e-8)

        if upper_rom < rom_mean - 1.5 * rom_std:
            quality_findings.append("insufficient_upperarm_ROM")
            details["insufficient_upperarm_ROM"] = {
                "value": upper_rom,
                "template_mean": rom_mean,
                "reason": "上臂活动范围低于模板，可能存在关节活动度不足"
            }

    # =========================
    # 3. 前臂旋转/肩肘腕协调性
    # =========================
    forearm_rotation = _safe_float(forearmF.get("rotation_range", 0.0))
    forearm_mean = template_task.get("forearm_rotation_mean", None)
    forearm_std = template_task.get("forearm_rotation_std", None)

    if forearm_mean is not None and forearm_std is not None:
        forearm_mean = _safe_float(forearm_mean)
        forearm_std = max(abs(_safe_float(forearm_std)), 1e-8)

        if abs(forearm_rotation - forearm_mean) > 2.0 * forearm_std:
            quality_findings.append("forearm_coordination_abnormal")
            details["forearm_coordination_abnormal"] = {
                "value": forearm_rotation,
                "template_mean": forearm_mean,
                "reason": "前臂旋转范围偏离健康模板，可能存在肩肘腕协调异常"
            }

    # =========================
    # 4. 平滑性代偿：速度峰值/停顿过多
    # =========================
    peaks = _safe_float(wristF.get("num_speed_peaks", 0.0))
    pauses = _safe_float(wristF.get("pause_count", 0.0))

    if peaks >= 8:
        quality_findings.append("unsmooth_motion")
        details["unsmooth_motion"] = {
            "num_speed_peaks": peaks,
            "reason": "速度峰值过多，动作不够平滑"
        }

    if pauses >= 3:
        quality_findings.append("excessive_pauses")
        details["pause_compensation"] = {
            "pause_count": pauses,
            "reason": "动作中停顿较多，可能存在发力不足或代偿调整"
        }

    # =========================
    # 5. 肌肉代偿：分肌肉 RMS/MAV/WL 与模板偏离
    # =========================
    abnormal_muscles = []

    for m in muscles:
        rms = _safe_float(emgF.get(f"{m}_RMS", 0.0))
        rms_mean = template_task.get(f"{m}_RMS_mean", None)
        rms_std = template_task.get(f"{m}_RMS_std", None)

        if rms_mean is None or rms_std is None:
            continue

        rms_mean = _safe_float(rms_mean)
        rms_std = max(abs(_safe_float(rms_std)), 1e-8)

        z = (rms - rms_mean) / rms_std

        if z > 2.0:
            abnormal_muscles.append({
                "muscle": m,
                "feature": "RMS",
                "z_score": round(float(z), 3),
                "reason": f"{m} 激活明显高于模板，可能存在异常募集或代偿"
            })

    if abnormal_muscles:
        quality_findings.append("abnormal_muscle_recruitment")
        details["abnormal_muscle_recruitment"] = abnormal_muscles

    has_compensation = len(compensation_types) > 0

    # 简单风险分：每类代偿加一点
    compensation_score = min(100, len(set(compensation_types)) * 20)

    return {
        "has_compensation": has_compensation,
        "compensation_types": sorted(list(set(compensation_types))),
        "compensation_score": compensation_score,
        "details": details,
        "quality_findings": sorted(list(set(quality_findings))),
    }
