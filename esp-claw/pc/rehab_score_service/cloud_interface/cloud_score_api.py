# cloud_interface/cloud_score_api.py

import sys
import json
import math
from pathlib import Path
import pandas as pd

from template.load_template import load_healthy_template
from scoring.score_action_integrated import get_scoring_model_info, score_action_integrated

COMPENSATION_NAME_MAP = {
    "LF": "躯干前倾",
    "TR": "躯干旋转",
    "TREMOR": "手部抖动",
    "SE": "肩部异常上提",
    "trunk_compensation": "躯干代偿",
    "excessive_trunk_ROM": "躯干活动范围过大",
    "insufficient_upperarm_ROM": "上臂活动范围不足",
    "forearm_coordination_abnormal": "前臂协调异常",
    "unsmooth_motion": "动作不平滑",
    "pause_compensation": "停顿代偿",
    "muscle_compensation": "肌肉异常募集"
}

# 让当前脚本可以导入项目根目录模块，并让默认资产不依赖调用者 cwd。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))
from scoring_assets import TEMPLATE_ROOT

DEFAULT_HEALTHY_TEMPLATE_PATH = TEMPLATE_ROOT / "MHH_healthy_template.json"


SCORE_TRIAL_SCORE_VERSION = "v1.2_reasonable_speed"

REQUIRED_TRIAL_FIELDS = [
    "TaskNumber",
    "WristMeanSpeed_mm_s",
    "NumSpeedPeaks",
    "PauseCount",
    "UpperArmROM_mm",
    "TrunkCompensationRatio",
    "Extensor_digitorum_RMS",
    "Extensor_digitorum_MAV",
    "Extensor_digitorum_WL_mean",
    "Biceps_brachii_RMS",
    "Biceps_brachii_MAV",
    "Biceps_brachii_WL_mean",
    "Triceps_brachii_RMS",
    "Triceps_brachii_MAV",
    "Triceps_brachii_WL_mean",
    "Extensor_carpi_ulnaris_RMS",
    "Extensor_carpi_ulnaris_MAV",
    "Extensor_carpi_ulnaris_WL_mean",
]


def invalid_score_trial_result(reason):
    return {
        "score_version": SCORE_TRIAL_SCORE_VERSION,
        "valid": False,
        "invalid_reason": str(reason),
    }


def _is_missing_value(value):
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def validate_score_trial_input(trial_json):
    if not isinstance(trial_json, dict):
        return "request_body_must_be_json_object"

    missing = [
        field for field in REQUIRED_TRIAL_FIELDS
        if field not in trial_json or _is_missing_value(trial_json.get(field))
    ]
    if missing:
        return "missing_required_fields: " + ",".join(missing)

    invalid_numeric = []
    for field in REQUIRED_TRIAL_FIELDS:
        try:
            value = float(trial_json[field])
        except Exception:
            invalid_numeric.append(field)
            continue
        if pd.isna(value):
            invalid_numeric.append(field)

    if invalid_numeric:
        return "invalid_numeric_fields: " + ",".join(invalid_numeric)

    end_reason = trial_json.get("end_reason", trial_json.get("task_end_reason"))
    if isinstance(end_reason, str) and end_reason.lower() in {
        "aborted",
        "cancelled",
        "canceled",
        "exception",
        "failed",
        "interrupted",
    }:
        return "task_ended_abnormally: " + end_reason

    return None


def _as_string_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _finite_float_required(value, field_name):
    try:
        parsed = float(value)
    except Exception as exc:
        raise ValueError(f"invalid_numeric_field:{field_name}") from exc
    if pd.isna(parsed) or not math.isfinite(parsed):
        raise ValueError(f"invalid_numeric_field:{field_name}")
    return parsed


def _public_compensation(raw_compensation):
    raw_compensation = raw_compensation or {}
    types = raw_compensation.get("types", raw_compensation.get("compensation_types", []))
    return {
        "detected": bool(raw_compensation.get("detected", raw_compensation.get("has_compensation", False))),
        "types": _as_string_list(types),
    }


