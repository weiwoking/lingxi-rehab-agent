import { Cloud, Edit3, Printer, RefreshCw, Rotate3D, Save, UploadCloud } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import type { RehabReport } from "../contracts/report";
import {
  DRINK_TASK_NAME,
  formatDurationSeconds,
  formatRecordDateTime,
  getPublicRecordLabel,
  isShowcaseSession,
  showcaseReports,
  showcaseSessions
} from "../data/showcaseRecords";
import { fetchSessions, type SessionSummary } from "../services/patientApi";
import {
  fetchCloudSyncSummary,
  refreshCloudSyncStatus,
  setCloudSyncAutoUpload,
  uploadCloudSyncSession,
  uploadPendingCloudSync,
  type CloudSyncActionResult,
  type CloudSyncSummary
} from "../services/cloudSyncApi";
import { fetchSessionReport } from "../services/reportApi";
import { loadDoctorNote, saveDoctorNote } from "../utils/doctorNotes";
import { formatCompensation } from "../utils/reportFieldFormatters";
import { buildRehabRecommendations } from "../utils/reportRecommendation";
import {
  CURRENT_SUBSCORE_FIELDS,
  type CurrentSubscoreKey,
  currentSubscoreRows,
  finiteNumber,
  formatScore,
  formatSubscore,
  isNoDataText
} from "../utils/currentReportSchema";
import {
  FALLBACK_SCORE_NOTICE,
  isFallbackReport,
  reportCompletedReps,
  reportValidReps,
  scoreStatusLabelForReport
} from "../utils/scoreFallbackDisplay";

