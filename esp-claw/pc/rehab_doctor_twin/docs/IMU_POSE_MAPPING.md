# IMU Pose Mapping

姿态映射采用4个IMU相对四元数：q_shoulder = inverse(q_trunk) * q_upper，q_elbow = inverse(q_upper) * q_forearm，q_wrist = inverse(q_forearm) * q_wrist_sensor。前端由 `ImuPoseMapper` 统一归一化、平滑、限幅和超时保持。

## 2026-07-22 Right Upper Limb Update

Active patient side is right upper limb. New sessions use `lower_back_imu`, `upper_arm_imu`, `forearm_imu` and `wrist_imu`; `trunk_imu` remains a legacy read alias only. Relative quaternion mapping is qShoulder=inverse(qTrunk)*qUpperArm, qElbow=inverse(qUpperArm)*qForearm and qWristRelative=inverse(qForearm)*qWrist.