def _public_brs_result(trial_json):
    is_brs_task = (
        trial_json.get("is_brs_assessment") is True
        or str(trial_json.get("task_type", "")).lower() in {"brs", "brs_assessment"}
        or str(trial_json.get("assessment_type", "")).lower() in {"brs", "brs_assessment"}
    )
    if not is_brs_task:
        return None

    brs = trial_json.get("brs")
    if isinstance(brs, dict) and "stage" in brs and "confidence" in brs:
        return {
            "stage": str(brs["stage"]),
            "confidence": float(brs["confidence"]),
        }

    return None


def to_public_score_trial_result(full_result, trial_json):
    scores = full_result.get("scores", {}) or {}
    final_score = scores.get("final_score")

    return {
        "score_version": SCORE_TRIAL_SCORE_VERSION,
        "valid": True,
        "final_score": round(float(final_score), 2),
        "subscores": {
            "speed_score": round(float(scores.get("speed_score", 0)), 2),
            "ROM_score": round(float(scores.get("ROM_score", 0)), 2),
            "smoothness_score": round(float(scores.get("smoothness_score", 0)), 2),
            "coordination_score": round(float(scores.get("coordination_score", 0) or 0), 2),
            "muscle_score": round(float(scores.get("muscle_score", 0)), 2),
        },
        "compensation": _public_compensation(full_result.get("compensation", {})),
        "fatigue": {
            "state": "正常",
            "need_rest": False,
            "penalty": 0,
        },
        "brs": _public_brs_result(trial_json),
        "feedback": [str(item) for item in full_result.get("feedback", [])],
    }

def json_to_score_row(trial_json):
    """
    把云端传入的单 trial JSON 转成 score_action_integrated 可以读取的一行数据。

    当前版本使用的是“已提取好的特征”格式，不直接处理原始 ADC/IMU 序列。
    """

    row = {
        "Group": trial_json.get("Group", "Stroke"),
        "SubjectID": trial_json.get("SubjectID", "cloud_user"),
        "TaskNumber": trial_json["TaskNumber"],
        "TrialID": trial_json.get("TrialID", 1),
        "FileName": trial_json.get("FileName", "cloud_trial"),

        # Wrist feature group: derived from IMU4 with rehab_sensor_mapping_20260806.
        "WristMeanSpeed_mm_s": trial_json["WristMeanSpeed_mm_s"],
        "NumSpeedPeaks": trial_json["NumSpeedPeaks"],
        "PauseCount": trial_json["PauseCount"],

        # Forearm feature group: derived from IMU3 with rehab_sensor_mapping_20260806.
        "ForearmRotationRange_deg": trial_json.get("ForearmRotationRange_deg", None),
        "ForearmMeanAngularSpeed_deg_s": trial_json.get("ForearmMeanAngularSpeed_deg_s", None),

        # Upper-arm feature group: derived from IMU1 with rehab_sensor_mapping_20260806.
        "UpperArmROM_mm": trial_json["UpperArmROM_mm"],
        "UpperArmMeanAngularSpeed_deg_s": trial_json.get("UpperArmMeanAngularSpeed_deg_s", None),

        # Trunk feature group: derived from IMU2 with rehab_sensor_mapping_20260806.
        "TrunkROM_deg": trial_json.get("TrunkROM_deg", None),
        "TrunkForwardLean_deg": trial_json.get("TrunkForwardLean_deg", 0),
        "TrunkRotation_deg": trial_json.get("TrunkRotation_deg", 0),

        # 4 路 EMG
        "Extensor_digitorum_RMS": trial_json["Extensor_digitorum_RMS"],
        "Extensor_digitorum_MAV": trial_json["Extensor_digitorum_MAV"],
        "Extensor_digitorum_WL_mean": trial_json["Extensor_digitorum_WL_mean"],

        "Biceps_brachii_RMS": trial_json["Biceps_brachii_RMS"],
        "Biceps_brachii_MAV": trial_json["Biceps_brachii_MAV"],
        "Biceps_brachii_WL_mean": trial_json["Biceps_brachii_WL_mean"],

        "Triceps_brachii_RMS": trial_json["Triceps_brachii_RMS"],
        "Triceps_brachii_MAV": trial_json["Triceps_brachii_MAV"],
        "Triceps_brachii_WL_mean": trial_json["Triceps_brachii_WL_mean"],

        "Extensor_carpi_ulnaris_RMS": trial_json["Extensor_carpi_ulnaris_RMS"],
        "Extensor_carpi_ulnaris_MAV": trial_json["Extensor_carpi_ulnaris_MAV"],
        "Extensor_carpi_ulnaris_WL_mean": trial_json["Extensor_carpi_ulnaris_WL_mean"],
    }

    if "TrunkCompensationRatio" in trial_json:
        row["TrunkCompensationRatio"] = _finite_float_required(
            trial_json["TrunkCompensationRatio"],
            "TrunkCompensationRatio",
        )

    for key in (
        "DrinkWaterTremorDetected",
        "DrinkWaterTremorFraction",
        "DrinkWaterTremorValidWindowCount",
        "DrinkWaterTremorPositiveWindowCount",
        "DrinkWaterLFDetected",
        "DrinkWaterLFNeutralRoll_deg",
        "DrinkWaterLFPeakAbsRoll_deg",
        "DrinkWaterLFMaxDurationOverThreshold_s",
        "DrinkWaterLFPositiveDuration_s",
        "DrinkWaterLFNegativeDuration_s",
        "DrinkWaterLFRollThreshold_deg",
        "DrinkWaterLFMinDuration_s",
        "DrinkWaterLFValidSampleCount",
        "DrinkWaterLFEstimatedRateHz",
    ):
        if key in trial_json and not _is_missing_value(trial_json.get(key)):
            row[key] = _finite_float_required(trial_json[key], key)

    for key in ("DrinkWaterTremorStatus", "DrinkWaterTremorVersion", "DrinkWaterLFStatus", "DrinkWaterLFVersion"):
        if key in trial_json:
            row[key] = trial_json[key]

    optional_emg_keys = [
        "Extensor_digitorum_IEMG",
        "Extensor_digitorum_SSI",
        "Biceps_brachii_IEMG",
        "Biceps_brachii_SSI",
        "Triceps_brachii_IEMG",
        "Triceps_brachii_SSI",
        "Extensor_carpi_ulnaris_IEMG",
        "Extensor_carpi_ulnaris_SSI",
    ]

    for key in optional_emg_keys:
        if key in trial_json:
            row[key] = trial_json[key]

    return pd.Series(row)


