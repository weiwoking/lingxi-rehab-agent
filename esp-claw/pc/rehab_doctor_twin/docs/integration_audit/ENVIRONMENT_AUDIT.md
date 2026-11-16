# Environment Audit

审计时间：2026-07-23

## 当前本机环境

| 项 | 结果 | 状态 |
| --- | --- | --- |
| ESP-Claw 根目录 | `C:\ec\esp-claw` | 存在 |
| ESP-IDF 主工程 | `C:\ec\esp-claw\application\edge_agent` | 存在 |
| PC 视觉服务源码 | `C:\ec\esp-claw\pc\vision_interpreter` | 存在；已恢复可启动 |
| 历史视觉迁移目录 | `E:\wulianwang\vision_pc_migration\vision_interpreter` | 存在，仅作为来源核验 |
| PC 评分服务源码 | `C:\ec\esp-claw\pc\rehab_score_service` | 存在；已恢复可启动 |
| 历史评分迁移目录 | `E:\wulianwang\score_pc_migration\rehab_score_system` | 存在，仅作为来源核验 |
| 医生端 PC UI | `C:\ev\rehab_doctor_twin` | 存在 |
| `where python` | `C:\Users\ZDragon\scoop\apps\python313\current\python.exe`; `C:\Users\ZDragon\AppData\Local\Programs\Python\Python39\python.exe`; WindowsApps stub | 可用，但默认是 3.13 |
| `where conda` | 未找到 | PATH 未配置 conda |
| `py -0p` | 3.9 at `C:\Users\ZDragon\AppData\Local\Programs\Python\Python39\python.exe`; 3.12 at `E:\rengongzhineng\Anaconda\install\python.exe` | 3.12 是 Anaconda Python |
| Conda 可执行文件 | `E:\rengongzhineng\Anaconda\install\Scripts\conda.exe` | 存在但不在 PATH |
| ESP-IDF | 已发现 `C:\Espressif\tools\Microsoft.v6.0.PowerShell_profile.ps1`，导出后 `IDF_PATH=E:\esp\.espressif\v6.0\esp-idf`，ESP-IDF v6.0 | 已恢复；P4 固件 build 通过 |
| 医生端前端 | Node `v24.18.0`, npm `11.16.0`, pnpm `11.9.0`, `frontend\node_modules` 存在 | 可 typecheck |
| 医生端后端 | `backend\.venv` 存在，Python 3.13.12，核心 imports 通过 | 可启动 |
| 8000/8020/5173 等常用端口 | 最终复查无 listen 结果 | 空闲，无残留服务 |

## 视觉启动器实际调用链

`Start-VisionPC.cmd`
-> `powershell.exe -NoProfile -ExecutionPolicy Bypass -File Start-VisionPC.ps1`
-> `Start-VisionPC.ps1`
-> 推导 `$repoRoot = <脚本目录>\..\..`
-> 固定 `$projectRoot = $repoRoot\pc\vision_interpreter`
-> 固定 `$condaExe = C:\ProgramData\anaconda3\Scripts\conda.exe`
-> 固定 `$envRoot = C:\ec\_conda_envs\vision_interpreter`
-> 固定 `$python = $envRoot\python.exe`
-> 工作目录 `$projectRoot`
-> 启动 `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1`
-> 本机 health `http://127.0.0.1:8000/api/v1/vision/health`
-> OpenAPI 检查 `/api/v1/person-follow/detect`
-> 热点地址 health `http://<hotspot-ip>:8000/api/v1/vision/health`
-> P4 发现只读调用 `/api/camera/status` 与 `/api/tof/status`

日志与状态写入 `%LOCALAPPDATA%\ESP-Claw\vision_launcher`。

## Conda 是否真正必需

视觉服务没有 `environment.yml` 或 `conda-lock`。权威依赖文件是 `C:\ec\esp-claw\pc\vision_interpreter\requirements.txt`。配置文件指定 `device: cpu`，源码使用 `ultralytics`, `torch`, `opencv-python-headless`, `fastapi`, `uvicorn`, `numpy`, `requests`，没有发现 CUDA/cuDNN 必需配置。因此从设计上看 Conda 不是接口或算法的必需项。

