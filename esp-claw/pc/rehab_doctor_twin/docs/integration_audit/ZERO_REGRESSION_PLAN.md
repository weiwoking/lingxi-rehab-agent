# Zero Regression Plan

审计时间：2026-07-23

## 冻结边界

| 边界 | 不允许回归 |
| --- | --- |
| 视觉 | `/api/v1/vision/health`, `/api/v1/vision/fuse`, `/api/v1/person-follow/detect` 路径、schema、模型配置语义 |
| 追人 | Agent 只 start/stop/status；本地 worker 逐帧；每轮最多一个有界动作；STOP 优先 |
| 评分 | `/api/v1/score/jobs*` job API、旧 `/score_trial` public 字段、`ROM_score` 大小写 |
| 训练记录 | `/sdcard/rehab_sessions/YYYYMMDD_TASKNNN`、frozen CSV 86 列、七字段 `task.json` |
| 疲劳 | 仅在评分成功并落盘后更新；不可用时 fail-open |
| 医生端 | `/ws/live` 消息类型、`session_id`/`seq` 过滤、`applyLiveSensorFrame` 统一入口 |
| 阿里云 | 不作为本地闭环必经路径；不在无鉴权/脱敏设计前上传患者敏感数据 |

## 回归验证命令

| 层级 | 命令 | 目标 |
| --- | --- | --- |
| 视觉启动器 | `C:\ec\esp-claw\tools\vision_launcher\Start-VisionPC.cmd -SelfTest` | 启动器路径和环境发现 |
| 视觉服务 | `Start-VisionPC.ps1 -ValidationMode -HotspotWaitSeconds 1 -HealthTimeoutSeconds 120 -P4ScanBudgetSeconds 1` | 本机 health、OpenAPI、进程清理 |
| 评分环境 | `C:\ec\esp-claw\tools\score_launcher\Check-ScorePC.ps1` | Python 3.10、imports、资产 |
| 评分 smoke | `Start-ScorePC.ps1`; `Smoke-ScorePC.ps1`; `Stop-ScorePC.ps1` | health、OpenAPI、关键路由 |
| 医生后端 | `C:\ev\rehab_doctor_twin\backend\.venv\Scripts\python.exe run.py`; `GET /health` | gateway 可启动 |
| 医生前端 | `pnpm run typecheck`; `pnpm run build`; `pnpm run dev` HTTP 200 | UI 构建和 dev server |
| ESP 固件 | 导入 ESP-IDF v6 profile 后 `idf.py bmgr -c ./boards -b esp32_p4_function_ev`; `idf.py build` | P4 固件构建与镜像生成 |

## 后续集成保护策略

1. 新增 PC Adapter 时，只做协议转换：ESP/评分/视觉现有输出 -> 医生端 `RealtimeMessage`，不要改前端消息类型。
2. 医生端后端若需和视觉服务并行运行，优先调整启动编排或新增反向代理；不要直接改视觉 API 路径。
3. 评分服务保持本地 8020 job API 为 P4 正式入口；旧 loopback POST 接口只给本机调试/兼容。
4. 任何 Aliyun 上传必须异步、可关闭、失败不影响本地训练，并记录脱敏字段清单。
5. P4 现场验证前，不把 `real` 模式标志写成硬件已就绪。

## 本轮已验证

| 项 | 结果 |
| --- | --- |
| 视觉 self-test | 通过 |
| 视觉 health/openapi | `status=ok`, `model_ready=True`, `/api/v1/person-follow/detect` 存在 |
| 评分 check/smoke | 通过，health ready，service version `0.2.1` |
| 医生端后端 | `/health` 返回 `status=ok` |
| 医生端前端 | typecheck/build 通过，dev 首页 HTTP 200 |
| ESP 固件 | `idf.py bmgr -c ./boards -b esp32_p4_function_ev` 与 `idf.py build` 通过，生成 `edge_agent.bin` |
| 残留端口 | 8000/8020/5173 无监听 |

## 尚未验证

1. P4 热点和真实设备访问 PC 视觉/评分服务。
2. ESP-Claw `flash monitor` 后真实板端启动。
3. 真实训练 Session 从采集、上传、评分、落盘、查询的端到端闭环。
4. P4 scan `:8030` PC pipeline。
5. 医生端真实 Adapter 和真实 WebSocket 数据。
