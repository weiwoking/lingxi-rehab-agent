# Current System Facts

本文件为只读源码审计事实报告。除创建本报告外，本轮未修改代码、配置、接口、环境或服务器；未构建、未烧录、未登录或访问阿里云。

## 1. 审计范围

| 工程 | 审计目录 | 依据类型 |
|---|---|---|
| ESP-Claw 固件 | `C:\ec\esp-claw\application\edge_agent`，重点 `main` 与 `components\rehab_*` | 源码事实 |
| PC 视觉服务 | `C:\ec\esp-claw\pc\vision_interpreter` | 源码事实、启动脚本 |
| PC 评分服务 | `C:\ec\esp-claw\pc\rehab_score_service` | 源码事实、启动脚本 |
| 医生端 PC UI | `C:\ev\rehab_doctor_twin` | 源码事实、本地 demo 数据 |
| 既有审计文档 | `C:\ev\rehab_doctor_twin\docs\integration_audit` | 文档陈述 |

## 2. 各工程启动入口

| 工程 | 启动入口 | 主程序 | 配置入口 | 监听地址/端口 | 依赖服务 | 数据目录 | 日志目录 | 启动脚本 | 可静态确认可启动模块 | 尚未运行验证 |
|---|---|---|---|---|---|---|---|---|---|---|
| ESP-Claw 固件 | `C:\ec\esp-claw\application\edge_agent\main\main.c:479` `app_main()` | `main.c` 初始化 NVS、Board Manager、camera、ToF、capture、cloud fusion、HTTP server、Wi-Fi、rehab system、scan capture/transfer、UI | `C:\ec\esp-claw\application\edge_agent\components\app_config\app_config.c:64-65,103`；`sdkconfig` 由用户声明保持 v5.5.4 基线 | ESP 侧 HTTP server 在 `main.c:513-542` 启动；具体监听端口未在本轮重点文件中静态定位 | Board Manager、Wi-Fi、Camera、ToF、rehab sensor UDP、PC 视觉、PC 评分、可选 scan PC service | `C:\ec\esp-claw\application\edge_agent\main\app_fs.c:32-42,210-245`：SYSTEM `/system`，DATA 为 SD mount 或 `/fatfs` | 固件运行日志为 ESP log/串口；本轮未运行 | `rehab_sensor_recording`、`rehab_score_bridge`、`rehab_person_follow`、`rehab_cloud_fusion`、`rehab_scan_capture`、`rehab_scan_transfer`、`rehab_fatigue` 均由 `main.c` 或 `rehab_system.c` 接入 | 未运行 `idf.py`、未 flash、未 monitor，真实硬件可用性待确认 |
| PC 视觉服务 | `C:\ec\esp-claw\pc\vision_interpreter\run_windows.ps1:13`；`C:\ec\esp-claw\tools\vision_launcher\Start-VisionPC.ps1:1084-1086` | `C:\ec\esp-claw\pc\vision_interpreter\app\main.py:111,144,172` FastAPI app | `C:\ec\esp-claw\pc\vision_interpreter\app\config.py:76-80`；`config\vision_config.json` | `run_windows.ps1:13` 为 `0.0.0.0:8000`；Linux service 为 `127.0.0.1:8010`：`deploy\vision-interpreter.service:13` | YOLO/Ultralytics、identity model、OpenCV、Torch | 模型与配置目录在项目内；最新追人预览为内存态 viewer | `%LOCALAPPDATA%\ESP-Claw\vision_launcher`，`Start-VisionPC.ps1:1013-1022` | `run_windows.ps1`、`tools\vision_launcher\Start-VisionPC.ps1`、`Bootstrap-VisionPC.ps1` | `/api/v1/vision/health`、`/api/v1/vision/fuse`、`/api/v1/person-follow/detect`、viewer endpoints | 本轮未启动服务；模型加载、GPU/CPU 运行态待确认 |
| PC 评分服务 | `C:\ec\esp-claw\tools\score_launcher\Start-ScorePC.ps1:130-140`；也可直接运行 `score_server.py:561-564` | `C:\ec\esp-claw\pc\rehab_score_service\cloud_interface\score_server.py:76` FastAPI app | 环境变量 `REHAB_SCORE_LISTEN_ADDRESS`、`REHAB_SCORE_PORT`、`REHAB_SCORE_RUNTIME_DATA_ROOT`：`score_server.py:30-35`；launcher local config `%LOCALAPPDATA%\ESP-Claw\score_launcher\score_launcher.local.json` | 默认 `0.0.0.0:8020`：`score_server.py:30-35`，launcher 同步设置 env：`Start-ScorePC.ps1:137-139` | FastAPI、Pandas/Numpy、评分模板、DTW 模板 | `pc\rehab_score_service\runtime_data` 默认：`score_server.py:29,35`；job store 在 runtime root 下创建 sessions/staging | `%LOCALAPPDATA%\ESP-Claw\score_launcher`，`ScoreLauncher.Common.ps1:96-100` | `Start-ScorePC.ps1`、`Check-ScorePC.ps1`、`Smoke-ScorePC.ps1`、`Stop-ScorePC.ps1` | health、legacy scoring endpoints、job API、task-directory scoring | 本轮未启动服务；真实评分调用待运行验证 |
| 医生端后端 | `C:\ev\rehab_doctor_twin\backend\run.py:4`；`scripts\start_backend.ps1` | `C:\ev\rehab_doctor_twin\backend\app\main.py:18,44,66,163` FastAPI app | `C:\ev\rehab_doctor_twin\backend\app\config.py:7-14`，`SOURCE_MODE="demo"` | `127.0.0.1:8000`：`backend\run.py:4` | demo engine、patient JSON、raw log service、WebSocket hub | `C:\ev\rehab_doctor_twin\backend\data`，patients/session/logs/report | 后端脚本未发现专用日志目录；raw logs 写入 session 目录 | `scripts\start_backend.ps1`、`scripts\start_all.ps1` | HTTP API、WebSocket `/ws/live`、demo engine、raw log writer、report builder | 本轮未启动后端 |
| 医生端前端 | `C:\ev\rehab_doctor_twin\frontend\package.json:7` | `C:\ev\rehab_doctor_twin\frontend\src\App.tsx:14-22` | Vite env `VITE_CLINICAL_DATA_MODE`：`frontend\src\services\clinicalWorkspaceDataSource.ts:5-9`；Vite proxy in `frontend\vite.config.ts` | `127.0.0.1:5173`：`frontend\package.json:7`; proxy `/api`,`/health`,`/ws` 到后端 `127.0.0.1:8000` | 医生端后端 | localStorage for clinical workspace demo；HTTP API for patients/sessions/logs/reports | 浏览器端无文件日志；后端 raw logs | `scripts\start_frontend.ps1`、`scripts\start_all.ps1` | React routes、WebSocket client、HTTP client、demo/localStorage data source | 本轮未启动前端 |

