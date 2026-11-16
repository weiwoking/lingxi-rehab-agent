import * as THREE from "three";

export const DRINK_CUP_AXIS_MAPPING = {
  shoulderFlexion: { axis: new THREE.Vector3(1, 0, 0), sign: -1, label: "local X negative" },
  shoulderAbduction: { axis: new THREE.Vector3(0, 0, 1), sign: 1, label: "local Z positive" },
  shoulderRotation: { axis: new THREE.Vector3(0, 1, 0), sign: 1, label: "local Y positive" },
  elbowFlexion: { axis: new THREE.Vector3(1, 0, 0), sign: -1, label: "local X negative" },
  wristExtension: { axis: new THREE.Vector3(1, 0, 0), sign: 1, label: "local X positive" },
  wristPronation: { axis: new THREE.Vector3(0, 1, 0), sign: 1, label: "local Y positive" }
} as const;

export type DrinkCupPhase = "DANGLE" | "PREPARE" | "REACH" | "GRASP" | "LIFT" | "APPROACH_MOUTH" | "RETURN";

export interface DrinkCupJointKeyframe {
  shoulderFlexionDeg: number;
  shoulderAbductionDeg: number;
  shoulderRotationDeg: number;
  elbowFlexionDeg: number;
  wristExtensionDeg: number;
  wristPronationDeg: number;
}

export const DRINK_CUP_KEYFRAMES: Record<DrinkCupPhase, DrinkCupJointKeyframe> = {
  DANGLE: { shoulderFlexionDeg: 3, shoulderAbductionDeg: 3, shoulderRotationDeg: -3, elbowFlexionDeg: 8, wristExtensionDeg: -5, wristPronationDeg: 7 },
  PREPARE: { shoulderFlexionDeg: 6, shoulderAbductionDeg: 5, shoulderRotationDeg: -4, elbowFlexionDeg: 14, wristExtensionDeg: -8, wristPronationDeg: 8 },
  REACH: { shoulderFlexionDeg: 26, shoulderAbductionDeg: 8, shoulderRotationDeg: 0, elbowFlexionDeg: 24, wristExtensionDeg: -2, wristPronationDeg: 14 },
  GRASP: { shoulderFlexionDeg: 30, shoulderAbductionDeg: 8, shoulderRotationDeg: 2, elbowFlexionDeg: 42, wristExtensionDeg: 0, wristPronationDeg: 18 },
  LIFT: { shoulderFlexionDeg: 32, shoulderAbductionDeg: 6, shoulderRotationDeg: 4, elbowFlexionDeg: 72, wristExtensionDeg: 7, wristPronationDeg: 21 },
  APPROACH_MOUTH: { shoulderFlexionDeg: 33, shoulderAbductionDeg: 4, shoulderRotationDeg: 5, elbowFlexionDeg: 82, wristExtensionDeg: 4, wristPronationDeg: 23 },
  RETURN: { shoulderFlexionDeg: 22, shoulderAbductionDeg: 6, shoulderRotationDeg: 0, elbowFlexionDeg: 30, wristExtensionDeg: -5, wristPronationDeg: 12 }
};

function axisQuat(axis: THREE.Vector3, signedDegrees: number) {
  return new THREE.Quaternion().setFromAxisAngle(axis, THREE.MathUtils.degToRad(signedDegrees));
}

export function shoulderQuaternion(frame: DrinkCupJointKeyframe) {
  return axisQuat(DRINK_CUP_AXIS_MAPPING.shoulderFlexion.axis, DRINK_CUP_AXIS_MAPPING.shoulderFlexion.sign * frame.shoulderFlexionDeg)
    .multiply(axisQuat(DRINK_CUP_AXIS_MAPPING.shoulderAbduction.axis, DRINK_CUP_AXIS_MAPPING.shoulderAbduction.sign * frame.shoulderAbductionDeg))
    .multiply(axisQuat(DRINK_CUP_AXIS_MAPPING.shoulderRotation.axis, DRINK_CUP_AXIS_MAPPING.shoulderRotation.sign * frame.shoulderRotationDeg))
    .normalize();
}

export function elbowQuaternion(frame: DrinkCupJointKeyframe) {
  return axisQuat(DRINK_CUP_AXIS_MAPPING.elbowFlexion.axis, DRINK_CUP_AXIS_MAPPING.elbowFlexion.sign * frame.elbowFlexionDeg).normalize();
}

export function wristQuaternion(frame: DrinkCupJointKeyframe) {
  return axisQuat(DRINK_CUP_AXIS_MAPPING.wristExtension.axis, DRINK_CUP_AXIS_MAPPING.wristExtension.sign * frame.wristExtensionDeg)
    .multiply(axisQuat(DRINK_CUP_AXIS_MAPPING.wristPronation.axis, DRINK_CUP_AXIS_MAPPING.wristPronation.sign * frame.wristPronationDeg))
    .normalize();
}