def get_score_level(total_score):
    """
    根据总分给出等级。
    """
    if total_score >= 85:
        return "优秀"
    elif total_score >= 70:
        return "良好"
    elif total_score >= 50:
        return "中等"
    else:
        return "需改进"


def score_ratio(score, max_score):
    """
    把子项分数转换成得分率。
    """
    if score is None:
        return None
    if max_score <= 0:
        return None
    return score / max_score


def generate_feedback(score_result):
    """
    根据各子项得分率生成反馈。
    兼容旧版评分和 4 IMU 评分。

    当前 4 IMU 版权重：
    speed: 15
    ROM: 20
    smoothness: 20
    coordination: 15
    muscle: 30

    注意：
    代偿不再作为扣分项进入动作质量分。
    代偿只通过 compensation 字段触发纠正提醒。
    """

    feedback = []

    speed = score_result.get("speed_score", 0)
    rom = score_result.get("ROM_score", 0)
    smoothness = score_result.get("smoothness_score", 0)
    coordination = score_result.get("coordination_score", None)
    muscle = score_result.get("muscle_score", 0)

    # 判断是否启用了 4 IMU coordination_score
    use_4imu = coordination is not None

    if use_4imu:
        speed_max = 15
        rom_max = 20
        smoothness_max = 20
        coordination_max = 15
        muscle_max = 30
    else:
        speed_max = 25
        rom_max = 25
        smoothness_max = 20
        coordination_max = None
        muscle_max = 20

    # 速度反馈
    r = score_ratio(speed, speed_max)
    if r is not None:
        if r >= 0.8:
            feedback.append("动作速度较好")
        elif r >= 0.5:
            feedback.append("动作速度一般，可适当提高动作效率")
        else:
            feedback.append("动作速度偏低，建议在保证安全的前提下提高主动运动速度")

    # ROM 反馈
    r = score_ratio(rom, rom_max)
    if r is not None:
        if r >= 0.8:
            feedback.append("上肢活动范围较好")
        elif r >= 0.5:
            feedback.append("上肢活动范围一般，建议增加主动活动幅度")
        else:
            feedback.append("上肢活动范围不足，可能存在关节活动受限或主动参与不足")

    # 平滑性反馈
    r = score_ratio(smoothness, smoothness_max)
    if r is not None:
        if r >= 0.8:
            feedback.append("动作连续性较好")
        elif r >= 0.5:
            feedback.append("动作连续性一般，存在一定停顿或速度波动")
        else:
            feedback.append("动作不够连续，存在明显停顿或速度峰值过多")

    # 协调性反馈，只有 4 IMU 模板启用后才显示
    if use_4imu:
        r = score_ratio(coordination, coordination_max)
        if r is not None:
            if r >= 0.8:
                feedback.append("前臂旋转与上肢协调性较好")
            elif r >= 0.5:
                feedback.append("前臂协调性一般，建议注意前臂旋转和上肢协同控制")
            else:
                feedback.append("前臂旋转或肩肘腕协调性不足，建议降低动作难度并加强分段控制")

    # 肌肉激活反馈
    r = score_ratio(muscle, muscle_max)
    if r is not None:
        if r >= 0.8:
            feedback.append("肌肉激活模式较接近健康模板")
        elif r >= 0.5:
            feedback.append("肌肉激活合理性一般，部分肌肉募集模式与健康模板存在差异")
        else:
            feedback.append("肌肉激活模式与健康模板差异较大，可能存在异常代偿或肌肉募集不足")

    return feedback


