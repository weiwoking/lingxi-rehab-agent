@echo off
rem Copy this file to launcher_config.cmd in the same directory when local paths need overrides.
rem Do not commit machine-specific launcher_config.cmd files.

rem Optional: score service Python. If omitted, START_ESP_CLAW_PC.cmd tries:
rem 1. %%REHAB_SCORE_PYTHON%%
rem 2. pc\rehab_score_service\.venv\Scripts\python.exe
rem 3. C:\ec\_conda_envs\rehab_score\python.exe
rem set "SCORE_PYTHON=C:\ec\_conda_envs\rehab_score\python.exe"

rem Optional: doctor backend Python. If omitted, the doctor launcher uses:
rem pc\rehab_doctor_twin\backend\.venv\Scripts\python.exe
rem set "DOCTOR_PYTHON=C:\path\to\doctor\.venv\Scripts\python.exe"
