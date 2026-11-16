$ErrorActionPreference = "Stop"

$ScoreBaseUrl = $env:SCORE_BASE_URL
if ([string]::IsNullOrWhiteSpace($ScoreBaseUrl)) {
    $ScoreBaseUrl = "http://127.0.0.1:8020"
}
$ScoreBaseUrl = $ScoreBaseUrl.TrimEnd("/")

$RuntimeRoot = $env:REHAB_TRAINING_DATA_ROOT
if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    $RuntimeRoot = "C:\ec\_rehab_training_data"
}

# TASK006 is a fixed repair-verification sample only. The formal product path
# must use the current session_id under sessions\<session_id>\input.
$Task006Dir = Join-Path $RuntimeRoot "sessions\20260726_TASK006\input"
$TaskPath = Join-Path $Task006Dir "task.json"
$CsvPath = Join-Path $Task006Dir "DATA_SEQ0000000001.csv"

function Read-JsonFile([string]$Path) {
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Get-NextJobId([string]$Root) {
    $today = Get-Date -Format "yyyyMMdd"
    $sessions = Join-Path $Root "sessions"
    $staging = Join-Path $Root ".staging"
    $used = @{}
    foreach ($dir in @($sessions, $staging)) {
        if (-not (Test-Path -LiteralPath $dir -PathType Container)) {
            continue
        }
        Get-ChildItem -LiteralPath $dir -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match "^${today}_TASK[0-9]{3}$" } |
            ForEach-Object { $used[$_.Name] = $true }
    }
    for ($index = 1; $index -le 999; $index++) {
        $candidate = "{0}_TASK{1:D3}" -f $today, $index
        if (-not $used.ContainsKey($candidate)) {
            return $candidate
        }
    }
    throw "No available ${today}_TASKNNN job id."
}

Write-Host "Checking score service: $ScoreBaseUrl"
$healthUri = "$ScoreBaseUrl/api/v1/score/health"
try {
    $health = Invoke-RestMethod -Method GET -Uri $healthUri -TimeoutSec 5
    Write-Host "Score service health:" ($health | ConvertTo-Json -Depth 5)
}
catch {
    throw "Score service is not reachable at $healthUri. Start it before running this script."
}

if (-not (Test-Path -LiteralPath $TaskPath -PathType Leaf)) {
    throw "TASK006 task.json was not found: $TaskPath"
}
if (-not (Test-Path -LiteralPath $CsvPath -PathType Leaf)) {
    throw "TASK006 CSV was not found: $CsvPath"
}

$task = Read-JsonFile $TaskPath
$csvInfo = Get-Item -LiteralPath $CsvPath
$csvHash = (Get-FileHash -LiteralPath $CsvPath -Algorithm SHA256).Hash.ToLowerInvariant()
$taskBytes = [System.Text.Encoding]::UTF8.GetBytes(($task | ConvertTo-Json -Depth 10 -Compress))
$taskHash = [System.BitConverter]::ToString(
    [System.Security.Cryptography.SHA256]::Create().ComputeHash($taskBytes)
).Replace("-", "").ToLowerInvariant()
$jobId = Get-NextJobId $RuntimeRoot

Write-Host "TASK006 source directory: $Task006Dir"
Write-Host "Source CSV file: $($csvInfo.Name)"
Write-Host "Source CSV size: $($csvInfo.Length)"
Write-Host "Source CSV SHA-256: $csvHash"
Write-Host "Task JSON SHA-256 for new job: $taskHash"
Write-Host "New score job id: $jobId"
Write-Host "Submitting the complete original CSV once. The source TASK006 files are not modified."

$headers = @{
    "Content-Type" = "application/json"
    "X-Session-Id" = $jobId
    "X-CSV-Size" = [string]$csvInfo.Length
    "X-CSV-SHA256" = $csvHash
}

$create = Invoke-RestMethod -Method POST -Uri "$ScoreBaseUrl/api/v1/score/jobs" -Headers $headers -Body $taskBytes -TimeoutSec 15
Write-Host "Create job response:" ($create | ConvertTo-Json -Depth 8)

if ($create.upload_required -eq $true) {
    $uploadHeaders = @{ "Content-Type" = "text/csv" }
    Invoke-RestMethod -Method PUT -Uri "$ScoreBaseUrl/api/v1/score/jobs/$jobId/csv" -Headers $uploadHeaders -InFile $CsvPath -TimeoutSec 120 | Out-Null
    Write-Host "Complete CSV upload submitted."
}
else {
    Write-Host "Upload was not required for this idempotent job."
}

$deadline = (Get-Date).AddMinutes(5)
$status = $null
do {
    Start-Sleep -Seconds 1
    $status = Invoke-RestMethod -Method GET -Uri "$ScoreBaseUrl/api/v1/score/jobs/$jobId" -TimeoutSec 10
    Write-Host ("Job state: {0}" -f $status.state)
    if ($status.state -in @("succeeded", "failed")) {
        break
    }
} while ((Get-Date) -lt $deadline)

if ($null -eq $status) {
    throw "No job status was returned."
}

$jobDir = Join-Path $RuntimeRoot "sessions\$jobId"
Write-Host "New job runtime directory: $jobDir"

if ($status.state -eq "failed") {
    Write-Host "Job failed."
    Write-Host ("error_code: {0}" -f $status.error_code)
    Write-Host ("message: {0}" -f $status.message)
    exit 2
}

if ($status.state -ne "succeeded") {
    Write-Host "Job did not finish before the local polling timeout."
    exit 3
}

$compact = Invoke-RestMethod -Method GET -Uri "$ScoreBaseUrl/api/v1/score/jobs/$jobId/result" -TimeoutSec 10
$resultPath = Join-Path $jobDir "result\score_result.json"
if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) {
    throw "Full score_result.json was not found: $resultPath"
}
$full = Read-JsonFile $resultPath

Write-Host "Compact result:" ($compact | ConvertTo-Json -Depth 8)
Write-Host "Full result path: $resultPath"
Write-Host ("valid: {0}" -f $full.valid)
Write-Host ("final_score: {0}" -f $full.summary.average_score)
Write-Host ("score_version: {0}" -f $full.score_version)
Write-Host ("action_count: {0}" -f @($full.actions).Count)
Write-Host "summary:" ($full.summary | ConvertTo-Json -Depth 8)
Write-Host "data_quality:" ($full.data_quality | ConvertTo-Json -Depth 8)
Write-Host "compensation:" ($full.compensation | ConvertTo-Json -Depth 8)
Write-Host "fatigue:" ($full.fatigue | ConvertTo-Json -Depth 8)
Write-Host "feedback:" ($full.feedback | ConvertTo-Json -Depth 8)
