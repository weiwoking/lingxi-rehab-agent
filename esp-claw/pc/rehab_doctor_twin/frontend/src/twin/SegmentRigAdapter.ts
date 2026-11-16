import * as THREE from "three";
import type { TelemetryPayload } from "../contracts/realtimeMessages";
import { anatomyManifest, rightBoneBusinessIds } from "./modelManifest";
import type { RelativeJointPose } from "./ImuPoseMapper";
import { DRINK_CUP_AXIS_MAPPING, DRINK_CUP_KEYFRAMES, elbowQuaternion, shoulderQuaternion, wristQuaternion } from "./MotionAxisMapping";
import type { HybridDrinkPose } from "./HybridDrinkPose";
import type { HybridLateralTransferPose } from "./HybridLateralTransferPose";

export interface SegmentRigOptions { meshes: THREE.Mesh[]; meshBusinessIds: Map<THREE.Mesh, string>; }
export type DiagnosticPoseEasing = "easeInOutSine" | "easeInOutCubic" | "easeOutCubic" | "smootherstep";
export interface DiagnosticPoseVariation {
  shoulderFlexionDeg: number;
  shoulderAbductionDeg: number;
  shoulderRotationDeg: number;
  elbowFlexionDeg: number;
  wristExtensionDeg: number;
  wristPronationDeg: number;
}
export interface DiagnosticPoseTransition {
  durationSeconds: number;
  easing: DiagnosticPoseEasing;
  variation?: DiagnosticPoseVariation;
}
type Segment = "shoulder" | "upperArm" | "forearm" | "hand" | "hidden";
interface MappedSegmentMesh { mesh: THREE.Mesh; businessId: string; segment: Segment; }

const region = {
  shoulder: "\u80a9\u90e8",
  shoulderGirdle: "\u80a9\u80db\u5e26",
  upperArm: "\u4e0a\u81c2",
  forearm: "\u524d\u81c2",
  hand: "\u624b\u90e8"
};

const segmentByBusinessId = Object.fromEntries(anatomyManifest.map((item) => {
  if (item.region === region.shoulder || item.region === region.shoulderGirdle) return [item.businessId, "shoulder"];
  if (item.region === region.upperArm) return [item.businessId, "upperArm"];
  if (item.region === region.forearm) return [item.businessId, "forearm"];
  if (item.region === region.hand) return [item.businessId, "hand"];
  return [item.businessId, "hidden"];
})) as Record<string, Segment>;

export class SegmentRigAdapter {
  readonly mode = "segment-rig-adapter";
  readonly group = new THREE.Group();
  static rendererInstanceCount = 0;
  private rightRoot = new THREE.Group();
  private rightShoulderPivot = new THREE.Group();
  private rightClavicleGroup = new THREE.Group();
  private rightScapulaGroup = new THREE.Group();
  private rightUpperArmGroup = new THREE.Group();
  private rightHumerusGroup = new THREE.Group();
  private rightUpperArmMuscleGroup = new THREE.Group();
  private rightElbowPivot = new THREE.Group();
  private rightForearmGroup = new THREE.Group();
  private rightRadiusGroup = new THREE.Group();
  private rightUlnaGroup = new THREE.Group();
  private rightForearmMuscleGroup = new THREE.Group();
  private rightWristPivot = new THREE.Group();
  private rightHandGroup = new THREE.Group();
  private rightHandBoneGroup = new THREE.Group();
  private rightHandSoftTissueGroup = new THREE.Group();
  private rightHandMarker = new THREE.Object3D();
  private matchedMeshes = 0;
  private targetShoulder = new THREE.Quaternion();
  private targetElbow = new THREE.Quaternion();
  private targetWrist = new THREE.Quaternion();
  private targetTrunk = new THREE.Quaternion();
  private targetTrunkZ = 0;
  private diagnosticTransition: {
    elapsedSeconds: number;
    durationSeconds: number;
    easing: DiagnosticPoseEasing;
    shoulderFrom: THREE.Quaternion;
    elbowFrom: THREE.Quaternion;
    wristFrom: THREE.Quaternion;
  } | null = null;
  private animationLoopCount = 0;
  private lastPoseSource: RelativeJointPose["source"] = "telemetry-fallback";
  private pivotAudit = {
    shoulder: new THREE.Vector3(),
    elbow: new THREE.Vector3(),
    wrist: new THREE.Vector3(),
    hand: new THREE.Vector3()
  };

