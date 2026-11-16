import type { RehabReport } from "../contracts/report";
import type { ScorePayload } from "../contracts/realtimeMessages";

export const FALLBACK_SCORE_NOTICE = "未检测到完整喝水动作周期，本次结果采用低置信度降级评分。";

type UnknownRecord = Record<string, unknown>;

export function isFallbackScore(score: ScorePayload | UnknownRecord | null | undefined): boolean {
  const value = asRecord(score);
  const segmentation = asRecord(value.segmentation);
  return (
    segmentation.fallback_used === true
    || value.action_source === "fallback"
    || value.source === "fallback"
    || value.confidence === "low"
  );
}

export function isFallbackReport(report: RehabReport | null | undefined): boolean {
  return isFallbackScore(report?.score);
}

export function isFallbackSession(value: unknown): boolean {
  const item = asRecord(value);
  const segmentation = asRecord(item.segmentation);
  return (
    item.fallback_used === true
    || item.score_fallback_used === true
    || segmentation.fallback_used === true
    || item.action_source === "fallback"
    || item.score_action_source === "fallback"
    || item.confidence === "low"
    || item.score_confidence === "low"
  );
}

export function scoreStatusLabelForReport(report: RehabReport): string {
  if (isFallbackReport(report)) return "已完成降级评分";
  return report.score.valid ? "有效" : "待确认";
}

export function reportCompletedReps(report: RehabReport): number {
  const value = repetitionField(report, "repetition_count");
  if (isFallbackReport(report)) return value ?? 0;
  return value ?? report.completion.completed_reps;
}

export function reportValidReps(report: RehabReport): number {
  const value = repetitionField(report, "valid_repetition_count");
  if (isFallbackReport(report)) return value ?? 0;
  return value ?? report.completion.valid_reps;
}

export function reportBrs(report: RehabReport, defaultValue = "BRS IV"): string {
  const value = report.score.brs || report.patient.brs;
  if (value) return value;
  return isFallbackReport(report) ? "暂无数据" : defaultValue;
}

function repetitionField(report: RehabReport, field: "repetition_count" | "valid_repetition_count"): number | null {
  const score = asRecord(report.score);
  const segmentation = asRecord(score.segmentation);
  return firstFiniteNumber(score[field], segmentation[field]);
}

function firstFiniteNumber(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function asRecord(value: unknown): UnknownRecord {
  return value && typeof value === "object" ? value as UnknownRecord : {};
}
