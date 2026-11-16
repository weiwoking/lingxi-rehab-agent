export type ClinicalDataSourceTag = "demo" | "live" | "unavailable";

export interface ClinicianPrescription {
  patient_id: "P-0217";
  affected_side: "right";
  affected_side_label: "右侧上肢";
  brs: string;
  training_goal: string;
  allowed_difficulty: number[];
  max_reach_height_cm: number;
  prohibited_task_types: string[];
  monitoring_focus: string[];
  version: string;
  updated_at: string;
  source: ClinicalDataSourceTag;
  enabled_tasks?: Array<"drink_water_right" | "pick_book_right">;
  valid_from?: string;
  valid_until?: string;
  training_frequency?: string;
  target_reps?: number;
  enabled?: boolean;
  requires_per_session_approval?: false;
}

export interface TaskCandidateDecision {
  candidate_task_id: string;
  candidate_task_name: string;
  environment_condition: string;
  clinician_boundary: string;
  suggested_difficulty: number;
  decision: "selected" | "rejected";
  reason: string;
}

export interface SessionTaskDecision {
  patient_id: "P-0217";
  session_id: string;
  task_id: string;
  task_name: string;
  selected_difficulty: number;
  training_objective: string;
  monitoring_focus: string;
  selection_basis: string;
  prescription_version: string;
  candidates: TaskCandidateDecision[];
  source: ClinicalDataSourceTag;
}

export type SafetyWorkflowState =
  | "safe"
  | "compensation_detected"
  | "paused_correction"
  | "waiting_recovery"
  | "retrying"
  | "recovered"
  | "completed";

export interface SafetyIntervention {
  id: string;
  event_type: "trunk_flexion" | "none";
  event_label: string;
  phase: string;
  measured_deg: number | null;
  threshold_deg: number | null;
  severity: "none" | "mild" | "moderate" | "severe";
  action: "none" | "pause_remind" | "retry";
  workflow: SafetyWorkflowState[];
  outcome: string;
  source: ClinicalDataSourceTag | "esp_claw";
}

export interface TrainingTrendPoint {
  session_id: string;
  date_label: string;
  overall_score: number;
  ROM_score: number;
  smoothness_score: number;
  compensation_count: number;
  duration_sec: number;
}

export interface PatientTrainingTrend {
  patient_id: "P-0217";
  points: TrainingTrendPoint[];
  source: ClinicalDataSourceTag;
}

export interface TherapistReview {
  patient_id: "P-0217";
  session_id: string;
  agent_suggestion: string;
  suggestion_notice: string;
  decision: "pending" | "accepted" | "modified" | "rejected";
  doctor_note: string;
  confirmed_plan: string;
  updated_at: string;
  source: ClinicalDataSourceTag;
}

export interface TrainingOutcomeSummary {
  patient_id: "P-0217";
  session_id: string;
  task_name: "右手拿杯喝水";
  state: "training" | "completed";
  final_score: number;
  completion_percent: number;
  ROM_score: number;
  smoothness_score: number;
  coordination_score: number;
  muscle_score: number;
  posture_stability: number;
  fluency: number;
  compensation_count: number;
  correction_success: boolean;
  duration_label: string;
  fatigue_label: string;
  source: ClinicalDataSourceTag;
}
