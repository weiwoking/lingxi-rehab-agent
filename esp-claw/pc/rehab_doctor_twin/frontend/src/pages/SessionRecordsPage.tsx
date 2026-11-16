import { Activity, BarChart3, Clock3, Database, ListChecks } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { fetchDoctorSessions, prefetchSessionResult, type SessionResultSummary } from "../services/sessionResultsApi";

const PREFETCH_RECENT_COMPLETED_LIMIT = 3;

export function SessionRecordsPage() {
  const [sessions, setSessions] = useState<SessionResultSummary[]>([]);
  const [message, setMessage] = useState("");
  const [selectedSessionId, setSelectedSessionId] = useState("");

  useEffect(() => {
    fetchDoctorSessions()
      .then((value) => {
        const formal = value.sessions
          .filter((session) => session.source_origin === "pc_udp_6006_ingest" || session.source_mode === "device" || session.source_mode === "pc_local")
          .sort((a, b) => (b.started_at_ms ?? 0) - (a.started_at_ms ?? 0));
        setSessions(formal);
        setSelectedSessionId((current) => current || formal[0]?.session_id || "");
        setMessage("");
      })
      .catch(() => {
        setSessions([]);
        setMessage("暂时无法读取 Session 评分记录。");
      });
  }, []);

  const selectedSession = useMemo(
    () => sessions.find((session) => session.session_id === selectedSessionId) ?? sessions[0],
    [selectedSessionId, sessions]
  );

  useEffect(() => {
    sessions
      .filter((session) => session.session_v12_status === "completed")
      .slice(0, PREFETCH_RECENT_COMPLETED_LIMIT)
      .forEach((session) => prefetchSessionResult(session.session_id));
  }, [sessions]);

  return (
    <main className="workspace-page session-records-page doctor-session-page doctor-session-records-page">
      <header className="page-title">
        <div>
          <h1>Session 记录</h1>
          <span>查看设备 Session、逐次评分状态和结果入口。</span>
        </div>
      </header>

      <section className="session-records-layout">
        <aside className="session-record-list">
          {sessions.map((session, index) => (
            <button
              key={session.session_id}
              type="button"
              className={session.session_id === selectedSession?.session_id ? "selected" : ""}
              onClick={() => setSelectedSessionId(session.session_id)}
              onFocus={() => prefetchSessionResult(session.session_id)}
              onMouseEnter={() => prefetchSessionResult(session.session_id)}
            >
              <strong>Session {String(index + 1).padStart(2, "0")}</strong>
              <span>{taskNameForSession(session)}</span>
              <small>{session.session_id}</small>
              <small>{formatDate(session.started_at_ms)}</small>
              <small>{statusLabel(session.session_v12_status || "not_started")}</small>
            </button>
          ))}
          {!sessions.length && (
            <EmptyState
              title="暂无 Session 记录"
              description={message || "完成正式训练后将在这里显示 Session 评分记录。"}
              size="compact"
            />
          )}
        </aside>

        <section className="session-record-detail">
          {selectedSession ? (
            <>
              <header>
                <div>
                  <span>当前 Session</span>
                  <h2>{selectedSession.session_id}</h2>
                  <p>{taskNameForSession(selectedSession)}</p>
                </div>
                <Link
                  className="primary-page-action"
                  to={`/doctor/sessions/${encodeURIComponent(selectedSession.session_id)}`}
                  onFocus={() => prefetchSessionResult(selectedSession.session_id)}
                  onMouseEnter={() => prefetchSessionResult(selectedSession.session_id)}
                >
                  查看逐次评分
                </Link>
              </header>
              <div className="session-record-facts">
                <Fact icon={<ListChecks size={16} />} label="训练任务" value={taskNameForSession(selectedSession)} />
                <Fact icon={<ListChecks size={16} />} label="Session 状态" value={statusLabel(selectedSession.session_v12_status || "not_started")} />
                <Fact icon={<Clock3 size={16} />} label="Session 时间" value={formatDate(selectedSession.started_at_ms)} />
                <Fact icon={<Database size={16} />} label="数据来源" value={`${selectedSession.source_mode || "-"} / ${selectedSession.source_origin || "-"}`} />
                <Fact icon={<Activity size={16} />} label="评分配置" value={scoreProfileLabel(selectedSession.score_profile)} />
                <Fact icon={<Activity size={16} />} label="动作 / 道具" value={`${motionLabel(selectedSession.motion_family)} / ${objectLabel(selectedSession.object_type)}`} />
                <Fact icon={<BarChart3 size={16} />} label="平均分" value={formatScore(selectedSession.final_score)} />
              </div>
              {selectedSession.task_registry_status === "unknown" && (
                <p className="session-result-error">任务未在 Task Registry 中登记：{selectedSession.unsupported_task_reason || "UNKNOWN_TASK"}</p>
              )}
              <p className="session-record-note">
                逐次评分、Rep 选择和传感器曲线均在 Session 详情页内查看；训练报告仍保留在“训练报告”页面。
              </p>
            </>
          ) : (
            <EmptyState title="请选择 Session" description={message || "左侧选择一条 Session 记录。"} />
          )}
        </section>
      </section>
    </main>
  );
}

function Fact({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div>
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function taskNameForSession(session: SessionResultSummary) {
  return session.task_name || session.original_task_name || "未知训练任务";
}

function motionLabel(value?: string | null) {
  if (value === "drink_water") return "喝水动作";
  if (value === "lateral_transfer") return "横向取放";
  return value || "未解析";
}

function objectLabel(value?: string | null) {
  if (value === "cup") return "水杯";
  if (value === "ball") return "球体";
  return value || "未解析";
}

function scoreProfileLabel(value?: string | null) {
  if (value === "drink_water") return "喝水评分";
  if (value === "lateral_transfer") return "横向取放评分";
  return value || "未解析";
}

function statusLabel(status: string) {
  if (status === "completed") return "已完成";
  if (status === "running") return "评分中";
  if (status === "pending") return "等待评分";
  if (status === "failed") return "失败";
  return "未开始";
}

function formatDate(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? new Date(value).toLocaleString("zh-CN") : "-";
}

function formatScore(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "-";
}
