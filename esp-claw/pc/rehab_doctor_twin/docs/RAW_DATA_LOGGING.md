# 原始数据日志

当前演示版在后端生成 WebSocket 数据时同步生成原始日志，不由浏览器回传数据形成日志。

## 日志目录

```text
backend/data/patients/P-0217/sessions/{session_id}/
├── session.json
├── imu_raw.jsonl
├── emg_raw.jsonl
├── events.jsonl
├── scores.jsonl
└── device_status.jsonl
```

## JSONL 格式

每行是一条独立 JSON。写入方式为追加写入，不会每条数据重写整个文件。

IMU 日志包含 `wrist_imu`、`forearm_imu`、`upper_arm_imu` 和 `trunk_imu`。sEMG 日志包含 CH1-CH4 的原始 ADC、微伏值和归一化活动度。当前均标注 `DEMO RAW FRAME`。

## 时间戳语义

- `timestamp_ms`：数据源时间戳，当前由演示引擎生成。
- `received_at_ms`：PC 后端收到或生成该帧并准备写入日志的时间。

## seq 语义

`seq` 继承当前 WebSocket 会话内的单调递增序号。前端仍按 `session_id` 和 `seq` 过滤乱序或迟到消息。

## DEMO 和 LIVE

- `demo`：后端演示引擎生成，用于竞赛演示。
- `live`：后续真实 ESP32-P4/IMU/sEMG 接入后使用。
- 当前日志不是临床原始数据，不代表真实患者结果。

## CSV 导出

接口：

```text
GET /api/patients/{patient_id}/sessions/{session_id}/logs/export?type=imu&format=csv
GET /api/patients/{patient_id}/sessions/{session_id}/logs/export?type=imu&format=jsonl
```

文件名包含 `patient_id`、`session_id` 和日志类型。

## 真实传感器接入位置

- 后端 adapter 可替换 `backend/app/demo_engine.py` 的数据来源。
- 原始日志写入入口为 `backend/app/raw_log_service.py`。
- 前端日志页面为 `frontend/src/pages/RawDataLogsPage.tsx`。

## 高频 sEMG 后续方案

当前演示只保存降频展示帧。后续真实 1000 Hz sEMG 应按时间分块保存，例如每 30 秒或每 1 MB 一个 JSONL/二进制块，并在索引文件中记录块范围，避免一次读取全部高频数据。


## 国赛演示收尾说明（2026-07-22）

本版本新增医生首页一键国赛演示入口，实时页采用 `body.glb` + 分段解剖网格运动映射。姿态由4个IMU相对四元数映射，sEMG固定为 CH1/CH2/CH3/CH4 四路业务肌肉归一化活动度。演示数据保持 `DEMO DATA` 标识，不代表真实临床结果。
