param(
    [string]$DoctorRoot = "",
    [string]$ScorePcRoot = "",
    [string]$ScorePythonPath = "",
    [string]$DoctorPythonPath = "",
    [string]$RuntimeRoot = "",
    [switch]$NoBrowser,
    [switch]$NoStatusWait,
    [ValidateSet("", "after_score", "after_doctor")]
    [string]$SimulateFailureStage = ""
)

$ErrorActionPreference = "Stop"
$RehabProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\..\..\.."))

. (Join-Path $PSScriptRoot "launcher_lifecycle.ps1")

function Resolve-PositiveIntEnv([string]$Name, [int]$DefaultValue, [int]$MinimumValue) {
    $raw = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $DefaultValue
    }
    $parsed = 0
    if (-not [int]::TryParse($raw, [ref]$parsed) -or $parsed -lt $MinimumValue) {
        Write-Warning "$Name must be an integer of at least $MinimumValue; using $DefaultValue."
        return $DefaultValue
    }
    return $parsed
}

function Assert-ProcessRunning($Process, [string]$Name, [string]$StderrPath) {
    if ($null -eq $Process -or $Process.HasExited) {
        throw "$Name process exited before readiness. stderr: $StderrPath"
    }
}

function Wait-HttpJsonReady([string]$Name, [string]$Url, [scriptblock]$Ready, $Process, [string]$StderrPath, [int]$TimeoutSeconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastError = "no response"
    while ([DateTime]::UtcNow -lt $deadline) {
        Assert-ProcessRunning $Process $Name $StderrPath
        try {
            $payload = Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 2
            if (& $Ready $payload) {
                return $payload
            }
            $lastError = "readiness payload did not match the expected schema"
        }
        catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 250
    }
    throw "$Name did not become ready within $TimeoutSeconds seconds at $Url. Last error: $lastError"
}

function Wait-FrontendReady($Process, [string]$StderrPath, [int]$TimeoutSeconds) {
    $url = "http://127.0.0.1:5173/"
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastError = "no response"
    while ([DateTime]::UtcNow -lt $deadline) {
        Assert-ProcessRunning $Process "Frontend" $StderrPath
        try {
            $response = Invoke-WebRequest -Uri $url -Method Get -TimeoutSec 2 -UseBasicParsing
            if ([int]$response.StatusCode -ge 200 -and [int]$response.StatusCode -lt 400 -and $response.Content) {
                return
            }
            $lastError = "HTTP status $($response.StatusCode) or empty body"
        }
        catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 250
    }
    throw "Frontend did not become HTTP-ready within $TimeoutSeconds seconds at $url. Last error: $lastError"
}

function Get-ResolvedPath([string]$Path, [string]$Description, [string]$PathType) {
    if (-not (Test-Path -LiteralPath $Path -PathType $PathType)) {
        throw "$Description does not exist: $Path"
    }
    return (Resolve-Path -LiteralPath $Path).Path
}

$ProjectRoot = if ([string]::IsNullOrWhiteSpace($DoctorRoot)) {
    Split-Path -Parent $PSScriptRoot
}
else {
    Get-ResolvedPath $DoctorRoot "Doctor root" "Container"
}
$BackendDir = Get-ResolvedPath (Join-Path $ProjectRoot "backend") "Doctor backend directory" "Container"
$FrontendDir = Get-ResolvedPath (Join-Path $ProjectRoot "frontend") "Doctor frontend directory" "Container"
$BackendRunFile = Get-ResolvedPath (Join-Path $BackendDir "run.py") "Doctor backend entry point" "Leaf"
. (Join-Path $PSScriptRoot "frontend_dependency_paths.ps1")
Initialize-FrontendDependencyPaths -FrontendDir $FrontendDir
$FrontendModules = Get-ResolvedPath (Join-Path $FrontendDir "node_modules") "Frontend dependencies" "Container"

$BackendPythonCandidate = if ([string]::IsNullOrWhiteSpace($DoctorPythonPath)) {
    Join-Path $BackendDir ".venv\Scripts\python.exe"
}
else {
    $DoctorPythonPath
}
$BackendPython = Get-ResolvedPath $BackendPythonCandidate "Doctor backend Python" "Leaf"

