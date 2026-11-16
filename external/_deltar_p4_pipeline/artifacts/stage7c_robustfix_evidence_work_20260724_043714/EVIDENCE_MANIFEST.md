# Stage 7C Robust Orientation Gate Fix Evidence

This lightweight evidence set supports the controlled Stage 7C minimal fix.
The formal report is intentionally not included.

## Contents

- `before_after/`: exact before and after copies of the five modified files.
- `unified_diffs/`: one `git diff --no-index` patch per modified file.
- `test_outputs/`: authoritative unittest, compileall, frozen-profile validator,
  and three real scan-validation outputs.
- `real_world_calibration/`: compact real calibration reports and diagnostics;
  no raw scan data or overlays.
- `real_world_validation/`: compact independent validation reports and
  diagnostics; no duplicate formal profile.
- `formal_profile/`: generated `camera_world_pose.v1.json`.
- `source_snippets/`: focused gate and test excerpts.
- `environment/`: fixed runtime and command inventory without environment-variable
  dumps or credentials.
- `git_readonly/`: read-only P4/ESP-Claw repository state before and after.

## Key Result

- Formal profile: `camera_world_pose_9b9147068f6cd2ae`
- Calibration: PASS
- Independent validation: PASS
- Captured/base-valid/orientation-inlier: 15/15/14
- Per-pose inliers: 5/5/4
- Rejected frame: `world_front_far/frame_000001`
- `absolute_geometry_valid`: false

## Exclusions

No build output, runtime data, raw scan copies, old evidence archives, report
copy, keys, tokens, credentials, or full environment-variable dump is included.
