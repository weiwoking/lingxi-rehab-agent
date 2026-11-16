import * as THREE from "three";
import type { SessionTrajectoryFrame } from "../services/sessionResultsApi";
import { computeQuaternionTrunkLean } from "./HybridDrinkPose";
import { elbowQuaternion, shoulderQuaternion, wristQuaternion } from "./MotionAxisMapping";

export type LateralTransferPhase = "DANGLE" | "REACH_BALL" | "GRASP_BALL" | "INWARD_TRANSFER" | "OUTWARD_TRANSFER" | "RELEASE_BALL" | "RETURN_DANGLE";

export interface LateralJointAngles {
  shoulderFlexionDeg: number;
  shoulderAbductionDeg: number;
  shoulderRotationDeg: number;
  elbowFlexionDeg: number;
  wristExtensionDeg: number;
  wristPronationDeg: number;
}

export interface HybridLateralTransferPose {
  motionFamily: "lateral_transfer";
  phase: LateralTransferPhase;
  shoulderQuaternion: THREE.Quaternion;
  elbowQuaternion: THREE.Quaternion;
  wristQuaternion: THREE.Quaternion;
  trunkQuaternion: THREE.Quaternion;
  ballPosition: THREE.Vector3;
  canonicalJointAngles: LateralJointAngles;
  measuredCorrectionReliable: {
    trunk: boolean;
    shoulder: false;
    elbow: false;
    wrist: false;
  };
  trunkDeviationDeg: number | null;
  trunkLeanDeg: number | null;
  sourceMode: "fixed_prior" | "offline_session_csv";
}

const LATERAL_POINTS: Array<{ at: number; phase: LateralTransferPhase; angles: LateralJointAngles; ball: [number, number, number] }> = [
  { at: 0.0, phase: "DANGLE", ball: [0.36, 0.76, 0.22], angles: { shoulderFlexionDeg: 4, shoulderAbductionDeg: 4, shoulderRotationDeg: 0, elbowFlexionDeg: 8, wristExtensionDeg: -4, wristPronationDeg: 4 } },
  { at: 0.18, phase: "REACH_BALL", ball: [0.64, 1.03, 0.14], angles: { shoulderFlexionDeg: 32, shoulderAbductionDeg: 24, shoulderRotationDeg: -4, elbowFlexionDeg: 20, wristExtensionDeg: 0, wristPronationDeg: 8 } },
  { at: 0.30, phase: "GRASP_BALL", ball: [0.72, 1.04, 0.12], angles: { shoulderFlexionDeg: 35, shoulderAbductionDeg: 27, shoulderRotationDeg: -4, elbowFlexionDeg: 14, wristExtensionDeg: 1, wristPronationDeg: 9 } },
  { at: 0.38, phase: "INWARD_TRANSFER", ball: [0.54, 1.025, 0.12], angles: { shoulderFlexionDeg: 16, shoulderAbductionDeg: 24, shoulderRotationDeg: 10, elbowFlexionDeg: 42, wristExtensionDeg: 2, wristPronationDeg: 10 } },
  { at: 0.46, phase: "INWARD_TRANSFER", ball: [0.36, 1.015, 0.12], angles: { shoulderFlexionDeg: 2, shoulderAbductionDeg: 22, shoulderRotationDeg: 22, elbowFlexionDeg: 76, wristExtensionDeg: 2, wristPronationDeg: 10 } },
  { at: 0.50, phase: "INWARD_TRANSFER", ball: [0.24, 1.005, 0.12], angles: { shoulderFlexionDeg: -8, shoulderAbductionDeg: 22, shoulderRotationDeg: 30, elbowFlexionDeg: 94, wristExtensionDeg: 2, wristPronationDeg: 10 } },
  { at: 0.54, phase: "INWARD_TRANSFER", ball: [0.12, 1.0, 0.12], angles: { shoulderFlexionDeg: -16, shoulderAbductionDeg: 22, shoulderRotationDeg: 36, elbowFlexionDeg: 102, wristExtensionDeg: 2, wristPronationDeg: 10 } },
  { at: 0.58, phase: "OUTWARD_TRANSFER", ball: [0.24, 1.005, 0.12], angles: { shoulderFlexionDeg: -8, shoulderAbductionDeg: 22, shoulderRotationDeg: 30, elbowFlexionDeg: 94, wristExtensionDeg: 2, wristPronationDeg: 10 } },
  { at: 0.62, phase: "OUTWARD_TRANSFER", ball: [0.36, 1.015, 0.12], angles: { shoulderFlexionDeg: 2, shoulderAbductionDeg: 22, shoulderRotationDeg: 22, elbowFlexionDeg: 76, wristExtensionDeg: 2, wristPronationDeg: 10 } },
  { at: 0.70, phase: "OUTWARD_TRANSFER", ball: [0.56, 1.03, 0.12], angles: { shoulderFlexionDeg: 20, shoulderAbductionDeg: 23, shoulderRotationDeg: 8, elbowFlexionDeg: 42, wristExtensionDeg: 1, wristPronationDeg: 9 } },
  { at: 0.74, phase: "OUTWARD_TRANSFER", ball: [0.66, 1.04, 0.12], angles: { shoulderFlexionDeg: 30, shoulderAbductionDeg: 26, shoulderRotationDeg: 0, elbowFlexionDeg: 24, wristExtensionDeg: 1, wristPronationDeg: 9 } },
  { at: 0.84, phase: "RELEASE_BALL", ball: [0.72, 1.04, 0.12], angles: { shoulderFlexionDeg: 35, shoulderAbductionDeg: 27, shoulderRotationDeg: -4, elbowFlexionDeg: 14, wristExtensionDeg: 0, wristPronationDeg: 9 } },
  { at: 1.0, phase: "RETURN_DANGLE", ball: [0.36, 0.76, 0.22], angles: { shoulderFlexionDeg: 4, shoulderAbductionDeg: 4, shoulderRotationDeg: 0, elbowFlexionDeg: 8, wristExtensionDeg: -4, wristPronationDeg: 4 } }
];

