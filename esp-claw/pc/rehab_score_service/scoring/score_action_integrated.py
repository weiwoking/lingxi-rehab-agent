# scoring/score_action_integrated.py

import numpy as np

from feature_extractors.compensation_detector import detect_compensation_4imu_4emg
from feature_extractors.extract_features import (
    MUSCLES,
    extract_wrist_features,
    extract_forearm_features,
    extract_upperarm_features,
    extract_trunk_features,
    extract_emg_features
)


SCORING_MODEL_INFO = {
    "model_name": "rule_based_multimodal_upper_limb_score",
    "model_version": "v1.3-action-compensation-only",
    "method": "healthy-template z-score with rule-based compensation alert",
    "action_quality_weights_4imu_4emg": {
        "speed": 15,
        "ROM": 20,
        "smoothness": 20,
        "coordination": 15,
        "muscle_activation": 30,
    },
    "action_quality_weights_legacy": {
        "speed": 25,
        "ROM": 25,
        "smoothness": 20,
        "muscle_activation": 20,
    },
    "emg_features": ["RMS", "MAV", "WL"],
    "safety_policy": {
        "compensation_affects_score": True,
        "reason": "Drink-water LF and TREMOR add final-score compensation penalties without changing the five subscores.",
    },
    "clinical_status": "engineering prototype; not a clinical diagnosis",
}

LF_EXTRA_PENALTY = 10.0
TREMOR_PENALTY = 15.0


def get_scoring_model_info():
    return SCORING_MODEL_INFO.copy()


def safe_std(std_value, eps=1e-8):
    """
    防止 std 为 0 或 NaN。
    """
    if std_value is None:
        return 1.0

    try:
        if np.isnan(std_value):
            return 1.0
    except TypeError:
        return 1.0

    if abs(float(std_value)) < eps:
        return 1.0

    return float(std_value)


def clip_score(score, min_score=0, max_score=100):
    """
    限制分数范围。
    """
    return max(min_score, min(max_score, float(score)))


def calculate_additional_compensation_penalty(compensation_result):
    comp_types = compensation_result.get("compensation_types", []) if compensation_result else []
    if not isinstance(comp_types, list):
        comp_types = []
    type_set = {str(item) for item in comp_types}
    penalty = 0.0
    breakdown = {}
    if "LF" in type_set:
        penalty += LF_EXTRA_PENALTY
        breakdown["LF"] = LF_EXTRA_PENALTY
    if "TREMOR" in type_set:
        penalty += TREMOR_PENALTY
        breakdown["TREMOR"] = TREMOR_PENALTY
    return penalty, breakdown


def score_by_z(value, mean, std, max_score, mode='two_sided'):
    """
    基于健康模板 Z-score 的评分。

    mode:
    - two_sided: 越接近健康模板越好
    - lower_better: 越低越好，例如速度峰值数、停顿数
    - higher_better: 越高越好，例如 ROM
    """

    if value is None or mean is None:
        return max_score * 0.6

    try:
        value = float(value)
        mean = float(mean)
    except Exception:
        return max_score * 0.6

    std = safe_std(std)

    z = (value - mean) / std
    abs_z = abs(z)

    if mode == 'two_sided':
        score = max_score / (1 + 0.2 * abs_z)

    elif mode == 'lower_better':
        if value <= mean:
            score = max_score
        else:
            score = max_score / (1 + 0.3 * abs_z)

    elif mode == 'higher_better':
        if value >= mean:
            score = max_score
        else:
            score = max_score / (1 + 0.3 * abs_z)

    else:
        score = max_score / (1 + 0.2 * abs_z)

    return clip_score(score, 0, max_score)


def z_score_detail(value, mean, std, max_score, mode='two_sided'):
    """
    Return score evidence for one template-based feature.
    """
    score = score_by_z(value, mean, std, max_score=max_score, mode=mode)

    try:
        value_f = float(value) if value is not None else None
        mean_f = float(mean) if mean is not None else None
        std_f = safe_std(std)
        z = None if value_f is None or mean_f is None else (value_f - mean_f) / std_f
    except Exception:
        value_f = None
        mean_f = None
        std_f = None
        z = None

    return {
        "value": value_f,
        "template_mean": mean_f,
        "template_std": std_f,
        "z_score": round(float(z), 4) if z is not None else None,
        "score": round(float(score), 6),
        "max_score": max_score,
        "mode": mode,
    }