端口核对：`8000` 同时出现在 PC 视觉 Windows 启动和医生端后端；`8010` 出现在视觉 Linux systemd/nginx 部署；`8020` 出现在 PC 评分与 ESP score bridge auto port；`8030` 出现在 scan PC service 文档/ESP scan transfer 默认语义；`5173` 出现在医生端 Vite；`8083` 本轮在审计范围源码中未发现实际监听证据。

## 3. 各工程真实功能

状态只描述当前源码事实；“完整实现和调用证据”不等同本轮运行验证。

| 功能项 | 状态 | 文件/行号/符号 | 调用证据 | 数据来源 |
|---|---|---|---|---|
| 患者档案 | 有完整实现和调用证据 | `C:\ev\rehab_doctor_twin\backend\app\patient_service.py:17,24`；`backend\app\main.py:103-108`；`frontend\src\services\patientApi.ts:37-44` | 前端 `DoctorDashboardPage.tsx:43` 调 `fetchPatients()`；后端读 `patients.json` | `backend\data\patients.json` |
| `patient_id` | 有完整实现和调用证据 | 医生端 `backend\app\raw_log_service.py:42-64`；评分 `cloud_score_api.py:606-609`；ESP score/task metadata in `rehab_score_bridge_storage.c:393-459` | 医生端 demo 写 session/log/report；评分结果保留 patient/session/trial/task 字段 | demo patient、task.json、score request |
| `device_id` | 有完整实现和调用证据 | 医生端 `backend\app\demo_engine.py:424-430`；前端 `ReportPreview.tsx:79-86` | demo engine 广播 `device.status` 并写 `device_status.jsonl` | `sensor_topology.json`、demo frame |
| `baseline` | 有实现但没有调用证据 | 评分服务 `score_server.py:166-170,426-445`；score API BRS/baseline-like support in `cloud_score_api.py:397-425` | 发现 HTTP endpoint `/set_baseline_from_rows`，未发现 ESP/医生端前端调用 | 请求 JSON rows |
| 处方 | 使用 mock/demo | 前端 data source `DemoClinicalWorkspaceDataSource.ts:25-50`；live source `HttpClinicalWorkspaceDataSource.ts:10-42` 全部抛 `LIVE_CLINICAL_WORKSPACE_API_NOT_IMPLEMENTED` | `DoctorDashboardPage.tsx:43-57` 读写 clinicalWorkspaceDataSource；默认 mode 为 demo | localStorage |
| training session | 有完整实现和调用证据 | ESP recorder `rehab_training_recorder.c:855-892,981,1248-1257`；医生端 `demo_engine.py:120-124,234-240` | ESP finalized callback 接 score bridge；医生端 demo start 写 session | ESP CSV/task.json；医生端 demo scenario |
| trial | 有完整实现和调用证据 | 评分 `score_server.py:87-150,407-409`；`cloud_score_api.py:71-218,533-545`；`session_processor.py:922-978` | `/score_trial` 调 `score_trial_json`；session processor 从 segment 建 trial 并评分 | HTTP trial JSON 或 task CSV segment |
| 摄像头 | 有完整实现和调用证据 | ESP `main.c:364-391` `start_camera_provider()`；person follow snapshot `rehab_person_follow_perception.c:55-130`；vision `/fuse` image decode `main.py:268-276` | ESP 启动 camera provider；person follow 获取 JPEG 后 POST PC | Board Manager camera frame/JPEG |
| ToF | 有完整实现和调用证据 | ESP `main.c:399-408`；vision `/fuse` ToF parse `main.py:180-189` | ESP 启动 ToF provider；vision fuse 校验 ToF JSON | ToF provider snapshot / multipart JSON |
| IMU | 有完整实现和调用证据 | ESP CSV header `rehab_training_recorder.c:37-44`；UDP status `rehab_sensor_rx.c:384-430,784-810,891-928`；doctor raw logs `raw_log_service.py:110-143` | ESP UDP receiver 更新 4 IMU；doctor demo 写 IMU JSONL | UDP sensor packet；demo telemetry |
| sEMG | 有完整实现和调用证据 | ESP CSV header `rehab_training_recorder.c:37-44`；UDP update `rehab_sensor_rx.c:384-391`；doctor `raw_log_service.py:144-153` | ESP CSV 包含 emg_*；doctor demo 写 emg JSONL | UDP sensor packet；demo EMG |
| scan | 有实现但被调用方无法确认 | ESP scan capture `rehab_scan_capture.c:64-102,198-221,334-450`；scan transfer `rehab_scan_transfer_http.c:115-172`；`main.c:596-628` | ESP main 初始化 scan capture/transfer；未在本轮审计范围发现 PC scan service 源码 | DATA `rehab/scans`、`rehab/scan_results` |
| 人体检测 | 有完整实现和调用证据 | vision `main.py:276-287`；`person_follow_service.py:48-90` | `/api/v1/vision/fuse` 调 YOLO predict；`/api/v1/person-follow/detect` 调 detector | 上传图像 |
| 身份分类 | 有完整实现和调用证据 | `identity_classifier.py:37-55,98-167`；`main.py:34,94-100,279-283` | vision lifespan load identity；fuse 调 `identity_model.classify()` | YOLO person crops |
| 目标跟随 | 有完整实现和调用证据 | ESP `main.c:165-206`；`rehab_person_follow_perception.c:129-219`；PC `person_follow_router.py:42-81` | ESP 构造 `/api/v1/person-follow/detect` endpoint 并注册 capabilities；PC route 调 service.detect | Camera JPEG + metadata |
| 视觉融合 | 有完整实现和调用证据 | ESP `rehab_cloud_fusion_client.c:42-51,271-451`；PC `main.py:172-313` | ESP `rehab_cloud_fusion_upload_once()` POST multipart；PC `/api/v1/vision/fuse` 解包/推理/返回 | camera image + ToF JSON |
| 评分 | 有完整实现和调用证据 | ESP `rehab_score_bridge_core.c:420-544`；HTTP `rehab_score_bridge_http.c:210-403`；PC `score_job_api.py:48-104`；PC worker/store/session processor | recorder finalized callback -> score bridge -> PC job API -> result | ESP task.json + CSV |
| fatigue | 有完整实现和调用证据 | ESP `rehab_fatigue.c:23,349-377,406,451,477,498,520`；`rehab_system.c:112-125` | score success observer 接入 fatigue；before_training gate/start/stop/status 存在 | `rehab/fatigue_state.json` + score result |
| compensation | 有完整实现和调用证据 | PC `feature_extractors\compensation_detector.py:24-123`；`session_processor.py:941-960`；doctor `ReportPreview.tsx:104,116` | session processor 从 raw score 生成 compensation events；doctor report 展示 | score features / demo report |
| BRS | 有实现但没有调用证据；医生端有 UI 展示 | PC `cloud_score_api.py:126-166`；doctor `ReportPreview.tsx:79-86` | BRS 只在 trial_json 标记 assessment 时输出；未发现 ESP/医生端真实调用 | HTTP trial JSON 或 demo report |
| WebSocket | 有完整实现和调用证据 | doctor backend `main.py:163-164`；hub `websocket_hub.py:10-53`；frontend `websocketClient.ts:24-40` | 前端启动 WebSocket `/ws/live`；后端 accept/pump/broadcast | demo engine snapshot/messages |
| 历史记录 | 有完整实现和调用证据 | doctor `log_export_service.py:31-49`；frontend `SessionHistoryPage.tsx:15-45` | 前端 fetch sessions；后端读 `session.json` | `backend\data\patients\<id>\sessions\<session>\session.json` |
| 报告 | 有完整实现和调用证据 | doctor `report_service.py:51-146`；`demo_engine.py:234-240`；`ReportPage.tsx:23-40` | demo complete 后 build_report/write report.json/broadcast report_ready；前端 HTTP 读取 | demo snapshot/scenario |
| 本地存储 | 有完整实现和调用证据 | ESP `app_fs.c:210-245`；ESP score/status/result `rehab_score_bridge_storage.c:498-557`；doctor raw logs `raw_log_service.py:42-102` | ESP DATA root、doctor session logs、score runtime storage 均有读写代码 | FATFS/SD、runtime_data、本地 JSONL |
| 云端通信 | 仅配置存在；康复业务公网调用未发现 | ESP DashScope defaults `app_config.c:123,134`；HTTP UI Aliyun links `externalLinks.ts:12-13`；vision title/deploy naming | 发现通用 LLM base URL/配置 UI；未发现患者/评分/医生端上传阿里云 | 配置项 |
| 离线缓存 | 未发现明确实现 | doctor localStorage 是 demo workspace persistence；ESP 本地存储不是专名 offline cache | 未发现离线队列/重放/同步状态机 | 无法确认 |
| 心跳 | 有完整实现和调用证据 | vision `main.py:144-172`；score `score_server.py:292-294`；doctor `main.py:44-52`；frontend WS status `websocketClient.ts:24-40` | 三个 PC 服务均有 health；前端维护 WS 状态与重连 | service runtime snapshot |

