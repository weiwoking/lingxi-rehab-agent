import { FileText, Home, ListChecks } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import type { RehabReport } from "../contracts/report";
import { formatRecordDateTime } from "../data/showcaseRecords";
import type { SessionSummary } from "../services/patientApi";
import { fetchPatientReport, fetchPatientReports } from "../services/runtimeApi";
import {
  CURRENT_SUBSCORE_FIELDS,
  type CurrentSubscoreKey,
  currentSubscoreRows,
  finiteNumber,
  formatScore,
  formatSubscore,
  isNoDataText
} from "../utils/currentReportSchema";
import { formatCompensation } from "../utils/reportFieldFormatters";
import {
  FALLBACK_SCORE_NOTICE,
  isFallbackReport,
  reportCompletedReps,
  reportValidReps,
  scoreStatusLabelForReport
} from "../utils/scoreFallbackDisplay";
import { buildRehabRecommendations } from "../utils/reportRecommendation";

export function PatientReportsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [report, setReport] = useState<RehabReport | null>(null);
  const [message, setMessage] = useState("正在读取训练报告...");

  useEffect(() => {
    fetchPatientReports()
      .then((items) => {
        const sorted = [...items].sort((a, b) => reportTime(b) - reportTime(a));
        setSessions(sorted);
        const requested = searchParams.get("session_id") || "";
        const selected = sorted.find((item) => item.session_id === requested)?.session_id ?? sorted[0]?.session_id ?? "";
        setSelectedSessionId(selected);
        setMessage(sorted.length ? "" : "暂无训练报告");
      })
      .catch(() => {
        setSessions([]);
        setSelectedSessionId("");
        setMessage("暂无训练报告");
      });
  }, [searchParams]);

  useEffect(() => {
    if (!selectedSessionId) {
      setReport(null);
      return;
    }
    fetchPatientReport(selectedSessionId)
      .then(setReport)
      .catch(() => setReport(null));
  }, [selectedSessionId]);

  const selectedSession = useMemo(
    () => sessions.find((item) => item.session_id === selectedSessionId),
    [selectedSessionId, sessions]
  );

  const selectSession = (sessionId: string) => {
    setSelectedSessionId(sessionId);
    setSearchParams({ session_id: sessionId });
  };

  return (
    <main className="patient-training-page patient-report-center-page">
      <header className="patient-header">
        <div>
          <span>患者训练端</span>
          <h1>训练报告</h1>
        </div>
        <nav className="patient-nav">
          <Link to="/patient">康复智能体</Link>
          <Link to="/patient/reports">训练报告</Link>
          <Link to="/">
            <Home size={16} />
            返回角色入口
          </Link>
        </nav>
      </header>

      {!sessions.length ? (
        <section className="patient-report-empty">
          <EmptyState
            size="large"
            icon={<FileText size={30} />}
            title="暂无训练报告"
            description="完成有效训练后可在此查看报告。"
          />
        </section>
      ) : (
        <section className="patient-report-center">
          <aside className="patient-report-history patient-report-session-list">
            <header>
              <ListChecks size={18} />
              <h2>历史训练记录</h2>
            </header>
            {sessions.map((session) => (
              <button
                key={session.session_id}
                type="button"
                className={selectedSessionId === session.session_id ? "patient-report-session-card selected" : "patient-report-session-card"}
                onClick={() => selectSession(session.session_id)}
              >
                <strong>{labelForSession(session)}</strong>
                <span>{formatRecordDateTime(session.ended_at_ms ?? session.started_at_ms)}</span>
                <small>时长 {formatDurationMs(session.duration_ms)} · 综合评分 {formatScore(session.final_score)}</small>
              </button>
            ))}
          </aside>
          <article className="patient-report-detail patient-report-main-card">
            {!report ? (
              <EmptyState title="报告暂不可用" description={message || "请稍后再试。"} />
            ) : (
              <PatientReportDetail report={report} session={selectedSession} />
            )}
          </article>
        </section>
      )}
    </main>
  );
}

