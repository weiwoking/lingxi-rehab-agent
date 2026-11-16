import { startDemo } from "../services/demoApi";
import type { AppState } from "../contracts/state";

export async function checkCompetitionDemoReadiness(wsStatus: AppState["wsStatus"]) {
  const [health, model] = await Promise.all([
    fetch("/api/health").then((r) => r.ok).catch(() => false),
    fetch("/api/model/status").then((r) => r.ok ? r.json() as Promise<{ body_glb_present: boolean }> : null).catch(() => null)
  ]);
  const notes: string[] = [];
  if (!health) notes.push("backend health failed");
  if (!model?.body_glb_present) notes.push("body.glb \u5c1a\u672a\u5c31\u7eea");
  if (wsStatus !== "open") notes.push(`websocket ${wsStatus}`);
  return { backend: health, websocket: wsStatus === "open", model: Boolean(model?.body_glb_present), notes };
}

export async function startCompetitionDemo(patientId: string, profile: "standard" | "fast" = "standard") {
  const speed = profile === "fast" ? 3.2 : 1.8;
  const response = await fetch("/api/competition-demo/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ patient_id: patientId, speed_mode: profile, speed })
  });
  if (!response.ok) { const body = await response.json().catch(() => ({ detail: "快速训练启动失败" })); throw new Error(body.detail ?? "快速训练启动失败"); }
  return response.json() as Promise<{ session_id: string; scenario_id: string; status: string }>;
}

export async function startStandardDemo(patientId: string) {
  return startDemo(patientId);
}
