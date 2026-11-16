export type SessionV12Status = "not_started" | "pending" | "running" | "completed" | "failed" | string;

export interface SessionResultSummary {
  session_id: string;
  task_name?: string | null;
  original_task_name?: string | null;
  task_registry_version?: string | null;
  task_registry_status?: string | null;
  unsupported_task_reason?: string | null;
  motion_family?: "drink_water" | "lateral_transfer" | string | null;
  object_type?: "cup" | "ball" | string | null;
  score_profile?: string | null;
  twin_profile?: "drink_water" | "lateral_transfer" | string | null;
  task_level?: number | null;
  session_v12_status?: SessionV12Status;
  session_v12_error?: string | null;
  session_v12_baseline_version?: string | null;
  source_mode?: string;
  source_origin?: string;
  started_at_ms?: number | null;
  duration_ms?: number | null;
  final_score?: number | null;
}

export interface SessionResultRepetition {
  rep_index: number;
  valid: boolean;
  start_time_ms?: number | null;
  end_time_ms?: number | null;
  duration_ms?: number | null;
  final_score?: number | null;
  subscores: Partial<Record<"speed_score" | "ROM_score" | "smoothness_score" | "coordination_score" | "muscle_score", number | null>>;
  compensation: {
    detected: boolean;
    types: string[];
    labels: string[];
  };
  feedback: string[];
  playback_available: boolean;
  playback_error?: string | null;
  invalid_reason?: string | null;
}

export interface SessionResultDetail {
  session_id: string;
  task_name?: string | null;
  original_task_name?: string | null;
  task_registry_version?: string | null;
  task_registry_status?: string | null;
  unsupported_task_reason?: string | null;
  motion_family?: "drink_water" | "lateral_transfer" | string | null;
  object_type?: "cup" | "ball" | string | null;
  score_profile?: string | null;
  twin_profile?: "drink_water" | "lateral_transfer" | string | null;
  task_level?: number | null;
  scoring_status: SessionV12Status;
  source_mode?: string;
  source_origin?: string;
  started_at?: string | null;
  ended_at?: string | null;
  duration_ms?: number | null;
  baseline_version?: string | null;
  repetition_count: number;
  valid_repetition_count: number;
  rejected_repetition_count: number;
  statistics: {
    average_score?: number | null;
    maximum_score?: number | null;
    minimum_score?: number | null;
    subscore_averages: SessionResultRepetition["subscores"];
  };
  repetitions: SessionResultRepetition[];
  scoring_freshness?: {
    status: "current" | "stale" | "unknown" | string;
    reasons: string[];
    stored_versions: Record<string, unknown>;
    current_versions: Record<string, unknown>;
  };
  rescore_allowed?: boolean;
  error?: string | null;
}

export interface BaselinePrecheck {
  ok: boolean;
  baseline_template: string;
  baseline_version?: string | null;
  segmentation_version?: string | null;
  sensor_mapping_version?: string | null;
}

export interface SessionTrajectoryFrame {
  time_ms: number;
  original_time_ms: number;
  log_seq: number;
  imu1?: ImuTrajectorySample;
  imu2?: ImuTrajectorySample;
  imu3?: ImuTrajectorySample;
  imu4?: ImuTrajectorySample;
  emg1?: EmgTrajectorySample;
  emg2?: EmgTrajectorySample;
  emg3?: EmgTrajectorySample;
  emg4?: EmgTrajectorySample;
}

export interface ImuTrajectorySample {
  sensor_id: string;
  qw?: number | null;
  qx?: number | null;
  qy?: number | null;
  qz?: number | null;
  roll?: number | null;
  pitch?: number | null;
  yaw?: number | null;
  gx?: number | null;
  gy?: number | null;
  gz?: number | null;
}

export interface EmgTrajectorySample {
  channel: "CH1" | "CH2" | "CH3" | "CH4" | string;
  latest?: number | null;
  rms?: number | null;
  mav?: number | null;
  peak?: number | null;
}

export interface SessionTrajectory {
  schema_version: "doctor_real_trajectory_v1" | string;
  session_id: string;
  rep_index: number;
  sensor_mapping_version: string;
  time_base: "original_timestamp" | string;
  playback_available: boolean;
  playback_error?: string | null;
  frame_count: number;
  duration_ms?: number | null;
  orientation_format: "quaternion" | "euler" | "unavailable" | string;
  frames: SessionTrajectoryFrame[];
}