def _append_unique_feedback(feedback, text):
    """
    避免重复添加反馈。
    """
    text = str(text).strip()
    if text and text not in feedback:
        feedback.append(text)


def _append_compensation_feedback(feedback, compensation_result):
    """
    根据 compensation 字段追加中文代偿反馈。
    """
    compensation_result = compensation_result or {}

    has_compensation = bool(compensation_result.get("has_compensation", False))
    if not has_compensation:
        return feedback

    comp_types = compensation_result.get("compensation_types", [])
    if not isinstance(comp_types, list):
        comp_types = []

    comp_types_cn = [
        COMPENSATION_NAME_MAP.get(str(x), str(x))
        for x in comp_types
    ]

    if len(comp_types_cn) > 0:
        comp_text = "检测到可能存在代偿：" + "、".join(comp_types_cn) + "，建议暂停动作并调整姿势。"
    else:
        comp_text = "检测到可能存在代偿，建议暂停动作并调整姿势。"

    _append_unique_feedback(feedback, comp_text)
    if "LF" in comp_types:
        _append_unique_feedback(feedback, "检测到躯干前倾代偿。")
    if "TREMOR" in comp_types:
        _append_unique_feedback(feedback, "检测到手部抖动代偿，建议保持腕部稳定，降低不必要的快速往复抖动。")

    return feedback


def assess_input_quality(trial_json):
    """
    Summarize whether this trial is suitable for interpreting a formal score.

    The current raw CSV captures may be connectivity tests rather than segmented
    rehabilitation actions, so this metadata keeps the score from being read as
    clinical/training evidence without context.
    """
    data_quality = trial_json.get("data_quality", {}) or {}
    capture_context = trial_json.get("capture_context", {}) or {}
    file_name = str(trial_json.get("FileName", "")).lower()
    session_id = str(trial_json.get("session_id", "")).lower()

    warnings = []
    is_test_capture = False

    explicitly_segmented = capture_context.get("is_segmented_rehab_action") is True
    if (
        (("fused_" in file_name or "raw_log" in session_id) and not explicitly_segmented)
        or "raw_session" in session_id
        or capture_context.get("interpretation") == "pipeline_check_only"
        or capture_context.get("is_segmented_rehab_action") is False
    ):
        is_test_capture = True
        warnings.append("当前输入来自原始融合日志/测试采集，未证明是一次完整康复动作窗口。")

    row_count = data_quality.get("row_count")
    estimated_rate_hz = data_quality.get("estimated_rate_hz")
    large_gap_40 = data_quality.get("large_gap_count_over_40ms")
    large_gap_100 = data_quality.get("large_gap_count_over_100ms")
    missing_seq = data_quality.get("missing_log_seq_count_est")

    if row_count is not None and row_count < 50:
        warnings.append("采样行数偏少，动作特征稳定性不足。")

    if estimated_rate_hz is not None and estimated_rate_hz < 40:
        warnings.append("估计采样率低于 40 Hz，速度峰值和停顿等动作特征可能不稳定。")

    if large_gap_40 is not None and large_gap_40 > 0:
        warnings.append(f"存在 {large_gap_40} 个超过 40 ms 的采样间隔。")

    if large_gap_100 is not None and large_gap_100 > 0:
        warnings.append(f"存在 {large_gap_100} 个超过 100 ms 的明显采样间隔。")

    if missing_seq is not None and missing_seq > 0:
        warnings.append(f"估计存在 {missing_seq} 个日志序号缺口。")

    if is_test_capture:
        interpretation = "pipeline_check_only"
        message = "可用于验证上传、换算和评分流程，不建议解释为正式康复动作得分。"
    elif warnings:
        interpretation = "use_with_caution"
        message = "可生成评分，但需要结合采集质量和动作窗口确认后解释。"
    else:
        interpretation = "score_interpretable"
        message = "数据质量检查未发现明显问题，可按单次动作评分解释。"

    return {
        "interpretation": interpretation,
        "is_test_capture": is_test_capture,
        "message": message,
        "warnings": warnings,
    }


