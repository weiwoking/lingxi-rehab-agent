import { useSyncExternalStore } from "react";
import type { AppState, EmgSeriesPoint, ImuSeriesPoint } from "../contracts/state";
import type { RealtimeMessage, RehabSnapshot } from "../contracts/realtimeMessages";

const MAX_SENSOR_POINTS = 120;

let state: AppState = {
  wsStatus: "connecting",
  lastMessageAt: null,
  latencyMs: null,
  currentSessionId: null,
  currentReplayGeneration: null,
  lastSeq: 0,
  snapshot: null,
  telemetry: null,
  emg: null,
  devices: [],
  scores: [],
  alert: null,
  chart: [],
  imuSeries: {},
  emgSeries: {}
};

const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

export function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getState() {
  return state;
}

export function useRehabStore<T>(selector: (value: AppState) => T): T {
  return useSyncExternalStore(subscribe, () => selector(state), () => selector(state));
}

export function setWsStatus(wsStatus: AppState["wsStatus"]) {
  state = { ...state, wsStatus };
  emit();
}

export function applySnapshot(snapshot: RehabSnapshot) {
  const sessionChanged = snapshot.session_id !== state.currentSessionId;
  const nextReplayGeneration = getReplayGeneration(snapshot);
  const replayGenerationChanged = (
    !sessionChanged
    && nextReplayGeneration !== null
    && nextReplayGeneration !== state.currentReplayGeneration
  );
  state = appendRuntimeSamples({
    ...state,
    currentSessionId: snapshot.session_id,
    currentReplayGeneration: nextReplayGeneration,
    snapshot,
    telemetry: snapshot.telemetry,
    emg: snapshot.emg,
    devices: snapshot.devices,
    scores: snapshot.scores,
    alert: snapshot.alert
  }, snapshot, sessionChanged || replayGenerationChanged);
  emit();
}

export function applyRealtimeMessage(message: RealtimeMessage) {
  if (message.type !== "rehab.state") {
    if (
      state.currentSessionId
      && state.currentSessionId !== "SESSION_IDLE"
      && message.session_id !== state.currentSessionId
    ) return;
    if (message.seq <= state.lastSeq) return;
  }

  const now = Date.now();
  const latencyMs = message.timestamp_ms > 0 ? Math.max(0, now - message.timestamp_ms) : state.latencyMs;
  let next: AppState = { ...state, lastSeq: Math.max(state.lastSeq, message.seq), lastMessageAt: now, latencyMs };

  if (message.type === "rehab.state") {
    const snapshot = message.payload.snapshot;
    const sessionChanged = snapshot.session_id !== state.currentSessionId;
    const nextReplayGeneration = getReplayGeneration(snapshot);
    const replayGenerationChanged = (
      !sessionChanged
      && nextReplayGeneration !== null
      && nextReplayGeneration !== state.currentReplayGeneration
    );
    next = {
      ...next,
      lastSeq: sessionChanged ? message.seq : Math.max(state.lastSeq, message.seq),
      currentSessionId: snapshot.session_id,
      currentReplayGeneration: nextReplayGeneration,
      snapshot,
      telemetry: snapshot.telemetry,
      emg: snapshot.emg,
      devices: snapshot.devices,
      scores: snapshot.scores,
      alert: snapshot.alert,
      chart: next.chart
    };
    next = appendRuntimeSamples(next, snapshot, sessionChanged || replayGenerationChanged);
  } else if (message.type === "rehab.telemetry") {
    next.telemetry = message.payload;
    if (next.snapshot) next.snapshot = { ...next.snapshot, telemetry: message.payload };
  } else if (message.type === "rehab.emg") {
    next.emg = message.payload;
    if (next.snapshot) next.snapshot = { ...next.snapshot, emg: message.payload };
  } else if (message.type === "rehab.score") {
    next.scores = [...next.scores, message.payload].slice(-5);
    if (next.snapshot) {
      next.snapshot = {
        ...next.snapshot,
        scores: next.scores,
        task: { ...next.snapshot.task, completed_reps: message.payload.rep_index },
        quality: message.payload.quality
      };
    }
  } else if (message.type === "rehab.alert") {
    next.alert = message.payload;
    if (next.snapshot) next.snapshot = { ...next.snapshot, alert: message.payload };
  } else if (message.type === "rehab.event") {
    const events = [...(next.snapshot?.events ?? []), message.payload].slice(-8);
    if (next.snapshot) next.snapshot = { ...next.snapshot, events };
  } else if (message.type === "rehab.report_ready") {
    if (next.snapshot) next.snapshot = { ...next.snapshot, status: "REPORT_READY", report_ready: true };
  } else if (message.type === "device.status") {
    next.devices = message.payload.devices;
    if (next.snapshot) next.snapshot = { ...next.snapshot, devices: message.payload.devices };
  }

  state = next;
  emit();
}

function getReplayGeneration(snapshot: RehabSnapshot): number | null {
  return typeof snapshot.csv_replay_generation === "number" && Number.isFinite(snapshot.csv_replay_generation)
    ? snapshot.csv_replay_generation
    : null;
}

function appendRuntimeSamples(current: AppState, snapshot: RehabSnapshot, reset = false): AppState {
  const imuSeries = reset ? {} : { ...current.imuSeries };
  const emgSeries = reset ? {} : { ...current.emgSeries };
  for (const sample of snapshot.sensor_samples?.imu ?? []) {
    if (!sample.received_at_ms || !sample.valid) continue;
    const rows = imuSeries[sample.sensor_id] ?? [];
    if (rows.at(-1)?.sequence === sample.sequence) continue;
    imuSeries[sample.sensor_id] = [...rows, { ...sample, t: sample.received_at_ms } as ImuSeriesPoint].slice(-MAX_SENSOR_POINTS);
  }
  for (const sample of snapshot.sensor_samples?.emg ?? []) {
    if (!sample.received_at_ms || !sample.valid || !Number.isFinite(sample.rms)) continue;
    const rows = emgSeries[sample.sensor_id] ?? [];
    if (rows.at(-1)?.sequence === sample.sequence) continue;
    emgSeries[sample.sensor_id] = [...rows, { ...sample, t: sample.received_at_ms } as EmgSeriesPoint].slice(-MAX_SENSOR_POINTS);
  }
  return { ...current, imuSeries, emgSeries };
}
