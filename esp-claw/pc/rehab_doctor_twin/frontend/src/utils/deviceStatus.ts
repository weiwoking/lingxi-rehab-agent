import type { DeviceStatus, NodeConnectionStatus } from "../contracts/realtimeMessages";

export function resolvedConnectionStatus(device: DeviceStatus): NodeConnectionStatus {
  if (
    device.connection_status === "normal" ||
    device.connection_status === "stale" ||
    device.connection_status === "unknown"
  ) {
    return device.connection_status;
  }

  return device.online === true ? "normal" : "unknown";
}

export function isImuDevice(device: DeviceStatus) {
  return (device.sensor_type ?? "").toLowerCase() === "imu";
}

export function isSemgDevice(device: DeviceStatus) {
  return (device.sensor_type ?? "").toLowerCase() === "semg";
}

export function connectionStatusText(status: NodeConnectionStatus) {
  if (status === "normal") return "正常";
  if (status === "stale") return "数据中断";
  return "未知";
}
