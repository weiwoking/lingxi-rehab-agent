import * as THREE from "three";
import type { SessionTrajectoryFrame } from "../services/sessionResultsApi";
import { DRINK_CUP_AXIS_MAPPING, elbowQuaternion, shoulderQuaternion, wristQuaternion } from "./MotionAxisMapping";

export type HybridActionPhase = "DANGLE" | "REACH" | "GRASP" | "LIFT" | "DRINK_HOLD" | "LOWER" | "RELEASE" | "RETURN_DANGLE";
export type HybridCupPhase =
  | "DANGLE_START"
  | "REACH"
  | "GRASP"
  | "LIFT"
  | "DRINK_HOLD"
  | "LOWER"
  | "RELEASE"
  | "RETURN_DANGLE"
  | "DANGLE_END";
export type SensorDeltaOrder = "current_times_inverse_neutral";

export interface DrinkJointAngles {
  shoulderFlexionDeg: number;
  shoulderAbductionDeg: number;
  shoulderRotationDeg: number;
  elbowFlexionDeg: number;
  wristExtensionDeg: number;
  wristPronationDeg: number;
}

export interface HybridDrinkPose {
  shoulderQuaternion: THREE.Quaternion;
  elbowQuaternion: THREE.Quaternion;
  wristQuaternion: THREE.Quaternion;
  trunkQuaternion: THREE.Quaternion;
  cupPhase: HybridCupPhase;
  actionPhase: HybridActionPhase;
  canonicalJointAngles: DrinkJointAngles;
  measuredCorrections: DrinkJointAngles;
  correctionReliability: {
    shoulder: boolean;
    elbow: boolean;
    wrist: boolean;
    trunk: boolean;
  };
  trunkLeanDeg: number | null;
  sensorDeltaOrder: SensorDeltaOrder;
  alignmentReady: Record<"imu1" | "imu2" | "imu3" | "imu4", boolean>;
}

export const HYBRID_CORRECTION_LIMITS = {
  shoulderFlexionDeg: 20,
  shoulderAbductionDeg: 12,
  shoulderRotationDeg: 12,
  elbowFlexionDeg: 25,
  wristExtensionDeg: 18,
  wristPronationDeg: 20,
  elbowCorrectionWeight: 0.28,
  shoulderCorrectionWeight: 0.28,
  wristCorrectionWeight: 0.22
} as const;

export const SENSOR_TO_MODEL_ALIGNMENT = {
  imu1: new THREE.Quaternion(),
  imu2: new THREE.Quaternion(),
  imu3: new THREE.Quaternion(),
  imu4: new THREE.Quaternion()
} as const;

export const ALIGNMENT_READY = {
  // No reliable installation-to-model basis is available in the current frontend contract for limb IMUs.
  // Keep their measured corrections disabled instead of treating identity as a completed alignment.
  imu1: false,
  imu2: true,
  imu3: false,
  imu4: false
} as const;

const SAMPLE_POINTS: Array<{ at: number; phase: HybridActionPhase; cupPhase: HybridCupPhase; angles: DrinkJointAngles }> = [
  { at: 0.0, phase: "DANGLE", cupPhase: "DANGLE_START", angles: { shoulderFlexionDeg: 3, shoulderAbductionDeg: 3, shoulderRotationDeg: -3, elbowFlexionDeg: 8, wristExtensionDeg: -5, wristPronationDeg: 7 } },
  { at: 0.1, phase: "REACH", cupPhase: "REACH", angles: { shoulderFlexionDeg: 24, shoulderAbductionDeg: 8, shoulderRotationDeg: 0, elbowFlexionDeg: 24, wristExtensionDeg: -2, wristPronationDeg: 14 } },
  { at: 0.24, phase: "GRASP", cupPhase: "GRASP", angles: { shoulderFlexionDeg: 30, shoulderAbductionDeg: 8, shoulderRotationDeg: 2, elbowFlexionDeg: 42, wristExtensionDeg: 0, wristPronationDeg: 18 } },
  { at: 0.46, phase: "LIFT", cupPhase: "LIFT", angles: { shoulderFlexionDeg: 32, shoulderAbductionDeg: 6, shoulderRotationDeg: 4, elbowFlexionDeg: 74, wristExtensionDeg: 7, wristPronationDeg: 21 } },
  { at: 0.62, phase: "DRINK_HOLD", cupPhase: "DRINK_HOLD", angles: { shoulderFlexionDeg: 33, shoulderAbductionDeg: 4, shoulderRotationDeg: 5, elbowFlexionDeg: 84, wristExtensionDeg: 4, wristPronationDeg: 23 } },
  { at: 0.78, phase: "LOWER", cupPhase: "LOWER", angles: { shoulderFlexionDeg: 27, shoulderAbductionDeg: 6, shoulderRotationDeg: 2, elbowFlexionDeg: 52, wristExtensionDeg: -1, wristPronationDeg: 17 } },
  { at: 0.9, phase: "RELEASE", cupPhase: "RELEASE", angles: { shoulderFlexionDeg: 20, shoulderAbductionDeg: 6, shoulderRotationDeg: 0, elbowFlexionDeg: 30, wristExtensionDeg: -5, wristPronationDeg: 12 } },
  { at: 1.0, phase: "RETURN_DANGLE", cupPhase: "DANGLE_END", angles: { shoulderFlexionDeg: 3, shoulderAbductionDeg: 3, shoulderRotationDeg: -3, elbowFlexionDeg: 10, wristExtensionDeg: -5, wristPronationDeg: 7 } }
];

