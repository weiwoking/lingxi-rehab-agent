import type { EmgPayload, TelemetryPayload } from "../contracts/realtimeMessages";
import type {
  SessionResultDetail,
  SessionResultRepetition,
  SessionTrajectory,
  SessionTrajectoryFrame
} from "../services/sessionResultsApi";

export const MAX_HISTORY_VISIBLE_POINTS = 120;

export const COMPENSATION_LABELS: Record<string, string> = {
  LF: "躯干前倾",
  TR: "躯干旋转",
  TREMOR: "手部抖动"
};

const MUSCLE_LABELS = {
  CH1: "指伸肌",
  CH2: "肱二头肌",
  CH3: "肱三头肌",
  CH4: "尺侧腕伸肌"
} as const;

const MUSCLE_IDS = {
  CH1: "finger_extensors_right",
  CH2: "biceps_brachii_right",
  CH3: "triceps_brachii_right",
  CH4: "extensor_carpi_ulnaris_right"
} as const;

export interface NormalizedFrame {
  frame: SessionTrajectoryFrame;
  relativeMs: number;
}

export interface NormalizedRepTimeline {
  sessionId: string;
  repIndex: number;
  frames: NormalizedFrame[];
  durationMs: number;
  firstTimestampMs: number;
  lastTimestampMs: number;
  playbackAvailable: boolean;
  playbackError: string | null;
}

export interface OfflineRepPlan {
  rep: SessionResultRepetition;
  trajectory: SessionTrajectory | null;
  timeline: NormalizedRepTimeline;
  startMs: number;
  endMs: number;
  lfDetected: boolean;
  compensationLabels: string[];
  emgScale: Record<"CH1" | "CH2" | "CH3" | "CH4", number>;
}

export interface OfflinePlaybackPlan {
  sessionId: string;
  repCount: number;
  validRepCount: number;
  plans: OfflineRepPlan[];
  totalDurationMs: number;
  unavailableReasons: string[];
}

export function normalizeRepTimeline(trajectory: SessionTrajectory | null | undefined): NormalizedRepTimeline {
  if (!trajectory) return emptyTimeline("", 0, "trajectory_not_loaded");
  if (!trajectory.playback_available) {
    return emptyTimeline(trajectory.session_id, trajectory.rep_index, trajectory.playback_error || "trajectory_unavailable");
  }
  const firstTimestamp = finiteOrNull(trajectory.frames[0]?.time_ms);
  if (firstTimestamp === null) return emptyTimeline(trajectory.session_id, trajectory.rep_index, "trajectory_missing_time_ms");

  const frames: NormalizedFrame[] = [];
  for (const frame of trajectory.frames) {
    const timestamp = finiteOrNull(frame.time_ms);
    if (timestamp === null) return emptyTimeline(trajectory.session_id, trajectory.rep_index, "trajectory_invalid_time_ms");
    frames.push({ frame, relativeMs: Math.max(0, timestamp - firstTimestamp) });
  }
  if (!frames.length) return emptyTimeline(trajectory.session_id, trajectory.rep_index, "trajectory_empty");

  const lastTimestamp = finiteOrNull(frames.at(-1)?.frame.time_ms) ?? firstTimestamp;
  const declaredDuration = finiteOrNull(trajectory.duration_ms);
  return {
    sessionId: trajectory.session_id,
    repIndex: trajectory.rep_index,
    frames,
    durationMs: Math.max(0, lastTimestamp - firstTimestamp) || Math.max(0, declaredDuration ?? 0),
    firstTimestampMs: firstTimestamp,
    lastTimestampMs: lastTimestamp,
    playbackAvailable: true,
    playbackError: null
  };
}

