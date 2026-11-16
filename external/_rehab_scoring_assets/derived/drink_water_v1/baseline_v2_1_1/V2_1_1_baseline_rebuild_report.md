# V2.1.1 Baseline Rebuild Report

## 1. 数据来源

- 40 rep总数: 40
- 29 normal reference reps: 29
- excluded reps: 11

## 2. baseline变化

- 旧baseline来源: template/MHH_healthy_template.json
- 旧baseline版本: legacy healthy-template JSON
- 新baseline: drink_water_v2.1.1
- 新baseline目录: C:\ec\esp-claw\pc\rehab_score_service\runtime_data\derived\drink_water_v1\baseline_v2_1_1
- 正式模板未覆盖，线上baseline未切换。

## 3. Session评分结果

| 类别 | 数量 | 平均分 | 代偿检测 |
| --- | ---: | ---: | --- |
| 正常 | 29 | 87.569310345 | 0 |
| 前倾代偿 | 5 | 64.244 | 0 |
| 混合 | 6 | 72.656666667 | 0 |

## 4. 建议

READY_FOR_BASELINE_SWITCH

## 5. 说明

- 本报告由离线函数生成，未启动HTTP服务。
- 冻结接口路径和返回合同未修改。
- 原始CSV、旧bootstrap、正式模板和线上评分行为未修改。
## 6. 原始数据SHA-256复核

- 使用 runtime_data/derived/drink_water_v1/before_standard_csv_sha256.csv 复核。
- checked entries: 23
- changed_count: 0
- missing_count: 0
- 原始标准数据集未被本轮流程修改。
