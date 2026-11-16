# Environment Fix Report

修复时间：2026-07-23

## 原始问题

运行 `C:\ec\esp-claw\tools\vision_launcher\Start-VisionPC.cmd` 时，启动器在依赖检查前退出：

```text
[错误] Conda 不存在：C:\ProgramData\anaconda3\Scripts\conda.exe
```

## 根因

1. 视觉启动器硬编码 `C:\ProgramData\anaconda3\Scripts\conda.exe`，但本机 Conda 在 `E:\rengongzhineng\Anaconda\install\Scripts\conda.exe`，且不在 PATH。
2. 启动器硬编码 `C:\ec\_conda_envs\vision_interpreter`，本机原本没有该环境。
3. 普通项目 `.venv` 方案使用 Python 3.12 安装依赖后，`pip check` 通过，但 `torch` 导入时报 `c10.dll` 初始化失败。因此普通 venv 在本机不能稳定运行视觉服务。
4. 原启动器在启动 Uvicorn 前要求检测到 Windows 移动热点；热点未开启时会阻止本机服务 health 验证。

## 修改文件

| 文件 | 变更 |
| --- | --- |
| `C:\ec\esp-claw\tools\vision_launcher\Start-VisionPC.ps1` | 增加环境发现，去除固定 `C:\ProgramData\anaconda3` 强依赖；支持 `VISION_*` 环境变量；先启动本机服务并 health，再把热点/P4 作为外部条件 |
| `C:\ec\esp-claw\tools\vision_launcher\README.md` | 更新为项目 venv 优先、Conda 可选后备、本机恢复和诊断说明 |

## 新增文件

| 文件 | 用途 |
| --- | --- |
| `C:\ec\esp-claw\tools\vision_launcher\Bootstrap-VisionPC.ps1` | 创建视觉项目 `.venv` 并安装 `requirements.txt` 的 bootstrap 脚本 |
| `C:\ev\rehab_doctor_twin\docs\integration_audit\backup_20260723_202405\*` | 修改前备份 |

## 安装的依赖

### PC 视觉服务

创建 Conda prefix：

```powershell
& 'E:\rengongzhineng\Anaconda\install\Scripts\conda.exe' create --prefix 'C:\ec\_conda_envs\vision_interpreter' python=3.10 pip -y
& 'C:\ec\_conda_envs\vision_interpreter\python.exe' -m pip install -r 'C:\ec\esp-claw\pc\vision_interpreter\requirements.txt'
& 'C:\ec\_conda_envs\vision_interpreter\python.exe' -m pip check
```

核心安装结果：`fastapi 0.139.2`, `uvicorn 0.51.0`, `python-multipart 0.0.32`, `pydantic 2.13.4`, `numpy 2.2.6`, `opencv-python-headless 4.13.0.92`, `opencv-python 5.0.0.93`, `ultralytics 8.4.104`, `torch 2.13.0`, `torchvision 0.28.0`, `requests 2.34.2`。

项目 `.venv` 曾按 `requirements.txt` 安装成功，但 `torch` 导入失败，随后已清理，不作为可用环境。

### PC 评分服务

创建 Conda prefix：

```powershell
& 'E:\rengongzhineng\Anaconda\install\Scripts\conda.exe' create --prefix 'C:\ec\_conda_envs\rehab_score' python=3.10 pip -y
& 'C:\ec\_conda_envs\rehab_score\python.exe' -m pip install -r 'C:\ec\esp-claw\pc\rehab_score_service\requirements-lock.txt'
& 'C:\ec\_conda_envs\rehab_score\python.exe' -m pip check
```

锁定直接依赖安装结果：`fastapi 0.139.0`, `uvicorn 0.51.0`, `numpy 2.2.6`, `pandas 2.3.3`, `matplotlib 3.10.9`, `pydantic 2.13.4`, `requests 2.34.2`, `httpx 0.28.1`。

## 使用的 Python 环境

| 模块 | Python |
| --- | --- |
| PC 视觉服务 | `C:\ec\_conda_envs\vision_interpreter\python.exe`, Python 3.10.20 |
| PC 评分服务 | `C:\ec\_conda_envs\rehab_score\python.exe`, Python 3.10.20 |
| rehab_doctor_twin 后端 | `C:\ev\rehab_doctor_twin\backend\.venv\Scripts\python.exe`, Python 3.13.12 |
| ESP-Claw 固件 | ESP-IDF v6.0 profile `C:\Espressif\tools\Microsoft.v6.0.PowerShell_profile.ps1`，导出后 `IDF_PATH=E:\esp\.espressif\v6.0\esp-idf` |

## 启动命令