export function buildOfflinePlaybackPlan(
  detail: SessionResultDetail,
  trajectories: Map<number, SessionTrajectory>
): OfflinePlaybackPlan {
  const validReps = [...detail.repetitions].filter((rep) => rep.valid).sort((a, b) => a.rep_index - b.rep_index);
  let cursor = 0;
  const unavailableReasons: string[] = [];
  const plans = validReps.map((rep) => {
    const trajectory = trajectories.get(rep.rep_index) ?? null;
    const timeline = normalizeRepTimeline(trajectory);
    const availability = resolveSegmentAvailability(rep, trajectory);
    if (!availability.available) unavailableReasons.push(`${detail.session_id} rep ${rep.rep_index}: ${availability.reason}`);
    const durationMs = timeline.playbackAvailable ? timeline.durationMs : 0;
    const plan: OfflineRepPlan = {
      rep,
      trajectory,
      timeline,
      startMs: cursor,
      endMs: cursor + durationMs,
      lfDetected: resolveRepLfState(rep),
      compensationLabels: compensationLabels(rep.compensation?.types ?? []),
      emgScale: trajectory ? trajectoryEmgScale(trajectory) : defaultEmgScale()
    };
    cursor += durationMs;
    return plan;
  });
  return {
    sessionId: detail.session_id,
    repCount: detail.repetitions.length,
    validRepCount: validReps.length,
    plans,
    totalDurationMs: cursor,
    unavailableReasons
  };
}

export function resolveSegmentAvailability(rep: SessionResultRepetition, trajectory: SessionTrajectory | null | undefined) {
  if (!rep.valid) return { available: false, reason: rep.invalid_reason || "rep_invalid" };
  if (!trajectory) return { available: false, reason: "trajectory_not_loaded" };
  if (!trajectory.playback_available) return { available: false, reason: trajectory.playback_error || "trajectory_unavailable" };
  if (!trajectory.frames.length) return { available: false, reason: "trajectory_empty" };
  return { available: true, reason: null };
}

export function resolveFrameAtElapsedMs(timeline: NormalizedRepTimeline, elapsedMs: number): SessionTrajectoryFrame | null {
  if (!timeline.playbackAvailable || !timeline.frames.length) return null;
  const target = Math.max(0, elapsedMs);
  let selected = timeline.frames[0].frame;
  for (const item of timeline.frames) {
    if (item.relativeMs > target) break;
    selected = item.frame;
  }
  return selected;
}

export function frameAtElapsedMs(trajectory: SessionTrajectory, elapsedMs: number): SessionTrajectoryFrame | null {
  return resolveFrameAtElapsedMs(normalizeRepTimeline(trajectory), elapsedMs);
}

export function resolvePlanAtElapsedMs(plan: OfflinePlaybackPlan, elapsedMs: number) {
  if (!plan.plans.length) return null;
  const bounded = Math.max(0, Math.min(elapsedMs, Math.max(0, plan.totalDurationMs)));
  return plan.plans.find((item) => bounded >= item.startMs && bounded <= item.endMs) ?? plan.plans.at(-1) ?? null;
}

export function resolveFrameInPlanAtElapsedMs(plan: OfflineRepPlan, elapsedMs: number) {
  return resolveFrameAtElapsedMs(plan.timeline, Math.max(0, elapsedMs - plan.startMs));
}

export function resolveSessionNeutralFrame(plan: OfflinePlaybackPlan): SessionTrajectoryFrame | null {
  for (const repPlan of plan.plans) {
    const first = repPlan.timeline.frames[0]?.frame;
    if (first) return first;
  }
  return null;
}

export function resolveSessionNeutralCalibration(plan: OfflinePlaybackPlan) {
  const frame = resolveSessionNeutralFrame(plan);
  if (!frame) return null;
  return frameToTelemetry(frame, plan.plans[0]?.trajectory);
}

export function resolveRepLfState(rep: SessionResultRepetition) {
  return Boolean(rep.compensation?.detected && rep.compensation.types?.includes("LF"));
}

export function frameToTelemetry(frame: SessionTrajectoryFrame, trajectory: SessionTrajectory | null | undefined): TelemetryPayload | null {
  if (!isFiniteFrameTime(frame)) return null;
  const quaternion = trajectory?.orientation_format === "quaternion";
  const euler = trajectory?.orientation_format === "euler";
  if (quaternion && hasCompleteQuaternionPose(frame)) {
    return {
      ...eulerTelemetry(frame),
      action_phase: "offline_csv_repetition_playback",
      imu_pose: {
        schema_version: "rehab.imu_pose.v1",
        mapping_mode: "relative_quaternion_live",
        source: "LIVE",
        quaternion_order: "w,x,y,z",
        sensors: {
          upper_arm_imu: imuPose("upper_arm_imu", frame.imu1!, frame.time_ms),
          lower_back_imu: imuPose("lower_back_imu", frame.imu2!, frame.time_ms),
          forearm_imu: imuPose("forearm_imu", frame.imu3!, frame.time_ms),
          wrist_imu: imuPose("wrist_imu", frame.imu4!, frame.time_ms)
        }
      }
    };
  }
  if (euler || hasAnyEulerPose(frame)) return eulerTelemetry(frame);
  return null;
}

