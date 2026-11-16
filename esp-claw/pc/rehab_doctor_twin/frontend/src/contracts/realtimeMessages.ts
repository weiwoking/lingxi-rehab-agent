export type RehabStatus = "IDLE" | "RECORDING" | "FINALIZING" | "SCORING" | "COMPLETED" | "INVALID" | "FAILED" | "PREPARING" | "READY" | "RUNNING" | "UPLOADING" | "REPORT_READY";
export interface ImuQuaternion { w: number; x: number; y: number; z: number; }
export type ImuPoseSensorId = "lower_back_imu" | "upper_arm_imu" | "forearm_imu" | "wrist_imu";
export interface ImuSensorPose { sensor_id: ImuPoseSensorId | "trunk_imu"; quaternion: ImuQuaternion; calibrated: boolean; quality: number; timestamp_ms: number; }
export interface ImuPoseFrame { schema_version: "rehab.imu_pose.v1"; mapping_mode: "relative_quaternion_demo" | "relative_quaternion_live"; source: "DEMO DATA" | "LIVE" | "PRESENTATION"; quaternion_order?: "w,x,y,z"; sensors: { lower_back_imu?: ImuSensorPose; trunk_imu?: ImuSensorPose; upper_arm_imu: ImuSensorPose; forearm_imu: ImuSensorPose; wrist_imu: ImuSensorPose; }; }
export interface LiveImuFrame { timestamp_ms: number; session_id: string; source_mode: "demo" | "live" | "presentation" | "device" | "pc_local"; lower_back_imu: ImuSensorPose; upper_arm_imu: ImuSensorPose; forearm_imu: ImuSensorPose; wrist_imu: ImuSensorPose; }
export interface TelemetryPayload { shoulder_flexion_deg: number | null; shoulder_abduction_deg: number | null; shoulder_rotation_deg: number | null; elbow_flexion_deg: number | null; forearm_pronation_deg: number | null; wrist_flexion_deg: number | null; trunk_flexion_deg: number | null; trajectory_match: number | null; phase_progress: number | null; spatial_error_cm: number | null; action_phase?: string; imu_pose?: ImuPoseFrame; }
export interface EmgChannelPayload { label: string; activation: number | null; quality: number; primary: boolean; muscle_id?: string; }
export interface EmgPayload { CH1: EmgChannelPayload; CH2: EmgChannelPayload; CH3: EmgChannelPayload; CH4: EmgChannelPayload; }
export interface MotionQuality { final_score: number | null; subscores: Partial<{ speed_score: number; ROM_score: number; smoothness_score: number; coordination_score: number; muscle_score: number; }>; fatigue_level: string; compensation_risk: string; suggestion: string; }
export type NodeConnectionStatus = "unknown" | "normal" | "stale";
export interface DeviceStatus { device_id: string; name: string; category: string; sensor_type?: string; role?: string; mode: string; online: boolean; sample_rate: string; raw_sampling_hz?: number | null; visualization_hz?: number | null; latency_ms?: number | null; avg_latency_ms?: number | null; quality?: number | null; last_seq: number; tx_gap?: number | null; packet_loss?: number | null; packet_count?: number | null; out_of_order_dropped?: number | null; error_count?: number | null; updated_at: string; topology_mode?: string; hub_id?: string; channel_id?: string; business_muscle_id?: string; connection_status?: NodeConnectionStatus; }
export interface ActionScoreSummary { rep_index: number; valid?: boolean | null; final_score: number | null; subscores?: MotionQuality["subscores"]; imu_dtw_score?: number | null; emg_score?: number | null; compensation?: Record<string, unknown>; fatigue?: Record<string, unknown>; feedback?: string[]; invalid_reason?: string | null; }
export interface ScorePayload { session_id?: string; score_version: string; valid: boolean; final_score: number | null; subscores: MotionQuality["subscores"]; available_metrics?: string[]; action_summaries?: ActionScoreSummary[]; compensation: Record<string, unknown>; fatigue: Record<string, unknown>; brs: null | string; feedback: string[]; data_quality_level?: string | null; warnings?: string[]; completed_at?: string | null; segmentation?: Record<string, unknown>; action_source?: string | null; confidence?: "low" | "medium" | "high" | string | null; repetition_count?: number | null; valid_repetition_count?: number | null; }
export interface RepScoreMessagePayload { rep_index: number; score: ScorePayload; quality: MotionQuality; }
export interface RehabEvent { key: string; time_sec: number; text: string; }
export interface AlertPayload { level: "info" | "warning" | "danger"; title: string; message: string; rep_index?: number; }
export interface ImuRuntimeSample { sensor_id: "imu1" | "imu2" | "imu3" | "imu4"; label: string; source: string; session_id: string; sequence: number; received_at_ms: number | null; age_ms?: number | null; tx_ts_us: number | null; online: boolean; valid: boolean; roll_deg: number | null; pitch_deg: number | null; yaw_deg: number | null; gx?: number | null; gy?: number | null; gz?: number | null; ax?: number | null; ay?: number | null; az?: number | null; lax?: number | null; lay?: number | null; laz?: number | null; }
export interface EmgRuntimeSample { sensor_id: "emg1" | "emg2" | "emg3" | "emg4"; label: string; source: string; session_id: string; sequence: number; received_at_ms: number | null; age_ms?: number | null; tx_ts_us: number | null; online: boolean; valid: boolean; rms: number | null; mav: number | null; peak: number | null; latest: number | null; }
export interface SensorRuntimeSamples { imu: ImuRuntimeSample[]; emg: EmgRuntimeSample[]; }
export interface RehabSnapshot { status: RehabStatus; phase: string; next_phase: string; session_id: string; patient: { patient_id: string; profile_label: string; diagnosis_stage: string; affected_side: string; brs: string; age_band: string; today_sessions: number; today_average_score: number | null; risk_level: string; }; task: { task_id: string; name: string; difficulty: string; target_reps: number; completed_reps: number; prescription: string; target_angles: Partial<{ shoulder_flexion_deg: number; elbow_flexion_deg: number; wrist_flexion_deg: number; }>; }; telemetry: TelemetryPayload; emg: EmgPayload; quality: MotionQuality; devices: DeviceStatus[]; sensor_samples?: SensorRuntimeSamples; scores: RepScoreMessagePayload[]; events: RehabEvent[]; alert: AlertPayload | null; report_ready: boolean; updated_at: string; connection_message?: string; software_status?: "ready" | "degraded"; data_stream_status?: "waiting" | "receiving" | "stale"; udp_stream_status?: "waiting" | "receiving" | "stale"; parse_status?: "waiting" | "invalid" | "invalid_active" | "normal" | "stale"; has_current_valid_frame?: boolean; receive_rate_hz?: number; valid_frame_rate_hz?: number; udp_datagram_rate_hz?: number; udp_datagram_count?: number; udp_bytes_total?: number; udp_last_source_ip?: string | null; udp_last_source_port?: number | null; udp_last_received_at_ms?: number | null; udp_last_age_ms?: number | null; valid_frame_count?: number; invalid_frame_count?: number; duplicate_frame_count?: number; last_source_ip?: string | null; last_source_port?: number | null; last_log_seq?: number | null; last_time_ms?: number | null; last_received_at?: string | null; last_valid_frame_at_ms?: number | null; last_valid_frame_age_ms?: number | null; last_column_count?: number | null; last_parse_error?: string | null; last_error?: string | null; last_invalid_frame_at_ms?: number | null; last_duplicate_frame_at_ms?: number | null; device_observation_status?: "unknown" | "observed"; control_mode?: "p4" | "pc_local"; training_status?: { session_id?: string; state?: string; node1_online?: number; node2_online?: number; required_ok?: number; training_rows?: number | null; recording_row_count?: number; recording_duration_seconds?: number; score_state?: string; can_start?: boolean; active_task_available?: boolean; auto_score?: { submission_state?: string | null; last_error?: string | null; retry_count?: number; next_retry_at_ms?: number | null; }; record_queue_drop?: number | null; sd_write_error?: number | null; }; raw_log_summary?: { rows?: number; sampling_rate_hz?: number | null; job_state?: string; score_state?: string; udp_datagram_count?: number; valid_frame_count?: number; invalid_frame_count?: number; duplicate_frame_count?: number; }; }
export interface RehabSnapshot {
  stream_source_mode?: "udp" | "csv_tail" | "csv_replay";
  stream_source_label?: string;
  csv_valid_frame_count?: number | null;
  csv_invalid_frame_count?: number | null;
  csv_duplicate_frame_count?: number | null;
  csv_last_column_count?: number | null;
  csv_last_parse_error?: string | null;
  csv_current_row?: number | null;
  csv_total_rows?: number | null;
  csv_last_log_seq?: number | null;
  csv_last_time_ms?: number | null;
  csv_file_name?: string;
  csv_file_size?: number | null;
  csv_read_offset?: number | null;
  csv_rows_read?: number | null;
  csv_last_new_data_at_ms?: number | null;
  csv_file_status?: string;
  csv_replay_state?: "idle" | "paused" | "playing" | "complete" | "error" | string;
  csv_replay_ready?: boolean;
  csv_play_requested?: boolean;
  csv_replay_generation?: number | null;
  csv_replay_speed?: number | null;
  selected_session_id?: string | null;
  selected_csv_file?: string;
  csv_activation_state?: "discovering" | "loading" | "ready" | "playing" | "paused" | "complete" | "error" | string;
  csv_activation_error?: string | null;
}
export type RealtimeMessage =
  | RealtimeMessageBase<"rehab.state", { snapshot: RehabSnapshot }>
  | RealtimeMessageBase<"rehab.telemetry", TelemetryPayload>
  | RealtimeMessageBase<"rehab.emg", EmgPayload>
  | RealtimeMessageBase<"rehab.score", RepScoreMessagePayload>
  | RealtimeMessageBase<"rehab.event", RehabEvent>
  | RealtimeMessageBase<"rehab.alert", AlertPayload>
  | RealtimeMessageBase<"rehab.report_ready", { session_id: string; report?: unknown }>
  | RealtimeMessageBase<"device.status", { devices: DeviceStatus[] }>;

interface RealtimeMessageBase<T extends string, P> { schema_version: string; type: T; device_id: string; patient_id: string; session_id: string; seq: number; timestamp_ms: number; source_mode: "demo" | "live" | "hybrid" | "presentation" | "device" | "pc_local" | "DEMO DATA" | "LIVE"; payload: P; }

export function isRealtimeMessage(value: unknown): value is RealtimeMessage {
  if (!value || typeof value !== "object") return false;
  const maybe = value as { type?: unknown; payload?: unknown; seq?: unknown; timestamp_ms?: unknown };
  return typeof maybe.type === "string" && maybe.payload !== undefined && typeof maybe.seq === "number" && typeof maybe.timestamp_ms === "number";
}
