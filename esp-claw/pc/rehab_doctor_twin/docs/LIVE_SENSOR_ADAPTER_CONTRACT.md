# Live Sensor Adapter Contract

Generated: 2026-07-22T10:15:03.843822+00:00

## Unified Input

`frontend/src/contracts/realtimeMessages.ts` defines `LiveImuFrame` with:

- `timestamp_ms`
- `session_id`
- `source_mode`: `demo` or `live`
- `lower_back_imu`
- `upper_arm_imu`
- `forearm_imu`
- `wrist_imu`

Each IMU carries a quaternion and optional runtime metadata through the existing `ImuSensorPose` contract. `trunk_imu` is accepted only through the legacy `ImuPoseFrame` path.

## Unified Runtime Entry

`AnatomyScene.applyLiveSensorFrame(frame, emgFrame)` is the shared visual drive entry for Demo and Live sensor frames. React pages do not rotate anatomical Mesh objects directly.

## sEMG Mapping

- CH1 -> `finger_extensors_right`
- CH2 -> `biceps_brachii_right`
- CH3 -> `triceps_brachii_right`
- CH4 -> `extensor_carpi_ulnaris_right`

CH4 maps only to right extensor carpi ulnaris, not the whole forearm extensor group.

UNIFIED_SENSOR_ENTRY=applyLiveSensorFrame
REACT_DIRECT_MESH_ROTATION=false
