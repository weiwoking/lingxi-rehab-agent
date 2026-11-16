import type { ImuRuntimeSample } from "../contracts/realtimeMessages";
import type { ImuSeriesPoint } from "../contracts/state";
import { SensorTrendChart } from "./SensorTrendChart";

export function ImuSensorCard({
  label,
  sample,
  points
}: {
  label: string;
  sample?: ImuRuntimeSample;
  points: ImuSeriesPoint[];
}) {
  const visible = lastTenSeconds(points);
  const interrupted = sample ? !sample.online : false;
  return (
    <article className={`sensor-card imu-card ${interrupted ? "sensor-offline" : ""}`}>
      <header>
        <div><span>惯性传感器</span><h2>{label}</h2></div>
        <b>{sample?.online ? "在线" : sample ? "连接中断" : "等待数据"}</b>
      </header>
      <SensorTrendChart
        timestamps={visible.map((point) => point.t)}
        unit="°"
        series={[
          { key: "roll", label: "Roll", color: "#54d7ca", values: visible.map((point) => point.roll_deg) },
          { key: "pitch", label: "Pitch", color: "#62aef0", values: visible.map((point) => point.pitch_deg) },
          { key: "yaw", label: "Yaw", color: "#f0bd58", values: visible.map((point) => point.yaw_deg) }
        ]}
      />
      {!points.length && <p className="sensor-waiting">等待 IMU 数据</p>}
      <dl className="sensor-values">
        <Value label="Roll" value={formatNumber(sample?.roll_deg, "°")} />
        <Value label="Pitch" value={formatNumber(sample?.pitch_deg, "°")} />
        <Value label="Yaw" value={formatNumber(sample?.yaw_deg, "°")} />
      </dl>
    </article>
  );
}

function lastTenSeconds(points: ImuSeriesPoint[]) {
  const end = points.at(-1)?.t;
  return end ? points.filter((point) => point.t >= end - 10_000) : [];
}

function Value({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

function formatNumber(value: number | null | undefined, unit: string) {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(2)}${unit}` : "--";
}
