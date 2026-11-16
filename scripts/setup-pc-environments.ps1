[CmdletBinding()]
param(
    [ValidateSet('all', 'deltar', 'score', 'vision', 'scan_yolo', 'tts')]
    [string[]]$Environment = @('all'),
    [switch]$Plan,
    [switch]$CheckExisting
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$specRoot = Join-Path $projectRoot 'environment'
$specs = Get-Content -LiteralPath (Join-Path $specRoot 'environments.json') -Raw | ConvertFrom-Json
$selected = @($Environment)
if ($selected -contains 'scan_yolo' -and $selected -notcontains 'all') { $selected += 'deltar' }
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:CONDA_AUTO_UPDATE_CONDA = 'false'

function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Command" }
}

function Assert-PythonVersion {
    param([string]$Command, [string[]]$PrefixArguments, [string]$Expected)
    $actual = & $Command @PrefixArguments -B -c 'import platform; print(platform.python_version())'
    if ($LASTEXITCODE -ne 0 -or ($actual | Out-String).Trim() -ne $Expected) {
        throw "Required Python $Expected; observed $actual"
    }
}

function Test-EnvironmentHealth {
    param($Spec, [string]$Python)
    Assert-PythonVersion -Command $Python -PrefixArguments @() -Expected $Spec.python_version
    Invoke-Checked -Command $Python -Arguments @('-B', (Join-Path $PSScriptRoot 'check-pc-environment.py'), '--root', $projectRoot, '--environment', $Spec.name)
    Invoke-Checked -Command $Python -Arguments @('-B', '-m', 'pip', 'check')
    Write-Output "PASS $($Spec.name): version, pinned packages, critical imports and pip check"
}

$failed = $false
foreach ($spec in $specs) {
    if ($selected -notcontains 'all' -and $selected -notcontains $spec.name) { continue }
    $target = [IO.Path]::GetFullPath((Join-Path $projectRoot $spec.original_relative_path))
    if (-not $target.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Environment target escapes project root'
    }
    $python = Join-Path $projectRoot $spec.python_relative_path
    $specDirectory = Join-Path $specRoot $spec.name
    if ($Plan) {
        $action = if (Test-Path -LiteralPath $target) { if ($CheckExisting) { 'CHECK_EXISTING' } else { 'SKIP_EXISTING' } } else { 'CREATE_PINNED' }
        Write-Output "PLAN $($spec.name): $action [$($spec.environment_type), Python $($spec.python_version)] -> $target"
        continue
    }
    try {
        if (Test-Path -LiteralPath $target) {
            if ($CheckExisting) { Test-EnvironmentHealth -Spec $spec -Python $python }
            else { Write-Output "SKIP_EXISTING $($spec.name): $target" }
            continue
        }
        if ($CheckExisting) { throw 'CheckExisting requires an existing environment; nothing was created' }
        # Check all prerequisites before creating any target directory.
        if ($spec.environment_type -eq 'conda') {
            $conda = Get-Command conda -ErrorAction Stop
            Invoke-Checked -Command $conda.Name -Arguments @('--version')
            Invoke-Checked -Command $conda.Name -Arguments @('create', '--yes', '--prefix', $target, '--file', (Join-Path $specDirectory 'conda-explicit.txt'))
            Invoke-Checked -Command $python -Arguments @('-B', '-m', 'pip', 'install', '--disable-pip-version-check', '--requirement', (Join-Path $specDirectory 'requirements.pip.txt'))
        }
        else {
            if ($spec.base_environment) {
                $baseSpec = @($specs | Where-Object { $_.name -eq $spec.base_environment })[0]
                $basePython = Join-Path $projectRoot $baseSpec.python_relative_path
                $baseArguments = @()
            }
            else {
                $basePython = (Get-Command py -ErrorAction Stop).Source
                $minor = ($spec.python_version -split '\.')[0..1] -join '.'
                $baseArguments = @("-$minor")
            }
            Assert-PythonVersion -Command $basePython -PrefixArguments $baseArguments -Expected $spec.python_version
            Invoke-Checked -Command $basePython -Arguments @($baseArguments + @('-B', '-m', 'venv', '--copies', $target))
            Invoke-Checked -Command $python -Arguments @('-B', '-m', 'pip', 'install', '--disable-pip-version-check', '--requirement', (Join-Path $specDirectory 'requirements.bootstrap.txt'))
            Invoke-Checked -Command $python -Arguments @('-B', '-m', 'pip', 'install', '--disable-pip-version-check', '--requirement', (Join-Path $specDirectory 'requirements.freeze.txt'))
        }
        Test-EnvironmentHealth -Spec $spec -Python $python
    }
    catch {
        Write-Output "FAIL $($spec.name): $($_.Exception.Message)"
        $failed = $true
    }
}
if ($failed) { exit 1 }
Write-Output 'PASS setup-pc-environments (existing directories are never overwritten)'
