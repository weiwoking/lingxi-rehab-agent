export type ImuSensorId = "lower_back_imu" | "upper_arm_imu" | "forearm_imu" | "wrist_imu";
export type LegacyImuSensorId = "trunk_imu";
export type SemgBusinessMuscleId = "finger_extensors_right" | "biceps_brachii_right" | "triceps_brachii_right" | "extensor_carpi_ulnaris_right";

export interface ImuNode { sensor_id: ImuSensorId; legacy_aliases?: LegacyImuSensorId[]; chinese_name: string; placement: string; role: "trunk_compensation" | "shoulder_motion" | "elbow_chain" | "end_effector_motion"; raw_sampling_hz: number; visualization_hz: number; }
export interface SemgChannel { sensor_id: "semg_ch1" | "semg_ch2" | "semg_ch3" | "semg_ch4"; hub_id: "semg_hub_01"; channel_id: "CH1" | "CH2" | "CH3" | "CH4"; business_muscle_id: SemgBusinessMuscleId; chinese_name: string; function: string; topology_mode: "shared_hub_four_channels"; raw_sampling_hz: number; visualization_hz: number; }
export interface SensorTopology { schema_version: "rehab.sensor_topology.v1"; patient_id: "P-0217"; affected_side: "right"; affected_side_label: "\u53f3\u4fa7\u4e0a\u80a2"; imu_nodes: ImuNode[]; semg_channels: SemgChannel[]; system_services: Array<{ service_id: string; name: string; role: string }>; topology_mode: "shared_hub_four_channels"; imu: ImuNode[]; semg: { hub_id: "semg_hub_01"; raw_sampling_hz: number; visualization_hz: number; channels: SemgChannel[] }; }
