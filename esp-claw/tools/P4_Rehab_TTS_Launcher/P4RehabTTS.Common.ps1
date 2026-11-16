$script:ServiceName = "p4-rehab-tts"
$script:ServicePort = 8765
$script:ServiceRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..\..\pc\rehab_tts_service")
)
$script:RuntimeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\..\..\external\P4RehabTTS"))
$script:VenvRoot = Join-Path $script:RuntimeRoot "venv"
$script:LogRoot = Join-Path $script:RuntimeRoot "logs"
$script:PidPath = Join-Path $script:RuntimeRoot "service.pid"
$script:RequirementsMarker = Join-Path $script:VenvRoot ".requirements.sha256"

function Initialize-P4RehabTTSPaths {
    New-Item -ItemType Directory -Force -Path $script:RuntimeRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $script:LogRoot | Out-Null
}

function Get-P4RehabTTSHealth {
    try {
        return Invoke-RestMethod `
            -Uri "http://127.0.0.1:$($script:ServicePort)/health" `
            -Method Get `
            -TimeoutSec 2
    }
    catch {
        return $null
    }
}

function Test-P4RehabTTSPortInUse {
    try {
        return $null -ne (Get-NetTCPConnection `
            -LocalPort $script:ServicePort `
            -State Listen `
            -ErrorAction Stop |
            Select-Object -First 1)
    }
    catch {
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $result = $client.BeginConnect("127.0.0.1", $script:ServicePort, $null, $null)
            return $result.AsyncWaitHandle.WaitOne(500) -and $client.Connected
        }
        finally {
            $client.Dispose()
        }
    }
}

function Get-P4RehabTTSOwnedProcess {
    if (-not (Test-Path -LiteralPath $script:PidPath)) {
        return $null
    }
    $pidText = (Get-Content -LiteralPath $script:PidPath -Raw).Trim()
    if ($pidText -notmatch "^[0-9]+$") {
        return $null
    }
    $processInfo = Get-CimInstance Win32_Process `
        -Filter "ProcessId = $pidText" `
        -ErrorAction SilentlyContinue
    if ($null -eq $processInfo) {
        return $null
    }
    $command = [string]$processInfo.CommandLine
    $expectedModule = "rehab_tts_service.app:app"
    $expectedRoot = (Split-Path -Parent $script:ServiceRoot).ToLowerInvariant()
    if (
        -not $command.Contains($expectedModule) -or
        -not $command.ToLowerInvariant().Contains($expectedRoot)
    ) {
        return $null
    }
    return $processInfo
}

function Find-P4RehabTTSPython {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        return @($py.Source, "-3")
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        return @($python.Source)
    }
    throw "Python 3 was not found. Install Python 3.10 or newer and try again."
}

function Assert-P4RehabTTSPythonVersionText {
    param([string]$VersionText)

    if ($VersionText -notmatch "^(?<Major>[0-9]+)\.(?<Minor>[0-9]+)\.(?<Patch>[0-9]+)$") {
        throw "Unable to parse the selected Python version. Python 3.10 or newer is required."
    }
    $major = [int]$Matches.Major
    $minor = [int]$Matches.Minor
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
        throw "需要 Python 3.10 或更高版本 (Python 3.10 or newer is required). Detected Python $VersionText."
    }
}

function Assert-P4RehabTTSPythonVersion {
    param([object[]]$PythonCommand)

    if ($null -eq $PythonCommand -or $PythonCommand.Count -eq 0) {
        throw "No Python interpreter was selected."
    }
    $executable = [string]$PythonCommand[0]
    $arguments = @()
    if ($PythonCommand.Count -gt 1) {
        $arguments += $PythonCommand[1..($PythonCommand.Count - 1)]
    }
    $arguments += @(
        "-c",
        "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
    )
    $versionOutput = @(& $executable @arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to query the selected Python version. Python 3.10 or newer is required."
    }
    $versionText = (
        $versionOutput |
        Select-Object -Last 1 |
        ForEach-Object { [string]$_ }
    ).Trim()
    Assert-P4RehabTTSPythonVersionText -VersionText $versionText
    return $versionText
}

