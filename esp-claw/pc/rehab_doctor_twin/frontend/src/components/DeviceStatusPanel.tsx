import type { DeviceStatus } from "../contracts/realtimeMessages";

export function DeviceStatusPanel({ devices }: { devices: DeviceStatus[] }) {
  const waitingDevices = ["ESP32-P4", "上臂IMU", "前臂IMU", "腕部IMU", "躯干IMU", "sEMG采集", "评分服务"].map((name) => ({
    name, mode: "等待真实设备" as const, online: false, sample_rate: "--", latency_ms: null, quality: null, updated_at: ""
  }));
  return (
    <section className="panel">
      <h2>设备状态</h2>
      <div className="device-list">
        {(devices.length ? devices : waitingDevices).map((device) => (
          <div className="device-row" key={device.name}>
            <span className={device.online ? "dot online" : "dot offline"} />
            <strong>{device.name}</strong>
            <small>{device.sample_rate}</small>
            <small>{device.latency_ms == null ? "--" : `${device.latency_ms} ms`}</small>
            <small>{device.quality == null ? "--" : `${device.quality}%`}</small>
          </div>
        ))}
      </div>
    </section>
  );
}
