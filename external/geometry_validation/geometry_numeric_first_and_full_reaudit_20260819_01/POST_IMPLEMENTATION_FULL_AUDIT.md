# POST_IMPLEMENTATION_FULL_AUDIT

## Independent verdict

The frozen implementation satisfies Numeric-First publication semantics and restores complete numeric coverage on the six historical replays, but the independently recomputed all-numeric distributions expose large underlying support instability. Therefore `READY_FOR_FRESH_REAL_SCAN=false` for this frozen version.

## Full chain

| Stage | Authority / audit result |
|---|---|
| Capture and RGB/ToF pairing | 11-frame contract; all six fresh outputs retain 11 RGB; capture remains frame authority |
| Stage2 | protected calibration hash unchanged; camera authority retained |
| DELTAR-D | core math hash unchanged; heavy computation remains in exact worker |
| Stage3A | protected gravity hash unchanged; world-z authority retained |
| Detection / segmentation | both feed logical object observations; no class-specific support value |
| Generic environment support | sole support authority; direct evidence first, bounded best-effort numeric fallback when candidates exist |
| Cup/bottle extent | upright-container estimator active; reads support, determines top/extent only |
| Ball extent | sphere estimator active with generic fallback; reads support only |
| Other extent | generic spatially coherent top-k |
| Geometry publication | `numeric_available` independent of confidence; `ok/degraded/unavailable` validated |
| ToF degradation | status-invalid finite raw metrics use explicit `TOF_DEGRADED_RAW`; no false normal status |
| P4 | all selected numeric values preserved; degraded numeric remains `valid=true` |
| result / Review ZIP | terminal result completed; six ZIPs pass CRC, path, manifest, hash checks |
| UI | all six webpages show 11/11 RGB; degraded cards retain values and reason/method fields |

## Authority boundary answers

- Support is decided only by generic environment support selection.
- Top/extent is decided by upright-container, ball, or generic extent according to the evidence router.
- Shape estimators cannot override support; same-surface consensus is diagnostic/identity-only.
- Confidence no longer blocks an already finite metric result; it changes `geometry_status` to degraded.
- ToF quality alone no longer forces frame failure when formal or raw current metric evidence exists.
- Capture, not Geometry success, is UI frame authority.
- P4 preserves degraded numeric.

## Frozen-output all-numeric distributions

| Scan | Layer | n | null | median | MAD | p05 | p95 | min | max | span | >30 | >50 | >75 | >100 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| G001 | upper | 11 | 0 | 1089.155 | 0.329 | 1080.556 | 1090.939 | 1080.099 | 1091.241 | 11.143 | 0 | 0 | 0 | 0 |
| G001 | lower | 22 | 0 | 730.295 | 19.098 | 676.598 | 763.670 | 660.077 | 766.241 | 106.164 | 8 | 1 | 0 | 0 |
| G002 | upper | 10 | 0 | 1086.776 | 1.473 | 1073.406 | 1089.560 | 1072.992 | 1090.521 | 17.529 | 0 | 0 | 0 | 0 |
| G002 | lower | 22 | 0 | 714.041 | 7.095 | 699.311 | 728.861 | 692.534 | 763.456 | 70.923 | 2 | 0 | 0 | 0 |
| SCAN_161718 | upper | 3 | 0 | 1113.602 | 1.926 | 1074.971 | 1115.335 | 1070.679 | 1115.528 | 44.849 | 2 | 1 | 0 | 0 |
| SCAN_161718 | lower | 22 | 0 | 769.891 | 15.023 | 727.053 | 798.511 | 716.801 | 798.981 | 82.180 | 16 | 9 | 0 | 0 |
| SCAN_165011 | upper | 22 | 0 | 1072.768 | 8.292 | 1064.259 | 1083.359 | 1063.412 | 1090.245 | 26.832 | 0 | 0 | 0 | 0 |
| SCAN_165011 | lower | 33 | 0 | 787.125 | 10.576 | 754.624 | 810.316 | 726.515 | 812.177 | 85.663 | 31 | 26 | 8 | 0 |
| SCAN_070604 | upper | 12 | 0 | 1073.199 | 0.962 | 1053.253 | 1081.453 | 1048.683 | 1083.558 | 34.876 | 0 | 0 | 0 | 0 |
| SCAN_070604 | lower | 34 | 0 | 765.084 | 30.491 | 497.344 | 855.943 | 468.820 | 864.858 | 396.038 | 29 | 23 | 13 | 13 |
| SCAN_085223 | upper | 16 | 0 | 1013.832 | 57.073 | 586.589 | 1080.164 | 514.888 | 1081.959 | 567.071 | 11 | 9 | 6 | 6 |
| SCAN_085223 | lower | 22 | 0 | 782.413 | 37.611 | 673.295 | 884.020 | 661.193 | 894.710 | 233.517 | 17 | 16 | 6 | 5 |

The lower layer is not a single stable workspace: scans 161718/165011 cluster high, 070604 spans 396.038 mm, and 085223 spans 233.517 mm. In 085223, 85–90 cm support values remain and are mostly low-confidence best-effort/occlusion evidence:

- `frame_000000` bottle: 894.710 mm, method=best_effort_environment_surface, confidence=0.15, status=degraded
- `frame_000000` sports ball: 884.724 mm, method=best_effort_environment_surface, confidence=0.15, status=degraded
- `frame_000001` bottle: 870.648 mm, method=occluded_environment_contact_surface, confidence=0.2, status=degraded
- `frame_000001` sports ball: 870.346 mm, method=direct_observed_environment_surface, confidence=0.499999, status=degraded
- `frame_000002` person: 887.888 mm, method=occluded_environment_contact_surface, confidence=0.2, status=degraded

This shows Numeric-First did not create a healthy estimator; it exposed values that the prior quality gate had hidden. Specialized shape estimators improve extent when their evidence is accepted, but support spread is upstream and is not caused by shape support override.

## Blockers and next action

Blockers are catastrophic all-numeric errors (>100 mm), high variance/bimodality, and repeated environment-surface identity changes under degraded/raw ToF. A subsequent round should retain Numeric-First semantics while improving generic surface identity/temporal consistency and qualifying raw-ToF evidence from current sensor geometry. It must not restore confidence-based nulling or fit against the shelf truth.
