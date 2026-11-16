import type { SensorTopology } from "../contracts/sensorTopology";

export const semgDisplayLabels = {
  CH1: "EMG1 - 指伸肌 - 手指伸展激活",
  CH2: "EMG2 - 肱二头肌 - 肘屈曲募集",
  CH3: "EMG3 - 肱三头肌 - 肘伸展募集",
  CH4: "EMG4 - 尺侧腕伸肌 - 腕伸与手腕稳定"
} as const;

export const sensorTopology: SensorTopology = {
  schema_version: "rehab.sensor_topology.v1",
  patient_id: "P-0217",
  affected_side: "right",
  affected_side_label: "右侧上肢",
  imu_nodes: [
    { sensor_id: "lower_back_imu", legacy_aliases: ["trunk_imu"], chinese_name: "后腰 IMU", placement: "lower_back", role: "trunk_compensation", raw_sampling_hz: 100, visualization_hz: 20 },
    { sensor_id: "wrist_imu", chinese_name: "腕部 IMU", placement: "wrist_right", role: "end_effector_motion", raw_sampling_hz: 100, visualization_hz: 20 },
    { sensor_id: "forearm_imu", chinese_name: "前臂 IMU", placement: "forearm_right", role: "elbow_chain", raw_sampling_hz: 100, visualization_hz: 20 },
    { sensor_id: "upper_arm_imu", chinese_name: "上臂 IMU", placement: "upper_arm_right", role: "shoulder_motion", raw_sampling_hz: 100, visualization_hz: 20 }
  ],
  semg_channels: [
    { sensor_id: "semg_ch1", hub_id: "semg_hub_01", channel_id: "CH1", business_muscle_id: "finger_extensors_right", chinese_name: "指伸肌", function: "手指伸展激活", topology_mode: "shared_hub_four_channels", raw_sampling_hz: 1000, visualization_hz: 10 },
    { sensor_id: "semg_ch2", hub_id: "semg_hub_01", channel_id: "CH2", business_muscle_id: "biceps_brachii_right", chinese_name: "肱二头肌", function: "肘屈曲募集", topology_mode: "shared_hub_four_channels", raw_sampling_hz: 1000, visualization_hz: 10 },
    { sensor_id: "semg_ch3", hub_id: "semg_hub_01", channel_id: "CH3", business_muscle_id: "triceps_brachii_right", chinese_name: "肱三头肌", function: "肘伸展募集", topology_mode: "shared_hub_four_channels", raw_sampling_hz: 1000, visualization_hz: 10 },
    { sensor_id: "semg_ch4", hub_id: "semg_hub_01", channel_id: "CH4", business_muscle_id: "extensor_carpi_ulnaris_right", chinese_name: "尺侧腕伸肌", function: "腕伸与手腕稳定", topology_mode: "shared_hub_four_channels", raw_sampling_hz: 1000, visualization_hz: 10 }
  ],
  system_services: [
    { service_id: "ws_stream", name: "实时 WebSocket", role: "streaming" },
    { service_id: "report_api", name: "报告生成服务", role: "reporting" }
  ],
  topology_mode: "shared_hub_four_channels",
  get imu() { return this.imu_nodes; },
  semg: {
    hub_id: "semg_hub_01",
    raw_sampling_hz: 1000,
    visualization_hz: 10,
    get channels() { return sensorTopology.semg_channels; }
  }
};
