# Function Inventory

审计时间：2026-07-23

本文为只读功能盘点，不代表新增承诺。本轮未修改视觉、评分、康复控制、医生端 UI、API、WebSocket 或阿里云逻辑。

## 1. ESP-Claw 固件端

| 模块 | 现有职责 | 入口/接口 | 当前状态 |
| --- | --- | --- | --- |
| `rehab_integration` | Rehab 控制面、Capability 注册、mock/real 模式标志、疲劳门禁、训练/评分查询统一入口 | `rehab_system_init`; capability group `rehab_integration` | 源码完整；构建未验证 |
| `rehab_sensor_recording` | 从 S3/UDP/IMU/sEMG 状态写训练 CSV，创建 `YYYYMMDD_TASKNNN` 会话目录，结束时写七字段 `task.json` | `rehab_training_start`, `rehab_training_stop`, `rehab_training_status` | 源码完整；依赖 SD、时间、传感器在线 |
| `rehab_score_bridge` | 训练 finalized 后上传 PC 评分服务，轮询作业，下载 compact result 并落盘，查询历史评分 | `GET /api/v1/score/health`; `/api/v1/score/jobs*`; `rehab_score_query` | PC 评分服务已恢复；ESP 侧未上板验证 |
| `rehab_person_follow` | 相机 JPEG -> PC 追人检测 -> 目标选择 -> 有界离散车控动作；Agent 只 start/stop/status | `/api/v1/person-follow/detect`; `person_follow_start/stop/status` | PC 视觉服务已恢复；P4 摄像头/热点未验证 |
| `rehab_scan_transfer` | 上传已提交的 P4 scan 目录到独立 PC scan service，下载 `deltar_scan_result.v1.1` | `rehab_scan_send latest|<id>|status|abort`; default `:8030/api/v1/scan/jobs` | 源码完整；PC scan pipeline 未恢复 |
| `app_config` | 设备配置持久化，包含 LLM/DashScope、视觉融合 URL、评分 base URL | `perception_fusion_url`, `rehab_score_base_url` | 配置能力存在；未改配置 |

## 2. PC 视觉服务

| 功能 | 路径 | 输入 | 输出 | 状态 |
| --- | --- | --- | --- | --- |
| 视觉健康检查 | `GET /api/v1/vision/health` | 无 | 模型状态、几何/图像/ToF 配置 | 已本机验证 `status=ok`, `model_ready=True` |
| RGB565 + ToF 融合 | `POST /api/v1/vision/fuse` | `image` RGB565 文件 + `tof` JSON | 目标检测、身份分类、ToF/几何融合结果 | 服务可启动；未做真实 P4 输入验证 |
| 追人检测 | `POST /api/v1/person-follow/detect` | multipart JPEG 640x360 + metadata `person_follow_request.v1` | `person_follow.v1` 检测框、中心点、模型和计时 | OpenAPI 已验证存在；未接真实相机流 |
| 追人查看页 | `/person-follow-view` 和 latest jpg/json | 最近一次检测帧 | 原图、标注图、metadata | 路由存在；等待真实帧 |

## 3. PC 评分服务

| 功能 | 路径 | 输入 | 输出 | 状态 |
| --- | --- | --- | --- | --- |
| 健康检查 | `GET /api/v1/score/health` | 无 | service/version/assets/runtime_data/capabilities | 已验证 ready |
| 持久评分作业创建 | `POST /api/v1/score/jobs` | `task.json` + `X-Session-Id`/CSV size/SHA256 | job response | 冻结接口；loopback/RFC1918 限制 |
| CSV 上传 | `PUT /api/v1/score/jobs/{job_id}/csv` | frozen 86 列 CSV | accepted/scoring 状态 | 冻结接口；输入强校验 |
| 状态查询 | `GET /api/v1/score/jobs/{job_id}` | job id | job status | 可用 |
| 结果下载 | `GET /api/v1/score/jobs/{job_id}/result` | job id | `rehab_score_result_v1` compact result | 可用；需完成正式评分 |
| 旧接口 | `/score_trial`, `/process_session*`, `/process_task_directory*` | 单次特征/记录/目录 | full/public score JSON 或 NDJSON | 保留；POST 仅 loopback |

## 4. 治疗师 PC UI

| 功能 | 路径 | 数据源 | 状态 |
| --- | --- | --- | --- |
| FastAPI gateway | `C:\ev\rehab_doctor_twin\backend\app\main.py` | 当前 demo engine/store/raw logs | 已启动验证 `/health` |
| WebSocket 实时推送 | `/ws/live` | demo engine；未来 PC Adapter | 协议固定，消息类型未删改 |
| React 医生端 | `C:\ev\rehab_doctor_twin\frontend` | Vite proxy -> backend 8000 | typecheck/build/dev HTTP 200 |
| 数字孪生 | `frontend/src/twin` | `applyLiveSensorFrame(frame, emgFrame)` | Demo 可用；真实传感器 Adapter 未接 |
| 原始日志/报告 | backend data + report service | demo JSONL/report | Demo 可用；真实 P4 数据未接 |

## 5. 未接入或未恢复

| 项 | 现状 |
| --- | --- |
| ESP-IDF 固件构建 | 当前 shell 无 `IDF_PATH`、`idf.py`、`cmake`、`ninja` |
| P4 真实设备闭环 | Windows 热点未开启，P4 自动发现/真实 camera/tof/status 未验证 |
| P4 scan PC service | 依赖 `C:\ec\_deltar_p4_pipeline` 与独立 deltar 环境；本轮未恢复 |
| 治疗师 UI 真实 Adapter | README 明确“后续”；目前仍是 demo source |
| 阿里云康复闭环 | 未发现独立康复云同步服务；医生端明确未接阿里云 |
