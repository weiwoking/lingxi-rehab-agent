# Runtime Verification Report

验证时间：2026-07-24 01:16-01:34，Asia/Shanghai。

本轮依据 `C:\ev\rehab_doctor_twin\docs\integration_audit\CURRENT_SYSTEM_FACTS.md` 进行运行态验证。未修改代码、配置、端口、ESP-IDF、固件、环境或服务器；未构建、未烧录、未执行 Git 操作；未访问阿里云。仅启动/停止本机进程、调用本机 HTTP/WebSocket 接口，并新增本报告。

## 1. 视觉服务启动结果

| 项 | 结果 |
|---|---|
| 工程路径 | `C:\ec\esp-claw\pc\vision_interpreter` |
| 启动器 | `C:\ec\esp-claw\tools\vision_launcher\Start-VisionPC.ps1` |
| 使用 Python | `C:\ec\_conda_envs\vision_interpreter\python.exe` |
| SelfTest | 通过；启动器自检输出“全部启动器自检通过” |
| ValidationMode | 实际拉起服务；命令在本轮 180 秒工具超时前未返回，但服务已经启动并通过 health/openapi；后续手动停止 |
| 实际监听 | `0.0.0.0:8000`，进程 `C:\ec\_conda_envs\vision_interpreter\python.exe`，PID `13192` |
| 停止结果 | 已停止 PID `49368` launcher 与 PID `13192` uvicorn；停止后 8000 无视觉残留监听 |

日志证据：

- `YOLO model loaded`
- `Identity model loaded`
- `Detector warm-up complete artifact=c:\ec\esp-claw\pc\vision_interpreter\models\yolo11n.pt`
- `Person-follow ready=True shared_with_fusion=True`
- `Uvicorn running on http://0.0.0.0:8000`

## 2. 视觉 health 和模型状态

| 验证项 | 结果 |
|---|---|
| `GET /api/v1/vision/health` | 200 |
| `GET /openapi.json` | 200 |
| OpenAPI 包含 `/api/v1/person-follow/detect` | 是 |
| OpenAPI 包含 `/api/v1/vision/fuse` | 是 |
| `/api/v1/person-follow/detect` | 200；返回 `schema_version=person_follow.v1`，`target_found=false`，模型字段 `device=cpu` |
| `/api/v1/vision/fuse` | 200；返回 `schema_version=vision_fusion.v1`，`valid=true`，`status=ok` |
| 模型 A | 已真实加载；health `model_path=C:\ec\esp-claw\pc\vision_interpreter\models\yolo11n.pt`，`model_ready=true` |
| 模型 B | 已真实加载；服务日志 `Identity model loaded` |
| CPU/GPU | `torch.cuda.is_available=False`，`torch.cuda.device_count=0`；person-follow 返回 `device=cpu` |

本轮 person-follow 和 fuse 使用本机生成的空图像/RAW RGB565 与合法 ToF 测试负载，只验证接口和模型调用可达，不代表真实摄像头、ToF 或 P4 数据。

## 3. 评分服务启动结果

| 项 | 结果 |
|---|---|
| 工程路径 | `C:\ec\esp-claw\pc\rehab_score_service` |
| 启动器 | `C:\ec\esp-claw\tools\score_launcher\Start-ScorePC.ps1` |
| 使用 Python | `C:\ec\_conda_envs\rehab_score\python.exe` |
| 启动结果 | 成功 |
| 实际监听 | `0.0.0.0:8020`，PID `28152` |
| service version | `0.2.1` |
| runtime root | `C:\ec\esp-claw\pc\rehab_score_service\runtime_data` |
| 停止结果 | `Stop-ScorePC.ps1` 成功停止 owned PID `28152`；停止后 8020 无残留监听 |

`Check-ScorePC.ps1` 结果：

- repo/service/python/python-version/imports 均 OK。
- Python 为 `3.10.20`。
- healthy template 存在。
- Task 12 DTW 模板目录包含 6 个 CSV。
- service identity 为 `rehab_score_service 0.2.1 ready`。

## 4. 评分 smoke 和冻结接口结果

`Smoke-ScorePC.ps1` 结果：

- health ready。
- `GET /` 通过。
- `GET /demo` 通过。
- `GET /openapi.json` 与关键路由通过。
- 未提供 `-TaskDirectory`，脚本按设计跳过真实评分；该 smoke 只证明服务身份与接口合同，不证明医学正确性。

Job API 实测：

| 接口 | 结果 |
|---|---|
| `POST /api/v1/score/jobs` | 使用既有 runtime_data `20260714_TASK002` 的 `task.json`/CSV 做幂等创建，返回 200，`schema_version=rehab_score_job_response_v1`，`state=succeeded` |
| `PUT /api/v1/score/jobs/20260714_TASK002/csv` | 200，`schema_version=rehab_score_job_status_v1`，`state=succeeded` |
| `GET /api/v1/score/jobs/20260714_TASK002` | 200，`state=succeeded` |
| `GET /api/v1/score/jobs/20260714_TASK002/result` | 200，`schema_version=rehab_score_result_v1`，`valid=true`，`average_score=44.42` |