const MODEL_FORWARD_AXIS = computeModelForwardAxis();

export function sampleDrinkReferencePose(normalizedPhase: number): HybridDrinkPose {
  const phase = THREE.MathUtils.clamp(Number.isFinite(normalizedPhase) ? normalizedPhase : 0, 0, 1);
  const { left, right } = enclosingSamples(phase);
  const span = Math.max(0.0001, right.at - left.at);
  const t = smootherstep((phase - left.at) / span);
  const angles = interpolateAngles(left.angles, right.angles, t);
  return poseFromAngles(angles, right.phase, right.cupPhase, zeroAngles(), {
    shoulder: true,
    elbow: true,
    wrist: true,
    trunk: true
  }, null);
}

export function composeHybridDrinkPose(
  frame: SessionTrajectoryFrame,
  normalizedPhase: number,
  neutralFrame: SessionTrajectoryFrame | null
): HybridDrinkPose {
  const base = sampleDrinkReferencePose(normalizedPhase);
  const measured = neutralFrame ? computeMeasuredJointCorrections(frame, neutralFrame) : measuredFallback();
  const reliability = assessJointCorrectionReliability(measured);
  const shoulderCorrection = reliability.shoulder ? {
    shoulderFlexionDeg: clamp(measured.shoulderFlexionDeg * HYBRID_CORRECTION_LIMITS.shoulderCorrectionWeight, -HYBRID_CORRECTION_LIMITS.shoulderFlexionDeg, HYBRID_CORRECTION_LIMITS.shoulderFlexionDeg),
    shoulderAbductionDeg: clamp(measured.shoulderAbductionDeg * HYBRID_CORRECTION_LIMITS.shoulderCorrectionWeight, -HYBRID_CORRECTION_LIMITS.shoulderAbductionDeg, HYBRID_CORRECTION_LIMITS.shoulderAbductionDeg),
    shoulderRotationDeg: clamp(measured.shoulderRotationDeg * HYBRID_CORRECTION_LIMITS.shoulderCorrectionWeight, -HYBRID_CORRECTION_LIMITS.shoulderRotationDeg, HYBRID_CORRECTION_LIMITS.shoulderRotationDeg)
  } : { shoulderFlexionDeg: 0, shoulderAbductionDeg: 0, shoulderRotationDeg: 0 };
  const elbowCorrection = reliability.elbow
    ? clamp(measured.elbowFlexionDeg * HYBRID_CORRECTION_LIMITS.elbowCorrectionWeight, -HYBRID_CORRECTION_LIMITS.elbowFlexionDeg, HYBRID_CORRECTION_LIMITS.elbowFlexionDeg)
    : 0;
  const wristCorrection = reliability.wrist ? {
    wristExtensionDeg: clamp(measured.wristExtensionDeg * HYBRID_CORRECTION_LIMITS.wristCorrectionWeight, -HYBRID_CORRECTION_LIMITS.wristExtensionDeg, HYBRID_CORRECTION_LIMITS.wristExtensionDeg),
    wristPronationDeg: clamp(measured.wristPronationDeg * HYBRID_CORRECTION_LIMITS.wristCorrectionWeight, -HYBRID_CORRECTION_LIMITS.wristPronationDeg, HYBRID_CORRECTION_LIMITS.wristPronationDeg)
  } : { wristExtensionDeg: 0, wristPronationDeg: 0 };

  const finalAngles: DrinkJointAngles = {
    shoulderFlexionDeg: clamp(base.canonicalJointAngles.shoulderFlexionDeg + shoulderCorrection.shoulderFlexionDeg, 0, 42),
    shoulderAbductionDeg: clamp(base.canonicalJointAngles.shoulderAbductionDeg + shoulderCorrection.shoulderAbductionDeg, 0, 16),
    shoulderRotationDeg: clamp(base.canonicalJointAngles.shoulderRotationDeg + shoulderCorrection.shoulderRotationDeg, -10, 16),
    elbowFlexionDeg: clamp(base.canonicalJointAngles.elbowFlexionDeg + elbowCorrection, 8, 105),
    wristExtensionDeg: clamp(base.canonicalJointAngles.wristExtensionDeg + wristCorrection.wristExtensionDeg, -16, 18),
    wristPronationDeg: clamp(base.canonicalJointAngles.wristPronationDeg + wristCorrection.wristPronationDeg, 0, 32)
  };
  if (!validateForwardReachDirection(base.canonicalJointAngles, finalAngles)) {
    finalAngles.shoulderFlexionDeg = base.canonicalJointAngles.shoulderFlexionDeg;
    finalAngles.shoulderAbductionDeg = base.canonicalJointAngles.shoulderAbductionDeg;
    finalAngles.shoulderRotationDeg = base.canonicalJointAngles.shoulderRotationDeg;
    finalAngles.wristExtensionDeg = base.canonicalJointAngles.wristExtensionDeg;
    finalAngles.wristPronationDeg = base.canonicalJointAngles.wristPronationDeg;
  }
  const appliedCorrections: DrinkJointAngles = {
    shoulderFlexionDeg: finalAngles.shoulderFlexionDeg - base.canonicalJointAngles.shoulderFlexionDeg,
    shoulderAbductionDeg: finalAngles.shoulderAbductionDeg - base.canonicalJointAngles.shoulderAbductionDeg,
    shoulderRotationDeg: finalAngles.shoulderRotationDeg - base.canonicalJointAngles.shoulderRotationDeg,
    elbowFlexionDeg: finalAngles.elbowFlexionDeg - base.canonicalJointAngles.elbowFlexionDeg,
    wristExtensionDeg: finalAngles.wristExtensionDeg - base.canonicalJointAngles.wristExtensionDeg,
    wristPronationDeg: finalAngles.wristPronationDeg - base.canonicalJointAngles.wristPronationDeg
  };
  return poseFromAngles(finalAngles, base.actionPhase, base.cupPhase, appliedCorrections, reliability, measured.trunkLeanDeg);
}

