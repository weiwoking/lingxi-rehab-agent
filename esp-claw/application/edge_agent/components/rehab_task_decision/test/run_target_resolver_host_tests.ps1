[CmdletBinding()]
param(
    [string]$PacketPath = "C:\ec\_deltar_scan_service_data\results\scan_20260731_171257_000\stage8b4a_p4_decision_package.v1.json"
)

$ErrorActionPreference = "Stop"
$component = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$idfCjson = "C:\esp\v5.5.4\frameworks\esp-idf-v5.5.4\components\json\cJSON"
$compiler = "C:\msys64\ucrt64\bin\gcc.exe"
if (-not (Test-Path -LiteralPath $compiler) -or
    -not (Test-Path -LiteralPath $PacketPath) -or
    -not (Test-Path -LiteralPath $idfCjson)) {
    throw "Target resolver host-test prerequisite is unavailable"
}

$binary = Join-Path $env:TEMP (
    "rehab_task_target_resolver_{0}.exe" -f ([guid]::NewGuid().ToString("N")))
$originalPath = $env:PATH
try {
    $env:PATH = "$(Split-Path -Parent $compiler);$originalPath"
    & $compiler `
        -std=gnu11 -Wall -Wextra -Werror `
        -I (Join-Path $PSScriptRoot "target_resolver_host_stubs") `
        -I (Join-Path $component "src") `
        -I $idfCjson `
        (Join-Path $PSScriptRoot "test_task_target_resolver.c") `
        (Join-Path $component "src\rehab_task_decision_policy.c") `
        (Join-Path $component "src\rehab_task_target_resolver.c") `
        (Join-Path $idfCjson "cJSON.c") `
        -o $binary -lm
    if ($LASTEXITCODE -ne 0) {
        throw "Target resolver host-test compilation failed"
    }
    & $binary $PacketPath
    if ($LASTEXITCODE -ne 0) {
        throw "Target resolver host tests failed"
    }
} finally {
    $env:PATH = $originalPath
    if (Test-Path -LiteralPath $binary) {
        Remove-Item -LiteralPath $binary -Force
    }
}
