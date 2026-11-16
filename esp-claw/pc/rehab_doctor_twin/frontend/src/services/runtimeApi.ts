import type { RehabReport } from "../contracts/report";
import type { RehabSnapshot } from "../contracts/realtimeMessages";
import type { SessionSummary } from "./patientApi";

export interface TrainingState {
  session_id: string | null;
  state: string;
  recording_row_count: number;
  recording_duration_seconds: number;
  report_ready: boolean;
  can_start: boolean;
  data_stream_status: string;
  udp_stream_status?: string;
  parse_status?: string;
}

async function postTraining(path: "start" | "stop"): Promise<TrainingState> {
  const response = await fetch(`/api/training/${path}`, { method: "POST" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "训练操作失败");
  return (payload as { training: TrainingState }).training;
}

export const startTraining = () => postTraining("start");
export const stopTraining = () => postTraining("stop");

export interface CsvReplayControlResult {
  accepted: boolean;
  reason?: string;
  stream_source_mode: string;
  stream_source_label: string;
  csv_replay_state: string;
  csv_replay_ready?: boolean;
  csv_replay_generation?: number | null;
  csv_replay_speed: number | null;
  csv_current_row: number | null;
  csv_total_rows: number | null;
  csv_file_name: string;
}

export interface CsvReplayControlResponse {
  csv_replay: CsvReplayControlResult;
  snapshot?: RehabSnapshot;
}

export async function controlCsvReplay(action: "play" | "pause" | "resume" | "restart", speed?: number): Promise<CsvReplayControlResponse> {
  const response = await fetch("/api/patient/runtime/csv-replay", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, speed })
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "CSV replay control failed");
  const result = (payload as CsvReplayControlResponse).csv_replay;
  if (!result?.accepted) {
    throw new Error(result?.reason || "CSV replay control was rejected");
  }
  return payload as CsvReplayControlResponse;
}

export async function fetchPatientRuntime(): Promise<RehabSnapshot> {
  const response = await fetch("/api/patient/runtime");
  if (!response.ok) throw new Error("无法读取患者训练状态");
  return ((await response.json()) as { snapshot: RehabSnapshot }).snapshot;
}

export async function fetchPatientReports(): Promise<SessionSummary[]> {
  const response = await fetch("/api/patient/reports");
  if (!response.ok) throw new Error("无法读取历史训练报告");
  return ((await response.json()) as { sessions: SessionSummary[] }).sessions;
}

export async function fetchPatientReport(sessionId: string): Promise<RehabReport> {
  const response = await fetch(`/api/patient/reports/${encodeURIComponent(sessionId)}`);
  if (!response.ok) throw new Error("该训练报告暂不可用");
  return ((await response.json()) as { report: RehabReport }).report;
}
