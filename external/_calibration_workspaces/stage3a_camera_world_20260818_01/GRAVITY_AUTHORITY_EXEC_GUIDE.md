# Stage3A W04G gravity-authority execution guide

This tool never captures a scan. Registration is a separate, explicit command after the user has captured the one permitted W04G group.

## Before capture

Use one exported preview frame with the existing read-only scorer:

```powershell
Set-Location 'C:\ec\_calibration_workspaces\stage3a_camera_world_20260818_01'
python .\stage3a_oblique_gravity_score.py score-frame --image <image-or-rgb565>
```

Keep the board fixed, place the vehicle at roughly middle distance, shift the whole vehicle sideways, and aim back so the full board has margin. Follow only the scorer categories. Capture one 11-frame scan only after `GOOD`.

## Explicit execution sequence after the one scan exists

Prefer the exact scan path:

```powershell
python .\stage3a_gravity_authority.py register-w04g --scan <validated-scan-path>
python .\stage3a_gravity_authority.py inspect-w04g
python .\stage3a_gravity_authority.py extract-gravity
python .\stage3a_gravity_authority.py validate-cross-distance
python .\stage3a_gravity_authority.py summary
```

`--latest` exists only for an explicit user invocation. No status, summary, inspect, extraction, or validation command invokes it implicitly.

If summary reports `READY_FOR_STAGE3B_Z_VALIDATION`, candidate export is allowed but still explicit:

```powershell
python .\stage3a_gravity_authority.py export-gravity-candidate
```

The exported file remains `CANDIDATE_NOT_PROMOTED`; this tool has no promotion operation.

## Fail-closed behavior

- A second W04G registration or overwrite is refused.
- W01/W02/W03 cannot be rebound as W04G.
- A non-GOOD scan may be registered only as diagnostic evidence and cannot extract gravity.
- Source hashes are verified before inspection, extraction, validation, and export.
- Solver disagreement, frame spread, insufficient inliers, or branch catastrophe prevents gravity readiness.
- A failed cross-distance route cannot export a candidate and recommends no second oblique capture.
- `NEEDS_OWNER_JUDGMENT` also blocks candidate export.

## Authority contract

W04G produces only a normalized `u_up_camera` vector and uncertainty. It never produces full Camera→World rotation authority. World yaw remains `SEPARATE_LOW_CONFIDENCE`.