统计：上表 27 项中，标记为“有完整实现和调用证据”的功能项为 21 项；标记为 mock/demo 或包含 mock/demo 数据源的功能项在第 11 节单列。

## 4. 医生端页面及数据来源

| 页面 | 组件文件 | 当前展示数据 | 数据来源 | 常量/mock/本地/HTTP/WS/demo/真实服务/保存 |
|---|---|---|---|---|
| Dashboard | `frontend\src\pages\DoctorDashboardPage.tsx:43-57` | 患者卡、处方边界、WS 状态 | `/api/patients` + `clinicalWorkspaceDataSource` | 患者走 HTTP；处方默认 localStorage demo；save 只写 data source，live source 未实现 |
| Live Monitor | `frontend\src\pages\LiveMonitorPage.tsx:71-154,188-194` | 实时状态、遥测、EMG、摄像头 preset、评分/报告入口 | `useRehabStore` + `/api/demo/start`/reset + `/ws/live` | 摄像头 preset 与 EMG 映射为前端常量；实时数据来自 demo WebSocket；未调用视觉/评分服务 |
| Decision Review | `frontend\src\pages\DecisionReviewPage.tsx:25-49` | 处方、任务决策、Agent trace、治疗师审查 | `clinicalWorkspaceDataSource` | 默认 demo/localStorage；live source 未实现；保存写 localStorage |
| Device Status | `frontend\src\pages\DeviceStatusPage.tsx:7,69-74` | 设备在线状态 | `useRehabStore` 或 fallback devices 常量 | 有固定 fallback；demo engine 生成 device.status；未读真实设备 |
| Raw Data Logs | `frontend\src\pages\RawDataLogsPage.tsx:19-57` | session 列表、日志表、导出 | `/api/patients/{id}/sessions`、`/logs`、`/logs/export` | HTTP 读本地 JSONL；2 秒刷新；保存由后端 raw_log_service 完成 |
| Session History | `frontend\src\pages\SessionHistoryPage.tsx:15-45` | 历史 session 卡、报告入口 | `/api/patients/{id}/sessions` + demo training trend | HTTP 读本地 session.json；疲劳/代偿字段部分由 final_score 派生常量逻辑 |
| Report | `frontend\src\pages\ReportPage.tsx:23-40`；`components\ReportPreview.tsx:75-165` | 当前或历史报告、临床上下文、打印 | `/api/report/current` 或 `/api/patients/{id}/sessions/{session}/report` + clinicalWorkspaceDataSource | 报告走 HTTP 读本地 `report.json`；临床上下文默认 demo/localStorage；有浏览器打印；无真实评分服务调用 |

医生端后端 demo engine 会生成并保存 demo session：`backend\app\demo_engine.py:120-124,234-240`；raw log 写入 JSONL：`backend\app\raw_log_service.py:15-19,42-102`。未发现医生端直接调用 PC 视觉 `:8000` 或评分 `:8020` 的源码。

## 5. 实际接口清单

