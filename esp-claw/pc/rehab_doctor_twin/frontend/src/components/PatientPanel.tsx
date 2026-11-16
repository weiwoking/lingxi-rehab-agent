import type { RehabSnapshot } from "../contracts/realtimeMessages";

const t = {
  patientId: "\u60a3\u8005\u7f16\u53f7",
  stage: "\u9636\u6bb5",
  side: "\u60a3\u4fa7",
  ageBand: "\u5e74\u9f84\u6bb5",
  todaySessions: "\u4eca\u65e5\u6b21\u6570",
  averageScore: "\u5e73\u5747\u5f97\u5206",
  riskLevel: "\u98ce\u9669\u7b49\u7ea7",
  diagnosis: "\u8111\u5352\u4e2d\u6062\u590d\u671f",
  rightSide: "\u53f3\u4fa7\u4e0a\u80a2",
  brs: "IV\u671f",
  age: "55-65\u5c81",
  low: "\u4f4e"
};

export function PatientPanel({ snapshot }: { snapshot: RehabSnapshot | null }) {
  const patient = snapshot?.patient;
  return <section className="panel patient-panel"><h2>P-0217</h2><div className="kv-grid"><label>{t.patientId}</label><strong>{patient?.patient_id ?? "P-0217"}</strong><label>{t.stage}</label><strong>{patient?.diagnosis_stage ?? t.diagnosis}</strong><label>{t.side}</label><strong>{patient?.affected_side ?? t.rightSide}</strong><label>BRS</label><strong>{patient?.brs ?? t.brs}</strong><label>{t.ageBand}</label><strong>{patient?.age_band ?? t.age}</strong><label>{t.todaySessions}</label><strong>{patient?.today_sessions ?? "--"}</strong><label>{t.averageScore}</label><strong>{patient?.today_average_score ?? "--"}</strong><label>{t.riskLevel}</label><strong>{patient?.risk_level ?? "待评估"}</strong></div></section>;
}
