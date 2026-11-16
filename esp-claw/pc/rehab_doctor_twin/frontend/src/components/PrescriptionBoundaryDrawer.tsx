import { X } from "lucide-react";
import { useEffect, useState } from "react";
import type { ClinicianPrescription } from "../contracts/clinicalWorkspace";

interface Props {
  open: boolean;
  prescription: ClinicianPrescription | null;
  onClose: () => void;
  onSave: (prescription: ClinicianPrescription) => Promise<void>;
}

export function PrescriptionBoundaryDrawer({ open, prescription, onClose, onSave }: Props) {
  const [draft, setDraft] = useState<ClinicianPrescription | null>(prescription);
  const [saving, setSaving] = useState(false);

  useEffect(() => setDraft(prescription), [prescription]);

  if (!open || !draft) return null;

  const update = <K extends keyof ClinicianPrescription>(key: K, value: ClinicianPrescription[K]) => {
    setDraft((current) => current ? { ...current, [key]: value } : current);
  };

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      await onSave(draft);
    } finally {
      setSaving(false);
    }
  };

  return (
    <aside className="clinical-drawer" aria-label="医生处方边界">
      <header>
        <div>
          <span>临床训练约束</span>
          <h2>医生处方边界</h2>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭处方抽屉"><X size={18} /></button>
      </header>
      <div className="drawer-body">
        <div className="clinical-kv-grid">
          <p><span>患者编号</span><strong>{draft.patient_id}</strong></p>
          <p><span>患侧</span><strong>{draft.affected_side_label}</strong></p>
          <p><span>BRS</span><strong>{draft.brs}</strong></p>
          <p><span>版本</span><strong>{draft.version}</strong></p>
          <p><span>更新时间</span><strong>{draft.updated_at}</strong></p>
        </div>
        <label className="drawer-field">
          <span>当前训练目标</span>
          <textarea value={draft.training_goal} onChange={(event) => update("training_goal", event.target.value)} />
        </label>
        <label className="drawer-field">
          <span>允许难度</span>
          <input value={draft.allowed_difficulty.join("-") + "级"} readOnly />
        </label>
        <label className="drawer-field">
          <span>最大取物高度 cm</span>
          <input type="number" min={60} max={140} value={draft.max_reach_height_cm} onChange={(event) => update("max_reach_height_cm", Number(event.target.value))} />
        </label>
        <label className="drawer-field">
          <span>禁止任务类型</span>
          <textarea value={draft.prohibited_task_types.join("、")} onChange={(event) => update("prohibited_task_types", event.target.value.split(/[、,，]/).map((item) => item.trim()).filter(Boolean))} />
        </label>
        <label className="drawer-field">
          <span>重点监测</span>
          <textarea value={draft.monitoring_focus.join("、")} onChange={(event) => update("monitoring_focus", event.target.value.split(/[、,，]/).map((item) => item.trim()).filter(Boolean))} />
        </label>
        <p className="drawer-permission-note">ESP-Claw 读取医生处方边界用于任务约束；患者指令不能修改处方边界。</p>
      </div>
      <footer>
        <button type="button" onClick={() => setDraft(prescription)}>取消</button>
        <button type="button" className="primary" onClick={save} disabled={saving}>{saving ? "保存中" : "保存处方"}</button>
      </footer>
    </aside>
  );
}