| ID | 类型 | 定义方 | 调用方 | 被调用方 | 地址/端口 | 方法/事件 | 请求字段 | 返回字段 | 超时/重试 | 证据状态 |
|---|---|---|---|---|---|---|---|---|---|---|
| I-01 | HTTP | PC vision `main.py:144-172` | launcher/ESP/测试 | PC vision | `:8000` 或 deploy `:8010` | `GET /api/v1/vision/health` | 无 | service/model/identity/person_follow readiness | launcher health timeout in `Start-VisionPC.ps1` | 实际 endpoint；本轮未运行 |
| I-02 | HTTP multipart | PC vision `main.py:172-313` | ESP `rehab_cloud_fusion_client.c:271-451` | PC vision | default `http://192.168.137.1:8000/api/v1/vision/fuse` | `POST /api/v1/vision/fuse` | image, tof, metadata | detections/fusion/status | ESP timeout arg；HTTP status check | 完整调用证据 |
| I-03 | HTTP multipart | PC vision `person_follow_router.py:42-81` | ESP `rehab_person_follow_perception.c:129-219` | PC vision | derived `:8000/api/v1/person-follow/detect` | `POST /api/v1/person-follow/detect` | JPEG image, metadata JSON | target_found, detections, timing | ESP `http_timeout_ms`；WS client retry unrelated | 完整调用证据 |
| I-04 | HTTP | PC vision viewer `person_follow_viewer.py:226-240` | browser | PC vision | `:8000` | `/person-follow-view`, latest original/annotated/json | 无 | HTML/JPEG/JSON | browser polling | viewer endpoint |
| I-05 | HTTP | PC score `score_server.py:292-294` | ESP `rehab_score_bridge_http.c:210-217`/launcher | PC score | `:8020` | `GET /api/v1/score/health` | 无 | service/status/assets/runtime | ESP timeout 10000ms in `rehab_score_bridge_http.c:158` | 完整调用证据 |
| I-06 | HTTP JSON | PC score `score_job_api.py:48-65` | ESP `rehab_score_bridge_http.c:260-296` | PC score | `:8020` | `POST /api/v1/score/jobs` | task JSON; headers `X-Session-Id`, `X-CSV-Size`, `X-CSV-SHA256` | job_id, state/status | ESP timeout 10000ms | 完整调用证据 |
| I-07 | HTTP CSV | PC score `score_job_api.py:71-82` | ESP `rehab_score_bridge_http.c:324-330` | PC score | `:8020` | `PUT /api/v1/score/jobs/{job_id}/csv` | text/csv body | state/status | ESP timeout 10000ms | 完整调用证据 |
| I-08 | HTTP | PC score `score_job_api.py:88-92` | ESP `rehab_score_bridge_http.c:360-366` | PC score | `:8020` | `GET /api/v1/score/jobs/{job_id}` | path job_id | job status | ESP timeout 10000ms | 完整调用证据 |
| I-09 | HTTP | PC score `score_job_api.py:95-99` | ESP `rehab_score_bridge_http.c:390-403` | PC score | `:8020` | `GET /api/v1/score/jobs/{job_id}/result` | path job_id | `rehab_score_result_v1` compact result | ESP timeout 10000ms | 完整调用证据 |
| I-10 | HTTP | PC score `score_job_api.py:102-104` | 未发现正式调用 | PC score | `:8020` | `POST /api/v1/score/jobs/{job_id}/retry` | path job_id | job status | 未定位 | endpoint 存在 |
| I-11 | HTTP | PC score `score_server.py:276-297` | browser/Smoke | PC score | `:8020` | `/`, `/demo` | 无 | service/demo | launcher smoke | endpoint 存在 |
| I-12 | HTTP JSON | PC score `score_server.py:407-409` | legacy loopback/test | PC score | `:8020` | `POST /score_trial` | trial JSON | public score result | legacy loopback guard | endpoint 存在 |
| I-13 | HTTP JSON | PC score `score_server.py:412-445` | 未发现正式调用 | PC score | `:8020` | `/reset_fatigue_state`, `/set_baseline_from_rows` | rows/baseline body | fatigue/baseline status | legacy loopback guard | endpoint 存在 |
| I-14 | HTTP JSON/stream | PC score `score_server.py:449-557` | tests/local tooling | PC score | `:8020` | `/process_session`, `/process_session_stream`, `/process_task_directory`, `/process_task_directory_stream` | records or task_directory | score result / NDJSON stream | legacy loopback guard | endpoint 存在 |
| I-15 | HTTP JSON | Doctor backend `main.py:44-52` | frontend/browser | Doctor backend | `127.0.0.1:8000` | `GET /health` | 无 | status/service/source/active_session | frontend no explicit retry | endpoint 存在 |
| I-16 | HTTP JSON | Doctor backend `main.py:66-86` | frontend `demoApi.ts:2-20` | Doctor backend | `:8000` | `/api/demo/scenarios`, `/api/competition-demo/start`, `/api/demo/start`, `/api/demo/reset`, `/api/state/current` | scenario/speed | snapshot/session | frontend promise only | 完整调用证据 |
| I-17 | HTTP JSON | Doctor backend `main.py:91` | frontend maybe status UI | Doctor backend | `:8000` | `GET /api/model/status` | 无 | model status | 未定位重试 | endpoint 存在 |
| I-18 | HTTP JSON | Doctor backend `main.py:103-125` | frontend `patientApi.ts`, `reportApi.ts` | Doctor backend | `:8000` | patients/sessions/report | path params | patient/session/report | no retry | 完整调用证据 |
| I-19 | HTTP JSON/CSV | Doctor backend `main.py:128-149` | frontend `logApi.ts`, `RawDataLogsPage.tsx` | Doctor backend | `:8000` | logs/read/export | query type/sensor/time/limit/format | JSON items or CSV/JSONL response | 2s refresh in page | 完整调用证据 |
| I-20 | HTTP JSON | Doctor backend `main.py:152-160` | frontend `reportApi.ts:3-9` | Doctor backend | `:8000` | `GET /api/report/current` | 无 | current report | no retry | 完整调用证据 |
| I-21 | WebSocket | Doctor backend `main.py:163-164`, `websocket_hub.py:10-53` | frontend `websocketClient.ts:24-40` | Doctor backend | `ws://<host>/ws/live` proxied to `:8000` | realtime messages | WS connect | snapshot/messages | exponential reconnect max 5s | 完整调用证据 |
| I-22 | HTTP proxy | Doctor Vite `frontend\vite.config.ts` | browser dev server | Doctor backend | `5173 -> 8000` | `/api`, `/health`, `/ws` | proxied | proxied | Vite proxy | 源码配置 |
| I-23 | UDP | ESP sensor `rehab_sensor_rx.c:784-810` | S3/sensor nodes | ESP P4 | listen port from sensor config | sensor packet receive | IMU/sEMG packet | status/CSV rows | health counters; no retry found | 需要真实设备 |
| I-24 | UART | ESP car `main.c:80-111`, `rehab_car` | ESP target/person follow | car controller | UART port/gpio in rehab_car config | STOP/move lines | text command | no ACK in startup log | write wait timeout | 需要真实硬件 |
| I-25 | HTTP | ESP HTTP server `main.c:513-542` | browser/config UI | ESP | port not located in focused audit | config/files/webim UI | app config/files | JSON/static UI | not audited | endpoint group exists |
| I-26 | Local JSON | ESP app config `app_config.c:77-103` | app/main/UI | ESP local storage | DATA root | config load/save | LLM/IM/vision/score URLs | app_config_t | no network | local persistence |
| I-27 | Local FATFS/SD | ESP `app_fs.c:210-245` | all ESP components | flash/SD | `/fatfs` or SD mount | file IO | binary/json/csv | files | recovery copy | local persistence |
| I-28 | Local CSV/JSON | ESP recorder `rehab_training_recorder.c:855-892` | score bridge/skills | DATA session dir | `DATA_SEQ*.csv`, `task.json` | CSV + 7-field task | CSV/task metadata | finalized callback | 完整调用证据 |
| I-29 | Local JSON | ESP score bridge `rehab_score_bridge_storage.c:498-557` | rehab capabilities/fatigue | DATA session dir | `score_status.json`, `score_result.json` | status/result | parsed query result | atomic write | 完整调用证据 |
| I-30 | Local JSON | ESP fatigue `rehab_fatigue.c:23,215-309,360-377` | rehab system | DATA root | `rehab/fatigue_state.json` | state | fatigue status | atomic write | 完整调用证据 |
| I-31 | Local files | ESP scan capture `rehab_scan_capture.c:64-102,198-221` | scan transfer | DATA root | `rehab/scans/<scan>` | image/tof/frame/scan JSON | scan dir | atomic/rename | capture exists; PC service待确认 |
| I-32 | HTTP | ESP scan transfer `rehab_scan_transfer_http.c:115-172` | ESP scan_transfer | external scan service | default docs `:8030/api/v1/scan/jobs` | create/status/download | scan job JSON | result JSON file | not fully traced | 被调用方不在本轮范围 |
| I-33 | Local JSONL | Doctor raw logs `raw_log_service.py:15-19,42-102` | demo engine/log API | local FS | `backend\data\patients\<id>\sessions\<session>` | `*.jsonl`, `session.json`, `report.json` | logs/report | queue; drop count | 完整调用证据 |
| I-34 | Local JSON | Doctor patients `patient_service.py:17-24` | doctor API | local FS | `backend\data\patients.json` | read JSON | patients | none | 完整调用证据 |
| I-35 | localStorage | Doctor clinical workspace `DemoClinicalWorkspaceDataSource.ts:32-50,149-164` | Dashboard/Decision/Report | browser | localStorage prefix | prescription/review | demo objects | none | mock/demo |
| I-36 | Nginx config | Vision deploy `deploy\nginx-location.conf:2-10` | external nginx | PC vision | proxy `127.0.0.1:8010` | `/api/v1/vision/*` implied | HTTP proxy | HTTP proxy | timeouts lines 7-9 | deploy config only |
| I-37 | systemd | Vision deploy `deploy\vision-interpreter.service:11-13` | Linux systemd | PC vision | `127.0.0.1:8010` | service exec | env config | process | restart by systemd | deploy config only |
| I-38 | Process spawn | Vision launcher `Start-VisionPC.ps1:1084-1086` | user | PC vision | host/port env | uvicorn | process args | PID/status | health wait | startup script |
| I-39 | Process spawn | Score launcher `Start-ScorePC.ps1:130-140` | user | PC score | host/port config | uvicorn | process args/env | PID/status | health/OpenAPI identity | startup script |
| I-40 | Process spawn | Doctor `scripts\start_all.ps1:56-81` | user | backend/frontend | `8000`/`5173` | Start-Process/npm | process args | windows | none | startup script |
| I-41 | HTTP config/UI external link | ESP embedded UI `externalLinks.ts:12-13` | browser user | Aliyun docs/console | public HTTPS | docs links | none | page navigation | none | link only |
| I-42 | HTTP LLM base URL | ESP app_config `app_config.c:123,134` | generic LLM backend code outside rehab scope | DashScope-compatible endpoint | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI-compatible API | LLM request | LLM response | not audited | config/default only |
| I-43 | Local model files | Vision config `vision_config.json:75,95-108` | vision service | local FS | `models\*.pt` | model load | weights | detector/classifier | load at lifespan | local dependency |
| I-44 | Local templates/CSV | Score `score_server.py:24-29`, `session_processor.py:60,904-910` | score service | local FS | template/data dirs | template load | scoring templates | score result | no network | local dependency |