$ResolvedScorePcRoot = if ([string]::IsNullOrWhiteSpace($ScorePcRoot)) {
    Split-Path -Parent $ProjectRoot
}
else {
    Get-ResolvedPath $ScorePcRoot "ESP-Claw PC root" "Container"
}
$ScoreServiceDir = Get-ResolvedPath (Join-Path $ResolvedScorePcRoot "rehab_score_service") "Score service directory" "Container"
$ScorePythonCandidate = if (-not [string]::IsNullOrWhiteSpace($ScorePythonPath)) {
    $ScorePythonPath
}
elseif (Test-Path -LiteralPath (Join-Path $ScoreServiceDir ".venv\Scripts\python.exe") -PathType Leaf) {
    Join-Path $ScoreServiceDir ".venv\Scripts\python.exe"
}
elseif (-not [string]::IsNullOrWhiteSpace($env:REHAB_SCORE_PYTHON)) {
    $env:REHAB_SCORE_PYTHON
}
else {
    (Join-Path $RehabProjectRoot "external\_conda_envs\rehab_score\python.exe")
}
$ScorePython = Get-ResolvedPath $ScorePythonCandidate "Score service Python" "Leaf"
$TrainingDataRoot = if ([string]::IsNullOrWhiteSpace($env:REHAB_TRAINING_DATA_ROOT)) {
    (Join-Path $RehabProjectRoot "external\_rehab_training_data")
} else {
    [System.IO.Path]::GetFullPath($env:REHAB_TRAINING_DATA_ROOT)
}
$ScoringAssetsRoot = if ([string]::IsNullOrWhiteSpace($env:REHAB_SCORING_ASSETS_ROOT)) {
    (Join-Path $RehabProjectRoot "external\_rehab_scoring_assets")
} else {
    [System.IO.Path]::GetFullPath($env:REHAB_SCORING_ASSETS_ROOT)
}

$FrontendNode = (Get-Command node.exe -ErrorAction Stop).Source
$FrontendEntry = Get-ResolvedPath (Join-Path $FrontendDir "node_modules\.pnpm\vite@8.1.5\node_modules\vite\bin\vite.js") "Installed Vite entry" "Leaf"

$RuntimeDir = if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    Join-Path $RehabProjectRoot "external\ESP-Claw_localappdata\doctor_pc_only"
}
else {
    [System.IO.Path]::GetFullPath($RuntimeRoot)
}
$RunId = "{0}_{1}" -f (Get-Date).ToString("yyyyMMdd_HHmmssfff"), ([Guid]::NewGuid().ToString("N").Substring(0, 8))
$RunDir = Join-Path (Join-Path $RuntimeDir "runs") $RunId
$StatePath = Join-Path $RuntimeDir "processes.json"
$LauncherLog = Join-Path $RunDir "launcher.log"
$ScoreStdout = Join-Path $RunDir "score.stdout.log"
$ScoreStderr = Join-Path $RunDir "score.stderr.log"
$DoctorStdout = Join-Path $RunDir "doctor.stdout.log"
$DoctorStderr = Join-Path $RunDir "doctor.stderr.log"
$FrontendStdout = Join-Path $RunDir "frontend.stdout.log"
$FrontendStderr = Join-Path $RunDir "frontend.stderr.log"

New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
Set-LauncherLogPath $LauncherLog

$ScoreTimeoutSeconds = Resolve-PositiveIntEnv "PC_SCORE_STARTUP_TIMEOUT_SECONDS" 60 1
$DoctorTimeoutSeconds = Resolve-PositiveIntEnv "PC_DOCTOR_STARTUP_TIMEOUT_SECONDS" 90 1
$FrontendTimeoutSeconds = Resolve-PositiveIntEnv "PC_FRONTEND_STARTUP_TIMEOUT_SECONDS" 60 1

$scoreProcess = $null
$backendProcess = $null
$frontendProcess = $null
$scoreListenerEvidence = $null
$backendListenerEvidence = $null
$frontendListenerEvidence = $null
$scoreLauncherEvidence = $null
$backendLauncherEvidence = $null
$frontendLauncherEvidence = $null
$failureStage = "preflight"
$previousCleanupCompleted = $false

