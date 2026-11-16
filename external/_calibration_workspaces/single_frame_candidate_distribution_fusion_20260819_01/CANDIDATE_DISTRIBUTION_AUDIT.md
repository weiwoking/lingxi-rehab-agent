# Candidate Distribution Audit (truth blind)

`RUNTIME_USES_CROSS_FRAME_INFORMATION = false`  
`ELEVEN_FRAME_REPEATABILITY_IS_EVALUATION_ONLY = true`  
`TRUTH_LOADED_DURING_METHOD_CONSTRUCTION = false`

Every method consumes one frame only. The eleven results are collected only after every frame output has been calculated independently. Candidate provenance is consolidated within each object/source component, then each object receives one total vote inside an image-spatial support context.

## Inputs

| Scan | Replay | Geometry artifact SHA-256 | Frames |
|---|---|---|---:|
| G001 | `scan_20260818_092411_numericfirst01` | `32ba0a4f2689fe1d4dd24584661fbb333a2aa1c0482cd3404d23cc10ffc4c287` | 11 |
| G002 | `scan_20260818_092729_numericfirst01` | `19404300e018e95d5245334c1adf29f794b3d4073a2217e446d484570b3a89a1` | 11 |
| SCAN_161718 | `scan_20260818_161718_numericfirst01` | `987579cc3241e5c7ceb630c2caa88cadf1e232a171c5499d2956faee18eb7a2c` | 11 |
| SCAN_165011 | `scan_20260818_165011_numericfirst01` | `9a0a7fe392f3dd30fb7146c17c338605078d2729a599f4f476521a18db01ea8a` | 11 |
| SCAN_070604 | `scan_20260819_070604_numericfirst01` | `cb264aa7c53b07348f43129abc23fa3a5dba38719ca337abccb284ae1e8e4c6e` | 11 |
| SCAN_085223 | `scan_20260819_085223_numericfirst01` | `348f8e35d3d3fded36580e975eb09db3b00c2a04118542e2fb89d0d218d2fea7` | 11 |

## Truth-blind method comparison

| Method | mean scan MAD | mean scan std | mean scan span | >100 mm repeatability outliers |
|---|---:|---:|---:|---:|
| `current_winner_take_all` | 39.434 | 152.451 | 462.236 | 80 |
| `provenance_weighted_median` | 34.591 | 152.192 | 432.619 | 82 |
| `trimmed_huber_location` | 33.444 | 150.216 | 431.102 | 82 |
| `surface_hypothesis_expectation` | 30.889 | 153.168 | 437.331 | 82 |
| `mixture_posterior_soft_estimate` | 30.921 | 153.120 | 436.583 | 82 |
| `hybrid_dominant_or_soft` | 33.564 | 153.278 | 437.599 | 82 |

Truth-blind selected method: `surface_hypothesis_expectation`.

Selection order is frozen: minimize >100 mm repeatability outliers, then mean scan MAD, then mean scan span. Shelf truth is not present in this script or comparison file.

## Extraction counts

| Scan | Frames | Objects | Raw candidates | Provenance groups | Hypotheses |
|---|---:|---:|---:|---:|---:|
| G001 | 11 | 33 | 459 | 280 | 25 |
| G002 | 11 | 32 | 351 | 230 | 21 |
| SCAN_161718 | 11 | 25 | 268 | 181 | 14 |
| SCAN_165011 | 11 | 55 | 632 | 386 | 23 |
| SCAN_070604 | 11 | 46 | 700 | 434 | 36 |
| SCAN_085223 | 11 | 38 | 666 | 402 | 31 |

## Candidate semantics

- `raw_candidates` is the union of current eligible, rejected, and best-effort environment-surface observations, deduplicated only by the exact recorded surface ID.
- `provenance_normalized_candidates` consolidates close duplicates only within one object, generator, axial neighborhood, and spatial component neighborhood.
- Per-object weights sum to one; candidate-generating abundance cannot create extra votes.
- Support contexts are formed from same-frame bbox-bottom image geometry. Scene-spanning detections are isolated. No 725/1065 constant or target-name rule participates.
- Narrow/unimodal distributions can be normal confidence. Wide or competing modes remain numeric but become degraded/low confidence.
