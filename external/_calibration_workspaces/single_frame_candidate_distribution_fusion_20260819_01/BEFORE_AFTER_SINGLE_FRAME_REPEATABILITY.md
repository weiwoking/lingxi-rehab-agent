# Before/After Single-Frame Repeatability

Frozen truth-blind method: `surface_hypothesis_expectation`  
`RUNTIME_USES_CROSS_FRAME_INFORMATION = false`  
`ELEVEN_FRAME_REPEATABILITY_IS_EVALUATION_ONLY = true`  
`TRUTH_LOADED_ONLY_AFTER_FREEZE = true`

| Scan | Layer | Before MAD | After MAD | Before std | After std | Before span | After span | Hypothesis present rate |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| G001 | upper | 0.329 | 4.197 | 3.532 | 13.733 | 11.143 | 43.663 | 1.000 |
| G001 | lower | 19.098 | 16.229 | 28.940 | 18.128 | 106.164 | 47.038 | 1.000 |
| G002 | upper | 1.473 | 1.398 | 6.438 | 2.620 | 17.529 | 8.534 | 1.000 |
| G002 | lower | 7.095 | 4.354 | 14.035 | 6.650 | 70.923 | 22.589 | 1.000 |
| SCAN_070604 | upper | 0.962 | 9.014 | 9.777 | 11.993 | 34.876 | 40.643 | 1.000 |
| SCAN_070604 | lower | 30.491 | 22.777 | 113.130 | 96.772 | 396.038 | 325.266 | 0.455 |
| SCAN_085223 | upper | 57.073 | 72.143 | 170.715 | 169.285 | 567.071 | 520.782 | 0.636 |
| SCAN_085223 | lower | 37.611 | 41.610 | 61.067 | 67.354 | 233.517 | 226.478 | 0.455 |
| SCAN_161718 | upper | 1.926 | 16.219 | 20.703 | 15.602 | 44.849 | 38.079 | 0.667 |
| SCAN_161718 | lower | 15.023 | 6.391 | 22.618 | 8.545 | 82.180 | 32.178 | 0.909 |
| SCAN_165011 | upper | 8.292 | 1.787 | 7.671 | 11.281 | 26.832 | 42.193 | 1.000 |
| SCAN_165011 | lower | 10.576 | 11.886 | 17.942 | 12.778 | 85.663 | 40.728 | 0.273 |

## Aggregate error counts

- >50 mm: 85 → 77
- >75 mm: 33 → 30
- >100 mm: 24 → 20

## Decision

`CANDIDATE_DISTRIBUTION_FUSION_GO = false`

Scenario B. The gate fails; production must remain unchanged.
