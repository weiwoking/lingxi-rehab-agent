# Windows 本地启动

在已准备好项目 Python 环境的 PowerShell 中运行：

```powershell
Set-Location C:\ec\esp-claw\pc\vision_interpreter
.\run_windows.ps1
```

如需使用另一份配置：

```powershell
.\run_windows.ps1 -ConfigPath C:\path\to\vision_config.json
```

服务监听 `0.0.0.0:8000`。另开 PowerShell 检查健康状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/vision/health
```

在运行服务的窗口按 `Ctrl+C` 停止。
