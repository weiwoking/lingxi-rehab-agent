@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0P4_Rehab_TTS_Launcher.ps1"
endlocal