export function SessionHistoryPage() {
  const { patientId = "P-0217" } = useParams();
  const [params, setParams] = useSearchParams();
  const [sessions, setSessions] = useState<SessionSummary[]>(showcaseSessions);
  const [report, setReport] = useState<RehabReport | null>(null);
  const [message, setMessage] = useState("");
  const [note, setNote] = useState("");
  const [cloudSummary, setCloudSummary] = useState<CloudSyncSummary | null>(null);
  const [cloudMessage, setCloudMessage] = useState("");
  const [busySessionId, setBusySessionId] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [autoBusy, setAutoBusy] = useState(false);
  const noteRef = useRef<HTMLTextAreaElement | null>(null);
  const sorted = useMemo(
    () => [...sessions].sort((a, b) => (b.started_at_ms ?? 0) - (a.started_at_ms ?? 0)),
    [sessions]
  );
  const sessionId = params.get("session_id") || sorted[0]?.session_id || "";

  const reloadCloudSummary = async () => {
    try {
      setCloudSummary(await fetchCloudSyncSummary());
    } catch {
      setCloudSummary(null);
    }
  };

  const reloadSessions = async () => {
    try {
      const items = await fetchSessions(patientId);
      setSessions([
        ...items.filter((item) => item.report_ready && item.source_origin === "pc_udp_6006_ingest"),
        ...showcaseSessions
      ]);
    } catch {
      setSessions(showcaseSessions);
    }
  };

  const reloadCloudAndSessions = async () => {
    await Promise.all([reloadSessions(), reloadCloudSummary()]);
  };

  useEffect(() => {
    void reloadCloudAndSessions();
  }, [patientId]);

  useEffect(() => {
    setReport(null);
    setMessage("");
    if (!sessionId) return;
    setNote(loadDoctorNote(sessionId));
    if (isShowcaseSession(sessionId)) {
      setReport(showcaseReports[sessionId]);
      return;
    }
    fetchSessionReport(patientId, sessionId)
      .then(setReport)
      .catch(() => setMessage("该记录暂未生成可查看的训练报告。"));
  }, [patientId, sessionId]);

  const selectedSession = sorted.find((session) => session.session_id === sessionId);

  const saveNote = () => {
    if (!sessionId) return;
    saveDoctorNote(sessionId, note);
    setMessage("备注已保存。");
  };

  const handleSingleUpload = async (targetSessionId: string) => {
    setBusySessionId(targetSessionId);
    setCloudMessage("");
    try {
      const result = await uploadCloudSyncSession(targetSessionId);
      setCloudMessage(cloudActionMessage(result, "single"));
    } catch {
      setCloudMessage("提交上传请求失败，请稍后重试。");
    } finally {
      setBusySessionId(null);
      await reloadCloudAndSessions();
    }
  };

  const handleBulkUpload = async () => {
    setBulkBusy(true);
    setCloudMessage("");
    try {
      const result = await uploadPendingCloudSync();
      setCloudMessage(cloudActionMessage(result, "bulk"));
    } catch {
      setCloudMessage("提交批量上传请求失败，请稍后重试。");
    } finally {
      setBulkBusy(false);
      await reloadCloudAndSessions();
    }
  };

  const handleRefreshCloud = async () => {
    setRefreshBusy(true);
    setCloudMessage("");
    try {
      setCloudSummary(await refreshCloudSyncStatus());
      setCloudMessage("云端状态已刷新。");
    } catch {
      setCloudMessage("刷新云端状态失败。");
    } finally {
      setRefreshBusy(false);
      await reloadSessions();
    }
  };

  const handleAutoUploadChange = async (enabled: boolean) => {
    setAutoBusy(true);
    setCloudMessage("");
    try {
      const result = await setCloudSyncAutoUpload(enabled);
      setCloudMessage(cloudActionMessage(result, "auto"));
    } catch {
      setCloudMessage("更新自动同步开关失败。");
    } finally {
      setAutoBusy(false);
      await reloadCloudAndSessions();
    }
  };

  return (
    <main className="workspace-page reports-page">
      <header className="page-title">
        <div>
          <h1>训练报告</h1>
          <span>查看训练评估结果、康复建议与动作还原入口。</span>
        </div>
        {report && (
          <div className="report-page-actions">
            <button type="button" onClick={() => noteRef.current?.focus()}>
              <Edit3 size={16} />
              编辑医生备注
            </button>
            <Link className="primary-page-action" to={`/doctor/reports/${encodeURIComponent(sessionId)}/print`}>
              <Printer size={16} />
              打印报告
            </Link>
          </div>
        )}
      </header>
      <section className="cloud-sync-panel" aria-label="阿里云同步状态">
        <div className="cloud-sync-summary">
          <Cloud size={16} />
          <strong>阿里云同步：{cloudSyncOnlineText(cloudSummary)}</strong>
          <span>已上传：{cloudSummary?.synced_count ?? 0}</span>
          <span>未上传：{cloudSummary?.not_uploaded_count ?? 0}</span>
          <span>等待上传：{cloudSummary?.queued_count ?? 0}</span>
          <span>上传中：{cloudSummary?.uploading_count ?? 0}</span>
          <span>失败：{cloudSummary?.failed_count ?? 0}</span>
          <span>冲突：{cloudSummary?.conflict_count ?? 0}</span>
        </div>
        <div className="cloud-sync-controls">
          <label className={!cloudSummary?.enabled ? "disabled" : ""}>
            <input
              type="checkbox"
              checked={Boolean(cloudSummary?.auto_upload_enabled)}
              disabled={!cloudSummary?.enabled || autoBusy}
              onChange={(event) => void handleAutoUploadChange(event.currentTarget.checked)}
            />
            云端在线时自动同步
          </label>
          <button type="button" onClick={() => void handleBulkUpload()} disabled={!cloudSummary?.enabled || bulkBusy}>
            <UploadCloud size={15} />
            上传全部待同步记录
          </button>
          <button type="button" onClick={() => void handleRefreshCloud()} disabled={refreshBusy}>
            <RefreshCw size={15} />
            刷新云端状态
          </button>
        </div>
        {cloudMessage && <p className="cloud-sync-message">{cloudMessage}</p>}
      </section>
      <section className="reports-layout">
        <aside className="report-session-list">
          {sorted.map((session) => (
            <article
              key={session.session_id}
              className={session.session_id === sessionId ? "report-session-card selected" : "report-session-card"}
            >
              <button
                className="report-session-pick"
                type="button"
                onClick={() => setParams({ session_id: session.session_id })}
              >
                <strong>{labelForSession(session)}</strong>
                <span>{formatRecordDateTime(session.started_at_ms)}</span>
                <small>
                  {formatDurationMs(session.duration_ms)} · 综合评分 {formatScore(session.final_score)}
                </small>
              </button>
              {!isShowcaseSession(session.session_id) && (
                <Link className="cloud-sync-record-action" to={`/doctor/sessions/${encodeURIComponent(session.session_id)}`}>
                  逐次评分
                </Link>
              )}
              <div className="cloud-sync-record-row">
                <span className={`cloud-sync-badge ${cloudSyncClass(session)}`}>
                  {cloudSyncLabel(session)}
                </span>
                {cloudSyncActionText(session) && (
                  <button
                    className="cloud-sync-record-action"
                    type="button"
                    disabled={!cloudSummary?.enabled || busySessionId === session.session_id}
                    onClick={() => void handleSingleUpload(session.session_id)}
                  >
                    {busySessionId === session.session_id ? "提交中" : cloudSyncActionText(session)}
                  </button>
                )}
              </div>
            </article>
          ))}
          {!sorted.length && <EmptyState title="暂无训练报告" size="compact" />}
        </aside>
        <section className="real-report-panel polished-report-panel">
          {!report ? (
            <EmptyState title="请选择训练记录" description={message || "选择左侧记录后查看报告详情。"} />
          ) : (
            <ReportDetail
              report={report}
              selectedSession={selectedSession}
              sessionId={sessionId}
              note={note}
              noteRef={noteRef}
              setNote={setNote}
              saveNote={saveNote}
              message={message}
            />
          )}
        </section>
      </section>
    </main>
  );
}

