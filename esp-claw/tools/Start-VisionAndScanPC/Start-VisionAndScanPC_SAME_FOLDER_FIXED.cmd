@echo off
setlocal
title ESP-Claw Vision and Scan PC Launcher

set "LAUNCHER=%~dp0start_vision_and_scan_services.ps1"

if not exist "%LAUNCHER%" (
    echo.
    echo [ERROR] Unified PowerShell launcher was not found:
    echo %LAUNCHER%
    echo.
    pause
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] Launcher exited with code %EXIT_CODE%.
    echo Logs: %~dp0..\..\..\external\ESP-Claw_localappdata\vision_scan_launcher
    echo.
    pause
)

exit /b %EXIT_CODE%