  constructor(options: SegmentRigOptions) {
    SegmentRigAdapter.rendererInstanceCount += 1;
    this.group.name = "rehab_right_upper_limb_segment_rig";
    this.rightRoot.name = "rightRoot";
    this.rightShoulderPivot.name = "rightShoulderPivot";
    this.rightClavicleGroup.name = "rightClavicleGroup";
    this.rightScapulaGroup.name = "rightScapulaGroup";
    this.rightUpperArmGroup.name = "rightUpperArmGroup";
    this.rightHumerusGroup.name = "rightHumerusGroup";
    this.rightUpperArmMuscleGroup.name = "rightUpperArmMuscleGroup";
    this.rightElbowPivot.name = "rightElbowPivot";
    this.rightForearmGroup.name = "rightForearmGroup";
    this.rightRadiusGroup.name = "rightRadiusGroup";
    this.rightUlnaGroup.name = "rightUlnaGroup";
    this.rightForearmMuscleGroup.name = "rightForearmMuscleGroup";
    this.rightWristPivot.name = "rightWristPivot";
    this.rightHandGroup.name = "rightHandGroup";
    this.rightHandBoneGroup.name = "rightHandBoneGroup";
    this.rightHandSoftTissueGroup.name = "rightHandSoftTissueGroup";
    this.rightHandMarker.name = "rightHandMarker";
    this.group.add(this.rightRoot);
    this.rightRoot.add(this.rightShoulderPivot);
    this.rightShoulderPivot.add(this.rightClavicleGroup, this.rightScapulaGroup, this.rightUpperArmGroup);
    this.rightUpperArmGroup.add(this.rightHumerusGroup, this.rightUpperArmMuscleGroup);
    this.rightUpperArmGroup.add(this.rightElbowPivot);
    this.rightElbowPivot.add(this.rightForearmGroup);
    this.rightForearmGroup.add(this.rightRadiusGroup, this.rightUlnaGroup, this.rightForearmMuscleGroup);
    this.rightForearmGroup.add(this.rightWristPivot);
    this.rightWristPivot.add(this.rightHandGroup);
    this.rightHandGroup.add(this.rightHandBoneGroup, this.rightHandSoftTissueGroup, this.rightHandMarker);

    const mappedMeshes: MappedSegmentMesh[] = [];
    for (const mesh of options.meshes) {
      const businessId = options.meshBusinessIds.get(mesh) ?? mesh.userData.anatomyBusinessId;
      if (typeof businessId !== "string") continue;
      const segment = segmentByBusinessId[businessId];
      if (!segment || segment === "hidden") continue;
      mappedMeshes.push({ mesh, businessId, segment });
    }

    this.configurePivots(mappedMeshes);

    for (const { mesh, businessId, segment } of mappedMeshes) {
      mesh.visible = true;
      this.tintMesh(mesh, businessId);
      this.targetGroupFor(businessId, segment).attach(mesh);
      this.matchedMeshes += 1;
    }
  }

  updateFromPose(pose: RelativeJointPose, telemetry: TelemetryPayload) {
    this.diagnosticTransition = null;
    this.targetTrunk.copy(pose.trunk).normalize();
    this.targetShoulder.copy(pose.shoulder).normalize();
    this.targetElbow.copy(pose.elbow);
    this.targetWrist.copy(pose.wrist);
    this.lastPoseSource = pose.source;
    this.targetTrunkZ = 0;
  }

  updateHybridDrinkPose(pose: HybridDrinkPose) {
    this.diagnosticTransition = null;
    this.targetTrunk.copy(pose.trunkQuaternion).normalize();
    this.targetShoulder.copy(pose.shoulderQuaternion).normalize();
    this.targetElbow.copy(pose.elbowQuaternion).normalize();
    this.targetWrist.copy(pose.wristQuaternion).normalize();
    this.lastPoseSource = "offline-hybrid-drink" as RelativeJointPose["source"];
    this.targetTrunkZ = 0;
  }

  updateHybridLateralTransferPose(pose: HybridLateralTransferPose) {
    this.diagnosticTransition = null;
    this.targetTrunk.copy(pose.trunkQuaternion).normalize();
    this.targetShoulder.copy(pose.shoulderQuaternion).normalize();
    this.targetElbow.copy(pose.elbowQuaternion).normalize();
    this.targetWrist.copy(pose.wristQuaternion).normalize();
    this.lastPoseSource = "offline-hybrid-lateral-transfer" as RelativeJointPose["source"];
    this.targetTrunkZ = 0;
  }

