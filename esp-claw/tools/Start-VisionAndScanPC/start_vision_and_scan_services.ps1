[CmdletBinding()]
param(
    [switch]$VisionOnly,
    [switch]$ScanOnly,
    [switch]$CheckOnly,
    [switch]$NoP4Discovery,
    [switch]$SelfTest,
    [switch]$ValidationMode,
    [int]$HealthTimeoutSeconds = 60,
    [int]$P4RetrySeconds = 30,
    [int]$P4ProbeTimeoutMs = 450
)

$ErrorActionPreference = 'Stop'

$script:ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
$script:RepoRoot = Join-Path $script:ProjectRoot 'esp-claw'
$script:VisionRoot = Join-Path $script:ProjectRoot 'esp-claw\pc\vision_interpreter'
$script:PipelineRoot = Join-Path $script:ProjectRoot 'external\_deltar_p4_pipeline'
$script:ScanScript = Join-Path $script:ProjectRoot 'external\_deltar_p4_pipeline\scripts\run_scan_service.py'
$script:PreflightScript = Join-Path $script:ProjectRoot 'external\_deltar_p4_pipeline\scripts\run_offline_preflight.py'
$script:PreflightStatusPath = Join-Path $script:ProjectRoot 'external\_deltar_scan_service_data\progress\offline_preflight.json'
$script:Python = Join-Path $script:ProjectRoot 'external\_conda_envs\vision_interpreter\python.exe'
$script:WorkerPython = Join-Path $script:ProjectRoot 'external\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
$script:HotspotIP = '192.168.137.1'
$script:RuntimeDir = Join-Path $script:ProjectRoot 'external\ESP-Claw_localappdata\vision_scan_launcher'
$script:StatusPath = Join-Path $script:RuntimeDir 'launcher_status.json'
$script:P4CachePath = Join-Path $script:RuntimeDir 'last_p4_ip.txt'
$script:VisionHealthUrl = 'http://127.0.0.1:8000/api/v1/vision/health'
$script:VisionOpenApiUrl = 'http://127.0.0.1:8000/openapi.json'
$script:VisionViewUrl = 'http://127.0.0.1:8000/person-follow-view'
$script:ScanLocalHealthUrl = 'http://127.0.0.1:8030/api/v1/scan/health'
$script:ScanHotspotHealthUrl = 'http://192.168.137.1:8030/api/v1/scan/health'
$script:ConsoleUrl = 'http://127.0.0.1:8030/vision-console'
$script:PreviousStatus = $null
$script:ExitCode = 0
$script:Interrupted = $false

function Write-Step([string]$Message) {
    Write-Host "`n=== $Message ===" -ForegroundColor Cyan
}

function Write-Info([string]$Message) {
    Write-Host "[信息] $Message"
}

function Write-Good([string]$Message) {
    Write-Host "[完成] $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host "[提示] $Message" -ForegroundColor Yellow
}

function Write-Bad([string]$Message) {
    Write-Host "[错误] $Message" -ForegroundColor Red
}

function Format-PassFail([bool]$Value) {
    if ($Value) { return 'PASS' }
    return 'FAIL'
}

function Format-ScanEndpointDiagnostic {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [int]$StatusCode,
        [bool]$JsonPass,
        [bool]$HealthPass,
        [string]$HealthStatus = ''
    )
    $json = Format-PassFail -Value $JsonPass
    $health = Format-PassFail -Value $HealthPass
    return "Scan $Name HTTP=$StatusCode，JSON=$json，health=$health，status=$HealthStatus"
}

function Assert-Path {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Description,
        [switch]$Container
    )
    $pathType = if ($Container) { 'Container' } else { 'Leaf' }
    if (-not (Test-Path -LiteralPath $Path -PathType $pathType)) {
        throw "$Description 不存在：$Path"
    }
    Write-Good "$Description：$Path"
}

function Read-JsonFile([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json
    } catch {
        Write-Warn "忽略无法解析的状态文件：$Path"
        return $null
    }
}

function Invoke-HttpGet {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutMs = 2500,
        [switch]$Json
    )
    try {
        $request = [System.Net.HttpWebRequest]::Create($Url)
        $request.Method = 'GET'
        $request.Timeout = [Math]::Max(200, $TimeoutMs)
        $request.ReadWriteTimeout = [Math]::Max(200, $TimeoutMs)
        $request.UserAgent = 'ESP-Claw-Vision-Scan-Launcher/1.0'
        $request.KeepAlive = $false
        # These endpoints are loopback/hotspot/LAN control-plane probes.  A
        # configured Internet proxy must not intercept 127.0.0.1 or
        # 192.168.137.1 and turn a healthy local service into a timeout.
        $request.Proxy = $null
        $response = $request.GetResponse()
        try {
            $statusCode = [int]$response.StatusCode
            $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
            try {
                $body = $reader.ReadToEnd()
            } finally {
                $reader.Dispose()
            }
            if ($statusCode -lt 200 -or $statusCode -ge 300) {
                return [pscustomobject]@{
                    Success = $false
                    StatusCode = $statusCode
                    Body = $body
                    Data = $null
                    JsonPass = $false
                    Error = "HTTP $statusCode"
                }
            }
            $data = $null
            if ($Json) {
                try {
                    $data = $body | ConvertFrom-Json
                } catch {
                    return [pscustomobject]@{
                        Success = $false
                        StatusCode = $statusCode
                        Body = $body
                        Data = $null
                        JsonPass = $false
                        Error = '响应不是有效 JSON'
                    }
                }
            }
            return [pscustomobject]@{
                Success = $true
                StatusCode = $statusCode
                Body = $body
                Data = $data
                JsonPass = [bool](-not $Json -or $null -ne $data)
                Error = ''
            }
        } finally {
            $response.Dispose()
        }
    } catch {
        return [pscustomobject]@{
            Success = $false
            StatusCode = 0
            Body = ''
            Data = $null
            JsonPass = $false
            Error = $_.Exception.Message
        }
    }
}