def build_rehab_summary(score_result, feedback, input_quality):
    """
    Build a structured therapist/patient-facing summary.

    Detailed z-score evidence is kept in score_explainability; this summary is a
    higher-level interpretation for dashboards and reports.
    """
    scores = score_result.get("scores", {}) if "scores" in score_result else score_result
    final_score = scores.get("final_score", scores.get("total_score"))
    compensation = score_result.get("compensation", {}) or {}

    key_findings = []
    training_advice = []
    correction_alerts = []

    if final_score is not None:
        if final_score >= 85:
            overall = "动作完成质量较好，可在安全前提下维持当前任务难度。"
        elif final_score >= 70:
            overall = "动作完成质量良好，但仍需关注薄弱环节。"
        elif final_score >= 50:
            overall = "动作完成质量中等，建议降低速度要求并加强分段控制。"
        else:
            overall = "动作完成质量偏低，建议降低任务难度并在看护或治疗师指导下训练。"
    else:
        overall = "本次动作质量分暂不可解释。"

    low_items = []
    item_thresholds = [
        ("速度控制", "speed_score", 15, 0.5),
        ("活动范围", "ROM_score", 20, 0.5),
        ("动作平滑性", "smoothness_score", 20, 0.5),
        ("前臂/上肢协调", "coordination_score", 15, 0.5),
        ("肌肉激活模式", "muscle_score", 30, 0.5),
    ]
    for label, key, max_score, threshold in item_thresholds:
        value = scores.get(key)
        if value is not None and max_score > 0 and value / max_score < threshold:
            low_items.append(label)

    if low_items:
        key_findings.append("主要薄弱环节：" + "、".join(low_items) + "。")
        training_advice.append("建议围绕上述薄弱环节进行慢速、分段、低难度重复训练。")
    else:
        key_findings.append("各子项未见特别突出的低分项。")

    if compensation.get("has_compensation", False):
        comp_types = [
            COMPENSATION_NAME_MAP.get(str(x), str(x))
            for x in compensation.get("compensation_types", [])
        ]
        text = "检测到可能存在代偿"
        if comp_types:
            text += "：" + "、".join(comp_types)
        correction_alerts.append(text + "。")
        training_advice.append("出现代偿时建议暂停当前动作，调整坐姿/站姿和上肢发力方式后再继续。")

    data_note = input_quality.get("message", "")
    if input_quality.get("interpretation") == "pipeline_check_only":
        data_note = "当前数据为链路/测试采集，只能用于验证评分流程，不建议解释为正式康复动作表现。"

    return {
        "overall": overall,
        "key_findings": key_findings,
        "training_advice": training_advice,
        "correction_alerts": correction_alerts,
        "data_note": data_note,
        "raw_feedback": feedback,
    }