  render(deltaSeconds: number) {
    this.animationLoopCount += 1;
    if (this.diagnosticTransition) {
      const transition = this.diagnosticTransition;
      transition.elapsedSeconds += Math.max(0, deltaSeconds);
      const progress = THREE.MathUtils.clamp(
        transition.elapsedSeconds / transition.durationSeconds,
        0,
        1
      );
      const eased = diagnosticEase(progress, transition.easing);
      this.rightShoulderPivot.quaternion.slerpQuaternions(
        transition.shoulderFrom,
        this.targetShoulder,
        eased
      );
      this.rightElbowPivot.quaternion.slerpQuaternions(
        transition.elbowFrom,
        this.targetElbow,
        eased
      );
      this.rightWristPivot.quaternion.slerpQuaternions(
        transition.wristFrom,
        this.targetWrist,
        eased
      );
      if (progress >= 1) this.diagnosticTransition = null;
    } else {
      const alpha = 1 - Math.exp(-10 * Math.max(0.001, Math.min(deltaSeconds, 0.05)));
      this.rightRoot.quaternion.slerp(this.targetTrunk, alpha);
      this.rightShoulderPivot.quaternion.slerp(this.targetShoulder, alpha);
      this.rightElbowPivot.quaternion.slerp(this.targetElbow, alpha);
      this.rightWristPivot.quaternion.slerp(this.targetWrist, alpha);
    }
    const alpha = 1 - Math.exp(-10 * Math.max(0.001, Math.min(deltaSeconds, 0.05)));
    this.group.position.z = THREE.MathUtils.lerp(this.group.position.z, this.targetTrunkZ, alpha);
    this.rightShoulderPivot.updateMatrixWorld(true);
    this.rightRoot.updateMatrixWorld(true);
    this.rightElbowPivot.updateMatrixWorld(true);
    this.rightWristPivot.updateMatrixWorld(true);
    this.group.updateMatrixWorld(true);
  }

  dispose() {
    SegmentRigAdapter.rendererInstanceCount = Math.max(0, SegmentRigAdapter.rendererInstanceCount - 1);
  }

  applyDiagnosticPose(
    phase: "DANGLE" | "PREPARE" | "REACH" | "LIFT" | "APPROACH_MOUTH" | "RETURN",
    transition?: DiagnosticPoseTransition
  ) {
    const keyframe = variedKeyframe(phase, transition?.variation);
    this.targetShoulder.copy(shoulderQuaternion(keyframe));
    this.targetElbow.copy(elbowQuaternion(keyframe));
    this.targetWrist.copy(wristQuaternion(keyframe));
    this.targetTrunk.identity();
    this.targetTrunkZ = 0;
    this.diagnosticTransition = transition
      ? {
        elapsedSeconds: 0,
        durationSeconds: Math.max(0.05, transition.durationSeconds),
        easing: transition.easing,
        shoulderFrom: this.rightShoulderPivot.quaternion.clone(),
        elbowFrom: this.rightElbowPivot.quaternion.clone(),
        wristFrom: this.rightWristPivot.quaternion.clone()
      }
      : null;
  }

  applyShoulderAxisDebug(axis: "X" | "Y" | "Z", degrees = 12) {
    this.diagnosticTransition = null;
    const axisVector = axis === "X" ? new THREE.Vector3(1, 0, 0) : axis === "Y" ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(0, 0, 1);
    this.targetShoulder.setFromAxisAngle(axisVector, THREE.MathUtils.degToRad(degrees)).normalize();
    this.targetElbow.identity();
    this.targetWrist.identity();
    this.targetTrunk.identity();
    this.targetTrunkZ = 0;
  }

  applyElbowAxisDebug(axis: "X" | "Y" | "Z", degrees = 45) {
    this.diagnosticTransition = null;
    const axisVector = axis === "X" ? new THREE.Vector3(1, 0, 0) : axis === "Y" ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(0, 0, 1);
    this.targetShoulder.copy(shoulderQuaternion(DRINK_CUP_KEYFRAMES.REACH));
    this.targetElbow.setFromAxisAngle(axisVector, THREE.MathUtils.degToRad(degrees)).normalize();
    this.targetWrist.identity();
    this.targetTrunk.identity();
    this.targetTrunkZ = 0;
  }

  setTrunkCompensationHighlight(_active: boolean) {}

