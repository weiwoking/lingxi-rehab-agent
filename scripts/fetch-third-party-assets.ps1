# Restore local-only runtime dependencies directly from their official owners.
# Download access does not grant permission to redistribute these assets.
[CmdletBinding()]
param(
    [ValidateSet('DELTAR', 'QGP', 'WitMotion')]
    [string[]]$Components = @('DELTAR', 'QGP', 'WitMotion'),
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$manifest = Get-Content -LiteralPath (Join-Path $root 'third_party/runtime-assets.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.schema_version -ne 1) { throw 'Unsupported runtime asset manifest.' }
$assets = @($manifest.assets | Where-Object { $_.component -in $Components })
$pending = @()
$skipped = 0
foreach ($asset in $assets) {
    $target = [IO.Path]::GetFullPath((Join-Path $root $asset.path))
    if (-not $target.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Asset path escapes project root: $($asset.path)"
    }
    if (Test-Path -LiteralPath $target) {
        $existing = Get-Item -LiteralPath $target -Force
        if ($existing.PSIsContainer -or ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing non-regular existing target: $($asset.path)"
        }
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ine $asset.sha256) {
            throw "Existing file differs; preserved without overwrite: $($asset.path)"
        }
        Write-Output "VERIFIED/SKIP $($asset.path)"
        $skipped++
    } else {
        $pending += [pscustomobject]@{ Asset = $asset; Target = $target }
    }
}
if ($CheckOnly) {
    foreach ($item in $pending) { Write-Output "MISSING $($item.Asset.path)" }
    if ($pending.Count) { throw "$($pending.Count) required files missing. Run this script without -CheckOnly." }
    Write-Output "PASS: $skipped files verified; no downloads or services started."
    return
}
if (-not $pending.Count) {
    Write-Output "PASS: $skipped files verified; nothing to download."
    return
}
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$stage = Join-Path $tempBase ('rehab-third-party-' + [Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($stage) | Out-Null
$downloads = @{}
$prepared = @()
try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    foreach ($item in $pending) {
        $asset = $item.Asset
        if (-not $downloads.ContainsKey($asset.url)) {
            $download = Join-Path $stage ('download-' + $downloads.Count)
            Write-Output "DOWNLOAD $($asset.component) from official source"
            Invoke-WebRequest -Uri $asset.url -OutFile $download -UseBasicParsing -TimeoutSec 300
            $downloads[$asset.url] = $download
        }
        $download = $downloads[$asset.url]
        if ($asset.archive_sha256) {
            if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ine $asset.archive_sha256) {
                throw 'Official archive hash changed; nothing installed. Review upstream before updating the manifest.'
            }
            $preparedPath = Join-Path $stage ('member-' + $prepared.Count)
            $archive = [IO.Compression.ZipFile]::OpenRead($download)
            try {
                $entry = $archive.GetEntry($asset.member)
                if ($null -eq $entry) { throw "Pinned archive member missing: $($asset.member)" }
                $inputStream = $entry.Open()
                try {
                    $outputStream = [IO.File]::Open($preparedPath, [IO.FileMode]::CreateNew)
                    try { $inputStream.CopyTo($outputStream) } finally { $outputStream.Dispose() }
                } finally { $inputStream.Dispose() }
            } finally { $archive.Dispose() }
        } else {
            $preparedPath = $download
        }
        if ((Get-FileHash -LiteralPath $preparedPath -Algorithm SHA256).Hash -ine $asset.sha256) {
            throw "Downloaded file hash mismatch; nothing installed: $($asset.path). A login/quota HTML response is not an asset."
        }
        $prepared += [pscustomobject]@{ Target = $item.Target; Source = $preparedPath; Asset = $asset }
    }
    # All downloads/members pass before any project file is installed.
    foreach ($item in $prepared) {
        $parent = [IO.Path]::GetDirectoryName($item.Target)
        $ancestor = $parent
        while ($ancestor.Length -gt $root.Length) {
            if ((Test-Path -LiteralPath $ancestor) -and ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Refusing a linked destination directory: $ancestor"
            }
            $ancestor = [IO.Path]::GetDirectoryName($ancestor)
        }
        [IO.Directory]::CreateDirectory($parent) | Out-Null
        # CreateNew is also a final no-overwrite check against concurrent writers.
        $outputStream = [IO.File]::Open($item.Target, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try {
            $inputStream = [IO.File]::OpenRead($item.Source)
            try { $inputStream.CopyTo($outputStream) } finally { $inputStream.Dispose() }
        } finally { $outputStream.Dispose() }
        if ((Get-FileHash -LiteralPath $item.Target -Algorithm SHA256).Hash -ine $item.Asset.sha256) {
            throw "Post-install verification failed: $($item.Asset.path)"
        }
        Write-Output "RESTORED $($item.Asset.path)"
    }
    Write-Output "PASS: restored $($prepared.Count) missing files; skipped $skipped verified files."
} finally {
    # Only this invocation's generated staging directory is cleaned up.
    $resolvedStage = [IO.Path]::GetFullPath($stage)
    if ($resolvedStage.StartsWith($tempBase.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -and
        [IO.Path]::GetFileName($resolvedStage).StartsWith('rehab-third-party-')) {
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}
