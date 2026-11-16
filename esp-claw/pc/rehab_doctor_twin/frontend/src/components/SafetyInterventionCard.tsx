import type { SafetyIntervention } from "../contracts/clinicalWorkspace";

const workflowLabel: Record<string, string> = {
  safe: "安全",
  compensation_detected: "检测到代偿",
  paused_correction: "暂停纠正",
  waiting_recovery: "等待恢复",
  retrying: "重新尝试",
  recovered: "已恢复",
  completed: "完成"
};

export function SafetyInterventionCard({ interventions }: { interventions: SafetyIntervention[] }) {
  const event = interventions.find((item) => item.event_type !== "none");
  return (
    <section className="panel clinical-card safety-card">
      <header className="clinical-card-head">
        <div><span>训练过程监测</span><h2>训练安全与纠正</h2></div>
        <strong>{event ? "轻度提醒" : "当前训练安全"}</strong>
      </header>
      {!event ? (
        <p className="safe-state">当前训练安全 / 未检测到需要干预的代偿</p>
      ) : (
        <>
          <div className="safety-summary">
            <p><span>事件</span><strong>{event.event_label}</strong></p>
            <p><span>阶段</span><strong>{event.phase}</strong></p>
            <p><span>测量值</span><strong>{event.measured_deg}°</strong></p>
            <p><span>提醒阈值</span><strong>{event.threshold_deg}°</strong></p>
            <p><span>严重程度</span><strong>{event.severity}</strong></p>
            <p><span>处理</span><strong>暂停 / 提醒</strong></p>
          </div>
          <div className="workflow-strip">{event.workflow.map((state) => <span key={state}>{workflowLabel[state] ?? state}</span>)}</div>
          <p className="clinical-muted">{event.outcome}。请结合患者现场状态进行临床判断。</p>
        </>
      )}
    </section>
  );
}
