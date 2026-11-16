# Stage3A Handoff

Stage3A has not started. This handoff is intentionally world-parameter-free.

## Preconditions

1. Camera↔ToF bracket and vehicle mounting are in final mechanical configuration.
2. Fasteners are witness-marked; adhesive has completed the manufacturer's cure interval; no subsequent adjustment is planned.
3. Stage2 decision owner has explicitly accepted the Stage2C evidence or requested and completed additional validation.
4. The exact Stage2 bundle SHA is `63c974cd2ff64dd0f135d47d8c92eb170d06658cb315563fb93588862ba8b057` and every Stage3A capture manifest records it.
5. Old Stage7C `C:\calibration\stage7c_world_result_robustfix_20260724_044035\camera_world_pose.v1.json` (`5c730171f850ff3e05ab777eecca9849714aa20376c6d1f001da2b378ad24619`) is blocked from reuse.

## Inputs to Stage3A

- This Stage2 bundle and its SHA file.
- Fixed camera raster/K/D and Camera↔ToF raw-zone/axial-z/transform authority.
- A new board/ground/world capture set collected only after the mechanical preconditions.
- Environmental and fixture measurements with units and uncertainty.

## Required Stage3A outputs

- New immutable camera-world pose with explicit direction, convention, units, covariance/uncertainty and fit/held-out evidence.
- Source scan/pose IDs, file hashes, board/ground definition, time-sync diagnostics and protected-hash proof.
- A versioned formal binding proposal that references this Stage2 bundle SHA; no overwriting of old Stage7C.
- Reprojection, plane, height, multi-pose stability and downstream Geometry acceptance results.

## Stop conditions

Stop and reacquire if the rig moves, cure is incomplete, the bundle hash differs, the camera mode/resolution changes, timestamps violate the gate, or any attempt is made to initialize from/reuse old Stage7C as truth.

## Readiness verdict

`CONDITIONALLY_READY_AFTER_MECHANICAL_FIXATION_AND_GLUE_CURE`: Stage2 evidence is assembled and internally consistent. Entry also requires explicit owner disposition of the Stage2C degradation; this bundle itself does not make that promotion decision.
