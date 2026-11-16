# Numeric-First Geometry implementation and frozen full-chain re-audit

```text
IMPLEMENTATION_COMPLETE = true
NUMERIC_FIRST_IMPLEMENTED = true

QUALITY_GATE_CAN_NULL_CALCULABLE_SUPPORT = false
QUALITY_GATE_CAN_NULL_CALCULABLE_EXTENT = false

NUMERIC_AVAILABILITY_SEPARATE_FROM_CONFIDENCE = true
DEGRADED_GEOMETRY_CAN_CARRY_NUMERIC_SUPPORT = true
DEGRADED_GEOMETRY_CAN_CARRY_NUMERIC_OBJECT_HEIGHT = true

AMBIGUOUS_SUPPORT_RETURNS_BEST_EFFORT_NUMERIC = true
AMBIGUOUS_SUPPORT_RETURNS_NULL = false

CUP_BOTTLE_SPECIALIZED_ESTIMATOR_ACTIVE = true
BALL_SPECIALIZED_ESTIMATOR_ACTIVE = true
OTHER_CLASSES_GENERIC = true
SHAPE_CAN_OVERRIDE_SUPPORT = false

TOF_QUALITY_ONLY_CAUSES_DEGRADED = true
TOF_TRUE_INCALCULABLE_CAN_CAUSE_UNAVAILABLE = true
SAFE_NEAREST_TOF_FALLBACK_PASS = true

SCAN_085223_CAPTURED_RGB_COUNT = 11
SCAN_085223_UI_VISIBLE_RGB_COUNT = 11
SCAN_085223_NUMERIC_SUPPORT_OBSERVATION_COUNT = 38
SCAN_085223_NUMERIC_OBJECT_HEIGHT_OBSERVATION_COUNT = 38
SCAN_085223_NULL_SUPPORT_OBSERVATION_COUNT = 0
SCAN_085223_HARD_REJECTED_FRAME_COUNT = 0

G001_ALL_NUMERIC_LOWER_MEDIAN_MM = 730.295
G001_ALL_NUMERIC_LOWER_MAD_MM = 19.098
G001_ALL_NUMERIC_LOWER_SPAN_MM = 106.164

G002_ALL_NUMERIC_LOWER_MEDIAN_MM = 714.041
G002_ALL_NUMERIC_LOWER_MAD_MM = 7.095
G002_ALL_NUMERIC_LOWER_SPAN_MM = 70.923

SCAN_161718_ALL_NUMERIC_LOWER_MEDIAN_MM = 769.891
SCAN_161718_ALL_NUMERIC_LOWER_MAD_MM = 15.023
SCAN_161718_ALL_NUMERIC_LOWER_SPAN_MM = 82.180

SCAN_165011_ALL_NUMERIC_LOWER_MEDIAN_MM = 787.125
SCAN_165011_ALL_NUMERIC_LOWER_MAD_MM = 10.576
SCAN_165011_ALL_NUMERIC_LOWER_SPAN_MM = 85.663

SCAN_070604_ALL_NUMERIC_LOWER_MEDIAN_MM = 765.084
SCAN_070604_ALL_NUMERIC_LOWER_MAD_MM = 30.491
SCAN_070604_ALL_NUMERIC_LOWER_SPAN_MM = 396.038

SCAN_085223_ALL_NUMERIC_LOWER_MEDIAN_MM = 782.413
SCAN_085223_ALL_NUMERIC_LOWER_MAD_MM = 37.611
SCAN_085223_ALL_NUMERIC_LOWER_SPAN_MM = 233.517

ALL_NUMERIC_CATASTROPHIC_GT100MM_COUNT = 24
ALL_NUMERIC_GT75MM_ERROR_COUNT = 33
ALL_NUMERIC_GT50MM_ERROR_COUNT = 85

FINAL_IMPLEMENTATION_FROZEN_BEFORE_REAUDIT = true
POST_IMPLEMENTATION_FULL_AUDIT_COMPLETE = true
FINAL_NULL_AND_FAILURE_PATH_AUDIT_PASS = true
FINAL_GEOMETRY_COMPLEXITY_AUDIT_PASS = true
FINAL_UPRIGHT_CONTAINER_AUDIT_PASS = true
FINAL_BALL_ESTIMATOR_AUDIT_PASS = true

SCAN_8030_REGRESSION_PASS = true
PARENT_HEAVY_IMPORT_BOUNDARY_PASS = true
P4_PACKAGE_CONTRACT_PASS = true
REVIEW_BUNDLE_REGRESSION_PASS = true

DOWNLOAD_VISUALIZATION_FOLDER_CREATED = true
DOWNLOAD_VISUALIZATION_FOLDER_PATH = C:\Users\Weiwo\Downloads\geometry_numeric_first_visual_review_20260819_01
DOWNLOAD_VISUALIZATION_IS_ZIP = false
DOWNLOAD_VISUALIZATION_SCAN_COUNT = 6

STAGE2_MODIFIED = false
DELTAR_D_MATH_MODIFIED = false
STAGE3A_MODIFIED = false
P4_FIRMWARE_MODIFIED = false

READY_FOR_OWNER_VISUAL_REVIEW = true
READY_FOR_FRESH_REAL_SCAN = false
READY_FOR_COMMIT = false

POST_AUDIT_BLOCKERS = catastrophic all-numeric support errors; high variance/bimodality; degraded/raw-ToF surface hopping
POST_AUDIT_RECOMMENDATION = retain Numeric-First; improve generic surface identity/temporal consistency and raw-ToF evidence qualification without truth fitting or quality-null gates

GIT_COMMIT = false
EVIDENCE_ZIP_CREATED = true
EVIDENCE_ZIP_PATH = C:\ec\_codex_reports\geometry_numeric_first_and_full_reaudit_20260819_01\geometry_numeric_first_and_full_reaudit_20260819_01_evidence.zip
SUGGESTED_COMMIT_NAME = geometry: publish degraded metric estimates with numeric-first semantics
```