| 模块 | 命令 |
| --- | --- |
| PC 视觉服务 | `C:\ec\esp-claw\tools\vision_launcher\Start-VisionPC.cmd` |
| PC 视觉服务有界验证 | `Start-VisionPC.ps1 -ValidationMode -HotspotWaitSeconds 1 -HealthTimeoutSeconds 120 -P4ScanBudgetSeconds 1` |
| PC 评分服务 | `C:\ec\esp-claw\tools\score_launcher\Start-ScorePC.ps1` |
| PC 评分 Smoke | `C:\ec\esp-claw\tools\score_launcher\Smoke-ScorePC.ps1` |
| PC 评分停止 | `C:\ec\esp-claw\tools\score_launcher\Stop-ScorePC.ps1` |
| 医生端后端 | `C:\ev\rehab_doctor_twin\scripts\start_backend.ps1` |
| 医生端前端 | `C:\ev\rehab_doctor_twin\scripts\start_frontend.ps1` |
| ESP-Claw P4 board manager | 导入 ESP-IDF v6 PowerShell profile 后：`idf.py bmgr -c ./boards -b esp32_p4_function_ev` |
| ESP-Claw P4 build | 导入 ESP-IDF v6 PowerShell profile 后：`idf.py build` |

## 健康检查结果

| 模块 | 结果 |
| --- | --- |
| 视觉启动器自检 | `Start-VisionPC.cmd -SelfTest` 通过 |
| 视觉服务有界启动 | Uvicorn 成功启动，`GET /api/v1/vision/health` 返回 `status=ok`, `model_ready=True` |
| 视觉 OpenAPI | `/openapi.json` 包含 `/api/v1/person-follow/detect` |
| 视觉热点/P4 | Windows 移动热点未检测到；已降级为外部网络条件，服务本机就绪 |
| 视觉停止 | ValidationMode 退出时清理本次 Uvicorn |
| 评分环境检查 | `Check-ScorePC.ps1` 通过，资产 ready |
| 评分服务启动 | `health=ready`, version `0.2.1` |
| 评分 Smoke | health、`GET /`、`GET /demo`、OpenAPI 与关键路由通过；未跑真实评分 |
| 评分停止 | launcher owned PID 正常停止 |
| 医生端后端 | `GET /health` 返回 `status=ok` |
| 医生端前端 | `pnpm run typecheck` 和 `pnpm run build` 通过；dev server 首页 HTTP 200 |
| ESP-Claw 固件 | `idf.py bmgr -c ./boards -b esp32_p4_function_ev` 通过；`idf.py build` 通过，生成 `C:\ec\esp-claw\application\edge_agent\build\edge_agent.bin`，app partition 剩余约 28% |

## ESP-IDF v6 兼容修复

| 问题 | 修复 |
| --- | --- |
| 普通 PowerShell 未导出 ESP-IDF | 使用本机已有 `C:\Espressif\tools\Microsoft.v6.0.PowerShell_profile.ps1`；未安装新工具链，未修改系统 PATH |
| `idf.py bmgr` 在 GBK 控制台输出 Unicode 时失败 | 构建 shell 设置 `PYTHONIOENCODING=utf-8` |
| ESP-IDF v6 下 rehab 组件依赖 `json` 解析失败 | 仅修改 CMake 构建依赖名为 `espressif__cjson` |
| MbedTLS 4 不再公开 `mbedtls/sha256.h` | 评分桥存储层改用公开 `mbedtls/md.h` 计算同一 SHA-256 hex |
| board manager 生成 DSI 字段与当前 IDF v6 头文件不匹配 | 生成脚本按已安装头文件能力决定是否输出 `clock_lane_force_hs` 字段 |
| 旧 `build` 目录含 IDF v5.5.4 CMake cache | 重命名为 `build.stale_v55_20260723_2118` 后重新生成 IDF v6 build |

## 未解决问题

1. Windows 移动热点未开启，因此没有验证热点地址 health、P4 自动发现和真实 P4 闭环。
2. P4 扫描 PC pipeline `C:\ec\_deltar_p4_pipeline`/`:8030` 未纳入本轮环境恢复。
3. 医生端后端和 PC 视觉服务默认都用 8000；本轮只做分时验证，未改变接口或页面行为。
4. ESP-Claw 已 build 通过，但未执行 flash/monitor，未做真实板端启动验证。
5. 未登录、访问或修改阿里云；阿里云职责仍待功能审计后评估。

## 回滚方式

1. 从 `C:\ev\rehab_doctor_twin\docs\integration_audit\backup_20260723_202405` 复制回 `Start-VisionPC.ps1` 和 `README.md`。
2. 删除 `C:\ec\esp-claw\tools\vision_launcher\Bootstrap-VisionPC.ps1`。
3. 停止相关服务后，可删除 `C:\ec\_conda_envs\vision_interpreter` 与 `C:\ec\_conda_envs\rehab_score`。
4. 清理 `%LOCALAPPDATA%\ESP-Claw\vision_launcher` 和 `%LOCALAPPDATA%\ESP-Claw\score_launcher` 中本轮运行日志。

## Git diff 摘要

本轮环境修复涉及：

```text
tools/vision_launcher/Start-VisionPC.ps1
tools/vision_launcher/README.md
tools/vision_launcher/Bootstrap-VisionPC.ps1
application/edge_agent/components/rehab_*/CMakeLists.txt
application/edge_agent/components/rehab_score_bridge/src/rehab_score_bridge_storage.c
application/edge_agent/managed_components/espressif__esp_board_manager/peripherals/periph_dsi/periph_dsi.py
application/edge_agent/sdkconfig
```

工作区中 `application/edge_agent/p4_scan_serial_*.log` 的 staged/AD 状态在本轮开始前已存在，不属于本轮修复内容。
