# 评分预测实现说明

## 范围

本功能只预测 `drink_water_right`（右手拿杯饮水）与 `pick_book_right`（右手拿书）的下一次正式训练得分。预测在医生端后端计算，前端不保存、重算或硬编码分数。

只读接口：

```text
GET /api/predictions/next-score?task_id=drink_water_right
GET /api/predictions/next-score?task_id=pick_book_right
```

接口不调用评分服务，不修改 session、报告、P4 状态或评分结果。

## 样本筛选

读取既有目录：

```text
backend/data/patients/P-0217/sessions/<session_id>/session.json
backend/data/patients/P-0217/sessions/<session_id>/report.json
```

记录必须同时满足：

- session 与 report 的患者和 session 标识一致；
- `source_mode=device`；
- session 已标记 `report_ready=true`；
- `session_id` 不以 `SESSION_DEMO_` 开头；
- `report.score.valid=true`；
- `report.score.raw_source=real`；
- `score_version` 非空；
- `final_score` 是 0 至 100 的有限数值。

预测输出从不写入上述目录，也不会成为后续预测输入。

## 任务识别

先检查 session 的 `scenario_id` 与 `task` 是否为两个明确 task id。没有明确 id 时，复用 `RealRuntimeService._task_id` 已有规则：名称含“书”映射到 `pick_book_right`，名称含“水”或“杯”映射到 `drink_water_right`。其他名称不猜测，只能进入跨任务参考池。

## 确定性算法

记录按正式完成时间从新到旧排序。

- 同任务至少 3 条：`base=0.50*latest+0.30*previous+0.20*third`；`trend=clamp(0.35*(latest-previous)+0.15*(previous-third), -3, 3)`。
- 同任务 2 条：`base=0.65*latest+0.35*previous`；`trend=clamp(0.35*(latest-previous), -3, 3)`。
- 同任务 1 条：`base=latest`，`trend=0`。
- 同任务 0 条但存在其他合格记录：对全部任务最近三条使用同样的按数量公式，标记 `basis_type=cross_task`，不增加任务加减分。
- 没有合格记录：`available=false`，不返回默认预测分。

最终预测为 `clamp(base+trend, 0, 100)`。计算不使用随机数。

## 区间与置信度

- 1 条：半宽 8；
- 2 条：半宽 6；
- 至少 3 条：最近三条样本标准差乘 1.5，并限制在 3.5 至 8；
- 跨任务参考在对应半宽上增加 2，最大为 10。

区间上下界均限制在 0 至 100。同任务 1 条为低、2 条为中、至少 3 条为较高；跨任务参考始终为低。

## 未来五次趋势

第 `k` 次使用：

```text
clamp(base + trend * (1 + 0.65 + ... + 0.65^(k-1)), 0, 100)
```

`trend=0` 时五个点保持当前预测值，不制造上升曲线。

## 当前静态结论

当前医生端正式历史目录中没有满足全部筛选条件的记录，因此两个任务当前均返回 `available=false`。该结论来自文件静态审查，未启动后端或调用接口。
