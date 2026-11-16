import { ArrowLeft, Pause, Play, Rotate3D, RotateCcw, View } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { SensorTrendChart } from "../components/SensorTrendChart";
import { DRINK_TASK_NAME, PATIENT_ID, formatRecordDateTime, showcaseSessions } from "../data/showcaseRecords";
import { fetchSessions, type SessionSummary } from "../services/patientApi";
import {
  fetchSessionResult,
  fetchSessionTrajectory,
  type SessionResultDetail,
  type SessionResultRepetition,
  type SessionTrajectory
} from "../services/sessionResultsApi";
import { AnatomyScene } from "../twin/AnatomyScene";
import {
  ensureActualPerfTrace,
  markActualPerf,
  measureActualPerfSync,
  startActualPerfTrace,
  updateActualPerfContext
} from "../twin/actualPerfTrace";
import { prewarmAnatomyModel } from "../twin/cachedAnatomyModel";
import { composeHybridDrinkPose } from "../twin/HybridDrinkPose";
import { composeHybridLateralTransferPose } from "../twin/HybridLateralTransferPose";
import {
  buildOfflinePlaybackPlan,
  frameToEmg,
  resolveFrameInPlanAtElapsedMs,
  resolvePlanAtElapsedMs,
  resolveSessionNeutralFrame,
  trajectorySeries,
  type OfflinePlaybackPlan,
  type OfflineRepPlan
} from "../twin/actualTrajectory";

export type ReconstructionView = "records" | "actual";
type ActualLoadState = "loading" | "ready" | "playing" | "paused" | "completed" | "unavailable" | "error";
type ActualPlaybackScope = "all" | "rep";
type MotionFamily = "drink_water" | "lateral_transfer";
type PresentationObjectType = "cup" | "ball";

interface ReconstructionRecord {
  session: SessionSummary;
  sessionId: string;
  publicLabel: string;
  taskCode: string;
  actionName: string;
  description: string;
  motionFamily: MotionFamily | null;
  objectType: PresentationObjectType | null;
  dateLabel: string;
  playable: boolean;
  unavailableReason?: string;
}

const DRINK_ACTION_NAME = "水杯拿取与饮水训练（二级）";
const DRINK_ACTION_DESCRIPTION = "前伸取杯、屈肘抬杯完成饮水、放回水杯并回到手臂自然下垂位。";
const LATERAL_ACTION_DESCRIPTION = "手臂自然下垂，伸手拿取目标物，前臂近水平向内拉，再向外送出放回，最后回到自然下垂位。";
const PLAYBACK_SPEEDS = [0.5, 1, 1.5, 2] as const;

