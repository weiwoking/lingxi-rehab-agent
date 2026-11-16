import { ArrowLeft } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { TaskDecisionCard } from "../components/TaskDecisionCard";
import { TherapistReviewCard } from "../components/TherapistReviewCard";
import type { ClinicianPrescription, SessionTaskDecision, TherapistReview } from "../contracts/clinicalWorkspace";
import { clinicalWorkspaceDataSource } from "../services/clinicalWorkspaceDataSource";
import { useRehabStore } from "../store/rehabStore";

export function DecisionReviewPage() {
  const { patientId = "P-0217" } = useParams();
  const [searchParams] = useSearchParams();
  const snapshot = useRehabStore((s) => s.snapshot);
  const wsStatus = useRehabStore((s) => s.wsStatus);
  const sessionId = searchParams.get("sessionId") ?? snapshot?.session_id ?? "SESSION_IDLE";
  const [prescription, setPrescription] = useState<ClinicianPrescription | null>(null);
  const [decision, setDecision] = useState<SessionTaskDecision | null>(null);
  const [review, setReview] = useState<TherapistReview | null>(null);

  useEffect(() => {
    let mounted = true;
    Promise.all([
      clinicalWorkspaceDataSource.getPrescription(patientId),
      clinicalWorkspaceDataSource.getTaskDecision(patientId, sessionId),
      clinicalWorkspaceDataSource.getTherapistReview(patientId, sessionId)
    ]).then(([rx, taskDecision, therapistReview]) => {
      if (!mounted) return;
      setPrescription(rx);
      setDecision(taskDecision);
      setReview(therapistReview);
    }).catch(() => {
      if (!mounted) return;
      setPrescription(null);
      setDecision(null);
      setReview(null);
    });
    return () => { mounted = false; };
  }, [patientId, sessionId]);

  const saveReview = async (next: TherapistReview) => {
    const saved = await clinicalWorkspaceDataSource.saveTherapistReview(next);
    setReview(saved);
  };

  return (
    <main className="workspace-page decision-review-page">
      <header className="decision-review-header">
        <Link to={`/patients/${patientId}/live`}><ArrowLeft size={15} />返回实时监护</Link>
        <div><span>患者</span><strong>{patientId}</strong></div>
        <div><span>患侧</span><strong>右侧上肢</strong></div>
        <div><span>当前会话</span><strong>{sessionId}</strong></div>
        <div><span>处方版本</span><strong>{prescription?.version ?? "--"}</strong></div>
        <div><span>训练状态</span><strong>{statusLabel(snapshot?.status)}</strong></div>
        <div><span>WebSocket</span><strong>{wsStatus}</strong></div>
      </header>
      <TaskDecisionCard decision={decision} />
      <section className="decision-review-main">
        <TherapistReviewCard review={review} onSave={saveReview} />
      </section>
    </main>
  );
}

function statusLabel(status: string | undefined) {
  if (status === "RUNNING") return "训练进行中";
  if (status === "UPLOADING") return "数据上传中";
  if (status === "SCORING") return "智能分析中";
  if (status === "REPORT_READY") return "评估已完成";
  return "准备就绪";
}
