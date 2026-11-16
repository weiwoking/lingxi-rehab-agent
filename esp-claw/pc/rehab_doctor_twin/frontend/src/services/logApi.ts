export type LogType = "imu" | "emg" | "events" | "scores" | "device_status";

export interface RawLogQuery {
  type: LogType;
  sensor_id?: string;
  start_ms?: string;
  end_ms?: string;
  limit?: number;
  offset?: number;
}

export interface RawLogResult {
  items: Record<string, unknown>[];
  limit: number;
  offset: number;
  has_more: boolean;
  total_seen: number;
}

export async function fetchLogs(patientId: string, sessionId: string, query: RawLogQuery): Promise<RawLogResult> {
  const params = new URLSearchParams();
  params.set("type", query.type);
  if (query.sensor_id) params.set("sensor_id", query.sensor_id);
  if (query.start_ms) params.set("start_ms", query.start_ms);
  if (query.end_ms) params.set("end_ms", query.end_ms);
  params.set("limit", String(query.limit ?? 200));
  params.set("offset", String(query.offset ?? 0));
  const response = await fetch(`/api/patients/${encodeURIComponent(patientId)}/sessions/${encodeURIComponent(sessionId)}/logs?${params}`);
  if (!response.ok) throw new Error("读取日志失败");
  return response.json();
}

export function exportLogUrl(patientId: string, sessionId: string, type: LogType, format: "csv" | "jsonl") {
  const params = new URLSearchParams({ type, format });
  return `/api/patients/${encodeURIComponent(patientId)}/sessions/${encodeURIComponent(sessionId)}/logs/export?${params}`;
}

