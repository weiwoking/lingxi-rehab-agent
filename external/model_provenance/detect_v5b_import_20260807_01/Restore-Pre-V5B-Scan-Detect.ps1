[CmdletBinding()]
param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$profileRoot = 'C:\ec\_deltar_p4_pipeline\profiles'
$manifestPath = Join-Path $profileRoot 'model_manifest.json'
$runtimePath = Join-Path $profileRoot 'stage8b1_yolo_runtime_profile.json'
$algorithmPath = Join-Path $profileRoot 'algorithm_profile.json'
$prior = [ordered]@{
    name = 'YOLO11n COCO80 volleyball Detect v3 Epoch 10 experimental'
    path = 'C:\ec\_deltar_p4_pipeline\models\rehab_scan_yolo11n_coco80_volleyball_detect_v3_epoch10_exp.pt'
    sha256 = 'e73128f36a3a7e01b0b8bb42cb1387194257470c594fd5b566cd045328f7919b'
    upstream_commit = $null
    runtime = 'Ultralytics 8.4.93 CUDA FP32 experimental Epoch 10'
    confidence = 0.20
}

function Read-Json([string]$Path) {
    Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json
}

function Write-AtomicJson([string]$Path, [object]$Value) {
    $temporary = "$Path.$PID.tmp"
    try {
        $json = $Value | ConvertTo-Json -Depth 100
        [IO.File]::WriteAllText($temporary, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
        [IO.File]::Replace($temporary, $Path, $null)
    } finally {
        if (Test-Path -LiteralPath $temporary) {
            [IO.File]::Delete($temporary)
        }
    }
}

$manifest = Read-Json $manifestPath
$runtime = Read-Json $runtimePath
$algorithm = Read-Json $algorithmPath
$detect = @($manifest.models | Where-Object task -eq 'detect')
$segment = @($manifest.models | Where-Object task -eq 'segment')
if ($detect.Count -ne 1 -or $segment.Count -ne 1) {
    throw 'Expected exactly one Detect and one Segment manifest entry.'
}
$segmentIdentity = "$($segment[0].path)|$($segment[0].sha256)"

Write-Host "Mode: $(if ($Apply) {'APPLY'} else {'DRY-RUN'})"
Write-Host "Current Detect: $($detect[0].path) [$($detect[0].sha256)]"
Write-Host "Restore Detect: $($prior.path) [$($prior.sha256)]"
Write-Host "Restore conf: $($prior.confidence)"
Write-Host 'v5-B model will not be deleted; Segment and Person-Follow are not modified.'

if (-not $Apply) {
    Write-Host 'DRY_RUN_PASS: no files changed.'
    exit 0
}

$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in 8000, 8030 })
if ($listeners.Count -gt 0) {
    throw 'Refusing restore while port 8000 or 8030 is listening.'
}

$detect[0].name = $prior.name
$detect[0].path = $prior.path
$detect[0].sha256 = $prior.sha256
$detect[0].upstream_commit = $prior.upstream_commit
$detect[0].runtime = $prior.runtime
$runtime.models.detect_sha256 = $prior.sha256
$algorithm.yolo_confidence = $prior.confidence

Write-AtomicJson $manifestPath $manifest
Write-AtomicJson $runtimePath $runtime
Write-AtomicJson $algorithmPath $algorithm

$verifiedManifest = Read-Json $manifestPath
$verifiedRuntime = Read-Json $runtimePath
$verifiedAlgorithm = Read-Json $algorithmPath
$verifiedDetect = @($verifiedManifest.models | Where-Object task -eq 'detect')
$verifiedSegment = @($verifiedManifest.models | Where-Object task -eq 'segment')
if ($verifiedDetect.Count -ne 1 -or
    $verifiedDetect[0].path -ne $prior.path -or
    $verifiedDetect[0].sha256 -ne $prior.sha256 -or
    $verifiedRuntime.models.detect_sha256 -ne $prior.sha256 -or
    [double]$verifiedAlgorithm.yolo_confidence -ne [double]$prior.confidence -or
    "$($verifiedSegment[0].path)|$($verifiedSegment[0].sha256)" -ne $segmentIdentity) {
    throw 'Restore verification failed.'
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $prior.path).Hash.ToLowerInvariant() -ne $prior.sha256) {
    throw 'Restored Detect model hash verification failed.'
}
Write-Host 'RESTORE_APPLY_PASS'