def score_trial_full_json(trial_json, template_path=DEFAULT_HEALTHY_TEMPLATE_PATH):
    """
    云端评分入口：
    输入单 trial JSON
    输出评分 JSON

    当前接口只输出动作质量评分、代偿纠正提醒、可解释证据和输入质量信息。
    """

    template = load_healthy_template(template_path)
    row = json_to_score_row(trial_json)

    score_result = score_action_integrated(row, template)

    total_score = score_result["total_score"]
    final_score = score_result.get("final_score", total_score)

    level = get_score_level(final_score)

    feedback = generate_feedback(score_result)

    # 合并 score_action_integrated 里已有的普通反馈
    # 注意：这里会过滤掉英文代偿反馈，避免重复显示。
    extra_feedback = score_result.get("feedback", "")
    if extra_feedback:
        for item in str(extra_feedback).split("；"):
            item = item.strip()

            # 过滤旧版英文代偿反馈
            if item.startswith("检测到可能存在代偿：") and (
                "trunk_compensation" in item
                or "forearm_coordination_abnormal" in item
                or "muscle_compensation" in item
                or "unsmooth_motion" in item
                or "pause_compensation" in item
                or "excessive_trunk_ROM" in item
                or "insufficient_upperarm_ROM" in item
            ):
                continue

            _append_unique_feedback(feedback, item)

    # 统一追加中文代偿反馈
    compensation_result = score_result.get("compensation", {}) or {}
    feedback = _append_compensation_feedback(feedback, compensation_result)
    input_quality = assess_input_quality(trial_json)

    for warning in input_quality.get("warnings", []):
        _append_unique_feedback(feedback, warning)

    summary_source = {
        **score_result,
        "scores": {
            "speed_score": round(score_result["speed_score"], 2),
            "ROM_score": round(score_result["ROM_score"], 2),
            "smoothness_score": round(score_result["smoothness_score"], 2),
            "coordination_score": (
                round(score_result["coordination_score"], 2)
                if "coordination_score" in score_result
                else None
            ),
            "muscle_score": round(score_result["muscle_score"], 2),
            "total_score": round(total_score, 2),
            "final_score": round(final_score, 2),
        },
    }
    rehab_summary = build_rehab_summary(
        summary_source,
        feedback=feedback,
        input_quality=input_quality,
    )

    return {
        "patient_id": trial_json.get("patient_id", trial_json.get("SubjectID", "cloud_user")),
        "session_id": trial_json.get("session_id", "default_session"),
        "trial_id": trial_json.get("TrialID", 1),
        "task_number": trial_json["TaskNumber"],

        "scores": {
            "speed_score": round(score_result["speed_score"], 2),
            "ROM_score": round(score_result["ROM_score"], 2),
            "smoothness_score": round(score_result["smoothness_score"], 2),
            "coordination_score": (
                round(score_result["coordination_score"], 2)
                if "coordination_score" in score_result
                else None
            ),
            "muscle_score": round(score_result["muscle_score"], 2),

            # 代偿不扣分，这里保留字段只是为了兼容旧前端/旧接口
            "compensation_penalty": round(score_result.get("compensation_penalty", 0), 2),
            "total_score": round(total_score, 2),
            "final_score": round(final_score, 2),
        },

        "level": level,
        "scoring_model": get_scoring_model_info(),
        "score_explainability": score_result.get("score_explainability", {}),
        "rehab_summary": rehab_summary,
        "feedback": feedback,

        # 代偿检测结果
        "compensation": compensation_result,

        # 输入质量与解释边界，防止把测试 CSV 误解为正式康复动作评分。
        "input_quality": input_quality,

        # 原始 CSV 转换时附带的硬件参数与数据质量信息，便于云端追溯。
        "input_metadata": {
            "hardware": trial_json.get("hardware", {}),
            "data_quality": trial_json.get("data_quality", {}),
            "capture_context": trial_json.get("capture_context", {})
        }
    }


def score_trial_json(trial_json, template_path=DEFAULT_HEALTHY_TEMPLATE_PATH):
    invalid_reason = validate_score_trial_input(trial_json)
    if invalid_reason:
        return invalid_score_trial_result(invalid_reason)

    try:
        full_result = score_trial_full_json(trial_json, template_path=template_path)
    except Exception as exc:
        return invalid_score_trial_result("scoring_failed: " + str(exc))

    return to_public_score_trial_result(full_result, trial_json)


if __name__ == "__main__":
    # 本地测试用示例
    example_trial = {
        "patient_id": "S001",
        "session_id": "test_session_001",
        "Group": "Stroke",
        "SubjectID": "S001",
        "TaskNumber": 12,
        "TrialID": 1,
        "FileName": "cloud_test_trial",

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
        "Extensor_carpi_ulnaris_WL_mean": 0.25,
    }

    result = score_trial_json(example_trial)
    print(json.dumps(result, ensure_ascii=False, indent=4))
