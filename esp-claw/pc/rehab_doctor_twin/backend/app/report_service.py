from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Any


def _project_rep_details(rep_metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    semg_notes = [
        "右侧肱二头肌募集偏高，指伸肌与腕伸稳定仍需协同",
        "右侧指伸肌、肱二头肌和尺侧腕伸肌节律改善",
        "右侧4路表面肌电活动可接受，躯干前倾时协同略受影响",
        "右侧肱二头肌、肱三头肌和腕伸稳定协调改善",
        "右侧指伸肌、肱二头肌、肱三头肌和尺侧腕伸肌协同良好",
    ]
    projected: list[dict[str, Any]] = []
    for rep in rep_metrics:
        visible_keys = {
            "rep_index",
            "final_score",
            "speed_score",
            "ROM_score",
            "smoothness_score",
            "coordination_score",
            "muscle_score",
            "ROM",
            "smoothness",
            "coordination",
            "muscle_performance",
            "fatigue",
            "compensation",
            "feedback",
        }
        item = {key: value for key, value in rep.items() if key in visible_keys}
        rep_index = int(rep.get("rep_index", len(projected) + 1))
        if 1 <= rep_index <= len(semg_notes):
            item["muscle_performance"] = semg_notes[rep_index - 1]
        item["finger_extensors_right_activation"] = rep.get("finger_extensors_right_activation", 0)
        item["biceps_brachii_right_activation"] = rep.get("biceps_brachii_right_activation", 0)
        item["triceps_brachii_right_activation"] = rep.get("triceps_brachii_right_activation", 0)
        item["extensor_carpi_ulnaris_right_activation"] = rep.get("extensor_carpi_ulnaris_right_activation", 0)
        projected.append(item)
    return projected


def _avg_activation(rep_metrics: list[dict[str, Any]], key: str) -> int:
    values = [rep.get(key, 0) for rep in rep_metrics]
    return round(mean(values) * 100)


def build_report(snapshot: dict[str, Any], scenario: dict[str, Any], session_id: str) -> dict[str, Any]:
    scores = list(snapshot.get("scores", []))
    final_score = scores[-1]["score"]["final_score"] if scores else 0
    rep_metrics = list(scenario["rep_metrics"])
    return {
        "title": "脑卒中上肢康复训练评估报告",
        "patient": {
            "patient_id": "P-0217",
            "affected_side": "右侧上肢",
            "brs": "IV期",
            "task": "拿杯喝水",
            "difficulty": "中低",
            "training_time": "约70秒演示流程",
            "session_id": session_id,
            "device_id": "P4_REHAB_01",
        },
        "completion": {
            "planned_reps": 5,
            "completed_reps": 5,
            "valid_reps": 5,
            "duration_sec": scenario["duration_sec"],
            "pause_count": 0,
            "fully_completed": True,
            "end_reason": "演示训练完整完成",
        },
        "score": {
            "score_version": "demo-score-1.0",
            "valid": True,
            "final_score": final_score,
            "subscores": {
                "speed_score": 84,
                "ROM_score": 88,
                "smoothness_score": 85,
                "coordination_score": 86,
                "muscle_score": 83,
            },
            "compensation": {"trunk_flexion": "第3次轻度躯干前倾，后续改善"},
            "fatigue": {"level": "轻度", "rest_recommended": False},
            "brs": None,
            "feedback": ["保持躯干稳定", "继续强化右肩关节主动前屈"],
        },
        "rep_details": _project_rep_details(rep_metrics),
        "kinematics": {
            "max_shoulder_flexion_deg": 72,
            "max_shoulder_abduction_deg": 32,
            "max_elbow_flexion_deg": 112,
            "wrist_range_deg": 28,
            "peak_angular_velocity_deg_s": 92,
            "movement_duration_sec": 8.6,
            "trajectory_match": 91,
        },
        "emg_analysis": {
            "finger_extensors_right_avg_activation": _avg_activation(rep_metrics, "finger_extensors_right_activation"),
            "biceps_brachii_right_avg_activation": _avg_activation(rep_metrics, "biceps_brachii_right_activation"),
            "triceps_brachii_right_avg_activation": _avg_activation(rep_metrics, "triceps_brachii_right_activation"),
            "extensor_carpi_ulnaris_right_avg_activation": _avg_activation(rep_metrics, "extensor_carpi_ulnaris_right_activation"),
            "signal_quality": "稳定",
            "coordination": "后程协同改善",
            "label": "4路表面肌电归一化活动强度",
            "note": "4路表面肌电分别采集右侧指伸肌、肱二头肌、肱三头肌和尺侧腕伸肌的归一化活动强度，用于反映手指伸展准备、肘屈伸募集及腕部稳定状态，不代表直接肌力测量。",
        },
        "compensation_analysis": {
            "finding": "第3次出现轻度躯干前倾",
            "phase": "抬杯阶段",
            "severity": "轻度",
            "improved_afterwards": True,
            "score_impact": "轻度影响第3次动作协调性评分",
        },
        "fatigue_analysis": {
            "level": "轻度",
            "trend": "后程略有上升",
            "late_session_change": "第5次出现轻度疲劳但未达到强制休息阈值",
            "rest_recommended": False,
        },
        "summary": (
            "患者使用右侧上肢完成5次拿杯喝水训练，单次动作得分由76分提升至86分，整体呈改善趋势。"
            "第3次动作在抬杯阶段检测到轻度躯干前倾，经过实时提醒后，第4次和第5次动作代偿程度降低。"
            "右肩、右肘活动范围基本达到训练目标，动作平滑度和协调性后程有所提升。"
        ),
        "next_recommendations": [
            "保持当前难度",
            "强化右肩关节主动前屈",
            "继续减少躯干前倾参与",
            "关注动作后程疲劳",
            "下次建议完成5次同类训练",
        ],
        "versions": {
            "source_mode": "DEMO DATA",
            "score_version": "demo-score-1.0",
            "schema_version": "1.0",
            "device_id": "P4_REHAB_01",
            "scenario_version": scenario["version"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "disclaimer": "本报告由系统根据训练传感数据和评分结果自动生成，仅供康复专业人员参考，不构成独立医学诊断。",
        "demo_notice": "演示数据，不代表真实患者临床结果。",
        "sensor_mapping_notice": "本次数字孪生姿态由后腰、右上臂、右前臂和右腕部4个IMU的相对姿态映射得到。4路表面肌电分别采集右侧指伸肌、肱二头肌、肱三头肌和尺侧腕伸肌的归一化活动强度，用于反映手指伸展准备、肘屈伸募集及腕部稳定状态，不代表直接肌力测量。",
    }