function Test-VisionHealthObject([AllowNull()][object]$Health) {
    if ($null -eq $Health) {
        return $false
    }
    return [string]$Health.status -eq 'ok' -and
        $Health.model_ready -eq $true -and
        [int]$Health.image.width -eq 640 -and
        [int]$Health.image.height -eq 360 -and
        [string]$Health.image.pixel_format -eq 'RGB565' -and
        [string]$Health.tof.resolution -eq '8x8' -and
        [int]$Health.tof.rows -eq 8 -and
        [int]$Health.tof.cols -eq 8
}

function Test-ScanHealthObject([AllowNull()][object]$Health) {
    if ($null -eq $Health) {
        return $false
    }
    return [string]$Health.status -eq 'ok' -and
        [string]$Health.schema_version -eq 'deltar_scan_service_health.v1' -and
        -not [string]::IsNullOrWhiteSpace([string]$Health.pipeline_version) -and
        $null -ne $Health.limits -and
        $null -ne $Health.local_runtime -and
        $Health.local_runtime.ready -eq $true -and
        $Health.local_runtime.runtime_network_required -eq $false
}

function Get-VisionValidation {
    $healthResponse = Invoke-HttpGet -Url $script:VisionHealthUrl -TimeoutMs 3000 -Json
    $healthPass = $healthResponse.Success -and (Test-VisionHealthObject -Health $healthResponse.Data)

    $openApiResponse = Invoke-HttpGet -Url $script:VisionOpenApiUrl -TimeoutMs 3000 -Json
    $routePass = $false
    if ($openApiResponse.Success -and $null -ne $openApiResponse.Data.paths) {
        $routePass = @($openApiResponse.Data.paths.PSObject.Properties.Name) -contains '/api/v1/person-follow/detect'
    }

    $viewResponse = Invoke-HttpGet -Url $script:VisionViewUrl -TimeoutMs 3000
    return [pscustomobject]@{
        Pass = [bool]($healthPass -and $routePass -and $viewResponse.Success)
        HealthPass = [bool]$healthPass
        RoutePass = [bool]$routePass
        ViewPass = [bool]$viewResponse.Success
        Health = $healthResponse.Data
        HealthStatusCode = $healthResponse.StatusCode
        OpenApiStatusCode = $openApiResponse.StatusCode
        ViewStatusCode = $viewResponse.StatusCode
        Error = (@($healthResponse.Error, $openApiResponse.Error, $viewResponse.Error) |
            Where-Object { $_ }) -join '; '
    }
}

function Get-ScanValidation {
    $local = Invoke-HttpGet -Url $script:ScanLocalHealthUrl -TimeoutMs 3000 -Json
    $hotspot = Invoke-HttpGet -Url $script:ScanHotspotHealthUrl -TimeoutMs 3000 -Json
    $localPass = $local.Success -and (Test-ScanHealthObject -Health $local.Data)
    $hotspotPass = $hotspot.Success -and (Test-ScanHealthObject -Health $hotspot.Data)
    return [pscustomobject]@{
        # The production command binds 0.0.0.0.  Both the local operator path
        # and the P4 hotspot path must be healthy before Scan is READY.
        Pass = [bool]($localPass -and $hotspotPass)
        AllEndpointsPass = [bool]($localPass -and $hotspotPass)
        LocalPass = [bool]$localPass
        HotspotPass = [bool]$hotspotPass
        LocalJsonPass = [bool]$local.JsonPass
        HotspotJsonPass = [bool]$hotspot.JsonPass
        LocalHealthStatus = if ($null -ne $local.Data) { [string]$local.Data.status } else { '' }
        HotspotHealthStatus = if ($null -ne $hotspot.Data) { [string]$hotspot.Data.status } else { '' }
        Local = $local.Data
        Hotspot = $hotspot.Data
        LocalStatusCode = $local.StatusCode
        HotspotStatusCode = $hotspot.StatusCode
        Error = (@($local.Error, $hotspot.Error) | Where-Object { $_ }) -join '; '
    }
}

function Write-ScanPerformanceWarning([AllowNull()][object]$Validation) {
    if ($null -eq $Validation) {
        return
    }
    $health = if ($null -ne $Validation.Local) { $Validation.Local } else { $Validation.Hotspot }
    if ($null -eq $health -or $null -eq $health.performance_guard) {
        return
    }
    $guard = $health.performance_guard
    if ([string]$guard.status -in @('degraded', 'failed')) {
        Write-Warn "Scan 功能健康，但性能告警：status=$($guard.status)，last_job_wall_ms=$($guard.last_job_wall_ms)，threshold_ms=$($guard.threshold_ms)，message=$($guard.message)"
    }
}

function Wait-ServiceValidation {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('Vision', 'Scan')][string]$Service,
        [int]$WaitSeconds
    )
    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $WaitSeconds))
    $last = $null
    do {
        $last = if ($Service -eq 'Vision') { Get-VisionValidation } else { Get-ScanValidation }
        if ($last.Pass) {
            return $last
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $last
}

function Get-PortListeners([int]$Port) {
    return @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object LocalAddress, LocalPort, OwningProcess -Unique)
}

function Get-ProcessInfo([int]$ProcessId) {
    return Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue |
        Select-Object -First 1 ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine
}