  getRightHandWorldPosition(target = new THREE.Vector3()) {
    return this.rightHandMarker.getWorldPosition(target);
  }

  getPivotAudit() {
    return {
      shoulder: this.pivotAudit.shoulder.toArray(),
      elbow: this.pivotAudit.elbow.toArray(),
      wrist: this.pivotAudit.wrist.toArray(),
      hand: this.pivotAudit.hand.toArray(),
      hierarchy: [
        "rightRoot",
        "rightShoulderPivot",
        "rightClavicleGroup",
        "rightScapulaGroup",
        "rightUpperArmGroup",
        "rightHumerusGroup",
        "rightUpperArmMuscleGroup",
        "rightElbowPivot",
        "rightForearmGroup",
        "rightRadiusGroup",
        "rightUlnaGroup",
        "rightForearmMuscleGroup",
        "rightWristPivot",
        "rightHandGroup",
        "rightHandBoneGroup",
        "rightHandSoftTissueGroup"
      ]
    };
  }

  getMotionDiagnostics() {
    return {
      rightShoulderPivot: this.rightShoulderPivot.quaternion.toArray(),
      rightElbowPivot: this.rightElbowPivot.quaternion.toArray(),
      rightWristPivot: this.rightWristPivot.quaternion.toArray(),
      shoulderTarget: this.targetShoulder.toArray(),
      elbowTarget: this.targetElbow.toArray(),
      wristTarget: this.targetWrist.toArray(),
      rightHandWorldPosition: this.getRightHandWorldPosition(new THREE.Vector3()).toArray(),
      axisMapping: {
        shoulderFlexion: DRINK_CUP_AXIS_MAPPING.shoulderFlexion.label,
        shoulderAbduction: DRINK_CUP_AXIS_MAPPING.shoulderAbduction.label,
        elbowFlexion: DRINK_CUP_AXIS_MAPPING.elbowFlexion.label,
        wristExtension: DRINK_CUP_AXIS_MAPPING.wristExtension.label
      },
      animationLoopCount: this.animationLoopCount,
      rendererInstanceCount: SegmentRigAdapter.rendererInstanceCount,
      poseSource: this.lastPoseSource
    };
  }

  get label() {
    return this.matchedMeshes > 0
      ? `右上肢分段解剖网格运动映射 - 已绑定 ${this.matchedMeshes} 个真实mesh`
      : "右上肢分段解剖网格运动映射 - 当前模型未暴露可绑定右上肢mesh";
  }

  private configurePivots(mappedMeshes: MappedSegmentMesh[]) {
    const humerus = this.unionFor(mappedMeshes, rightBoneBusinessIds.humerus);
    const radius = this.unionFor(mappedMeshes, rightBoneBusinessIds.radius);
    const ulna = this.unionFor(mappedMeshes, rightBoneBusinessIds.ulna);
    const hand = this.unionFor(mappedMeshes, rightBoneBusinessIds.handBones);
    const forearm = new THREE.Box3();
    if (radius) forearm.union(radius);
    if (ulna) forearm.union(ulna);

    const humerusCenter = humerus?.getCenter(new THREE.Vector3()) ?? new THREE.Vector3(-0.34, 1.86, -0.05);
    const forearmCenter = forearm.isEmpty() ? new THREE.Vector3(-0.5, 1.22, -0.03) : forearm.getCenter(new THREE.Vector3());
    const handCenter = hand?.getCenter(new THREE.Vector3()) ?? new THREE.Vector3(-0.58, 0.82, 0.08);

    const shoulderPivot = humerus
      ? new THREE.Vector3(humerusCenter.x, humerus.max.y, humerusCenter.z)
      : new THREE.Vector3(-0.34, 2.02, -0.05);
    const elbowPivot = humerus && !forearm.isEmpty()
      ? new THREE.Vector3(
        (humerusCenter.x + forearmCenter.x) / 2,
        (humerus.min.y + forearm.max.y) / 2,
        (humerusCenter.z + forearmCenter.z) / 2
      )
      : new THREE.Vector3(-0.46, 1.46, -0.04);
    const wristPivot = !forearm.isEmpty() && hand
      ? new THREE.Vector3(
        (forearmCenter.x + handCenter.x) / 2,
        (forearm.min.y + hand.max.y) / 2,
        (forearmCenter.z + handCenter.z) / 2
      )
      : new THREE.Vector3(-0.56, 0.96, 0.02);

    this.rightShoulderPivot.position.copy(shoulderPivot);
    this.rightElbowPivot.position.copy(elbowPivot.clone().sub(shoulderPivot));
    this.rightWristPivot.position.copy(wristPivot.clone().sub(elbowPivot));
    this.rightHandMarker.position.copy(handCenter.clone().sub(wristPivot));
    this.pivotAudit.shoulder.copy(shoulderPivot);
    this.pivotAudit.elbow.copy(elbowPivot);
    this.pivotAudit.wrist.copy(wristPivot);
    this.pivotAudit.hand.copy(handCenter);
  }

