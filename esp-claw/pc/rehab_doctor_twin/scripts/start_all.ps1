$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendDir = Join-Path $ProjectRoot "frontend"

$BackendPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
$BackendRunFile = Join-Path $BackendDir "run.py"
$FrontendModules = Join-Path $FrontendDir "node_modules"

Write-Host "Project root: $ProjectRoot"

if (-not (Test-Path $BackendDir)) {
    throw "Backend directory was not found: $BackendDir"
}

if (-not (Test-Path $FrontendDir)) {
    throw "Frontend directory was not found: $FrontendDir"
}

if (-not (Test-Path $BackendPython)) {
    throw "Backend virtual environment was not found. Run scripts\install.ps1 first."
}

if (-not (Test-Path $BackendRunFile)) {
    throw "Backend entry file was not found: $BackendRunFile"
}

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js is not available in PATH. Install Node.js LTS or add node.exe to PATH."
}

$PackageManager = $null

if (Get-Command pnpm -ErrorAction SilentlyContinue) {
    $PackageManager = "pnpm"
}
elseif (Get-Command npm -ErrorAction SilentlyContinue) {
    $PackageManager = "npm"
}
else {
    throw "Neither pnpm nor npm is available in PATH."
}

if (-not (Test-Path $FrontendModules)) {
    throw "Frontend dependencies were not found. Run scripts\install.ps1 first."
}

$BackendCommand = @"
Set-Location -LiteralPath '$BackendDir'
if (-not `$env:DOCTOR_SOURCE_MODE) { `$env:DOCTOR_SOURCE_MODE = 'device' }
if (-not `$env:DOCTOR_BACKEND_PORT) { `$env:DOCTOR_BACKEND_PORT = '8001' }
if (-not `$env:VISION_BASE_URL) { `$env:VISION_BASE_URL = 'http://127.0.0.1:8000' }
if (-not `$env:SCORE_BASE_URL) { `$env:SCORE_BASE_URL = 'http://127.0.0.1:8020' }
if (-not `$env:P4_RUNTIME_POLL_INTERVAL_SECONDS) { `$env:P4_RUNTIME_POLL_INTERVAL_SECONDS = '0.1' }
if (-not `$env:P4_DEVICE_OFFLINE_TIMEOUT_SECONDS) { `$env:P4_DEVICE_OFFLINE_TIMEOUT_SECONDS = '2' }
if (-not `$env:P4_BASE_URL) {
    `$P4Cache = Join-Path `$env:LOCALAPPDATA 'ESP-Claw\vision_launcher\last_p4_ip.txt'
    if (Test-Path -LiteralPath `$P4Cache -PathType Leaf) {
        `$P4IP = (Get-Content -Raw -LiteralPath `$P4Cache).Trim()
        if (`$P4IP -match '^\d{1,3}(\.\d{1,3}){3}$') { `$env:P4_BASE_URL = "http://`$P4IP" }
    }
}
& '$BackendPython' 'run.py'
"@

$FrontendCommand = @"
Set-Location -LiteralPath '$FrontendDir'
if (-not `$env:DOCTOR_BACKEND_PORT) { `$env:DOCTOR_BACKEND_PORT = '8001' }
if (-not `$env:VITE_CLINICAL_DATA_MODE) { `$env:VITE_CLINICAL_DATA_MODE = 'live' }
& '$PackageManager' run dev -- --host 127.0.0.1
"@

Write-Host "Starting backend..."
Start-Process powershell.exe -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    $BackendCommand
)

Start-Sleep -Seconds 2

Write-Host "Starting frontend..."
Start-Process powershell.exe -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    $FrontendCommand
)

Write-Host ""
Write-Host "Backend and frontend startup commands were launched."
Write-Host "Open: http://127.0.0.1:5173"