export function computeMeasuredJointCorrections(frame: SessionTrajectoryFrame, neutralFrame: SessionTrajectoryFrame) {
  const trunkDelta = sensorDeltaFor(frame.imu2, neutralFrame.imu2, "imu2");
  const upperDelta = sensorDeltaFor(frame.imu1, neutralFrame.imu1, "imu1");
  const forearmDelta = sensorDeltaFor(frame.imu3, neutralFrame.imu3, "imu3");
  const wristDelta = sensorDeltaFor(frame.imu4, neutralFrame.imu4, "imu4");
  const upperRelative = relativeDelta(trunkDelta, upperDelta);
  const forearmRelative = relativeDelta(upperDelta, forearmDelta);
  const wristRelative = relativeDelta(forearmDelta, wristDelta);
  return {
    shoulderFlexionDeg: extractSignedHingeAngle(upperRelative, DRINK_CUP_AXIS_MAPPING.shoulderFlexion.axis) * DRINK_CUP_AXIS_MAPPING.shoulderFlexion.sign,
    shoulderAbductionDeg: extractSignedHingeAngle(upperRelative, DRINK_CUP_AXIS_MAPPING.shoulderAbduction.axis) * DRINK_CUP_AXIS_MAPPING.shoulderAbduction.sign,
    shoulderRotationDeg: extractSignedHingeAngle(upperRelative, DRINK_CUP_AXIS_MAPPING.shoulderRotation.axis) * DRINK_CUP_AXIS_MAPPING.shoulderRotation.sign,
    elbowFlexionDeg: extractSignedHingeAngle(forearmRelative, DRINK_CUP_AXIS_MAPPING.elbowFlexion.axis) * DRINK_CUP_AXIS_MAPPING.elbowFlexion.sign,
    wristExtensionDeg: extractSignedHingeAngle(wristRelative, DRINK_CUP_AXIS_MAPPING.wristExtension.axis) * DRINK_CUP_AXIS_MAPPING.wristExtension.sign,
    wristPronationDeg: extractSignedHingeAngle(wristRelative, DRINK_CUP_AXIS_MAPPING.wristPronation.axis) * DRINK_CUP_AXIS_MAPPING.wristPronation.sign,
    trunkLeanDeg: computeQuaternionTrunkLean(frame, neutralFrame)
  };
}

