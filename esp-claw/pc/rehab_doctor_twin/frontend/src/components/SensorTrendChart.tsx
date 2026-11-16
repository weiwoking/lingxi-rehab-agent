interface Series {
  key: string;
  label: string;
  color: string;
  values: Array<number | null>;
}

export function SensorTrendChart({
  timestamps,
  series,
  unit
}: {
  timestamps: number[];
  series: Series[];
  unit: string;
}) {
  const numeric = series.flatMap((item) => item.values.filter((value): value is number => typeof value === "number" && Number.isFinite(value)));
  if (!timestamps.length || !numeric.length) {
    return <div className="sensor-chart-empty">等待真实数据</div>;
  }
  const rawMin = Math.min(...numeric);
  const rawMax = Math.max(...numeric);
  const span = Math.max(rawMax - rawMin, Math.max(Math.abs(rawMax), 1) * 0.08);
  const min = rawMin - span * 0.12;
  const max = rawMax + span * 0.12;
  const first = timestamps[0];
  const last = timestamps.at(-1) ?? first;
  const timeSpan = Math.max(last - first, 1);

  return (
    <div className="sensor-chart">
      <div className="sensor-chart-legend">
        {series.map((item) => <span key={item.key}><i style={{ background: item.color }} />{item.label}</span>)}
        <b>{unit}</b>
      </div>
      <svg viewBox="0 0 360 150" role="img" aria-label={`实时趋势 ${unit}`}>
        <g className="sensor-grid">
          {[28, 70, 112].map((y) => <line key={y} x1="34" x2="350" y1={y} y2={y} />)}
        </g>
        <text x="4" y="31">{formatAxis(max)}</text>
        <text x="4" y="116">{formatAxis(min)}</text>
        {series.map((item) => (
          <path
            key={item.key}
            d={buildPath(item.values, timestamps, first, timeSpan, min, max)}
            style={{ stroke: item.color }}
          />
        ))}
      </svg>
    </div>
  );
}

function buildPath(
  values: Array<number | null>,
  timestamps: number[],
  first: number,
  timeSpan: number,
  min: number,
  max: number
) {
  let drawing = false;
  return values.map((value, index) => {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      drawing = false;
      return "";
    }
    const x = 34 + ((timestamps[index] - first) / timeSpan) * 316;
    const y = 124 - ((value - min) / Math.max(max - min, 1)) * 104;
    const command = drawing ? "L" : "M";
    drawing = true;
    return `${command}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

function formatAxis(value: number) {
  return Math.abs(value) >= 100 ? value.toFixed(0) : value.toFixed(1);
}
