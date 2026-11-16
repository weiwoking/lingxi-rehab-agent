$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Python = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not $env:DOCTOR_SOURCE_MODE) { $env:DOCTOR_SOURCE_MODE = "device" }
if (-not $env:DOCTOR_BACKEND_PORT) { $env:DOCTOR_BACKEND_PORT = "8001" }
if (-not $env:VISION_BASE_URL) { $env:VISION_BASE_URL = "http://127.0.0.1:8000" }
if (-not $env:SCORE_BASE_URL) { $env:SCORE_BASE_URL = "http://127.0.0.1:8020" }
if (-not $env:P4_RUNTIME_POLL_INTERVAL_SECONDS) { $env:P4_RUNTIME_POLL_INTERVAL_SECONDS = "0.1" }
if (-not $env:P4_DEVICE_OFFLINE_TIMEOUT_SECONDS) { $env:P4_DEVICE_OFFLINE_TIMEOUT_SECONDS = "2" }
if (-not $env:P4_BASE_URL) {
  $P4Cache = Join-Path $env:LOCALAPPDATA "ESP-Claw\vision_launcher\last_p4_ip.txt"
  if (Test-Path -LiteralPath $P4Cache -PathType Leaf) {
    $P4IP = (Get-Content -Raw -LiteralPath $P4Cache).Trim()
    if ($P4IP -match '^\d{1,3}(\.\d{1,3}){3}$') {
      $env:P4_BASE_URL = "http://$P4IP"
    }
  }
}
if (-not (Test-Path $Python)) {
  $Python = "python"
}
Push-Location $Backend
& $Python run.py
Pop-Location
