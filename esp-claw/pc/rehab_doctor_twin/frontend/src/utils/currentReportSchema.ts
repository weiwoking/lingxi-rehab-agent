export const CURRENT_SUBSCORE_FIELDS = [
  "speed_score",
  "ROM_score",
  "smoothness_score",
  "coordination_score",
  "muscle_score"
] as const;

export type CurrentSubscoreKey = (typeof CURRENT_SUBSCORE_FIELDS)[number];

export const CURRENT_SUBSCORE_LABELS: Record<CurrentSubscoreKey, string> = {
  speed_score: "速度评分",
  ROM_score: "活动范围评分",
  smoothness_score: "平滑性评分",
  coordination_score: "协调性评分",
  muscle_score: "肌肉激活评分"
};

export const CURRENT_SUBSCORE_SHORT_LABELS: Record<CurrentSubscoreKey, string> = {
  speed_score: "速度",
  ROM_score: "ROM",
  smoothness_score: "平滑",
  coordination_score: "协调",
  muscle_score: "肌肉激活"
};

export const CURRENT_SUBSCORE_MAX: Record<CurrentSubscoreKey, number> = {
  speed_score: 15,
  ROM_score: 20,
  smoothness_score: 20,
  coordination_score: 15,
  muscle_score: 30
};

export function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function formatScore(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "";
}

export function formatSubscore(value: number | null | undefined, key: CurrentSubscoreKey): string {
  const score = formatScore(value);
  return score ? `${score} / ${CURRENT_SUBSCORE_MAX[key]}` : "";
}

export function isNoDataText(value: string): boolean {
  return value.includes("暂无") || value.includes("鏆傛棤");
}

export function currentSubscoreRows(subscores: Partial<Record<CurrentSubscoreKey, number | null>> | undefined) {
  return CURRENT_SUBSCORE_FIELDS.map((key) => ({
    key,
    label: CURRENT_SUBSCORE_LABELS[key],
    shortLabel: CURRENT_SUBSCORE_SHORT_LABELS[key],
    value: finiteNumber(subscores?.[key]),
    max: CURRENT_SUBSCORE_MAX[key]
  })).filter((item) => item.value !== null);
}
