# 医生端业务工作台前端接入契约

本文件描述当前医生端前端已经预留的数据源接口。当前页面默认使用 `DEMO DATA`，不声明真实 ESP-Claw、真实评分或真实临床历史已经接入。

## 前端 DataSource

`frontend/src/services/ClinicalWorkspaceDataSource.ts` 定义统一接口：

- `getPrescription(patientId)`
- `savePrescription(patientId, prescription)`
- `getTaskDecision(patientId, sessionId)`
- `getAgentTrace(patientId, sessionId)`
- `getSafetyInterventions(patientId, sessionId)`
- `getTrainingTrend(patientId, limit?)`
- `getTherapistReview(patientId, sessionId)`
- `saveTherapistReview(review)`

`VITE_CLINICAL_DATA_MODE=demo` 时使用演示数据；未来设置为 `live` 后，可在 `HttpClinicalWorkspaceDataSource` 中对接真实后端。

## 推荐后端端点

- `GET /api/patients/{patient_id}/prescription`
- `PUT /api/patients/{patient_id}/prescription`
- `GET /api/sessions/{session_id}/task-decision`
- `GET /api/sessions/{session_id}/agent-trace`
- `GET /api/sessions/{session_id}/safety-events`
- `GET /api/patients/{patient_id}/trends?limit=7`
- `GET /api/sessions/{session_id}/therapist-review`
- `POST /api/sessions/{session_id}/therapist-review`

## 未来真实数据来源

- 医生处方服务：训练目标、允许难度、最大取物高度、禁忌任务、监测重点、版本与更新时间。
- ESP-Claw 事件适配器：任务选择、候选比较、训练状态、安全纠正与报告生成事件。
- 视觉感知：目标物类别、高度、距离和可达性。
- 真实 4 IMU + 4 sEMG：右侧上肢姿态、肌电通道和数据质量。
- 评分服务：正式 score result、反馈、代偿、疲劳和报告生成依据。
- 会话历史：最近 7 次有效训练趋势、治疗师审核状态与备注。

## 关键边界

- 当前患者固定为 `P-0217`，患侧为右侧上肢。
- 当前任务展示为 `右手拿杯喝水`。
- 前端不会改变既有 WebSocket 消息类型。
- 前端不会改变评分结果结构，`ROM_score` 字段大小写必须保持。
- 智能体建议仅供治疗师参考，不自动修改医生处方。
- 患者命令不能修改医生处方边界；该语义目前仅在前端展示，真实权限控制需要后端实现。
