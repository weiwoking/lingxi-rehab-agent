# Demo Motion Pipeline Diagnostic

## Scope

This pass only fixes the motion path used by the real-time digital twin:

Demo posture data -> WebSocket -> `applyLiveSensorFrame()` -> `ImuPoseMapper` -> `SegmentRigAdapter` -> `requestAnimationFrame`.

It does not change patient side semantics, scoring, report schema, route layout, the GLB asset, or `C:\ev\p4`.

## Root Cause

The demo backend previously generated each sensor quaternion independently from local angle fields. The frontend correctly interpreted IMU data as absolute sensor orientations and calculated relative joints with:

- shoulder = trunk^-1 * upper_arm
- elbow = upper_arm^-1 * forearm
- wrist = forearm^-1 * wrist

Because the demo forearm and wrist quaternions were not chained from the upper-arm orientation, the relative shoulder, elbow, and wrist motion could be visually muted after calibration. The browser path also rebuilt telemetry inside `applyLiveSensorFrame()` from the previous frame, which made the first visible frame effectively static.

## Fix

- Backend demo IMU now emits chained absolute orientations for lower back, upper arm, forearm, and wrist.
- Quaternion order is explicitly marked as `w,x,y,z`.
- The frontend passes the current telemetry frame into `applyLiveSensorFrame()` instead of reconstructing it from stale values.
- Demo auto-calibration runs once on the first `PREPARE` frame for each session.
- `SegmentRigAdapter.render()` writes slerped target quaternions into the actual shoulder, elbow, and wrist pivot objects and updates matrix world state.
- Development-only diagnostics are available through `window.rehabMotionDebug` in Vite dev mode.

## Four-Level Diagnostics

Level 1 backend output:

- `timestamp_ms`
- `session_id`
- `source_mode`
- four IMU quaternions
- `quaternion_order=w,x,y,z`

Level 2 browser receive:

- received frame count
- dropped/non-monotonic frame count
- current session id
- raw four IMU quaternions

Level 3 `ImuPoseMapper`:

- calibrationReady
- qShoulder
- qElbow
- qWristRelative
- rejectedFrameCount
- rejectReason

Level 4 `SegmentRigAdapter`:

- right shoulder/elbow/wrist pivot quaternions
- target shoulder/elbow/wrist quaternions
- animationLoopCount
- rendererInstanceCount

## Development Console Probe

In dev mode:

```js
window.rehabMotionDebug.applyDiagnosticPose("PREPARE")
window.rehabMotionDebug.applyDiagnosticPose("REACH")
window.rehabMotionDebug.applyDiagnosticPose("LIFT")
window.rehabMotionDebug.applyDiagnosticPose("APPROACH_MOUTH")
window.rehabMotionDebug.applyDiagnosticPose("RETURN")
window.rehabMotionDebug.getMotionDiagnostics()
```

If these direct diagnostic poses move the model, SegmentRig and the animation loop are working. If demo still does not move, the issue is in backend/WebSocket/calibration/frame routing.