function Test-ExpectedProcess {
    param(
        [AllowNull()][object]$Process,
        [Parameter(Mandatory = $true)][ValidateSet('Vision', 'Scan')][string]$Service
    )
    if ($null -eq $Process -or
        [string]::IsNullOrWhiteSpace([string]$Process.ExecutablePath) -or
        [string]::IsNullOrWhiteSpace([string]$Process.CommandLine) -or
        -not [string]::Equals([string]$Process.ExecutablePath, $script:Python,
            [System.StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }
    $command = [string]$Process.CommandLine
    if ($Service -eq 'Vision') {
        return $command -match '(?i)(^|\s)-m\s+uvicorn(?:\s|$)' -and
            $command -match '(?i)(^|\s)app\.main:app(?:\s|$)' -and
            $command -match '(?i)(^|\s)--port(?:\s+|=)8000(?:\s|$)'
    }
    $scanPattern = [regex]::Escape($script:ScanScript)
    return $command -match "(?i)$scanPattern" -and
        $command -match '(?i)(^|\s)--host(?:\s+|=)0\.0\.0\.0(?:\s|$)' -and
        $command -match '(?i)(^|\s)--port(?:\s+|=)8030(?:\s|$)'
}

function Format-PortOwnerDetails([AllowNull()][object[]]$Listeners) {
    $items = @()
    $listenerPids = @($Listeners | ForEach-Object { [int]$_.OwningProcess } | Sort-Object -Unique)
    foreach ($listenerPid in $listenerPids) {
        $process = Get-ProcessInfo -ProcessId $listenerPid
        $name = if ($process) { [string]$process.Name } else { '<unavailable>' }
        $path = if ($process -and $process.ExecutablePath) { [string]$process.ExecutablePath } else { '<unavailable>' }
        $command = if ($process -and $process.CommandLine) {
            ([string]$process.CommandLine) -replace '[\r\n]+', ' '
        } else {
            '<unavailable>'
        }
        $items += "PID=$listenerPid, Name=$name, ExecutablePath=$path, CommandLine=$command"
    }
    return $items -join '; '
}

function Get-PreviousServicePid([string]$Service) {
    if ($null -eq $script:PreviousStatus -or $null -eq $script:PreviousStatus.services) {
        return 0
    }
    try {
        $entry = $script:PreviousStatus.services.$Service
        if ($null -ne $entry -and $null -ne $entry.pid) {
            return [int]$entry.pid
        }
    } catch {
        return 0
    }
    return 0
}

function Get-ServiceOccupancy {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('Vision', 'Scan')][string]$Service,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $listeners = @(Get-PortListeners -Port $Port)
    if ($listeners.Count -eq 0) {
        return [pscustomobject]@{
            State = 'Free'
            Process = $null
            Validation = $null
            Details = ''
        }
    }

    $pids = @($listeners | ForEach-Object { [int]$_.OwningProcess } | Sort-Object -Unique)
    if ($pids.Count -ne 1) {
        return [pscustomobject]@{
            State = 'Conflict'
            Process = $null
            Validation = $null
            Details = Format-PortOwnerDetails -Listeners $listeners
        }
    }

    $process = Get-ProcessInfo -ProcessId $pids[0]
    if (-not (Test-ExpectedProcess -Process $process -Service $Service)) {
        return [pscustomobject]@{
            State = 'Conflict'
            Process = $process
            Validation = $null
            Details = Format-PortOwnerDetails -Listeners $listeners
        }
    }

    $validation = if ($Service -eq 'Vision') { Get-VisionValidation } else { Get-ScanValidation }
    if ($validation.Pass) {
        return [pscustomobject]@{
            State = 'Reusable'
            Process = $process
            Validation = $validation
            Details = Format-PortOwnerDetails -Listeners $listeners
        }
    }

    $previousPid = Get-PreviousServicePid -Service $Service
    if ($previousPid -gt 0 -and $previousPid -eq [int]$process.ProcessId) {
        return [pscustomobject]@{
            State = 'StaleOwned'
            Process = $process
            Validation = $validation
            Details = Format-PortOwnerDetails -Listeners $listeners
        }
    }

    return [pscustomobject]@{
        State = 'MatchingUnhealthyExternal'
        Process = $process
        Validation = $validation
        Details = Format-PortOwnerDetails -Listeners $listeners
    }
}

function New-ServiceState {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('Vision', 'Scan')][string]$Name,
        [Parameter(Mandatory = $true)][int]$Port
    )
    return [pscustomobject]@{
        Name = $Name
        Port = $Port
        Pass = $false
        Reused = $false
        StartedByThisRun = $false
        OwnedProcess = $null
        Pid = $null
        Command = ''
        Cwd = ''
        StdoutLog = ''
        StderrLog = ''
        StartedAt = ''
        Validation = $null
        Error = ''
        Cleanup = 'not_required'
    }
}

function Stop-MatchingProcessById {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('Vision', 'Scan')][string]$Service,
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $processInfo = Get-ProcessInfo -ProcessId $ProcessId
    if (-not (Test-ExpectedProcess -Process $processInfo -Service $Service)) {
        throw "拒绝关闭 PID=$ProcessId：命令行不再匹配 $Service 服务。"
    }
    Stop-Process -Id $ProcessId -ErrorAction Stop
    $deadline = (Get-Date).AddSeconds(8)
    do {
        $alive = $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
        $listeners = @(Get-PortListeners -Port $Port)
        if (-not $alive -and $listeners.Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 100
    } while ((Get-Date) -lt $deadline)
    throw "PID=$ProcessId 已请求关闭，但进程或端口 $Port 未在 8 秒内释放。"
}

