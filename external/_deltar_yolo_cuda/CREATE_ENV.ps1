[CmdletBinding()]
param([switch]$Plan)

$ErrorActionPreference = "Stop"
$environmentRoot = Split-Path -Parent $PSCommandPath
$externalRoot = Split-Path -Parent $environmentRoot
$sourcePython = Join-Path $externalRoot '_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
$venvRoot = Join-Path $environmentRoot "venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$requirements = Join-Path $environmentRoot "requirements.lock.txt"

if ($Plan) {
    Write-Output "PLAN scan_yolo: $sourcePython -> $venvRoot"
    return
}

if (-not (Test-Path -LiteralPath $sourcePython -PathType Leaf)) {
    throw "Pinned Python source is missing: $sourcePython"
}
if (Test-Path -LiteralPath $venvRoot) {
    throw "Refusing to overwrite existing isolated environment: $venvRoot"
}

& $sourcePython -m venv --copies $venvRoot
if ($LASTEXITCODE -ne 0) {
    throw "venv creation failed with exit code $LASTEXITCODE"
}
& $venvPython -m pip install --disable-pip-version-check --requirement $requirements
if ($LASTEXITCODE -ne 0) {
    throw "dependency installation failed with exit code $LASTEXITCODE"
}
& (Join-Path $environmentRoot "VERIFY_ENV.ps1")
