$ErrorActionPreference = 'Stop'
$Python = 'C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
$Runner = 'C:\ec\_calibration_workspaces\stage3b_competition_geometry_20260818_01\candidate_geometry\stage3b_geometry_candidate.py'
& $Python $Runner replay-all
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
