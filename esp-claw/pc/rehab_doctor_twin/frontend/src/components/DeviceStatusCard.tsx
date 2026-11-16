import { useState } from "react";
import type { DeviceStatus } from "../contracts/realtimeMessages";
import { connectionStatusText, resolvedConnectionStatus } from "../utils/deviceStatus";

export function DeviceStatusCard({ device, sessionId }: { device: DeviceStatus; sessionId: string }) {
  const [open, setOpen] = useState(false);
  const sensorType = (device.sensor_type ?? "").toLowerCase();
  const isSemg = sensorType === "semg";
  const status = resolvedConnectionStatus(device);
  const statusLabel = connectionStatusText(status);

  return (
    <article className={`device-card ${device.category === "system_service" ? "service-card" : ""}`}>
      <header>
        <span className={`dot ${status}`} />
        <div>
          <h2>{device.name}</h2>
          <p>{device.device_id}{device.channel_id ? ` 路 ${device.channel_id}` : ""}</p>
        </div>
        <b>{statusLabel}</b>
      </header>
      <div className="device-quick-row">
        <span>类型 <b>{device.category === "system_service" ? "系统服务" : device.sensor_type}</b></span>
        <span>接收频率 <b>{device.sample_rate}</b></span>
        <span>数据包 <b>{device.packet_count ?? "--"}</b></span>
      </div>
      <button className="details-toggle" onClick={() => setOpen((value) => !value)}>
        {open ? "收起详情" : "展开详情"}
      </button>
      {open && (
        <div className="device-detail-grid">
          <span>运行状态 <b>{statusLabel}</b></span>
          <span>最后时间戳 <b>{device.updated_at ? new Date(device.updated_at).toLocaleTimeString() : "--"}</b></span>
          <span>最近 sequence <b>{device.last_seq ?? 0}</b></span>
          <span>累计丢包 <b>{device.packet_loss ?? 0}</b></span>
          <span>乱序包 <b>{device.out_of_order_dropped ?? 0}</b></span>
          <span>异常次数 <b>{device.error_count ?? 0}</b></span>
          <span>当前会话 <b>{sessionId}</b></span>
          {isSemg && <span>真实字段 <b>latest / RMS / MAV / peak_abs</b></span>}
        </div>
      )}
    </article>
  );
}
