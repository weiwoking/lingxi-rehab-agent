import type { SessionTaskDecision } from "../contracts/clinicalWorkspace";

export function TaskDecisionCard({ decision }: { decision: SessionTaskDecision | null }) {
  if (!decision) return <section className="panel clinical-card"><h2>本轮任务决策</h2><p className="clinical-muted">训练决策数据读取中...</p></section>;
  return (
    <section className="panel clinical-card task-decision-card">
      <header className="clinical-card-head">
        <div><span>训练任务评估</span><h2>本轮任务决策</h2></div>
        <strong>{decision.task_name}</strong>
      </header>
      <div className="decision-table" role="table" aria-label="任务候选决策">
        <div className="decision-row decision-head" role="row">
          <span>候选任务</span><span>环境条件</span><span>医生边界</span><span>难度</span><span>决策</span><span>原因</span>
        </div>
        {decision.candidates.map((candidate) => (
          <div className={`decision-row ${candidate.decision}`} role="row" key={candidate.candidate_task_id}>
            <span><b>{candidate.candidate_task_name}</b><small>{candidate.candidate_task_id}</small></span>
            <span>{candidate.environment_condition}</span>
            <span>{candidate.clinician_boundary}</span>
            <span>{candidate.suggested_difficulty}级</span>
            <span>{candidate.decision === "selected" ? "已选择" : "已拒绝"}</span>
            <span>{candidate.reason}</span>
          </div>
        ))}
      </div>
      <footer className="decision-footer">
        <p><span>训练任务</span><strong>{decision.task_name}</strong></p>
        <p><span>难度</span><strong>{decision.selected_difficulty}级</strong></p>
        <p><span>训练目标</span><strong>{decision.training_objective}</strong></p>
        <p><span>重点监测</span><strong>{decision.monitoring_focus}</strong></p>
        <p><span>选择依据</span><strong>{decision.selection_basis}</strong></p>
      </footer>
    </section>
  );
}