## Outcome

Phase A is implemented, tested, fresh-replayed, visualized, and hash-frozen. Phase B independently re-audited the same frozen production sources and recomputed every stability metric from the frozen outputs. Numeric coverage is complete in all six replays, including 38/38 support and object-height observations for 085223, but the visible all-numeric evidence is not stable enough for a fresh real scan.

`READY_FOR_FRESH_REAL_SCAN=false` is driven by evidence, not by a confidence filter: 24 observations exceed 100 mm absolute support error and 33 exceed 75 mm. The worst lower-layer spans are 396.038 mm (070604) and 233.517 mm (085223); 085223 upper spans 567.071 mm.

## Deliverables

- `NUMERIC_PUBLICATION_GATE_AUDIT.md`
- `POST_IMPLEMENTATION_FULL_AUDIT.md`
- `FINAL_NULL_AND_FAILURE_PATH_AUDIT.md`
- `FINAL_GEOMETRY_COMPLEXITY_AUDIT.md`
- `FINAL_UPRIGHT_CONTAINER_AUDIT.md`
- `FINAL_BALL_ESTIMATOR_AUDIT.md`
- `FINAL_IMPLEMENTATION_FREEZE.json`
- evidence ZIP and verification receipt
- six-scan Downloads visualization: `C:\Users\Weiwo\Downloads\geometry_numeric_first_visual_review_20260819_01`

## Verification summary

- Focused implementation suite: 49 passed.
- Formal regression set plus new tests: 108 passed, 30 warnings.
- Six fresh historical replays completed on 8030; every result/package request returned successfully and each output retained 11 RGB frames.
- Exact parent: `C:\ec\_conda_envs\vision_interpreter\python.exe`; clean import probe heavy imports: `[]`.
- Exact worker: `C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe`; 8030 health passed.
- P4 parser: 23 cases passed; current-package C harness: four operations, all exit 0.
- Six Review ZIPs passed CRC, safe-path, uniqueness, required-entry, manifest, and SHA-256 verification.
- Protected Stage2, DELTAR-D core, Stage3A, and depth-model hashes match the frozen baseline. P4 firmware was not edited.

## Independent all-numeric metrics

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

## Interpretation

Numeric-First correctly separates availability from quality: ambiguous candidate evidence now produces low-confidence degraded numbers, UI displays them, and P4 preserves them. It also proves why those semantics cannot alone make the geometry ready: previously hidden values reveal repeated wrong-surface selection, especially under weak/raw ToF. No audit-phase production patch was made, no commit was created, and no real scan was captured.