## 6. 实际数据流

| 数据类型 | 生成 | 转换 | 展示 | 保存 | 跨进程/跨设备/公网 | 唯一 ID/重复源 |
|---|---|---|---|---|---|---|
| Camera + ToF fusion | ESP camera/ToF providers，`main.c:364-408` | ESP multipart -> PC vision `/fuse`; PC YOLO/fusion `main.py:172-313` | 未发现医生端展示真实 fusion | PC vision 返回 HTTP；ESP 可消费结果 | ESP -> PC，局域网 HTTP；未发现公网上传 | frame metadata；fusion 与 person-follow 都可使用 camera |
| Person-follow frame | ESP `rehab_person_follow_perception.c:55-130` 获取 JPEG | PC `person_follow_service.py:48-149` 解析 detection | PC viewer 可查看 latest | viewer 内存态；无持久化证据 | ESP -> PC，局域网 HTTP | frame_id/frame_generation |
| Sensor training CSV | ESP UDP receiver/recorder | `rehab_training_recorder.c:855-892` 写 `DATA_SEQ*.csv` + `task.json`；score bridge 上传 | ESP capabilities 可查；医生端未接真实 ESP CSV | ESP DATA session dir；PC score runtime job dir | sensor -> ESP UDP；ESP -> PC score HTTP | session_id、csv SHA256 |
| Score result | PC score job worker/session_processor | compact/full result JSON | ESP query capabilities；医生端 demo report 不读 PC score | ESP `score_result.json`；PC runtime result | ESP <-> PC score HTTP | job_id/session_id |
| Fatigue state | ESP score success observer | `rehab_fatigue_on_score_succeeded` 更新 | rehab capabilities | `rehab/fatigue_state.json` | 本机 ESP storage | latest_session_id |
| Doctor demo realtime | `demo_engine.py` | WebSocket messages + raw_log_service records | React pages | `backend\data\patients\<id>\sessions\<session>\*.jsonl` | backend -> frontend WS；未跨设备 | demo session_id |
| Doctor report | demo snapshot/scenario | `report_service.py:51-146` | `ReportPage`/`ReportPreview` | `report.json` | 本机 HTTP | session_id |
| Prescription/review | 前端 demo data source | localStorage JSON | Dashboard/Decision/Report | browser localStorage | 不跨进程 | patient/session key |

## 7. 本地持久化

