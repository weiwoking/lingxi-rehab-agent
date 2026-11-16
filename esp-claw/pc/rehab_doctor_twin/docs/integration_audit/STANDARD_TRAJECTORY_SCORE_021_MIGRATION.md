# 标准轨迹评分 0.2.1 迁移

权威来源根目录：

`C:\Users\ZDragon\Downloads\评分\pc\rehab_score_service`

正式目标：

`C:\ec\esp-claw\pc\rehab_score_service`

两端 `SERVICE_VERSION` 均为 `0.2.1`。本轮定向迁移 profile 解析、完整喝水周期处理、配置、标准轨迹说明和对应测试源码，没有覆盖整个评分目录，也没有迁入 `runtime_data`、缓存、PNG 或历史患者会话。

标准轨迹：

`data/segmented_trials/temporary_standard_20260724/trial_001.csv`

- 86 列；
- 384 条数据行；
- SHA-256：`5AA77CD552C899DB3B309E25B2F464A8DCD55381EF5C585AC4A899B136CE44EA`；
- 目标与来源逐字节哈希一致；
- 未重新生成、未插值、未平滑、未补包、未修改原始传感器值。

正式喝水任务评分名为 `喝水动作模拟`。启动时使用：

```text
REHAB_SCORE_TASK12_TEMPLATE_PROFILE=temporary_standard_20260724
REHAB_SCORE_DRINK_FULL_CYCLE=1
```

旧 `fused_20260526_205718` 模板保留。三个冻结 legacy 接口和 Job API 路径未因医生端集成而修改。本轮没有运行评分测试或服务。
