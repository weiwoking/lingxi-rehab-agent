$ErrorActionPreference = "Stop"
$python = "C:\ec\_conda_envs\rehab_score\python.exe"
$scoreRoot = "C:\ec\esp-claw\pc\rehab_score_service"
$assetRoot = if ($env:REHAB_SCORING_ASSETS_ROOT) { $env:REHAB_SCORING_ASSETS_ROOT } else { "C:\ec\_rehab_scoring_assets" }
$sessionRoot = Join-Path $assetRoot "calibration_sessions"
$baseline = Join-Path $assetRoot "templates\MHH_healthy_template.json"
$outputRoot = Join-Path $assetRoot ("derived\drink_water_lf_v2_tremor_v1_1_validation_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
$penaltyTolerance = 0.05
New-Item -ItemType Directory -Force $outputRoot | Out-Null

Set-Location $scoreRoot

$sessionSpecs = @(
  @{ Task="TASK003"; Labels=@("normal","lf","normal") },
  @{ Task="TASK005"; Labels=@("normal","lf","normal") },
  @{ Task="TASK006"; Labels=@("normal","normal","normal","normal","normal") },
  @{ Task="TASK007"; Labels=@("normal","normal","normal","normal","normal") },
  @{ Task="TASK008"; Labels=@("normal") },
  @{ Task="TASK009"; Labels=@("normal") },
  @{ Task="TASK011"; Labels=@("normal") },
  @{ Task="TASK012"; Labels=@("lf") },
  @{ Task="TASK013"; Labels=@("lf") },
  @{ Task="TASK014"; Labels=@("lf") },
  @{ Task="TASK015"; Labels=@("tremor") },
  @{ Task="TASK016"; Labels=@("tremor") },
  @{ Task="TASK019"; Labels=@("tremor") },
  @{ Task="TASK020"; Labels=@("tremor") },
  @{ Task="TASK021"; Labels=@("tremor") },
  @{ Task="TASK022"; Labels=@("tremor") },
  @{ Task="TASK023"; Labels=@("tremor") }
)

function Get-ScoreFiles {
  param([string]$SessionOutput)
  $perRep = Get-ChildItem -LiteralPath $SessionOutput -Recurse -Directory |
    Where-Object { $_.Name -eq "per_rep_scores" } |
    Select-Object -First 1
  $root = if ($null -ne $perRep) { $perRep.FullName } else { $SessionOutput }
  return Get-ChildItem -LiteralPath $root -Recurse -Filter "*_score.json" -File |
    Where-Object { $_.Name -notlike "*_score_metadata.json" } |
    Sort-Object FullName
}

function Get-ExpectedPenalty {
  param([bool]$HasLf, [bool]$HasTremor)
  $value = 0.0
  if ($HasLf) { $value += 10.0 }
  if ($HasTremor) { $value += 15.0 }
  return $value
}

$rows = @()
foreach ($spec in $sessionSpecs) {
  $sessionId = "20260807_$($spec.Task)"
  $sessionDir = Join-Path $sessionRoot $sessionId
  if (!(Test-Path $sessionDir)) { throw "Missing session dir: $sessionDir" }
  $csv = Join-Path $sessionDir "raw_session.csv"
  if (!(Test-Path $csv)) {
    $csvFile = Get-ChildItem -LiteralPath $sessionDir -Filter "*.csv" -File | Select-Object -First 1
    if ($null -eq $csvFile) { throw "Missing session CSV: $sessionDir" }
    $csv = $csvFile.FullName
  }

  $sessionOut = Join-Path $outputRoot $sessionId
  & $python "tools\drink_water_session_scoring_v1.py" --source-session $csv --baseline-template $baseline --output-root $sessionOut --session-id $sessionId --overwrite
  if ($LASTEXITCODE -ne 0) {
    Write-Host "SESSION_SCORE_FAILED=$($spec.Task)"
    exit $LASTEXITCODE
  }

  $scoreFiles = @(Get-ScoreFiles -SessionOutput $sessionOut)
  $expectedCount = $spec.Labels.Count
  $actualCount = $scoreFiles.Count
  if ($actualCount -ne $expectedCount) {
    Write-Host "REP_COUNT_MISMATCH"
    Write-Host "session=$sessionId"
    Write-Host "expected=$expectedCount"
    Write-Host "actual=$actualCount"
    exit 2
  }

  for ($i = 0; $i -lt $expectedCount; $i++) {
    $score = Get-Content -LiteralPath $scoreFiles[$i].FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    $comp = $score.compensation
    $types = @()
    if ($comp -and $comp.types) { $types = @($comp.types) }
    $sub = $score.subscores
    $baseScore = [double]$sub.speed_score + [double]$sub.ROM_score + [double]$sub.smoothness_score + [double]$sub.coordination_score + [double]$sub.muscle_score
    $finalScore = [double]$score.final_score
    $effectivePenalty = [Math]::Max(0.0, $baseScore - $finalScore)
    $hasLf = $types -contains "LF"
    $hasTremor = $types -contains "TREMOR"
    $expectedPenalty = Get-ExpectedPenalty -HasLf $hasLf -HasTremor $hasTremor
    $expectedEffective = [Math]::Min($expectedPenalty, $baseScore)
    $penaltyError = $effectivePenalty - $expectedEffective
    $penaltyCheck = if ([Math]::Abs($penaltyError) -le $penaltyTolerance) { "PASS" } else { "FAIL" }
    $rows += [pscustomobject]@{
      session_id = $sessionId
      rep_index = $i + 1
      expected_label = $spec.Labels[$i]
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
      expected_effective_penalty = $expectedEffective
      penalty_error = $penaltyError
      penalty_tolerance = $penaltyTolerance
      feedback = (($score.feedback) -join " ")
      penalty_check = $penaltyCheck
    }
  }
}

$summaryCsv = Join-Path $outputRoot "drink_water_tremor_validation_summary.csv"
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

Write-Host "VALIDATION_OUTPUT=$outputRoot"
Write-Host "SUMMARY_CSV=$summaryCsv"
Write-Host "REP_COUNT_TOTAL=$($rows.Count)"
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
foreach ($label in @("normal", "lf", "tremor")) {
  $group = @($rows | Where-Object { $_.expected_label -eq $label })
  $avg = if ($group.Count -gt 0) { ($group | Measure-Object final_score -Average).Average } else { $null }
  $key = switch ($label) {
    "normal" { "AVG_FINAL_SCORE_NORMAL" }
    "lf" { "AVG_FINAL_SCORE_LF" }
    "tremor" { "AVG_FINAL_SCORE_TREMOR" }
  }
  if ($null -eq $avg) {
    Write-Host "$key=NA"
  } else {
    Write-Host ("{0}={1:N2}" -f $key, $avg)
  }
}
if ($penaltyFail -gt 0) {
  exit 3
}
if ($rows.Count -ne 29) {
  Write-Host "REP_COUNT_TOTAL_EXPECTED=29"
  exit 4
}
if ($tremorTp -ne 7 -or $tremorFn -ne 0 -or $tremorFp -ne 0 -or $tremorTn -ne 22) {
  Write-Host "TREMOR_CONFUSION_MATRIX_MISMATCH"
  Write-Host "EXPECTED_TREMOR_TP=7"
  Write-Host "EXPECTED_TREMOR_FN=0"
  Write-Host "EXPECTED_TREMOR_FP=0"
  Write-Host "EXPECTED_TREMOR_TN=22"
  exit 5
}
if ($lfTp -ne 5 -or $lfFn -ne 0 -or $lfFp -ne 0 -or $lfTn -ne 24) {
  Write-Host "LF_CONFUSION_MATRIX_MISMATCH"
  Write-Host "EXPECTED_LF_TP=5"
  Write-Host "EXPECTED_LF_FN=0"
  Write-Host "EXPECTED_LF_FP=0"
  Write-Host "EXPECTED_LF_TN=24"
  exit 6
}
Write-Host "DRINK_WATER_LF_V2_TREMOR_V1_1_GATE=PASS"
