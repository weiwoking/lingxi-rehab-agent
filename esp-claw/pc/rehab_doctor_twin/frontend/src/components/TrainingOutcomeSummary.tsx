import type { RehabSnapshot } from "../contracts/realtimeMessages";

export function TrainingOutcomeSummary({ snapshot }: { snapshot: RehabSnapshot | null }) {
  const score = snapshot?.quality.final_score;
  const subscores = snapshot?.quality.subscores ?? {};

  return (
    <section className="panel clinical-card outcome-card">
      <header className="clinical-card-head">
        <div><span>本次训练</span><h2>训练结果摘要</h2></div>
        <strong>{snapshot?.report_ready ? "已完成" : snapshot?.phase ?? "等待设备数据"}</strong>
      </header>
      <div className="outcome-grid">
        <p><span>任务</span><strong>{snapshot?.task.name || "--"}</strong></p>
        <p><span>session_id</span><strong>{snapshot?.session_id ?? "--"}</strong></p>
        <p><span>综合评分</span><strong>{formatScore(score)}</strong></p>
        <p><span>ROM</span><strong>{formatScore(subscores.ROM_score)}</strong></p>
        <p><span>平滑度</span><strong>{formatScore(subscores.smoothness_score)}</strong></p>
        <p><span>协调性</span><strong>{formatScore(subscores.coordination_score)}</strong></p>
        <p><span>肌肉评分</span><strong>{formatScore(subscores.muscle_score)}</strong></p>
        <p><span>疲劳</span><strong>{snapshot?.quality.fatigue_level ?? "--"}</strong></p>
      </div>
    </section>
  );
}

function formatScore(value: number | null | undefined) {
  return Number.isFinite(value) ? Number(value).toFixed(2) : "--";
}
