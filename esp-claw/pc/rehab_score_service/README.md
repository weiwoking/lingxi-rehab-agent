# Rehab Score Service

## 当前正式评分体系

当前正式运行路径为五任务 Task Registry 与两套 score profile：

```text
drink_water
lateral_transfer
```

P4 上传的 finalized session 进入 `/api/v1/score/jobs`，由
`ScoreJobWorker` 调用 dedicated job scorer，再经 Task Registry 分派到
`drink_water` 或 `lateral_transfer` 专用评分编排层。历史 Task-12
Session 级 weighted-DTW 管线已退出正式运行路径。

## 环境合同

- Python：3.10.x。
- 独立 Conda prefix：`C:\ec\_conda_envs\rehab_score`。
- 不与 `C:\ec\_conda_envs\vision_interpreter`（YOLO/视觉）混用。
- `requirements.txt` 是直接依赖清单。
- `requirements-lock.txt` 是首个 Windows Python 3.10 复现基线，只冻结已经验证的直接依赖，不伪装成完整传递依赖 lock。

用户本地创建和安装（本次 Codex 实施未执行）：

```powershell
& 'C:\ProgramData\anaconda3\Scripts\conda.exe' create `
  --prefix 'C:\ec\_conda_envs\rehab_score' python=3.10 pip -y
& 'C:\ec\_conda_envs\rehab_score\python.exe' -m pip install `
  -r 'C:\ec\esp-claw\pc\rehab_score_service\requirements-lock.txt'
& 'C:\ec\_conda_envs\rehab_score\python.exe' -m pip check
```

安装并完成本机验证后，再决定是否生成和保存完整 `pip freeze`。

## 服务合同（0.2.1）

- ASGI：`cloud_interface.score_server:app`
- 默认监听：`0.0.0.0:8020`；launcher 仍支持显式绑定具体 PC LAN IPv4。
- 健康：`GET /api/v1/score/health`
- 启动命令由 `tools\score_launcher` 固定为单 worker、无 reload。
- 训练数据唯一根为 `REHAB_TRAINING_DATA_ROOT`（默认 `C:\ec\_rehab_training_data`）。
- 评分资产唯一根为 `REHAB_SCORING_ASSETS_ROOT`（默认 `C:\ec\_rehab_scoring_assets`）。

保留端点：

```text
GET  /
GET  /demo
POST /score_trial
POST /reset_fatigue_state
POST /set_baseline_from_rows
```

也保留 FastAPI `/docs` 和 `/openapi.json`。三个兼容 POST 端点继续只允许 loopback。`/api/v1/score/health` 与全部 `/api/v1/score/jobs*` 路由只接受 ASGI `request.client.host` 为 IPv4 loopback 或 RFC1918；公网 IPv4、APIPA、IPv6、缺失/无法解析来源返回 403。策略不读取也不信任 `X-Forwarded-For`。根页面、demo 与 OpenAPI 不属于 Job API 来源策略。

Phase 4.2B1 新增异步 job 合同：

```text
POST /api/v1/score/jobs
PUT  /api/v1/score/jobs/{job_id}/csv
GET  /api/v1/score/jobs/{job_id}
GET  /api/v1/score/jobs/{job_id}/result
POST /api/v1/score/jobs/{job_id}/retry
```

创建请求原样保存严格七字段 `task.json`，CSV 通过 `request.stream()` 分块写入并同时校验 128 MiB 限额、SHA-256、固定 86 列 header 与首尾 `log_seq`。完整输入从 `operations/staging/uploads` 原子提升到 `sessions/<id>/input` 后才入队。队列上限 8，单 worker 调用 dedicated job scorer；完整输出在 `operations/staging/scoring` 完成后原子发布到 `sessions/<id>/scoring`。状态持久化在 `sessions/<id>/runtime/job_state.json`，使用同目录临时文件、flush/fsync 和 `os.replace`；服务重启只扫描现存 session，恢复 accepted/scoring，保留 uploading/succeeded/failed。

Job API 通过 Task Registry 解析 `task.json.task_name`，当前正式任务数为 5。有效任务会分派到 `drink_water` 或 `lateral_transfer` profile；未知任务按 dedicated scorer 的正式错误合同返回，不回退到旧 DTW Session 评分。

`GET .../result` 只在 succeeded 时投影 `rehab_score_result_v1`，不包含 actions、轨迹采样、DTW comparisons、curve_data、PNG、Base64 或二进制，序列化上限 64 KiB。完整版始终保留在 `sessions/<id>/scoring/full_result.json`。

当前版本没有 Token、Bearer 或其他鉴权；`0.0.0.0` 只改变监听范围，Job API 的应用层来源限制也不能替代 Windows Firewall，因此仍只适合可信私有局域网原型，不是公网安全方案。

health 只检查服务身份和关键资产，不执行评分。当前 health 依赖五任务 registry、正式喝水模板与正式横向取放模板，不依赖旧 Task-12 DTW profile 资产。fatigue 只是兼容状态，不是验证过的患者疲劳推断；BRS 没有实现推断模型，因此两者在 health 中均为不可用。

## Task directory

目录名必须匹配 `YYYYMMDD_TASKNNN`，包含一个 `task.json` 和其声明的 `DATA_SEQ<digits>.csv`。`task.json` 必须且只能有以下七字段：

```text
task_name, csv_file, start_time, end_time,
start_log_seq, end_log_seq, end_reason
```

开始/结束时间必须为非空 ISO-8601，日志序号必须为整数，CSV 首尾 `log_seq` 必须与 manifest 一致。`end_time=""`、`end_log_seq=0` 的未完成任务会被拒绝，不能正常评分。未显式指定 `output_dir` 时，dedicated scorer 结果写入 job 目录的 `result\`。

正式模板由评分资产 resolver 定位，Task Registry 仍按源码位置解析，二者都不依赖调用者 cwd。job 路径只接受 `^[0-9]{8}_TASK[0-9]{3}$`，上传文件名只接受 `^DATA_SEQ[0-9]{10}\.csv$`。代码仓库不保存患者输入、结果或评分资产副本。测试源码位于 `tests\`，使用仓库外临时目录与 mock scoring adapter 覆盖 API、状态机、恢复、安全边界和 compact result。
