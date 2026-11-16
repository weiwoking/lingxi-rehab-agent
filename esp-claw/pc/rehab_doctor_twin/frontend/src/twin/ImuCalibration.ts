import * as THREE from "three";
import type { ImuPoseFrame, ImuQuaternion } from "../contracts/realtimeMessages";

export type ImuSensorId = keyof ImuPoseFrame["sensors"];

function isValidQuaternion(value: ImuQuaternion) {
  return Number.isFinite(value.x) && Number.isFinite(value.y) && Number.isFinite(value.z) && Number.isFinite(value.w)
    && Math.hypot(value.x, value.y, value.z, value.w) > 0.00001;
}

function toThree(value: ImuQuaternion): THREE.Quaternion {
  if (!isValidQuaternion(value)) return new THREE.Quaternion();
  return new THREE.Quaternion(value.x, value.y, value.z, value.w).normalize();
}

export class ImuCalibration {
  private offsets = new Map<ImuSensorId, THREE.Quaternion>();
  private calibratedAt = 0;

  calibrateFromCurrentFrame(frame: ImuPoseFrame) {
    this.offsets.clear();
    (Object.keys(frame.sensors) as ImuSensorId[]).forEach((id) => {
      const sensor = frame.sensors[id];
      if (sensor) this.offsets.set(id, toThree(sensor.quaternion).invert());
    });
    this.calibratedAt = Date.now();
  }

  capture(frame: ImuPoseFrame) { this.calibrateFromCurrentFrame(frame); }

  resetCalibration() {
    this.offsets.clear();
    this.calibratedAt = 0;
  }

  isCalibrationReady() { return this.ready; }

  apply(id: ImuSensorId, raw: ImuQuaternion): THREE.Quaternion {
    const q = toThree(raw);
    const offset = this.offsets.get(id);
    return offset ? offset.clone().multiply(q).normalize() : q;
  }

  get ready() { return this.offsets.size >= 4; }
  get ageMs() { return this.calibratedAt ? Date.now() - this.calibratedAt : null; }
}