| 工程 | 路径/格式 | 证据 | 说明 |
|---|---|---|---|
| ESP | DATA root `/fatfs` 或 SD mount；SYSTEM `/system` | `app_fs.c:32-42,210-245` | SD 可用时 DATA 切到 SD mount；否则 flash FATFS |
| ESP training | `<DATA>/rehab_sessions/<session>/DATA_SEQ*.csv`、`task.json` | `rehab_score_bridge_priv.h:11`；`rehab_training_recorder.c:855-892` | 评分桥使用 frozen CSV/task |
| ESP score | `score_status.json`、`score_result.json` | `rehab_score_bridge_storage.c:498-557` | atomic write |
| ESP fatigue | `<DATA>/rehab/fatigue_state.json` | `rehab_fatigue.c:23,215-309` | atomic write |
| ESP scan | `<DATA>/rehab/scans`、`<DATA>/rehab/scan_results` | `main.c:596-628`; scan storage files | PC scan service未在本轮范围确认 |
| PC vision | `models\*.pt`、config JSON；launcher logs under `%LOCALAPPDATA%` | `vision_config.json`; `Start-VisionPC.ps1:1013-1022` | latest viewer 为内存态 |
| PC score | `runtime_data`、template JSON、DTW CSV | `score_server.py:24-35`; `session_processor.py:1093` | job/session result |
| Doctor UI/backend | `backend\data\patients.json`; `backend\data\patients\<id>\sessions\<session>\*.jsonl/session.json/report.json` | `patient_service.py:17`; `raw_log_service.py:42-102`; `log_export_service.py:31-49` | 当前 demo 数据源 |
| Doctor frontend | localStorage | `DemoClinicalWorkspaceDataSource.ts:32-50,149-164` | 处方/审查 demo 存储 |

## 8. 阿里云相关代码痕迹

| 痕迹 | 位置 | 状态 |
|---|---|---|
| DashScope compatible base URL default/preset | `C:\ec\esp-claw\application\edge_agent\components\app_config\app_config.c:123,134` | 仅配置存在；属于通用 LLM 后端配置 |
| ESP embedded setup/LLM UI Aliyun Bailian 链接 | `application\edge_agent\components\http_server\frontend_source\src\constants\externalLinks.ts:12-13`; LLM preset `LlmPage.tsx:53-59`; setup wizard `SetupWizardPage.tsx:116-122` | 仅 UI 配置/文档链接 |
| PC vision FastAPI title/部署文档历史命名 | `pc\vision_interpreter\app\main.py` title 使用 Aliyun 命名；`DEPLOY_IDENTITY_MODEL_B_REPORT.md` 提到 Nginx/systemd | 名称/文档痕迹；启动时未发现阿里云 API 调用 |
| Nginx/systemd | `pc\vision_interpreter\deploy\vision-interpreter.service:11-13`; `deploy\nginx-location.conf:2-10` | 本地部署配置，proxy 到 `127.0.0.1:8010` |
| AccessKey/OSS/数据库 | 本轮 `rg` 未在审计范围源码中发现康复业务 AccessKey env、OSS SDK、数据库连接调用 | 未发现 |
| 康复数据上传公网 | 未发现 ESP/PC score/doctor 端把患者、训练、评分、报告上传到阿里云 | 未发现 |

## 9. 已接通链路

以下为源码调用链证据，不代表本轮运行验证。

| 链路 | 证据 |
|---|---|
| ESP recorder -> ESP score bridge -> PC score job API | `rehab_training_recorder.c:1248-1257` finalized callback；`rehab_score_bridge_core.c:439-544` 注册并调用；`rehab_score_bridge_http.c:260-403` HTTP；PC `score_job_api.py:48-104` |
| ESP cloud fusion -> PC vision `/api/v1/vision/fuse` | ESP `rehab_cloud_fusion_client.c:271-451`；PC `main.py:172-313` |
| ESP person-follow -> PC vision `/api/v1/person-follow/detect` | ESP `rehab_person_follow_perception.c:129-219`；PC `person_follow_router.py:42-81` |
| ESP score success -> fatigue state | `rehab_system.c:112-125` observer；`rehab_fatigue.c:406` |
| Doctor frontend -> Doctor backend HTTP | `patientApi.ts`, `demoApi.ts`, `logApi.ts`, `reportApi.ts`; backend routes `main.py:66-160` |
| Doctor frontend -> Doctor backend WebSocket | `websocketClient.ts:24-40`; backend `main.py:163-164`; `websocket_hub.py:10-53` |
| Doctor demo engine -> raw logs/report -> UI | `demo_engine.py:120-240`; `raw_log_service.py:42-102`; UI pages consume APIs |

## 10. 未接通链路

| 链路 | 当前事实 |
|---|---|
| Doctor UI -> ESP-Claw 实时训练状态 | 未发现医生端后端调用 ESP HTTP、UDP、串口或读取 ESP SD 数据；医生端当前 `SOURCE_MODE="demo"` |
| Doctor UI -> PC vision service | 未发现医生端调用 `:8000/api/v1/vision/*`；医生端后端自身也监听 `8000` |
| Doctor UI -> PC score service | 未发现医生端调用 `:8020/api/v1/score/*`；报告为 demo report |
| ESP scan transfer -> 本轮范围内 PC scan service | ESP client 存在；本轮审计范围未包含/未发现实现 `/api/v1/scan/jobs` 的 PC scan service |
| 阿里云康复数据同步 | 未发现患者档案、训练 CSV、score_result、doctor report 上传阿里云 |
| `VITE_CLINICAL_DATA_MODE=live` 医生端真实数据源 | `HttpClinicalWorkspaceDataSource.ts:10-42` 全部方法抛未实现错误 |
| `baseline`/BRS 到正式链路 | 评分服务有 endpoint/字段支持；未发现 ESP/医生端调用 |

## 11. mock/demo 项

| 项 | 证据 | 状态 |
|---|---|---|
| 医生端全局 source mode | `backend\app\config.py:7` `SOURCE_MODE="demo"` | demo |
| 医生端 demo engine | `backend\app\demo_engine.py:120-240,361,412` | 生成 demo realtime、demo-score |
| 医生端 report | `backend\app\report_service.py:77,138-146` | `demo-score-1.0` 与 demo notice |
| 医生端 clinical workspace | `DemoClinicalWorkspaceDataSource.ts:25-50,91-103,149-164` | localStorage demo |
| 医生端 live clinical data source | `HttpClinicalWorkspaceDataSource.ts:10-42` | live API 未实现 |
| 医生端设备状态 | `demo_engine.py:424-430`; `DeviceStatusPage.tsx:7,69-74` | demo generated + fallback constants |
| 医生端历史/demo session | `backend\data\patients\P-0217\sessions\SESSION_DEMO_*` | 本地 demo session |
| PC 评分 mock trials | `pc\rehab_score_service\data\mock_trials\*.json` | mock/test data |

mock/demo 项计数：8。

## 12. 文档与源码冲突

