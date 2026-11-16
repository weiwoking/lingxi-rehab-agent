import { useMemo, useState } from "react";
import { semgDisplayLabels } from "../config/sensorTopology";

export function RawLogTable({ items, type }: { items: Record<string, unknown>[]; type: string }) {
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null);
  const rows = useMemo(
    () => items.map((item, index) => ({
      item,
      index,
      time: formatTime(item.timestamp_ms),
      received: formatTime(item.received_at_ms)
    })),
    [items]
  );

  return (
    <section className="raw-log-table polished-log-table">
      <table>
        <thead><tr><th>接收时间</th><th>session_id</th><th>sequence</th><th>类型</th><th>来源</th><th>有效/质量</th><th>真实字段摘要</th><th>详情</th></tr></thead>
        <tbody>
          {rows.map(({ item, index, time, received }) => (
            <tr key={`${String(item.sequence ?? item.seq)}-${index}`}>
              <td><b>{received !== "--" ? received : time}</b></td>
              <td>{String(item.session_id ?? "--")}</td>
              <td>{String(item.sequence ?? item.seq ?? "--")}</td>
              <td>{String(item.log_type ?? type)}</td>
              <td>{String(item.source ?? item.sensor_id ?? "--")}</td>
              <td>{qualitySummary(item)}</td>
              <td>{summarize(item, String(item.log_type ?? type))}</td>
              <td><button onClick={() => setSelected(item)}>JSON</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {!items.length && <div className="empty-state">暂无最新数据。设备开始传输后将在此显示接收记录。</div>}
      {selected && (
        <div className="json-drawer">
          <header><strong>完整 JSON · 原始时间戳 {String(selected.timestamp_ms ?? "--")}</strong><button onClick={() => setSelected(null)}>关闭</button></header>
          <pre>{JSON.stringify(sanitizeForDisplay(selected), null, 2)}</pre>
        </div>
      )}
    </section>
  );
}

function sanitizeForDisplay(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sanitizeForDisplay);
  if (!value || typeof value !== "object") return value;
  const hidden = new Set(["raw_source", "data_origin", "field_sources"]);
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter(([key]) => !hidden.has(key))
      .map(([key, item]) => [key, sanitizeForDisplay(item)])
  );
}

function formatTime(value: unknown) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return "--";
  const date = new Date(numeric);
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}:${String(date.getSeconds()).padStart(2, "0")}.${String(date.getMilliseconds()).padStart(3, "0")}`;
}

function summarize(item: Record<string, unknown>, type: string) {
  if (type === "imu") return summarizeImu(item.imu);
  if (type === "emg") return summarizeEmg(item.emg);
  if (type === "scores") {
    const score = item.score as { final_score?: number } | undefined;
    return `final_score ${score?.final_score ?? "--"}`;
  }
  if (type === "device_status") return JSON.stringify(item.quality ?? {});
  return String(item.business_event ?? item.text ?? item.title ?? "--");
}

function summarizeImu(value: unknown) {
  if (!value || typeof value !== "object") return "--";
  const imu = value as Record<string, Record<string, unknown>>;
  return Object.entries(imu).map(([key, sample]) => `${key}: angle ${format(sample.angle_x_deg)}/${format(sample.angle_y_deg)}/${format(sample.angle_z_deg)}° gyro ${format(sample.gyro_x_dps)}/${format(sample.gyro_y_dps)}/${format(sample.gyro_z_dps)}`).join(" · ");
}

function summarizeEmg(value: unknown) {
  if (!value || typeof value !== "object") return "--";
  const emg = value as Record<string, unknown>;
  return (Object.keys(semgDisplayLabels) as Array<keyof typeof semgDisplayLabels>).map((channel, index) =>
    `${semgDisplayLabels[channel]}：RMS ${arrayValue(emg.rms, index)} / MAV ${arrayValue(emg.mav, index)} / Peak ${arrayValue(emg.peak_abs, index)} / latest ${arrayValue(emg.latest, index)}`
  ).join(" · ");
}

function qualitySummary(item: Record<string, unknown>) {
  const quality = item.quality;
  if (!quality || typeof quality !== "object") return String(item.result ?? "--");
  const value = quality as Record<string, unknown>;
  return `online ${String(value.online ?? "--")} · gap ${String(value.gap_count ?? "--")} · checksum ${String(value.checksum_error_count ?? "--")}`;
}

function arrayValue(value: unknown, index: number) {
  return Array.isArray(value) ? format(value[index]) : "--";
}

function format(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "--";
}
