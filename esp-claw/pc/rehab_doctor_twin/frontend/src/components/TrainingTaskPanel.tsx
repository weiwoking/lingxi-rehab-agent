import type { RehabSnapshot } from "../contracts/realtimeMessages";

const t = {
  task: "\u8bad\u7ec3\u4efb\u52a1",
  taskStatus: "\u4efb\u52a1\u72b6\u6001",
  difficulty: "\u96be\u5ea6",
  targetReps: "\u76ee\u6807\u6b21\u6570",
  completedReps: "\u5b8c\u6210\u6b21\u6570",
  currentPhase: "\u5f53\u524d\u9636\u6bb5",
  nextPhase: "\u4e0b\u4e00\u9636\u6bb5\u53c2\u8003",
  drinkCup: "\u62ff\u676f\u559d\u6c34",
  mediumLow: "\u4e2d\u4f4e",
  prepare: "\u51c6\u5907",
  reach: "\u4f38\u624b",
  prescription: "\u5750\u4f4d\uff0c\u5de6\u4fa7\u4e0a\u80a2\u4e3b\u52a8\u8f85\u52a9\u8bad\u7ec3\uff0c\u6ce8\u610f\u8eaf\u5e72\u7a33\u5b9a\u3002",
  shoulder: "\u80a9",
  elbow: "\u8098",
  wrist: "\u8155"
};

export function TrainingTaskPanel({ snapshot }: { snapshot: RehabSnapshot | null }) {
  const task = snapshot?.task;
  const progress = task ? Math.round((task.completed_reps / task.target_reps) * 100) : 0;
  return <section className="panel task-panel"><header><span>{t.task}</span><strong>{task?.name ?? t.drinkCup}</strong><span>{task?.task_id ?? "DRINK_CUP"}</span></header><div className="progress"><i style={{ width: `${progress}%` }} /></div><div className="kv-grid"><label>{t.taskStatus}</label><strong>{statusLabel(snapshot?.status)}</strong><label>{t.difficulty}</label><strong>{task?.difficulty ?? t.mediumLow}</strong><label>{t.targetReps}</label><strong>{task?.target_reps ?? 5}</strong><label>{t.completedReps}</label><strong>{task?.completed_reps ?? 0}</strong><label>{t.currentPhase}</label><strong>{snapshot?.phase ?? t.prepare}</strong><label>{t.nextPhase}</label><strong>{snapshot?.next_phase ?? t.reach}</strong></div><p className="prescription">{task?.prescription ?? t.prescription}</p><div className="target-angles"><span>{t.shoulder} {task?.target_angles.shoulder_flexion_deg ?? 70}{"\u00b0"}</span><span>{t.elbow} {task?.target_angles.elbow_flexion_deg ?? 105}{"\u00b0"}</span><span>{t.wrist} {task?.target_angles.wrist_flexion_deg ?? 10}{"\u00b0"}</span></div></section>;
}

function statusLabel(status: string | undefined) {
  if (status === "RUNNING") return "训练进行中";
  if (status === "UPLOADING") return "数据上传中";
  if (status === "SCORING") return "智能分析中";
  if (status === "REPORT_READY") return "评估已完成";
  return "准备就绪";
}
