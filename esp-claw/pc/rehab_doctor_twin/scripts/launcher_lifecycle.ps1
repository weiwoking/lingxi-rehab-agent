$script:LauncherStateSchema = "esp_claw_pc_scoring_processes_v2"
$script:LauncherLogPath = $null

function Set-LauncherLogPath([string]$Path) {
    $script:LauncherLogPath = $Path
}

function Write-LauncherEvent([string]$Message) {
    $line = "{0} {1}" -f (Get-Date).ToString("o"), $Message
    Write-Host $Message
    if (-not [string]::IsNullOrWhiteSpace($script:LauncherLogPath)) {
        Add-Content -LiteralPath $script:LauncherLogPath -Value $line -Encoding UTF8
    }
}

function Protect-CommandLine([string]$CommandLine) {
    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return ""
    }
    $result = $CommandLine
    $result = [regex]::Replace($result, '(?i)(api[_-]?key|token|password|secret)(\s*[=:]\s*)([^\s"'']+)', '$1$2<redacted>')
    return $result
}

function Convert-CimCreationDateToUtc($CreationDate) {
    if ($null -eq $CreationDate) {
        return $null
    }
    if ($CreationDate -is [DateTime]) {
        return $CreationDate.ToUniversalTime()
    }
    try {
        return [System.Management.ManagementDateTimeConverter]::ToDateTime([string]$CreationDate).ToUniversalTime()
    }
    catch {
        return $null
    }
}

function Get-LauncherCimProcess([int]$ProcessId) {
    return Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
}

function Get-TcpListenerPids([int]$Port) {
    return @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
}

function Get-UdpOwnerPids([int]$Port) {
    return @(
        Get-NetUDPEndpoint -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
}

function Get-ProcessEvidence([int]$ProcessId) {
    $process = Get-LauncherCimProcess $ProcessId
    if ($null -eq $process) {
        return [PSCustomObject]@{
            pid = $ProcessId
            creation_time_utc = $null
            executable = ""
            command_line = ""
        }
    }
    $created = Convert-CimCreationDateToUtc $process.CreationDate
    return [PSCustomObject]@{
        pid = [int]$process.ProcessId
        creation_time_utc = if ($created) { $created.ToString("o") } else { $null }
        executable = [string]$process.ExecutablePath
        command_line = Protect-CommandLine ([string]$process.CommandLine)
    }
}

function Format-ProcessEvidence([int]$ProcessId) {
    $evidence = Get-ProcessEvidence $ProcessId
    return "PID=$($evidence.pid); executable=$($evidence.executable); command=$($evidence.command_line)"
}

function Test-ProcessEvidence($Expected) {
    if ($null -eq $Expected -or -not $Expected.pid -or [string]::IsNullOrWhiteSpace([string]$Expected.creation_time_utc)) {
        return $false
    }
    $actual = Get-LauncherCimProcess ([int]$Expected.pid)
    if ($null -eq $actual) {
        return $false
    }
    $actualCreated = Convert-CimCreationDateToUtc $actual.CreationDate
    $expectedCreated = $null
    try {
        if ($Expected.creation_time_utc -is [DateTime]) {
            $expectedCreated = $Expected.creation_time_utc.ToUniversalTime()
        }
        else {
            $expectedCreated = [DateTime]::Parse([string]$Expected.creation_time_utc).ToUniversalTime()
        }
    }
    catch {
        return $false
    }
    if ($null -eq $actualCreated -or [Math]::Abs(($actualCreated - $expectedCreated).TotalSeconds) -gt 2) {
        return $false
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$Expected.executable) -and
        -not [string]::Equals([string]$Expected.executable, [string]$actual.ExecutablePath, [StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }
    $actualCommand = Protect-CommandLine ([string]$actual.CommandLine)
    if (-not [string]::IsNullOrWhiteSpace([string]$Expected.command_line) -and
        -not [string]::Equals([string]$Expected.command_line, $actualCommand, [StringComparison]::Ordinal)) {
        return $false
    }
    return $true
}

function Get-ProcessTreeIds([int]$RootProcessId) {
    $snapshot = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $result = [System.Collections.Generic.List[int]]::new()
    $pending = [System.Collections.Generic.Queue[int]]::new()
    $seen = [System.Collections.Generic.HashSet[int]]::new()
    $pending.Enqueue($RootProcessId)
    [void]$seen.Add($RootProcessId)
    while ($pending.Count -gt 0) {
        $current = $pending.Dequeue()
        foreach ($child in @($snapshot | Where-Object { [int]$_.ParentProcessId -eq $current })) {
            $childId = [int]$child.ProcessId
            if ($seen.Add($childId)) {
                $pending.Enqueue($childId)
                $result.Add($childId)
            }
        }
    }
    $result.Reverse()
    $result.Add($RootProcessId)
    return @($result)
}

function Stop-ExactProcessTree([string]$Label, $Evidence, [int]$TimeoutSeconds = 10) {
    if ($null -eq $Evidence -or -not $Evidence.pid) {
        return
    }
    $processId = [int]$Evidence.pid
    if (-not (Get-Process -Id $processId -ErrorAction SilentlyContinue)) {
        return
    }
    if (-not (Test-ProcessEvidence $Evidence)) {
        throw "$Label PID $processId identity no longer matches the saved run state; refusing to stop it."
    }
    $tree = @(Get-ProcessTreeIds $processId)
    Write-LauncherEvent "Stopping $Label process tree: root PID=$processId; members=$($tree -join ',')."
    foreach ($id in $tree) {
        Stop-Process -Id ([int]$id) -Force -ErrorAction SilentlyContinue
    }
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (-not (Get-Process -Id $processId -ErrorAction SilentlyContinue)) {
            return
        }
        Start-Sleep -Milliseconds 200
    }
    throw "$Label PID $processId did not exit within $TimeoutSeconds seconds."
}

function Wait-TcpPortFree([int]$Port, [int]$TimeoutSeconds = 10) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (@(Get-TcpListenerPids $Port).Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 200
    }
    $owners = @(Get-TcpListenerPids $Port)
    throw "TCP $Port did not become free within $TimeoutSeconds seconds. Owners: $($owners -join ', ')."
}