try {
    Write-LauncherEvent "Run $RunId starting. Runtime directory: $RunDir"
    Write-LauncherEvent "Cleaning the previous scoring stack."
    Clear-PreviousScoringStack $StatePath
    $previousCleanupCompleted = $true
    Assert-TargetPortsFree

    $failureStage = "score_start"
    $env:REHAB_SCORE_LISTEN_ADDRESS = "0.0.0.0"
    $env:REHAB_SCORE_PORT = "8020"
    $env:REHAB_SCORE_TASK12_TEMPLATE_PROFILE = "temporary_standard_20260724"
    $env:REHAB_SCORE_DRINK_FULL_CYCLE = "1"
    $env:ESP_CLAW_PC_ROOT = $ResolvedScorePcRoot
    $env:REHAB_SCORE_ROOT = $ScoreServiceDir
    $env:SCORE_SERVICE_ROOT = $ScoreServiceDir
    $env:REHAB_TASK_REGISTRY_PATH = Join-Path $ScoreServiceDir "config\rehab_task_registry_v1.json"
    $env:REHAB_SCORE_PYTHON = $ScorePython
    $env:REHAB_TRAINING_DATA_ROOT = $TrainingDataRoot
    $env:REHAB_SCORING_ASSETS_ROOT = $ScoringAssetsRoot
    $scoreProcess = Start-Process -FilePath $ScorePython -ArgumentList @(
        "-m", "uvicorn", "cloud_interface.score_server:app", "--host", "0.0.0.0", "--port", "8020", "--workers", "1"
    ) -WorkingDirectory $ScoreServiceDir -PassThru -WindowStyle Hidden -RedirectStandardOutput $ScoreStdout -RedirectStandardError $ScoreStderr
    $scoreLauncherEvidence = Get-ProcessEvidence ([int]$scoreProcess.Id)

    $failureStage = "score_readiness"
    Wait-HttpJsonReady "Score" "http://127.0.0.1:8020/api/v1/score/health" {
        param($payload)
        $payload.status -eq "ready" -and $payload.runtime_data -and $payload.runtime_data.ready -eq $true
    } $scoreProcess $ScoreStderr $ScoreTimeoutSeconds | Out-Null
    $scoreListenerPid = Resolve-SingleOwner "Score TCP 8020" (Get-TcpListenerPids 8020)
    $scoreListenerEvidence = Get-ProcessEvidence $scoreListenerPid
    Write-LauncherEvent "Score ready: PID=$scoreListenerPid TCP=8020."
    if ($SimulateFailureStage -eq "after_score") {
        throw "Simulated failure after Score readiness."
    }

    $failureStage = "doctor_start"
    $env:DOCTOR_SOURCE_MODE = "pc_local"
    $env:DOCTOR_BACKEND_HOST = "0.0.0.0"
    $env:DOCTOR_BACKEND_PORT = "8001"
    $env:SCORE_BASE_URL = "http://127.0.0.1:8020"
    $env:SCORE_FULL_RESULT_WAIT_SECONDS = "5.0"
    $env:SCORE_FULL_RESULT_POLL_INTERVAL_SECONDS = "0.10"
    if ([string]::IsNullOrWhiteSpace($env:PC_STREAM_SOURCE_MODE)) { $env:PC_STREAM_SOURCE_MODE = "udp" }
    if ([string]::IsNullOrWhiteSpace($env:PC_STREAM_UDP_HOST)) { $env:PC_STREAM_UDP_HOST = "0.0.0.0" }
    if ([string]::IsNullOrWhiteSpace($env:PC_STREAM_UDP_PORT)) { $env:PC_STREAM_UDP_PORT = "6006" }
    if ([string]::IsNullOrWhiteSpace($env:PC_STREAM_STALE_SECONDS)) { $env:PC_STREAM_STALE_SECONDS = "3.0" }
    if ([string]::IsNullOrWhiteSpace($env:PC_NODE_STALE_MS)) { $env:PC_NODE_STALE_MS = "1000" }
    Remove-Item Env:P4_BASE_URL -ErrorAction SilentlyContinue
    $backendProcess = Start-Process -FilePath $BackendPython -ArgumentList @("run.py") -WorkingDirectory $BackendDir -PassThru -WindowStyle Hidden -RedirectStandardOutput $DoctorStdout -RedirectStandardError $DoctorStderr
    $backendLauncherEvidence = Get-ProcessEvidence ([int]$backendProcess.Id)

    $failureStage = "doctor_readiness"
    Wait-HttpJsonReady "Doctor" "http://127.0.0.1:8001/health" {
        param($payload)
        $payload.status -eq "ok"
    } $backendProcess $DoctorStderr $DoctorTimeoutSeconds | Out-Null
    $backendListenerPid = Resolve-SingleOwner "Doctor TCP 8001" (Get-TcpListenerPids 8001)
    if ($env:PC_STREAM_SOURCE_MODE -eq "udp") {
        $udpOwnerPid = Resolve-SingleOwner "Doctor UDP 6006" (Get-UdpOwnerPids 6006)
        if ([int]$udpOwnerPid -ne [int]$backendListenerPid) {
            throw "Doctor TCP 8001 PID $backendListenerPid does not match UDP 6006 PID $udpOwnerPid."
        }
    }
    $backendListenerEvidence = Get-ProcessEvidence $backendListenerPid
    Write-LauncherEvent "Doctor ready: PID=$backendListenerPid TCP=8001 UDP=6006."
    if ($SimulateFailureStage -eq "after_doctor") {
        throw "Simulated failure after Doctor readiness."
    }

    $failureStage = "frontend_start"
    $env:VITE_CLINICAL_DATA_MODE = "live"
    $frontendProcess = Start-Process -FilePath $FrontendNode -ArgumentList @(('"{0}"' -f $FrontendEntry), "--host", "127.0.0.1", "--port", "5173") -WorkingDirectory $FrontendDir -PassThru -WindowStyle Hidden -RedirectStandardOutput $FrontendStdout -RedirectStandardError $FrontendStderr
    $frontendLauncherEvidence = Get-ProcessEvidence ([int]$frontendProcess.Id)

    $failureStage = "frontend_readiness"
    Wait-FrontendReady $frontendProcess $FrontendStderr $FrontendTimeoutSeconds
    $frontendListenerPid = Resolve-SingleOwner "Frontend TCP 5173" (Get-TcpListenerPids 5173)
    $frontendListenerEvidence = Get-ProcessEvidence $frontendListenerPid
    Write-LauncherEvent "Frontend ready: PID=$frontendListenerPid TCP=5173 HTTP=ready."

    $failureStage = "state_save"
    $state = [ordered]@{
        schema_version = $script:LauncherStateSchema
        run_id = $RunId
        started_at = (Get-Date).ToString("o")
        run_directory = $RunDir
        roles = @(
            New-RoleState "score" $scoreListenerPid ([int]$scoreProcess.Id) "tcp" @(8020) $ScoreServiceDir $ScoreStdout $ScoreStderr
            New-RoleState "doctor" $backendListenerPid ([int]$backendProcess.Id) "tcp+udp" @(8001, 6006) $BackendDir $DoctorStdout $DoctorStderr
            New-RoleState "frontend" $frontendListenerPid ([int]$frontendProcess.Id) "tcp" @(5173) $FrontendDir $FrontendStdout $FrontendStderr
        )
    }
    Write-LauncherStateAtomic $StatePath $state
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $RunDir "processes.json") -Encoding UTF8

    $failureStage = "browser_open"
    if (-not $NoBrowser) {
        Start-Process "http://127.0.0.1:5173"
        Write-LauncherEvent "Browser opened after all readiness gates passed."
    }

    Write-Host ""
    Write-Host "ESP-Claw PC Scoring Stack — RUNNING"
    Write-Host ""
    Write-Host ("Score Service      RUNNING   PID {0}   TCP 8020" -f $scoreListenerPid)
    Write-Host ("Doctor Backend     RUNNING   PID {0}   TCP 8001" -f $backendListenerPid)
    Write-Host "Doctor UDP         RUNNING             UDP 6006"
    Write-Host ("Frontend           RUNNING   PID {0}   TCP 5173" -f $frontendListenerPid)
    Write-Host ""
    Write-Host "Frontend:"
    Write-Host "http://127.0.0.1:5173"
    Write-Host ""
    Write-Host "Logs:"
    Write-Host $RunDir
    Write-Host ""
    Write-Host "Press Enter to close this status window."
    Write-Host "Services will keep running."
    Write-Host "The next START will replace them automatically."
    if (-not $NoStatusWait) {
        [void](Read-Host)
    }
    exit 0
}
catch {
    $errorMessage = $_.Exception.Message
    Write-LauncherEvent "START FAILED at stage '$failureStage': $errorMessage"
    try { Stop-CurrentRole "Frontend" $frontendListenerEvidence $frontendLauncherEvidence } catch { Write-LauncherEvent "Frontend rollback warning: $($_.Exception.Message)" }
    try { Stop-CurrentRole "Doctor" $backendListenerEvidence $backendLauncherEvidence } catch { Write-LauncherEvent "Doctor rollback warning: $($_.Exception.Message)" }
    try { Stop-CurrentRole "Score" $scoreListenerEvidence $scoreLauncherEvidence } catch { Write-LauncherEvent "Score rollback warning: $($_.Exception.Message)" }
    try {
        if ($null -ne $frontendLauncherEvidence) { Wait-TcpPortFree 5173 }
        if ($null -ne $backendLauncherEvidence) { Wait-TcpPortFree 8001; Wait-UdpPortFree 6006 }
        if ($null -ne $scoreLauncherEvidence) { Wait-TcpPortFree 8020 }
    }
    catch {
        Write-LauncherEvent "Rollback port verification warning: $($_.Exception.Message)"
    }
    if ($previousCleanupCompleted) {
        Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
    }
    [ordered]@{
        schema_version = "esp_claw_pc_scoring_failed_run_v1"
        run_id = $RunId
        failed_at = (Get-Date).ToString("o")
        stage = $failureStage
        error = $errorMessage
        run_directory = $RunDir
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $RunDir "failure.json") -Encoding UTF8
    Write-Error "ESP-Claw PC startup failed at stage '$failureStage'. Logs: $RunDir. Error: $errorMessage"
    exit 1
}
