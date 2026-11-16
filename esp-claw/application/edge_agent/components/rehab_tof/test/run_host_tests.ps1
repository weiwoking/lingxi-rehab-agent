[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$componentRoot = Split-Path -Parent $PSScriptRoot
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) 'espclaw_rehab_tof_host_test'
$binary = Join-Path $tempRoot 'test_rehab_tof_frame_core.exe'
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
    (Join-Path $componentRoot 'src\rehab_tof_frame_core.c'),
    (Join-Path $componentRoot 'src\rehab_tof_startup_core.c'),
    (Join-Path $PSScriptRoot 'test_rehab_tof_frame_core.c'),
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
} finally {
    $env:PATH = $originalProcessPath
}

$provider = Get-Content -LiteralPath (Join-Path $componentRoot 'src\rehab_tof_provider.c') -Raw
$header = Get-Content -LiteralPath (Join-Path $componentRoot 'include\rehab_tof.h') -Raw
$platform = Get-Content -LiteralPath (Join-Path $componentRoot 'vl53l5cx_uld\platform.h') -Raw
$platformSource = Get-Content -LiteralPath (Join-Path $componentRoot 'src\rehab_tof_platform.c') -Raw
$frameCore = Get-Content -LiteralPath (Join-Path $componentRoot 'src\rehab_tof_frame_core.c') -Raw
$startupCore = Get-Content -LiteralPath (Join-Path $componentRoot 'src\rehab_tof_startup_core.c') -Raw
$uldHeader = Get-Content -LiteralPath (Join-Path $componentRoot 'vl53l5cx_uld\vl53l5cx_api.h') -Raw

foreach ($signature in @(
    'rehab_tof_init(',
    'rehab_tof_start(',
    'rehab_tof_snapshot_acquire(',
    'rehab_tof_window_acquire(',
    'rehab_tof_get_status(',
    'rehab_tof_stop(',
    'rehab_tof_deinit('
)) {
    if (-not $header.Contains($signature)) {
        throw "legacy source contract missing: $signature"
    }
}
if ($platform.Contains('VL53L5CX_DISABLE_RANGE_SIGMA_MM')) {
    throw 'range sigma is still disabled'
}
if ($platform.Contains('VL53L5CX_DISABLE_NB_TARGET_DETECTED') -or
    $platform.Contains('VL53L5CX_DISABLE_TARGET_STATUS') -or
    $platform.Contains('VL53L5CX_DISABLE_DISTANCE_MM')) {
    throw 'required ResultsData output is disabled'
}
foreach ($extendedField in @(
    'distance_mm[REHAB_TOF_ZONE_COUNT]',
    'range_sigma_mm[REHAB_TOF_ZONE_COUNT]',
    'target_status[REHAB_TOF_ZONE_COUNT]',
    'nb_target_detected[REHAB_TOF_ZONE_COUNT]'
)) {
    if (-not $header.Contains($extendedField)) {
        throw "extended frame field missing: $extendedField"
    }
}
if (-not $provider.Contains('VL53L5CX_RANGING_MODE_CONTINUOUS')) {
    throw 'provider does not explicitly select continuous mode'
}
if ([regex]::Matches($provider, '\bvl53l5cx_start_ranging\s*\(').Count -ne 1) {
    throw 'stream start must have exactly one implementation call site'
}
if (-not $provider.Contains('#define REHAB_TOF_STARTUP_DISCARD_FRAMES   0U')) {
    throw 'official baseline must not discard a startup frame without hardware evidence'
}
if (-not $provider.Contains('REHAB_TOF_DIAG_LOG_PERIOD_US') -or
    -not $provider.Contains('INT64_C(10000000)')) {
    throw 'normal diagnostics must use the named 10-second period'
}
if (-not $header.Contains('.startup_max_attempts = 3') -or
    -not $header.Contains('.startup_retry_delay_ms = 50')) {
    throw 'bounded startup retry defaults changed'
}
if ($provider.Contains('REHAB_TOF_INITIAL_FRAME_LOG_COUNT') -or
    $provider -match 'sequence\s*%') {
    throw 'normal diagnostics must not use frame-count gating'
}
if ([regex]::Matches($provider, 'rehab_tof_diag_log_gate_due\s*\(').Count -ne 1 -or
    $provider -notmatch '(?s)if\s*\(diagnostic_log_due\)\s*\{.*TOF_FRAME seq=.*TOF_EXT_SUMMARY seq=') {
    throw 'TOF_FRAME and TOF_EXT_SUMMARY must share one diagnostic gate'
}
if ([regex]::Matches($provider, '\bxTaskCreate\s*\(').Count -ne 1) {
    throw 'provider must have exactly one worker creation call site'
}
if (-not $provider.Contains('status_code == 5U || status_code == 6U || status_code == 9U')) {
    throw 'legacy usable status semantics changed'
}
if (-not $provider.Contains('TOF_START_RETRY') -or
    -not $provider.Contains('raw_err=%s') -or
    -not $provider.Contains('mapped_error=%s')) {
    throw 'structured startup retry diagnostics missing'
}
if (-not $platform.Contains('first_io_error') -or
    -not $platformSource.Contains('vl53l5cx_platform_begin_operation') -or
    -not $platformSource.Contains('vl53l5cx_platform_operation_error')) {
    throw 'first raw transport error preservation missing'
}
if (-not $provider.Contains('bus_owned') -or
    $provider.Contains('i2c_master_get_bus_handle')) {
    throw 'component-owned bus contract changed'
}
if (-not $provider.Contains('rehab_tof_frame_summarize(&s_tof.collector.latest')) {
    throw 'summary must use the exact payload already published by the producer'
}
if (-not $uldHeader.Contains('#define VL53L5CX_API_REVISION')) {
    throw 'vendored ULD revision marker missing'
}
if ($provider -match '\bmalloc\s*\(' -or $provider -match '\bfree\s*\(') {
    throw 'provider must not allocate or free per frame'
}
if ($frameCore -match '\bmalloc\s*\(' -or $frameCore -match '\bfree\s*\(' -or
    $startupCore -match '\bmalloc\s*\(' -or $startupCore -match '\bfree\s*\(') {
    throw 'host-testable frame/startup cores must not allocate dynamically'
}
if (-not $provider.Contains('rehab_tof_frame_collector_stop(&s_tof.collector)') -or
    -not $provider.Contains('xSemaphoreGive(s_tof.stopped)')) {
    throw 'worker shutdown/stale invalidation contract missing'
}
Write-Host 'rehab_tof static contracts: all tests passed'
