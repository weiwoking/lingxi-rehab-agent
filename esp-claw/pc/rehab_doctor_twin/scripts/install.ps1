$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"

function Require-Command($Name) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "$Name 未找到。请安装或将其加入当前终端 PATH 后重试。"
  }
}

Require-Command python
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  Write-Warning "node 未在 PATH 中找到。如使用 Codex 内置 Node，可手动将其 bin 目录加入当前终端 PATH。"
} else {
  node --version
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  Write-Warning "npm 未在 PATH 中找到。前端依赖安装将被跳过。"
} else {
  npm --version
}

Push-Location $Backend
if (-not (Test-Path ".venv")) {
  python -m venv .venv
}
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
Pop-Location

if (Get-Command npm -ErrorAction SilentlyContinue) {
  Push-Location $Frontend
  npm install
  Pop-Location
}

Write-Host "安装流程完成。"