def reasonable_range_speed_score(value, config, max_score=15.0):
    if not isinstance(config, dict) or config.get("semantics") != "reasonable_range":
        return None
    try:
        value = float(value)
        lower_good = float(config["lower_good"])
        upper_good = float(config["upper_good"])
        lower_extreme = float(config["lower_extreme"])
        upper_extreme = float(config["upper_extreme"])
        min_score = float(config.get("min_score", 5.0))
    except Exception:
        return None
    if not all(np.isfinite(item) for item in (value, lower_good, upper_good, lower_extreme, upper_extreme, min_score)):
        return None
    if not (lower_extreme < lower_good <= upper_good < upper_extreme):
        return None
    if lower_good <= value <= upper_good:
        return clip_score(max_score, 0, max_score)
    if value < lower_good:
        if value <= lower_extreme:
            return clip_score(min_score, 0, max_score)
        score = min_score + (max_score - min_score) * ((value - lower_extreme) / (lower_good - lower_extreme))
        return clip_score(score, 0, max_score)
    if value >= upper_extreme:
        return clip_score(min_score, 0, max_score)
    score = min_score + (max_score - min_score) * ((upper_extreme - value) / (upper_extreme - upper_good))
    return clip_score(score, 0, max_score)


def speed_score_from_template(value, template_task, max_score=15.0):
    configured = reasonable_range_speed_score(value, template_task.get("speed_scoring"), max_score=max_score)
    if configured is not None:
        return configured
    return score_by_z(
        value,
        template_task.get('speed_mean', None),
        template_task.get('speed_std', None),
        max_score=max_score,
        mode='two_sided',
    )


def speed_score_detail(value, template_task, max_score=15.0):
    configured = reasonable_range_speed_score(value, template_task.get("speed_scoring"), max_score=max_score)
    if configured is None:
        return z_score_detail(
            value,
            template_task.get('speed_mean', None),
            template_task.get('speed_std', None),
            max_score=max_score,
            mode='two_sided',
        )
    config = template_task.get("speed_scoring")
    return {
        "value": float(value),
        "score": round(float(configured), 6),
        "max_score": max_score,
        "mode": "reasonable_range",
        "formula": config.get("formula", "piecewise_linear_plateau"),
        "lower_good": float(config["lower_good"]),
        "upper_good": float(config["upper_good"]),
        "lower_extreme": float(config["lower_extreme"]),
        "upper_extreme": float(config["upper_extreme"]),
        "min_score": float(config.get("min_score", 5.0)),
    }


def build_muscle_evidence(emgF, template_task):
    evidence = {}

    for m in MUSCLES:
        wl_value = emgF.get(f'{m}_WL', emgF.get(f'{m}_WL_mean', 0))
        evidence[m] = {
            "RMS": z_score_detail(
                emgF.get(f'{m}_RMS', 0),
                template_task.get(f'{m}_RMS_mean', None),
                template_task.get(f'{m}_RMS_std', None),
                max_score=3,
                mode='two_sided',
            ),
            "MAV": z_score_detail(
                emgF.get(f'{m}_MAV', 0),
                template_task.get(f'{m}_MAV_mean', None),
                template_task.get(f'{m}_MAV_std', None),
                max_score=3,
                mode='two_sided',
            ),
            "WL": z_score_detail(
                wl_value,
                template_task.get(f'{m}_WL_mean', None),
                template_task.get(f'{m}_WL_std', None),
                max_score=2,
                mode='two_sided',
            ),
        }

    return evidence


def get_task_template(row, template):
    """
    兼容两种输入：
    1. template 是完整模板：template[12], template[18], template[21]
    2. template 已经是某个 task 的模板
    """

    task = int(row['TaskNumber'])

    if task in template:
        return template[task]

    if str(task) in template:
        return template[str(task)]

    return template


def has_4imu_template(template_task):
    """
    判断当前模板是否包含前臂 IMU 字段。
    """
    return (
        'forearm_rotation_mean' in template_task
        and 'forearm_rotation_std' in template_task
    )


