# Stage3B Competition Geometry Validation

This workspace is validation-only. It does not modify or promote formal runtime, firmware, Stage2, Stage3A, `C:\calibration`, or the Competition Shelf Stabilizer.

## What the first capture means

The first registered real competition-cabinet scan automatically becomes `PRIMARY_COMPETITION_VIEW`; every later registration becomes `ROBUSTNESS_EXTRA`. Registration is dynamic and unbounded. There is no distance argument, no pose argument, no sample ID argument, and no expected capture count.

For owner operation, make the first scan the main competition view (roughly 2 m is the intended visual setup). If useful, later add a closer robustness view (roughly 1.5 m) and/or a farther stress view (roughly 3 m). Extra views are optional and are never averaged into Primary acceptance. Range is inferred only as a diagnostic.

Do not run registration until a new scan of the fixed competition cabinet, two bottles, and volleyball has completed formal validation. This delivered workspace intentionally has an empty registry.

## No-argument commands

Register the newest receipted formal scan, automatically assign its role, analyze all 11 frames, and show the sample result:

```powershell
& 'C:\ec\_calibration_workspaces\stage3b_competition_geometry_20260818_01\wrappers\REGISTER_AND_ANALYZE_LATEST.ps1'
```

Show Primary separately from optional robustness samples:

```powershell
& 'C:\ec\_calibration_workspaces\stage3b_competition_geometry_20260818_01\wrappers\SUMMARY.ps1'
```

Status and compact result export are also no-argument:

```powershell
& 'C:\ec\_calibration_workspaces\stage3b_competition_geometry_20260818_01\wrappers\STATUS.ps1'
& 'C:\ec\_calibration_workspaces\stage3b_competition_geometry_20260818_01\wrappers\EXPORT_RESULTS.ps1'
```

Duplicate registration fails closed. A failed analysis remains registered and can be retried with the tool's no-value `analyze` subcommand; every replay uses a new attempt directory and preserves prior evidence.

## Output semantics

- Primary authority: `RAW_GEOMETRY_BEFORE_STABILIZER`.
- `support_source` remains separated (`matched_plane` versus `point_p5_fallback`). Bbox fallback is reported as a region source/method, not relabeled as a plane.
- `world_z_authority=Stage3A gravity candidate`.
- `horizontal_world_authority=NOT_VALIDATED`; no yaw/full world rotation is fabricated.
- Stabilized values are secondary only.
- Each target includes median, p05/p95, RMS, valid-frame count, source consistency, signed/absolute mm error, percent error, and truth uncertainty.
- `UNRESOLVED_TARGET_MATCH` is explicit and preserves candidates.

The preferred bands (support <=20 mm, object height <=5%) and competition-acceptable bands (support <=50 mm, object height <=10%) are engineering interpretation bands, not the only hard gate. Sign errors, non-positive heights, or errors in the hundreds of millimetres are separately classified as catastrophic geometry.
