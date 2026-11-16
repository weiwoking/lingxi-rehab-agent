[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$environmentRoot = Split-Path -Parent $PSCommandPath
$venvPython = Join-Path $environmentRoot "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Isolated YOLO Python is missing: $venvPython"
}

$verifier = Join-Path $environmentRoot "verify_env.py"
& $venvPython $verifier
if ($LASTEXITCODE -ne 0) {
    throw "CUDA YOLO environment verification failed with exit code $LASTEXITCODE"
}
