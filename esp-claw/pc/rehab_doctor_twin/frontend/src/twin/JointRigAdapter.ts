import * as THREE from "three";
import type { TelemetryPayload } from "../contracts/realtimeMessages";

const boneNames = ["root", "clavicle_L", "upper_arm_L", "forearm_L", "hand_L"] as const;
type RigBoneName = (typeof boneNames)[number];

function numberOr(value: number | null | undefined, fallback = 0) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export class JointRigAdapter {
  private bones = new Map<RigBoneName, THREE.Bone>();
  private offsets = new Map<RigBoneName, THREE.Quaternion>();
  private mirror = -1;
  readonly mode: "bone-driven" | "reference-mapping";

  constructor(bones: THREE.Bone[], options?: { mirror?: boolean }) {
    for (const name of boneNames) {
      const found = bones.find((bone) => bone.name === name);
      if (found) this.bones.set(name, found);
      this.offsets.set(name, new THREE.Quaternion());
    }
    this.mirror = options?.mirror === false ? 1 : -1;
    this.mode = boneNames.every((name) => this.bones.has(name)) ? "bone-driven" : "reference-mapping";
  }

  update(telemetry: TelemetryPayload) {
    if (this.mode !== "bone-driven") return;
    this.rotate("clavicle_L", numberOr(telemetry.shoulder_abduction_deg) * 0.18, "z");
    this.rotate("upper_arm_L", numberOr(telemetry.shoulder_flexion_deg), "x");
    this.rotate("forearm_L", numberOr(telemetry.elbow_flexion_deg), "x");
    this.rotate("hand_L", numberOr(telemetry.wrist_flexion_deg), "x");
  }

  kinematicPoints(telemetry: TelemetryPayload): THREE.Vector3[] {
    const shoulder = new THREE.Vector3(-0.25, 1.45, 0);
    const upperLen = 0.72;
    const foreLen = 0.62;
    const handLen = 0.24;
    const shoulderRad = THREE.MathUtils.degToRad(THREE.MathUtils.clamp(numberOr(telemetry.shoulder_flexion_deg), -20, 110));
    const elbowRad = THREE.MathUtils.degToRad(THREE.MathUtils.clamp(numberOr(telemetry.elbow_flexion_deg), 0, 145));
    const abduction = THREE.MathUtils.degToRad(THREE.MathUtils.clamp(numberOr(telemetry.shoulder_abduction_deg), -20, 90));
    const elbow = shoulder.clone().add(new THREE.Vector3(this.mirror * Math.sin(abduction) * 0.24, -Math.cos(shoulderRad) * upperLen, Math.sin(shoulderRad) * upperLen));
    const wrist = elbow.clone().add(new THREE.Vector3(this.mirror * 0.1, -Math.cos(shoulderRad - elbowRad * 0.72) * foreLen, Math.sin(shoulderRad - elbowRad * 0.72) * foreLen));
    const hand = wrist.clone().add(new THREE.Vector3(this.mirror * 0.08, -0.06, handLen));
    return [shoulder, elbow, wrist, hand];
  }

  private rotate(name: RigBoneName, degrees: number, axis: "x" | "y" | "z") {
    const bone = this.bones.get(name);
    if (!bone) return;
    const clamped = THREE.MathUtils.clamp(degrees, -130, 160);
    const target = new THREE.Quaternion().setFromAxisAngle(
      axis === "x" ? new THREE.Vector3(1, 0, 0) : axis === "y" ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(0, 0, 1),
      THREE.MathUtils.degToRad(clamped) * this.mirror
    );
    target.multiply(this.offsets.get(name) ?? new THREE.Quaternion());
    bone.quaternion.slerp(target, 0.14);
  }
}
