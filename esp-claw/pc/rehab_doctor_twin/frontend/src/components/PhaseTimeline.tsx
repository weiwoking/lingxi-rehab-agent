import type { RehabSnapshot } from "../contracts/realtimeMessages";

const phases = ["准备", "伸手", "抓握", "抬杯", "接近口部", "回收", "完成"];

export function PhaseTimeline({ snapshot }: { snapshot: RehabSnapshot | null }) {
  const telemetry = snapshot?.telemetry;
  return (
    <section className="panel">
      <h2>动作阶段</h2>
      <div className="phase-lane">
        {phases.map((phase) => <span key={phase} className={phase === snapshot?.phase ? "active" : ""}>{phase}</span>)}
      </div>
      <div className="kv-grid compact">
        <label>当前阶段</label><strong>{snapshot?.phase ?? "准备"}</strong>
        <label>下一阶段参考</label><strong>{snapshot?.next_phase ?? "伸手"}</strong>
        <label>肩角</label><strong>{formatNumber(telemetry?.shoulder_flexion_deg)}°</strong>
        <label>肘角</label><strong>{formatNumber(telemetry?.elbow_flexion_deg)}°</strong>
        <label>腕角</label><strong>{formatNumber(telemetry?.wrist_flexion_deg)}°</strong>
        <label>轨迹匹配度</label><strong>{formatNumber(telemetry?.trajectory_match)}%</strong>
      </div>
      <div className="progress"><i style={{ width: `${telemetry?.phase_progress ?? 0}%` }} /></div>
    </section>
  );
}

function formatNumber(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(1) : "0.0";
}