export function assessJointCorrectionReliability(corrections: ReturnType<typeof computeMeasuredJointCorrections>) {
  const finite = (value: number) => Number.isFinite(value);
  return {
    shoulder: ALIGNMENT_READY.imu1 && ALIGNMENT_READY.imu2 && finite(corrections.shoulderFlexionDeg) && Math.abs(corrections.shoulderFlexionDeg) < 90,
    elbow: ALIGNMENT_READY.imu1 && ALIGNMENT_READY.imu3 && finite(corrections.elbowFlexionDeg) && Math.abs(corrections.elbowFlexionDeg) < 140,
    wrist: ALIGNMENT_READY.imu3 && ALIGNMENT_READY.imu4 && finite(corrections.wristExtensionDeg) && Math.abs(corrections.wristExtensionDeg) < 120,
    trunk: ALIGNMENT_READY.imu2 && corrections.trunkLeanDeg !== null
  };
}

export function computeSensorDelta(current: THREE.Quaternion, neutral: THREE.Quaternion): THREE.Quaternion {
  return current.clone().normalize().multiply(neutral.clone().normalize().invert()).normalize();
}

export function mapSensorDeltaToModelBasis(delta: THREE.Quaternion, sensorToModel = new THREE.Quaternion()): THREE.Quaternion {
  return sensorToModel.clone().normalize().multiply(delta.clone().normalize()).multiply(sensorToModel.clone().normalize().invert()).normalize();
}

export function extractSignedHingeAngle(delta: THREE.Quaternion, axis: THREE.Vector3): number {
  const hingeAxis = axis.clone().normalize();
  const vectorPart = new THREE.Vector3(delta.x, delta.y, delta.z);
  const projected = hingeAxis.multiplyScalar(vectorPart.dot(hingeAxis));
  const twist = new THREE.Quaternion(projected.x, projected.y, projected.z, delta.w).normalize();
  const angle = 2 * Math.atan2(new THREE.Vector3(twist.x, twist.y, twist.z).dot(axis.clone().normalize()), twist.w);
  return THREE.MathUtils.radToDeg(Math.atan2(Math.sin(angle), Math.cos(angle)));
}

export function computeQuaternionTrunkLean(frame: SessionTrajectoryFrame, neutralFrame: SessionTrajectoryFrame): number | null {
  const trunkDelta = sensorDeltaFor(frame.imu2, neutralFrame.imu2, "imu2");
  if (!trunkDelta) return null;
  const up = new THREE.Vector3(0, 1, 0).applyQuaternion(trunkDelta);
  const sagittal = new THREE.Vector2(up.y, up.z);
  if (sagittal.lengthSq() < 0.000001) return null;
  return THREE.MathUtils.radToDeg(Math.atan2(sagittal.y, sagittal.x));
}

export function validateForwardReachDirection(baseAngles: DrinkJointAngles, finalAngles: DrinkJointAngles) {
  const dangle = handPositionFromAngles(SAMPLE_POINTS[0].angles);
  const baseDelta = handPositionFromAngles(baseAngles).sub(dangle);
  const finalDelta = handPositionFromAngles(finalAngles).sub(dangle);
  const expectedProgress = baseDelta.dot(MODEL_FORWARD_AXIS);
  if (expectedProgress <= 0.02) return true;
  return finalDelta.dot(MODEL_FORWARD_AXIS) >= 0 && finalDelta.dot(baseDelta.clone().normalize()) >= 0;
}

function poseFromAngles(
  angles: DrinkJointAngles,
  actionPhase: HybridActionPhase,
  cupPhase: HybridCupPhase,
  measuredCorrections: DrinkJointAngles,
  correctionReliability: HybridDrinkPose["correctionReliability"],
  trunkLeanDeg: number | null
): HybridDrinkPose {
  return {
    shoulderQuaternion: shoulderQuaternion(angles),
    elbowQuaternion: elbowQuaternion(angles),
    wristQuaternion: wristQuaternion(angles),
    trunkQuaternion: new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), THREE.MathUtils.degToRad(clamp(trunkLeanDeg ?? 0, -25, 25) * -1.0)).normalize(),
    cupPhase,
    actionPhase,
    canonicalJointAngles: angles,
    measuredCorrections,
    correctionReliability,
    trunkLeanDeg,
    sensorDeltaOrder: "current_times_inverse_neutral",
    alignmentReady: { ...ALIGNMENT_READY }
  };
}

