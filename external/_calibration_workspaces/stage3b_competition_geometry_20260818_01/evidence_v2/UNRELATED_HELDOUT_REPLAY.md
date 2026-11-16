# Unrelated Held-Out Replay

- Scan: `scan_20260810_123714_000`
- Baseline objects: 65; candidate v2 objects: 65
- Finite: 65/65; positive extent: 65/65
- Catastrophic regressions: 0
- Family routing: {'generic': 42, 'roughly_spherical': 7, 'upright_slender': 16}
- Support sources: {'local_gravity_support_surface': 12, 'same_surface_consensus': 53}

This is a structural regression result, not an accuracy result. Candidate count is preserved, all outputs are finite and positive, and no height jump crossed the predeclared 500 mm / 3× catastrophic guard. `UNRELATED_HELDOUT_REGRESSION = false` under those guards.
