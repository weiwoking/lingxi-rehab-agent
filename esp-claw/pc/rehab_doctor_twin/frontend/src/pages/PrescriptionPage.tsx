import { Eye, EyeOff, Stethoscope } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  fetchTaskLibrary,
  updatePatientTask,
  type PatientTask,
  type SupportedTask
} from "../services/taskLibraryApi";

export function PrescriptionPage() {
  const [tasks, setTasks] = useState<PatientTask[]>([]);
  const [supportedRegistry, setSupportedRegistry] = useState<SupportedTask[]>([]);
  const [message, setMessage] = useState("");
  const visibleCount = useMemo(() => tasks.filter((task) => task.enabled).length, [tasks]);

  useEffect(() => {
    fetchTaskLibrary()
      .then((value) => {
        setTasks(value.tasks);
        setSupportedRegistry(value.supported_registry);
        setMessage("");
      })
      .catch((error) => {
        setTasks([]);
        setSupportedRegistry([]);
        setMessage(error instanceof Error ? error.message : "任务库读取失败");
      });
  }, []);

  async function toggleTask(task: PatientTask) {
    setMessage("");
    try {
      const updated = await updatePatientTask(task.task_id, { enabled: !task.enabled });
      setTasks((current) => current.map((item) => item.task_id === updated.task_id ? updated : item));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "任务状态更新失败");
    }
  }

  async function activateTask(task: PatientTask) {
    if (!task.training_eligible || task.active) return;
    setMessage("");
    try {
      const updated = await updatePatientTask(task.task_id, { active: true });
      setTasks((current) => current.map((item) => (
        item.task_id === updated.task_id ? updated : { ...item, active: false }
      )));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "活动任务切换失败");
    }
  }

  return (
    <main className="workspace-page prescription-page">
      <header className="page-title">
        <div>
          <h1>任务库 / 医嘱管理</h1>
          <span>任务来源于正式 Task Registry；等级只作为处方和展示元数据，不改变评分算法。</span>
        </div>
      </header>

      {message && <p className="form-message">{message}</p>}

      <section className="task-library">
        <header>
          <div>
            <h2>患者任务库</h2>
            <span>共 {tasks.length} 项，当前启用 {visibleCount} 项。Registry 支持任务 {supportedRegistry.length} 项。</span>
          </div>
          <Stethoscope size={22} />
        </header>

        {tasks.map((task) => (
          <article key={task.task_id}>
            <div>
              <strong>{task.display_name}</strong>
              <span>{task.description || "暂无任务说明。"}</span>
              <small>{motionLabel(task.motion_family)} / {objectLabel(task.object_type)} / {task.level ? `${task.level}级` : "等级未标注"}</small>
            </div>
            <div className="task-library-status">
              <span>{task.enabled ? "启用中" : "已停用"}</span>
              {task.active && <span>当前任务</span>}
            </div>
            <div className="task-library-actions">
              <button type="button" onClick={() => toggleTask(task)}>
                {task.enabled ? <EyeOff size={16} /> : <Eye size={16} />}
                {task.enabled ? "停用" : "启用"}
              </button>
              <button type="button" disabled={!task.training_eligible || task.active} onClick={() => activateTask(task)}>
                设为当前
              </button>
            </div>
          </article>
        ))}
      </section>
    </main>
  );
}

function motionLabel(value?: string) {
  if (value === "drink_water") return "喝水动作";
  if (value === "lateral_transfer") return "横向取放";
  return value || "未解析动作";
}

function objectLabel(value?: string) {
  if (value === "cup") return "水杯";
  if (value === "ball") return "球体";
  return value || "未解析道具";
}