首次尝试使用 `tests\_job_test_support.py` 固定 `SESSION_ID=20260714_TASK002` 但生成的测试内容与本机已有同名 runtime_data 不一致，因此 `POST /jobs` 和 `PUT /csv` 返回 409 `session_content_conflict`。随后使用已有同名目录内的真实 `task.json` 和 CSV 重新验证幂等创建/上传，返回 200。

冻结 legacy 接口：

| 接口 | 结果 |
|---|---|
| `POST /reset_fatigue_state` | 200，`valid=true`，fatigue `state=正常` |
| `POST /set_baseline_from_rows` | 200，使用 `data\mock_trials\mock_trial_normal.json` 作为 rows[0]，`baseline_row_count=1` |
| `POST /score_trial` | 200，使用 `data\mock_trials\mock_trial_normal.json`，返回 `valid=true`，`final_score=76.08`，`score_version=v1.0` |

Health 返回结构中 `fatigue.available=false`、`brs.available=false`，原因分别为兼容状态/未实现推理模型；这与静态审计中“BRS 有字段支持但无正式调用证据”的结论一致。

## 5. 医生端启动结果

| 项 | 结果 |
|---|---|
| 工程路径 | `C:\ev\rehab_doctor_twin` |
| 后端入口 | `backend\.venv\Scripts\python.exe run.py` |
| 后端监听 | `127.0.0.1:8000`，进程树 PID `10792 -> 28096` |
| 后端 health | `GET /health` 返回 200，`service=lingxi-rehab-doctor-twin-gateway`，`status=ok` |
| 前端入口 | `npm run dev` |
| 前端监听 | `127.0.0.1:5173`，Vite node PID `18024` |
| 前端 HTTP | `GET http://127.0.0.1:5173` 返回 200 |
| 停止结果 | 已停止后端、前端及子进程；停止后 8000/5173 无残留监听 |

医生端本轮验证期间 PC 视觉服务和 PC 评分服务均未运行；医生端没有访问 ESP 或阿里云。

## 6. 医生端页面和 WebSocket 结果

| 项 | 结果 |
|---|---|
| 患者列表 | `GET /api/patients` 返回 200，患者数量 1 |
| demo scenarios | `GET /api/demo/scenarios` 返回 200，scenario 数量 1 |
| demo session 启动 | `POST /api/demo/start` 返回 200，session `SESSION_DEMO_20260723_172926_0e206b` |
| WebSocket | `ws://127.0.0.1:8000/ws/live` 成功连接并收到 12 条消息 |
| WebSocket 消息类型 | `device.status`、`rehab.emg`、`rehab.event`、`rehab.state`、`rehab.telemetry` |
| report | `GET /api/report/current` 返回 200，`score_version=demo-score-1.0` |
| report demo notice | `演示数据，不代表真实患者临床结果。` |
| session history | `GET /api/patients/P-0217/sessions` 返回 200，历史 session 数量 69，包含本轮 session |
| raw logs | `GET /api/patients/P-0217/sessions/<session>/logs?type=imu&limit=5` 返回 200，items 数量 5 |
| raw log export | `GET /logs/export?type=imu&format=csv` 返回 200，`content-type=text/csv; charset=utf-8` |
| history report | `GET /api/patients/P-0217/sessions/<session>/report` 返回 200 |
| demo reset | `POST /api/demo/reset` 返回 200，`status=reset` |

明确记录：

- 当前医生端数据来自 demo engine、backend 本地 `backend\data` 和前端 demo/localStorage data source。
- 当前医生端未调用 PC 视觉服务。
- 当前医生端未调用 PC 评分服务。
- 当前医生端未访问 ESP。
- 当前医生端未访问阿里云。

## 7. 8000 端口冲突实测

静态审计发现 PC 视觉 Windows 服务默认 `0.0.0.0:8000`，医生端后端默认 `127.0.0.1:8000`。

实测 A：医生端先占用 8000，再启动视觉 launcher。

| 项 | 结果 |
|---|---|
| 医生端后端监听 | `127.0.0.1:8000`，PID `51400` |
| 视觉启动器结果 | 退出码 1 |
| 视觉启动器错误 | `8000 端口被无法确认的进程占用；为避免误杀，启动器拒绝关闭。进程信息：PID=51400 ... run.py` |
| 是否自动改端口 | 否 |
| 是否误杀医生端 | 否 |

实测 B：视觉先占用 8000，再启动医生端后端。

