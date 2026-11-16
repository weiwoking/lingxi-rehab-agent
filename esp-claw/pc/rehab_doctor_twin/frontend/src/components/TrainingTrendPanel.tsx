import { useMemo, useState } from "react";
import type { PatientTrainingTrend, TrainingTrendPoint } from "../contracts/clinicalWorkspace";

type TrendMetric = "overall_score" | "ROM_score" | "smoothness_score" | "compensation_count" | "duration_sec";

const tabs: Array<[TrendMetric, string]> = [
  ["overall_score", "综合评分"],
  ["ROM_score", "ROM"],
  ["smoothness_score", "平滑度"],
  ["compensation_count", "代偿次数"],
  ["duration_sec", "完成时长"]
];

export function TrainingTrendPanel({ trend }: { trend: PatientTrainingTrend | null }) {
  const [metric, setMetric] = useState<TrendMetric>("overall_score");
  const points = trend?.points ?? [];
  const path = useMemo(() => buildPath(points, metric), [points, metric]);
  return (
    <section className="panel clinical-card trend-card">
      <header className="clinical-card-head">
        <div><span>最近 7 次有效训练</span><h2>训练趋势</h2></div>
      </header>
      <div className="trend-tabs">{tabs.map(([key, label]) => <button className={metric === key ? "selected" : ""} onClick={() => setMetric(key)} key={key}>{label}</button>)}</div>
      <svg viewBox="0 0 360 130" role="img" aria-label="训练趋势图">
        <g className="grid-lines"><line x1="20" y1="20" x2="340" y2="20" /><line x1="20" y1="65" x2="340" y2="65" /><line x1="20" y1="110" x2="340" y2="110" /></g>
        <path d={path} />
        {points.map((point, index) => {
          const { x, y } = pointPosition(points, metric, point, index);
          return <circle cx={x} cy={y} r="3.5" key={point.session_id} />;
        })}
        {points.map((point, index) => <text x={20 + index * (320 / Math.max(points.length - 1, 1))} y="126" key={point.session_id}>{point.date_label}</text>)}
      </svg>
    </section>
  );
}

function buildPath(points: TrainingTrendPoint[], metric: TrendMetric) {
  return points.map((point, index) => {
    const { x, y } = pointPosition(points, metric, point, index);
    return `${index ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ");
}

function pointPosition(points: TrainingTrendPoint[], metric: TrendMetric, point: TrainingTrendPoint, index: number) {
  const values = points.map((item) => item[metric]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(max - min, 1);
  const x = 20 + index * (320 / Math.max(points.length - 1, 1));
  const y = 110 - ((point[metric] - min) / range) * 90;
  return { x, y };
}