function Wait-UdpPortFree([int]$Port, [int]$TimeoutSeconds = 10) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (@(Get-UdpOwnerPids $Port).Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 200
    }
    $owners = @(Get-UdpOwnerPids $Port)
    throw "UDP $Port did not become free within $TimeoutSeconds seconds. Owners: $($owners -join ', ')."
}

function Stop-Tcp8020Owner {
    $owners = @(Get-TcpListenerPids 8020 | Where-Object { $_ -gt 0 } | Select-Object -Unique)
    foreach ($owner in $owners) {
        $evidence = Get-ProcessEvidence ([int]$owner)
        Write-LauncherEvent "Authorized TCP 8020 takeover: $(Format-ProcessEvidence ([int]$owner))."
        Stop-ExactProcessTree "TCP 8020 owner" $evidence
    }
    Wait-TcpPortFree 8020
}

function Convert-LegacyLauncherState($LegacyState) {
    $roles = [System.Collections.Generic.List[object]]::new()
    foreach ($definition in @(
        [PSCustomObject]@{ role = "doctor"; listener_pid = $LegacyState.backend_pid; launcher_pid = $LegacyState.backend_launcher_pid; marker = 'rehab_doctor_twin\\backend.*run\.py' },
        [PSCustomObject]@{ role = "frontend"; listener_pid = $LegacyState.frontend_pid; launcher_pid = $LegacyState.frontend_launcher_pid; marker = 'rehab_doctor_twin\\frontend.*vite' }
    )) {
        $listenerPid = 0
        if (-not [int]::TryParse([string]$definition.listener_pid, [ref]$listenerPid) -or $listenerPid -le 0) {
            continue
        }
        $listener = Get-ProcessEvidence $listenerPid
        if ([string]::IsNullOrWhiteSpace([string]$listener.creation_time_utc)) {
            continue
        }
        if ([string]$listener.command_line -notmatch $definition.marker) {
            Write-LauncherEvent "Legacy $($definition.role) PID $listenerPid does not match the official command path and will not be trusted."
            continue
        }
        $launcher = $null
        $launcherPid = 0
        if ([int]::TryParse([string]$definition.launcher_pid, [ref]$launcherPid) -and $launcherPid -gt 0) {
            $candidate = Get-ProcessEvidence $launcherPid
            if (-not [string]::IsNullOrWhiteSpace([string]$candidate.creation_time_utc)) {
                $launcher = $candidate
            }
        }
        $roles.Add([PSCustomObject]@{
            role = $definition.role
            listener = $listener
            launcher = $launcher
        })
    }
    if ($roles.Count -gt 0) {
        Write-LauncherEvent "Migrating $($roles.Count) trusted live owner(s) from legacy doctor_pc_only_processes_v1 state."
    }
    else {
        Write-LauncherEvent "Legacy doctor_pc_only_processes_v1 state has no trusted live conditional-port owner."
    }
    return [PSCustomObject]@{
        schema_version = $script:LauncherStateSchema
        run_id = "legacy_v1_migration"
        roles = @($roles)
    }
}