| 项 | 结果 |
|---|---|
| 视觉监听 | `0.0.0.0:8000`，PID `51568`，health ready |
| 医生端后端启动 | 成功绑定 `127.0.0.1:8000`，PID `2244` |
| 同时监听状态 | 同时存在 `0.0.0.0:8000` 与 `127.0.0.1:8000` |
| `http://127.0.0.1:8000/health` | 返回医生端 health 200 |
| `http://127.0.0.1:8000/api/v1/vision/health` | 返回 404，因为 localhost 请求被医生端接收 |
| 运行差异 | 在 Windows 上二者可形成同时监听，但 localhost 访问被医生端特定绑定覆盖；这不是可用的并行服务状态 |

结论：端口冲突真实存在。视觉 launcher 能识别医生端占用并拒绝启动；反向启动时系统允许两个不同 LocalAddress 的 8000 listener 同时存在，但 `127.0.0.1:8000` 只访问医生端，导致视觉 localhost health 不可达。

## 8. 各服务实际监听进程

| 阶段 | 地址 | PID | 进程 |
|---|---|---|---|
| 视觉单独验证 | `0.0.0.0:8000` | `13192` | `C:\ec\_conda_envs\vision_interpreter\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1` |
| 评分单独验证 | `0.0.0.0:8020` | `28152` | `C:\ec\_conda_envs\rehab_score\python.exe -m uvicorn cloud_interface.score_server:app --host 0.0.0.0 --port 8020 --workers 1` |
| 医生端后端 | `127.0.0.1:8000` | `28096` | `backend\.venv\Scripts\python.exe run.py` |
| 医生端前端 | `127.0.0.1:5173` | `18024` | `node ... vite.js --host 127.0.0.1 --port 5173` |
| 冲突实测 B 视觉 | `0.0.0.0:8000` | `51568` | vision uvicorn |
| 冲突实测 B 医生端 | `127.0.0.1:8000` | `2244` | doctor backend uvicorn |

## 9. 产生的测试数据

| 类别 | 路径/说明 |
|---|---|
| 视觉 launcher 运行状态/日志 | `%LOCALAPPDATA%\ESP-Claw\vision_launcher\launcher_status.json`、`vision_service.log`、`vision_service.stdout.log` 被本轮启动更新 |
| 评分 launcher 运行状态/日志 | `%LOCALAPPDATA%\ESP-Claw\score_launcher\launcher_status.json`、`score_service.log`、`score_service.stdout.log` 被本轮启动/停止更新 |
| 评分 runtime_data | 本轮复用既有 `C:\ec\esp-claw\pc\rehab_score_service\runtime_data\sessions\20260714_TASK002` 验证幂等 job API；未创建新的业务格式 |
| 医生端 demo session | `C:\ev\rehab_doctor_twin\backend\data\patients\P-0217\sessions\SESSION_DEMO_20260723_172926_0e206b`，由 demo engine 产生 session/raw logs/report |
| 临时进程日志 | `%TEMP%\codex_doctor_runtime_verify`、`%TEMP%\codex_vision_conflict_verify`，仅为本轮启动进程 stdout/stderr |

## 10. 停止后的残留进程

最终检查 `8000`、`8020`、`5173` 无监听。

最终进程筛选未发现以下命令行残留：

- `app.main:app`
- `vision_interpreter`
- `cloud_interface.score_server`
- `rehab_doctor_twin`
- `run.py`
- `vite`

筛选输出中只出现执行检查命令自身的 PowerShell 进程，不属于残留服务。

## 11. 静态审计结论与运行结果的差异

| 静态审计结论 | 运行结果 | 差异 |
|---|---|---|
| 视觉可启动，模型运行待确认 | 已确认启动，模型 A/B 加载，CPU 运行 | 运行验证补齐 |
| 评分可启动，真实 job API 待确认 | 已确认启动，Check/Smoke/job API/冻结接口可响应 | 运行验证补齐 |
| 医生端 demo 可启动，真实运行待确认 | 已确认后端、前端、WebSocket、demo、raw logs、history、report 可响应 | 运行验证补齐 |
| 两个 8000 服务不能默认同时运行 | 实测 A 中视觉 launcher 会拒绝医生端占用；实测 B 中 Windows 允许 `0.0.0.0:8000` 与 `127.0.0.1:8000` 同时监听，但 localhost 只访问医生端，视觉 localhost health 404 | 更精确：不是简单“不能绑定”，而是默认 localhost 访问冲突且不可作为并行可用状态 |
| 医生端未接 PC 视觉/评分/ESP/阿里云 | 本轮运行验证期间仍未发现医生端访问这些服务 | 运行结果支持 |

## 12. 尚未验证的硬件链路

本轮没有真实 P4、热点或传感器，因此不得声称已验证：

- P4 到 PC 视觉；
- P4 到 PC 评分；
- 热点通信；
- 摄像头真实图像；
- ToF 真实数据；
- IMU/sEMG 真实数据；
- fatigue 真实触发；
- 真实训练 session；
- ESP 固件在 v5.5.4-dirty 基线下的本轮启动、flash 或 monitor。

这些仍只能标记为：源码链路存在，硬件运行待验证。
