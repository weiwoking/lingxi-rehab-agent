# All-Object Geometry v2 Architecture

## Decision

The isolated v2 candidate implements an all-object pipeline with a generic baseline that is always available. Shape-family logic is an optional enhancer behind geometry and confidence gates; it is never the sole route to a Geometry result.

## Data flow

1. Convert accepted Stage2 depth through the frozen dynamic-Fr camera model and frozen Stage3A gravity transform.
2. Estimate support in this order: accepted global plane; local gravity-aligned context surface; geometric same-surface consensus; generic lower-envelope fallback.
3. Estimate a generic top from a spatially coherent top-k component. This result exists before routing.
4. Route by bbox/mask geometry, roundness, gravity alignment, depth compactness, support confidence, and at most a 0.10 semantic prior.
5. Try `upright_slender` or `roughly_spherical` enhancement. Reject the enhancer when unavailable or when its top differs from the generic top by more than 20 mm.
6. Emit generic support plus generic extent for unknown, irregular, uncertain, or enhancer-rejected objects.

## Boundaries

- Stage2 calibration bundle and Stage3A gravity candidate are read-only authorities.
- Ground truth is consumed only by competition aggregation/evaluation, not by estimators or routing.
- The implementation is isolated under `candidate_geometry_v2`; formal runtime and stabilizer were not modified.
- No scene target names, class-specific fixed offsets, nominal heights, or nominal shelf Z values exist in estimator configuration.