function Initialize-YoloPythonBase {
    # The active profile selects this venv. A moved Windows venv retains its
    # base interpreter in pyvenv.cfg; only its two active fields need repair.
    $profilePath = Join-Path $script:PipelineRoot 'profiles\stage8b1_yolo_runtime_profile.json'
    $profile = Get-Content -LiteralPath $profilePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $workerPath = [IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $profilePath) $profile.worker_python))
    $venvRoot = Split-Path -Parent (Split-Path -Parent $workerPath)
    $configPath = Join-Path $venvRoot 'pyvenv.cfg'
    $externalRoot = [IO.Path]::GetFullPath((Join-Path $script:ProjectRoot 'external'))
    if (-not $configPath.StartsWith($externalRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'YOLO venv config must stay inside the current project external directory.'
    }
    Assert-Path -Path $script:WorkerPython -Description 'YOLO base Python'
    $baseRoot = Split-Path -Parent $script:WorkerPython
    $content = [IO.File]::ReadAllText($configPath)
    $updated = [regex]::Replace($content, '(?m)^home = [^\r\n]*', "home = $baseRoot")
    $updated = [regex]::Replace($updated, '(?m)^executable = [^\r\n]*', "executable = $script:WorkerPython")
    if ($updated -ne $content) {
        [IO.File]::WriteAllText($configPath, $updated, [Text.UTF8Encoding]::new($false))
        Write-Good "YOLO venv base resolved inside this project: $baseRoot"
    }
}

function Invoke-LocalModelPreflight {
    Initialize-YoloPythonBase
    Write-Step '正在检查本地模型...'
    $arguments = @(
        $script:PreflightScript,
        '--output', $script:PreflightStatusPath,
        '--pipeline-root', $script:PipelineRoot,
        '--profile-bundle', (Join-Path $script:PipelineRoot 'profiles'),
        '--device', 'cuda:0'
    )
    $output = @(& $script:WorkerPython @arguments 2>&1)
    $exit = $LASTEXITCODE
    foreach ($line in $output) {
        if (-not [string]::IsNullOrWhiteSpace([string]$line)) {
            Write-Info ([string]$line)
        }
    }
    $status = Read-JsonFile -Path $script:PreflightStatusPath
    if ($exit -ne 0 -or $null -eq $status -or $status.ready -ne $true) {
        $reason = if ($null -ne $status -and $null -ne $status.error) {
            [string]$status.error.message
        } else {
            "preflight 进程退出码 $exit"
        }
        throw "本地模型预检失败：$reason"
    }
    if (@($status.network_attempts).Count -ne 0) {
        throw '本地模型预检检测到外网访问尝试。'
    }
    $audit = $status.checkpoint_key_audit
    if (@($audit.missing_keys).Count -ne 0 -or
        @($audit.unexpected_keys).Count -ne 0 -or
        @($audit.shape_mismatches).Count -ne 0) {
        throw 'DELTAR checkpoint 未完整覆盖模型参数。'
    }
    Write-Good "DELTAR：本地模型就绪；运行时外网依赖：无；最后预检：$($status.checked_at)"
}

function Reset-TargetPort {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('Vision', 'Scan')][string]$Service,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $listeners = @(Get-PortListeners -Port $Port)
    if ($listeners.Count -eq 0) {
        Write-Info "$Service 端口 $Port 启动前为空闲。"
        return
    }
    $listenerPids = @($listeners | ForEach-Object {
        [int]$_.OwningProcess
    } | Sort-Object -Unique)
    foreach ($listenerPid in $listenerPids) {
        $process = Get-ProcessInfo -ProcessId $listenerPid
        $command = if ($process -and $process.CommandLine) {
            ([string]$process.CommandLine) -replace '[\r\n]+', ' '
        } else {
            '<unavailable>'
        }
        Write-Warn "强制重启 $Service：关闭端口 $Port 旧占用者 PID=$listenerPid"
        Write-Info "旧进程命令：$command"
        Stop-Process -Id $listenerPid -ErrorAction Stop
    }
    $deadline = (Get-Date).AddSeconds(8)
    do {
        $remaining = @(Get-PortListeners -Port $Port)
        if ($remaining.Count -eq 0) {
            Write-Good "$Service 端口 $Port 已在有界等待内释放。"
            return
        }
        Start-Sleep -Milliseconds 100
    } while ((Get-Date) -lt $deadline)
    throw "端口 $Port 的旧占用者已请求关闭，但端口未在 8 秒内释放。$(
        Format-PortOwnerDetails -Listeners $remaining)"
}