export function trajectoryEmgScale(trajectory: SessionTrajectory) {
  const maxByChannel = defaultEmgScale();
  for (const frame of trajectory.frames) {
    for (const [key, channel] of [
      ["emg1", "CH1"],
      ["emg2", "CH2"],
      ["emg3", "CH3"],
      ["emg4", "CH4"]
    ] as const) {
      const value = emgValue(frame[key]);
      if (value !== null) maxByChannel[channel] = Math.max(maxByChannel[channel], Math.abs(value));
    }
  }
  return maxByChannel;
}

export function frameToEmg(frame: SessionTrajectoryFrame, scale: Record<"CH1" | "CH2" | "CH3" | "CH4", number>): EmgPayload {
  return {
    CH1: emgChannel(emgValue(frame.emg1), "CH1", scale.CH1),
    CH2: emgChannel(emgValue(frame.emg2), "CH2", scale.CH2),
    CH3: emgChannel(emgValue(frame.emg3), "CH3", scale.CH3),
    CH4: emgChannel(emgValue(frame.emg4), "CH4", scale.CH4)
  };
}

export function compensationLabels(types: string[] = []) {
  return types.map((type) => COMPENSATION_LABELS[type] ?? `未知代偿类型：${type}`);
}

export function trajectorySeries(trajectory: SessionTrajectory, elapsedMs: number) {
  const timeline = normalizeRepTimeline(trajectory);
  const cursorMs = timeline.firstTimestampMs + Math.max(0, elapsedMs);
  return {
    cursorMs,
    imu: [
      makeImuSeries(trajectory, cursorMs, "imu1", "IMU1 上臂"),
      makeImuSeries(trajectory, cursorMs, "imu2", "IMU2 躯干"),
      makeImuSeries(trajectory, cursorMs, "imu3", "IMU3 前臂"),
      makeImuSeries(trajectory, cursorMs, "imu4", "IMU4 手腕")
    ],
    emg: [
      makeEmgSeries(trajectory, cursorMs, "emg1", "EMG1 指伸肌"),
      makeEmgSeries(trajectory, cursorMs, "emg2", "EMG2 肱二头肌"),
      makeEmgSeries(trajectory, cursorMs, "emg3", "EMG3 肱三头肌"),
      makeEmgSeries(trajectory, cursorMs, "emg4", "EMG4 尺侧腕伸肌")
    ]
  };
}

function eulerTelemetry(frame: SessionTrajectoryFrame): TelemetryPayload {
  return {
    shoulder_flexion_deg: subtractIfBoth(frame.imu1?.pitch, frame.imu2?.pitch),
    shoulder_abduction_deg: subtractIfBoth(frame.imu1?.roll, frame.imu2?.roll),
    shoulder_rotation_deg: subtractIfBoth(frame.imu1?.yaw, frame.imu2?.yaw),
    elbow_flexion_deg: subtractIfBoth(frame.imu3?.pitch, frame.imu1?.pitch),
    forearm_pronation_deg: subtractIfBoth(frame.imu3?.yaw, frame.imu1?.yaw),
    wrist_flexion_deg: subtractIfBoth(frame.imu4?.pitch, frame.imu3?.pitch),
    trunk_flexion_deg: finiteOrNull(frame.imu2?.pitch),
    trajectory_match: null,
    phase_progress: null,
    spatial_error_cm: null,
    action_phase: "offline_csv_repetition_playback"
  };
}

function makeImuSeries(trajectory: SessionTrajectory, cursorMs: number, key: "imu1" | "imu2" | "imu3" | "imu4", label: string) {
  const allPoints = trajectory.frames
    .map((frame) => ({ timeMs: finiteOrNull(frame.time_ms), value: finiteOrNull(frame[key]?.pitch) }))
    .filter((point): point is { timeMs: number; value: number } => point.timeMs !== null && point.value !== null);
  return { label, field: "pitch", totalPoints: allPoints.length, points: playbackWindow(allPoints, cursorMs) };
}