| 文档 | 文档陈述 | 源码/当前事实核对 | 标记 |
|---|---|---|---|
| `ALIYUN_CURRENT_STATE.md:9-28` | DashScope 是通用 LLM 配置；康复闭环未接阿里云 | 源码只发现 DashScope config/UI 链接，未发现康复数据上传阿里云 | 源码支持 |
| `STARTUP_MATRIX.md:8-11` | 视觉 8000、评分 8020、医生后端 8000、前端 5173 | 启动脚本源码支持这些端口 | 源码支持 |
| `STARTUP_MATRIX.md:7`、`LOCAL_SETUP.md:163-173`、`ZERO_REGRESSION_PLAN.md:27,46` | ESP-IDF v6.0 build/验证 | 用户当前声明正式基线为 ESP-IDF v5.5.4；本轮未运行构建 | 已过时/与当前基线冲突 |
| `CURRENT_ARCHITECTURE.md:66` | 文档称本机没有 ESP-IDF 和真实 P4/热点验证 | 用户当前声明 v5.5.4 下已成功构建；本轮未复核运行 | 文档阶段性过时 |
| `FUNCTION_INVENTORY.md:15` | scan transfer 指向独立 PC scan service `:8030`，PC scan pipeline 未恢复 | ESP scan transfer 源码存在；本轮范围未发现 PC scan service | 源码部分支持，PC service 无法复核 |
| `INTERFACE_MATRIX.md:19` 等 | 评分 job/health API 字段 | PC score `score_job_api.py` 与 ESP `rehab_score_bridge_http.c` 支持 | 源码支持 |
| `CONTEXT_RECOVERY_REPORT.md:74,126-133` | 记录若干运行报告、v6 阶段验证 | 本轮只读未复跑；其中 v6 基线与当前用户声明 v5.5.4 不一致 | 只有运行报告支持/部分过时 |

## 13. 无法确认事项

| 事项 | 原因 |
|---|---|
| 当前 ESP 固件在真实 P4 上启动、Camera/ToF/UDP/UART 硬件均可用 | 本轮禁止构建、烧录、运行、连接设备 |
| PC 视觉模型实际加载与推理性能 | 本轮未启动服务；仅有模型文件和加载代码 |
| PC 评分服务实际处理当前 ESP 生成 CSV 的运行结果 | 本轮未启动评分服务；未提交真实 task directory |
| 医生端当前后端/前端运行态 | 本轮未启动；仅静态确认入口和源码 |
| scan PC service `:8030` | 目标服务不在本轮审计范围内，未发现对应源码 |
| 8083 端口 | 本轮在审计范围源码/脚本中未发现实际监听或调用证据 |
| 阿里云在线状态/服务器配置 | 用户禁止登录/访问阿里云；本轮只检查本地痕迹 |
| `baseline`/BRS 是否被比赛流程使用 | 发现服务端支持，但未发现当前调用方 |
| 离线缓存 | 未发现明确的离线同步/重放队列实现；本地持久化存在但不能等同离线缓存 |

## 14. 2026-07-25 真实运行接入源码事实更新

本节记录本轮源码实施后的静态事实，优先于本文前面关于医生端仅有 demo 数据源、live clinical source 未实现、医生端未调用 P4/评分服务的旧结论。本轮禁止运行测试、服务、网络请求、构建和硬件，因此以下均为“源码实现存在，运行待验证”，不是运行成功声明。

### 14.1 已确认的原始传感协议

- 传感节点到 P4 使用 UDP，P4 监听 `0.0.0.0:5005`：`application/edge_agent/components/rehab_sensor_recording/src/rehab_sensor_internal.h`、`rehab_sensor_rx.c`。
- 冻结包为 `sensor_packet v5`，小端 packed struct，长度 242 字节，magic `0x34533353`：`rehab_sensor_recording/src/sensor_packet_v5.h`。
- Node 1 类型为 `EMG_DUAL_IMU`，含四通道 sEMG、IMU1、IMU2；Node 2 类型为 `DUAL_IMU`，含 IMU1、IMU2。
- IMU 原始字段为 `ts_us`、`update_count`、`age_ms`、`acc_*_g`、`lin_acc_*_g`、`gyro_*_dps`、`angle_*_deg`。
- sEMG 原始字段为 `window_start_us`、`window_end_us`、`sample_count`、四通道 `latest`、`rms`、`mav`、`peak_abs`。
- 源码范围内未发现传感节点发送器、PC UDP 接收器或 `6006` 端口；现有接收方是 P4，而不是医生端 PC。

### 14.2 新增只读 P4 运行快照

- `rehab_training_get_live_snapshot()` 在线程锁内复制 P4 已验证的最新 Node 1/2 包、节点计数和训练状态，不改变 UDP 解码、训练状态机或 recorder：`rehab_sensor_recording.h`、`rehab_sensor_rx.c`。
- P4 现有 HTTP 服务端口仍为 `80`，新增 `GET /api/rehab/runtime`：`http_server_rehab_api.c`、`http_server_core.c`。
- 响应 schema 为 `rehab.runtime.v1`，包含 `training.current_session`、`training.last_completed_session`、`score_bridge`、Node 1/2 最新包和真实累计质量字段。
- 接口仅暴露当前最新包快照及累计计数，不是逐包流式转发；医生端默认以 10 Hz 轮询，因此不能声称 PC 收到每一个 50/100 Hz 原始包。

### 14.3 医生端正式设备模式

- `DOCTOR_SOURCE_MODE=device` 是正式启动脚本默认值；`presentation` 和 `demo` 仍可显式选择，但不会被正式设备模式调用：`backend/app/config.py`、`scripts/start_backend.ps1`、`scripts/start_all.ps1`。
- 启动脚本只读复用 `%LOCALAPPDATA%\ESP-Claw\vision_launcher\last_p4_ip.txt` 形成 `P4_BASE_URL`；缓存不存在时保持未配置并显示等待设备数据。
- `RealRuntimeService` 轮询 `P4_BASE_URL/api/rehab/runtime`，使用每个节点真实 `tx_seq` 去重，以每类最多 500 条的内存环形队列保存 IMU、sEMG、设备、事件和评分日志：`backend/app/real_runtime_service.py`。
- 在线状态由最后一次 P4 响应和集中配置 `P4_DEVICE_OFFLINE_TIMEOUT_SECONDS` 判断；超时后不再生成数据。
- P4 `recording` 状态触发医生端 `RUNNING`，P4 回到 `idle` 且存在本次完成会话时触发停止；网页在 `control_mode=p4` 时不显示医生端开始、快速演示和重置控制。
- 当前 session 只来自 P4 的 `current_session.session_id` 或 `last_completed_session.session_id`；评分查询优先使用 P4 score bridge 上报的实际 `pc_job_id`，缺失时才使用同一实际 session id，并校验结果中的 `session_id` 后接收。正式路径不固定读取 `20260724_TASK001`。
- 评分 `subscores` 存在时原样使用；缺失时正式设备模式保持空字段，不将 presentation 补充分数表述为评分服务原始字段。
- Agent 轨迹只在真实设备上下线、首次收到本 session 传感快照、训练状态变化、score bridge 状态变化和真实 score result 到达时生成，内部标记 `agent_trace=generated_from_real_events`。

