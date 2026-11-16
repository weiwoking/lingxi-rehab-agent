$ErrorActionPreference = "Stop"
$python = "C:\ec\_conda_envs\rehab_score\python.exe"
$root = "C:\ec\esp-claw\pc\rehab_score_service"
$assetRoot = if ($env:REHAB_SCORING_ASSETS_ROOT) { $env:REHAB_SCORING_ASSETS_ROOT } else { "C:\ec\_rehab_scoring_assets" }
$outputRoot = Join-Path $assetRoot ("derived\lateral_transfer_v1\calibration_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
Set-Location $root
New-Item -ItemType Directory -Force $outputRoot | Out-Null

function Invoke-Step {
  param([scriptblock]$Command, [string]$FailureCode)
  & $Command
  if ($LASTEXITCODE -ne 0) {
    Write-Host "$FailureCode=$LASTEXITCODE"
    throw "$FailureCode=$LASTEXITCODE"
  }
}

$segOut = Join-Path $outputRoot "segmentation"
$templateOut = Join-Path $outputRoot "healthy_template_candidate"
$lfOut = Join-Path $outputRoot "lf"
$tremorOut = Join-Path $outputRoot "tremor"

Invoke-Step { & $python "tools\lateral_transfer_segmentation_analysis.py" --output-dir $segOut } "SEGMENTATION_ANALYSIS_FAILED"

$segJson = Join-Path $segOut "lateral_transfer_segmentation_analysis.json"
$seg = Get-Content -LiteralPath $segJson -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Host "RAW_POOL_SEGMENTATION_READY=False"
Write-Host "RAW_POOL_MATCHED_SESSION_COUNT=$($seg.matched_session_count)"
Write-Host "RAW_POOL_MISMATCH_SESSION_COUNT=$($seg.mismatch_session_count)"
Write-Host "RAW_POOL_ACTUAL_VALID_REP_TOTAL=$($seg.actual_valid_rep_total)"
Write-Host "RAW_POOL_EXPECTED_VALID_REP_TOTAL=61"
Write-Host "RAW_POOL_BLOCKS_CALIBRATION=False"
Write-Host "APPROVED_CALIBRATION_MANIFEST_CANDIDATE=$($seg.approved_calibration_manifest_candidate)"
Write-Host "APPROVED_CALIBRATION_MANIFEST_REQUIRED=True"

Invoke-Step { & $python "tools\build_lateral_transfer_healthy_template_v1.py" --output-dir $templateOut } "HEALTHY_TEMPLATE_ANALYSIS_FAILED"
Invoke-Step { & $python "tools\lateral_transfer_lf_calibration_v1.py" --output-dir $lfOut } "LF_CALIBRATION_FAILED"
Invoke-Step { & $python "tools\lateral_transfer_tremor_calibration_v1.py" --output-dir $tremorOut } "TREMOR_CALIBRATION_FAILED"

$report = Join-Path $outputRoot "CALIBRATION_REPORT.md"
@"
# Lateral Transfer Calibration Summary

- expected_session_count: 33
- expected_rep_count: 61
- segmentation_analysis: $segOut
- healthy_template_candidate: $templateOut
- LF calibration: $lfOut
- TREMOR calibration: $tremorOut

Review recommendation JSON files and approve a profile explicitly through:

tools\09_APPLY_APPROVED_LATERAL_PROFILE.ps1

Do not modify drink_water profile files.
"@ | Set-Content -LiteralPath $report -Encoding UTF8

Write-Host "CALIBRATION_OUTPUT=$outputRoot"
Write-Host "SEGMENTATION_ANALYSIS=$segOut"
Write-Host "LF_CALIBRATION=$lfOut"
Write-Host "TREMOR_CALIBRATION=$tremorOut"
Write-Host "RECOMMENDATION_JSON=$lfOut\lateral_transfer_lf_threshold_recommendation.json;$tremorOut\lateral_transfer_tremor_threshold_recommendation.json"
Write-Host "expected_session_count=33"
Write-Host "expected_rep_count=61"
Write-Host "LF_positive_count=6"
Write-Host "LF_negative_count=55"
Write-Host "TREMOR_positive_count=5"
Write-Host "TREMOR_negative_count=56"
