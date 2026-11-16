@echo off
setlocal EnableExtensions
chcp 65001 >nul
title ESP-Claw PC Scoring Stack

for %%I in ("%~dp0..\..") do set "ESP_CLAW_ROOT=%%~fI"
for %%I in ("%ESP_CLAW_ROOT%\..") do set "PROJECT_ROOT=%%~fI"
set "ESP_CLAW_PC_ROOT=%ESP_CLAW_ROOT%\pc"
set "DOCTOR_ROOT=%ESP_CLAW_PC_ROOT%\rehab_doctor_twin"
set "SCORE_ROOT=%ESP_CLAW_PC_ROOT%\rehab_score_service"
set "LAUNCH_SCRIPT=%DOCTOR_ROOT%\scripts\start_pc_only.ps1"
set "CONFIG_FILE=%~dp0launcher_config.cmd"

set "SCORE_PYTHON="
set "DOCTOR_PYTHON="
if exist "%CONFIG_FILE%" call "%CONFIG_FILE%"

if not defined SCORE_PYTHON if defined REHAB_SCORE_PYTHON set "SCORE_PYTHON=%REHAB_SCORE_PYTHON%"
if not defined SCORE_PYTHON if exist "%SCORE_ROOT%\.venv\Scripts\python.exe" set "SCORE_PYTHON=%SCORE_ROOT%\.venv\Scripts\python.exe"
if not defined SCORE_PYTHON if exist "%PROJECT_ROOT%\external\_conda_envs\rehab_score\python.exe" set "SCORE_PYTHON=%PROJECT_ROOT%\external\_conda_envs\rehab_score\python.exe"

if not exist "%LAUNCH_SCRIPT%" (
  echo Missing launcher: %LAUNCH_SCRIPT%
  goto fail
)
if not exist "%SCORE_ROOT%" (
  echo Missing score service: %SCORE_ROOT%
  goto fail
)
if not defined SCORE_PYTHON (
  echo SCORE_PYTHON is not configured.
  goto fail
)
if not exist "%SCORE_PYTHON%" (
  echo Score Python does not exist: %SCORE_PYTHON%
  goto fail
)

set "DEEPSEEK_AUTO_START=NO"
if defined DOCTOR_PYTHON goto launch_with_doctor_python

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LAUNCH_SCRIPT%" -DoctorRoot "%DOCTOR_ROOT%" -ScorePcRoot "%ESP_CLAW_PC_ROOT%" -ScorePythonPath "%SCORE_PYTHON%"
set "START_EXIT=%ERRORLEVEL%"
goto launch_done

:launch_with_doctor_python
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LAUNCH_SCRIPT%" -DoctorRoot "%DOCTOR_ROOT%" -ScorePcRoot "%ESP_CLAW_PC_ROOT%" -ScorePythonPath "%SCORE_PYTHON%" -DoctorPythonPath "%DOCTOR_PYTHON%"
set "START_EXIT=%ERRORLEVEL%"

:launch_done
if "%START_EXIT%"=="0" exit /b 0
echo.
echo ESP-Claw PC scoring stack failed to start. Exit code: %START_EXIT%
echo Review the run directory printed above.
pause
exit /b %START_EXIT%

:fail
echo.
echo Check launcher_config.cmd, Python environments, and frontend node_modules.
pause
exit /b 1