function ReportDetail({
  report,
  selectedSession,
  sessionId,
  note,
  noteRef,
  setNote,
  saveNote,
  message
}: {
  report: RehabReport;
  selectedSession?: SessionSummary;
  sessionId: string;
  note: string;
  noteRef: RefObject<HTMLTextAreaElement | null>;
  setNote: (value: string) => void;
  saveNote: () => void;
  message: string;
}) {
  const reportCompensation = metricCompensation(report.score.compensation);
  const fallbackUsed = isFallbackReport(report);
  const actionSummaries = fallbackUsed ? [] : (report.action_summaries ?? report.score.action_summaries ?? []);
  const reportSubscores = currentSubscoreRows(report.score.subscores);
  const rehabRecommendations = buildRehabRecommendations(report).items;
  return (
    <>
      <header className="real-report-head">
        <div>
          <span>本次训练评估</span>
          <h2>{labelForSession(selectedSession)}</h2>
          <p>{displayTaskName(selectedSession, report.patient.task)}</p>
        </div>
        <div className="real-report-score">
          <strong>{formatScore(report.score.final_score)}</strong>
          <span>综合评分</span>
        </div>
      </header>
      {fallbackUsed && (
        <section className="score-fallback-notice">
          <strong>已完成降级评分</strong>
          <p>{FALLBACK_SCORE_NOTICE}</p>
        </section>
      )}
      <div className="report-fact-grid">
        <Fact label="报告状态" value={scoreStatusLabelForReport(report)} />
        <Fact label="数据质量" value={report.score.data_quality_level || "良好"} />
        <Fact label="训练时间" value={formatDateText(report.patient.training_time)} />
        <Fact label="训练时长" value={formatDurationSeconds(report.completion.duration_sec)} />
        <Fact label="完成次数" value={`${reportCompletedReps(report)} 次`} />
        <Fact label="有效次数" value={`${reportValidReps(report)} 次`} />
        <Fact label="代偿评估" value={reportCompensation} />
      </div>
      {reportSubscores.length > 0 && (
        <section className="report-section">
          <h3>五维动作质量评分</h3>
          <div className="report-fact-grid">
            {reportSubscores.map((item) => (
              <Fact key={item.key} label={item.label} value={formatSubscore(item.value, item.key)} />
            ))}
          </div>
        </section>
      )}
      <section className="report-section">
        <h3>动作评分明细</h3>
        <div className="report-action-list">
          {actionSummaries.length ? actionSummaries.map((item) => (
            <article key={item.rep_index}>
              <strong>第 {item.rep_index} 次</strong>
              <span>综合 {formatScore(item.final_score)}</span>
              {currentActionSubscoreRows(item).map((subscore) => (
                <span key={subscore.key}>{subscore.shortLabel} {formatSubscore(subscore.value, subscore.key)}</span>
              ))}
              <span>{metricCompensation(item.compensation ?? report.score.compensation)}</span>
            </article>
          )) : (
            <article className="report-action-empty">
              <strong>未检测到完整动作周期</strong>
              <span>未生成完整动作明细</span>
            </article>
          )}
        </div>
      </section>
      <section className="report-section">
        <h3>康复建议</h3>
        <ul className="business-list">
          {rehabRecommendations.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>
      <section className="doctor-note-panel">
        <h3>医生备注</h3>
        <textarea
          ref={noteRef}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="医生可结合患者实际情况补充专业意见……"
        />
        <button type="button" onClick={saveNote}>
          <Save size={16} />
          保存备注
        </button>
      </section>
      {isShowcaseSession(sessionId) ? (
        <Link className="primary-page-action" to={`/doctor/reconstruction?session_id=${encodeURIComponent(sessionId)}`}>
          <Rotate3D size={16} />
          查看动作还原
        </Link>
      ) : (
        <button className="primary-page-action" type="button" disabled>
          <Rotate3D size={16} />
          动作还原暂不可用
        </button>
      )}
      {message && <p className="form-message">{message}</p>}
    </>
  );
}

function labelForSession(session?: SessionSummary) {
  if (!session) return "训练记录";
  return session.record_kind === "showcase" ? getPublicRecordLabel(session.session_id) : "训练记录";
}

function displayTaskName(session?: SessionSummary, reportTask?: string) {
  return session?.task_name || session?.original_task_name || reportTask || DRINK_TASK_NAME;
}

function Fact({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function currentActionSubscoreRows(action: unknown) {
  const source = action && typeof action === "object" ? action as Record<string, unknown> : {};
  const subscores = source.subscores && typeof source.subscores === "object" ? source.subscores as Record<string, unknown> : {};
  return CURRENT_SUBSCORE_FIELDS.map((key) => ({
    key,
    shortLabel: key === "speed_score" ? "速度" : key === "ROM_score" ? "ROM" : key === "smoothness_score" ? "平滑" : key === "coordination_score" ? "协调" : "肌肉激活",
    value: finiteNumber(subscores[key])
  })).filter((item): item is { key: CurrentSubscoreKey; shortLabel: string; value: number } => item.value !== null);
}

function metricCompensation(value: unknown) {
  const formatted = formatCompensation(value);
  return isNoDataText(formatted) ? "" : formatted;
}

function formatDateText(value: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function formatDurationMs(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  return `${Math.round(value / 1000)} 秒`;
}

function cloudSyncOnlineText(summary: CloudSyncSummary | null) {
  if (!summary?.enabled) return "未启用";
  return summary.cloud_online ? "在线" : "离线";
}

function cloudSyncLabel(session: SessionSummary) {
  if (session.record_kind === "showcase") return "系统演示";
  switch (session.cloud_sync?.status) {
    case "not_ready":
      return "数据尚未完成";
    case "not_uploaded":
      return "未上传";
    case "queued":
      return "等待上传";
    case "synced":
      return "已上传";
    case "uploading":
      return "上传中";
    case "failed":
      return "上传失败";
    case "conflict":
      return "同步冲突";
    default:
      return "未上传";
  }
}

function cloudSyncClass(session: SessionSummary) {
  if (session.record_kind === "showcase") return "showcase";
  const status = session.cloud_sync?.status;
  if (
    status === "not_ready" ||
    status === "not_uploaded" ||
    status === "queued" ||
    status === "uploading" ||
    status === "synced" ||
    status === "failed" ||
    status === "conflict"
  ) {
    return status;
  }
  return "not_uploaded";
}

function cloudSyncActionText(session: SessionSummary) {
  if (session.record_kind === "showcase") return "";
  const status = session.cloud_sync?.status;
  if (status === "not_uploaded") return "上传";
  if (status === "failed") return "重试";
  return "";
}

function cloudActionMessage(result: CloudSyncActionResult, mode: "single" | "bulk" | "auto") {
  if (result.reason === "feature_disabled") return "云端功能未启用。";
  if (result.reason === "cloud_offline") return "阿里云当前离线，请启动服务器后重试。";
  if (result.reason === "not_ready") return "该训练记录数据尚未完成。";
  if (result.reason === "session_not_found") return "未找到对应训练记录。";
  if (result.reason === "already_synced") return "该训练记录已上传。";
  if (result.reason === "remote_session_sha_conflict") return "云端已有同名记录且文件指纹不同。";
  if (mode === "bulk" && result.accepted) return `已加入上传队列：${result.queued_count ?? 0} 条。`;
  if (mode === "auto" && result.accepted) {
    return result.auto_upload_enabled ? "已开启本次运行期间的自动同步。" : "已关闭自动同步。";
  }
  if (mode === "single" && result.accepted) return "已加入上传队列。";
  return result.reason || "操作未被接受。";
}