export const LATERAL_TRANSFER_FIXED_REPETITION_COUNT = 5;

export function sampleLateralTransferFixedPose(normalizedPhase: number): HybridLateralTransferPose {
  const phase = THREE.MathUtils.clamp(Number.isFinite(normalizedPhase) ? normalizedPhase : 0, 0, 1);
  const { left, right } = enclosingSamples(phase);
  const span = Math.max(0.0001, right.at - left.at);
  const t = smootherstep((phase - left.at) / span);
  const angles = interpolateAngles(left.angles, right.angles, t);
  const ball = new THREE.Vector3(
    THREE.MathUtils.lerp(left.ball[0], right.ball[0], t),
    THREE.MathUtils.lerp(left.ball[1], right.ball[1], t),
    THREE.MathUtils.lerp(left.ball[2], right.ball[2], t)
  );
  return poseFromAngles(angles, right.phase, ball, null, "fixed_prior");
}

export function composeHybridLateralTransferPose(
  frame: SessionTrajectoryFrame,
  normalizedPhase: number,
  neutralFrame: SessionTrajectoryFrame | null
): HybridLateralTransferPose {
  const base = sampleLateralTransferFixedPose(normalizedPhase);
  const trunkDeviationDeg = neutralFrame ? computeQuaternionTrunkLean(frame, neutralFrame) : null;
  const pose = poseFromAngles(base.canonicalJointAngles, base.phase, base.ballPosition, trunkDeviationDeg, "offline_session_csv");
  pose.measuredCorrectionReliable.trunk = trunkDeviationDeg !== null;
  return pose;
}

export function isLateralActualOfflineCsvReplayContract() {
  return {
    motionFamily: "lateral_transfer",
    actualSource: "selected historical Session saved CSV",
    doesNotUse: ["runtimeSnapshot", "SESSION_IDLE", "realtime_udp", "device_online"],
    tremorDisplay: "badge/text only; no synthetic bone shake"
  };
}

function poseFromAngles(
  angles: LateralJointAngles,
  phase: LateralTransferPhase,
  ballPosition: THREE.Vector3,
  trunkDeviationDeg: number | null,
  sourceMode: HybridLateralTransferPose["sourceMode"]
): HybridLateralTransferPose {
  return {
    motionFamily: "lateral_transfer",
    phase,
    shoulderQuaternion: shoulderQuaternion(angles),
    elbowQuaternion: elbowQuaternion(angles),
    wristQuaternion: wristQuaternion(angles),
    trunkQuaternion: new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), THREE.MathUtils.degToRad(THREE.MathUtils.clamp(trunkDeviationDeg ?? 0, -25, 25) * -1)).normalize(),
    ballPosition,
    canonicalJointAngles: angles,
    measuredCorrectionReliable: { trunk: trunkDeviationDeg !== null, shoulder: false, elbow: false, wrist: false },
    trunkDeviationDeg,
    trunkLeanDeg: trunkDeviationDeg,
    sourceMode
  };
}

function enclosingSamples(phase: number) {
  let left = LATERAL_POINTS[0];
  let right = LATERAL_POINTS[LATERAL_POINTS.length - 1];
  for (let index = 0; index < LATERAL_POINTS.length - 1; index += 1) {
    if (phase >= LATERAL_POINTS[index].at && phase <= LATERAL_POINTS[index + 1].at) {
      left = LATERAL_POINTS[index];
      right = LATERAL_POINTS[index + 1];
      break;
    }
  }
  return { left, right };
}

function interpolateAngles(left: LateralJointAngles, right: LateralJointAngles, t: number): LateralJointAngles {
  return {
    shoulderFlexionDeg: THREE.MathUtils.lerp(left.shoulderFlexionDeg, right.shoulderFlexionDeg, t),
    shoulderAbductionDeg: THREE.MathUtils.lerp(left.shoulderAbductionDeg, right.shoulderAbductionDeg, t),
    shoulderRotationDeg: THREE.MathUtils.lerp(left.shoulderRotationDeg, right.shoulderRotationDeg, t),
    elbowFlexionDeg: THREE.MathUtils.lerp(left.elbowFlexionDeg, right.elbowFlexionDeg, t),
    wristExtensionDeg: THREE.MathUtils.lerp(left.wristExtensionDeg, right.wristExtensionDeg, t),
    wristPronationDeg: THREE.MathUtils.lerp(left.wristPronationDeg, right.wristPronationDeg, t)
  };
}

function smootherstep(value: number) {
  const t = THREE.MathUtils.clamp(value, 0, 1);
  return t * t * t * (t * (t * 6 - 15) + 10);
}

