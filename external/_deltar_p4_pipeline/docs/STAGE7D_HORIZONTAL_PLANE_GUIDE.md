# Stage 7D horizontal-plane real acceptance

Replace `<PROJECT_ROOT>` in the commands with the current repository root.

Stage 7D is an opt-in PC diagnostic path. It does not change the P4 capture
protocol, the frozen Stage 6 result schemas, or the Stage 7C profile.

## Scene and measurement

Prepare one stationary scene with visible ground, one horizontal tabletop or
shelf, and one vertical matte white board. Measure the support surface height
above the ground with a tape measure. Run the normal P4 scan/upload flow, then
run `scan-run` with `--stage7d-horizontal-planes` and the frozen Stage 7A,
Stage 7B, and Stage 7C profiles.

Inspect both `axial_z` and `radial_range` directories. Compare:

- ground height error;
- support-surface height error;
- plane-normal tilt;
- whether the vertical board was falsely accepted;
- whether the RGB contour is roughly aligned.

Initial competition-demo gates are 50 mm ground error, 50 mm support height
error, 10 degrees normal tilt, zero vertical-board support acceptance, and
human-confirmed rough RGB boundary alignment. One stable scan is enough for
the first acceptance. Add repeats only when a result is near a gate or visibly
unstable.

If only one hypothesis passes, select that hypothesis. If both pass, select
the one with lower physical error and the more plausible overlay. If neither
passes, first inspect scene visibility and dense-depth quality before making
small demo-threshold adjustments.

After real acceptance, write a separate
`stage7d_absolute_geometry_validation.v1.json`. Never modify Stage 7C or set
its `absolute_geometry_valid` field to true.

Stage 7E consumes the selected per-pixel world-height map, ground mask,
support-surface mask, support height, and support boundary. Each hypothesis
persists `world_height_mm.npy`; `plane_masks.npz` always contains
`ground_mask` and `support_surface_mask` in addition to per-plane masks.

## Example command

```powershell
& '<PROJECT_ROOT>\external\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe' `
  -m deltar_p4_pipeline.cli scan-run `
  --scan-dir <existing_scan_dir> `
  --output-dir <new_empty_output_dir> `
  --profile-bundle '<PROJECT_ROOT>\external\_deltar_p4_pipeline\profiles' `
  --stage7d-horizontal-planes `
  --camera-intrinsics-profile 'C:\calibration\result_20260719_172703\calibration_profile.v2.json' `
  --camera-world-profile 'C:\calibration\stage7c_world_result_robustfix_20260724_044035\camera_world_pose.v1.json' `
  --camera-tof-profile 'C:\calibration\stage7b_result_unitfix_20260724_012041\camera_tof_extrinsics.v1.json' `
  --stage7d-profile '<PROJECT_ROOT>\external\_deltar_p4_pipeline\profiles\stage7d_horizontal_plane_profile.json'
```