export function ReconstructionPage() {
  const [params, setParams] = useSearchParams();
  const requestedSessionId = params.get("session_id") || "";
  const requestedTaskCode = normalizeTaskId(params.get("task_id") || requestedSessionId);
  const requestedView = params.get("view");
  const requestedRep = Number(params.get("rep") || "1");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loadMessage, setLoadMessage] = useState("");
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [view, setView] = useState<ReconstructionView>("records");
  const [sessionDetail, setSessionDetail] = useState<SessionResultDetail | null>(null);
  const [trajectories, setTrajectories] = useState<Map<number, SessionTrajectory>>(new Map());
  const [actualState, setActualState] = useState<ActualLoadState>("loading");
  const [actualMessage, setActualMessage] = useState("");
  const [actualScope, setActualScope] = useState<ActualPlaybackScope>("all");
  const [actualRepIndex, setActualRepIndex] = useState(1);
  const [actualElapsedMs, setActualElapsedMs] = useState(0);
  const [actualPlaying, setActualPlaying] = useState(false);
  const [actualSpeed, setActualSpeed] = useState<(typeof PLAYBACK_SPEEDS)[number]>(1);
  const actualSceneContainerRef = useRef<HTMLDivElement | null>(null);
  const sceneRef = useRef<AnatomyScene | null>(null);
  const actualRafRef = useRef<number | null>(null);
  const actualLastTickRef = useRef<number | null>(null);
  const calibratedPlanKeyRef = useRef("");

  useEffect(() => {
    fetchSessions(PATIENT_ID)
      .then((items) => {
        const realSessions = items.filter((item) => item.record_kind !== "showcase");
        setSessions(realSessions.length ? realSessions : showcaseSessions);
        setLoadMessage(realSessions.length ? "" : "暂无正式训练记录，当前显示演示记录。");
      })
      .catch(() => {
        setSessions(showcaseSessions);
        setLoadMessage("暂时无法读取正式训练记录，当前显示演示记录。");
      });
  }, []);

  const records = useMemo(() => makeReconstructionRecords(sessions), [sessions]);
  const selectedRecord = useMemo(
    () => records.find((record) => record.sessionId === selectedSessionId) ?? records[0],
    [records, selectedSessionId]
  );
  const selectedMotionFamily = selectedRecord?.motionFamily ?? null;
  const selectedObjectType = selectedRecord?.objectType ?? null;

  useEffect(() => {
    if (view !== "records" || !records.some((record) => record.playable)) return;
    const schedule = window as Window & {
      requestIdleCallback?: (callback: () => void) => number;
      cancelIdleCallback?: (handle: number) => void;
    };
    const start = () => {
      void prewarmAnatomyModel().catch((error) => {
        console.warn("[anatomy-model-cache] prewarm failed", error);
      });
    };
    if (schedule.requestIdleCallback) {
      const handle = schedule.requestIdleCallback(start);
      return () => schedule.cancelIdleCallback?.(handle);
    }
    const timeoutId = window.setTimeout(start, 0);
    return () => window.clearTimeout(timeoutId);
  }, [records, view]);

  useEffect(() => {
    if (!records.length) return;
    const requestedRecord =
      records.find((record) => record.sessionId === requestedSessionId) ??
      (requestedTaskCode ? records.find((record) => record.taskCode === requestedTaskCode) : undefined);
    const currentStillExists = records.some((record) => record.sessionId === selectedSessionId);
    if (!currentStillExists || !selectedSessionId) {
      const nextRecord = requestedRecord ?? records[0];
      setSelectedSessionId(nextRecord.sessionId);
    }
    if (requestedView === "actual" && requestedRecord?.playable) setView("actual");
    else if (requestedView === "fixed" && requestedRecord?.playable) {
      setParams(
        {
          session_id: requestedRecord.sessionId,
          view: "actual",
          ...(Number.isFinite(requestedRep) && requestedRep > 0 ? { rep: String(requestedRep) } : {})
        },
        { replace: true }
      );
      setView("actual");
    } else if ((requestedView === "actual" || requestedView === "fixed") && requestedRecord) {
      setParams({ session_id: requestedRecord.sessionId }, { replace: true });
      setView("records");
    }
  }, [records, requestedSessionId, requestedTaskCode, requestedView, selectedSessionId]);

  const offlinePlan = useMemo(() => {
    if (!sessionDetail) return null;
    return buildOfflinePlaybackPlan(sessionDetail, trajectories);
  }, [sessionDetail, trajectories]);

  const activeOfflinePlan = useMemo(() => {
    if (!offlinePlan) return null;
    if (actualScope === "all") return offlinePlan;
    const plan = offlinePlan.plans.find((item) => item.rep.rep_index === actualRepIndex);
    return plan ? singleRepPlaybackPlan(offlinePlan.sessionId, plan) : null;
  }, [actualRepIndex, actualScope, offlinePlan]);

  const sessionNeutralFrame = offlinePlan ? resolveSessionNeutralFrame(offlinePlan) : null;
  const activeRepPlan = activeOfflinePlan ? resolvePlanAtElapsedMs(activeOfflinePlan, actualElapsedMs) : null;
  const activeFrame = activeRepPlan ? resolveFrameInPlanAtElapsedMs(activeRepPlan, actualElapsedMs) : null;
  const activeRelativeMs = activeRepPlan ? Math.max(0, actualElapsedMs - activeRepPlan.startMs) : 0;
  const activeSeries = activeRepPlan?.trajectory ? trajectorySeries(activeRepPlan.trajectory, activeRelativeMs) : null;
  const activeNormalizedPhase = activeRepPlan?.timeline.durationMs
    ? Math.max(0, Math.min(1, activeRelativeMs / activeRepPlan.timeline.durationMs))
    : 0;
  const activeHybridPose = activeFrame && selectedMotionFamily
    ? selectedMotionFamily === "lateral_transfer"
      ? composeHybridLateralTransferPose(activeFrame, activeNormalizedPhase, sessionNeutralFrame)
      : composeHybridDrinkPose(activeFrame, activeNormalizedPhase, sessionNeutralFrame)
    : null;

  useEffect(() => {
    if (
      view !== "actual" ||
      !actualSceneContainerRef.current ||
      !selectedMotionFamily ||
      !selectedObjectType
    ) return;
    ensureActualPerfTrace({
      sessionId: selectedRecord?.sessionId,
      scoreProfile: selectedRecord?.session.score_profile,
      objectType: selectedObjectType
    });
    markActualPerf("AnatomyScene_construct_start");
    const scene = measureActualPerfSync(
      "scene_construct_ms",
      () => new AnatomyScene(actualSceneContainerRef.current as HTMLDivElement, emptySceneCallbacks())
    );
    markActualPerf("AnatomyScene_construct_end");
    scene.stopExistingArmMotion();
    scene.setTrajectoryVisible(false);
    scene.setPresentationObjectMode(selectedObjectType);
    scene.setPresentationCupVisible(true);
    scene.clearExternalEmg();
    scene.clearMeasuredTrunkLean();
    sceneRef.current = scene;
    return () => {
      scene.stopExistingArmMotion();
      scene.clearExternalEmg();
      scene.clearMeasuredTrunkLean();
      scene.dispose();
      sceneRef.current = null;
    };
  }, [selectedRecord?.session.score_profile, selectedRecord?.sessionId, view, selectedMotionFamily, selectedObjectType]);

  useEffect(() => {
    if (
      view !== "actual" ||
      !selectedRecord?.sessionId ||
      !selectedRecord.playable ||
      !selectedMotionFamily ||
      !selectedObjectType
    ) return;
    let cancelled = false;
    const sessionId = selectedRecord.sessionId;
    updateActualPerfContext({
      sessionId,
      scoreProfile: selectedRecord.session.score_profile,
      objectType: selectedObjectType
    });
    setSessionDetail(null);
    setTrajectories(new Map());
    setActualState("loading");
    setActualMessage("正在读取Session详情。");
    resetActualPlayback("rep", 1);
    fetchSessionResult(sessionId)
      .then((detail) => {
        if (cancelled) return;
        setSessionDetail(detail);
        const validReps = detail.repetitions.filter((rep) => rep.valid).sort((a, b) => a.rep_index - b.rep_index);
        if (!validReps.length) {
          setActualState("unavailable");
          setActualMessage("该Session没有可用于动作还原的有效动作轨迹。");
          return;
        }
        const firstRep = chooseInitialRep(validReps, requestedRep);
        resetActualPlayback("rep", firstRep);
        setActualState("loading");
        setActualMessage(`正在加载第${firstRep}次动作数据。`);
        fetchSessionTrajectory(sessionId, firstRep)
          .then((trajectory) => {
            if (cancelled) return;
            setTrajectories((current) => new Map(current).set(firstRep, trajectory));
            if (!trajectory.playback_available) {
              setActualState("error");
              setActualMessage(trajectory.playback_error || `${sessionId} rep ${firstRep}: trajectory_unavailable`);
              return;
            }
            setActualMessage("");
            markActualPerf("first_rep_trajectory_ready");
            markActualPerf("actual_ready_timestamp");
            setActualState("ready");
          })
          .catch(() => {
            if (cancelled) return;
            setActualState("error");
            setActualMessage(`${sessionId} rep ${firstRep}: trajectory_not_found`);
          })
          .finally(() => {
            if (cancelled) return;
            validReps
              .filter((rep) => rep.rep_index !== firstRep)
              .forEach((rep) => {
                fetchSessionTrajectory(sessionId, rep.rep_index)
                  .then((trajectory) => {
                    if (cancelled) return;
                    setTrajectories((current) => new Map(current).set(rep.rep_index, trajectory));
                  })
                  .catch(() => {
                    // Background prefetch must not fail the visible first repetition.
                  });
              });
          });
      })
      .catch(() => {
        if (cancelled) return;
        setActualState("error");
        setActualMessage(`无法读取Session详情：${sessionId}`);
      });
    return () => {
      cancelled = true;
    };
  }, [requestedRep, selectedMotionFamily, selectedObjectType, selectedRecord?.playable, selectedRecord?.sessionId, view]);

  const selectedRepTrajectoryLoaded = trajectories.has(actualRepIndex);

  useEffect(() => {
    if (
      view !== "actual" ||
      actualScope !== "rep" ||
      !sessionDetail ||
      !selectedRecord?.sessionId
    ) return;
    const validReps = sessionDetail.repetitions.filter((rep) => rep.valid);
    if (!validReps.some((rep) => rep.rep_index === actualRepIndex)) return;
    if (selectedRepTrajectoryLoaded) {
      const trajectory = trajectories.get(actualRepIndex);
      if (trajectory && !trajectory.playback_available && actualState === "loading") {
        setActualState("error");
        setActualMessage(trajectory.playback_error || `${selectedRecord.sessionId} rep ${actualRepIndex}: trajectory_unavailable`);
        return;
      }
      if (actualState === "loading") {
        setActualMessage("");
        setActualState("ready");
      }
      return;
    }
    let cancelled = false;
    const sessionId = selectedRecord.sessionId;
    setActualPlaying(false);
    setActualState("loading");
    setActualMessage(`正在加载第${actualRepIndex}次动作数据。`);
    fetchSessionTrajectory(sessionId, actualRepIndex)
      .then((trajectory) => {
        if (cancelled) return;
        setTrajectories((current) => new Map(current).set(actualRepIndex, trajectory));
        if (!trajectory.playback_available) {
          setActualState("error");
          setActualMessage(trajectory.playback_error || `${sessionId} rep ${actualRepIndex}: trajectory_unavailable`);
          return;
        }
        setActualMessage("");
        markActualPerf("actual_ready_timestamp");
        setActualState("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setActualState("error");
        setActualMessage(`${sessionId} rep ${actualRepIndex}: trajectory_not_found`);
      });
    return () => {
      cancelled = true;
    };
  }, [actualRepIndex, actualScope, selectedRecord?.sessionId, selectedRepTrajectoryLoaded, sessionDetail, view]);

  useEffect(() => {
    if (actualScope !== "all" || !activeOfflinePlan || activeOfflinePlan.totalDurationMs <= 0) return;
    if (actualState === "loading") {
      setActualMessage("");
      setActualState("ready");
    }
  }, [activeOfflinePlan, actualScope, actualState]);

  useEffect(() => {
    if (!actualPlaying || !activeOfflinePlan || activeOfflinePlan.totalDurationMs <= 0) return;
    const tick = (now: number) => {
      const previous = actualLastTickRef.current ?? now;
      actualLastTickRef.current = now;
      setActualElapsedMs((value) => {
        const next = value + (now - previous) * actualSpeed;
        if (next >= activeOfflinePlan.totalDurationMs) {
          setActualPlaying(false);
          setActualState("completed");
          return activeOfflinePlan.totalDurationMs;
        }
        setActualState("playing");
        return next;
      });
      actualRafRef.current = window.requestAnimationFrame(tick);
    };
    actualRafRef.current = window.requestAnimationFrame(tick);
    return () => stopActualTimer();
  }, [actualPlaying, actualSpeed, activeOfflinePlan]);

  useEffect(() => {
    const scene = sceneRef.current;
    if (view !== "actual" || !scene || !selectedMotionFamily || !selectedObjectType || !activeOfflinePlan || !activeRepPlan || !activeFrame || !activeHybridPose) return;
    const planKey = activeOfflinePlan.sessionId;
    if (calibratedPlanKeyRef.current !== planKey) {
      scene.stopExistingArmMotion();
      scene.clearExternalEmg();
      scene.clearMeasuredTrunkLean();
      scene.setPresentationObjectMode(selectedObjectType);
      scene.setPresentationCupVisible(true);
      calibratedPlanKeyRef.current = planKey;
    }
    if (selectedMotionFamily === "lateral_transfer") {
      scene.applyHybridLateralTransferPose(activeHybridPose as ReturnType<typeof composeHybridLateralTransferPose>, frameToEmg(activeFrame, activeRepPlan.emgScale), activeRepPlan.lfDetected);
    } else {
      scene.applyHybridDrinkPose(activeHybridPose as ReturnType<typeof composeHybridDrinkPose>, frameToEmg(activeFrame, activeRepPlan.emgScale), activeRepPlan.lfDetected);
    }
  }, [activeFrame, activeHybridPose, activeOfflinePlan, activeRepPlan, selectedMotionFamily, selectedObjectType, view]);

  function selectRecord(record: ReconstructionRecord) {
    setSelectedSessionId(record.sessionId);
    setParams({ session_id: record.sessionId }, { replace: true });
  }

  function playSelectedRecord(record: ReconstructionRecord) {
    if (!record.playable) return;
    startActualPerfTrace({
      sessionId: record.sessionId,
      scoreProfile: record.session.score_profile,
      objectType: record.objectType
    });
    setSelectedSessionId(record.sessionId);
    setParams({ session_id: record.sessionId, view: "actual" }, { replace: true });
    setView("actual");
  }

  function returnToRecords() {
    sceneRef.current?.stopExistingArmMotion();
    sceneRef.current?.clearExternalEmg();
    sceneRef.current?.clearMeasuredTrunkLean();
    resetActualPlayback("all", 1);
    setView("records");
    setParams({}, { replace: true });
  }

  function resetActualPlayback(scope: ActualPlaybackScope = actualScope, repIndex = actualRepIndex) {
    stopActualTimer();
    setActualPlaying(false);
    setActualScope(scope);
    setActualRepIndex(repIndex);
    setActualElapsedMs(0);
    calibratedPlanKeyRef.current = "";
    sceneRef.current?.stopExistingArmMotion();
    sceneRef.current?.clearExternalEmg();
    sceneRef.current?.clearMeasuredTrunkLean();
  }

  function startActualPlayback(scope: ActualPlaybackScope, repIndex = actualRepIndex) {
    resetActualPlayback(scope, repIndex);
    setActualState("playing");
    setActualPlaying(true);
    if (scope === "rep" && selectedRecord?.sessionId) {
      setParams({ session_id: selectedRecord.sessionId, view: "actual", rep: String(repIndex) }, { replace: true });
    }
  }

  function toggleActualPlayback() {
    if (!activeOfflinePlan || activeOfflinePlan.totalDurationMs <= 0) return;
    setActualPlaying((value) => {
      const next = !value;
      setActualState(next ? "playing" : "paused");
      return next;
    });
  }

  function selectActualRep(repIndex: number) {
    if (!selectedRecord?.sessionId) return;
    resetActualPlayback("rep", repIndex);
    setParams({ session_id: selectedRecord.sessionId, view: "actual", rep: String(repIndex) }, { replace: true });
    const trajectory = trajectories.get(repIndex);
    if (trajectory?.playback_available) {
      setActualMessage("");
      setActualState("ready");
    } else if (trajectory) {
      setActualState("error");
      setActualMessage(trajectory.playback_error || `${selectedRecord.sessionId} rep ${repIndex}: trajectory_unavailable`);
    } else {
      setActualState("loading");
      setActualMessage(`正在加载第${repIndex}次动作数据。`);
    }
  }

  function seekActual(value: number) {
    setActualPlaying(false);
    setActualState("paused");
    setActualElapsedMs(Math.max(0, Math.min(value, activeOfflinePlan?.totalDurationMs ?? 0)));
  }

  function stopActualTimer() {
    if (actualRafRef.current !== null) window.cancelAnimationFrame(actualRafRef.current);
    actualRafRef.current = null;
    actualLastTickRef.current = null;
  }

  return (
    <main className="workspace-page reconstruction-page">
      <header className="page-title">
        <div>
          <h1>动作还原</h1>
          <span>{view === "records" ? "选择一条训练记录" : "基于训练CSV的患者动作还原"}</span>
        </div>
      </header>

      {view === "records" && (
        <section className="reconstruction-records">
          <section className="reconstruction-record-list">
            <header><h2>训练记录</h2><span>{records.length} 条记录</span></header>
            <div>
              {records.map((record) => (
                <button key={record.sessionId} className={selectedRecord?.sessionId === record.sessionId ? "selected" : ""} onClick={() => selectRecord(record)}>
                  <strong>{record.publicLabel}</strong>
                  <span>{record.actionName}</span>
                  <small>{record.dateLabel}</small>
                  <small>{record.playable ? `${historyRepLabel(record.session)} / ${objectLabel(record.objectType)}` : "任务路由不可用"}</small>
                  <small>{record.playable ? "可播放" : record.unavailableReason}</small>
                </button>
              ))}
              {!records.length && <EmptyState title="暂无训练记录" size="compact" />}
            </div>
          </section>
          <section className="reconstruction-selection">
            {selectedRecord ? (
              <>
                <Rotate3D size={30} />
                <h2>{selectedRecord.publicLabel}</h2>
                <dl>
                  <div className="reconstruction-detail-row"><dt>记录</dt><dd>{selectedRecord.publicLabel}</dd></div>
                  <div className="reconstruction-detail-row reconstruction-detail-action"><dt>训练任务</dt><dd>{selectedRecord.actionName}</dd></div>
                  <div className="reconstruction-detail-row"><dt>日期</dt><dd>{selectedRecord.dateLabel}</dd></div>
                  <div className="reconstruction-detail-row"><dt>动作数据</dt><dd>{historyRepLabel(selectedRecord.session)}</dd></div>
                  <div className="reconstruction-detail-row"><dt>Session ID</dt><dd>{selectedRecord.sessionId}</dd></div>
                  <div className="reconstruction-detail-row reconstruction-detail-workflow"><dt>动作流程</dt><dd>{selectedRecord.description}</dd></div>
                </dl>
                <button className="primary-page-action" onClick={() => playSelectedRecord(selectedRecord)} disabled={!selectedRecord.playable}><Play size={17} />动作还原</button>
                {!selectedRecord.playable && <p className="form-message">{selectedRecord.unavailableReason}</p>}
                {loadMessage && <p className="form-message">{loadMessage}</p>}
              </>
            ) : (
              <EmptyState title="暂无训练记录" description={loadMessage || "暂无可用记录。"} />
            )}
          </section>
        </section>
      )}

      {view === "actual" && selectedRecord && (
        <section className="reconstruction-playback split-reconstruction-playback actual-reconstruction-view">
          <section className="twin-stage reconstruction-model-stage">
            <div className="stage-canvas" ref={actualSceneContainerRef} />
            {actualState !== "playing" && <div className="actual-reconstruction-message"><strong>{actualStatusLabel(actualState)}</strong><span>{actualMessage || selectedSessionUnavailableMessage(activeOfflinePlan)}</span></div>}
          </section>
          <aside className="reconstruction-control-panel actual-reconstruction-panel">
            <button className="back-button" onClick={returnToRecords}><ArrowLeft size={16} />返回训练记录</button>
            <h2>患者动作还原</h2>
            <p>{selectedMotionFamily === "lateral_transfer" ? `动作基础运动学来自横向取放标准示范；动作次数、持续时间、传感器信息、躯干代偿和肌电强度来自所选训练Session保存的CSV。${objectLabel(selectedObjectType)}位置仅用于动作可视化，不代表CSV测得的${objectLabel(selectedObjectType)}绝对坐标。` : "动作基础运动学来自标准喝水示范；动作次数、持续时间、传感器信息、躯干代偿和肌电强度来自所选训练Session保存的CSV。水杯位置仅用于动作可视化，不代表CSV测得的水杯绝对坐标。"}</p>
            <dl>
              <div><dt>Session ID</dt><dd>{selectedRecord.sessionId}</dd></div>
              <div><dt>数据来源</dt><dd>已保存训练CSV</dd></div>
              <div><dt>有效动作次数</dt><dd>{offlinePlan ? offlinePlan.validRepCount : "-"}</dd></div>
              <div><dt>播放范围</dt><dd>{actualScope === "all" ? "全部动作" : `第${actualRepIndex}次`}</dd></div>
              <div><dt>当前动作</dt><dd>{activeRepPlan ? `第${activeRepPlan.rep.rep_index}次` : "-"}</dd></div>
              <div><dt>代偿提示</dt><dd>{activeRepPlan ? compensationLabel(activeRepPlan) : "-"}</dd></div>
              <div><dt>躯干前倾角</dt><dd>{activeHybridPose?.trunkLeanDeg === null || activeHybridPose?.trunkLeanDeg === undefined ? "-" : `${activeHybridPose.trunkLeanDeg.toFixed(1)}°`}</dd></div>
              <div><dt>时间</dt><dd>{formatMs(actualElapsedMs)} / {formatMs(activeOfflinePlan?.totalDurationMs ?? 0)}</dd></div>
              <div><dt>倍速</dt><dd>{actualSpeed.toFixed(1)}x</dd></div>
            </dl>
            {offlinePlan && offlinePlan.validRepCount === 0 && <p className="form-message">没有可用的动作还原轨迹。</p>}
            {actualState === "error" && <p className="session-fusion-warning">{actualMessage}</p>}
            {activeRepPlan?.lfDetected && <p className="session-fusion-warning">检测到躯干前倾代偿</p>}
            {activeRepPlan?.compensationLabels.includes("手部抖动") && <p className="session-fusion-warning">检测到手部抖动代偿</p>}
            <div className="playback-button-row">
              <button onClick={() => startActualPlayback("all")} disabled={!canPlayActual(activeOfflinePlan)}>播放全部</button>
              <button onClick={toggleActualPlayback} disabled={!canPlayActual(activeOfflinePlan)}>{actualPlaying ? <Pause size={16} /> : <Play size={16} />}{actualPlaying ? "暂停" : "播放"}</button>
              <button onClick={() => resetActualPlayback(actualScope, actualRepIndex)} disabled={!canPlayActual(activeOfflinePlan)}><RotateCcw size={16} />重新开始</button>
              <button onClick={() => sceneRef.current?.resetCamera()}><View size={16} />重置视角</button>
            </div>
            <input className="offline-reconstruction-progress" type="range" min={0} max={Math.max(0, activeOfflinePlan?.totalDurationMs ?? 0)} value={Math.min(actualElapsedMs, activeOfflinePlan?.totalDurationMs ?? 0)} disabled={!canPlayActual(activeOfflinePlan)} onChange={(event) => seekActual(Number(event.currentTarget.value))} />
            <div className="speed-picker">{PLAYBACK_SPEEDS.map((value) => <button key={value} className={actualSpeed === value ? "selected" : ""} onClick={() => setActualSpeed(value)}>{value.toFixed(1)}x</button>)}</div>
            <div className="repetition-picker">{offlinePlan?.plans.map((plan) => <button key={plan.rep.rep_index} onClick={() => selectActualRep(plan.rep.rep_index)} className={actualScope === "rep" && actualRepIndex === plan.rep.rep_index ? "selected" : ""}>第{plan.rep.rep_index}次</button>)}</div>
            <OfflineSensorSnapshot plan={activeRepPlan} series={activeSeries} frameTimeMs={activeFrame?.time_ms ?? null} />
          </aside>
        </section>
      )}
    </main>
  );
}

