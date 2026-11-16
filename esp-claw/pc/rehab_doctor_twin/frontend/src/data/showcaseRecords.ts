import type { RehabReport } from "../contracts/report";
import type { ActionScoreSummary } from "../contracts/realtimeMessages";
import type { SessionSummary } from "../services/patientApi";

export interface PrintableActionRow {
  rep_index: number;
  final_score: number;
  imu_dtw_score: number;
  emg_score: number;
  compensation: string;
  fatigue: string;
}

export const DRINK_TASK_NAME = "饮水动作训练";
export const PATIENT_ID = "P-0217";

const records = [
  {
    sessionId: "SHOWCASE_DRINK_001",
    publicLabel: "训练记录 01",
    startedAtMs: Date.UTC(2026, 6, 26, 1, 20, 18),
    durationSec: 72,
    completedReps: 4,
    finalScore: 82.7,
    imuScore: 81.5,
    emgScore: 80.6,
    feedback: "饮水动作完成较完整，末段控制仍可继续强化。",
    doctorNote: "患者能够完成完整饮水动作，建议继续加强放杯后手臂回落阶段的控制。"
  },
  {
    sessionId: "SHOWCASE_DRINK_002",
    publicLabel: "训练记录 02",
    startedAtMs: Date.UTC(2026, 6, 26, 1, 35, 18),
    durationSec: 78,
    completedReps: 5,
    finalScore: 86.4,
    imuScore: 85.2,
    emgScore: 84.3,
    feedback: "动作节奏稳定，抬杯、饮水和回收路径较完整。",
    doctorNote: "患者动作节奏较稳定，建议维持当前训练强度，并注意保持躯干稳定。"
  }
] as const;

export const showcaseSessions: SessionSummary[] = records.map((record) => ({
  patient_id: PATIENT_ID,
  session_id: record.sessionId,
  scenario_id: "drink_water_right",
  task: DRINK_TASK_NAME,
  target_reps: record.completedReps,
  completed_reps: record.completedReps,
  status: "COMPLETED",
  final_score: record.finalScore,
  report_ready: true,
  source_mode: "showcase",
  source_origin: "system_showcase_fixture",
  started_at_ms: record.startedAtMs,
  ended_at_ms: record.startedAtMs + record.durationSec * 1000,
  duration_ms: record.durationSec * 1000,
  rows_written: 0,
  valid_row_count: record.completedReps,
  score_status: "showcase",
  score_valid: true,
  record_kind: "showcase",
  formal: false,
  prediction_eligible: false
}));

export const showcaseReports: Record<string, RehabReport> = Object.fromEntries(
  records.map((record) => [
    record.sessionId,
    makeReport({
      sessionId: record.sessionId,
      score: record.finalScore,
      imuScore: record.imuScore,
      emgScore: record.emgScore,
      completedReps: record.completedReps,
      durationSec: record.durationSec,
      trainingTime: new Date(record.startedAtMs).toISOString(),
      summary: record.feedback
    })
  ])
);

export const showcaseDoctorNotes: Record<string, string> = Object.fromEntries(
  records.map((record) => [record.sessionId, record.doctorNote])
);

export const printableActionRows: Record<string, PrintableActionRow[]> = Object.fromEntries(
  records.map((record) => [record.sessionId, makePrintableRows(record)])
);

export function isShowcaseSession(sessionId: string) {
  return records.some((record) => record.sessionId === normalizeSessionId(sessionId));
}

export function getPublicRecordLabel(sessionId: string) {
  return records.find((record) => record.sessionId === normalizeSessionId(sessionId))?.publicLabel || "训练记录";
}

export function getPublicRecordIndex(sessionId: string) {
  const index = records.findIndex((record) => record.sessionId === normalizeSessionId(sessionId));
  return index >= 0 ? index + 1 : 0;
}

export function formatRecordDateTime(timestampMs?: number | null) {
  if (!timestampMs) return "时间暂无数据";
  return new Date(timestampMs).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

export function formatDurationSeconds(value?: number | null) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "暂无";
  return `${Math.round(value)} 秒`;
}

export function normalizeSessionId(sessionId: string) {
  return String(sessionId || "").trim().toUpperCase();
}

function makePrintableRows(record: (typeof records)[number]): PrintableActionRow[] {
  const count = record.completedReps;
  return Array.from({ length: count }, (_, index) => {
    const center = index - (count - 1) / 2;
    const polish = record.sessionId.endsWith("002") ? 0.35 : 0.22;
    return {
      rep_index: index + 1,
      final_score: round(record.finalScore + center * polish),
      imu_dtw_score: round(record.imuScore + center * (polish + 0.08)),
      emg_score: round(record.emgScore + center * (polish + 0.04)),
      compensation: "未检测到明显代偿",
      fatigue: "低"
    };
  });
}

function makeReport({
  sessionId,
  score,
  imuScore,
  emgScore,
  completedReps,
  durationSec,
  trainingTime,
  summary
}: {
  sessionId: string;
  score: number;
  imuScore: number;
  emgScore: number;
  completedReps: number;
  durationSec: number;
  trainingTime: string;
  summary: string;
}): RehabReport {
  const actionRows: ActionScoreSummary[] = makePrintableRows(
    records.find((record) => record.sessionId === sessionId) ?? records[0]
  ).map((row) => ({
    rep_index: row.rep_index,
    valid: true,
    final_score: row.final_score,
    imu_dtw_score: row.imu_dtw_score,
    emg_score: row.emg_score,
    compensation: { detected: false },
    feedback: [summary]
  }));

  return {
    title: "上肢康复训练评估报告",
    patient: {
      patient_id: PATIENT_ID,
      affected_side: "右侧上肢",
      brs: "BRS IV",
      task: DRINK_TASK_NAME,
      difficulty: "常规训练",
      training_time: trainingTime,
      session_id: sessionId,
      device_id: "PRESENTATION_RECORD"
    },
    completion: {
      planned_reps: completedReps,
      completed_reps: completedReps,
      valid_reps: completedReps,
      duration_sec: durationSec,
      pause_count: 0,
      fully_completed: true,
      end_reason: "completed"
    },
    score: {
      valid: true,
      final_score: score,
      score_version: "front_presentation_v1",
      completed_at: trainingTime,
      data_quality_level: "良好",
      available_metrics: ["IMU DTW", "EMG", "代偿评估"],
      action_summaries: actionRows,
      subscores: {},
      compensation: { detected: false },
      fatigue: { level: "low" },
      brs: "BRS IV",
      feedback: [summary],
      warnings: []
    },
    available_metrics: ["IMU DTW", "EMG", "代偿评估"],
    action_summaries: actionRows,
    rep_details: [],
    kinematics: {},
    emg_analysis: {},
    compensation_analysis: { detected: false },
    fatigue_analysis: { level: "low" },
    summary,
    next_recommendations: [summary],
    versions: {
      source_mode: "showcase",
      source_origin: "system_showcase_fixture",
      score_version: "front_presentation_v1",
      schema_version: "rehab_score_result_v1",
      device_id: "PRESENTATION_RECORD",
      generated_at: trainingTime
    },
    disclaimer: "",
    demo_notice: ""
  };
}

function round(value: number) {
  return Number(value.toFixed(2));
}
