import type { RehabReport } from "../contracts/report";

export async function fetchCurrentReport(): Promise<RehabReport> {
  const response = await fetch("/api/report/current");
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "报告尚未准备好" }));
    throw new Error(body.detail ?? "报告尚未准备好");
  }
  return ((await response.json()) as { report: RehabReport }).report;
}

export async function fetchSessionReport(patientId: string, sessionId: string): Promise<RehabReport> {
  const response = await fetch(`/api/patients/${encodeURIComponent(patientId)}/sessions/${encodeURIComponent(sessionId)}/report`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "该会话报告不可用" }));
    throw new Error(body.detail ?? "该会话报告不可用");
  }
  return ((await response.json()) as { report: RehabReport }).report;
}
