import * as THREE from "three";
import type { ImuPoseFrame, ImuSensorPose, TelemetryPayload } from "../contracts/realtimeMessages";
import { ImuCalibration } from "./ImuCalibration";
import { DRINK_CUP_AXIS_MAPPING } from "./MotionAxisMapping";

export interface RelativeJointPose { shoulder: THREE.Quaternion; elbow: THREE.Quaternion; wrist: THREE.Quaternion; trunk: THREE.Quaternion; timestampMs: number; held: boolean; source: "imu" | "telemetry-fallback"; }
export const POSE_SMOOTHING_RATE = 10;
export const MAX_ANGULAR_SPEED_DEG_S = 360;
export const SENSOR_STALE_TIMEOUT_MS = 700;
const MAX_ANGLE_STEP = THREE.MathUtils.degToRad(28); const HOLD_TIMEOUT_MS = SENSOR_STALE_TIMEOUT_MS;

function axisQuat(axis: THREE.Vector3, signedDegrees: number) {
  return new THREE.Quaternion().setFromAxisAngle(axis, THREE.MathUtils.degToRad(signedDegrees));
}

function numberOr(value: number | null | undefined, fallback = 0) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function lowerBackSensor(frame: ImuPoseFrame): ImuSensorPose | undefined {
  return frame.sensors.lower_back_imu ?? frame.sensors.trunk_imu;
}

