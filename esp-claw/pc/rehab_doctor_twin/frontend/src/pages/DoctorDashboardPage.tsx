import { ClipboardCheck, FileText, Library, TrendingUp } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { EmptyState } from "../components/EmptyState";
import { getPublicRecordLabel, showcaseReports, showcaseSessions } from "../data/showcaseRecords";
import { fetchDoctorOverview, type DoctorOverview } from "../services/doctorOverviewApi";

type OverviewRecord = {
  session_id: string;
  started_at_ms: number;
  final_score: number | null;
  record_kind?: string;
};

export function DoctorDashboardPage() {
  const [overview, setOverview] = useState<DoctorOverview | null>(null);

  useEffect(() => {
    fetchDoctorOverview().then(setOverview).catch(() => setOverview(null));
  }, []);

  const hasRealRecords = Boolean(overview?.recent_sessions.length);
  const fallbackSessions = useMemo(
    () => [...showcaseSessions].sort((a, b) => (b.started_at_ms ?? 0) - (a.started_at_ms ?? 0)),
    []
  );
  const displaySessions: OverviewRecord[] = hasRealRecords
    ? overview!.recent_sessions.map((item) => ({ ...item, record_kind: "formal" }))
    : fallbackSessions.map((item) => ({
      session_id: item.session_id,
      started_at_ms: item.started_at_ms,
      final_score: item.final_score,
      record_kind: item.record_kind
    }));
  const latest = displaySessions[0];
  const trend = hasRealRecords
    ? overview?.score_trend ?? []
    : [...fallbackSessions]
      .sort((a, b) => (a.started_at_ms ?? 0) - (b.started_at_ms ?? 0))
      .map((item) => ({ session_id: item.session_id, started_at_ms: item.started_at_ms, final_score: item.final_score ?? 0 }));
  const attentionText = hasRealRecords
    ? overview?.attention_items[0]?.message
    : showcaseReports.SHOWCASE_DRINK_001.summary;

  return (
    <main className="workspace-page overview-page">
      <header className="page-title">
        <div>
          <h1>康复训练总览</h1>
          <span>汇总患者近期训练、评估与康复记录。</span>
        </div>
      </header>

      <section className="overview-band">
        <article className="overview-patient-card">
          <span>患者</span>
          <h2>{overview?.patient.display_name || "P-0217"}</h2>
          <p>{overview?.patient.patient_id || "P-0217"}</p>
        </article>
        <article className="overview-latest-card">
          <span>最近一次训练</span>
          <h2>{latest ? recordLabel(latest) : "暂无训练记录"}</h2>
          <p>{latest ? formatDateTime(latest.started_at_ms) : "完成训练后自动更新"}</p>
        </article>
      </section>

      <section className="overview-summary-grid">
        <Summary label="最近综合评分" value={formatScore(latest?.final_score)} icon={<ClipboardCheck size={17} />} />
        <Summary label="本周训练" value={`${hasRealRecords ? overview?.weekly_valid_session_count ?? 0 : 2} 次`} icon={<TrendingUp size={17} />} />
        <Summary label="训练报告" value={`${hasRealRecords ? overview?.formal_report_count ?? 0 : 2} 份`} icon={<FileText size={17} />} />
        <Summary label="任务库数量" value={`${overview?.task_library_count ?? 2} 项`} icon={<Library size={17} />} />
      </section>

      <section className="offline-overview-grid">
        <article className="overview-list-card overview-trend-card">
          <h2>近期评分趋势</h2>
          {trend.length ? (
            <ol>
              {trend.map((item) => (
                <li key={item.session_id} className="overview-list-row">
                  <span>{recordLabel({ ...item, record_kind: hasRealRecords ? "formal" : "showcase" })}</span>
                  <strong>{formatScore(item.final_score)}</strong>
                </li>
              ))}
            </ol>
          ) : (
            <EmptyState size="compact" title="暂无评分趋势" />
          )}
        </article>
        <article className="overview-list-card overview-recent-card">
          <h2>最近训练记录</h2>
          {displaySessions.length ? (
            <ol>
              {displaySessions.map((session) => (
                <li key={session.session_id} className="overview-list-row">
                  <span>{recordLabel(session)}</span>
                  <strong>{formatScore(session.final_score)}</strong>
                </li>
              ))}
            </ol>
          ) : (
            <EmptyState size="compact" title="暂无训练记录" />
          )}
        </article>
        <article className="overview-list-card overview-attention-card">
          <h2>待关注提醒</h2>
          {attentionText ? (
            <ol>
              <li className="overview-list-row">
                <span>{latest ? recordLabel(latest) : "训练记录"}</span>
                <strong>{attentionText}</strong>
              </li>
            </ol>
          ) : (
            <EmptyState size="compact" title="暂无提醒" />
          )}
        </article>
      </section>
    </main>
  );
}

function Summary({ label, value, icon }: { label: string; value: string; icon: ReactNode }) {
  return (
    <article className="overview-summary-item">
      <span>{icon}{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function recordLabel(record: OverviewRecord) {
  return record.record_kind === "showcase" ? getPublicRecordLabel(record.session_id) : "训练记录";
}

function formatDateTime(value?: number | null) {
  return value ? new Date(value).toLocaleString("zh-CN") : "暂无";
}

function formatScore(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "暂无";
}