function Start-ServiceSafely {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('Vision', 'Scan')][string]$Service,
        [Parameter(Mandatory = $true)][object]$State
    )
    Write-Step "$Service / 端口 $($State.Port)"
    try {
        # Only PIDs proven to own this exact target port are stopped.  There
        # is deliberately no broad python.exe enumeration or termination.
        Reset-TargetPort -Service $Service -Port $State.Port
    } catch {
        $State.Error = $_.Exception.Message
        Write-Bad $State.Error
        return
    }

    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
    $stdout = Join-Path $script:RuntimeDir "$($Service.ToLower())_${stamp}.stdout.log"
    $stderr = Join-Path $script:RuntimeDir "$($Service.ToLower())_${stamp}.stderr.log"
    if ($Service -eq 'Vision') {
        $arguments = @('-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000', '--workers', '1')
        $cwd = $script:VisionRoot
        $command = "`"$script:Python`" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"
    } else {
        $arguments = @(
            $script:ScanScript,
            '--host', '0.0.0.0',
            '--port', '8030',
            '--require-local-preflight'
        )
        $cwd = $script:PipelineRoot
        $command = "`"$script:Python`" `"$script:ScanScript`" --host 0.0.0.0 --port 8030 --require-local-preflight"
    }

    try {
        $process = Start-Process -FilePath $script:Python -ArgumentList $arguments `
            -WorkingDirectory $cwd -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $State.OwnedProcess = $process
        $State.StartedByThisRun = $true
        $State.Pid = $process.Id
        $State.Command = $command
        $State.Cwd = $cwd
        $State.StdoutLog = $stdout
        $State.StderrLog = $stderr
        $State.StartedAt = (Get-Date).ToString('o')
        Write-Info "启动命令：$command"
        Write-Info "cwd：$cwd"
        Write-Info "PID：$($process.Id)"
        Write-Info "stdout：$stdout"
        Write-Info "stderr：$stderr"

        $validation = Wait-ServiceValidation -Service $Service -WaitSeconds $HealthTimeoutSeconds
        $State.Validation = $validation
        if ($validation.Pass) {
            $State.Pass = $true
            Write-Good "$Service 健康检查通过。"
        } else {
            $State.Error = "$Service 在 $HealthTimeoutSeconds 秒内未通过完整健康检查。$($validation.Error)"
            Write-Bad $State.Error
            Stop-OwnedService -State $State -Reason 'health_failed'
        }
    } catch {
        $State.Error = $_.Exception.Message
        Write-Bad "$Service 启动失败：$($State.Error)"
        Stop-OwnedService -State $State -Reason 'start_failed'
    }
}

function Stop-OwnedService {
    param(
        [Parameter(Mandatory = $true)][object]$State,
        [string]$Reason = 'launcher_exit'
    )
    if (-not $State.StartedByThisRun -or $null -eq $State.OwnedProcess) {
        return
    }
    if ([string]$State.Cleanup -like 'released:*') {
        return
    }
    try {
        if (-not $State.OwnedProcess.HasExited) {
            $current = Get-ProcessInfo -ProcessId ([int]$State.OwnedProcess.Id)
            if (-not (Test-ExpectedProcess -Process $current -Service $State.Name)) {
                $State.Cleanup = 'refused_signature_changed'
                Write-Bad "拒绝清理 PID=$($State.OwnedProcess.Id)：命令签名已变化。"
                return
            }
            Stop-Process -Id $State.OwnedProcess.Id -ErrorAction Stop
            [void]$State.OwnedProcess.WaitForExit(8000)
        }
        $deadline = (Get-Date).AddSeconds(8)
        do {
            $remaining = @(Get-PortListeners -Port $State.Port |
                Where-Object { [int]$_.OwningProcess -eq [int]$State.Pid })
            if ($remaining.Count -eq 0) {
                $State.Cleanup = "released:$Reason"
                Write-Good "$($State.Name) PID=$($State.Pid) 已清理；端口 $($State.Port) 不再由该 PID 监听。"
                return
            }
            Start-Sleep -Milliseconds 100
        } while ((Get-Date) -lt $deadline)
        $State.Cleanup = "port_still_owned:$Reason"
        Write-Bad "$($State.Name) PID=$($State.Pid) 已请求停止，但端口 $($State.Port) 仍由该 PID 监听。"
    } catch {
        $State.Cleanup = "cleanup_error:$($_.Exception.Message)"
        Write-Bad "清理 $($State.Name) PID=$($State.Pid) 失败：$($_.Exception.Message)"
    }
}

function Get-HotspotEntry {
    return @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            [string]$_.IPAddress -eq $script:HotspotIP -and
            [string]$_.AddressState -eq 'Preferred'
        } |
        Select-Object -First 1 IPAddress, PrefixLength, InterfaceAlias, InterfaceIndex, AddressState)
}

function Test-UsableP4Address([string]$IP) {
    $parsed = $null
    if (-not [System.Net.IPAddress]::TryParse($IP, [ref]$parsed)) {
        return $false
    }
    return $parsed.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
        $IP -like '192.168.137.*' -and
        $IP -ne $script:HotspotIP -and
        $IP -ne '192.168.137.0' -and
        $IP -ne '192.168.137.255'
}

function Test-P4Identity([string]$IP) {
    if (-not (Test-UsableP4Address -IP $IP)) {
        return $false
    }
    $camera = Invoke-HttpGet -Url "http://$IP/api/camera/status" -TimeoutMs $P4ProbeTimeoutMs -Json
    if (-not $camera.Success -or $null -eq $camera.Data) {
        return $false
    }
    $tof = Invoke-HttpGet -Url "http://$IP/api/tof/status" -TimeoutMs $P4ProbeTimeoutMs -Json
    if (-not $tof.Success -or $null -eq $tof.Data) {
        return $false
    }
    return [string]$camera.Data.capture_mode -eq 'on_demand' -and
        [int]$camera.Data.output_width -eq 640 -and
        [int]$camera.Data.output_height -eq 360 -and
        [string]$camera.Data.output_pixel_format -eq 'RGB565' -and
        $tof.Data.ok -eq $true -and
        [string]$tof.Data.capture_mode -eq 'on_demand' -and
        [int]$tof.Data.resolution.width -eq 8 -and
        [int]$tof.Data.resolution.height -eq 8
}

function Find-P4ReadOnly {
    $candidates = @()
    if (Test-Path -LiteralPath $script:P4CachePath -PathType Leaf) {
        $candidates += (Get-Content -Raw -Encoding UTF8 -LiteralPath $script:P4CachePath).Trim()
    } else {
        $oldCache = Join-Path $script:OldRuntimeDir 'last_p4_ip.txt'
        if (Test-Path -LiteralPath $oldCache -PathType Leaf) {
            $candidates += (Get-Content -Raw -Encoding UTF8 -LiteralPath $oldCache).Trim()
        }
    }
    $neighbors = @(Get-NetNeighbor -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            [string]$_.IPAddress -like '192.168.137.*' -and
            [string]$_.State -notin @('Unreachable', 'Incomplete')
        } |
        Select-Object -ExpandProperty IPAddress)
    $candidates += $neighbors
    $candidates = @($candidates | Where-Object { Test-UsableP4Address -IP ([string]$_) } |
        Sort-Object -Unique)
    foreach ($candidate in $candidates) {
        if (Test-P4Identity -IP $candidate) {
            Set-Content -LiteralPath $script:P4CachePath -Value $candidate -Encoding UTF8
            return [string]$candidate
        }
    }
    return ''
}

function Convert-ServiceStateForStatus([object]$State) {
    return [ordered]@{
        pass = [bool]$State.Pass
        reused = [bool]$State.Reused
        started_by_this_run = [bool]$State.StartedByThisRun
        pid = $State.Pid
        command = $State.Command
        cwd = $State.Cwd
        stdout_log = $State.StdoutLog
        stderr_log = $State.StderrLog
        started_at = $State.StartedAt
        error = $State.Error
        cleanup = $State.Cleanup
    }
}

function Write-Status {
    param(
        [Parameter(Mandatory = $true)][string]$StateName,
        [Parameter(Mandatory = $true)][object]$VisionState,
        [Parameter(Mandatory = $true)][object]$ScanState,
        [string]$P4IP = '',
        [string]$Message = ''
    )
    $payload = [ordered]@{
        updated_at = (Get-Date).ToString('o')
        state = $StateName
        message = $Message
        hotspot_ip = $script:HotspotIP
        p4_ip = $P4IP
        runtime_dir = $script:RuntimeDir
        services = [ordered]@{
            Vision = Convert-ServiceStateForStatus -State $VisionState
            Scan = Convert-ServiceStateForStatus -State $ScanState
        }
    }
    $payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $script:StatusPath -Encoding UTF8
}

function Write-ReadyBanner {
    param(
        [Parameter(Mandatory = $true)][object]$VisionState,
        [Parameter(Mandatory = $true)][object]$ScanState,
        [string]$P4IP = ''
    )
    $visionText = if ($VisionState.Pass) { 'PASS' } else { 'FAIL' }
    $scanText = if ($ScanState.Pass) { 'PASS' } else { 'FAIL' }
    $p4Text = if ($P4IP) { $P4IP } else { '等待连接' }
    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Green
    Write-Host 'ESP-Claw PC 双服务已就绪'
    Write-Host ''
    Write-Host "Vision health: $visionText"
    Write-Host 'Vision docs:   http://127.0.0.1:8000/docs'
    Write-Host '追人实时查看:  http://127.0.0.1:8000/person-follow-view'
    Write-Host ''
    Write-Host "Scan health:   $scanText"
    Write-Host "统一主页面:    $script:ConsoleUrl"
    Write-Host 'Scan local:    http://127.0.0.1:8030/api/v1/scan/health'
    Write-Host 'Scan hotspot:  http://192.168.137.1:8030/api/v1/scan/health'
    Write-Host ''
    Write-Host 'PC 热点 IP:   192.168.137.1'
    Write-Host "P4 IP:        $p4Text"
    Write-Host '============================================================' -ForegroundColor Green
    Write-Host ''

    if ($VisionState.Pass -and $ScanState.Pass) {
        Write-Good 'UNIFIED READY'
        Write-Good 'Vision 8000 = PASS'
        Write-Good 'Scan 8030 = PASS'
    } elseif ($VisionState.Pass) {
        Write-Warn 'PARTIAL READY'
        Write-Warn '追人链路仍可使用；扫描上传暂不可用。'
    } elseif ($ScanState.Pass) {
        Write-Warn 'PARTIAL READY'
        Write-Warn '扫描上传仍可使用；追人链路暂不可用。'
    } else {
        Write-Bad 'NOT READY：Vision 8000 与 Scan 8030 均不可用。'
    }
}

function Invoke-SelfTest {
    Write-Step '统一启动器静态自检'
    Assert-Path -Path $script:RepoRoot -Description 'ESP-Claw 仓库' -Container
    Assert-Path -Path $script:VisionRoot -Description '8000 服务工程' -Container
    Assert-Path -Path $script:PipelineRoot -Description '8030 pipeline 根目录' -Container
    Assert-Path -Path $script:ScanScript -Description '8030 scan service 脚本'
    Assert-Path -Path $script:PreflightScript -Description '本地模型预检脚本'
    Assert-Path -Path $script:Python -Description '服务 Python'
    Assert-Path -Path $script:WorkerPython -Description 'pipeline worker Python'
    foreach ($modelPath in @(
        (Join-Path $script:VisionRoot 'models\yolo11n.pt'),
        (Join-Path $script:VisionRoot 'models\identity\best_zxl_identity.pt'),
        (Join-Path $script:VisionRoot 'models\identity\yolo11n-cls.pt')
    )) {
        Assert-Path -Path $modelPath -Description 'Vision 模型'
    }
    $visionMock = [pscustomobject]@{
        ExecutablePath = $script:Python
        CommandLine = "`"$script:Python`" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"
    }
    $scanMock = [pscustomobject]@{
        ExecutablePath = $script:Python
        CommandLine = "`"$script:Python`" `"$script:ScanScript`" --host 0.0.0.0 --port 8030"
    }
    $unrelatedMock = [pscustomobject]@{
        ExecutablePath = $script:Python
        CommandLine = "`"$script:Python`" -m uvicorn other.main:app --port 8000"
    }
    if (-not (Test-ExpectedProcess -Process $visionMock -Service Vision)) {
        throw 'Vision 进程签名自检失败。'
    }
    if (-not (Test-ExpectedProcess -Process $scanMock -Service Scan)) {
        throw 'Scan 进程签名自检失败。'
    }
    if (Test-ExpectedProcess -Process $unrelatedMock -Service Vision) {
        throw '无关进程被误识别为 Vision。'
    }
    $performanceDegradedMock = [pscustomobject]@{
        status = 'ok'
        schema_version = 'deltar_scan_service_health.v1'
        pipeline_version = 'self-test'
        limits = [pscustomobject]@{}
        local_runtime = [pscustomobject]@{ ready = $true; runtime_network_required = $false }
        performance_guard = [pscustomobject]@{ status = 'degraded'; ready = $false }
        warnings = @('performance_canary_slow')
    }
    $workerFailedMock = [pscustomobject]@{
        status = 'runtime_not_ready'
        schema_version = 'deltar_scan_service_health.v1'
        pipeline_version = 'self-test'
        limits = [pscustomobject]@{}
        local_runtime = [pscustomobject]@{ ready = $true; runtime_network_required = $false }
    }
    if (-not (Test-ScanHealthObject -Health $performanceDegradedMock)) {
        throw '性能退化不应阻断 Scan functional health。'
    }
    if (Test-ScanHealthObject -Health $workerFailedMock) {
        throw 'worker/runtime 失败必须阻断 Scan functional health。'
    }
    $diagnostic = Format-ScanEndpointDiagnostic -Name 'local' -StatusCode 200 `
        -JsonPass $true -HealthPass $true -HealthStatus 'ok'
    if ($diagnostic -ne 'Scan local HTTP=200，JSON=PASS，health=PASS，status=ok') {
        throw 'Scan JSON/health 分层诊断格式自检失败。'
    }
    Write-Good '进程签名自检通过；启动逻辑只处理 8000/8030 的实际监听 PID，不批量关闭 Python。'
    Write-Good 'Scan health 分层自检通过；性能告警放行，功能 runtime 失败仍阻断。'
    $source = Get-Content -LiteralPath $PSCommandPath -Raw -Encoding UTF8
    if (($source | Select-String -Pattern 'Start-Process\s+-FilePath\s+\$script:ConsoleUrl' -AllMatches).Matches.Count -ne 1) {
        throw '统一主页面必须且只能有一个打开点。'
    }
    $forbiddenOption = '--auto-open-' + 'visualization'
    if ($source -match [regex]::Escape($forbiddenOption)) {
        throw 'Scan 服务不得再自动打开单独结果页。'
    }
    Write-Good '统一主页面打开点唯一，且 Scan 结果页自动打开已禁用。'
    Write-Good '自检不启动、不停止任何服务。'
}

