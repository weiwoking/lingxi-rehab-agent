# Stage 6 profile bundle contract

The bundle consists of `model_manifest.json`, `algorithm_profile.json`, `scan_strategy_profile.json`, `calibration_profile.example.json`, and `zone_orientation_profile.json`. Every document has a versioned schema and stable `profile_id`; the runtime records every file SHA-256.

The model manifest pins DELTAR, YOLO detect and YOLO segment by path, task, SHA-256, runtime and upstream commit. `profile-validate` hashes all three assets before inference and fails with `E_PROFILE_HASH` on absence or mismatch.

The algorithm is frozen: official DELTAR is the sole production depth chain; direct and letterbox both run. Segmentation primary is `segment_eroded_3px + mad_median`; detection fallback is `bbox_full + median`. Depth semantics are engineering-candidate `axial_z`; `radial_range` is diagnostic only. Stage 6 does no ranking or tuning.

The scan strategy starts at 1.5 m and treats object ranges as operator guidance, never a hard acceptance threshold. Range departure emits `W_SCAN_DISTANCE_OUTSIDE_RECOMMENDED`, not an automatic rejection. ToF status, sigma and valid zones remain the quality evidence.

Calibration status is `synthetic`, `unverified`, or `measured`. Unless a measured profile is installed and verified, `absolute_geometry_valid`, `physical_height_valid`, and `bottom_height_valid` must all remain false. The bundled example is unverified by design.
