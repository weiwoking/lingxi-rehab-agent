# Focused change record

The workspace is not a Git checkout, so this is a focused semantic diff plus complete changed sources in the archive.

- `stage2b_camera_tof.py`
  - adds `advanced-diagnostics --subset-trials --seed --max-iterations`;
  - adds truthful `*_deg` and `*_us` coverage fields with explicit deprecated aliases;
  - adds names/units and labeled values to `parameter_std`.
- `advanced_diagnostics.py` (new)
  - identical-condition 8-D4 Model-B gauge fits keyed by raw zone index;
  - Camera ray, 400/600/1000-mm center/footprint/IoU comparisons;
  - axial/radial full, heldout, LOPO, deterministic unique 80% subsets;
  - separate core-reference-plane and all-broad scores;
  - residual provenance and raw-zone outlier tables;
  - LOPO/subset Camera mapping stability and pre14/current15 reconstruction;
  - protected-input before/after SHA-256 enforcement.
- `test_advanced_diagnostics.py` (new)
  - pure D4 gauge equivalence and non-equivalence detection;
  - known axial/radial recovery; provenance layers; dynamic N; units; protected hashes.
- `USAGE_STAGE2B.md`, `tool_architecture.md`
  - documents the read-only command and observable-authority/core-vs-broad semantics.

No promotion, capture, registration, blacklist, runtime-policy, candidate, formal-scan, or Stage2C path was added or modified.
