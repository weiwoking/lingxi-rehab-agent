[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$componentRoot = Split-Path -Parent $PSScriptRoot
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) 'espclaw_rehab_fatigue_host_test'
$binary = Join-Path $tempRoot 'test_rehab_fatigue_rules.exe'
$persistenceBinary = Join-Path $tempRoot 'test_rehab_fatigue_persistence.exe'
$compilerBin = 'C:\msys64\ucrt64\bin'
$originalProcessPath = $env:PATH

New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
$gccArgs = @(
    '-std=c11',
    '-Wall',
    '-Wextra',
    '-Werror',
    '-I',
    (Join-Path $componentRoot 'src'),
    (Join-Path $componentRoot 'src\rehab_fatigue_rules.c'),
    (Join-Path $PSScriptRoot 'test_rehab_fatigue_rules.c'),
    '-o',
    $binary
)
try {
    if (Test-Path -LiteralPath $compilerBin) {
        $env:PATH = "$compilerBin;$originalProcessPath"
    }
    & gcc @gccArgs
    if ($LASTEXITCODE -ne 0) {
        throw "gcc failed with exit code $LASTEXITCODE"
    }
    & $binary
    if ($LASTEXITCODE -ne 0) {
        throw "host test failed with exit code $LASTEXITCODE"
    }
    $appRoot = Split-Path -Parent (Split-Path -Parent $componentRoot)
    $cjsonRoot = Join-Path $appRoot 'managed_components\espressif__cjson\cJSON'
    $persistenceArgs = @(
        '-std=gnu17',
        '-Wall',
        '-Wextra',
        '-Werror',
        '-include',
        (Join-Path $PSScriptRoot 'host_stubs\host_compat.h'),
        '-I',
        (Join-Path $PSScriptRoot 'host_stubs'),
        '-I',
        (Join-Path $componentRoot 'include'),
        '-I',
        (Join-Path $componentRoot 'src'),
        '-I',
        $cjsonRoot,
        (Join-Path $componentRoot 'src\rehab_fatigue.c'),
        (Join-Path $componentRoot 'src\rehab_fatigue_rules.c'),
        (Join-Path $cjsonRoot 'cJSON.c'),
        (Join-Path $PSScriptRoot 'test_rehab_fatigue_persistence.c'),
        '-o',
        $persistenceBinary
    )
    & gcc @persistenceArgs
    if ($LASTEXITCODE -ne 0) {
        throw "persistence gcc failed with exit code $LASTEXITCODE"
    }
    $caseRoot = Join-Path $tempRoot ([guid]::NewGuid().ToString('N'))
    $normalRoot = Join-Path $caseRoot 'normal'
    $rolloverRoot = Join-Path $caseRoot 'rollover'
    $untrustedRoot = Join-Path $caseRoot 'untrusted'
    $untrustedExistingDayRoot = Join-Path $caseRoot 'untrusted_existing_day'
    $corruptRoot = Join-Path $caseRoot 'corrupt'
    $unwritableRoot = Join-Path $caseRoot 'unwritable'
    New-Item -ItemType Directory -Path $caseRoot -Force | Out-Null
    foreach ($case in @(
        @('create', $normalRoot),
        @('duplicate', $normalRoot),
        @('rollover', $rolloverRoot),
        @('untrusted', $untrustedRoot),
        @('untrusted_existing_day', $untrustedExistingDayRoot),
        @('corrupt', $corruptRoot),
        @('unwritable', $unwritableRoot)
    )) {
        & $persistenceBinary $case[0] $case[1]
        if ($LASTEXITCODE -ne 0) {
            throw "persistence case $($case[0]) failed with exit code $LASTEXITCODE"
        }
    }
    & python (Join-Path $PSScriptRoot 'verify_rehab_fatigue_static.py')
    if ($LASTEXITCODE -ne 0) {
        throw "static test failed with exit code $LASTEXITCODE"
    }
} finally {
    $env:PATH = $originalProcessPath
}
