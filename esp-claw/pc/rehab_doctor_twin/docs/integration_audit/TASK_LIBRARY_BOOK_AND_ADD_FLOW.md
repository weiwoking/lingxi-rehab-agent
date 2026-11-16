# Task Library Book And Add Flow

## Scope

This pass keeps the real training-start behavior untouched. The task library page is a competition-facing display and management surface, not a task-decision surface.

## Task Model

| task_id | display_name | ui_enabled | formal_score_supported | showcase_only | internal_score_task_name | score_mode | score_profile |
| --- | --- | --- | --- | --- | --- | --- | --- |
| drink_water_right | 饮水动作训练 | true | true | false | 喝水动作模拟 | drink_full_cycle | temporary_standard_20260724 |
| pick_book_right | 拿书动作训练 | true | false | true | N/A | N/A | N/A |

饮水是当前已确认可接入真实 IMU+sEMG、本地评分服务和饮水标准轨迹的正式评分任务。拿书只是前端 showcase 任务；当前不实现拿书真实评分，不写入真实 task.json，不进入正式 CSV、正式 session、正式评分结果或正式预测样本。

## UI Semantics

Both tasks may be displayed as `已启用` and `可用于康复训练`. UI enabled does not mean formal score support. The pick-book card uses the low-key label `系统展示任务`.

The page does not expose current-task selection, task switching, Agent choice, or decision results. No real Agent task decision is implemented or invoked.

## Add Flow

The doctor task page includes a visible `+ 添加任务` button in the page header. It opens a drawer listing supported display tasks that are not currently in the patient task library. If every task already exists, the entry remains visible and the drawer shows that all display tasks have been added.

## Removal Semantics

Removing a task only removes it from the patient task library view/data. It does not remove historical sessions, reports, score assets, traces, or prediction history.

## Patient Visibility

The patient training page continues to hide task names, task ids, internal score task names, task selection, and showcase task labels.