function OfflineSensorSnapshot({ plan, series, frameTimeMs }: { plan: OfflineRepPlan | null; series: ReturnType<typeof trajectorySeries> | null; frameTimeMs: number | null }) {
  if (!plan?.trajectory || !series) return <EmptyState title="暂无动作还原数据" description={plan?.timeline.playbackError || "trajectory_not_loaded"} size="compact" />;
  return (
    <section className="actual-sensor-status-grid offline-reconstruction-sensors">
      <h3>离线 IMU / EMG</h3>
      {[...series.imu, ...series.emg].map((row) => <div key={row.label}><strong>{row.label}</strong><span>{row.field}</span><small>已播放 {row.points.length} / {row.totalPoints}</small></div>)}
      <SensorTrendChart timestamps={series.imu[1]?.points.map((point) => point.timeMs) ?? []} unit="deg" series={[{ key: "imu2_pitch", label: "IMU2 躯干 pitch", color: "#7c3aed", values: series.imu[1]?.points.map((point) => point.value) ?? [] }]} />
      <small>当前真实帧时间：{frameTimeMs === null ? "-" : `${frameTimeMs} ms`}</small>
    </section>
  );
}

function makeReconstructionRecords(sessions: SessionSummary[]): ReconstructionRecord[] {
  const sorted = [...sessions].sort((a, b) => sessionTime(b) - sessionTime(a));
  const chronological = [...sorted].sort((a, b) => sessionTime(a) - sessionTime(b));
  const labels = new Map<string, string>();
  chronological.forEach((session, index) => labels.set(session.session_id, `训练记录 ${String(index + 1).padStart(2, "0")}`));
  return sorted.map((session) => {
    const motionFamily = motionFamilyForSession(session);
    const objectType = objectTypeForSession(session, motionFamily);
    const routeReady = motionFamily !== null && objectType !== null;
    return {
      session,
      sessionId: session.session_id,
      publicLabel: labels.get(session.session_id) ?? "训练记录",
      taskCode: taskCodeForSession(session),
      actionName: displayTaskNameForSession(session, motionFamily),
      description: routeReady
        ? motionFamily === "lateral_transfer"
          ? LATERAL_ACTION_DESCRIPTION
          : DRINK_ACTION_DESCRIPTION
        : "该训练任务未通过Task Registry解析，暂不提供动作还原。",
      motionFamily,
      objectType,
      dateLabel: formatRecordDateTime(session.started_at_ms),
      playable: Boolean(session.session_id && routeReady),
      unavailableReason: !session.session_id
        ? "请先选择训练记录"
        : routeReady
          ? undefined
          : session.unsupported_task_reason || "UNKNOWN_TASK"
    };
  });
}