但本机用 Python 3.12 普通 `.venv` 安装依赖后，`pip check` 虽通过，`import torch` 触发 `c10.dll` 初始化失败。为了按现有工程设计启动且不改业务代码，本机最终采用 Conda prefix `C:\ec\_conda_envs\vision_interpreter` 恢复。结论：Conda 不是视觉服务的设计必需项，但在当前机器上是稳定恢复的实际必要方案。

评分服务 README 声明 Python `3.10.x` 与独立 Conda prefix `C:\ec\_conda_envs\rehab_score`，并提供 `requirements-lock.txt`。本机没有现成 `C:\ec\_conda_envs\rehab_score\python.exe`，但存在可用 Conda 可执行文件 `E:\rengongzhineng\Anaconda\install\Scripts\conda.exe`。评分服务是否必须 Conda主要来自现有运行合同，而不是算法本身；为了零回归，优先沿用其 Python 3.10 隔离环境要求。

## 所需 Python 版本

| 模块 | 版本依据 | 结论 |
| --- | --- | --- |
| PC 视觉服务 | `requirements.txt` 未显式锁 Python；依赖含 ultralytics/torch/opencv；配置为 CPU | 本机使用 Conda Python 3.10.20 成功恢复；普通 Python 3.12 `.venv` 不稳定 |
| PC 评分服务 | `pc\rehab_score_service\README.md` 与 launcher 检查 | Python 3.10.x |
| rehab_doctor_twin 后端 | 已有 `.venv` 实测 Python 3.13.12 imports OK | 当前环境可用 |
| ESP-Claw 固件 | ESP-IDF 工具链决定 | 本机使用 ESP-IDF v6.0 PowerShell profile 恢复 |

## 依赖权威来源

| 模块 | 权威来源 | 备注 |
| --- | --- | --- |
| PC 视觉服务 | `pc\vision_interpreter\requirements.txt` | 与历史迁移目录同名文件大小一致；无 lock |
| PC 评分服务 | `pc\rehab_score_service\requirements-lock.txt` | README 声明为 Windows Python 3.10 复现基线 |
| rehab_doctor_twin 后端 | `backend\requirements.txt` | 已安装在 `backend\.venv` |
| rehab_doctor_twin 前端 | `frontend\pnpm-lock.yaml` + `package.json` | `pnpm run typecheck` 通过 |
| ESP-Claw 固件 | ESP-IDF v6.0 + board manager + `sdkconfig` | `idf.py bmgr -c ./boards -b esp32_p4_function_ev` 与 `idf.py build` 通过 |

## 已存在与缺失的依赖

| 模块 | 已存在 | 缺失或阻塞 |
| --- | --- | --- |
| PC 视觉服务 | 源码、配置、YOLO 模型、身份模型、`C:\ec\_conda_envs\vision_interpreter` | Windows 热点/P4 现场网络未验证 |
| PC 评分服务 | 源码、`requirements-lock.txt`、`runtime_data`、模板和 DTW 资产、`C:\ec\_conda_envs\rehab_score` | 无环境阻塞；真实评分需正式任务目录 |
| rehab_doctor_twin | 后端 `.venv`、前端 `node_modules`、pnpm | 无环境阻塞；已做构建和短时 HTTP 验证，未做人工浏览器全流程验收 |
| ESP-Claw 固件 | `application\edge_agent\build`、`sdkconfig`、`managed_components` 存在；`build\edge_agent.bin` 已生成 | 无固件构建环境阻塞；flash/monitor 未执行 |

## 模型与配置文件状态

| 资源 | 路径 | 状态 |
| --- | --- | --- |
| 视觉配置 | `pc\vision_interpreter\config\vision_config.json` | 存在 |
| YOLO 检测模型 | `pc\vision_interpreter\models\yolo11n.pt` | 存在 |
| 身份分类 PT | `pc\vision_interpreter\models\identity\best_zxl_identity.pt` | 存在 |
| 身份分类 ONNX | `pc\vision_interpreter\models\identity\best_zxl_identity.onnx` | 存在，可选 |
| 身份分类基础模型 | `pc\vision_interpreter\models\identity\yolo11n-cls.pt` | 存在 |
| 评分健康模板 | `pc\rehab_score_service\template\MHH_healthy_template.json` | 存在 |
| Task 12 DTW 模板目录 | `pc\rehab_score_service\data\segmented_trials\fused_20260526_205718` | 存在且包含 CSV |
| 医生端人体模型 | `frontend/public/models/body.glb` | 由医生端 health 暴露，源码允许缺失并显示 pending |

