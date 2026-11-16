import { lazy, Suspense, useEffect, useMemo } from "react";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import { PatientWorkspaceLayout } from "./layouts/PatientWorkspaceLayout";
import { DoctorDashboardPage } from "./pages/DoctorDashboardPage";
import { PatientReportsPage } from "./pages/PatientReportsPage";
import { PrescriptionPage } from "./pages/PrescriptionPage";
import { PrintableReportPage } from "./pages/PrintableReportPage";
import { RawDataLogsPage } from "./pages/RawDataLogsPage";
import { ReconstructionPage } from "./pages/ReconstructionPage";
import { RoleSelectionPage } from "./pages/RoleSelectionPage";
import { SessionHistoryPage } from "./pages/SessionHistoryPage";
import { SessionRecordsPage } from "./pages/SessionRecordsPage";
import { SessionResultsPage } from "./pages/SessionResultsPage";
import { fetchCurrentState } from "./services/stateApi";
import { RehabWebSocketClient } from "./services/websocketClient";
import { applySnapshot } from "./store/rehabStore";

const LiveMonitorPage = lazy(() => import("./pages/LiveMonitorPage").then((module) => ({ default: module.LiveMonitorPage })));

export default function App() {
  return <BrowserRouter><AppRoutes /></BrowserRouter>;
}

function AppRoutes() {
  const client = useMemo(() => new RehabWebSocketClient(), []);

  useEffect(() => {
    client.start();
    return () => client.stop();
  }, [client]);

  useEffect(() => {
    fetchCurrentState()
      .then((data: { snapshot: Parameters<typeof applySnapshot>[0] }) => applySnapshot(data.snapshot))
      .catch(() => undefined);
  }, []);

  return (
    <div className="app">
      <Suspense fallback={<div className="route-loading">正在载入工作区...</div>}>
        <Routes>
          <Route path="/" element={<RoleSelectionPage />} />
          <Route path="/patient" element={<LiveMonitorPage />} />
          <Route path="/patient/reports" element={<PatientReportsPage />} />
          <Route path="/doctor/reports/:recordId/print" element={<PrintableReportPage />} />
          <Route path="/doctor" element={<PatientWorkspaceLayout />}>
            <Route index element={<Navigate to="overview" replace />} />
            <Route path="overview" element={<DoctorDashboardPage />} />
            <Route path="logs" element={<RawDataLogsPage />} />
            <Route path="reconstruction" element={<ReconstructionPage />} />
            <Route path="sessions" element={<SessionRecordsPage />} />
            <Route path="reports" element={<SessionHistoryPage />} />
            <Route path="sessions/:sessionId" element={<SessionResultsPage />} />
            <Route path="predictions" element={<Navigate to="reports" replace />} />
            <Route path="prescription" element={<PrescriptionPage />} />
            <Route path="live" element={<Navigate to="/patient" replace />} />
            <Route path="*" element={<Navigate to="overview" replace />} />
          </Route>
          <Route path="/patients/:patientId" element={<Navigate to="/doctor/overview" replace />} />
          <Route path="/patients/:patientId/:legacyPage" element={<LegacyWorkspaceRedirect />} />
          <Route path="/report/current" element={<Navigate to="/doctor/reports" replace />} />
          <Route path="/report/:sessionId" element={<LegacyReportRedirect />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </div>
  );
}

function LegacyWorkspaceRedirect() {
  const { legacyPage = "overview" } = useParams();
  if (legacyPage === "live" || legacyPage === "devices") return <Navigate to="/patient" replace />;
  const page = legacyPage === "decision-review" ? "overview" : legacyPage;
  return <Navigate to={`/doctor/${page}`} replace />;
}

function LegacyReportRedirect() {
  const { sessionId = "" } = useParams();
  return <Navigate to={`/doctor/reports?session_id=${encodeURIComponent(sessionId)}`} replace />;
}
