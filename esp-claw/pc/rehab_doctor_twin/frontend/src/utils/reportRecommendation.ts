import type { RehabReport } from "../contracts/report";
import {
  CURRENT_SUBSCORE_FIELDS,
  CURRENT_SUBSCORE_MAX,
  finiteNumber,
  type CurrentSubscoreKey
} from "./currentReportSchema";

export const RECOMMENDATION_WEAK_THRESHOLD = 0.9;
export const RECOMMENDATION_MAX_WEAK_DIMENSIONS = 2;

export interface RehabRecommendationResult {
  items: string[];
  normalizedSubscores: Partial<Record<CurrentSubscoreKey, number>>;
  selectedWeakDimensions: CurrentSubscoreKey[];
}

const WEAK_DIMENSION_RECOMMENDATIONS: Record<CurrentSubscoreKey, string> = {
  speed_score: "当前动作节奏控制仍有改善空间，后续训练可重点保持适宜、稳定的完成节奏，避免明显过快或过慢。",
  ROM_score: "当前有效活动范围相对不足，后续训练可在任务要求范围内提高动作完成幅度，并保持动作路径完整。",
  smoothness_score: "动作平滑性仍有改善空间，训练时可减少不必要的停顿、突变和反复修正，保持运动过程连续、稳定。",
  coordination_score: "当前上肢各节段协同仍有提升空间，可重点关注上臂、前臂与手腕之间的协调配合，减少分段或不同步动作。",
  muscle_score: "当前肌肉激活模式仍有优化空间，后续训练可重点保持目标动作中的有效肌肉募集与协同，减少无效或不协调的激活模式。"
};

export function buildRehabRecommendations(report: RehabReport): RehabRecommendationResult {
  const normalizedSubscores = normalizeSubscores(report);
  const selectedWeakDimensions = selectWeakDimensions(normalizedSubscores);
  const compensationTypes = compensationTypesFrom(report.score.compensation);
  const finalScore = finiteNumber(report.score.final_score);
  const items = uniqueItems([
    overallRecommendation(finalScore),
    ...compensationRecommendations(compensationTypes),
    ...selectedWeakDimensions.map((key) => WEAK_DIMENSION_RECOMMENDATIONS[key]),
    retentionRecommendation(compensationTypes, selectedWeakDimensions)
  ]).slice(0, 4);

  return {
    items: items.length ? items : ["本次训练数据不足，暂不生成动作质量建议。"],
    normalizedSubscores,
    selectedWeakDimensions
  };
}

function normalizeSubscores(report: RehabReport): Partial<Record<CurrentSubscoreKey, number>> {
  const result: Partial<Record<CurrentSubscoreKey, number>> = {};
  for (const key of CURRENT_SUBSCORE_FIELDS) {
    const value = finiteNumber(report.score.subscores?.[key]);
    const maxScore = CURRENT_SUBSCORE_MAX[key];
    if (value === null || value < 0 || value > maxScore) continue;
    result[key] = value / maxScore;
  }
  return result;
}

function selectWeakDimensions(normalizedSubscores: Partial<Record<CurrentSubscoreKey, number>>) {
  return CURRENT_SUBSCORE_FIELDS
    .map((key) => ({ key, ratio: finiteNumber(normalizedSubscores[key]) }))
    .filter((item): item is { key: CurrentSubscoreKey; ratio: number } => (
      item.ratio !== null && item.ratio < RECOMMENDATION_WEAK_THRESHOLD
    ))
    .sort((a, b) => a.ratio - b.ratio)
    .slice(0, RECOMMENDATION_MAX_WEAK_DIMENSIONS)
    .map((item) => item.key);
}

function compensationTypesFrom(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  const types = (value as Record<string, unknown>).types;
  if (!Array.isArray(types)) return [];
  return types.filter((item): item is string => typeof item === "string" && item.length > 0);
}

function overallRecommendation(finalScore: number | null) {
  if (finalScore === null) return "";
  if (finalScore >= 85) return "本次训练整体动作完成质量较好，主要动作指标表现较稳定。";
  if (finalScore >= 70) return "本次训练整体完成有效，部分动作质量仍有进一步提升空间。";
  return "本次训练已完成，当前动作质量仍有较明显的改善空间。";
}

function compensationRecommendations(types: string[]) {
  const result: string[] = [];
  if (types.includes("TREMOR")) {
    result.push("训练过程中检测到手部抖动代偿，后续训练可重点加强末端动作稳定控制，减少取放或接近目标过程中手腕及手部的不稳定摆动。");
  }
  if (types.includes("LF")) {
    result.push("训练过程中检测到躯干前倾代偿，后续训练可重点保持躯干稳定，减少通过身体前倾辅助完成上肢任务的情况。");
  }
  return result;
}

function retentionRecommendation(types: string[], selectedWeakDimensions: CurrentSubscoreKey[]) {
  if (types.length || selectedWeakDimensions.length) return "";
  return "本次训练未检测到明显代偿，后续可继续保持当前动作模式，在保证动作完整性的基础上维持稳定执行。";
}

function uniqueItems(items: string[]) {
  const seen = new Set<string>();
  return items.filter((item) => {
    const text = item.trim();
    if (!text || seen.has(text)) return false;
    seen.add(text);
    return true;
  });
}
