import { useMemo, useState } from "react";
import type { ChartPoint } from "../contracts/state";

type ChartMode = "sEMG" | "肩关节角度" | "肘关节角度" | "腕关节角度" | "单次评分趋势";

export function RealtimeChart({ points }: { points: ChartPoint[] }) {
  const [mode, setMode] = useState<ChartMode>("sEMG");
  const path = useMemo(() => buildPaths(points, mode), [points, mode]);
  const current = points.at(-1);
  return (
    <section className="chart-panel">
      <div className="chart-head">
        <h2>底部实时图表</h2>
        <div className="segmented">
          {(["sEMG", "肩关节角度", "肘关节角度", "腕关节角度", "单次评分趋势"] as ChartMode[]).map((item) => (
            <button key={item} className={mode === item ? "selected" : ""} onClick={() => setMode(item)}>{item}</button>
          ))}
        </div>
        <span>{mode === "sEMG" ? "归一化 0-1" : mode.includes("评分") ? "分" : "deg"} · 最近 {Math.min(points.length, 300)} 点</span>
      </div>
      <svg viewBox="0 0 900 130" role="img" aria-label="实时曲线">
        <g className="grid-lines">{[25, 50, 75, 100].map((y) => <line key={y} x1="36" x2="880" y1={y} y2={y} />)}</g>
        {path.map((item) => <path key={item.key} d={item.d} className={item.key} />)}
        <text x="42" y="22">{current?.phase ?? "准备"}</text>
        <text x="740" y="22">当前 {formatCurrent(current, mode)}</text>
      </svg>
    </section>
  );
}

function buildPaths(points: ChartPoint[], mode: ChartMode) {
  const series = mode === "sEMG"
    ? [["CH1", 1], ["CH2", 1], ["CH3", 1], ["CH4", 1]] as const
    : mode === "肩关节角度"
      ? [["shoulder", 120]] as const
      : mode === "肘关节角度"
        ? [["elbow", 150]] as const
        : mode === "腕关节角度"
          ? [["wrist", 80]] as const
          : [["score", 100]] as const;
  return series.map(([key, max]) => ({
    key,
    d: points.map((point, index) => {
      const x = 36 + (index / Math.max(points.length - 1, 1)) * 844;
      const value = Number(point[key as keyof ChartPoint]) || 0;
      const y = 118 - Math.max(0, Math.min(1, value / max)) * 96;
      return `${index === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    }).join(" ")
  }));
}

function formatCurrent(point: ChartPoint | undefined, mode: ChartMode) {
  if (!point) return "--";
  if (mode === "sEMG") return `${Math.round(point.CH1 * 100)}% / ${Math.round(point.CH2 * 100)}%`;
  if (mode === "肩关节角度") return `${point.shoulder.toFixed(1)}°`;
  if (mode === "肘关节角度") return `${point.elbow.toFixed(1)}°`;
  if (mode === "腕关节角度") return `${point.wrist.toFixed(1)}°`;
  return `${point.score}分`;
}

