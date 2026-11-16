$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Frontend = Join-Path $Root "frontend"
if (-not $env:DOCTOR_BACKEND_PORT) { $env:DOCTOR_BACKEND_PORT = "8001" }
if (-not $env:VITE_CLINICAL_DATA_MODE) { $env:VITE_CLINICAL_DATA_MODE = "live" }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  throw "npm was not found. Install Node.js or add npm to PATH."
}
Push-Location $Frontend
npm run dev
Pop-Location
