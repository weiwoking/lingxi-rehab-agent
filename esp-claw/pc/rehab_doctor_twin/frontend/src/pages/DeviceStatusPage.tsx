import { DeviceQualitySummary } from "../components/DeviceQualitySummary";
import { DeviceStatusCard } from "../components/DeviceStatusCard";
import { semgDisplayLabels, sensorTopology } from "../config/sensorTopology";
import type { DeviceStatus } from "../contracts/realtimeMessages";
import { useRehabStore } from "../store/rehabStore";
import { connectionStatusText, isSemgDevice, resolvedConnectionStatus } from "../utils/deviceStatus";

const fallbackDevices: DeviceStatus[] = [
  ...sensorTopology.imu.map((node) => ({
    device_id: node.sensor_id,
    name: node.chinese_name,
    category: "sensor" as const,
    sensor_type: "IMU" as const,
    role: node.role,
    mode: "等待真实设备" as const,
    online: false,
    sample_rate: `${node.raw_sampling_hz} Hz`,
    raw_sampling_hz: node.raw_sampling_hz,
    visualization_hz: node.visualization_hz,
    latency_ms: null,
    avg_latency_ms: null,
    quality: null,
    last_seq: 0,
    packet_loss: 0,
    out_of_order_dropped: 0,
    error_count: 0,
    updated_at: "",
    connection_status: "unknown" as const
  })),
  ...sensorTopology.semg.channels.map((channel) => ({
    device_id: channel.sensor_id,
    name: semgDisplayLabels[channel.channel_id],
    category: "sensor" as const,
    sensor_type: "sEMG" as const,
    topology_mode: sensorTopology.topology_mode,
    hub_id: sensorTopology.semg.hub_id,
    channel_id: channel.channel_id,
    business_muscle_id: channel.business_muscle_id,
    mode: "等待真实设备" as const,
    online: false,
    sample_rate: `${sensorTopology.semg.raw_sampling_hz} Hz raw / ${sensorTopology.semg.visualization_hz} Hz view`,
    raw_sampling_hz: sensorTopology.semg.raw_sampling_hz,
    visualization_hz: sensorTopology.semg.visualization_hz,
    latency_ms: null,
    avg_latency_ms: null,
    quality: null,
    last_seq: 0,
    packet_loss: 0,
    out_of_order_dropped: 0,
    error_count: 0,
    updated_at: "",
    connection_status: "unknown" as const
  })),
  ...sensorTopology.system_services.map((service) => ({
    device_id: service.service_id,
    name: service.name,
    category: "system_service" as const,
    mode: "等待真实设备" as const,
    online: false,
    sample_rate: "event",
    latency_ms: null,
    avg_latency_ms: null,
    quality: null,
    last_seq: 0,
    packet_loss: 0,
    out_of_order_dropped: 0,
    error_count: 0,
    updated_at: "",
    connection_status: "unknown" as const
  }))
];

export function DeviceStatusPage() {
  const devices = useRehabStore((s) => s.devices);
  const wsStatus = useRehabStore((s) => s.wsStatus);
  const lastMessageAt = useRehabStore((s) => s.lastMessageAt);
  const snapshot = useRehabStore((s) => s.snapshot);
  const list = devices.length ? devices : fallbackDevices;
  const sensorNodes = list.filter((device) => device.category !== "system_service").map(withSemgDisplayName);
  const systemServices = list.filter((device) => device.category === "system_service");
  const semgHubStatus = resolveSemgHubStatus(sensorNodes);
  const sessionId = snapshot?.session_id ?? "SESSION_IDLE";

  return (
    <main className="workspace-page device-page">
      <header className="page-title">
        <h1>设备状态</h1>
        <span>4 IMU、4 路 sEMG 与系统服务运行摘要</span>
      </header>
      <DeviceQualitySummary devices={list} wsStatus={wsStatus} lastMessageAt={lastMessageAt} sessionId={sessionId} />
      {sensorTopology.topology_mode === "shared_hub_four_channels" && (
        <section className="panel semg-hub-panel">
          <h2>sEMG 采集主机：{connectionStatusText(semgHubStatus)}</h2>
          <p>{Object.values(semgDisplayLabels).join("；")}</p>
        </section>
      )}
      <h2 className="section-caption">传感器节点</h2>
      <section className="device-card-grid">
        {sensorNodes.map((device) => <DeviceStatusCard key={device.device_id ?? device.name} device={device} sessionId={sessionId} />)}
      </section>
      <h2 className="section-caption">系统服务</h2>
      <section className="device-card-grid service-grid">
        {systemServices.map((device) => <DeviceStatusCard key={device.device_id ?? device.name} device={device} sessionId={sessionId} />)}
      </section>
    </main>
  );
}

function withSemgDisplayName(device: DeviceStatus): DeviceStatus {
  const channel = sensorTopology.semg_channels.find((item) =>
    item.sensor_id === device.device_id || item.channel_id === device.channel_id
  );
  return channel ? { ...device, name: semgDisplayLabels[channel.channel_id] } : device;
}

function resolveSemgHubStatus(devices: DeviceStatus[]) {
  const statuses = devices
    .filter(isSemgDevice)
    .map((device) => resolvedConnectionStatus(device));
  if (statuses.some((status) => status === "normal")) return "normal";
  if (statuses.some((status) => status === "stale")) return "stale";
  return "unknown";
}
