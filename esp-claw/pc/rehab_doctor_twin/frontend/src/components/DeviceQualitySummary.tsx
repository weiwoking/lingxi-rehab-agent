import type { DeviceStatus } from "../contracts/realtimeMessages";
import { summarizeDevices } from "../services/patientApi";

export function DeviceQualitySummary({
  devices,
  wsStatus,
  lastMessageAt,
  sessionId
}: {
  devices: DeviceStatus[];
  wsStatus: string;
  lastMessageAt: number | null;
  sessionId: string;
}) {
  const summary = summarizeDevices(devices);
  return (
    <section className="device-summary">
      <div><span>传感器状态</span><strong>{summary.online}/{summary.total || 8} 通道在线</strong></div>
      <div><span>IMU</span><strong>{summary.imuOnline}/{summary.imuTotal || 4}</strong></div>
      <div><span>sEMG</span><strong>{summary.semgOnline}/{summary.semgTotal || 4}</strong></div>
      <div><span>系统链路</span><strong>{summary.serviceOnline}/{summary.serviceTotal || 4}</strong></div>
      <div><span>平均延迟</span><strong>{summary.avgLatency == null ? "--" : `${summary.avgLatency} ms`}</strong></div>
      <div><span>数据完整率</span><strong>{summary.integrity == null ? "--" : `${summary.integrity}%`}</strong></div>
      <div><span>WebSocket</span><strong>{wsStatus}</strong></div>
      <div><span>最近数据</span><strong>{lastMessageAt ? new Date(lastMessageAt).toLocaleTimeString() : "--"}</strong></div>
      <div><span>当前 session_id</span><strong>{sessionId}</strong></div>
    </section>
  );
}
