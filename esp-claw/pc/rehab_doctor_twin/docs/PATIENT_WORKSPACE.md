# 患者工作区

本轮加入多患者架构入口，但竞赛演示版只配置一名患者：`P-0217`。

## 路由

- `/`：医生工作台首页。
- `/patients/:patientId/live`：实时数字孪生监护。
- `/patients/:patientId/devices`：设备状态。
- `/patients/:patientId/logs`：原始数据日志。
- `/patients/:patientId/reports`：历史会话与专业报告。
- `/report/current`：兼容当前报告预览入口。

## 患者数据

后端配置文件：

```text
backend/data/patients.json
```

前端通过 `GET /api/patients` 和 `GET /api/patients/{patient_id}` 读取患者信息，不在多个组件中重复硬编码患者档案。

## 工作区布局

公共布局文件：

```text
frontend/src/layouts/PatientWorkspaceLayout.tsx
```

顶部导航显示返回患者首页、当前患者、会话状态、实时监护、设备状态、数据日志、历史报告、`DEMO DATA`、WebSocket 状态和最后数据时间。


## 国赛演示收尾说明（2026-07-22）

本版本新增医生首页一键国赛演示入口，实时页采用 `body.glb` + 分段解剖网格运动映射。姿态由4个IMU相对四元数映射，sEMG固定为 CH1/CH2/CH3/CH4 四路业务肌肉归一化活动度。演示数据保持 `DEMO DATA` 标识，不代表真实临床结果。
