param(
    [string]$ConfigPath
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if ($ConfigPath) {
    $env:VISION_CONFIG_PATH = (Resolve-Path -LiteralPath $ConfigPath).Path
}

& python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
exit $LASTEXITCODE
