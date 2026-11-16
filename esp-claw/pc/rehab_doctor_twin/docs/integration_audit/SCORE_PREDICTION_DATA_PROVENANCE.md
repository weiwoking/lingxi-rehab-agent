# 评分预测数据来源

## 正式输入边界

预测只读取医生端既有 device 历史索引。`DeviceHistoryStore` 已限制目录、患者标识、session 标识和 `source_mode`；预测层继续核验报告有效性、正式评分来源和数值范围。

保留的来源字段包括：

- `session_id`
- `task_id` 或 `task_name`
- `final_score`
- `completed_at` 或正式完成时间
- `source_mode`
- `valid`

排除项包括 `SESSION_DEMO_*`、非 device 来源、无匹配报告、无效评分、非有限或越界分数、无 `raw_source=real` 的结果，以及预测输出。

## 当前文件审计

医生端 `backend/data/patients/P-0217/sessions` 当前只有 `SESSION_DEMO_*` 目录，没有可加入正式预测历史的 device session/report 文件对。因此当前合格正式评分数量为 **0**。

评分服务目录中发现：

```text
C:\ec\esp-claw\pc\rehab_score_service\runtime_data\sessions\20260724_TASK001\
```

其中：

- `result/score_result.json`：`valid=true`，`summary.average_score=55.84`，`session_id=20260724_TASK001`；
- `job_status.json`：job 状态为 `succeeded`；
- `task.json`：记录 654 行范围对应的任务时间；
- score result 的 `patient_id=S001`；
- 这些评分服务文件没有 `source_mode=device`；
- 它们不是医生端 `P-0217` 正式历史中的 session/report 文件对。

因此 55.84 没有被代码硬编码，也没有在缺少来源证明时被纳入预测。其任务名称含“水”，按项目已有名称映射可识别为 `drink_water_right`；但记录在通过正式来源筛选前不会进入同任务或跨任务样本池。

## 当前预测状态

| 任务 | 同任务合格样本 | 全部合格样本 | 当前结果 |
| --- | ---: | ---: | --- |
| `drink_water_right` | 0 | 0 | `available=false` |
| `pick_book_right` | 0 | 0 | `available=false` |

后续由现有 RealRuntimeService 将真实评分报告按 session 持久化后，符合条件的记录会由同一只读逻辑自动读取。