def calculate_muscle_score(emgF, template_task, max_score=20):
    """
    肌肉激活合理性评分。
    比较 4 路 EMG 的 RMS / MAV / WL 与健康模板的差异。

    注意：
    extract_emg_features(row) 里可能叫 xxx_WL，
    原始输入里可能叫 xxx_WL_mean。
    这里优先取 xxx_WL，取不到再取 xxx_WL_mean。
    """

    raw_scores = []

    for m in MUSCLES:
        rms_score = score_by_z(
            emgF.get(f'{m}_RMS', 0),
            template_task.get(f'{m}_RMS_mean', None),
            template_task.get(f'{m}_RMS_std', None),
            max_score=3,
            mode='two_sided'
        )

        mav_score = score_by_z(
            emgF.get(f'{m}_MAV', 0),
            template_task.get(f'{m}_MAV_mean', None),
            template_task.get(f'{m}_MAV_std', None),
            max_score=3,
            mode='two_sided'
        )

        wl_value = emgF.get(f'{m}_WL', emgF.get(f'{m}_WL_mean', 0))

        wl_score = score_by_z(
            wl_value,
            template_task.get(f'{m}_WL_mean', None),
            template_task.get(f'{m}_WL_std', None),
            max_score=2,
            mode='two_sided'
        )

        raw_scores.extend([rms_score, mav_score, wl_score])

    # 4 块肌肉，每块 3 + 3 + 2 = 8 分，原始满分 32
    raw_total = sum(raw_scores)
    raw_max = 32

    muscle_score = raw_total / raw_max * max_score

    return clip_score(muscle_score, 0, max_score)


def calculate_coordination_score(forearmF, template_task, max_score=15):
    """
    4 IMU 新增：前臂旋转 / 协调性评分。

    当前使用：
    - rotation_range
    - mean_angular_speed
    """

    if not has_4imu_template(template_task):
        return None

    rotation_score = score_by_z(
        forearmF.get('rotation_range', None),
        template_task.get('forearm_rotation_mean', None),
        template_task.get('forearm_rotation_std', None),
        max_score=10,
        mode='two_sided'
    )

    angular_speed_score = score_by_z(
        forearmF.get('mean_angular_speed', None),
        template_task.get('forearm_angular_speed_mean', None),
        template_task.get('forearm_angular_speed_std', None),
        max_score=5,
        mode='two_sided'
    )

    coordination_score = rotation_score + angular_speed_score

    return clip_score(coordination_score, 0, max_score)


def generate_feedback(score_result, use_4imu=False):
    """
    根据子项分数生成简要反馈。
    """

    feedback = []

    if score_result['speed_score'] < 8:
        feedback.append("动作速度偏离健康模板较明显")

    if score_result['ROM_score'] < 10:
        feedback.append("上肢活动范围不足")

    if score_result['smoothness_score'] < 10:
        feedback.append("动作不够连续，存在停顿或速度峰值过多")

    if use_4imu and score_result.get('coordination_score', 15) < 8:
        feedback.append("前臂旋转或肩肘腕协调性不足")

    if score_result['muscle_score'] < 10:
        feedback.append("肌肉激活模式与健康模板差异较大")

    # 新增：代偿检测反馈
    compensation = score_result.get("compensation", None)
    if compensation and compensation.get("has_compensation", False):
        comp_types = compensation.get("compensation_types", [])
        if comp_types:
            feedback.append("检测到可能存在代偿：" + "、".join(comp_types))
        if "LF" in comp_types:
            feedback.append("检测到躯干前倾代偿")
        if "TREMOR" in comp_types:
            feedback.append("检测到手部抖动代偿，建议保持腕部稳定，降低不必要的快速往复抖动")

    return "；".join(feedback)


