/*
 * Private, protected copy of the frozen S3 -> P4 sensor_packet v5 contract.
 * This wire layout intentionally retains the existing ESP little-endian,
 * native IEEE-754 float and packed-structure semantics for Phase 4.1.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#define SENSOR_PACKET_MAGIC   0x34533353u
#define SENSOR_PACKET_VERSION 5u

typedef enum {
    SENSOR_NODE_TYPE_UNKNOWN = 0,
    SENSOR_NODE_TYPE_EMG_DUAL_IMU = 1,
    SENSOR_NODE_TYPE_DUAL_IMU = 2,
} sensor_node_type_t;

typedef enum {
    SENSOR_FLAG_EMG_VALID = 1u << 0,
    SENSOR_FLAG_IMU1_VALID = 1u << 1,
    SENSOR_FLAG_IMU2_VALID = 1u << 2,
} sensor_packet_flags_t;

#define SENSOR_PACKET_KNOWN_FLAGS \
    (SENSOR_FLAG_EMG_VALID | SENSOR_FLAG_IMU1_VALID | SENSOR_FLAG_IMU2_VALID)

typedef struct __attribute__((packed)) {
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

typedef struct __attribute__((packed)) {
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

typedef struct __attribute__((packed)) {
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

_Static_assert(sizeof(sensor_emg_state_t) == 84, "sensor_packet v5 EMG layout changed");
_Static_assert(sizeof(sensor_imu_state_t) == 64, "sensor_packet v5 IMU layout changed");
_Static_assert(sizeof(sensor_packet_t) == 242, "sensor_packet v5 size must remain 242 bytes");
_Static_assert(offsetof(sensor_packet_t, magic) == 0, "sensor_packet v5 magic offset changed");
_Static_assert(offsetof(sensor_packet_t, version) == 4, "sensor_packet v5 version offset changed");
_Static_assert(offsetof(sensor_packet_t, packet_size) == 6, "sensor_packet v5 size offset changed");
_Static_assert(offsetof(sensor_packet_t, node_id) == 8, "sensor_packet v5 node id offset changed");
_Static_assert(offsetof(sensor_packet_t, node_type) == 9, "sensor_packet v5 node type offset changed");
_Static_assert(offsetof(sensor_packet_t, packet_rate_hz) == 10, "sensor_packet v5 rate offset changed");
_Static_assert(offsetof(sensor_packet_t, tx_seq) == 12, "sensor_packet v5 sequence offset changed");
_Static_assert(offsetof(sensor_packet_t, tx_ts_us) == 16, "sensor_packet v5 timestamp offset changed");
_Static_assert(offsetof(sensor_packet_t, flags) == 24, "sensor_packet v5 flags offset changed");
_Static_assert(offsetof(sensor_packet_t, emg) == 28, "sensor_packet v5 EMG offset changed");
_Static_assert(offsetof(sensor_packet_t, imu1) == 112, "sensor_packet v5 IMU1 offset changed");
_Static_assert(offsetof(sensor_packet_t, imu2) == 176, "sensor_packet v5 IMU2 offset changed");
_Static_assert(offsetof(sensor_packet_t, checksum) == 240, "sensor_packet v5 checksum offset changed");
