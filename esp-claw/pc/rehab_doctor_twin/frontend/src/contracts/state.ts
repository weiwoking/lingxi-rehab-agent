import type { AlertPayload, DeviceStatus, EmgPayload, EmgRuntimeSample, ImuRuntimeSample, RehabSnapshot, RepScoreMessagePayload, TelemetryPayload } from "./realtimeMessages";

export interface ImuSeriesPoint extends ImuRuntimeSample { t: number; }
export interface EmgSeriesPoint extends EmgRuntimeSample { t: number; }

export interface ChartPoint {
  t: number;
  phase: string;
  shoulder: number;
  elbow: number;
  wrist: number;
  CH1: number;
  CH2: number;
  CH3: number;
  CH4: number;
  score: number;
}

export interface AppState {
  wsStatus: "connecting" | "open" | "closed";
  lastMessageAt: number | null;
  latencyMs: number | null;
  currentSessionId: string | null;
  currentReplayGeneration: number | null;
  lastSeq: number;
  snapshot: RehabSnapshot | null;
  telemetry: TelemetryPayload | null;
  emg: EmgPayload | null;
  devices: DeviceStatus[];
  scores: RepScoreMessagePayload[];
  alert: AlertPayload | null;
  chart: ChartPoint[];
  imuSeries: Record<string, ImuSeriesPoint[]>;
  emgSeries: Record<string, EmgSeriesPoint[]>;
}
