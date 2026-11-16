$ErrorActionPreference = 'Stop'
& 'C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe' 'C:\ec\_calibration_workspaces\stage3b_competition_geometry_20260818_01\candidate_geometry_v2\stage3b_all_object_geometry_v2.py' summary
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