export function handPositionFromAngles(angles: DrinkJointAngles): THREE.Vector3 {
  const upperLength = 0.46;
  const forearmLength = 0.38;
  const handLength = 0.12;
  const upperDirection = new THREE.Vector3(0, -upperLength, 0).applyQuaternion(shoulderQuaternion(angles));
  const elbowRotation = shoulderQuaternion(angles).multiply(elbowQuaternion(angles));
  const forearmDirection = new THREE.Vector3(0, -forearmLength, 0).applyQuaternion(elbowRotation);
  const wristRotation = elbowRotation.multiply(wristQuaternion(angles));
  const handDirection = new THREE.Vector3(0, -handLength, 0).applyQuaternion(wristRotation);
  return upperDirection.add(forearmDirection).add(handDirection);
}

function sensorDeltaFor(current: SessionTrajectoryFrame["imu1"], neutral: SessionTrajectoryFrame["imu1"], key: keyof typeof SENSOR_TO_MODEL_ALIGNMENT) {
  const currentQuat = quaternionFromSample(current);
  const neutralQuat = quaternionFromSample(neutral);
  if (!currentQuat || !neutralQuat) return null;
  return mapSensorDeltaToModelBasis(computeSensorDelta(currentQuat, neutralQuat), SENSOR_TO_MODEL_ALIGNMENT[key]);
}

function relativeDelta(parent: THREE.Quaternion | null, child: THREE.Quaternion | null) {
  if (!parent || !child) return new THREE.Quaternion();
  return parent.clone().invert().multiply(child).normalize();
}

function quaternionFromSample(sample: SessionTrajectoryFrame["imu1"]) {
  if (!sample) return null;
  const values = [sample.qw, sample.qx, sample.qy, sample.qz];
  if (!values.every((value) => typeof value === "number" && Number.isFinite(value))) return null;
  return new THREE.Quaternion(sample.qx!, sample.qy!, sample.qz!, sample.qw!).normalize();
}

function enclosingSamples(phase: number) {
  let left = SAMPLE_POINTS[0];
  let right = SAMPLE_POINTS.at(-1)!;
  for (let index = 0; index < SAMPLE_POINTS.length - 1; index += 1) {
    if (phase >= SAMPLE_POINTS[index].at && phase <= SAMPLE_POINTS[index + 1].at) {
      left = SAMPLE_POINTS[index];
      right = SAMPLE_POINTS[index + 1];
      break;
    }
  }
  return { left, right };
}

function interpolateAngles(left: DrinkJointAngles, right: DrinkJointAngles, t: number): DrinkJointAngles {
  return {
    shoulderFlexionDeg: THREE.MathUtils.lerp(left.shoulderFlexionDeg, right.shoulderFlexionDeg, t),
    shoulderAbductionDeg: THREE.MathUtils.lerp(left.shoulderAbductionDeg, right.shoulderAbductionDeg, t),
    shoulderRotationDeg: THREE.MathUtils.lerp(left.shoulderRotationDeg, right.shoulderRotationDeg, t),
    elbowFlexionDeg: THREE.MathUtils.lerp(left.elbowFlexionDeg, right.elbowFlexionDeg, t),
    wristExtensionDeg: THREE.MathUtils.lerp(left.wristExtensionDeg, right.wristExtensionDeg, t),
    wristPronationDeg: THREE.MathUtils.lerp(left.wristPronationDeg, right.wristPronationDeg, t)
  };
}

function measuredFallback(): ReturnType<typeof computeMeasuredJointCorrections> {
  return { ...zeroAngles(), trunkLeanDeg: null };
}

function zeroAngles(): DrinkJointAngles {
  return {
    shoulderFlexionDeg: 0,
    shoulderAbductionDeg: 0,
    shoulderRotationDeg: 0,
    elbowFlexionDeg: 0,
    wristExtensionDeg: 0,
    wristPronationDeg: 0
  };
}

function smootherstep(value: number) {
  const t = THREE.MathUtils.clamp(value, 0, 1);
  return t * t * t * (t * (t * 6 - 15) + 10);
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, Number.isFinite(value) ? value : 0));
}

function computeModelForwardAxis() {
  return handPositionFromAngles(SAMPLE_POINTS[2].angles).sub(handPositionFromAngles(SAMPLE_POINTS[0].angles)).normalize();
}
