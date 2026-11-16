export interface CloudSyncSummary {
  enabled: boolean;
  cloud_online: boolean;
  state: "disabled" | "offline" | "online" | string;
  auto_upload_enabled: boolean;
  not_ready_count: number;
  not_uploaded_count: number;
  queued_count: number;
  uploading_count: number;
  synced_count: number;
  failed_count: number;
  conflict_count: number;
  last_sync_at?: string | null;
  last_check_at?: string | null;
}

export interface CloudSyncActionResult {
  accepted: boolean;
  session_id?: string;
  status?: string;
  reason?: string | null;
  eligible_count?: number;
  queued_count?: number;
  skipped_count?: number;
  auto_upload_enabled?: boolean;
}

export async function fetchCloudSyncSummary(): Promise<CloudSyncSummary> {
  const response = await fetch("/api/cloud-sync/summary");
  if (!response.ok) throw new Error("读取云端同步状态失败");
  return response.json() as Promise<CloudSyncSummary>;
}

export async function uploadCloudSyncSession(sessionId: string): Promise<CloudSyncActionResult> {
  const response = await fetch(`/api/cloud-sync/sessions/${encodeURIComponent(sessionId)}/upload`, {
    method: "POST"
  });
  if (!response.ok) throw new Error("提交单条上传失败");
  return response.json() as Promise<CloudSyncActionResult>;
}

export async function uploadPendingCloudSync(): Promise<CloudSyncActionResult> {
  const response = await fetch("/api/cloud-sync/upload-pending", { method: "POST" });
  if (!response.ok) throw new Error("提交批量上传失败");
  return response.json() as Promise<CloudSyncActionResult>;
}

export async function setCloudSyncAutoUpload(enabled: boolean): Promise<CloudSyncActionResult> {
  const response = await fetch("/api/cloud-sync/auto-upload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled })
  });
  if (!response.ok) throw new Error("更新自动同步开关失败");
  return response.json() as Promise<CloudSyncActionResult>;
}

export async function refreshCloudSyncStatus(): Promise<CloudSyncSummary> {
  const response = await fetch("/api/cloud-sync/check", { method: "POST" });
  if (!response.ok) throw new Error("刷新云端状态失败");
  return response.json() as Promise<CloudSyncSummary>;
}
