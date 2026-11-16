import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { EmptyState } from "../components/EmptyState";
import { formatRecordDateTime, getPublicRecordLabel, showcaseSessions } from "../data/showcaseRecords";
import { fetchSessions, type SessionSummary } from "../services/patientApi";
import { isFallbackSession } from "../utils/scoreFallbackDisplay";

export function RawDataLogsPage() {
  const [sessions, setSessions] = useState<SessionSummary[]>(showcaseSessions);
  const [message, setMessage] = useState("");

  const load = useCallback(() => {
    setMessage("");
    fetchSessions("P-0217")
      .then((items) => {
        setSessions([
          ...items.filter((item) => item.source_origin === "pc_udp_6006_ingest"),
          ...showcaseSessions
        ]);
      })
      .catch((error: Error) => {
        setSessions(showcaseSessions);
        setMessage(error.message);
      });
  }, []);

  useEffect(() => { load(); }, [load]);

  const sorted = useMemo(
    () => [...sessions].sort((a, b) => (b.started_at_ms ?? 0) - (a.started_at_ms ?? 0)),
    [sessions]
  );

  return (
    <main className="workspace-page logs-page">
      <header className="page-title">
        <div>
          <h1>数据日志</h1>
          <span>查看患者训练记录与评分摘要。</span>
        </div>
        <button onClick={load}><RefreshCw size={15} />刷新</button>
      </header>
      {message && <p className="form-message">{message}</p>}
      <div className="formal-session-table polished-log-table">
        <table>
          <thead>
            <tr>
              <th>训练编号</th>
              <th>训练时间</th>
              <th>训练时长</th>
              <th>有效次数</th>
              <th>状态</th>
              <th>评分状态</th>
              <th>综合评分</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((item) => (
              <tr key={item.session_id} className={`log-row ${statusClass(item.training_status || item.status)}`}>
                <td className="log-record-cell"><b>{labelForSession(item)}</b></td>
                <td>{formatRecordDateTime(item.started_at_ms)}</td>
                <td>{formatDurationMs(item.duration_ms)}</td>
                <td>{repetitionCountForLog(item)}</td>
                <td>
                  <span className={`log-status-badge ${statusClass(item.training_status || item.status)}`}>
                    {statusLabel(item.training_status || item.status)}
                  </span>
                </td>
                <td>
                  <span className={`log-score-status-badge ${scoreStatusClass(item.score_status || item.scoring_status, item)}`}>
                    {scoreStatusLabel(item.score_status || item.scoring_status, item)}
                  </span>
                </td>
                <td>
                  <span className={typeof item.final_score === "number" && Number.isFinite(item.final_score) ? "log-score-value" : "log-score-empty"}>
                    {formatScore(item.final_score)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!sorted.length && (
          <EmptyState
            title="暂无训练记录"
            description="完成训练后可在这里查看记录与评分摘要。"
          />
        )}
      </div>
    </main>
  );
}

function labelForSession(session: SessionSummary) {
  return session.record_kind === "showcase" ? getPublicRecordLabel(session.session_id) : "训练记录";
}

function statusLabel(value: string | undefined) {
  if (value === "completed") return "已完成";
  if (value === "COMPLETED") return "已完成";
  return value || "未知";
}

function statusClass(value: string | undefined) {
  if (value === "completed" || value === "COMPLETED") return "completed";
  if (value === "INVALID" || value === "invalid") return "invalid";
  if (value === "FAILED" || value === "failed") return "failed";
  return "unknown";
}

function scoreStatusLabel(value?: string, item?: SessionSummary) {
  if (isFallbackSession(item)) return "已完成降级评分";
  if (value === "failed") return item?.score_error_message || "评分服务未生成结果文件";
  if (value === "processing") return "评分中";
  if (value === "pending") return "等待评分";
  if (value === "unknown") return "暂未提交评分";
  if (value === "showcase" || value === "COMPLETED" || value === "completed") return "已评分";
  return value || "暂无";
}

function scoreStatusClass(value?: string, item?: SessionSummary) {
  if (isFallbackSession(item)) return "scored";
  if (value === "showcase" || value === "COMPLETED" || value === "completed" || value === "succeeded") return "scored";
  if (value === "failed") return "failed";
  if (value === "processing" || value === "pending") return "pending";
  return "unknown";
}

function repetitionCountForLog(item: SessionSummary) {
  if (isFallbackSession(item)) return item.valid_repetition_count ?? item.repetition_count ?? 0;
  return item.completed_reps ?? item.valid_row_count ?? 0;
}

function formatDurationMs(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "暂无";
  return `${Math.round(value / 1000)} 秒`;
}

function formatScore(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "暂无";
}
