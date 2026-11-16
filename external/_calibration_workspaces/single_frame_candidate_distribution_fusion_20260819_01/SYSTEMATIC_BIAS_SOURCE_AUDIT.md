# Systematic Bias Source Audit

This audit is intentionally bounded because the fusion gate failed. Stable-center bias is not claimed solved and no calibration was fitted.

## Evidence

- The frozen method uses only evidence-derived weights; truth was loaded after the method/output hashes were frozen.
- Several frame/layer correct-neighborhood hypothesis rates are below the required majority threshold. In those frames a selector/fusion method cannot recover support evidence that candidate generation did not expose in the relevant spatial context.
- Remaining offsets vary with candidate axial depth, raw/weak ToF regimes, and surface-generation height bands. This is consistent with upstream depth/world-Z/surface-generation bias or missing-evidence behavior, not a single generic offset proven by the current six scans.
- No linear scale/offset, depth-dependent correction, zone correction, or class/scene correction was fitted. Cross-validation evidence is insufficient.

## Next bounded investigation

Audit raw ToF range/status/zone pattern, DELTAR-D depth residual versus distance, Stage2 projection residual, Stage3A world-Z residual, and surface-generation component centers on multiple distances. Keep Stage2, DELTAR-D, Stage3A, and firmware frozen unless a concrete bug is independently demonstrated.

`SYSTEMATIC_BIAS_SOURCE_AUDIT_COMPLETE = true`  
`GENERIC_CALIBRATION_IMPLEMENTED = false`