function singleRepPlaybackPlan(sessionId: string, plan: OfflineRepPlan): OfflinePlaybackPlan {
  return { sessionId, repCount: 1, validRepCount: 1, plans: [{ ...plan, startMs: 0, endMs: plan.timeline.durationMs }], totalDurationMs: plan.timeline.durationMs, unavailableReasons: plan.timeline.playbackAvailable ? [] : [plan.timeline.playbackError || "trajectory_unavailable"] };
}

function chooseInitialRep(validReps: SessionResultRepetition[], requestedRep: number) {
  if (Number.isFinite(requestedRep) && validReps.some((rep) => rep.rep_index === requestedRep)) {
    return requestedRep;
  }
  return validReps[0]?.rep_index ?? 1;
}

function canPlayActual(plan: OfflinePlaybackPlan | null) {
  return Boolean(plan && plan.totalDurationMs > 0 && plan.plans.every((item) => item.timeline.playbackAvailable));
}

function selectedSessionUnavailableMessage(plan: OfflinePlaybackPlan | null) {
  if (!plan) return "正在加载所选Session的离线动作还原数据。";
  if (!plan.plans.length) return "该Session没有有效动作还原分段。";
  if (plan.unavailableReasons.length) return plan.unavailableReasons[0];
  return "离线动作还原已就绪。";
}

