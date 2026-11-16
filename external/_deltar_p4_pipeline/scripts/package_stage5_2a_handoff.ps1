param(
    [string]$Repository = 'C:\ec\_deltar_p4_pipeline',
    [string]$Downloads = 'C:\Users\Weiwo\Downloads',
    [string]$TemporaryRoot = 'C:\ec\_codex_tmp'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath $Repository).Path
$tempRoot = (Resolve-Path -LiteralPath $TemporaryRoot).Path
$stage = Join-Path $tempRoot 'stage5_2a_handoff_pack_20260718_01'
if (Test-Path -LiteralPath $stage) {
    throw "Staging path already exists; refusing to overwrite: $stage"
}
New-Item -ItemType Directory -Path $stage | Out-Null

try {
    foreach ($name in @('README.md', 'pyproject.toml', 'pytest.ini')) {
        Copy-Item -LiteralPath (Join-Path $repo $name) -Destination (Join-Path $stage $name)
    }
    $forbiddenExtensions = @('.pyc', '.pt', '.pth', '.ckpt', '.png', '.npy', '.npz', '.rgb565', '.h5', '.hdf5')
    foreach ($rootName in @('src', 'workers', 'scripts', 'tests', 'docs')) {
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

    $outputNames = @(
        'official_dataset_audit.json', 'yolo_object_manifest.json', 'matched_object_manifest.json',
        'official_zone_mapping.json', 'old_chain_results.json', 'new_chain_results.json',
        'paired_results.json', 'ablation_matrix.json', 'class_summary.json', 'size_summary.json',
        'distance_summary.json', 'mixed_zone_summary.json', 'ten_percent_acceptance.json',
        'old_vs_new_summary.json', 'stage5_2a_acceptance.json'
    )
    $outputDestination = Join-Path $stage 'outputs_stage5_2a'
    New-Item -ItemType Directory -Path $outputDestination | Out-Null
    foreach ($name in $outputNames) {
        Copy-Item -LiteralPath (Join-Path $repo "outputs_stage5_2a\$name") -Destination (Join-Path $outputDestination $name)
    }

    $base = 'DELTAR_STAGE5_2A_HANDOFF'
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
            $_ -match '(^|/)(samples|datasets_external|__pycache__|\.pytest_cache)(/|$)' -or
            $_ -match '\.(pt|pth|ckpt|png|npy|npz|rgb565|h5|hdf5|pyc)$'
        })
        $required = @('README.md', 'pyproject.toml', 'pytest.ini', 'models/MODEL_MANIFEST.json') +
            @($outputNames | ForEach-Object { "outputs_stage5_2a/$_" })
        $missing = @($required | Where-Object { $_ -notin $entries })
        $prefixMissing = @()
        foreach ($prefix in @('src/', 'workers/', 'scripts/', 'tests/', 'docs/')) {
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
