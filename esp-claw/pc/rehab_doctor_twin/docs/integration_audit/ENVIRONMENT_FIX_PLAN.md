# Environment Fix Plan

审计时间：2026-07-23

本计划只处理环境、路径、启动器、依赖安装和文档。禁止修改视觉算法、评分算法、康复逻辑、UI 页面行为、HTTP API、WebSocket 消息格式和阿里云服务。

## 1. PC 视觉启动器去固定 Conda 依赖

| 项 | 内容 |
| --- | --- |
| 根因 | `Start-VisionPC.ps1` 硬编码 `C:\ProgramData\anaconda3\Scripts\conda.exe` 和 `C:\ec\_conda_envs\vision_interpreter`，本机前者不存在且后者不存在 |
| 要修改的文件 | `C:\ec\esp-claw\tools\vision_launcher\Start-VisionPC.ps1`; `C:\ec\esp-claw\tools\vision_launcher\README.md` |
| 不允许修改的文件 | `pc\vision_interpreter\app\*.py` 视觉业务代码；`config\vision_config.json` 业务配置；模型文件；接口 schema |
| 修复方案 | 增加环境发现：`VISION_PYTHON` -> `pc\vision_interpreter\.venv\Scripts\python.exe` -> `VISION_CONDA_EXE`/`VISION_CONDA_ENV` -> 常见 Conda 位置；Conda 仅可选。支持 `ESP_CLAW_ROOT`, `VISION_PC_ROOT`, `VISION_HOST`, `VISION_PORT`, `VISION_LOG_DIR` |
| 风险 | 修改启动器可能影响旧环境；通过保留显式环境变量和默认项目路径降低风险 |
| 验证命令 | `C:\ec\esp-claw\tools\vision_launcher\Start-VisionPC.cmd -SelfTest`; `...\Start-VisionPC.cmd -ValidationMode -HotspotWaitSeconds 1 -HealthTimeoutSeconds 60 -P4ScanBudgetSeconds 1` |
| 回滚方法 | 从备份目录恢复 `Start-VisionPC.ps1` 和 `README.md` |

## 2. PC 视觉项目 `.venv`

| 项 | 内容 |
| --- | --- |
| 根因 | 当前可见 Python 环境缺 fastapi/uvicorn/torch/ultralytics/cv2 等视觉依赖 |
| 要修改的文件 | 新增 `C:\ec\esp-claw\pc\vision_interpreter\.venv`；可新增启动层 bootstrap 脚本 |
| 不允许修改的文件 | 视觉推理、身份分类、ToF 融合、person-follow 业务逻辑 |
| 修复方案 | 使用本机 Python 3.12 (`E:\rengongzhineng\Anaconda\install\python.exe`) 创建项目 `.venv`，执行 `python -m pip install -r requirements.txt` |
| 风险 | `ultralytics` 会安装 PyTorch CPU 相关依赖，耗时较长；若网络或 wheel 不可用则停止并记录真实错误 |
| 验证命令 | `.venv\Scripts\python.exe -c "import fastapi, uvicorn, torch, ultralytics, cv2, numpy"` |
| 回滚方法 | 删除 `pc\vision_interpreter\.venv` 并恢复启动器备份 |

## 3. PC 评分服务 Python 3.10 环境

| 项 | 内容 |
| --- | --- |
| 根因 | 评分 launcher 默认 `C:\ec\_conda_envs\rehab_score\python.exe` 不存在，并且服务检查要求 Python 3.10.x |
| 要修改的文件 | 新增 `C:\ec\_conda_envs\rehab_score`; 可能写入 `%LOCALAPPDATA%\ESP-Claw\score_launcher` 日志/状态 |
| 不允许修改的文件 | `cloud_interface\cloud_score_api.py`; `scoring\*.py`; `feature_extractors\*.py`; 冻结接口 |
| 修复方案 | 使用已存在 Conda `E:\rengongzhineng\Anaconda\install\Scripts\conda.exe` 创建 prefix `C:\ec\_conda_envs\rehab_score`，安装 `requirements-lock.txt`，运行 `Check-ScorePC.ps1` |
| 风险 | Conda 网络或包源失败；若失败不切换随机最新组合 |
| 验证命令 | `tools\score_launcher\Check-ScorePC.ps1`; `Start-ScorePC.ps1`; `Smoke-ScorePC.ps1`; `Stop-ScorePC.ps1` |
| 回滚方法 | 停止评分服务，删除 `C:\ec\_conda_envs\rehab_score`，清理 `%LOCALAPPDATA%\ESP-Claw\score_launcher` |

## 4. rehab_doctor_twin 最小启动验证

| 项 | 内容 |
| --- | --- |
| 根因 | 需要确认迁移后医生端后端、前端在本机按 README 可运行 |
| 要修改的文件 | 无计划修改 |
| 不允许修改的文件 | `frontend\src\*`; `backend\app\*`; WebSocket 合同；页面行为 |
| 修复方案 | 复用现有 `backend\.venv` 与 `frontend\node_modules`，执行后端 imports、前端 typecheck/build，短时启动 health |
| 风险 | 默认后端 8000 与视觉服务 8000 冲突；分时验证 |
| 验证命令 | `backend\.venv\Scripts\python.exe -c "import app.main"`; `pnpm run typecheck`; `pnpm run build`; 后端短时 `GET /health` |
| 回滚方法 | 无业务文件修改；若产生 dev 日志，仅保留审计记录 |

## 5. ESP-Claw 固件构建环境

| 项 | 内容 |
| --- | --- |
| 根因 | 普通 PowerShell 未导出 ESP-IDF；本机实际已有 ESP-IDF v6.0 profile；v6 还带来 `json` 组件名、MbedTLS 公开头文件、DSI 生成字段差异 |
| 要修改的文件 | 构建/启动兼容层：rehab 组件 `CMakeLists.txt`、`rehab_score_bridge_storage.c` 的哈希 API 调用、board manager DSI 生成脚本、`sdkconfig` 自动生成结果 |
| 不允许修改的文件 | 康复业务逻辑、评分规则、视觉算法、HTTP API、WebSocket、板卡业务定义 |
| 修复方案 | 不安装新 ESP-IDF、不改系统 PATH；导入本机 v6 profile，设置 `PYTHONIOENCODING=utf-8`，运行 P4 board manager 和 build；对 v6 编译失败点做最小构建兼容修复 |
| 风险 | `sdkconfig` 被 ESP-IDF v6 重新生成，diff 较大；属于工具链迁移产物，需要随 IDF 版本统一审阅 |
| 验证命令 | `idf.py bmgr -c ./boards -b esp32_p4_function_ev`; `idf.py build` |
| 回滚方法 | 恢复相关 CMake/哈希/board-manager 变更和 `sdkconfig`；删除或忽略 `build`/`build.stale_v55_20260723_2118` 生成目录 |