function compensationLabel(plan: OfflineRepPlan) {
  if (!plan.rep.compensation?.detected) return "未检测到明显代偿";
  return plan.compensationLabels.length ? plan.compensationLabels.join(" / ") : "检测到代偿";
}

function actualStatusLabel(status: ActualLoadState) {
  if (status === "loading") return "加载中";
  if (status === "ready") return "已就绪";
  if (status === "paused") return "已暂停";
  if (status === "completed") return "已完成";
  if (status === "unavailable") return "不可用";
  if (status === "error") return "加载失败";
  return "播放中";
}
function taskCodeForSession(session: SessionSummary) {
  const candidates = [session.session_id, session.scenario_id, session.task];
  const normalized = candidates.map(normalizeTaskId).find(Boolean);
  if (normalized) return normalized;
  const showcaseMatch = session.session_id.match(/SHOWCASE_DRINK_0*(\d+)/i);
  if (showcaseMatch) return `TASK${showcaseMatch[1].padStart(2, "0")}`;
  return "";
}

function normalizeTaskId(value: string | null | undefined) {
  if (!value) return null;
  const match = value.match(/TASK0*(\d+)/i);
  if (!match) return null;
  return `TASK${match[1].padStart(2, "0")}`;
}

function actionNameForSession(session: SessionSummary) {
  return displayTaskNameForSession(session, motionFamilyForSession(session));
}

