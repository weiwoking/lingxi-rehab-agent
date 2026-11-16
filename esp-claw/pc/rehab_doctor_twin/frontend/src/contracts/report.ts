import type { ActionScoreSummary, ScorePayload } from "./realtimeMessages";

export interface RehabReport {
  title: string;
  patient: {
    patient_id: string;
    affected_side: string;
    brs: string;
    task: string;
    difficulty: string;
    training_time: string;
    session_id: string;
    device_id: string;
  };
  completion: {
    planned_reps: number;
    completed_reps: number;
    valid_reps: number;
    duration_sec: number;
    pause_count: number;
    fully_completed: boolean;
    end_reason: string;
  };
  score: ScorePayload;
  available_metrics?: string[];
  action_summaries?: ActionScoreSummary[];
  rep_details: Array<{
    rep_index: number;
    final_score: number;
    ROM: string;
    smoothness: string;
    coordination: string;
    muscle_performance: string;
    fatigue: { level: string; trend: string };
    compensation: { risk: string; status: string };
  }>;
  kinematics: Record<string, number>;
  emg_analysis: Record<string, string | number>;
  compensation_analysis: Record<string, string | boolean>;
  fatigue_analysis: Record<string, string | boolean>;
  summary: string;
  next_recommendations: string[];
  versions: {
    source_mode: string;
    source_origin?: string;
    score_version: string;
    schema_version: string;
    device_id: string;
    scenario_version?: string;
    system_version?: string;
    report_version?: string;
    generated_at: string;
    raw_score_result_path?: string;
    compact_score_result_path?: string;
  };
  disclaimer: string;
  demo_notice: string;
  sensor_mapping_notice?: string;
}
