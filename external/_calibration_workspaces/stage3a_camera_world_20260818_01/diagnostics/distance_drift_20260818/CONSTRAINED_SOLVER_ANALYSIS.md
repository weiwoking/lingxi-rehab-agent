# Constrained solver analysis

The preferred physically matched model has 11 parameters for 1344 scalar image observations: two shared camera gravity components, plus per-pose vehicle/board relative yaw and XY translation. K/D, 972 mm camera height, 912 mm active-bottom height, vertical-board orientation, and 175×125 mm geometry are fixed.

Shared yaw is a gauge and is set to zero; the three relative yaws are nuisance values. Absolute vehicle-forward yaw is not observable from these data because vehicle-to-cabinet alignment was not measured. Camera height and board height are not fitted.

All-corner constrained reprojection: RMS **2.3142 px**, median **0.8162 px**, p95 **4.9772 px**—far above the unconstrained ~0.154 px frame RMS. Even the diagnostic 101-parameter variant with per-frame yaw/XY nuisance remains near 0.98 px RMS. Shared gravity orientation in the zero-yaw gauge: `{"roll_deg": 0.31066921976438244, "pitch_deg": -2.898925369693367, "yaw_deg": -0.007860973964490776}`.

The fixed-height residual is zero by construction and is not claimed as independent validation. Evidence comes from the small image-space penalty versus unconstrained PnP, cross-distance common gravity, and leave-one-pose-out fits where the held-out pose refits only its nuisance yaw/XY. Full LOPO results are in `CONSTRAINED_SOLVER_RESULT.json`.

Observability:

- Observable: two gravity components (roll/pitch) given fixed K/D, board verticality, board/camera heights, and multi-distance image observations.
- Observable per pose: relative board yaw and in-plane vehicle translation under the chosen yaw gauge.
- Not observable: absolute vehicle-forward yaw, global horizontal origin, or independent validation of the two heights after they are imposed.

Verdict: **reject solver integration on the present evidence**. The hard-constraint model does not reproduce the raw image observations closely enough, and its zero height residual is tautological. The production Stage3A solver remains unchanged; no candidate is exported or promoted. The constrained fits are retained only as falsification/observability diagnostics under `CANDIDATE_NOT_PROMOTED`.