function PatientReportDetail({ report, session }: { report: RehabReport; session?: SessionSummary }) {
  const [selectedRepIndex, setSelectedRepIndex] = useState(1);
  const label = session ? labelForSession(session) : "训练记录";
  const fallbackUsed = isFallbackReport(report);
  const reps = useMemo(() => patientRepRows(report), [report]);
  const selectedRep = reps.find((rep) => rep.rep_index === selectedRepIndex) ?? reps[0] ?? null;
  const reportSubscores = currentSubscoreRows(report.score.subscores);
  const compensationText = metricCompensation(report.score.compensation);
  const rehabRecommendations = buildRehabRecommendations(report).items;

  useEffect(() => {
    setSelectedRepIndex(reps[0]?.rep_index ?? 1);
  }, [report.patient.session_id, reps]);

  return (
    <div className="patient-report-content">
      <header>
        <span>{label}</span>
        <strong>{formatScore(report.score.final_score)}</strong>
        <small>综合评分</small>
      </header>
      {fallbackUsed && (
        <section className="score-fallback-notice">
          <strong>已完成降级评分</strong>
          <p>{FALLBACK_SCORE_NOTICE}</p>
        </section>
      )}
      <div className="patient-report-metrics">
        {formatDateText(report.patient.training_time) && <Metric label="训练时间" value={formatDateText(report.patient.training_time)} />}
        {formatDurationSeconds(report.completion.duration_sec) && <Metric label="训练时长" value={formatDurationSeconds(report.completion.duration_sec)} />}
        <Metric label="综合评分" value={formatScore(report.score.final_score)} />
        <Metric label="评分状态" value={scoreStatusLabelForReport(report)} />
        <Metric label="完成次数" value={`${reportCompletedReps(report)} 次`} />
        <Metric label="有效次数" value={`${reportValidReps(report)} 次`} />
        {report.score.data_quality_level && <Metric label="数据质量" value={report.score.data_quality_level} />}
        {compensationText && <Metric label="代偿评估" value={compensationText} />}
      </div>

      {reportSubscores.length > 0 && (
        <section className="patient-rep-score-section">
          <header>
            <h2>五维动作质量评分</h2>
          </header>
          <div className="patient-report-metrics">
            {reportSubscores.map((item) => (
              <Metric key={item.key} label={item.label} value={formatSubscore(item.value, item.key)} />
            ))}
          </div>
        </section>
      )}

      <ul className="business-list">
        {rehabRecommendations.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      {reps.length > 0 && (
        <section className="patient-rep-score-section">
          <header>
            <h2>逐次评分</h2>
            <span>选择某一次动作查看简要评分。</span>
          </header>
          <div className="patient-rep-picker">
            {reps.map((rep) => (
              <button
                key={rep.rep_index}
                type="button"
                className={selectedRep?.rep_index === rep.rep_index ? "selected" : ""}
                onClick={() => setSelectedRepIndex(rep.rep_index)}
              >
                Rep {rep.rep_index}
              </button>
            ))}
          </div>
          {selectedRep && (
            <div className="patient-rep-detail">
              <Metric label="本次综合评分" value={formatScore(selectedRep.final_score)} />
              {currentSubscoreRows(selectedRep.subscores).map((item) => (
                <Metric key={item.key} label={item.label} value={formatSubscore(item.value, item.key)} />
              ))}
              <Metric label="有效状态" value={selectedRep.valid ? "有效" : "无效"} />
              <Metric label="代偿" value={selectedRep.compensation.detected ? selectedRep.compensation.labels.join(" / ") : "未检测到明显代偿"} />
              {!selectedRep.valid && selectedRep.invalid_reason && <Metric label="无效原因" value={selectedRep.invalid_reason} />}
              {selectedRep.feedback.length > 0 && (
                <div className="patient-rep-feedback">
                  <span>动作反馈</span>
                  <strong>{selectedRep.feedback.join("；")}</strong>
                </div>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

interface PatientRepRow {
  rep_index: number;
  valid: boolean;
  final_score: number | null;
  subscores: Partial<Record<CurrentSubscoreKey, number | null>>;
  compensation: { detected: boolean; labels: string[] };
  feedback: string[];
  invalid_reason: string | null;
}

function patientRepRows(report: RehabReport): PatientRepRow[] {
  const actionRows = report.action_summaries ?? report.score.action_summaries ?? [];
  if (actionRows.length) {
    return actionRows.map((row) => {
      const record = row as unknown as Record<string, unknown>;
      const compensation = compensationFromUnknown(row.compensation);
      return {
        rep_index: row.rep_index,
        valid: row.valid !== false,
        final_score: finiteNumber(row.final_score),
        subscores: subscoreMap(record.subscores),
        compensation,
        feedback: Array.isArray(row.feedback) ? row.feedback.filter((item): item is string => typeof item === "string") : [],
        invalid_reason: typeof record.invalid_reason === "string" ? record.invalid_reason : null
      };
    });
  }
  return (report.rep_details ?? []).map((row) => ({
    rep_index: row.rep_index,
    valid: true,
    final_score: finiteNumber(row.final_score),
    subscores: {
      ROM_score: numericPrefix(row.ROM),
      smoothness_score: numericPrefix(row.smoothness),
      coordination_score: numericPrefix(row.coordination),
      muscle_score: numericPrefix(row.muscle_performance)
    },
    compensation: compensationFromUnknown(row.compensation),
    feedback: [],
    invalid_reason: null
  }));
}

function subscoreMap(value: unknown): PatientRepRow["subscores"] {
  const source = (value && typeof value === "object" ? value : {}) as Record<string, unknown>;
  return Object.fromEntries(CURRENT_SUBSCORE_FIELDS.map((key) => [key, finiteNumber(source[key])])) as PatientRepRow["subscores"];
}

function compensationFromUnknown(value: unknown): PatientRepRow["compensation"] {
  const source = (value && typeof value === "object" ? value : {}) as Record<string, unknown>;
  const detected = source.detected === true || source.status === "detected" || source.risk === "detected";
  const types = Array.isArray(source.types) ? source.types.filter((item): item is string => typeof item === "string") : [];
  const labels = Array.isArray(source.labels) ? source.labels.filter((item): item is string => typeof item === "string") : [];
  return {
    detected,
    labels: labels.length ? labels : types.length ? types.map(compensationTypeLabel) : detected ? ["检测到代偿"] : []
  };
}

function compensationTypeLabel(type: string) {
  if (type === "LF") return "躯干前倾";
  if (type === "TR") return "躯干旋转";
  if (type === "TREMOR") return "手部抖动";
  return type;
}

function numericPrefix(value: string | number | null | undefined): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "string") return null;
  const match = value.match(/-?\d+(?:\.\d+)?/u);
  return match ? Number(match[0]) : null;
}

function Metric({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div className="patient-report-metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function labelForSession(session: SessionSummary) {
  return session.task_name || session.original_task_name || "训练记录";
}

function reportTime(session: SessionSummary) {
  return session.ended_at_ms ?? session.started_at_ms ?? 0;
}

function formatDateText(value: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function formatDurationMs(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  return formatDurationSeconds(value / 1000);
}

function formatDurationSeconds(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  return `${Math.round(value)} 秒`;
}

function metricCompensation(value: unknown) {
  const formatted = formatCompensation(value);
  return isNoDataText(formatted) ? "" : formatted;
}
