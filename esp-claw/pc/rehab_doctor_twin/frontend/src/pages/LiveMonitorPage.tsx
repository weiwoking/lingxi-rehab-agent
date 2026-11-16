import { FileText, Home } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { RehabReport } from "../contracts/report";
import { fetchPatientReport, fetchPatientReports } from "../services/runtimeApi";
import type { SessionSummary } from "../services/patientApi";

const RECENT_LIMIT = 5;

export function LiveMonitorPage() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [latestReport, setLatestReport] = useState<RehabReport | null>(null);

  useEffect(() => {
    let active = true;
    fetchPatientReports()
      .then((items) => {
        if (!active) return;
        setSessions(sortFormalSessions(items));
      })
      .catch(() => {
        if (active) setSessions([]);
      });
    return () => {
      active = false;
    };
  }, []);

  const latestSession = sessions[0] ?? null;

  useEffect(() => {
    let active = true;
    if (!latestSession?.session_id) {
      setLatestReport(null);
      return () => {
        active = false;
      };
    }
    fetchPatientReport(latestSession.session_id)
      .then((report) => {
        if (active) setLatestReport(report);
      })
      .catch(() => {
        if (active) setLatestReport(null);
      });
    return () => {
      active = false;
    };
  }, [latestSession?.session_id]);

  const overview = useMemo(() => {
    const score = resolveScore(latestSession, latestReport);
    return [
      { label: "最近训练", value: latestSession ? resolveTaskName(latestSession, latestReport) : "暂无" },
      { label: "最近评分", value: formatScore(score) }
    ];
  }, [latestReport, latestSession]);

  return (
    <main className="patient-training-page patient-rehab-agent-page">
      <header className="patient-header rehab-agent-header">
        <div>
          <span>患者端</span>
          <h1>康复智能体</h1>
          <p>康复智能体根据现场状态自主决策训练任务</p>
        </div>
        <nav className="patient-nav">
          <Link to="/patient" className="active">康复智能体</Link>
          <Link to="/patient/reports">训练报告</Link>
          <Link to="/">
            <Home size={16} />
            返回角色入口
          </Link>
        </nav>
      </header>

      <section className="rehab-agent-overview" aria-label="康复概况">
        {overview.map((item) => (
          <article key={item.label} className="rehab-agent-stat-card">
            <span>{item.label}</span>
            <strong>{item.value}</strong>
          </article>
        ))}
      </section>

      <section className="rehab-agent-latest-card">
        <header>
          <div>
            <span>最近一次康复训练</span>
            <h2>{latestSession ? resolveTaskName(latestSession, latestReport) : "尚无康复训练记录"}</h2>
          </div>
          {latestSession && <em>康复智能体自主决策</em>}
        </header>

        {latestSession ? (
          <>
            <div className="rehab-agent-latest-grid">
              <InfoTile label="训练时间" value={formatSessionTime(latestSession, latestReport)} />
              <InfoTile label="训练时长" value={formatDuration(latestSession, latestReport)} />
              <InfoTile label="综合评分" value={formatScore(resolveScore(latestSession, latestReport))} strong />
              <InfoTile label="主要提示" value={formatPrimaryCue(latestReport)} />
            </div>
            {latestReport && resolveFeedback(latestReport) && (
              <div className="rehab-agent-advice">
                <span>康复建议</span>
                <p>{resolveFeedback(latestReport)}</p>
              </div>
            )}
            <div className="rehab-agent-actions">
              <Link to={`/patient/reports?session_id=${encodeURIComponent(latestSession.session_id)}`}>
                <FileText size={18} />
                查看训练报告
              </Link>
            </div>
          </>
        ) : (
          <div className="rehab-agent-empty">尚无康复训练记录</div>
        )}
      </section>

      <section className="rehab-agent-recent-card">
        <header>
          <h2>近期训练</h2>
          <Link to="/patient/reports">查看全部</Link>
        </header>
        {sessions.length ? (
          <div className="rehab-agent-recent-list">
            {sessions.slice(0, RECENT_LIMIT).map((session) => (
              <Link
                key={session.session_id}
                to={`/patient/reports?session_id=${encodeURIComponent(session.session_id)}`}
                className="rehab-agent-recent-row"
              >
                <time>{formatShortTime(session)}</time>
                <strong>{resolveTaskName(session, null)}</strong>
                <span>{formatScore(resolveScore(session, null))}</span>
              </Link>
            ))}
          </div>
        ) : (
          <div className="rehab-agent-empty">暂无训练记录</div>
        )}
      </section>
    </main>
  );
}

function sortFormalSessions(items: SessionSummary[]) {
  return [...items]
    .filter((session) => session.record_kind !== "showcase")
    .filter((session) => Boolean(session.session_id))
    .sort((left, right) => sessionTime(right) - sessionTime(left));
}

function sessionTime(session: SessionSummary) {
  return finiteNumber(session.ended_at_ms) ?? finiteNumber(session.started_at_ms) ?? 0;
}

function resolveTaskName(session: SessionSummary, report: RehabReport | null) {
  return (
    session.task_name ||
    session.original_task_name ||
    session.internal_score_task_name ||
    report?.patient?.task ||
    session.task ||
    "康复训练"
  );
}

function resolveScore(session: SessionSummary | null, report: RehabReport | null) {
  return finiteNumber(report?.score?.final_score) ?? finiteNumber(session?.final_score) ?? null;
}

function formatScore(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "--";
  return `${Math.round(value)} 分`;
}

function formatSessionTime(session: SessionSummary, report: RehabReport | null) {
  if (report?.patient?.training_time) return report.patient.training_time;
  const millis = finiteNumber(session.ended_at_ms) ?? finiteNumber(session.started_at_ms);
  if (millis === null) return "--";
  return new Date(millis).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function formatShortTime(session: SessionSummary) {
  const millis = finiteNumber(session.ended_at_ms) ?? finiteNumber(session.started_at_ms);
  if (millis === null) return "--";
  return new Date(millis).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function formatDuration(session: SessionSummary, report: RehabReport | null) {
  const seconds = finiteNumber(report?.completion?.duration_sec) ?? msToSeconds(session.duration_ms);
  if (seconds === null) return "--";
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}分${rest.toString().padStart(2, "0")}秒`;
}

function formatPrimaryCue(report: RehabReport | null) {
  if (!report) return "--";
  const compensation = report.score?.compensation;
  if (!compensation || typeof compensation !== "object" || Array.isArray(compensation)) return "未检测到明显代偿";
  const record = compensation as Record<string, unknown>;
  const types = Array.isArray(record.types) ? record.types.filter((item): item is string => typeof item === "string") : [];
  if (record.detected === true || record.status === "detected") {
    const labels = types.map(compensationTypeLabel).filter(Boolean);
    return labels.length ? labels.join(" / ") : "检测到代偿";
  }
  return "未检测到明显代偿";
}

function resolveFeedback(report: RehabReport) {
  const firstFeedback = report.score?.feedback?.find((item) => item && item.trim().length > 0);
  return firstFeedback || report.next_recommendations?.[0] || report.summary || "";
}

function msToSeconds(value: number | null | undefined) {
  const millis = finiteNumber(value);
  return millis === null ? null : millis / 1000;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function compensationTypeLabel(type: string) {
  if (type === "LF") return "躯干前倾";
  if (type === "TR") return "躯干旋转";
  if (type === "TREMOR") return "手部抖动";
  return type;
}

function InfoTile({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className={strong ? "rehab-agent-info-tile highlight" : "rehab-agent-info-tile"}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
