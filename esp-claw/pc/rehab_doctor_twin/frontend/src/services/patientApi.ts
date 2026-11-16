import type { DeviceStatus } from "../contracts/realtimeMessages";
import { isImuDevice, isSemgDevice, resolvedConnectionStatus } from "../utils/deviceStatus";

export interface PatientSummary {
  patient_id: string;
  display_name: string;
  profile_label: string;
  stage: string;
  affected_side: string;
  brs: string;
  age_range: string;
  today_training_count: number;
  latest_score: number | null;
  risk_level: string;
  current_status: string;
  current_task: string;
  latest_training_time: string;
  fatigue_risk: string;
  device_status: string;
}

export type DemoPatient = PatientSummary;

export interface SessionSummary {
  patient_id: string;
  session_id: string;
  scenario_id: string;
  task: string;
  task_name?: string;
  internal_score_task_name?: string;
  original_task_name?: string;
  task_registry_version?: string | null;
  task_registry_status?: string | null;
  unsupported_task_reason?: string | null;
  motion_family?: "drink_water" | "lateral_transfer" | string | null;
  object_type?: "cup" | "ball" | string | null;
  score_profile?: string | null;
  twin_profile?: "drink_water" | "lateral_transfer" | string | null;
  task_level?: number | null;
  target_reps: number;
  completed_reps: number;
  status: string;
  final_score: number | null;
  report_ready: boolean;
  source_mode: string;
  started_at_ms: number;
  ended_at_ms?: number;
  duration_ms?: number | null;
  rows_written?: number | null;
  valid_row_count?: number | null;
  invalid_row_count?: number | null;
  duplicate_row_count?: number | null;
  training_status?: string;
  scoring_status?: string;
  score_status?: string;
  score_error_code?: string | null;
  score_error_message?: string | null;
  score_valid?: boolean;
  session_v12_status?: "not_started" | "pending" | "running" | "completed" | "failed" | string;
  session_v12_error?: string | null;
  session_v12_baseline_version?: string | null;
  fallback_used?: boolean;
  score_fallback_used?: boolean;
  action_source?: string | null;
  score_action_source?: string | null;
  confidence?: string | null;
  score_confidence?: string | null;
  repetition_count?: number | null;
  valid_repetition_count?: number | null;
  segmentation?: Record<string, unknown>;
  task_finalized?: boolean;
  auto_score_submission_state?: string | null;
  auto_score_last_error?: string | null;
  auto_score_retry_count?: number;
  auto_score_next_retry_at_ms?: number | null;
  source_origin?: string;
  record_kind?: "formal" | "showcase" | string;
  formal?: boolean;
  prediction_eligible?: boolean;
  log_drop_count?: number;
  cloud_sync?: {
    session_id: string;
    status:
      | "not_ready"
      | "not_uploaded"
      | "queued"
      | "uploading"
      | "synced"
      | "failed"
      | "conflict"
      | string;
    attempt_count: number;
    synced_at?: string | null;
    last_error?: string | null;
    remote_record_id?: string | null;
    next_retry_at?: string | null;
  };
}

export async function fetchPatients(): Promise<PatientSummary[]> {
  const response = await fetch("/api/patients");
  if (!response.ok) throw new Error("读取患者列表失败");
  return ((await response.json()) as { patients: PatientSummary[] }).patients;
}

export async function fetchPatient(patientId: string): Promise<PatientSummary> {
  const response = await fetch(`/api/patients/${encodeURIComponent(patientId)}`);
  if (!response.ok) throw new Error("读取患者失败");
  return ((await response.json()) as { patient: PatientSummary }).patient;
}

export async function fetchSessions(patientId: string): Promise<SessionSummary[]> {
  const response = await fetch(`/api/patients/${encodeURIComponent(patientId)}/sessions`);
  if (!response.ok) throw new Error("读取历史会话失败");
  return ((await response.json()) as { sessions: SessionSummary[] }).sessions;
}

export async function fetchSession(patientId: string, sessionId: string): Promise<SessionSummary & { logging_status?: { drop_count: number; queue_size: number } }> {
  const response = await fetch(`/api/patients/${encodeURIComponent(patientId)}/sessions/${encodeURIComponent(sessionId)}`);
  if (!response.ok) throw new Error("读取会话失败");
  return ((await response.json()) as { session: SessionSummary & { logging_status?: { drop_count: number; queue_size: number } } }).session;
}

export function summarizeDevices(devices: DeviceStatus[]) {
  const sensors = devices.filter((device) => device.category !== "system_service");
  const services = devices.filter((device) => device.category === "system_service");
  const sensorList = sensors.length ? sensors : devices;
  const isNormal = (device: DeviceStatus) => resolvedConnectionStatus(device) === "normal";
  const online = sensorList.filter(isNormal).length;
  const imuOnline = sensorList.filter((device) => isImuDevice(device) && isNormal(device)).length;
  const imuTotal = sensorList.filter(isImuDevice).length;
  const semgOnline = sensorList.filter((device) => isSemgDevice(device) && isNormal(device)).length;
  const semgTotal = sensorList.filter(isSemgDevice).length;
  const serviceOnline = services.filter(isNormal).length;
  const latencyValues = sensorList
    .map((device) => device.latency_ms)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const qualityValues = sensorList
    .map((device) => device.quality)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const avgLatency = latencyValues.length
    ? Math.round(latencyValues.reduce((sum, value) => sum + value, 0) / latencyValues.length)
    : null;
  const integrity = qualityValues.length
    ? Math.round(qualityValues.reduce((sum, value) => sum + value, 0) / qualityValues.length)
    : null;
  return {
    online,
    total: sensorList.length,
    imuOnline,
    imuTotal,
    semgOnline,
    semgTotal,
    serviceOnline,
    serviceTotal: services.length,
    avgLatency,
    integrity
  };
}
