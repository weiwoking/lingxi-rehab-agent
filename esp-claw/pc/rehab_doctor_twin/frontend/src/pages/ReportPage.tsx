import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ReportPreview } from "../components/ReportPreview";
import type { ClinicianPrescription, PatientTrainingTrend, SafetyIntervention, SessionTaskDecision, TherapistReview } from "../contracts/clinicalWorkspace";
import type { RehabReport } from "../contracts/report";
import { clinicalWorkspaceDataSource } from "../services/clinicalWorkspaceDataSource";
import { fetchCurrentReport, fetchSessionReport } from "../services/reportApi";

export function ReportPage({ onBack }: { onBack: () => void }) {
  const { sessionId } = useParams();
  const [report, setReport] = useState<RehabReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [clinicalContext, setClinicalContext] = useState<{
    prescription: ClinicianPrescription | null;
    decision: SessionTaskDecision | null;
    safety: SafetyIntervention[];
    trend: PatientTrainingTrend | null;
    review: TherapistReview | null;
  }>({ prescription: null, decision: null, safety: [], trend: null, review: null });

  useEffect(() => {
    setError(null);
    const loader = sessionId && sessionId !== "current" ? fetchSessionReport("P-0217", sessionId) : fetchCurrentReport();
    loader.then(async (loadedReport) => {
      setReport(loadedReport);
      const currentSessionId = loadedReport.patient.session_id;
      const [prescription, decision, safety, trend, review] = await Promise.all([
        clinicalWorkspaceDataSource.getPrescription("P-0217"),
        clinicalWorkspaceDataSource.getTaskDecision("P-0217", currentSessionId),
        clinicalWorkspaceDataSource.getSafetyInterventions("P-0217", currentSessionId),
        clinicalWorkspaceDataSource.getTrainingTrend("P-0217", 7),
        clinicalWorkspaceDataSource.getTherapistReview("P-0217", currentSessionId)
      ]);
      setClinicalContext({ prescription, decision, safety, trend, review });
    }).catch((err: Error) => {
      setError(err.message);
      setClinicalContext({ prescription: null, decision: null, safety: [], trend: null, review: null });
    });
  }, [sessionId]);

  return (
    <main className="report-shell">
      <nav className="report-nav"><button onClick={onBack}>返回</button><button onClick={() => window.print()} disabled={!report}>浏览器打印</button></nav>
      {error && <div className="report-error">{error}</div>}
      {report ? <ReportPreview report={report} note={note} onNoteChange={setNote} clinicalContext={clinicalContext} /> : !error && <div className="report-loading">正在读取训练报告...</div>}
    </main>
  );
}
