import type { PatientSummary, SessionSummary } from "./patientApi";

export interface DoctorOverview {
  patient: PatientSummary;
  latest_session: SessionSummary | null;
  weekly_valid_session_count: number;
  formal_report_count: number;
  score_trend: Array<{ session_id: string; started_at_ms: number; final_score: number }>;
  task_library_count: number;
  recent_sessions: SessionSummary[];
  attention_items: Array<{ session_id: string; status: string; message: string }>;
}

export async function fetchDoctorOverview(): Promise<DoctorOverview> {
  const response = await fetch("/api/doctor/overview");
  if (!response.ok) throw new Error("无法读取医生端总览");
  return response.json();
}
