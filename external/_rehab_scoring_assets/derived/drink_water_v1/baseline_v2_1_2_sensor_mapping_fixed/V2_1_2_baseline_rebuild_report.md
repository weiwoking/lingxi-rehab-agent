# V2.1.2 Baseline Rebuild Report

## 1. 数据来源

- 40 rep总数: 40
- 29 normal reference reps: 29
- excluded reps: 11

## 2. baseline变化

- 旧baseline来源: template/MHH_healthy_template.json
- 旧baseline版本: legacy healthy-template JSON
- 新baseline: drink_water_v2.1.2
- 新baseline目录: C:\ec\esp-claw\pc\rehab_score_service\runtime_data\derived\drink_water_v1\baseline_v2_1_2_sensor_mapping_fixed.tmp_20260806_115424_972140
- sensor_mapping_version: rehab_sensor_mapping_20260806
- 正式模板未覆盖，线上baseline未切换。

## 3. Session评分结果

| 类别 | 数量 | 平均分 | 代偿检测 |
| --- | ---: | ---: | --- |
| 正常 | 29 | 87.407241379 | 0 |
| 前倾代偿 | 5 | 72.458 | 0 |
| 混合 | 6 | 77.49 | 0 |

## 4. readiness

- baseline_artifact_readiness: READY
- score_separation_readiness: OBSERVED
- compensation_detector_readiness: NOT_VALIDATED
- production_switch_readiness: MANUAL_DECISION_REQUIRED
- decision: MANUAL_DECISION_REQUIRED

## 5. 说明

- 本报告由离线函数生成，未启动HTTP服务。
- 冻结接口路径和返回合同未修改。
- 原始CSV、旧bootstrap、正式模板和线上评分行为未修改。