  private unionFor(mappedMeshes: MappedSegmentMesh[], businessId: string) {
    const box = new THREE.Box3();
    for (const { mesh, businessId: mappedBusinessId } of mappedMeshes) {
      if (mappedBusinessId === businessId) box.union(new THREE.Box3().setFromObject(mesh));
    }
    return box.isEmpty() ? undefined : box;
  }

  private tintMesh(mesh: THREE.Mesh, businessId: string) {
    const material = Array.isArray(mesh.material) ? mesh.material[0] : mesh.material;
    if (!(material instanceof THREE.MeshStandardMaterial)) return;
    material.transparent = true;
    material.opacity = businessId.includes("right") ? 0.94 : 0.3;
    material.emissive = new THREE.Color(businessId.includes("right") ? "#0a6978" : "#122c32");
    material.emissiveIntensity = businessId.includes("right") ? 0.1 : 0.02;
  }

  private targetGroupFor(businessId: string, segment: Segment) {
    if (businessId === rightBoneBusinessIds.clavicle) return this.rightClavicleGroup;
    if (businessId === rightBoneBusinessIds.scapula) return this.rightScapulaGroup;
    if (businessId === rightBoneBusinessIds.humerus) return this.rightHumerusGroup;
    if (businessId === rightBoneBusinessIds.radius) return this.rightRadiusGroup;
    if (businessId === rightBoneBusinessIds.ulna) return this.rightUlnaGroup;
    if (businessId === rightBoneBusinessIds.handBones) return this.rightHandBoneGroup;
    if (segment === "upperArm") return this.rightUpperArmMuscleGroup;
    if (segment === "forearm") return this.rightForearmMuscleGroup;
    if (segment === "hand") return this.rightHandSoftTissueGroup;
    return this.rightShoulderPivot;
  }
}

function variedKeyframe(
  phase: "DANGLE" | "PREPARE" | "REACH" | "LIFT" | "APPROACH_MOUTH" | "RETURN",
  variation?: DiagnosticPoseVariation
) {
  const base = DRINK_CUP_KEYFRAMES[phase];
  if (!variation) return base;
  const phaseScale = phase === "REACH" ? 0.45 : phase === "LIFT" ? 0.8 : phase === "RETURN" ? 0.35 : 1;
  return {
    shoulderFlexionDeg: THREE.MathUtils.clamp(base.shoulderFlexionDeg + variation.shoulderFlexionDeg * phaseScale, 2, 38),
    shoulderAbductionDeg: THREE.MathUtils.clamp(base.shoulderAbductionDeg + variation.shoulderAbductionDeg * phaseScale, 1, 12),
    shoulderRotationDeg: THREE.MathUtils.clamp(base.shoulderRotationDeg + variation.shoulderRotationDeg * phaseScale, -8, 12),
    elbowFlexionDeg: THREE.MathUtils.clamp(base.elbowFlexionDeg + variation.elbowFlexionDeg * phaseScale, 8, 90),
    wristExtensionDeg: THREE.MathUtils.clamp(base.wristExtensionDeg + variation.wristExtensionDeg * phaseScale, -12, 14),
    wristPronationDeg: THREE.MathUtils.clamp(base.wristPronationDeg + variation.wristPronationDeg * phaseScale, 4, 28)
  };
}

function diagnosticEase(progress: number, easing: DiagnosticPoseEasing) {
  if (easing === "easeInOutSine") return -(Math.cos(Math.PI * progress) - 1) / 2;
  if (easing === "easeInOutCubic") {
    return progress < 0.5
      ? 4 * progress * progress * progress
      : 1 - Math.pow(-2 * progress + 2, 3) / 2;
  }
  if (easing === "easeOutCubic") return 1 - Math.pow(1 - progress, 3);
  return progress * progress * progress * (progress * (progress * 6 - 15) + 10);
}
