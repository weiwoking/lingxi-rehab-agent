#ifndef __SENSOR_PACKET_H__
#define __SENSOR_PACKET_H__

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief S3 发给 P4 的统一传感器状态包。
 *
 * magic 近似 ASCII 字符串 "S3S4"，version 当前为 v5。
 * 三个工程必须保持本文件中的结构体字段顺序和大小一致。
 */
#define SENSOR_PACKET_MAGIC      0x34533353u
#define SENSOR_PACKET_VERSION    5u

/**
 * @brief 传感器节点类型。
 */
typedef enum
{
    SENSOR_NODE_TYPE_UNKNOWN      = 0,

    /**
     * node_id=1：左侧 S3，采集 ADS1292 EMG 和两个 IMU。
     */
    SENSOR_NODE_TYPE_EMG_DUAL_IMU = 1,

    /**
     * node_id=2：右侧 S3，只采集两个 IMU。
     */
    SENSOR_NODE_TYPE_DUAL_IMU     = 2,
} sensor_node_type_t;

/**
 * @brief 传感器状态有效位。
 */
typedef enum
{
    SENSOR_FLAG_EMG_VALID   = 1u << 0,
    SENSOR_FLAG_IMU1_VALID  = 1u << 1,
    SENSOR_FLAG_IMU2_VALID  = 1u << 2,
} sensor_packet_flags_t;

/**
 * @brief EMG 四通道窗口特征。
 */
typedef struct __attribute__((packed))
{
    int64_t window_start_us;
    int64_t window_end_us;

    uint16_t sample_count;
    uint16_t reserved;

    int32_t latest_ch1;
    int32_t latest_ch2;
    int32_t latest_ch3;
    int32_t latest_ch4;

    float rms_ch1;
    float rms_ch2;
    float rms_ch3;
    float rms_ch4;

    float mav_ch1;
    float mav_ch2;
    float mav_ch3;
    float mav_ch4;

    int32_t peak_abs_ch1;
    int32_t peak_abs_ch2;
    int32_t peak_abs_ch3;
    int32_t peak_abs_ch4;
} sensor_emg_state_t;

/**
 * @brief 单个 IMU 的姿态、加速度和角速度快照。
 *
 * acc_x/y/z 单位为 g，包含重力分量。
 * lin_acc_x/y/z 单位为 g，是 S3 端根据 roll/pitch 估计扣除重力后的线加速度。
 * angle_x/y/z 对应 roll / pitch / yaw，gyro_x/y/z 单位为 deg/s。
 */
typedef struct __attribute__((packed))
{
    int64_t ts_us;

    uint32_t update_count;
    uint16_t age_ms;
    uint16_t reserved;

    float acc_x_g;
    float acc_y_g;
    float acc_z_g;

    float lin_acc_x_g;
    float lin_acc_y_g;
    float lin_acc_z_g;

    float gyro_x_dps;
    float gyro_y_dps;
    float gyro_z_dps;

    float angle_x_deg;
    float angle_y_deg;
    float angle_z_deg;
} sensor_imu_state_t;

/**
 * @brief S3 -> P4 的统一状态包。
 *
 * node_id=1 表示 S3-L：EMG + IMU1 + IMU2。
 * node_id=2 表示 S3-R：包内仍叫 imu1/imu2，P4 融合层再映射成全局 IMU3/IMU4。
 * packet_rate_hz 表示当前 S3 发包频率，封存版本默认 100Hz。
 */
typedef struct __attribute__((packed))
{
    uint32_t magic;
    uint16_t version;
    uint16_t packet_size;

    uint8_t node_id;
    uint8_t node_type;
    uint16_t packet_rate_hz;

    uint32_t tx_seq;
    int64_t tx_ts_us;

    uint32_t flags;

    sensor_emg_state_t emg;
    sensor_imu_state_t imu1;
    sensor_imu_state_t imu2;

    uint16_t checksum;
} sensor_packet_t;

#ifdef __cplusplus
}
#endif

#endif
