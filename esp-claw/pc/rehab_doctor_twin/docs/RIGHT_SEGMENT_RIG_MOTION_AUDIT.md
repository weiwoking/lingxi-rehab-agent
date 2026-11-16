# Right Segment Rig Motion Audit

Generated: 2026-07-22T10:15:03.843822+00:00

## Input Mapping

The runtime keeps the existing ordinary-Mesh anatomy model and drives only segment pivots. It does not require `THREE.Bone`, `SkinnedMesh`, GLB animation clips or mirrored geometry.

Relative quaternion mapping is implemented in `frontend/src/twin/ImuPoseMapper.ts`:

- `qShoulder = inverse(qTrunk) * qUpperArm`
- `qElbow = inverse(qUpperArm) * qForearm`
- `qWristRelative = inverse(qForearm) * qWrist`

`trunk_imu` remains readable as a legacy alias, while new sessions use `lower_back_imu`.

## Smoothing

`SegmentRigAdapter.render(deltaSeconds)` uses RAF-frame smoothing with `alpha = 1 - exp(-10 * deltaSeconds)`. Sensor frames update targets; render frames update visible pivot quaternions.

## Bounds and Safety

- Invalid or non-monotonic IMU frames hold the previous valid pose.
- The actual trajectory comes from `rightHandMarker.getWorldPosition()`.
- The standard trajectory remains a cyan right DRINK_CUP template; it is not labeled as prediction.

RIGHT_SEGMENT_RIG_IMPLEMENTED=true
POSE_SLERP_IMPLEMENTED=true
ACTUAL_TRAJECTORY_SOURCE=RIGHT_HAND_MARKER_WORLD_POSITION
