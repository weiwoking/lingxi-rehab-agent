import { sensorTopology } from "../config/sensorTopology";
import type { LogType, RawLogQuery } from "../services/logApi";

const sensors = [
  { value: "", label: "全部传感器" },
  ...sensorTopology.imu.map((node) => ({ value: node.sensor_id, label: node.chinese_name })),
  ...sensorTopology.semg.channels.map((channel) => ({ value: channel.sensor_id, label: `${channel.channel_id} ${channel.chinese_name}` }))
];

export function LogFilterBar({
  sessions,
  sessionId,
  query,
  paused,
  onSession,
  onQuery,
  onSearch,
  onPause,
  onClear,
  onExport
}: {
  sessions: string[];
  sessionId: string;
  query: RawLogQuery;
  paused: boolean;
  onSession: (value: string) => void;
  onQuery: (value: RawLogQuery) => void;
  onSearch: () => void;
  onPause: () => void;
  onClear: () => void;
  onExport: (format: "csv" | "jsonl") => void;
}) {
  return (
    <section className="log-filter-bar">
      <select value={sessionId} onChange={(event) => onSession(event.target.value)}>
        {sessions.map((id) => <option key={id} value={id}>{id}</option>)}
      </select>
      <select value={query.type} onChange={(event) => onQuery({ ...query, type: event.target.value as LogType, offset: 0 })}>
        <option value="imu">IMU</option>
        <option value="emg">sEMG</option>
        <option value="events">运行事件</option>
        <option value="scores">评分</option>
        <option value="device_status">设备状态</option>
      </select>
      <select value={query.sensor_id ?? ""} onChange={(event) => onQuery({ ...query, sensor_id: event.target.value || undefined, offset: 0 })}>
        {sensors.map((sensor) => <option key={sensor.value || "all"} value={sensor.value}>{sensor.label}</option>)}
      </select>
      <input value={query.start_ms ?? ""} onChange={(event) => onQuery({ ...query, start_ms: event.target.value, offset: 0 })} placeholder="开始时间戳ms" />
      <input value={query.end_ms ?? ""} onChange={(event) => onQuery({ ...query, end_ms: event.target.value, offset: 0 })} placeholder="结束时间戳ms" />
      <button onClick={onSearch}>搜索</button>
      <button onClick={onPause}>{paused ? "继续自动刷新" : "暂停自动刷新"}</button>
      <button onClick={onClear}>清除筛选</button>
      <button onClick={() => onExport("csv")}>导出CSV</button>
      <button onClick={() => onExport("jsonl")}>导出JSONL</button>
    </section>
  );
}
