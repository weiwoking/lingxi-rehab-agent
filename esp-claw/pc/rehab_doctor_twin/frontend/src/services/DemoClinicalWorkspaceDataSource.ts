import type {
  ClinicianPrescription,
  PatientTrainingTrend,
  SafetyIntervention,
  SessionTaskDecision,
  TherapistReview
} from "../contracts/clinicalWorkspace";
import type { ClinicalWorkspaceDataSource } from "./ClinicalWorkspaceDataSourceContract";

const patientId = "P-0217" as const;
const defaultSessionId = "SESSION_DEMO_P0217";
const storagePrefix = "clinical_workspace_demo";

const defaultPrescription: ClinicianPrescription = {
  patient_id: patientId,
  affected_side: "right",
  affected_side_label: "右侧上肢",
  brs: "BRS IV",
  training_goal: "提升右手拿杯喝水动作的伸手稳定性与回收控制",
  allowed_difficulty: [1, 2],
  max_reach_height_cm: 90,
  prohibited_task_types: ["高处取物", "负重抬举", "快速连续抓取"],
  monitoring_focus: ["躯干前倾", "右肩代偿", "动作连续性"],
  version: "RX-P0217-20260723-demo",
  updated_at: "2026-07-23 09:20",
  source: "demo"
};

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson<T>(key: string, value: T) {
  window.localStorage.setItem(key, JSON.stringify(value));
}

export class DemoClinicalWorkspaceDataSource implements ClinicalWorkspaceDataSource {
  async getPrescription(): Promise<ClinicianPrescription> {
    return readJson(`${storagePrefix}:prescription:${patientId}`, defaultPrescription);
  }

  async savePrescription(_patientId: string, prescription: ClinicianPrescription): Promise<ClinicianPrescription> {
    const next = { ...prescription, source: "demo" as const, updated_at: new Date().toLocaleString("zh-CN", { hour12: false }) };
    writeJson(`${storagePrefix}:prescription:${patientId}`, next);
    return next;
  }

  async getTaskDecision(_patientId: string, sessionId = defaultSessionId): Promise<SessionTaskDecision> {
    const prescription = await this.getPrescription();
    return {
      patient_id: patientId,
      session_id: sessionId,
      task_id: "DRINK_CUP",
      task_name: "右手拿杯喝水",
      selected_difficulty: 2,
      training_objective: prescription.training_goal,
      monitoring_focus: "重点监测躯干前倾，必要时暂停提醒后重试",
      selection_basis: "水杯高度72 cm处于医生处方边界内，难度2级符合允许范围。",
      prescription_version: prescription.version,
      source: "demo",
      candidates: [
        {
          candidate_task_id: "DRINK_CUP",
          candidate_task_name: "右手拿杯喝水",
          environment_condition: "水杯高度72 cm，位于右前方桌面",
          clinician_boundary: "最大取物高度90 cm，允许难度1-2级",
          suggested_difficulty: 2,
          decision: "selected",
          reason: "目标高度和难度均在医生处方边界内，适合本轮训练。"
        },
        {
          candidate_task_id: "TAKE_BOOK_HIGH",
          candidate_task_name: "从高处书架取书",
          environment_condition: "书本高度126 cm",
          clinician_boundary: "最大取物高度90 cm，允许难度1-2级",
          suggested_difficulty: 4,
          decision: "rejected",
          reason: "物体高度126 cm，超过医生设置的90 cm上限"
        }
      ]
    };
  }

  async getSafetyInterventions(): Promise<SafetyIntervention[]> {
    return [
      {
        id: "safety-demo-trunk-flexion",
        event_type: "trunk_flexion",
        event_label: "躯干前倾",
        phase: "伸手取杯",
        measured_deg: 18,
        threshold_deg: 12,
        severity: "mild",
        action: "pause_remind",
        workflow: ["safe", "compensation_detected", "paused_correction", "waiting_recovery", "retrying", "recovered", "completed"],
        outcome: "姿态恢复后重新尝试成功",
        source: "demo"
      }
    ];
  }

  async getTrainingTrend(_patientId: string, limit = 7): Promise<PatientTrainingTrend> {
    return {
      patient_id: patientId,
      source: "demo",
      points: [
        ["7/17", 74, 69, 71, 3, 326],
        ["7/18", 76, 71, 73, 3, 318],
        ["7/19", 78, 73, 75, 2, 306],
        ["7/20", 80, 75, 78, 2, 292],
        ["7/21", 82, 77, 80, 1, 276],
        ["7/22", 84, 80, 82, 1, 268],
        ["7/23", 86, 82, 84, 1, 258]
      ].slice(-limit).map(([date_label, overall_score, ROM_score, smoothness_score, compensation_count, duration_sec], index) => ({
        session_id: `DEMO_TREND_${index + 1}`,
        date_label: String(date_label),
        overall_score: Number(overall_score),
        ROM_score: Number(ROM_score),
        smoothness_score: Number(smoothness_score),
        compensation_count: Number(compensation_count),
        duration_sec: Number(duration_sec)
      }))
    };
  }

  async getTherapistReview(_patientId: string, sessionId = defaultSessionId): Promise<TherapistReview> {
    return readJson(`${storagePrefix}:review:${sessionId}`, {
      patient_id: patientId,
      session_id: sessionId,
      agent_suggestion: "患者能够在提醒后纠正躯干前倾。下一次训练建议保持二级难度，继续关注伸手阶段的躯干稳定性。",
      suggestion_notice: "智能体建议仅供治疗师参考，不会自动修改医生处方。",
      decision: "pending",
      doctor_note: "",
      confirmed_plan: "保持右手拿杯喝水二级难度，继续监测躯干前倾。",
      updated_at: "2026-07-23 09:28",
      source: "demo"
    });
  }

  async saveTherapistReview(review: TherapistReview): Promise<TherapistReview> {
    const next = { ...review, source: "demo" as const, updated_at: new Date().toLocaleString("zh-CN", { hour12: false }) };
    writeJson(`${storagePrefix}:review:${review.session_id}`, next);
    return next;
  }
}
