# Formal Geometry Source Audit

Audit date: 2026-08-18. Scope was read-only: `C:\ec\_deltar_p4_pipeline`, `C:\ec\_deltar_formal`, and the relevant scan boundary in `C:\ec\esp-claw`. No formal file was edited.

## Current formal chain

```text
receipted formal scan (scan.json + 11 frame folders)
  -> FramePipeline RGB565 decode
  -> scan-level YOLO detect + segment workers
  -> ToF adapter (distance/sigma/mask + fr + official histogram sampling)
  -> official DELTAR forward, direct and letterbox canvases
  -> Stage6 per-frame detection/segment logical groups
  -> Stage7D dense world points + horizontal plane extraction
  -> Stage7E bottle/cup silhouette geometry
  -> all-object raw geometry (mask preferred, bbox/depth fallback)
  -> dual gate + selected object projection
  -> Stage8B4A Competition Shelf Stabilizer
  -> canonical P4 package
```

Source entry points:

- Formal scan orchestration: `src/deltar_p4_pipeline/scan_job.py` (`run_scan`, processor construction, 11-frame loop, raw geometry write, package build).
- Per-frame decode/YOLO/DELTAR/Geometry: `src/deltar_p4_pipeline/frame_pipeline.py` (`prepare_scan_yolo`, `_run_depth`, `process`).
- ToF tensor construction: `src/deltar_p4_pipeline/tof_adapter.py` (`adapt_inputs`). Official DELTAR receives `fr.unsqueeze(0)`, so each inference can receive a different `[1,64,4]` tensor.
- Stage7D plane extraction: `src/deltar_p4_pipeline/horizontal_planes.py` (`world_points_from_depth`, `extract_horizontal_planes`).
- Stage7E bottle/cup silhouette height: `src/deltar_p4_pipeline/object_geometry.py` (`measure_stage7e_object`).
- Raw all-object authority used by Stage8B: `src/deltar_p4_pipeline/all_object_geometry.py` (`measure_logical_object`, `AllObjectGeometryProcessor`).
- Stabilizer: `src/deltar_p4_pipeline/stage8b4a_package.py` (`_apply_competition_shelf_dynamic_stabilizer`).

## Frozen files still read by formal Stage7D/E

`scan_service_worker.WorkerConfig` passes:

- Stage7A camera: `C:\calibration\result_20260719_172703\calibration_profile.v2.json`
- Stage7B Camera-ToF: `C:\calibration\stage7b_result_unitfix_20260724_012041\camera_tof_extrinsics.v1.json`
- Stage7C full world pose: `C:\calibration\stage7c_world_result_robustfix_20260724_044035\camera_world_pose.v1.json`
- Stage7D absolute validation: `C:\calibration\stage7d_absolute_geometry_validation_20260724\stage7d_absolute_geometry_validation.v1.json`
- Stage7D and Stage7E numeric profiles are read from `profiles/stage7d_horizontal_plane_profile.json` and `profiles/stage7e_object_geometry_profile.json`.

`stage7d_contracts.py` hard-pins the old Stage7A/B/C paths, profile IDs and exact hashes. `stage7e_contracts.py` reuses those pins and additionally hard-pins the Stage7D absolute-validation artifact. Therefore Stage2/Stage3A candidates cannot be passed through formal Stage7D/E without changing formal contracts. Stage3B correctly uses an isolated adapter instead.

## DELTAR fr authority

The current formal `FramePipeline._adapter_profile` selects `regular_grid_source_then_transform`. `tof_adapter.adapt_inputs` then builds uniform 8x8 rectangles and warns that this is not Camera-ToF calibration. The Stage2 bundle states `runtime_replaced=false` and supplies depth-dependent raw-zone boundary rays.

The Stage3B adapter implements the source-defined candidate path:

```text
raw ToF index (identity raw order)
  -> current zone axial_z
  -> scale Stage2 model boundary ray by axial_z/ray.z
  -> p_camera = R_camera_from_tof * p_tof + t
  -> Stage2 Brown K/D projection
  -> source-camera [sy,sx,ey,ex]
  -> existing direct/letterbox canvas transform
  -> float32 [1,64,4] to official DELTAR
```

