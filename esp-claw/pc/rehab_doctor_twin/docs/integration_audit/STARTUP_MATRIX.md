# Startup Matrix

审计时间：2026-07-23

| 模块 | 工程路径 | 启动命令 | Python/Node 环境 | 配置文件 | 模型/资产 | 端口 | 健康检查 | 当前状态 | 阻塞原因 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ESP-Claw 固件构建 | `C:\ec\esp-claw\application\edge_agent` | 导入 `C:\Espressif\tools\Microsoft.v6.0.PowerShell_profile.ps1`; 设置 `PYTHONIOENCODING=utf-8`; `idf.py bmgr -c ./boards -b esp32_p4_function_ev`; `idf.py build` | ESP-IDF v6.0 Python/toolchain | `sdkconfig`, `sdkconfig.defaults*`, `boards\...` | SYSTEM/DATA FATFS, board overlays | 串口/设备相关 | `idf.py build` | 本机可构建 | 已生成 `build\edge_agent.bin`；未 flash/monitor |
| PC 视觉服务 | `C:\ec\esp-claw\pc\vision_interpreter` | `tools\vision_launcher\Start-VisionPC.cmd` -> `Start-VisionPC.ps1` -> `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1` | `C:\ec\_conda_envs\vision_interpreter\python.exe`, Python 3.10.20；启动器也支持 `VISION_PYTHON` 和项目 `.venv` | `config\vision_config.json`, optional `VISION_CONFIG_PATH` | `models\yolo11n.pt`, `models\identity\best_zxl_identity.pt`, `models\identity\yolo11n-cls.pt` | 8000 | `GET /api/v1/vision/health`; OpenAPI `/api/v1/person-follow/detect` | 本机可启动 | 普通 `.venv` 曾遇到 `torch`/`c10.dll` 初始化失败；本机采用 Conda prefix 恢复 |
| PC 评分服务 | `C:\ec\esp-claw\pc\rehab_score_service` | `tools\score_launcher\Start-ScorePC.cmd` -> `Start-ScorePC.ps1` -> `python -m uvicorn cloud_interface.score_server:app --host 0.0.0.0 --port 8020 --workers 1` | `C:\ec\_conda_envs\rehab_score\python.exe`, Python 3.10.20 | `%LOCALAPPDATA%\ESP-Claw\score_launcher\score_launcher.local.json` 可选 | `template\MHH_healthy_template.json`; Task 12 DTW CSV | 8020 | `GET /api/v1/score/health`; OpenAPI 冻结路由 | 本机可启动 | Smoke 通过；真实评分需传入正式任务目录 |
| rehab_doctor_twin 后端 | `C:\ev\rehab_doctor_twin\backend` | `scripts\start_backend.ps1` -> `.venv\Scripts\python.exe run.py` | `backend\.venv` Python 3.13.12 | `backend\app\config.py`, `demo\*.json`, `backend\data\*.json` | 可选 `frontend/public/models/body.glb` | 8000 | `GET /health` | 可启动 | 与视觉服务共享 8000，不能同时启动 |
| rehab_doctor_twin 前端 | `C:\ev\rehab_doctor_twin\frontend` | `pnpm run dev` 或 `scripts\start_frontend.ps1` | Node 24.18.0, pnpm 11.9.0, `node_modules` 存在 | `vite.config.ts`, `package.json`, `pnpm-lock.yaml` | Three.js, 可选 GLB/DRACO | 5173 | Vite 页面 `http://127.0.0.1:5173` | 可验证 | 无环境阻塞；依赖版本使用 `latest` 但 lock 已存在 |
| P4 扫描 PC 服务 | `C:\ec\_deltar_p4_pipeline` | `C:\ec\_conda_envs\vision_interpreter\python.exe C:\ec\_deltar_p4_pipeline\scripts\run_scan_service.py --host 192.168.137.1 --port 8030` | 另有测试脚本引用 `C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe` | pipeline profiles | P4 scan assets | 8030 | `/api/v1/scan/jobs` 语义 | 未恢复 | 不是本轮 PC 视觉 8000 服务；外部 pipeline 路径/环境未验证 |
| 阿里云客户端/同步服务 | 视觉服务标题与评分代码中存在 Aliyun/cloud 命名；ESP 通用 LLM 预设支持 DashScope；医生端 README 明确未接阿里云 | 无康复专用本地启动器 | 未发现康复专用环境 | 未发现本机康复云密钥配置 | 不适用 | 不适用 | 不适用 | 康复闭环未接入 | 当前仅通用 LLM/配置能力和历史命名；本轮不登录、不访问、不修改阿里云 |

## 端口互斥说明

`PC 视觉服务` 与 `rehab_doctor_twin 后端` 都默认监听 8000。两者是不同进程，不能在默认配置下同时运行。阶段 1 只验证各自可单独启动，不改 API 或 UI 行为；后续接入规划再决定是否通过外层适配器或启动编排解决端口共存。