function Read-LauncherState([string]$StatePath) {
    if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
        return $null
    }
    try {
        $state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        Write-LauncherEvent "Prior process state is unreadable and will not be trusted: $StatePath"
        return $null
    }
    if ($state.schema_version -eq "doctor_pc_only_processes_v1") {
        return Convert-LegacyLauncherState $state
    }
    if ($state.schema_version -ne $script:LauncherStateSchema) {
        Write-LauncherEvent "Prior process state schema '$($state.schema_version)' is not trusted for conditional ports."
        return $null
    }
    return $state
}

function Get-StateRole($State, [string]$Role) {
    if ($null -eq $State) {
        return $null
    }
    return @($State.roles | Where-Object { $_.role -eq $Role } | Select-Object -First 1)[0]
}

function Assert-ConditionalOwnersMatch([string]$Label, [int[]]$Owners, $RoleState) {
    $ids = @($Owners | Where-Object { $_ -gt 0 } | Select-Object -Unique)
    if ($ids.Count -eq 0) {
        return
    }
    if ($null -eq $RoleState -or $null -eq $RoleState.listener) {
        $details = @($ids | ForEach-Object { Format-ProcessEvidence ([int]$_) }) -join " | "
        throw "$Label is occupied by an unknown process; refusing to stop it. $details"
    }
    $recordedPid = [int]$RoleState.listener.pid
    if ($ids.Count -ne 1 -or [int]$ids[0] -ne $recordedPid -or -not (Test-ProcessEvidence $RoleState.listener)) {
        $details = @($ids | ForEach-Object { Format-ProcessEvidence ([int]$_) }) -join " | "
        throw "$Label owner does not match the saved scoring run; refusing to stop it. $details"
    }
}

function Stop-ConditionalRole([string]$Role, $RoleState, [int]$TcpPort, [int]$UdpPort = 0) {
    $tcpOwners = @(Get-TcpListenerPids $TcpPort)
    Assert-ConditionalOwnersMatch "$Role TCP $TcpPort" $tcpOwners $RoleState
    if ($UdpPort -gt 0) {
        $udpOwners = @(Get-UdpOwnerPids $UdpPort)
        Assert-ConditionalOwnersMatch "$Role UDP $UdpPort" $udpOwners $RoleState
        $combined = @($tcpOwners + $udpOwners | Where-Object { $_ -gt 0 } | Select-Object -Unique)
        if ($combined.Count -gt 1) {
            throw "$Role TCP $TcpPort and UDP $UdpPort do not have the same saved owner; refusing cleanup."
        }
    }
    if ($null -ne $RoleState) {
        if ($null -ne $RoleState.launcher -and (Test-ProcessEvidence $RoleState.launcher)) {
            Stop-ExactProcessTree "$Role launcher" $RoleState.launcher
        }
        if ($null -ne $RoleState.listener -and (Test-ProcessEvidence $RoleState.listener)) {
            Stop-ExactProcessTree "$Role listener" $RoleState.listener
        }
    }
    Wait-TcpPortFree $TcpPort
    if ($UdpPort -gt 0) {
        Wait-UdpPortFree $UdpPort
    }
}

