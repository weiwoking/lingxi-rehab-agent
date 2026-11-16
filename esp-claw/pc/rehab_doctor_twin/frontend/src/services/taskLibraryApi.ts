export interface TaskRegistryEntry {
  task_id: string;
  task_name?: string;
  display_name: string;
  registry_version?: string;
  motion_family?: "drink_water" | "lateral_transfer" | string;
  object_type?: "cup" | "ball" | string;
  score_profile?: string;
  twin_profile?: "drink_water" | "lateral_transfer" | string;
  level?: number;
  ui_enabled: boolean;
  formal_score_supported: boolean;
  showcase_only: boolean;
  internal_score_task_name?: string;
  score_mode?: string;
  description: string;
  sort_order: number;
}

export interface PatientTask extends TaskRegistryEntry {
  enabled: boolean;
  training_eligible: boolean;
  active?: boolean;
  created_at?: string;
  updated_at?: string;
}

export type SupportedTask = TaskRegistryEntry;

async function request(path: string, init?: RequestInit) {
  const response = await fetch(path, init);
  if (response.status === 204) return null;
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "任务库操作失败");
  return payload;
}

export async function fetchTaskLibrary(): Promise<{ tasks: PatientTask[]; supported_registry: SupportedTask[] }> {
  return request("/api/doctor/tasks");
}

export async function addPatientTask(taskId: string): Promise<PatientTask> {
  const value = await request("/api/doctor/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_id: taskId })
  });
  return value.task;
}

export async function updatePatientTask(
  taskId: string,
  changes: Partial<Pick<PatientTask, "enabled" | "active" | "sort_order">>
): Promise<PatientTask> {
  const value = await request(`/api/doctor/tasks/${encodeURIComponent(taskId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes)
  });
  return value.task;
}

export async function removePatientTask(taskId: string): Promise<void> {
  await request(`/api/doctor/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
}
