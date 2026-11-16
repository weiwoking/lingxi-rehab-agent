import type { RehabSnapshot } from "../contracts/realtimeMessages";

export async function fetchCurrentState(): Promise<{ snapshot: RehabSnapshot }> {
  const response = await fetch("/api/state/current");
  if (!response.ok) throw new Error("读取当前状态失败");
  return response.json();
}
