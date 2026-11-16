import type { RehabSnapshot } from "../contracts/realtimeMessages";

export function MotionQualityPanel({ snapshot }: { snapshot: RehabSnapshot | null }) {
  const quality = snapshot?.quality;
  const sub = quality?.subscores;
  return (
    <section className="panel">
      <h2>动作质量</h2>
      <div className="score-hero">{quality?.final_score ?? "--"}<span>综合分</span></div>
      <div className="quality-grid">
        <span>ROM <b>{sub?.ROM_score ?? "--"}</b></span>
        <span>平滑 <b>{sub?.smoothness_score ?? "--"}</b></span>
        <span>协调 <b>{sub?.coordination_score ?? "--"}</b></span>
        <span>肌肉 <b>{sub?.muscle_score ?? "--"}</b></span>
      </div>
      <div className="kv-grid compact">
        <label>疲劳等级</label><strong>{quality?.fatigue_level || "暂无最新数据"}</strong>
        <label>代偿风险</label><strong>{quality?.compensation_risk || "暂无最新数据"}</strong>
      </div>
      <p className={quality?.compensation_risk === "中" ? "suggestion warning" : "suggestion"}>{quality?.suggestion || "等待真实评分结果。"}</p>
    </section>
  );
}