function makeEmgSeries(trajectory: SessionTrajectory, cursorMs: number, key: "emg1" | "emg2" | "emg3" | "emg4", label: string) {
  const field = firstAvailableEmgField(trajectory, key);
  const allPoints = trajectory.frames
    .map((frame) => ({ timeMs: finiteOrNull(frame.time_ms), value: emgValue(frame[key]) }))
    .filter((point): point is { timeMs: number; value: number } => point.timeMs !== null && point.value !== null);
  return { label, field, totalPoints: allPoints.length, points: playbackWindow(allPoints, cursorMs) };
}

function playbackWindow(points: Array<{ timeMs: number; value: number }>, cursorMs: number) {
  return points.filter((point) => point.timeMs <= cursorMs).slice(-MAX_HISTORY_VISIBLE_POINTS);
}

function firstAvailableEmgField(trajectory: SessionTrajectory, key: "emg1" | "emg2" | "emg3" | "emg4") {
  for (const field of ["rms", "mav", "latest"] as const) {
    if (trajectory.frames.some((frame) => finiteOrNull(frame[key]?.[field]) !== null)) return field;
  }
  return "missing";
}

function emgValue(sample: SessionTrajectoryFrame["emg1"]): number | null {
  return finiteOrNull(sample?.rms) ?? finiteOrNull(sample?.mav) ?? finiteOrNull(sample?.latest);
}

function emgChannel(value: number | null, channel: "CH1" | "CH2" | "CH3" | "CH4", scale: number) {
  return {
    label: MUSCLE_LABELS[channel],
    muscle_id: MUSCLE_IDS[channel],
    activation: value === null ? null : Math.max(0, Math.min(1, Math.abs(value) / Math.max(1, scale))),
    quality: value === null ? 0 : 1,
    primary: true
  };
}

function imuPose(sensorId: "lower_back_imu" | "upper_arm_imu" | "forearm_imu" | "wrist_imu", sample: NonNullable<SessionTrajectoryFrame["imu1"]>, timeMs: number) {
  return {
    sensor_id: sensorId,
    quaternion: {
      w: finiteOrNull(sample.qw)!,
      x: finiteOrNull(sample.qx)!,
      y: finiteOrNull(sample.qy)!,
      z: finiteOrNull(sample.qz)!
    },
    calibrated: true,
    quality: 1,
    timestamp_ms: timeMs
  };
}

function isFiniteFrameTime(frame: SessionTrajectoryFrame) {
  return finiteOrNull(frame.time_ms) !== null && finiteOrNull(frame.log_seq) !== null;
}

function hasCompleteQuaternionPose(frame: SessionTrajectoryFrame) {
  return [frame.imu1, frame.imu2, frame.imu3, frame.imu4].every((sample) =>
    sample && finiteOrNull(sample.qw) !== null && finiteOrNull(sample.qx) !== null && finiteOrNull(sample.qy) !== null && finiteOrNull(sample.qz) !== null
  );
}

function hasAnyEulerPose(frame: SessionTrajectoryFrame) {
  return [frame.imu1, frame.imu2, frame.imu3, frame.imu4].some((sample) =>
    sample && (finiteOrNull(sample.roll) !== null || finiteOrNull(sample.pitch) !== null || finiteOrNull(sample.yaw) !== null)
  );
}

function subtractIfBoth(a: number | null | undefined, b: number | null | undefined) {
  const left = finiteOrNull(a);
  const right = finiteOrNull(b);
  return left !== null && right !== null ? left - right : null;
}

function emptyTimeline(sessionId: string, repIndex: number, reason: string): NormalizedRepTimeline {
  return {
    sessionId,
    repIndex,
    frames: [],
    durationMs: 0,
    firstTimestampMs: 0,
    lastTimestampMs: 0,
    playbackAvailable: false,
    playbackError: reason
  };
}

function defaultEmgScale(): Record<"CH1" | "CH2" | "CH3" | "CH4", number> {
  return { CH1: 1, CH2: 1, CH3: 1, CH4: 1 };
}

function finiteOrNull(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
