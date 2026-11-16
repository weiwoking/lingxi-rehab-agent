$ErrorActionPreference = 'Stop'

$pipelineRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = 'C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
$validatedRoot = 'C:\ec\_deltar_scan_service_data\validated'
$verificationRoot = Join-Path $pipelineRoot 'verification_runs'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Formal DELTAR interpreter is missing: $python"
}

$latest = Get-ChildItem -LiteralPath $validatedRoot -Directory |
    Where-Object {
        (Test-Path -LiteralPath (Join-Path $_.FullName 'scan.json') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $_.FullName '.validation_receipt.json') -PathType Leaf)
    } |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
if ($null -eq $latest) {
    throw "No receipted validated scan exists under $validatedRoot"
}

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$output = Join-Path $verificationRoot ("{0}_{1}" -f $latest.Name, $stamp)
$env:PYTHONPATH = Join-Path $pipelineRoot 'src'

Write-Host "Formal Geometry D scan: $($latest.Name)"
Write-Host "Output: $output"
& $python -m deltar_p4_pipeline.cli scan-run `
    --scan-dir $latest.FullName `
    --output-dir $output `
    --profile-bundle (Join-Path $pipelineRoot 'profiles') `
    --device cuda:0 `
    --require-validation-receipt `
    --defer-visualization `
    --stage7d-horizontal-planes `
    --stage7d-profile (Join-Path $pipelineRoot 'profiles\stage7d_horizontal_plane_profile.json')
if ($LASTEXITCODE -ne 0) {
    throw "Formal Geometry D replay failed with exit code $LASTEXITCODE"
}

$geometryPath = Join-Path $output 'stage8b_all_object_geometry.v1.json'
$packagePath = Join-Path $output 'stage8b4a_p4_decision_package.v1.json'
$geometry = Get-Content -Raw -LiteralPath $geometryPath | ConvertFrom-Json
$package = Get-Content -Raw -LiteralPath $packagePath | ConvertFrom-Json

Write-Host "`nPer-object formal Geometry v2:"
$geometry.frames.objects |
    Where-Object geometry_status -eq 'success' |
    Select-Object logical_object_id, class_name,
        @{Name='support_mm'; Expression={$_.geometry.support_height_mm}},
        @{Name='height_mm'; Expression={$_.geometry.object_height_mm}},
        @{Name='top_mm'; Expression={$_.geometry.top_height_above_floor_mm}},
        geometry_confidence |
    Format-Table -AutoSize

Write-Host "Canonical P4 package objects: $($package.objects.Count)"
Write-Host "SUCCESS: Stage2 + DELTAR-D + Stage3A gravity + all-object Geometry v2 completed."
Write-Host "Send back on failure: the console transcript and $output\logs\pipeline_progress.jsonl"
