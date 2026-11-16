import { Pause, Play, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { SensorTrendChart } from "../components/SensorTrendChart";
import {
  fetchSessionResult,
  fetchSessionTrajectory,
  requestSessionRescore,
  type SessionResultDetail,
  type SessionResultRepetition,
  type SessionTrajectory
} from "../services/sessionResultsApi";
import { compensationLabels, frameAtElapsedMs, trajectorySeries } from "../twin/actualTrajectory";

const SUBSCORE_KEYS = ["speed_score", "ROM_score", "smoothness_score", "coordination_score", "muscle_score"] as const;
const SUBSCORE_LABELS: Record<(typeof SUBSCORE_KEYS)[number], string> = {
  speed_score: "速度",
  ROM_score: "活动范围",
  smoothness_score: "平滑度",
  coordination_score: "协调性",
  muscle_score: "肌肉协同"
};
const POLLING_STATUSES = new Set(["not_started", "pending", "running"]);
const PLAYBACK_SPEEDS = [0.5, 1, 1.5, 2] as const;

type DetailMode = "score" | "sensors";
type SensorSeriesRow = {
  label: string;
  field: string;
  totalPoints: number;
  points: Array<{ timeMs: number; value: number }>;
};
type CanonicalSessionResultDetail = SessionResultDetail & {
  valid?: boolean | null;
  invalid_reason?: string | null;
  data_quality_level?: string | null;
  warnings?: string[];
  scoring_authority?: string | null;
  artifact_origin?: string | null;
  rescore_allowed?: boolean;
};

export function SessionResultsPage() {
  const { sessionId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const [detail, setDetail] = useState<SessionResultDetail | null>(null);
  const [trajectory, setTrajectory] = useState<SessionTrajectory | null>(null);
  const [message, setMessage] = useState("");
  const [rescoreMessage, setRescoreMessage] = useState("");
  const [rescoreBusy, setRescoreBusy] = useState(false);
  const [mode, setMode] = useState<DetailMode>("score");
  const [playing, setPlaying] = useState(false);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [speed, setSpeed] = useState<(typeof PLAYBACK_SPEEDS)[number]>(1);
  const animationRef = useRef<number | null>(null);
  const lastTickRef = useRef<number | null>(null);

  const requestedRep = Number(params.get("rep") || "1");
  const hasRepParam = params.has("rep");
  const selectedRep = useMemo(() => {
    if (!detail?.repetitions.length) return null;
    return detail.repetitions.find((rep) => rep.rep_index === requestedRep) ?? detail.repetitions[0];
  }, [detail, requestedRep]);

  const loadDetail = useCallback(async () => {
    if (!sessionId) return;
    try {
      const value = await fetchSessionResult(sessionId);
      setDetail(value);
      setMessage("");
      if (!hasRepParam && value.repetitions[0]) {
        setParams({ rep: String(value.repetitions[0].rep_index) }, { replace: true });
      }
    } catch {
      setMessage("当前训练记录尚未生成 Session V1.2 逐次评分结果。");
    }
  }, [hasRepParam, sessionId, setParams]);

  useEffect(() => {
    setDetail(null);
    setTrajectory(null);
    setElapsedMs(0);
    setPlaying(false);
    setMode("score");
    void loadDetail();
  }, [loadDetail]);

  useEffect(() => {
    if (!detail || !POLLING_STATUSES.has(detail.scoring_status)) return;
    const timer = window.setInterval(() => void loadDetail(), 1000);
    return () => window.clearInterval(timer);
  }, [detail, loadDetail]);

  useEffect(() => {
    setTrajectory(null);
    setElapsedMs(0);
    setPlaying(false);
  }, [selectedRep?.rep_index, sessionId]);

  useEffect(() => {
    if (mode !== "sensors") return;
    setTrajectory(null);
    setElapsedMs(0);
    setPlaying(false);
    if (!sessionId || !selectedRep) return;
    fetchSessionTrajectory(sessionId, selectedRep.rep_index)
      .then(setTrajectory)
      .catch(() =>
        setTrajectory({
          schema_version: "doctor_real_trajectory_v1",
          session_id: sessionId,
          rep_index: selectedRep.rep_index,
          sensor_mapping_version: "rehab_sensor_mapping_20260806",
          time_base: "original_timestamp",
          playback_available: false,
          playback_error: "trajectory_not_found",
          frame_count: 0,
          duration_ms: 0,
          orientation_format: "unavailable",
          frames: []
        })
      );
  }, [mode, selectedRep?.rep_index, sessionId]);

  useEffect(() => {
    if (!playing || !trajectory?.playback_available) return;
    const tick = (now: number) => {
      const previous = lastTickRef.current ?? now;
      lastTickRef.current = now;
      setElapsedMs((value) => {
        const next = value + (now - previous) * speed;
        const duration = Math.max(0, trajectory.duration_ms ?? 0);
        if (duration > 0 && next >= duration) {
          setPlaying(false);
          return duration;
        }
        return next;
      });
      animationRef.current = window.requestAnimationFrame(tick);
    };
    animationRef.current = window.requestAnimationFrame(tick);
    return () => {
      if (animationRef.current !== null) window.cancelAnimationFrame(animationRef.current);
      animationRef.current = null;
      lastTickRef.current = null;
    };
  }, [playing, speed, trajectory]);

  const durationMs = Math.max(0, trajectory?.duration_ms ?? selectedRep?.duration_ms ?? 0);
  const series = useMemo(() => trajectory ? trajectorySeries(trajectory, elapsedMs) : null, [elapsedMs, trajectory]);
  const currentFrame = trajectory ? frameAtElapsedMs(trajectory, elapsedMs) : null;

  const requestRescore = async () => {
    if (!sessionId) return;
    setRescoreBusy(true);
    setRescoreMessage("");
    try {
      await requestSessionRescore(sessionId);
      setRescoreMessage("已提交重新评分，状态将自动刷新。");
      await loadDetail();
    } catch (error) {
      setRescoreMessage(error instanceof Error ? error.message : "重新评分请求失败");
    } finally {
      setRescoreBusy(false);
    }
  };

  return (
    <main className="workspace-page session-results-page doctor-session-page doctor-session-results-page">
      <header className="page-title">
        <div>
          <h1>Session 逐次评分</h1>
          <span>查看本次训练的 Rep 评分摘要与历史传感器曲线。</span>
        </div>
        <Link className="primary-page-action" to="/doctor/sessions">
          返回 Session 记录
        </Link>
      </header>

      {!detail ? (
        <EmptyState title="暂无逐次评分结果" description={message || "Session V1.2 编排完成后会在这里显示。"} />
      ) : (
        <section className="session-results-layout">
          <section className="session-results-main">
            <SummaryPanel detail={detail} rescoreBusy={rescoreBusy} rescoreMessage={rescoreMessage} onRescore={requestRescore} />
            <RepPicker repetitions={detail.repetitions} selectedRep={selectedRep} onSelect={(rep) => setParams({ rep: String(rep.rep_index) })} />
          </section>

          <section className="session-detail-panel">
            <header>
              <div>
                <span>当前选择</span>
                <h2>Rep {selectedRep?.rep_index ?? "-"}</h2>
              </div>
              <div className="session-detail-tabs">
                <button type="button" className={mode === "score" ? "selected" : ""} onClick={() => setMode("score")}>评分摘要</button>
                <button type="button" className={mode === "sensors" ? "selected" : ""} onClick={() => setMode("sensors")}>传感器曲线</button>
              </div>
            </header>

            {mode === "score" ? (
              selectedRep ? <SelectedRepScore rep={selectedRep} /> : <EmptyState title="暂无 Rep" size="compact" />
            ) : (
              <SensorCardsPanel
                trajectory={trajectory}
                series={series}
                currentFrame={currentFrame}
                playing={playing}
                speed={speed}
                elapsedMs={elapsedMs}
                durationMs={durationMs}
                onPlayToggle={() => setPlaying((value) => !value)}
                onReset={() => {
                  setPlaying(false);
                  setElapsedMs(0);
                }}
                onSeek={(value) => {
                  setPlaying(false);
                  setElapsedMs(value);
                }}
                onSpeed={setSpeed}
              />
            )}
          </section>
        </section>
      )}
    </main>
  );
}

function SummaryPanel({ detail, rescoreBusy, rescoreMessage, onRescore }: { detail: SessionResultDetail; rescoreBusy: boolean; rescoreMessage: string; onRescore: () => void }) {
  const canonicalDetail = detail as CanonicalSessionResultDetail;
  const stats = detail.statistics;
  const freshness = detail.scoring_freshness?.status ?? "unknown";
  const canRescore = canonicalDetail.rescore_allowed !== false && (freshness === "stale" || freshness === "unknown");
  const warnings = Array.isArray(canonicalDetail.warnings) ? canonicalDetail.warnings : [];
  return (
    <section className="session-result-card">
      <header>
        <h2>{detail.session_id}</h2>
        <span className={`session-score-status ${detail.scoring_status}`}>{statusLabel(detail.scoring_status)}</span>
      </header>
      <div className="session-result-summary">
        <Metric label="训练任务" value={taskNameForDetail(detail)} />
        <Metric label="动作 / 道具" value={`${motionLabel(detail.motion_family)} / ${objectLabel(detail.object_type)}`} />
        <Metric label="来源" value={`${detail.source_mode || "-"} / ${detail.source_origin || "-"}`} />
        <Metric label="评分配置" value={scoreProfileLabel(detail.score_profile)} />
        <Metric label="评分版本" value={freshnessLabel(freshness)} />
        <Metric label="评分有效性" value={canonicalDetail.valid === false ? "无效" : canonicalDetail.valid === true ? "有效" : "-"} />
        <Metric label="无效原因" value={canonicalDetail.invalid_reason || "-"} />
        <Metric label="数据质量" value={canonicalDetail.data_quality_level || "-"} />
        <Metric label="权威来源" value={canonicalDetail.scoring_authority === "score_service_dedicated_job" ? "Score Job dedicated artifact" : canonicalDetail.scoring_authority || "-"} />
        <Metric label="总动作次数" value={String(detail.repetition_count)} />
        <Metric label="有效动作次数" value={String(detail.valid_repetition_count)} />
        <Metric label="拒绝动作次数" value={String(detail.rejected_repetition_count)} />
        <Metric label="平均分" value={formatScore(stats.average_score)} />
        <Metric label="最高分" value={formatScore(stats.maximum_score)} />
        <Metric label="最低分" value={formatScore(stats.minimum_score)} />
      </div>
      {detail.task_registry_status === "unknown" && (
        <p className="session-result-error">任务未在 Task Registry 中登记：{detail.unsupported_task_reason || "UNKNOWN_TASK"}</p>
      )}
      {canRescore && (
        <div className="session-rescore-panel">
          <p>当前历史结果的评分版本为{freshnessLabel(freshness)}，可由用户手动使用当前评分版本重新评分。</p>
          <button type="button" onClick={onRescore} disabled={rescoreBusy || detail.scoring_status === "running" || detail.scoring_status === "pending"}>
            {rescoreBusy ? "提交中..." : "使用当前评分版本重新评分"}
          </button>
        </div>
      )}
      {rescoreMessage && <p className="session-rescore-message">{rescoreMessage}</p>}
      {detail.error && <p className="session-result-error">{detail.error}</p>}
      {warnings.length > 0 && (
        <section className="session-feedback-box">
          <h3>数据质量警告</h3>
          {warnings.map((item, index) => <p key={`${item}-${index}`}>{item}</p>)}
        </section>
      )}
      <div className="session-subscore-grid">
        {SUBSCORE_KEYS.map((key) => (
          <Metric key={key} label={SUBSCORE_LABELS[key]} value={formatScore(stats.subscore_averages[key])} />
        ))}
      </div>
    </section>
  );
}

function RepPicker({ repetitions, selectedRep, onSelect }: { repetitions: SessionResultRepetition[]; selectedRep: SessionResultRepetition | null; onSelect: (rep: SessionResultRepetition) => void }) {
  return (
    <section className="session-result-card">
      <header><h2>Rep 选择</h2><span>选择后在右侧查看摘要或曲线。</span></header>
      <div className="session-rep-picker-grid">
        {repetitions.map((rep) => (
          <button key={rep.rep_index} type="button" className={selectedRep?.rep_index === rep.rep_index ? "selected" : ""} onClick={() => onSelect(rep)}>
            <strong>Rep {rep.rep_index}</strong>
            <span>{rep.valid ? "有效" : "无效"}</span>
            <small>{formatDuration(rep.duration_ms)}</small>
            <b>{formatScore(rep.final_score)}</b>
          </button>
        ))}
      </div>
      {!repetitions.length && <EmptyState title="暂无 Rep" description="本次 Session 已完成评分，但没有可用的有效动作片段。" size="compact" />}
    </section>
  );
}

function SelectedRepScore({ rep }: { rep: SessionResultRepetition }) {
  return (
    <section className="session-rep-score-card">
      <div className="session-selected-score">
        <span>Rep {rep.rep_index}</span>
        <strong>{formatScore(rep.final_score)}</strong>
        <small>{rep.valid ? "有效动作" : `无效：${rep.invalid_reason || "未知原因"}`}</small>
      </div>
      <div className="session-rep-score-grid">
        {SUBSCORE_KEYS.map((key) => (
          <Metric key={key} label={SUBSCORE_LABELS[key]} value={formatScore(rep.subscores[key])} />
        ))}
        <Metric className={rep.compensation.detected ? "session-compensation-detected" : "session-compensation-none"} label="代偿标签" value={rep.compensation.detected ? compensationLabels(rep.compensation.types).join(" / ") : "未检测到明显代偿"} />
        <Metric className="session-invalid-reason" label="无效原因" value={rep.invalid_reason || "-"} />
      </div>
      <section className="session-feedback-box">
        <h3>反馈摘要</h3>
        {rep.feedback?.length ? rep.feedback.map((item, index) => <p key={`${item}-${index}`}>{item}</p>) : <p>-</p>}
      </section>
    </section>
  );
}

function SensorCardsPanel(props: {
  trajectory: SessionTrajectory | null;
  series: ReturnType<typeof trajectorySeries> | null;
  currentFrame: ReturnType<typeof frameAtElapsedMs>;
  playing: boolean;
  speed: (typeof PLAYBACK_SPEEDS)[number];
  elapsedMs: number;
  durationMs: number;
  onPlayToggle: () => void;
  onReset: () => void;
  onSeek: (value: number) => void;
  onSpeed: (value: (typeof PLAYBACK_SPEEDS)[number]) => void;
}) {
  const canPlay = Boolean(props.trajectory?.playback_available && props.series);
  return (
    <section className="session-sensor-panel">
      <header className="session-sensor-head">
        <div>
          <h3>传感器曲线</h3>
          <p>曲线来自当前 Rep 的真实 trajectory 文件；不连接实时 WebSocket，不生成插值点。</p>
        </div>
        <span>{props.trajectory?.playback_available ? `${props.trajectory.frame_count} 帧` : props.trajectory?.playback_error || "trajectory_unavailable"}</span>
      </header>
      <PlaybackControls
        playing={props.playing}
        speed={props.speed}
        elapsedMs={props.elapsedMs}
        durationMs={props.durationMs}
        canPlay={canPlay}
        onPlayToggle={props.onPlayToggle}
        onReset={props.onReset}
        onSeek={props.onSeek}
        onSpeed={props.onSpeed}
      />
      {props.series ? (
        <div className="session-sensor-card-grid">
          {props.series.imu.map((row) => (
            <SessionSensorCard key={row.label} row={row} unit="deg" currentValue={currentSeriesValue(row, props.currentFrame?.time_ms)} kind="imu" />
          ))}
          {props.series.emg.map((row) => (
            <SessionSensorCard key={row.label} row={row} unit={row.field} currentValue={currentSeriesValue(row, props.currentFrame?.time_ms)} kind="emg" />
          ))}
        </div>
      ) : (
        <EmptyState title="暂无传感器曲线" description={props.trajectory?.playback_error || "当前 Rep 没有可用 trajectory。"} size="compact" />
      )}
    </section>
  );
}

function PlaybackControls(props: {
  playing: boolean;
  speed: (typeof PLAYBACK_SPEEDS)[number];
  elapsedMs: number;
  durationMs: number;
  canPlay: boolean;
  onPlayToggle: () => void;
  onReset: () => void;
  onSeek: (value: number) => void;
  onSpeed: (value: (typeof PLAYBACK_SPEEDS)[number]) => void;
}) {
  return (
    <div className="session-sensor-controls">
      <button type="button" onClick={props.onPlayToggle} disabled={!props.canPlay}>
        {props.playing ? <Pause size={15} /> : <Play size={15} />}
        {props.playing ? "暂停" : "播放"}
      </button>
      <button type="button" onClick={props.onReset} disabled={!props.canPlay}>
        <RotateCcw size={15} />
        回到开头
      </button>
      <input
        type="range"
        min={0}
        max={Math.max(0, props.durationMs)}
        value={Math.min(props.elapsedMs, props.durationMs)}
        disabled={!props.canPlay}
        onChange={(event) => props.onSeek(Number(event.currentTarget.value))}
      />
      <span>{formatMs(props.elapsedMs)} / {formatMs(props.durationMs)}</span>
      <div className="session-speed-picker">
        {PLAYBACK_SPEEDS.map((value) => (
          <button key={value} type="button" className={props.speed === value ? "selected" : ""} onClick={() => props.onSpeed(value)}>
            {value}x
          </button>
        ))}
      </div>
    </div>
  );
}

function SessionSensorCard({ row, unit, currentValue, kind }: { row: SensorSeriesRow; unit: string; currentValue: number | null; kind: "imu" | "emg" }) {
  return (
    <article className={`sensor-card session-sensor-card ${kind === "imu" ? "imu-card" : "emg-card"}`}>
      <header>
        <div><span>{kind === "imu" ? "惯性传感器" : "表面肌电"}</span><h2>{row.label}</h2></div>
        <b>{row.points.length ? "历史播放" : "缺失"}</b>
      </header>
      <SensorTrendChart
        timestamps={row.points.map((point) => point.timeMs)}
        unit={unit}
        series={[{ key: row.field, label: row.field, color: kind === "imu" ? "#62aef0" : "#eb776f", values: row.points.map((point) => point.value) }]}
      />
      {!row.points.length && <p className="sensor-waiting">当前 Rep 缺少该通道数据</p>}
      <dl className="sensor-values">
        <Value label="当前值" value={formatNumber(currentValue)} />
        <Value label="字段" value={row.field} />
        <Value label="样本数" value={`已播放 ${row.points.length} / ${row.totalPoints}`} />
      </dl>
    </article>
  );
}

function currentSeriesValue(row: SensorSeriesRow, currentTimeMs?: number | null) {
  if (typeof currentTimeMs !== "number" || !Number.isFinite(currentTimeMs)) return null;
  let selected: number | null = null;
  for (const point of row.points) {
    if (point.timeMs > currentTimeMs) break;
    selected = point.value;
  }
  return selected;
}

function Metric({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return <div className={className}><span>{label}</span><strong>{value}</strong></div>;
}

function Value({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

function taskNameForDetail(detail: SessionResultDetail) {
  return detail.task_name || detail.original_task_name || "未知训练任务";
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

function formatScore(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "-";
}

function formatNumber(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(3) : "--";
}

function formatDuration(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? `${(value / 1000).toFixed(1)}s` : "-";
}

function formatMs(value: number | null | undefined) {
  if (!value || !Number.isFinite(value)) return "0.0s";
  return `${(value / 1000).toFixed(1)}s`;
}

function statusLabel(status: string) {
  if (status === "completed") return "已完成";
  if (status === "running") return "评分中";
  if (status === "pending") return "等待评分";
  if (status === "failed") return "失败";
  return "未开始";
}

function freshnessLabel(status: string) {
  if (status === "current") return "当前";
  if (status === "stale") return "已过期";
  return "未知";
}