Invalid masked zones use the current frame's valid-zone median only to keep official patch-info construction finite; their DELTAR mask remains false. Valid zones always use their own current depth. Exact parity is checked against every reference-distance fixture in the frozen bundle at `<=1e-6 px`.

## Raw support and object height

The raw Stage8B authority is written as `stage8b_all_object_geometry.v1.json` before dual-gate packaging and before the shelf stabilizer.

Actual support enums in this path are:

- `matched_plane`: an accepted Stage7D plane matched by image contact, world gap, overlap and semantic-risk gates.
- `point_p5_fallback`: the object's robust world-Z p05 when no plane match is usable.

There is no literal `qualified-plane` enum. Stage7D accepted plane labels are `ground` and `support_surface`; once associated to an object, the all-object support enum is `matched_plane`. Likewise, `bbox/depth fallback` is not a support enum. It appears through `diagnostics.region_source=bbox` and `region_method` values `bbox_main_depth_cluster`, `bbox_center_robust`, or `bbox_full_robust`; after that region is measured, support is still either `matched_plane` or `point_p5_fallback`. Mask routes are `mask_eroded_1px_robust` and `mask_raw_fallback_robust`.

For all-object geometry:

- `top_height_above_floor_mm = robust world-Z p95`.
- `support_height_mm = matched corrected plane height`, otherwise robust world-Z p05.
- `object_height_mm = top_height_above_floor_mm - support_height_mm`.

The profile contains a pre-existing `support_matcher.corrected_height_bias_mm=-30.919045...`. Stage3B does not tune it; it records both source and errors so this correction cannot be mistaken for the post-package stabilizer.

Stage7E is a separate bottle/cup-only silhouette route. Its profile `allowed_classes` is exactly `cup`, `bottle`, and its object height is the median successful upright-silhouette candidate above the corrected support plane. The Stage3B primary comparison deliberately uses all-object raw Geometry because it also covers `sports ball` and is the artifact consumed by Stage8B.

## Detector classes and multiple bottles

The model/profile sources confirm the exact class strings:

- bottle: `bottle`
- volleyball: COCO `sports ball`
- cup: `cup`

`sports ball` is in `stage8b_p4_object_whitelist.json`. The production model manifest names the volleyball-capable detector, while runtime result metadata exposes the model's class table.

Within a frame, `build_logical_groups` combines matching detection/segmentation records. Across frames, formal `scan_aggregation._eligible` requires the same class plus bbox IoU or 3D-centroid proximity and refuses ambiguous merges. Stage3B keeps per-frame evidence, then automatically assigns the two intended bottles using image vertical order when both are present and a decisive support-height hypothesis only for singleton observations. Ambiguous singleton assignments are emitted as `UNRESOLVED_TARGET_MATCH` with candidates preserved.

## Competition Shelf Stabilizer boundary

`build_and_write_stage8b4a` first builds the package from raw all-object geometry, then calls `_apply_competition_shelf_dynamic_stabilizer`, and only then writes the canonical package. The stabilizer can change `support_height_mm`, `top_height_above_floor_mm`, and, for core-frame nominal classes, `object_height_mm`.

It contains independent fixed product authorities, including lower/upper targets (715/1059 mm), class nominal heights (bottle/cup 180 mm, sports ball 200 mm), and strength/cutoff constants. These are not Stage2 or Stage3A calibration. Stage3B neither modifies nor fits them. Raw pre-stabilizer results are primary; the canonical stabilized package is secondary comparison only.

## Duplicate/legacy authority findings

1. Formal Stage7D/E still pin the old Stage7A/B/C full transform and old Stage7D validation hash.
2. Formal DELTAR still uses uniform `fr`, not the Stage2 depth-dependent candidate.
3. The base Stage6 calibration profile remains synthetic/unverified; the Stage3B runner replaces only its camera model in memory for validation and does not use its ground transform as authority.
4. The all-object support matcher has a frozen vertical correction bias.
5. The post-package stabilizer has separate venue-specific shelf/nominal-height constants.

The isolated runner binds Stage2 K/D, raw-zone rays, Camera-ToF R/t, axial-Z, and dynamic `fr`; world height is only `dot(u_up_camera, p_camera_mm) + camera_height_mm`. It never constructs or claims `R_world_from_camera`. Camera X/Y are retained solely as a metric parameterization for plane fitting; exported authority remains `horizontal_world_authority=NOT_VALIDATED`.