if ($VisionOnly -and $ScanOnly) {
    Write-Bad '-VisionOnly 与 -ScanOnly 不能同时使用。'
    exit 2
}

$visionRequested = -not $ScanOnly
$scanRequested = -not $VisionOnly
$visionState = New-ServiceState -Name Vision -Port 8000
$scanState = New-ServiceState -Name Scan -Port 8030
$p4IP = ''

try {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetFolderPath('LocalApplicationData'))) {
        throw 'LOCALAPPDATA 不可用，无法创建隔离状态目录。'
    }

    if ($SelfTest) {
        Invoke-SelfTest
        exit 0
    }

    New-Item -ItemType Directory -Path $script:RuntimeDir -Force | Out-Null
    $script:PreviousStatus = Read-JsonFile -Path $script:StatusPath

    Write-Step 'ESP-Claw PC 双服务统一启动器'
    Write-Info "ESP-Claw 仓库：$script:RepoRoot"
    Write-Info "8000 服务工程：$script:VisionRoot"
    Write-Info "8030 scan service：$script:ScanScript"
    Write-Info "Python：$script:Python"
    Write-Info "运行日志与状态目录：$script:RuntimeDir"
    Write-Warn '正在扫描、分析或训练时不要重启 PC 服务；正常启动会强制重启 8000/8030。'

    Write-Step '路径和环境检查'
    Assert-Path -Path $script:RepoRoot -Description 'ESP-Claw 仓库' -Container
    Assert-Path -Path $script:VisionRoot -Description '8000 服务工程' -Container
    Assert-Path -Path $script:PipelineRoot -Description '8030 pipeline 根目录' -Container
    Assert-Path -Path $script:ScanScript -Description '8030 scan service 脚本'
    Assert-Path -Path $script:Python -Description '服务 Python'
    Assert-Path -Path $script:WorkerPython -Description 'pipeline worker Python'
    foreach ($modelPath in @(
        (Join-Path $script:VisionRoot 'models\yolo11n.pt'),
        (Join-Path $script:VisionRoot 'models\identity\best_zxl_identity.pt'),
        (Join-Path $script:VisionRoot 'models\identity\yolo11n-cls.pt')
    )) {
        Assert-Path -Path $modelPath -Description 'Vision 模型'
    }

    $hotspot = @(Get-HotspotEntry)
    if ($hotspot.Count -gt 0) {
        Write-Good "热点网卡：$($hotspot[0].InterfaceAlias)，IPv4=$($hotspot[0].IPAddress)/$($hotspot[0].PrefixLength)"
    } else {
        Write-Warn "未检测到 $script:HotspotIP。Vision 可独立启动；Scan 按正式命令绑定该地址时预计会失败。"
    }

    Write-Status -StateName 'validating' -VisionState $visionState -ScanState $scanState `
        -Message '正在检查端口和服务'

    if ($CheckOnly) {
        if ($visionRequested) {
            $visionState.Validation = Get-VisionValidation
            $visionState.Pass = [bool]$visionState.Validation.Pass
        }
        if ($scanRequested) {
            $scanState.Validation = Get-ScanValidation
            $scanState.Pass = [bool]$scanState.Validation.Pass
        }
    } else {
        if ($scanRequested) {
            Invoke-LocalModelPreflight
        }
        if ($visionRequested) {
            Start-ServiceSafely -Service Vision -State $visionState
        }
        if ($scanRequested) {
            Start-ServiceSafely -Service Scan -State $scanState
        }
    }

    if ($visionState.Validation) {
        Write-Info "Vision health HTTP=$($visionState.Validation.HealthStatusCode)，health=$($visionState.Validation.HealthPass)，OpenAPI person-follow=$($visionState.Validation.RoutePass)，person-follow-view HTTP=$($visionState.Validation.ViewStatusCode)"
    }
    if ($scanState.Validation) {
        Write-Info (Format-ScanEndpointDiagnostic -Name 'local' `
            -StatusCode $scanState.Validation.LocalStatusCode `
            -JsonPass $scanState.Validation.LocalJsonPass `
            -HealthPass $scanState.Validation.LocalPass `
            -HealthStatus $scanState.Validation.LocalHealthStatus)
        Write-Info (Format-ScanEndpointDiagnostic -Name 'hotspot' `
            -StatusCode $scanState.Validation.HotspotStatusCode `
            -JsonPass $scanState.Validation.HotspotJsonPass `
            -HealthPass $scanState.Validation.HotspotPass `
            -HealthStatus $scanState.Validation.HotspotHealthStatus)
        Write-ScanPerformanceWarning -Validation $scanState.Validation
    }

    $anyRequestedPass = ($visionRequested -and $visionState.Pass) -or ($scanRequested -and $scanState.Pass)
    if (-not $NoP4Discovery -and -not $CheckOnly -and $anyRequestedPass) {
        Write-Step 'P4 只读自动发现'
        $p4IP = Find-P4ReadOnly
        if ($p4IP) {
            Write-Good "P4 已发现：$p4IP"
        } else {
            Write-Warn 'P4 尚未连接热点；这不影响 PC 服务 READY/PARTIAL READY。'
            Write-Info "正常运行时每 $P4RetrySeconds 秒进行一次有界只读重试。"
        }
    } elseif ($NoP4Discovery) {
        Write-Info '已按 -NoP4Discovery 跳过 P4 发现。'
    }

    Write-ReadyBanner -VisionState $visionState -ScanState $scanState -P4IP $p4IP

    if (-not $CheckOnly -and -not $ValidationMode -and
        $visionRequested -and $scanRequested -and
        $visionState.Pass -and $scanState.Pass) {
        Write-Step '打开统一主页面'
        Start-Process -FilePath $script:ConsoleUrl
        Write-Good "已打开一次：$script:ConsoleUrl"
    }

    $bothRequestedFailed = (-not $visionRequested -or -not $visionState.Pass) -and
        (-not $scanRequested -or -not $scanState.Pass)
    if ($bothRequestedFailed) {
        $script:ExitCode = 1
    }

    $stateName = if ($visionState.Pass -and $scanState.Pass) {
        'unified_ready'
    } elseif ($visionState.Pass -or $scanState.Pass) {
        'partial_ready'
    } else {
        'not_ready'
    }
    Write-Status -StateName $stateName -VisionState $visionState -ScanState $scanState `
        -P4IP $p4IP -Message $stateName

    if (-not $CheckOnly -and -not $ValidationMode -and $anyRequestedPass) {
        Write-Step '运行中'
        Write-Info '按 Ctrl+C 退出；只清理本轮强制重启后实际启动的子进程。'
        $nextP4Retry = (Get-Date).AddSeconds([Math]::Max(30, $P4RetrySeconds))
        while ($true) {
            foreach ($serviceState in @($visionState, $scanState)) {
                if ($serviceState.StartedByThisRun -and $serviceState.Pass -and
                    $serviceState.OwnedProcess.HasExited) {
                    $serviceState.Pass = $false
                    $serviceState.Error = "$($serviceState.Name) PID=$($serviceState.Pid) 意外退出，退出码=$($serviceState.OwnedProcess.ExitCode)"
                    Write-Bad $serviceState.Error
                }
            }
            if (-not $NoP4Discovery -and -not $p4IP -and (Get-Date) -ge $nextP4Retry) {
                Write-Info '再次只读查找 P4……'
                $p4IP = Find-P4ReadOnly
                if ($p4IP) {
                    Write-Good "P4 已发现：$p4IP"
                } else {
                    Write-Warn '仍未发现 P4；PC 服务继续运行。'
                }
                $nextP4Retry = (Get-Date).AddSeconds([Math]::Max(30, $P4RetrySeconds))
            }
            Start-Sleep -Seconds 1
        }
    }
} catch {
    $script:ExitCode = 1
    Write-Bad $_.Exception.Message
} finally {
    if (-not $SelfTest -and -not $CheckOnly) {
        Write-Step '退出清理'
        Stop-OwnedService -State $scanState -Reason 'launcher_exit'
        Stop-OwnedService -State $visionState -Reason 'launcher_exit'
        try {
            Write-Status -StateName 'stopped' -VisionState $visionState -ScanState $scanState `
                -P4IP $p4IP -Message '启动器已退出并完成归属内清理'
        } catch {
            Write-Bad "最终状态保存失败：$($_.Exception.Message)"
            $script:ExitCode = 1
        }
    }
}

exit $script:ExitCode
