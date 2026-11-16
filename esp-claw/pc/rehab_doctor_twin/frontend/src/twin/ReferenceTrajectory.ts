import * as THREE from "three";
import type { TelemetryPayload } from "../contracts/realtimeMessages";

export class ReferenceTrajectory {
  readonly group = new THREE.Group();
  private actualLine: THREE.Line;
  private targetPoint: THREE.Mesh;
  private actualPoints: THREE.Vector3[] = [];
  private referencePoints: THREE.Vector3[];

  constructor() {
    this.referencePoints = this.buildDrinkCupPath();
    const referenceGeometry = new THREE.BufferGeometry().setFromPoints(this.referencePoints);
    const referenceMaterial = new THREE.LineBasicMaterial({ color: "#47d5d1", transparent: true, opacity: 0.48 });
    const referenceLine = new THREE.Line(referenceGeometry, referenceMaterial);
    referenceLine.name = "standard_right_drink_cup_trajectory";

    this.actualLine = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3()]),
      new THREE.LineBasicMaterial({ color: "#e6f3ff", transparent: true, opacity: 0.72 })
    );
    this.actualLine.name = "actual_right_hand_trajectory";
    this.targetPoint = new THREE.Mesh(
      new THREE.OctahedronGeometry(0.045, 0),
      new THREE.MeshStandardMaterial({ color: "#f0c15a", emissive: "#5f3d05", emissiveIntensity: 0.25 })
    );
    this.targetPoint.name = "standard_trajectory_target_point";
    this.targetPoint.position.copy(this.referencePoints[this.referencePoints.length - 1]);
    this.group.add(referenceLine, this.actualLine, this.targetPoint);
    this.group.visible = false;
  }

  update(telemetry: TelemetryPayload, actualRightHandPosition?: THREE.Vector3) {
    const phaseProgress = typeof telemetry.phase_progress === "number" && Number.isFinite(telemetry.phase_progress)
      ? telemetry.phase_progress
      : 0;
    const p = THREE.MathUtils.clamp(phaseProgress / 100, 0, 1);
    const targetIndex = Math.min(this.referencePoints.length - 1, Math.round(p * (this.referencePoints.length - 1)));
    this.targetPoint.position.copy(this.referencePoints[targetIndex]);
    if (!actualRightHandPosition) return;
    this.actualPoints.push(actualRightHandPosition.clone());
    if (this.actualPoints.length > 120) this.actualPoints.shift();
    this.actualLine.geometry.dispose();
    this.actualLine.geometry = new THREE.BufferGeometry().setFromPoints(this.actualPoints);
  }

  reset() {
    this.actualPoints = [];
    this.actualLine.geometry.dispose();
    this.actualLine.geometry = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3()]);
  }

  private buildDrinkCupPath() {
    const anchors = [
      new THREE.Vector3(-0.58, 0.72, 0.04),
      new THREE.Vector3(-0.62, 0.96, 0.46),
      new THREE.Vector3(-0.60, 1.02, 0.50),
      new THREE.Vector3(-0.47, 1.22, 0.42),
      new THREE.Vector3(-0.30, 1.46, 0.30),
      new THREE.Vector3(-0.60, 0.98, 0.46),
      new THREE.Vector3(-0.58, 0.72, 0.04)
    ];
    const points: THREE.Vector3[] = [];
    const samplesPerSegment = 9;
    for (let i = 0; i < anchors.length - 1; i += 1) {
      for (let j = 0; j < samplesPerSegment; j += 1) {
        const t = j / samplesPerSegment;
        points.push(anchors[i].clone().lerp(anchors[i + 1], this.smoothstep(t)));
      }
    }
    points.push(anchors[anchors.length - 1].clone());
    return points;
  }

  private smoothstep(t: number) {
    return t * t * (3 - 2 * t);
  }
}
