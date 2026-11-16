# Task Scoped Score Prediction

## Scope

The doctor prediction page shows two viewing tabs:

- 饮水动作训练
- 拿书动作训练

The tabs are result filters only. They are not training-task selectors and do not change patient task state. The pick-book tab remains visible for product consistency, but there is no current formal pick-book scoring support.

## Filtering Rules

Showcase records never enter prediction. They are marked:

- `record_kind=showcase`
- `formal=false`
- `prediction_eligible=false`
- `source_origin=system_showcase_fixture`

The prediction backend excludes records when:

- `formal` is explicitly `false`
- `prediction_eligible` is explicitly `false`
- `record_kind` is `showcase`

Missing `formal` or `prediction_eligible` fields do not automatically exclude older real pc_local sessions. Realness is still determined by the established checks:

- session id is not `SESSION_DEMO_*`
- `source_mode == device`
- `source_origin == pc_udp_6006_ingest`
- `status == COMPLETED`
- `report_ready == true`
- `score.valid == true`
- `score_version` exists
- finite final score
- full `score_result.json` exists
- `report.json` exists
- 55.84 unconfirmed score is excluded
- records are filtered by the requested `task_id`

Only tasks with `formal_score_supported=true` can contribute samples. Therefore current pick-book showcase data cannot contribute to prediction.

## Empty State

If a task has no eligible formal records, the page displays:

`暂无可用预测`

`当前没有满足来源与有效性要求的正式评分记录。`

No cross-task fallback is used.
