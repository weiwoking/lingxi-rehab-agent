import { ClipboardList, Database, FilePenLine, MonitorDot, RadioTower } from "lucide-react";
import { Link } from "react-router-dom";
import type { ClinicianPrescription } from "../contracts/clinicalWorkspace";
import type { DemoPatient } from "../services/patientApi";

export function PatientCard({
  patient,
  prescription,
  onOpenPrescription
}: {
  patient: DemoPatient;
  prescription?: ClinicianPrescription | null;
  onOpenPrescription?: () => void;
}) {
  const base = `/patients/${encodeURIComponent(patient.patient_id)}`;
  return (
    <article className="patient-card">
      <header>
        <div>
          <h2>{patient.patient_id}</h2>
          <span>{patient.display_name} · {patient.profile_label}</span>
        </div>
        <b>{patient.current_status}</b>
      </header>
      <div className="patient-clinical-grid">
        <p><span>阶段</span><strong>{patient.stage}</strong></p>
        <p><span>患侧</span><strong>右侧上肢</strong></p>
        <p><span>BRS</span><strong>{patient.brs}</strong></p>
        <p><span>年龄段</span><strong>{patient.age_range}</strong></p>
        <p><span>当前任务</span><strong>右手拿杯喝水</strong></p>
        <p><span>最近评分</span><strong>{patient.latest_score}</strong></p>
        <p><span>今日训练</span><strong>{patient.today_training_count}</strong></p>
        <p><span>最近训练</span><strong>{patient.latest_training_time}</strong></p>
        <p><span>疲劳风险</span><strong>{patient.fatigue_risk}</strong></p>
        <p><span>设备在线</span><strong>{patient.device_status}</strong></p>
      </div>
      {prescription && (
        <button type="button" className="prescription-summary" onClick={onOpenPrescription}>
          <FilePenLine size={18} />
          <span>
            <b>医生处方摘要</b>
            当前训练目标：{prescription.training_goal} · 允许难度：1-2级 · 最大取物高度：{prescription.max_reach_height_cm} cm · 重点监测：躯干前倾
          </span>
          <small>{prescription.version} · {prescription.updated_at}</small>
        </button>
      )}
      <footer>
        <Link className="card-action primary" to={`${base}/live`}><MonitorDot size={16} />实时工作台</Link>
        <Link className="card-action" to={`${base}/devices`}><RadioTower size={16} />设备状态</Link>
        <Link className="card-action" to={`${base}/logs`}><Database size={16} />数据日志</Link>
        <Link className="card-action" to={`${base}/reports`}><ClipboardList size={16} />历史报告</Link>
      </footer>
    </article>
  );
}
