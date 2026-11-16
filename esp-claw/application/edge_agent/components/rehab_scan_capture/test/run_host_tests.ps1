param(
    [Parameter(Mandatory = $true)]
    [string]$ReportDirectory
)

$ErrorActionPreference = 'Stop'
$python = 'C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
& $python (Join-Path $ReportDirectory 'SIMULATION_TEST.py')
exit $LASTEXITCODE
