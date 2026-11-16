param(
    [Parameter(Mandatory = $true)]
    [string]$Script
)

$ErrorActionPreference = "Stop"
$python = "C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe"
& $python (Join-Path $PSScriptRoot "test_result_contract.py")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& $python (Join-Path $PSScriptRoot "test_start_plan_allocation.py")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& $python $Script
exit $LASTEXITCODE