def score_action_integrated(
    row,
    template,
    enable_compensation=True
):
    """
    单 trial 综合评分。

    兼容：
    1. 旧版 3 IMU / MHH 表格
    2. 新版 4 IMU + 4 EMG 表格

    输出：
    - scores: 动作质量分
    - compensation: 代偿检测结果
    - score_explainability: 可解释评分证据

    旧代码只传 row, template 仍然可以运行。
    """

    template_task = get_task_template(row, template)

    wristF = extract_wrist_features(row)
    forearmF = extract_forearm_features(row)
    upperF = extract_upperarm_features(row)
    trunkF = extract_trunk_features(row)
    emgF = extract_emg_features(row)

    use_4imu = has_4imu_template(template_task)

    # =========================
    # 旧版权重：保持兼容
    # =========================
    if not use_4imu:
        speed_max = 25
        ROM_max = 25
        smoothness_max = 20
        muscle_max = 20
        penalty_max = 20

        speed_score = speed_score_from_template(
            wristF['mean_speed'],
            template_task,
            max_score=speed_max,
        )

        ROM_score = score_by_z(
            upperF['ROM'],
            template_task.get('ROM_mean', None),
            template_task.get('ROM_std', None),
            max_score=ROM_max,
            mode='two_sided'
        )

        peaks_score = score_by_z(
            wristF['num_speed_peaks'],
            template_task.get('num_speed_peaks_mean', 3),
            template_task.get('num_speed_peaks_std', 1),
            max_score=smoothness_max * 0.6,
            mode='two_sided'
        )

        pause_score = score_by_z(
            wristF['pause_count'],
            template_task.get('pause_count_mean', 0),
            template_task.get('pause_count_std', 1),
            max_score=smoothness_max * 0.4,
            mode='lower_better'
        )

        smoothness_score = peaks_score + pause_score
        smoothness_score = clip_score(smoothness_score, 0, smoothness_max)

        muscle_score = calculate_muscle_score(
            emgF,
            template_task,
            max_score=muscle_max
        )

        # 代偿不再作为扣分项，只作为动作安全/动作纠正提醒
        compensation_penalty = 0.0

        total_score = (
            speed_score
            + ROM_score
            + smoothness_score
            + muscle_score
        )

        total_score = clip_score(total_score, 0, 100)

        result = {
            'speed_score': round(speed_score, 6),
            'ROM_score': round(ROM_score, 6),
            'smoothness_score': round(smoothness_score, 6),
            'muscle_score': round(muscle_score, 6),
            'compensation_penalty': round(compensation_penalty, 6),
            'total_score': round(total_score, 6),
            'final_score': round(total_score, 6),

            # 旧版 3 IMU 不做完整 4IMU 代偿检测
            'compensation': {
                'has_compensation': compensation_penalty > 0,
                'compensation_types': ['trunk_compensation'] if compensation_penalty > 0 else [],
                'compensation_score': round(compensation_penalty, 6),
                'details': {
                    'note': '旧版 3 IMU 模式仅根据 TrunkCompensationRatio 进行躯干代偿扣分'
                }
            }
        }

        result['feedback'] = generate_feedback(result, use_4imu=False)

        return result

    # =========================
    # 新版 4 IMU 权重
    # =========================
    speed_max = 15
    ROM_max = 20
    smoothness_max = 20
    coordination_max = 15
    muscle_max = 30
    penalty_max = 20

    speed_score = speed_score_from_template(
        wristF['mean_speed'],
        template_task,
        max_score=speed_max,
    )

    ROM_score = score_by_z(
        upperF['ROM'],
        template_task.get('ROM_mean', None),
        template_task.get('ROM_std', None),
        max_score=ROM_max,
        mode='two_sided'
    )

    peaks_score = score_by_z(
        wristF['num_speed_peaks'],
        template_task.get('num_speed_peaks_mean', 3),
        template_task.get('num_speed_peaks_std', 1),
        max_score=smoothness_max * 0.6,
        mode='two_sided'
    )

    pause_score = score_by_z(
        wristF['pause_count'],
        template_task.get('pause_count_mean', 0),
        template_task.get('pause_count_std', 1),
        max_score=smoothness_max * 0.4,
        mode='lower_better'
    )

    smoothness_score = peaks_score + pause_score
    smoothness_score = clip_score(smoothness_score, 0, smoothness_max)

    coordination_score = calculate_coordination_score(
        forearmF,
        template_task,
        max_score=coordination_max
    )

    if coordination_score is None:
        coordination_score = 0

    muscle_score = calculate_muscle_score(
        emgF,
        template_task,
        max_score=muscle_max
    )
    score_explainability = {
        "mode": "4imu_4emg",
        "weights": SCORING_MODEL_INFO["action_quality_weights_4imu_4emg"],
        "features": {
            "speed": speed_score_detail(
                wristF['mean_speed'],
                template_task,
                max_score=speed_max,
            ),
            "ROM": z_score_detail(
                upperF['ROM'],
                template_task.get('ROM_mean', None),
                template_task.get('ROM_std', None),
                max_score=ROM_max,
                mode='two_sided',
            ),
            "smoothness": {
                "num_speed_peaks": z_score_detail(
                    wristF['num_speed_peaks'],
                    template_task.get('num_speed_peaks_mean', 3),
                    template_task.get('num_speed_peaks_std', 1),
                    max_score=smoothness_max * 0.6,
                    mode='two_sided',
                ),
                "pause_count": z_score_detail(
                    wristF['pause_count'],
                    template_task.get('pause_count_mean', 0),
                    template_task.get('pause_count_std', 1),
                    max_score=smoothness_max * 0.4,
                    mode='lower_better',
                ),
                "score": round(float(smoothness_score), 6),
                "max_score": smoothness_max,
            },
            "coordination": {
                "forearm_rotation": z_score_detail(
                    forearmF.get('rotation_range', None),
                    template_task.get('forearm_rotation_mean', None),
                    template_task.get('forearm_rotation_std', None),
                    max_score=10,
                    mode='two_sided',
                ),
                "forearm_angular_speed": z_score_detail(
                    forearmF.get('mean_angular_speed', None),
                    template_task.get('forearm_angular_speed_mean', None),
                    template_task.get('forearm_angular_speed_std', None),
                    max_score=5,
                    mode='two_sided',
                ),
                "score": round(float(coordination_score), 6),
                "max_score": coordination_max,
            },
            "muscle_activation": {
                "score": round(float(muscle_score), 6),
                "max_score": muscle_max,
                "muscles": build_muscle_evidence(emgF, template_task),
            },
        },
        "note": "Evidence fields show raw feature values, healthy-template statistics, z-scores and sub-scores for calibration/debugging.",
    }

    compensation_penalty = 0.0

    base_score = (
        speed_score
        + ROM_score
        + smoothness_score
        + coordination_score
        + muscle_score
    )
    total_score = clip_score(base_score, 0, 100)

    # =========================
    # 新增：4 IMU + 4 EMG 代偿检测
    # =========================
    if enable_compensation:
        compensation_result = detect_compensation_4imu_4emg(
            wristF=wristF,
            forearmF=forearmF,
            upperF=upperF,
            trunkF=trunkF,
            emgF=emgF,
            template_task=template_task,
            muscles=MUSCLES
        )
        compensation_alert = compensation_result.get("has_compensation", False)

        if compensation_alert:
            compensation_result["suggestion"] = "检测到可能存在代偿，请暂停当前动作，调整姿势后再继续训练。"
        else:
            compensation_result["suggestion"] = "未检测到明显代偿。"
    
    else:
        compensation_result = {
            'has_compensation': False,
            'compensation_types': [],
            'compensation_score': 0,
            'details': {
                'note': '代偿检测未启用'
            }
        }

    compensation_penalty, penalty_breakdown = calculate_additional_compensation_penalty(compensation_result)
    compensation_result["additional_penalty"] = round(compensation_penalty, 6)
    compensation_result["penalty_breakdown"] = penalty_breakdown
    compensation_result.setdefault("details", {})
    compensation_result["details"]["additional_penalty"] = {
        "LF_extra_penalty": LF_EXTRA_PENALTY,
        "TREMOR_penalty": TREMOR_PENALTY,
        "applied_penalty": round(compensation_penalty, 6),
        "breakdown": penalty_breakdown,
        "note": "Penalty is applied once at final-score calculation and does not modify subscores.",
    }
    total_score = clip_score(base_score - compensation_penalty, 0, 100)

    result = {
        'speed_score': round(speed_score, 6),
        'ROM_score': round(ROM_score, 6),
        'smoothness_score': round(smoothness_score, 6),
        'coordination_score': round(coordination_score, 6),
        'muscle_score': round(muscle_score, 6),
        'compensation_penalty': round(compensation_penalty, 6),
        'total_score': round(base_score, 6),
        'final_score': round(total_score, 6),
        'score_explainability': score_explainability,

        # 新增：代偿检测结果
        'compensation': compensation_result
    }

    result['feedback'] = generate_feedback(result, use_4imu=True)

    if compensation_result.get("has_compensation", False):
        comp_types = compensation_result.get("compensation_types", [])
        comp_text = "检测到可能存在代偿：" + "、".join(comp_types) + "，建议暂停动作并调整姿势。"
        result["feedback"] = (result.get("feedback", "") + "；" + comp_text).strip("；")

        result['feedback'] = generate_feedback(result, use_4imu=True)

    return result
