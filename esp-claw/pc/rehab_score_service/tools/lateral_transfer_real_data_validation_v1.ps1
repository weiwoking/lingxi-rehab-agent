$ErrorActionPreference = "Stop"
$python = Join-Path $projectRoot 'external\_conda_envs\rehab_score\python.exe'
$root = Split-Path -Parent $PSScriptRoot
$assetRoot = if ($env:REHAB_SCORING_ASSETS_ROOT) { $env:REHAB_SCORING_ASSETS_ROOT } else { Join-Path $projectRoot 'external\_rehab_scoring_assets' }
$template = Join-Path $assetRoot "templates\lateral_transfer_healthy_template_v1.json"
$manifest = Join-Path $assetRoot "derived\lateral_transfer_v1\approved_calibration_manifest\lateral_transfer_approved_calibration_manifest.json"
$outputRoot = Join-Path $assetRoot ("derived\lateral_transfer_v1\approved_final_validation_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
$penaltyTolerance = 0.05
Set-Location $root
New-Item -ItemType Directory -Force $outputRoot | Out-Null

function Stop-Gate {
  param([string]$Message, [int]$Code = 1)
  Write-Host $Message
  throw "$Message code=$Code"
}

function Get-ApprovedSessionSpecs {
  param([string]$ManifestPath)
  if (!(Test-Path -LiteralPath $ManifestPath)) {
    Stop-Gate "APPROVED_CALIBRATION_MANIFEST_MISSING=$ManifestPath" 10
  }
  $data = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $items = @($data.sessions | Where-Object { $_.approved_for_calibration -eq $true -and $_.review_status -eq "approved" })
  if ($items.Count -eq 0) {
    Stop-Gate "APPROVED_CALIBRATION_MANIFEST_HAS_NO_APPROVED_SESSIONS" 11
  }
  return $items
}

function Get-ScoreFiles {
  param([string]$SessionOutput)
  $perRep = Join-Path $SessionOutput "per_rep_scores"
  return Get-ChildItem -LiteralPath $perRep -Filter "*_score.json" -File |
    Where-Object { $_.Name -notlike "*_score_metadata.json" } |
    Sort-Object Name
}

function ExpectedPenalty {
  param([bool]$HasLf, [bool]$HasTremor)
  $value = 0.0
  if ($HasLf) { $value += 10.0 }
  if ($HasTremor) { $value += 15.0 }
  return $value
}

function Write-ScoreStats {
  param([object[]]$Rows, [string]$Label, [string]$Prefix)
  $items = @($Rows | Where-Object { $_.expected_label -eq $Label } | Sort-Object final_score)
  Write-Host "$($Prefix)_COUNT=$($items.Count)"
  if ($items.Count -eq 0) {
    Write-Host "$($Prefix)_SCORE_MEAN="
    Write-Host "$($Prefix)_SCORE_MEDIAN="
    Write-Host "$($Prefix)_SCORE_MIN="
    Write-Host "$($Prefix)_SCORE_MAX="
    return
  }
  $scores = @($items | ForEach-Object { [double]$_.final_score })
  $mean = ($scores | Measure-Object -Average).Average
  if (($scores.Count % 2) -eq 1) {
    $median = $scores[[int][Math]::Floor($scores.Count / 2)]
  } else {
    $upper = [int]($scores.Count / 2)
    $median = ($scores[$upper - 1] + $scores[$upper]) / 2.0
  }
  Write-Host "$($Prefix)_SCORE_MEAN=$([Math]::Round($mean, 2))"
  Write-Host "$($Prefix)_SCORE_MEDIAN=$([Math]::Round($median, 2))"
  Write-Host "$($Prefix)_SCORE_MIN=$([Math]::Round($scores[0], 2))"
  Write-Host "$($Prefix)_SCORE_MAX=$([Math]::Round($scores[$scores.Count - 1], 2))"
}

if (!(Test-Path -LiteralPath $template)) {
  Stop-Gate "LATERAL_TEMPLATE_MISSING=$template" 12
}

$sessionSpecs = @(Get-ApprovedSessionSpecs -ManifestPath $manifest)
$approvedExpectedRepTotal = 0
foreach ($spec in $sessionSpecs) {
  $approvedExpectedRepTotal += [int]$spec.expected_valid_rep_count
  if (@($spec.expected_labels).Count -ne [int]$spec.expected_valid_rep_count) {
    Stop-Gate "APPROVED_SESSION_LABEL_COUNT_MISMATCH:$($spec.session_id)" 13
  }
}

$rows = @()
foreach ($spec in $sessionSpecs) {
  $sessionDir = Join-Path $assetRoot ("calibration_sessions\" + $spec.session_id)

  if (!(Test-Path -LiteralPath $sessionDir)) {
    Stop-Gate "SOURCE_SESSION_DIR_MISSING=$($spec.session_id)" 14
  }

  $rawSource = Join-Path $sessionDir "raw_session.csv"

  if (Test-Path -LiteralPath $rawSource) {
    $source = $rawSource
  }
  else {
    $csvFiles = @(
      Get-ChildItem -LiteralPath $sessionDir -Filter "*.csv" -File |
      Sort-Object Name
    )

    if ($csvFiles.Count -eq 0) {
      Stop-Gate "SOURCE_SESSION_MISSING=$($spec.session_id)" 14
    }

    $source = $csvFiles[0].FullName
  }

  Write-Host "SOURCE_CSV=$($spec.session_id):$source"
  $sessionOut = Join-Path $outputRoot $spec.session_id
  & $python "tools\lateral_transfer_session_scoring_v1.py" --source-session $source --template $template --output-root $outputRoot --session-id $spec.session_id --overwrite
  if ($LASTEXITCODE -ne 0) { Stop-Gate "SESSION_SCORE_FAILED=$($spec.session_id)" $LASTEXITCODE }
  $scoreFiles = @(Get-ScoreFiles -SessionOutput $sessionOut)
  if ($scoreFiles.Count -ne [int]$spec.expected_valid_rep_count) {
    Write-Host "REP_COUNT_MISMATCH"
    Write-Host "session=$($spec.session_id)"
    Write-Host "expected=$($spec.expected_valid_rep_count)"
    Write-Host "actual=$($scoreFiles.Count)"
    Stop-Gate "REP_COUNT_MISMATCH:$($spec.session_id)" 15
  }
  $labels = @($spec.expected_labels)
  for ($i = 0; $i -lt $scoreFiles.Count; $i++) {
    $score = Get-Content -LiteralPath $scoreFiles[$i].FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    $types = @()
    if ($score.compensation -and $score.compensation.types) { $types = @($score.compensation.types) }
    $sub = $score.subscores
    $baseScore = [double]$sub.speed_score + [double]$sub.ROM_score + [double]$sub.smoothness_score + [double]$sub.coordination_score + [double]$sub.muscle_score
    $finalScore = [double]$score.final_score
    $hasLf = $types -contains "LF"
    $hasTremor = $types -contains "TREMOR"
    $expectedPenalty = ExpectedPenalty -HasLf $hasLf -HasTremor $hasTremor
    $expectedEffective = [Math]::Min($expectedPenalty, $baseScore)
    $effectivePenalty = [Math]::Max(0.0, $baseScore - $finalScore)
    $penaltyError = $effectivePenalty - $expectedEffective
    $penaltyCheck = if ([Math]::Abs($penaltyError) -le $penaltyTolerance) { "PASS" } else { "FAIL" }
    $rows += [pscustomobject]@{
      session_id = $spec.session_id
      rep_index = $i + 1
      expected_label = $labels[$i]
      final_score = $finalScore
      base_score = $baseScore
      effective_penalty = $effectivePenalty
      speed_score = $sub.speed_score
      ROM_score = $sub.ROM_score
      smoothness_score = $sub.smoothness_score
      coordination_score = $sub.coordination_score
      muscle_score = $sub.muscle_score
      has_lf = $hasLf
      has_tremor = $hasTremor
      types = ($types -join "|")
      expected_additional_penalty = $expectedPenalty
      penalty_error = $penaltyError
      penalty_check = $penaltyCheck
    }
  }
}

$summaryCsv = Join-Path $outputRoot "lateral_transfer_approved_final_validation_summary.csv"
$rows | Export-Csv -LiteralPath $summaryCsv -NoTypeInformation -Encoding UTF8

$tremorTp = ($rows | Where-Object { $_.expected_label -eq "tremor" -and $_.has_tremor }).Count
$tremorFn = ($rows | Where-Object { $_.expected_label -eq "tremor" -and -not $_.has_tremor }).Count
$tremorFp = ($rows | Where-Object { $_.expected_label -ne "tremor" -and $_.has_tremor }).Count
$tremorTn = ($rows | Where-Object { $_.expected_label -ne "tremor" -and -not $_.has_tremor }).Count
$lfTp = ($rows | Where-Object { $_.expected_label -eq "lf" -and $_.has_lf }).Count
$lfFn = ($rows | Where-Object { $_.expected_label -eq "lf" -and -not $_.has_lf }).Count
$lfFp = ($rows | Where-Object { $_.expected_label -ne "lf" -and $_.has_lf }).Count
$lfTn = ($rows | Where-Object { $_.expected_label -ne "lf" -and -not $_.has_lf }).Count
$penaltyPass = ($rows | Where-Object { $_.penalty_check -eq "PASS" }).Count
$penaltyFail = ($rows | Where-Object { $_.penalty_check -eq "FAIL" }).Count

Write-Host "VALIDATION_MODE=APPROVED_FINAL_VALIDATION"
Write-Host "VALIDATION_OUTPUT=$outputRoot"
Write-Host "APPROVED_MANIFEST=$manifest"
Write-Host "APPROVED_SESSION_COUNT=$($sessionSpecs.Count)"
Write-Host "APPROVED_EXPECTED_REP_TOTAL=$approvedExpectedRepTotal"
Write-Host "ACTUAL_SCORE_REP_TOTAL=$($rows.Count)"
Write-Host "LATERAL_WRAPPER_VERSION=lateral_transfer_segmentation_drink_reuse_v1"
Write-Host "SEGMENTATION_CORE_VERSION=drink_hybrid_segmentation_v2_1_1"
Write-Host "LF_VERSION=lateral_transfer_lf_v1"
Write-Host "TREMOR_VERSION=lateral_transfer_tremor_v1"
Write-Host "TEMPLATE_VERSION=lateral_transfer_healthy_template_v1"
Write-Host "PROFILE_SHA256=$((Get-FileHash -LiteralPath (Join-Path $root 'feature_extractors\lateral_transfer_profile.py') -Algorithm SHA256).Hash)"
Write-Host "TOTAL_REPS=$($rows.Count)"
Write-ScoreStats -Rows $rows -Label "normal" -Prefix "NORMAL"
Write-ScoreStats -Rows $rows -Label "lf" -Prefix "LF"
Write-ScoreStats -Rows $rows -Label "tremor" -Prefix "TREMOR"
Write-Host "TREMOR_TP=$tremorTp"
Write-Host "TREMOR_FN=$tremorFn"
Write-Host "TREMOR_FP=$tremorFp"
Write-Host "TREMOR_TN=$tremorTn"
Write-Host "LF_TP=$lfTp"
Write-Host "LF_FN=$lfFn"
Write-Host "LF_FP=$lfFp"
Write-Host "LF_TN=$lfTn"
Write-Host "PENALTY_CHECK_PASS=$penaltyPass"
Write-Host "PENALTY_CHECK_FAIL=$penaltyFail"
if ($rows.Count -ne $approvedExpectedRepTotal) { Stop-Gate "APPROVED_REP_TOTAL_MISMATCH=$($rows.Count):expected=$approvedExpectedRepTotal" 20 }
if ($tremorTp -ne 5 -or $tremorFn -ne 0 -or $tremorFp -ne 0 -or $tremorTn -ne 54) {
  Stop-Gate "FINAL_DETECTOR_REGRESSION=FAIL:TREMOR=$tremorTp/$tremorFn/$tremorFp/$tremorTn" 22
}
if ($lfTp -ne 6 -or $lfFn -ne 0 -or $lfFp -ne 2 -or $lfTn -ne 51) {
  Stop-Gate "FINAL_DETECTOR_REGRESSION=FAIL:LF=$lfTp/$lfFn/$lfFp/$lfTn" 23
}
if ($penaltyFail -ne 0) { Stop-Gate "PENALTY_CHECK_FAILED=$penaltyFail" 21 }
Write-Host "FINAL_DETECTOR_REGRESSION=PASS"
Write-Host "PENALTY_CONSISTENCY=PASS"
Write-Host "SCORE_CONTRACT=PASS"

