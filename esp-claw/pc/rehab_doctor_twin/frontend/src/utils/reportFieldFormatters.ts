const NO_DATA = "暂无数据";

export function formatCompensation(value: unknown): string {
  if (typeof value === "string") {
    if (value === "detected") return "检测到代偿";
    if (value === "not_detected") return "未检测到明显代偿";
    return NO_DATA;
  }

  if (!value || typeof value !== "object" || Array.isArray(value)) return NO_DATA;
  const record = value as Record<string, unknown>;
  const types = Array.isArray(record.types) ? record.types.filter((item): item is string => typeof item === "string") : [];
  const labels = types.map(compensationTypeLabel);

  if (record.detected === true) return labels.length ? labels.join(" / ") : "检测到代偿";
  if (record.detected === false) return "未检测到明显代偿";
  if (record.status === "detected") return labels.length ? labels.join(" / ") : "检测到代偿";
  if (record.status === "not_detected") return "未检测到明显代偿";

  return NO_DATA;
}

export function compensationTypeLabel(type: string): string {
  if (type === "LF") return "躯干前倾";
  if (type === "TR") return "躯干旋转";
  if (type === "TREMOR") return "手部抖动";
  return type;
}

export function formatFatigue(value: unknown): string {
  const raw = typeof value === "string"
    ? value
    : value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>).level ?? (value as Record<string, unknown>).status
      : undefined;

  if (raw === "low") return "低";
  if (raw === "medium") return "中";
  if (raw === "high") return "高";
  if (raw === "none" || raw === "not_detected") return "未检测到疲劳";

  return NO_DATA;
}

export function pickActionField(actionValue: unknown, reportValue: unknown): unknown {
  return actionValue === undefined || actionValue === null ? reportValue : actionValue;
}