export class ImuPoseMapper {
  private calibration = new ImuCalibration();
  private lastPose: RelativeJointPose | null = null;
  private lastSensorTimestampMs = 0;
  private rejectedFrameCount = 0;
  private rejectReason = "";
  calibrate(frame: ImuPoseFrame) { this.calibration.calibrateFromCurrentFrame(frame); }
  calibrateFromCurrentFrame(frame: ImuPoseFrame) { this.calibration.calibrateFromCurrentFrame(frame); this.rejectReason = ""; }
  resetCalibration() { this.calibration.resetCalibration(); this.lastPose = null; this.lastSensorTimestampMs = 0; this.rejectedFrameCount = 0; this.rejectReason = ""; }
  isCalibrationReady() { return this.calibration.isCalibrationReady(); }
  map(telemetry: TelemetryPayload): RelativeJointPose {
    if (!telemetry.imu_pose) {
      this.rejectReason = "IMU_POSE_MISSING";
      return this.fromTelemetry(telemetry);
    }
    const frame = telemetry.imu_pose;
    const lowerBack = lowerBackSensor(frame);
    if (!lowerBack) {
      this.rejectedFrameCount += 1;
      this.rejectReason = "LOWER_BACK_IMU_MISSING";
      return this.fromTelemetry(telemetry);
    }
    const now = Date.now();
    const timestampMs = Math.max(lowerBack.timestamp_ms, frame.sensors.upper_arm_imu.timestamp_ms, frame.sensors.forearm_imu.timestamp_ms, frame.sensors.wrist_imu.timestamp_ms);
    if (timestampMs <= this.lastSensorTimestampMs && this.lastPose) {
      this.rejectedFrameCount += 1;
      this.rejectReason = "NON_MONOTONIC_TIMESTAMP";
      return { ...this.lastPose, held: true };
    }
    this.lastSensorTimestampMs = timestampMs;
    const trunk = this.calibration.apply(lowerBack.sensor_id === "trunk_imu" ? "lower_back_imu" : lowerBack.sensor_id, lowerBack.quaternion);
    const upper = this.calibration.apply("upper_arm_imu", frame.sensors.upper_arm_imu.quaternion);
    const forearm = this.calibration.apply("forearm_imu", frame.sensors.forearm_imu.quaternion);
    const wristSensor = this.calibration.apply("wrist_imu", frame.sensors.wrist_imu.quaternion);
    return this.smoothAndClamp({
      trunk,
      shoulder: trunk.clone().invert().multiply(upper).normalize(),
      elbow: upper.clone().invert().multiply(forearm).normalize(),
      wrist: forearm.clone().invert().multiply(wristSensor).normalize(),
      timestampMs,
      held: false,
      source: "imu"
    }, now);
  }
  getDiagnostics() {
    return {
      calibrationReady: this.calibration.isCalibrationReady(),
      rejectedFrameCount: this.rejectedFrameCount,
      rejectReason: this.rejectReason,
      lastSensorTimestampMs: this.lastSensorTimestampMs,
      qShoulder: this.lastPose?.shoulder.toArray() ?? [0, 0, 0, 1],
      qElbow: this.lastPose?.elbow.toArray() ?? [0, 0, 0, 1],
      qWristRelative: this.lastPose?.wrist.toArray() ?? [0, 0, 0, 1],
      source: this.lastPose?.source ?? "telemetry-fallback"
    };
  }
  private fromTelemetry(telemetry: TelemetryPayload): RelativeJointPose {
    const shoulderFlexionDeg = numberOr(telemetry.shoulder_flexion_deg);
    const shoulderAbductionDeg = numberOr(telemetry.shoulder_abduction_deg);
    const shoulderRotationDeg = numberOr(telemetry.shoulder_rotation_deg);
    const elbowFlexionDeg = numberOr(telemetry.elbow_flexion_deg);
    const wristFlexionDeg = numberOr(telemetry.wrist_flexion_deg);
    const forearmPronationDeg = numberOr(telemetry.forearm_pronation_deg);
    const trunkFlexionDeg = numberOr(telemetry.trunk_flexion_deg);
    const shoulder = axisQuat(DRINK_CUP_AXIS_MAPPING.shoulderFlexion.axis, DRINK_CUP_AXIS_MAPPING.shoulderFlexion.sign * THREE.MathUtils.clamp(shoulderFlexionDeg, -20, 110))
      .multiply(axisQuat(DRINK_CUP_AXIS_MAPPING.shoulderAbduction.axis, DRINK_CUP_AXIS_MAPPING.shoulderAbduction.sign * THREE.MathUtils.clamp(shoulderAbductionDeg, -20, 80)))
      .multiply(axisQuat(DRINK_CUP_AXIS_MAPPING.shoulderRotation.axis, DRINK_CUP_AXIS_MAPPING.shoulderRotation.sign * THREE.MathUtils.clamp(shoulderRotationDeg, -45, 45)))
      .normalize();
    const elbow = axisQuat(DRINK_CUP_AXIS_MAPPING.elbowFlexion.axis, DRINK_CUP_AXIS_MAPPING.elbowFlexion.sign * THREE.MathUtils.clamp(elbowFlexionDeg * 0.72, 0, 125)).normalize();
    const wrist = axisQuat(DRINK_CUP_AXIS_MAPPING.wristExtension.axis, DRINK_CUP_AXIS_MAPPING.wristExtension.sign * THREE.MathUtils.clamp(wristFlexionDeg, -45, 45))
      .multiply(axisQuat(DRINK_CUP_AXIS_MAPPING.wristPronation.axis, DRINK_CUP_AXIS_MAPPING.wristPronation.sign * THREE.MathUtils.clamp(forearmPronationDeg, -70, 80)))
      .normalize();
    const trunk = axisQuat(DRINK_CUP_AXIS_MAPPING.shoulderAbduction.axis, THREE.MathUtils.clamp(trunkFlexionDeg, -5, 20)).normalize();
    return this.smoothAndClamp({ shoulder, elbow, wrist, trunk, timestampMs: Date.now(), held: false, source: "telemetry-fallback" }, Date.now());
  }
  private smoothAndClamp(next: RelativeJointPose, now: number): RelativeJointPose {
    if (!this.lastPose) { this.lastPose = next; return next; }
    const smoothed = { shoulder: this.clampedSlerp(this.lastPose.shoulder, next.shoulder), elbow: this.clampedSlerp(this.lastPose.elbow, next.elbow), wrist: this.clampedSlerp(this.lastPose.wrist, next.wrist), trunk: this.clampedSlerp(this.lastPose.trunk, next.trunk), timestampMs: next.timestampMs, held: false, source: next.source } satisfies RelativeJointPose;
    this.lastPose = smoothed; return smoothed;
  }
  private clampedSlerp(current: THREE.Quaternion, target: THREE.Quaternion) { const angle = current.angleTo(target); const alpha = angle > MAX_ANGLE_STEP ? MAX_ANGLE_STEP / angle : 0.18; return current.clone().slerp(target, alpha).normalize(); }
}
