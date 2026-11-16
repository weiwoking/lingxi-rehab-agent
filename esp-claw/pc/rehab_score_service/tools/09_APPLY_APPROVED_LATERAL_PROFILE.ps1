param(
  [Parameter(Mandatory=$true)][double]$LfThresholdDeg,
  [Parameter(Mandatory=$true)][double]$LfMinDurationS,
  [Parameter(Mandatory=$true)][double]$TremorBandLowHz,
  [Parameter(Mandatory=$true)][double]$TremorBandHighHz,
  [Parameter(Mandatory=$true)][double]$TremorWindowSeconds,
  [Parameter(Mandatory=$true)][double]$TremorStepSeconds,
  [Parameter(Mandatory=$true)][double]$TremorBandRatioThreshold,
  [Parameter(Mandatory=$true)][double]$TremorBandRmsThreshold,
  [Parameter(Mandatory=$true)][double]$TremorFractionThreshold,
  [Parameter(Mandatory=$true)][string]$CalibrationPath
)

$ErrorActionPreference = "Stop"
$root = "C:\ec\esp-claw\pc\rehab_score_service"
$assetRoot = if ($env:REHAB_SCORING_ASSETS_ROOT) { $env:REHAB_SCORING_ASSETS_ROOT } else { "C:\ec\_rehab_scoring_assets" }
$profilePath = Join-Path $root "feature_extractors\lateral_transfer_profile.py"
$derivedRoot = Join-Path $assetRoot "derived\lateral_transfer_v1\approved_profiles"
Set-Location $root

function Assert-Range {
  param([string]$Name, [double]$Value, [double]$Min, [double]$Max)
  if (($Value -lt $Min) -or ($Value -gt $Max)) {
    throw "INVALID_PARAMETER_RANGE:$Name=$Value expected [$Min,$Max]"
  }
}

Assert-Range "LfThresholdDeg" $LfThresholdDeg 1.0 45.0
Assert-Range "LfMinDurationS" $LfMinDurationS 0.1 5.0
Assert-Range "TremorBandLowHz" $TremorBandLowHz 1.0 10.0
Assert-Range "TremorBandHighHz" $TremorBandHighHz 1.0 12.0
if ($TremorBandHighHz -le $TremorBandLowHz) { throw "INVALID_TREMOR_BAND" }
Assert-Range "TremorWindowSeconds" $TremorWindowSeconds 0.5 5.0
Assert-Range "TremorStepSeconds" $TremorStepSeconds 0.1 2.0
Assert-Range "TremorBandRatioThreshold" $TremorBandRatioThreshold 0.0 1.0
Assert-Range "TremorBandRmsThreshold" $TremorBandRmsThreshold 0.0 2000.0
Assert-Range "TremorFractionThreshold" $TremorFractionThreshold 0.0 1.0
if (!(Test-Path -LiteralPath $CalibrationPath)) { throw "CALIBRATION_PATH_NOT_FOUND:$CalibrationPath" }

$oldProfile = Get-Content -LiteralPath $profilePath -Raw -Encoding UTF8
$newProfile = $oldProfile
$newProfile = [regex]::Replace($newProfile, 'threshold_deg: float = [-0-9.]+', "threshold_deg: float = $LfThresholdDeg")
$newProfile = [regex]::Replace($newProfile, 'min_duration_s: float = [-0-9.]+', "min_duration_s: float = $LfMinDurationS")
$newProfile = [regex]::Replace($newProfile, 'frequency_band_hz: tuple\[float, float\] = \([-0-9.]+, [-0-9.]+\)', "frequency_band_hz: tuple[float, float] = ($TremorBandLowHz, $TremorBandHighHz)")
$newProfile = [regex]::Replace($newProfile, 'window_seconds: float = [-0-9.]+', "window_seconds: float = $TremorWindowSeconds")
$newProfile = [regex]::Replace($newProfile, 'step_seconds: float = [-0-9.]+', "step_seconds: float = $TremorStepSeconds")
$newProfile = [regex]::Replace($newProfile, 'band_ratio_threshold: float = [-0-9.]+', "band_ratio_threshold: float = $TremorBandRatioThreshold")
$newProfile = [regex]::Replace($newProfile, 'band_rms_threshold: float = [-0-9.]+', "band_rms_threshold: float = $TremorBandRmsThreshold")
$newProfile = [regex]::Replace($newProfile, 'fraction_threshold: float = [-0-9.]+', "fraction_threshold: float = $TremorFractionThreshold")

New-Item -ItemType Directory -Force $derivedRoot | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$approvedJson = Join-Path $derivedRoot "lateral_transfer_profile_approved_$stamp.json"
$manifestPath = Join-Path $derivedRoot "approved_profile_manifest_$stamp.json"

$oldHash = (Get-FileHash -LiteralPath $profilePath -Algorithm SHA256).Hash
$calibrationHash = if ((Get-Item -LiteralPath $CalibrationPath).PSIsContainer) {
  "directory:" + (Get-ChildItem -LiteralPath $CalibrationPath -Recurse -File | Sort-Object FullName | ForEach-Object { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash } | Out-String).Trim()
} else {
  (Get-FileHash -LiteralPath $CalibrationPath -Algorithm SHA256).Hash
}

@{
  profile_version = "lateral_transfer_profile_approved_$stamp"
  approved_at = (Get-Date).ToString("o")
  params = @{
    lf_threshold_deg = $LfThresholdDeg
    lf_min_duration_s = $LfMinDurationS
    tremor_band_hz = @($TremorBandLowHz, $TremorBandHighHz)
    tremor_window_seconds = $TremorWindowSeconds
    tremor_step_seconds = $TremorStepSeconds
    tremor_band_ratio_threshold = $TremorBandRatioThreshold
    tremor_band_rms_threshold = $TremorBandRmsThreshold
    tremor_fraction_threshold = $TremorFractionThreshold
  }
  calibration_path = $CalibrationPath
  calibration_hash = $calibrationHash
  old_profile_sha256 = $oldHash
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

@{
  motion_family = "lateral_transfer"
  profile_source = "manual_approved_parameters"
  manifest = $manifestPath
  generated_profile_py_preview_sha256 = ([System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash([System.Text.Encoding]::UTF8.GetBytes($newProfile))).Replace("-", ""))
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $approvedJson -Encoding UTF8

Set-Content -LiteralPath $profilePath -Value $newProfile -Encoding UTF8
$newHash = (Get-FileHash -LiteralPath $profilePath -Algorithm SHA256).Hash

Write-Host "OLD_PROFILE_SHA256=$oldHash"
Write-Host "NEW_PROFILE_SHA256=$newHash"
Write-Host "APPROVED_PROFILE_JSON=$approvedJson"
Write-Host "APPROVED_PROFILE_MANIFEST=$manifestPath"
Write-Host "VALIDATION_RUN=NO"