## 硬编码路径与用途

| 文件:行 | 当前值 | 用途 | 电脑相关 | 建议 |
| --- | --- | --- | --- | --- |
| `tools\vision_launcher\Start-VisionPC.ps1:829` | `C:\ProgramData\anaconda3\Scripts\conda.exe` | Conda 存在性校验 | 是 | 改为可选发现或移除硬依赖 |
| `tools\vision_launcher\Start-VisionPC.ps1:830` | `C:\ec\_conda_envs\vision_interpreter` | 视觉 Python 环境 | 是 | 优先项目 `.venv`，支持 `VISION_PYTHON`/Conda 后备 |
| `tools\vision_launcher\README.md:28-30` | 固定 Conda、env、工程路径 | 使用文档 | 是 | 改为自动发现说明 |
| `tools\score_launcher\*.ps1` | `C:\ec\_conda_envs\rehab_score\python.exe` | 评分 Python 默认路径 | 是 | 本轮可通过创建该 env 恢复；后续可改为自动发现 |
| `pc\rehab_score_service\README.md:16-19` | `C:\ProgramData\anaconda3` 示例 | 历史安装说明 | 是 | 文档说明本机实际 Conda 路径 |
| `rehab_doctor_twin\frontend\vite.config.ts` | `127.0.0.1:8000`, `5173` | 本地 dev proxy | 本地配置 | 当前按设计保留 |

## 服务端口

| 服务 | 地址 | 健康检查 |
| --- | --- | --- |
| PC 视觉服务 | `0.0.0.0:8000`，本机探测 `127.0.0.1:8000` | `GET /api/v1/vision/health` |
| PC 评分服务 | `0.0.0.0:8020`，本机探测 `127.0.0.1:8020` | `GET /api/v1/score/health` |
| rehab_doctor_twin 后端 | `127.0.0.1:8000` | `GET /health` |
| rehab_doctor_twin 前端 | `127.0.0.1:5173` | Vite 页面 |

注意：视觉服务和医生端后端默认都使用 8000，不能同时占用同一端口；医生端前端通过 Vite proxy 访问医生端后端的 8000。

## 最终阻塞点

1. Windows 移动热点未开启，因此未验证 P4 通过 `192.168.137.1` 访问 PC 服务。
2. P4 扫描 PC 服务依赖 `C:\ec\_deltar_p4_pipeline` 和独立 deltar 环境，未纳入本轮恢复。
3. 视觉服务和医生端后端端口同为 8000，当前按分时启动验证；后续整合需要明确端口编排或 Adapter。
4. ESP-Claw 已完成 P4 固件本机构建，但未执行 `idf.py flash monitor`，未做真实板端启动验证。

## 已执行修复方式

1. 修改视觉启动器环境发现层，支持 `VISION_PYTHON`、项目 `.venv`、Conda prefix/name、PATH fallback，不再强制 `C:\ProgramData\anaconda3`。
2. 用本机已有 Conda 创建 `C:\ec\_conda_envs\vision_interpreter`，安装视觉依赖并验证 imports。
3. 用本机已有 Conda 创建 `C:\ec\_conda_envs\rehab_score`，安装评分锁定依赖并验证 health/smoke。
4. 医生端复用既有后端 `.venv` 与前端 `node_modules`，完成 typecheck/build/短时启动验证。
5. 发现本机已有 ESP-IDF v6.0 PowerShell 环境，未安装新工具链、未修改系统 PATH；导出后完成 P4 board manager 和固件 build。
6. 为适配 ESP-IDF v6，本轮仅调整构建兼容层：rehab 组件 CMake 依赖名从旧 `json` 改为 `espressif__cjson`，评分桥 SHA-256 调用改用公开 `mbedtls/md.h` 接口，board manager 的 DSI 生成按当前 ESP-IDF 头文件能力输出字段。