### 14.4 医嘱与页面数据源

- 正式前端 clinical data source 默认使用 HTTP，不再抛 `LIVE_CLINICAL_WORKSPACE_API_NOT_IMPLEMENTED`：`HttpClinicalWorkspaceDataSource.ts`、`clinicalWorkspaceDataSource.ts`。
- 后端新增处方、任务状态、事件轨迹、安全记录、趋势和治疗师审核接口：`backend/app/main.py`、`clinical_service.py`。
- 当前允许的预设任务 ID 仅为 `drink_water_right` 和 `pick_book_right`；处方字段明确 `requires_per_session_approval=false`，患者端训练不依赖医生每轮在线批准。
- 医生端 `/ws/live` 继续广播真实 snapshot 和 device status；原始日志页面读取同一运行服务的有界日志并以 500 ms 周期刷新。

### 14.5 仍需运行或硬件确认

- 新增 C 代码未执行 ESP-IDF build，HTTP schema 未在 P4 上实测。
- 医生端未启动，P4 HTTP、PC score HTTP 和 `/ws/live` 均未实际调用。
- 未确认四个 `imu1..imu4` 与人体部位的真实安装映射，因此正式模式不生成肩、肘、腕、躯干派生角度，也不驱动数字孪生。
- P4 快照轮询不能证明每个原始传感包均到达 PC。
- 动作复现、次数识别和标准模板 CSV 匹配未实施；用户提供的单次喝水标准 CSV 未复制、未使用、未打包。

## 15. 2026-07-25 医生端页面与实时曲线静态事实

本节仅记录源码状态。本轮未运行测试、服务、HTTP、WebSocket、浏览器或构建，以下实现均需用户在真实设备在线时验证。

### 15.1 正式页面

医生端患者工作区当前导航固定为七页：总览、实时监测、数据日志、Agent轨迹、动作还原、训练报告、医嘱管理。正式入口不提供演示启动、快速训练或重置操作。

- 总览只汇总患者、医嘱、设备、当前训练和已有 session。
- 实时监测只展示 P4 运行快照派生的 4 路 IMU 与 4 路 sEMG 图表。
- 数据日志读取运行服务已有的 IMU、sEMG、设备、训练、上传和评分事件。
- Agent轨迹按实际 session 查询真实状态变化事件，不生成固定轨迹。
- 动作还原目前只提供实际 session 数据索引，并明确显示“本次训练数据已保存，动作还原结果尚未生成。”
- 训练报告按实际 session 查询评分结果；缺失字段显示“暂无数据”，不生成替代分数。
- 医嘱管理使用现有处方 HTTP 数据源，允许任务限定为 `drink_water_right` 或 `pick_book_right`，且不要求每次训练在线审批。

### 15.2 八路实时曲线的数据绑定

- `imu1`、`imu2`、`imu3`、`imu4` 分别绑定 P4 runtime snapshot 的 `node1.imu1`、`node1.imu2`、`node2.imu1`、`node2.imu2`，展示原始 `angle_x_deg`、`angle_y_deg`、`angle_z_deg`，在医生端命名为 Roll、Pitch、Yaw。
- `emg1` 至 `emg4` 分别绑定 `node1.emg` 的四个通道。曲线使用真实 `rms`；卡片同时展示真实 `mav`、`peak_abs` 和 `latest`。
- 后端仅在节点 `tx_seq` 变化时更新时间戳；前端按 `sequence` 去重，每路最多保留 120 个快照点，并显示相对最新点约 10 秒的数据。
- P4 或节点离线时，样本 `online=false`，已有曲线冻结；缺失或无效样本不补零、不插值、不使用随机数。
- 这些曲线来自约 10 Hz 的 P4 最新快照轮询，不等同于 50/100 Hz 原始包完整归档。
- 四个 IMU 的人体安装部位映射仍未确认，因此页面只显示 IMU 1 至 IMU 4，不把它们命名为具体肢体部位，也不派生人体关节角。

### 15.3 运行边界

本轮未修改 P4 工程，未运行硬件或软件验证。八路曲线、页面路由、日志查询、处方保存和报告查询仅有静态源码证据；真实刷新率、断连表现、长时间稳定性和浏览器布局仍待用户亲自验证。

## 16. 2026-07-25 设备历史持久化与 sEMG 状态修正

本节仅记录源码实现，未进行服务、网络或浏览器运行验证。

### 16.1 真实 session 索引

- device 模式复用已有 `backend/data/patients/<patient_id>/sessions/<session_id>/` 目录体系。
- 每个真实 session 的必要摘要写入同目录 `session.json`，以 `session_id` 为唯一关联键。
- `RealRuntimeService.start()` 加载已有正式摘要，因此 `/api/patients/{patient_id}/sessions` 的数据源不再只依赖当前进程内存。
- 加载器只接受 `source_mode=device`、`patient_id` 匹配、文件内 `session_id` 与目录名一致且通过字符校验的记录。
- `SESSION_DEMO_*` 在读取元数据前排除，不进入正式历史索引。
- 医生端只持久化摘要和报告，不读取、复制或重新保存 P4 原始患者 CSV。

### 16.2 历史报告

- 真实报告由单个 `self._report` 改为 `self._reports[session_id]`。
- 评分完成后，报告写入对应 session 目录的 `report.json`。
- 服务启动时，仅为已通过正式 session 校验的目录加载报告，并再次校验报告内 `patient.patient_id`、`patient.session_id` 和 `versions.source_mode=device`。
- `GET /api/patients/{patient_id}/sessions/{session_id}/report` 按请求的 `session_id` 查找，不回退到其他 session；没有对应报告时返回 404。
- 报告字段仍来自真实评分结果，不生成固定历史报告或替代评分。

### 16.3 sEMG 状态

- 实时监测页的 sEMG 摘要直接查找 `device_id=node1_semg`。
- `node1_semg.online` 由 Node 1 在线且 sEMG `valid=true` 共同决定；Node 1 在线但 sEMG 无效时，摘要显示“异常或连接中断”。
- 各 EMG 卡片继续分别展示节点在线状态和当前数据有效状态，不生成正常状态。

### 16.4 保持不变

七页导航、八路图表布局、IMU/sEMG 字段绑定、每路 120 点上限、sequence 去重、断流冻结和动作还原占位逻辑均未改变。动作还原算法与次数识别仍未实现。
