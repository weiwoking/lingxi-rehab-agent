# 喝水前倾代偿阈值扫描 V1

- 扫描版本：`drink_water_compensation_threshold_scan_v1`
- 输入目录：`C:\ec\esp-claw\pc\rehab_score_service\runtime_data\derived\drink_water_v1\compensation_feature_diagnosis_v1_1`
- 总 rep：40
- normal / compensation：33 / 7
- 生产 detector：未修改
- 正式 baseline：未修改
- 阈值：未写入生产代码
- 结论性质：exploratory_only / current_dataset_only / not_clinically_validated

## 候选规则
- safety_candidate: `TrunkCompensationRatio >= 0.0988535051569` TP=7 FN=0 FP=0 TN=33 F1=1.0000
- balanced_candidate: `TrunkCompensationRatio >= 0.0988535051569` TP=7 FN=0 FP=0 TN=33 F1=1.0000
- demo_candidate: `TrunkCompensationRatio >= 0.0988535051569` TP=7 FN=0 FP=0 TN=33 F1=1.0000
- recommended_candidate: `TrunkCompensationRatio >= 0.0988535051569` TP=7 FN=0 FP=0 TN=33 F1=1.0000

## 人工决策

`manual_decision_required` 固定为 true。即使存在 Recall=1.0 且 FP<=1 的候选规则，也不得自动接入生产 detector。