function Clear-PreviousScoringStack([string]$StatePath) {
    $state = Read-LauncherState $StatePath
    Stop-ConditionalRole "Frontend" (Get-StateRole $state "frontend") 5173
    Stop-ConditionalRole "Doctor Backend" (Get-StateRole $state "doctor") 8001 6006
    Stop-Tcp8020Owner
    if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
        Remove-Item -LiteralPath $StatePath -Force
    }
}

function Assert-TargetPortsFree {
    foreach ($port in 8020, 8001, 5173) {
        $owners = @(Get-TcpListenerPids $port)
        if ($owners.Count -gt 0) {
            $details = @($owners | ForEach-Object { Format-ProcessEvidence ([int]$_) }) -join " | "
            throw "TCP $port is still occupied. $details"
        }
    }
    $udpOwners = @(Get-UdpOwnerPids 6006)
    if ($udpOwners.Count -gt 0) {
        $details = @($udpOwners | ForEach-Object { Format-ProcessEvidence ([int]$_) }) -join " | "
        throw "UDP 6006 is still occupied. $details"
    }
}

function Resolve-SingleOwner([string]$Label, [int[]]$Owners) {
    $ids = @($Owners | Where-Object { $_ -gt 0 } | Select-Object -Unique)
    if ($ids.Count -ne 1) {
        throw "$Label must have exactly one owner; found: $($ids -join ', ')."
    }
    return [int]$ids[0]
}

function New-RoleState([string]$Role, [int]$ListenerPid, [int]$LauncherPid, [string]$Protocol, [int[]]$Ports, [string]$WorkingDirectory, [string]$StdoutPath, [string]$StderrPath) {
    return [ordered]@{
        role = $Role
        listener = Get-ProcessEvidence $ListenerPid
        launcher = Get-ProcessEvidence $LauncherPid
        protocol = $Protocol
        expected_ports = @($Ports)
        working_directory = $WorkingDirectory
        stdout_log = $StdoutPath
        stderr_log = $StderrPath
    }
}

function Write-LauncherStateAtomic([string]$StatePath, $State) {
    $parent = Split-Path -Parent $StatePath
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $temporary = "$StatePath.$([Guid]::NewGuid().ToString('N')).tmp"
    $State | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $StatePath -Force
}

function Stop-CurrentRole([string]$Label, $ListenerEvidence, $LauncherEvidence) {
    if ($null -ne $LauncherEvidence -and (Test-ProcessEvidence $LauncherEvidence)) {
        Stop-ExactProcessTree "$Label launcher" $LauncherEvidence
    }
    elseif ($null -ne $LauncherEvidence -and $LauncherEvidence.pid) {
        # A short-lived cmd/venv shim may exit while its child remains. The parent
        # relation is still available in Win32_Process, and this evidence was
        # captured during the current START rather than loaded from disk.
        foreach ($descendantId in @(Get-ProcessTreeIds ([int]$LauncherEvidence.pid) | Where-Object { $_ -ne [int]$LauncherEvidence.pid })) {
            $descendant = Get-ProcessEvidence ([int]$descendantId)
            if ($descendant.creation_time_utc) {
                Stop-ExactProcessTree "$Label current-run descendant" $descendant
            }
        }
    }
    if ($null -ne $ListenerEvidence -and (Test-ProcessEvidence $ListenerEvidence)) {
        Stop-ExactProcessTree "$Label listener" $ListenerEvidence
    }
}