function motionFamilyForSession(session: SessionSummary): MotionFamily | null {
  if (session.task_registry_status === "unknown") return null;
  const resolvedFamily =
    session.twin_profile === "lateral_transfer" || session.motion_family === "lateral_transfer"
      ? "lateral_transfer"
      : session.twin_profile === "drink_water" || session.motion_family === "drink_water"
        ? "drink_water"
        : null;
  if (session.task_registry_status === "resolved") return resolvedFamily;
  if (resolvedFamily) return resolvedFamily;
  const taskCode = taskCodeForSession(session);
  const numeric = Number(taskCode.match(/TASK(\d+)/)?.[1] ?? NaN);
  const text = [session.session_id, session.scenario_id, session.task, session.task_name, session.internal_score_task_name].join(" ").toLowerCase();
  if ((Number.isFinite(numeric) && numeric >= 24 && numeric <= 58) || /lateral|volleyball|横向|排球|取放/.test(text)) {
    return "lateral_transfer";
  }
  if (/drink|喝水|饮水|水杯/.test(text)) return "drink_water";
  return null;
}

function objectTypeForSession(session: SessionSummary, family: MotionFamily | null): PresentationObjectType | null {
  if (session.task_registry_status === "unknown") return null;
  if (session.object_type === "cup" || session.object_type === "ball") return session.object_type;
  if (session.task_registry_status === "resolved") return null;
  const text = [session.task_name, session.task, session.internal_score_task_name].join(" ").toLowerCase();
  if (text.includes("球") || text.includes("volleyball")) return "ball";
  if (text.includes("杯") || text.includes("水")) return "cup";
  if (family === "drink_water") return "cup";
  if (family === "lateral_transfer") return "ball";
  return null;
}

function displayTaskNameForSession(session: SessionSummary, family: MotionFamily | null) {
  const name = session.task_name || session.original_task_name || session.task;
  if (name) return name;
  if (family === "lateral_transfer") return "横向取放训练";
  if (family === "drink_water") return DRINK_ACTION_NAME;
  return "不支持的训练任务";
}

function objectLabel(value: PresentationObjectType | null) {
  if (value === null) return "对象未解析";
  return value === "ball" ? "球体" : "水杯";
}

function historyRepLabel(session: SessionSummary) {
  const count = session.valid_repetition_count ?? session.repetition_count;
  return typeof count === "number" && Number.isFinite(count)
    ? `患者动作 ${count} 次`
    : "患者实际动作";
}
function sessionTime(session: SessionSummary) {
  return session.started_at_ms ?? session.ended_at_ms ?? 0;
}

function formatMs(value: number | null | undefined) {
  if (!value || !Number.isFinite(value)) return "0.0s";
  return `${(value / 1000).toFixed(1)}s`;
}

function emptySceneCallbacks() {
  return { onLoadStatus: () => undefined, onSelected: () => undefined, onRigMode: () => undefined };
}

