# Stage 7E Object Geometry Guide

Replace `<PROJECT_ROOT>` in the commands with the current repository root.

## Scope

Stage 7E is an opt-in PC output for competition-demo measurements of opaque
`cup` and `bottle` instances. It consumes the same-frame Stage 7D `axial_z`
world geometry and support planes. It does not change the existing
`result.json`, `job_report.json`, frame-result v1.1, or Stage 7D contracts.

Transparent glass, mirror-finish metal, very small containers, object tracking,
and multi-frame identity association are not supported by this first version.

## One-time real acceptance setup

1. Keep the final Camera and ToF mounting unchanged.
2. Use an ordinary classroom tabletop without adding special matte paper.
3. Place one ordinary opaque plastic cup, paper cup, or opaque bottle on the
   tabletop. For the first run, avoid transparent glass, mirror-finish metal,
   and very thin or very small containers.
4. Measure the physical object height with a ruler and record it as
   `object_measured_height_mm`.
5. Capture and upload one normal five-frame scan using the existing workflow.

Do not move the Camera/ToF assembly between the frozen Stage 7D acceptance and
this check.

## Run Stage 7D and Stage 7E

Use a new, nonexistent output directory:

```powershell
& '<PROJECT_ROOT>\external\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe' `
  -m deltar_p4_pipeline.cli scan-run `
  --scan-dir '<scan_dir>' `
  --output-dir '<new_output_dir>' `
  --profile-bundle '<PROJECT_ROOT>\external\_deltar_p4_pipeline\profiles' `
  --stage7d-horizontal-planes `
  --camera-intrinsics-profile 'C:\calibration\result_20260719_172703\calibration_profile.v2.json' `
  --camera-world-profile 'C:\calibration\stage7c_world_result_robustfix_20260724_044035\camera_world_pose.v1.json' `
  --camera-tof-profile 'C:\calibration\stage7b_result_unitfix_20260724_012041\camera_tof_extrinsics.v1.json' `
  --stage7d-profile '<PROJECT_ROOT>\external\_deltar_p4_pipeline\profiles\stage7d_horizontal_plane_profile.json' `
  --stage7e-object-geometry `
  --stage7d-absolute-validation-profile 'C:\calibration\stage7d_absolute_geometry_validation_20260724\stage7d_absolute_geometry_validation.v1.json' `
  --stage7e-profile '<PROJECT_ROOT>\external\_deltar_p4_pipeline\profiles\stage7e_object_geometry_profile.json'
```

Stage 7E intentionally refuses to run without the same-run Stage 7D arguments
and the frozen absolute-geometry validation profile.

## Stage 7E cup/bottle height contract

For `cup` and `bottle`, the official method is
`upright_silhouette_support_plane`. Support matching starts from the cleaned
instance mask's robust lower contour; the YOLO bbox bottom is diagnostic only.
The matched Stage 7D plane is copied and translated vertically by a bias
computed at runtime from the formal Stage 7D validation:

`support_surface_measured_height_mm - axial_z.aggregate.median_height_mm`.

No measured cup/bottle height or diameter is an estimator input. Base rays use
the frozen Stage 7A Brown Camera model and Stage 7C
`T_world_from_camera`, intersect the corrected support plane, and search along
world `+Z` while the projected point remains inside the instance mask. The
robust median across multiple anchors is the official object height.

Dense-depth height and axial-Z distance remain additive diagnostics. For thin
containers, distance is explicitly marked
`dense_depth_unverified_for_thin_object`; it must not be presented as a
high-confidence distance. Unsupported pose/support and unstable silhouette
projection fail closed and never fall back to dense depth as the official
height.

## Inspect

Open:

- `stage7e/stage7e_summary.v1.json`
- `stage7e/<frame_id>/object_geometry.v1.json`
- `stage7e/<frame_id>/object_geometry_overlay.png`

Check the following:

- `class_name` is `cup` or `bottle` and matches the object.
- `support.matched` is true and the highlighted support region is the physical
  tabletop or shelf beneath the object.
- `geometry.object_height_mm` is plausible.
- `geometry.bottom_height_above_floor_mm`,
  `geometry.top_height_above_floor_mm`, and `geometry.distance_mm` are present.
- `geometry.distance_semantics` is `camera_axial_z`.
- The object mask/bbox and support overlay are roughly aligned with the RGB
  image.

If `geometry_status` is not `measured`, use `rejection_reason` rather than
treating missing geometry fields as zero. Expected fail-closed reasons include
`insufficient_mask`, `insufficient_depth`, `support_not_found`, and
`height_out_of_demo_range`.

## Initial demo acceptance

- Class is correct.
- The matched support is correct.
- Absolute object-height error is at most 50 mm.
- The practical target is at most 30 mm.
- Overlay alignment is roughly correct.

One stable run is sufficient for the first acceptance. Repeat only when the
result is close to the threshold or visibly unstable. After acceptance, Stage
7E-Vis can be handled as a later user-visible presentation step.