function Initialize-P4RehabTTSVenv {
    Initialize-P4RehabTTSPaths
    if (-not (Test-Path -LiteralPath $script:ServiceRoot -PathType Container)) {
        throw "Service directory is missing: $($script:ServiceRoot)"
    }
    $requirements = Join-Path $script:ServiceRoot "requirements-lock.txt"
    if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
        throw "Requirements file is missing: $requirements"
    }

    $venvPython = Join-Path $script:VenvRoot "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        $pythonCommand = Find-P4RehabTTSPython
        Assert-P4RehabTTSPythonVersion -PythonCommand $pythonCommand | Out-Null
        $executable = $pythonCommand[0]
        $arguments = @()
        if ($pythonCommand.Count -gt 1) {
            $arguments += $pythonCommand[1..($pythonCommand.Count - 1)]
        }
        $arguments += @("-m", "venv", $script:VenvRoot)
        $process = Start-Process -FilePath $executable `
            -ArgumentList $arguments `
            -Wait `
            -PassThru `
            -WindowStyle Hidden
        if ($process.ExitCode -ne 0) {
            throw "Failed to create the dedicated virtual environment."
        }
    }
    Assert-P4RehabTTSPythonVersion -PythonCommand @($venvPython) | Out-Null

    $requirementsHash = (Get-FileHash `
        -LiteralPath $requirements `
        -Algorithm SHA256).Hash
    $installedHash = ""
    if (Test-Path -LiteralPath $script:RequirementsMarker) {
        $installedHash = (Get-Content -LiteralPath $script:RequirementsMarker -Raw).Trim()
    }
    if ($installedHash -ne $requirementsHash) {
        $process = Start-Process -FilePath $venvPython `
            -ArgumentList @("-m", "pip", "install", "--disable-pip-version-check", "-r", $requirements) `
            -Wait `
            -PassThru
        if ($process.ExitCode -ne 0) {
            throw "Dependency installation failed. See the visible pip output."
        }
        [System.IO.File]::WriteAllText(
            $script:RequirementsMarker,
            $requirementsHash,
            [System.Text.UTF8Encoding]::new($false)
        )
    }
    return $venvPython
}

function Get-P4RehabTTSLanIPv4 {
    $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -ne "127.0.0.1" -and
            -not $_.IPAddress.StartsWith("169.254.") -and
            $_.AddressState -eq "Preferred"
        } |
        Sort-Object -Property InterfaceMetric
    if ($addresses) {
        return ($addresses | Select-Object -First 1).IPAddress
    }
    return "(not detected)"
}

function Start-P4RehabTTS {
    $health = Get-P4RehabTTSHealth
    if ($null -ne $health) {
        if ($health.service -eq $script:ServiceName) {
            return
        }
        throw "Port $($script:ServicePort) is occupied by another HTTP service. It was not stopped."
    }
    if (Test-P4RehabTTSPortInUse) {
        throw "Port $($script:ServicePort) is occupied by another process. It was not stopped."
    }

    $venvPython = Initialize-P4RehabTTSVenv
    $stdout = Join-Path $script:LogRoot "uvicorn.stdout.log"
    $stderr = Join-Path $script:LogRoot "uvicorn.stderr.log"
    $arguments = @(
        "-m", "uvicorn",
        "rehab_tts_service.app:app",
        "--app-dir", (Split-Path -Parent $script:ServiceRoot),
        "--host", "0.0.0.0",
        "--port", [string]$script:ServicePort
    )
    $process = Start-Process `
        -FilePath $venvPython `
        -ArgumentList $arguments `
        -WorkingDirectory $script:ServiceRoot `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden `
        -PassThru
    [System.IO.File]::WriteAllText(
        $script:PidPath,
        [string]$process.Id,
        [System.Text.UTF8Encoding]::new($false)
    )

    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 250
        $health = Get-P4RehabTTSHealth
        if ($null -ne $health -and $health.service -eq $script:ServiceName) {
            return
        }
        if ($process.HasExited) {
            throw "The TTS service exited during startup. Check $stderr"
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "The TTS service did not become healthy within 30 seconds."
}

function Stop-P4RehabTTS {
    $owned = Get-P4RehabTTSOwnedProcess
    if ($null -eq $owned) {
        $health = Get-P4RehabTTSHealth
        if ($null -ne $health -or (Test-P4RehabTTSPortInUse)) {
            throw "A process uses port $($script:ServicePort), but its identity does not match this launcher. It was not stopped."
        }
        Remove-Item -LiteralPath $script:PidPath -Force -ErrorAction SilentlyContinue
        return
    }
    Stop-Process -Id $owned.ProcessId -ErrorAction Stop
    Wait-Process -Id $owned.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $script:PidPath -Force -ErrorAction SilentlyContinue
}

function Get-P4RehabTTSStatus {
    Initialize-P4RehabTTSPaths
    $health = Get-P4RehabTTSHealth
    $owned = Get-P4RehabTTSOwnedProcess
    $lanIp = Get-P4RehabTTSLanIPv4
    [PSCustomObject]@{
        Service = $script:ServiceName
        State = if ($null -ne $health) { "Running" } else { "Stopped" }
        Identity = if ($null -ne $health) { [string]$health.service } else { "-" }
        Health = if ($null -ne $health) { [string]$health.status } else { "-" }
        Provider = if ($null -ne $health) { [string]$health.provider } else { "edge-tts" }
        ProviderStatus = if ($null -ne $health) { [string]$health.provider_status } else { "-" }
        OwnedPID = if ($null -ne $owned) { [string]$owned.ProcessId } else { "-" }
        LAN_IPv4 = $lanIp
        Port = $script:ServicePort
        HealthURL = "http://$($lanIp):$($script:ServicePort)/health"
        DocsURL = "http://$($lanIp):$($script:ServicePort)/docs"
        Logs = $script:LogRoot
        Cache = (Join-Path $script:RuntimeRoot "cache")
        Venv = $script:VenvRoot
    }
}