const DETAIL_CACHE_MAX_ENTRIES = 64;
const TRAJECTORY_CACHE_MAX_ENTRIES = 32;
const detailCache = new Map<string, SessionResultDetail>();
const detailInflight = new Map<string, Promise<SessionResultDetail>>();
const trajectoryCache = new Map<string, SessionTrajectory>();
const trajectoryInflight = new Map<string, Promise<SessionTrajectory>>();

function rememberBounded<K, V>(cache: Map<K, V>, key: K, value: V, maxEntries: number) {
  if (cache.has(key)) cache.delete(key);
  cache.set(key, value);
  while (cache.size > maxEntries) {
    const oldest = cache.keys().next().value as K | undefined;
    if (oldest === undefined) break;
    cache.delete(oldest);
  }
}

function isImmutableCompletedDetail(value: SessionResultDetail) {
  return value.scoring_status === "completed" && value.rescore_allowed === false;
}

function trajectoryKey(sessionId: string, repIndex: number) {
  return `${sessionId}::${repIndex}`;
}

export async function fetchDoctorSessions(): Promise<{ baseline_precheck: BaselinePrecheck; sessions: SessionResultSummary[] }> {
  const response = await fetch("/api/sessions");
  if (!response.ok) throw new Error("read_session_results_failed");
  return response.json() as Promise<{ baseline_precheck: BaselinePrecheck; sessions: SessionResultSummary[] }>;
}

export async function fetchSessionResult(sessionId: string): Promise<SessionResultDetail> {
  const cached = detailCache.get(sessionId);
  if (cached) {
    detailCache.delete(sessionId);
    detailCache.set(sessionId, cached);
    return cached;
  }
  const inflight = detailInflight.get(sessionId);
  if (inflight) return inflight;
  const request = fetch(`/api/sessions/${encodeURIComponent(sessionId)}`)
    .then(async (response) => {
      if (!response.ok) throw new Error("read_session_result_failed");
      const value = ((await response.json()) as { session: SessionResultDetail }).session;
      if (isImmutableCompletedDetail(value)) {
        rememberBounded(detailCache, sessionId, value, DETAIL_CACHE_MAX_ENTRIES);
      }
      return value;
    })
    .finally(() => {
      detailInflight.delete(sessionId);
    });
  detailInflight.set(sessionId, request);
  return request;
}

export function prefetchSessionResult(sessionId: string): void {
  if (!sessionId || detailCache.has(sessionId) || detailInflight.has(sessionId)) return;
  void fetchSessionResult(sessionId).catch(() => {
    // Best-effort prefetch only. User-triggered fetches may retry normally.
  });
}

export async function fetchSessionResultUncached(sessionId: string): Promise<SessionResultDetail> {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
  if (!response.ok) throw new Error("read_session_result_failed");
  return ((await response.json()) as { session: SessionResultDetail }).session;
}

export async function fetchSessionTrajectory(sessionId: string, repIndex: number): Promise<SessionTrajectory> {
  const key = trajectoryKey(sessionId, repIndex);
  const cached = trajectoryCache.get(key);
  if (cached) {
    trajectoryCache.delete(key);
    trajectoryCache.set(key, cached);
    return cached;
  }
  const inflight = trajectoryInflight.get(key);
  if (inflight) return inflight;
  const request = fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/repetitions/${encodeURIComponent(String(repIndex))}/trajectory`
  )
    .then(async (response) => {
      if (!response.ok) throw new Error("read_session_trajectory_failed");
      const value = ((await response.json()) as { trajectory: SessionTrajectory }).trajectory;
      rememberBounded(trajectoryCache, key, value, TRAJECTORY_CACHE_MAX_ENTRIES);
      return value;
    })
    .finally(() => {
      trajectoryInflight.delete(key);
    });
  trajectoryInflight.set(key, request);
  return request;
}

export async function requestSessionRescore(sessionId: string): Promise<{ accepted: boolean; status: string; reason?: string | null }> {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/rescore`, { method: "POST" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = (payload as { detail?: { reason?: string; status?: string } }).detail;
    throw new Error(detail?.reason || "session_rescore_rejected");
  }
  detailCache.delete(sessionId);
  return ((payload as { rescore: { accepted: boolean; status: string; reason?: string | null } }).rescore);
}
