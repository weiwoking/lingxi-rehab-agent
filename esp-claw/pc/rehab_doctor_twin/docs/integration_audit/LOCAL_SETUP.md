# Local Setup

适用日期：2026-07-23

本文件用于在一台新 Windows 电脑上恢复 ESP-Claw、PC 视觉服务、PC 评分服务和医生端 UI 的本地环境。不要在文档或日志中写入真实密钥、密码或真实患者数据。

## 1. 前置软件

| 工具 | 用途 |
| --- | --- |
| Git | 获取仓库 |
| Python 3.10 | PC 评分服务、可用于视觉服务 |
| Conda 或等价隔离环境 | 推荐用于本机视觉/评分原生依赖隔离 |
| Node.js + pnpm | 医生端前端 |
| ESP-IDF | 固件构建 |

本机已验证 Conda 位于：

```powershell
E:\rengongzhineng\Anaconda\install\Scripts\conda.exe
```

如果新电脑上的 Conda 不在该路径，使用 `VISION_CONDA_EXE` 指定，或把命令里的路径替换成本机实际路径；不要修改全局 PATH 作为第一选择。

## 2. PC 视觉服务

首选尝试项目 `.venv`：

```powershell
cd C:\ec\esp-claw
.\tools\vision_launcher\Bootstrap-VisionPC.ps1
```

如果 `.venv` 中 `torch`/OpenCV native DLL 导入失败，改用 Conda prefix：

```powershell
& 'E:\rengongzhineng\Anaconda\install\Scripts\conda.exe' create --prefix 'C:\ec\_conda_envs\vision_interpreter' python=3.10 pip -y
& 'C:\ec\_conda_envs\vision_interpreter\python.exe' -m pip install -r 'C:\ec\esp-claw\pc\vision_interpreter\requirements.txt'
& 'C:\ec\_conda_envs\vision_interpreter\python.exe' -m pip check
```

验证核心依赖：

```powershell
$code = @'
import fastapi, uvicorn, torch, ultralytics, cv2, numpy, requests
print(1)
'@
& 'C:\ec\_conda_envs\vision_interpreter\python.exe' -c $code
```

模型与配置应存在：

```text
C:\ec\esp-claw\pc\vision_interpreter\config\vision_config.json
C:\ec\esp-claw\pc\vision_interpreter\models\yolo11n.pt
C:\ec\esp-claw\pc\vision_interpreter\models\identity\best_zxl_identity.pt
C:\ec\esp-claw\pc\vision_interpreter\models\identity\yolo11n-cls.pt
```

启动：

```powershell
C:\ec\esp-claw\tools\vision_launcher\Start-VisionPC.cmd
```

有界验证：

```powershell
cd C:\ec\esp-claw\tools\vision_launcher
.\Start-VisionPC.cmd -ValidationMode -HotspotWaitSeconds 1 -HealthTimeoutSeconds 120 -P4ScanBudgetSeconds 1
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/vision/health
```

停止：在启动器窗口按 `Ctrl+C`。ValidationMode 会自动清理本次启动的 Uvicorn。

## 3. PC 评分服务

创建 Python 3.10 Conda prefix：

```powershell
& 'E:\rengongzhineng\Anaconda\install\Scripts\conda.exe' create --prefix 'C:\ec\_conda_envs\rehab_score' python=3.10 pip -y
& 'C:\ec\_conda_envs\rehab_score\python.exe' -m pip install -r 'C:\ec\esp-claw\pc\rehab_score_service\requirements-lock.txt'
& 'C:\ec\_conda_envs\rehab_score\python.exe' -m pip check
```

环境检查：

```powershell
C:\ec\esp-claw\tools\score_launcher\Check-ScorePC.ps1
```

启动、Smoke、停止：

```powershell
C:\ec\esp-claw\tools\score_launcher\Start-ScorePC.ps1
C:\ec\esp-claw\tools\score_launcher\Smoke-ScorePC.ps1
C:\ec\esp-claw\tools\score_launcher\Stop-ScorePC.ps1
```

评分服务默认监听 `0.0.0.0:8020`，本机 health：

```powershell
Invoke-RestMethod http://127.0.0.1:8020/api/v1/score/health
```

冻结接口仍是：

```text
POST /reset_fatigue_state
POST /set_baseline_from_rows
POST /score_trial
```

## 4. 医生端 PC UI

安装：

```powershell
cd C:\ev\rehab_doctor_twin
.\scripts\install.ps1
```

后端启动：

```powershell
.\scripts\start_backend.ps1
```

后端健康：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

前端启动：

```powershell
.\scripts\start_frontend.ps1
```

打开：

```text
http://127.0.0.1:5173
```

验证：

```powershell
cd C:\ev\rehab_doctor_twin\frontend
pnpm run typecheck
pnpm run build
```

## 5. ESP-Claw 固件

本机已验证 ESP-IDF v6.0 安装在 `C:\Espressif`/`E:\esp\.espressif\v6.0\esp-idf`。在 Windows PowerShell 中先导入 Espressif profile，并强制 Python 输出 UTF-8，避免 board manager 在 GBK 控制台输出 Unicode 时失败：

```powershell
. 'C:\Espressif\tools\Microsoft.v6.0.PowerShell_profile.ps1'
$env:PYTHONIOENCODING = 'utf-8'
cd C:\ec\esp-claw\application\edge_agent
idf.py bmgr -c ./boards -b esp32_p4_function_ev
idf.py build
```

本轮已验证 `idf.py build` 通过并生成 `C:\ec\esp-claw\application\edge_agent\build\edge_agent.bin`。如需刷机，再连接 ESP32-P4 后运行：

```powershell
idf.py -p <PORT> flash monitor
```

不要在不确认 ESP-IDF 安装位置的情况下修改系统 PATH。

## 6. 常见错误

| 现象 | 处理 |
| --- | --- |
| `Conda 不存在：C:\ProgramData\anaconda3...` | 使用修复后的启动器；或设置 `VISION_CONDA_EXE`/`VISION_PYTHON` |
| `torch c10.dll` 初始化失败 | 使用 Conda prefix，并确认环境内有 VC runtime |
| 视觉 health OK 但热点失败 | 打开 Windows 移动热点；这不是视觉服务环境失败 |
| P4 未发现 | 确认 P4 连接同一热点，并且 `/api/camera/status`、`/api/tof/status` 可访问 |
| 8000 端口占用 | 视觉服务和医生端后端默认都用 8000；分时启动，或后续通过外层适配器规划端口 |
| 评分 health degraded | 检查 `template\MHH_healthy_template.json` 和 Task 12 DTW CSV 是否存在 |
| ESP-IDF build 命令不存在 | 导入 `C:\Espressif\tools\Microsoft.v6.0.PowerShell_profile.ps1`，或在 ESP-IDF 终端执行 |
| `idf.py bmgr` 输出 Unicode 报错 | 设置 `$env:PYTHONIOENCODING='utf-8'` 后重试 |
