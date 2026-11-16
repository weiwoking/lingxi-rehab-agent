param(
    [string]$Repository = 'C:\ec\_deltar_p4_pipeline',
    [string]$Downloads = 'C:\Users\Weiwo\Downloads',
    [string]$TemporaryRoot = 'C:\ec\_codex_tmp'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath $Repository).Path
$tempRoot = (Resolve-Path -LiteralPath $TemporaryRoot).Path
$stage = Join-Path $tempRoot ("stage5_2b_handoff_pack_" + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $stage) {
    throw "Staging path unexpectedly exists: $stage"
}
New-Item -ItemType Directory -Path $stage | Out-Null

try {
    foreach ($name in @('README.md', 'pyproject.toml', 'pytest.ini')) {
        Copy-Item -LiteralPath (Join-Path $repo $name) -Destination (Join-Path $stage $name)
    }
    $forbiddenExtensions = @('.pyc', '.pt', '.pth', '.ckpt', '.png', '.npy', '.npz', '.rgb565', '.h5', '.hdf5')
    foreach ($rootName in @('src', 'scripts', 'tests', 'docs')) {
        $sourceRoot = Join-Path $repo $rootName
        Get-ChildItem -LiteralPath $sourceRoot -Recurse -File |
            Where-Object {
                $_.FullName -notmatch '[\\/](__pycache__|\.pytest_cache)[\\/]' -and
                $_.Extension.ToLowerInvariant() -notin $forbiddenExtensions
            } |
            ForEach-Object {
                $relative = $_.FullName.Substring($repo.Length).TrimStart('\')
                $destination = Join-Path $stage $relative
                New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
                Copy-Item -LiteralPath $_.FullName -Destination $destination
            }
    }

    $modelDestination = Join-Path $stage 'models'
    New-Item -ItemType Directory -Path $modelDestination | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo 'models\MODEL_MANIFEST.json') -Destination (Join-Path $modelDestination 'MODEL_MANIFEST.json')

    $stage52aNames = @('ten_percent_acceptance.json', 'old_vs_new_summary.json', 'distance_summary.json', 'class_summary.json')
    $stage52bNames = @(
        'hardware_spec_audit.json', 'distance_band_analysis.json', 'tof_coverage_table.json',
        'scan_distance_recommendation.json', 'partial_object_contract.json', 'border_partial_analysis.json',
        'core_class_contract.json', 'core_class_performance.json', 'algorithm_optimization_decision.json',
        'stage6_entry_contract.json', 'stage5_2b_acceptance.json'
    )
    foreach ($spec in @(
        @{Dir='outputs_stage5_2a'; Names=$stage52aNames},
        @{Dir='outputs_stage5_2b'; Names=$stage52bNames}
    )) {
        $destinationRoot = Join-Path $stage $spec.Dir
        New-Item -ItemType Directory -Path $destinationRoot | Out-Null
        foreach ($name in $spec.Names) {
            Copy-Item -LiteralPath (Join-Path $repo "$($spec.Dir)\$name") -Destination (Join-Path $destinationRoot $name)
        }
    }

    $base = 'DELTAR_STAGE5_2B_HANDOFF'
    $zip = Join-Path $Downloads "$base.zip"
    $index = 2
    while (Test-Path -LiteralPath $zip) {
        $zip = Join-Path $Downloads ("{0}_{1:D2}.zip" -f $base, $index)
        $index++
    }
    Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($zip)
    try {
        $entries = @($archive.Entries | Where-Object { $_.Name -ne '' } | ForEach-Object { $_.FullName.Replace('\', '/') })
        $forbidden = @($entries | Where-Object {
            $_ -match '(^|/)(samples|datasets_external|docs_external|__pycache__|\.pytest_cache)(/|$)' -or
            $_ -match '\.(pt|pth|ckpt|pdf|png|npy|npz|rgb565|h5|hdf5|pyc)$'
        })
        $required = @('README.md', 'pyproject.toml', 'pytest.ini', 'models/MODEL_MANIFEST.json') +
            @($stage52aNames | ForEach-Object { "outputs_stage5_2a/$_" }) +
            @($stage52bNames | ForEach-Object { "outputs_stage5_2b/$_" })
        $missing = @($required | Where-Object { $_ -notin $entries })
        $prefixMissing = @()
        foreach ($prefix in @('src/', 'scripts/', 'tests/', 'docs/')) {
            if (-not @($entries | Where-Object { $_.StartsWith($prefix) }).Count) {
                $prefixMissing += $prefix
            }
        }
    }
    finally {
        $archive.Dispose()
    }

    $result = [PSCustomObject]@{
        Zip = $zip
        Entries = $entries.Count
        Bytes = (Get-Item -LiteralPath $zip).Length
        SHA256 = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
        ForbiddenCount = $forbidden.Count
        Forbidden = $forbidden
        MissingCount = $missing.Count + $prefixMissing.Count
        Missing = @($missing) + @($prefixMissing)
    }
    if ($result.ForbiddenCount -ne 0 -or $result.MissingCount -ne 0) {
        throw "ZIP audit failed: $($result | ConvertTo-Json -Depth 5 -Compress)"
    }
    $result | ConvertTo-Json -Depth 5
}
finally {
    if (Test-Path -LiteralPath $stage) {
        $stageResolved = (Resolve-Path -LiteralPath $stage).Path
        if (-not $stageResolved.StartsWith($tempRoot + '\') -or $stageResolved -eq $tempRoot) {
            throw "Unsafe staging cleanup target: $stageResolved"
        }
        Remove-Item -LiteralPath $stageResolved -Recurse -Force
    }
}
