export async function startDemo(patientId = "P-0217", speed = 1): Promise<{ session_id: string; status: string }> {
  const response = await fetch("/api/demo/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scenario_id: "drink_cup", patient_id: patientId, speed })
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "启动失败" }));
    throw new Error(body.detail ?? "启动失败");
  }
  return response.json();
}

export async function resetDemo(): Promise<void> {
  const response = await fetch("/api/demo/reset", { method: "POST" });
  if (!response.ok) throw new Error("重置失败");
}

export async function fetchCurrentState() {
  const response = await fetch("/api/state/current");
  if (!response.ok) throw new Error("读取当前状态失败");
  return response.json();
}
