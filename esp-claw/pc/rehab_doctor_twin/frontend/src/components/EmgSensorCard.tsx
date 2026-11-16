import type { EmgRuntimeSample } from "../contracts/realtimeMessages";
import type { EmgSeriesPoint } from "../contracts/state";
import { SensorTrendChart } from "./SensorTrendChart";

export function EmgSensorCard({
  label,
  sample,
  points
}: {
  label: string;
  sample?: EmgRuntimeSample;
  points: EmgSeriesPoint[];
}) {
  const visible = lastTenSeconds(points);
  const interrupted = sample ? !sample.online : false;
  return (
    <article className={`sensor-card emg-card ${interrupted ? "sensor-offline" : ""}`}>
      <header>
        <div><span>表面肌电</span><h2>{label}</h2></div>
        <b>{sample?.online ? "在线" : sample ? "连接中断" : "等待数据"}</b>
      </header>
      <SensorTrendChart
        timestamps={visible.map((point) => point.t)}
        unit="RMS"
        series={[
          { key: "rms", label: "RMS", color: "#eb776f", values: visible.map((point) => point.rms) }
        ]}
      />
      {!points.length && <p className="sensor-waiting">等待 sEMG 数据</p>}
      <dl className="sensor-values">
        <Value label="当前 RMS" value={formatNumber(sample?.rms)} />
      </dl>
    </article>
  );
}

function lastTenSeconds(points: EmgSeriesPoint[]) {
  const end = points.at(-1)?.t;
  return end ? points.filter((point) => point.t >= end - 10_000) : [];
}

function Value({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

function formatNumber(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(3) : "--";
}
