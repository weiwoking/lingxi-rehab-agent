/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define REHAB_TRAINING_TASK_NAME_MAX_BYTES 96
#define REHAB_TRAINING_STORAGE_BASE_LEN    32
#define REHAB_TRAINING_SESSION_PATH_LEN    160
#define REHAB_TRAINING_FILE_PATH_LEN       224
#define REHAB_TRAINING_ERROR_CODE_LEN      32
#define REHAB_TRAINING_ERROR_MESSAGE_LEN   96

typedef enum {
    REHAB_TRAINING_STATE_IDLE = 0,
    REHAB_TRAINING_STATE_STARTING,
    REHAB_TRAINING_STATE_RECORDING,
    REHAB_TRAINING_STATE_STOPPING,
    REHAB_TRAINING_STATE_ERROR,
} rehab_training_state_t;

typedef enum {
    REHAB_UDP_QUALITY_UNKNOWN = 0,
    REHAB_UDP_QUALITY_NORMAL,
    REHAB_UDP_QUALITY_ACCEPTABLE,
    REHAB_UDP_QUALITY_DEGRADED,
} rehab_udp_quality_t;

typedef struct {
    bool online;
    bool packet_valid;
    bool packet_updating;
    uint32_t last_rx_age_ms;
    float measured_pps;
    uint64_t valid_packet_count;
    uint64_t rx_count;
    uint32_t last_tx_seq;
    uint64_t gap_count;
    uint64_t duplicate_count;
    uint64_t out_of_order_count;
    uint64_t reboot_or_rebaseline_count;
    uint64_t window_received;
    uint64_t window_gap;
    float udp_gap_ratio_pct;
    rehab_udp_quality_t udp_quality;
    uint64_t bad_length_count;
    uint64_t bad_magic_count;
    uint64_t bad_version_count;
    uint64_t bad_packet_size_count;
    uint64_t bad_node_count;
    uint64_t bad_flags_count;
    uint64_t checksum_error_count;
    uint64_t non_finite_count;
} rehab_sensor_node_status_t;

typedef struct {
    bool valid;
    uint16_t sample_count;
    bool window_updating;
    bool group_1_updating;
    bool group_2_updating;
    bool group_1_changed_recently;
    bool group_2_changed_recently;
} rehab_emg_status_t;

typedef struct {
    bool valid;
    bool updating;
    bool metadata_updating;
    bool changed_recently;
    uint16_t source_age_ms;
    uint32_t p4_node_age_ms;
    uint32_t update_count;
    int64_t source_ts_us;
} rehab_imu_status_t;

typedef struct {
    bool accepted;
    rehab_training_state_t state;
    uint32_t generation;
    char task_name[REHAB_TRAINING_TASK_NAME_MAX_BYTES + 1];
    char session_path[REHAB_TRAINING_SESSION_PATH_LEN];
    char csv_path[REHAB_TRAINING_FILE_PATH_LEN];
    char task_json_path[REHAB_TRAINING_FILE_PATH_LEN];
    uint64_t rows_written;
    uint64_t start_log_seq;
    uint64_t end_log_seq;
    uint64_t duration_ms;
    uint64_t queue_drop_count;
    uint64_t write_error_count;
    char end_reason[24];
    char error_code[REHAB_TRAINING_ERROR_CODE_LEN];
    char error_message[REHAB_TRAINING_ERROR_MESSAGE_LEN];
} rehab_training_result_t;

typedef struct {
    bool initialized;
    rehab_training_state_t state;
    bool recording;
    uint32_t generation;
    bool current_session_active;
    uint32_t active_generation;
    char storage_base[REHAB_TRAINING_STORAGE_BASE_LEN];
    bool sd_ready;
    char task_name[REHAB_TRAINING_TASK_NAME_MAX_BYTES + 1];
    char session_path[REHAB_TRAINING_SESSION_PATH_LEN];
    char csv_path[REHAB_TRAINING_FILE_PATH_LEN];
    char task_json_path[REHAB_TRAINING_FILE_PATH_LEN];
    uint64_t rows_written;
    uint64_t start_log_seq;
    uint64_t end_log_seq;
    uint32_t queue_depth;
    uint32_t queue_high_water;
    uint64_t queue_drop_count;
    bool writer_alive;
    uint64_t writer_last_progress_ms;
    uint64_t write_error_count;
    rehab_sensor_node_status_t node1;
    rehab_sensor_node_status_t node2;
    rehab_emg_status_t emg;
    rehab_imu_status_t imu1;
    rehab_imu_status_t imu2;
    rehab_imu_status_t imu3;
    rehab_imu_status_t imu4;
    bool required_sensors_ok;
    bool anomaly_active;
    uint32_t anomaly_duration_ms;
    bool anomaly_over_10s;
    bool last_completed_session_exists;
    rehab_training_result_t last_completed_session;
    esp_err_t last_error_code;
    int last_errno;
    char last_error[REHAB_TRAINING_ERROR_MESSAGE_LEN];
} rehab_training_status_t;

typedef void (*rehab_training_finalized_cb_t)(const char *session_id,
                                              const char *session_path,
                                              void *user_ctx);

esp_err_t rehab_sensor_recording_init(const char *storage_base_path);

esp_err_t rehab_training_register_finalized_callback(
    rehab_training_finalized_cb_t callback,
    void *user_ctx);

esp_err_t rehab_training_start(const char *task_name,
                               rehab_training_result_t *result);

esp_err_t rehab_training_stop(const char *reason,
                              uint32_t wait_timeout_ms,
                              rehab_training_result_t *result);

esp_err_t rehab_training_get_status(rehab_training_status_t *status);

const char *rehab_training_state_to_string(rehab_training_state_t state);
const char *rehab_udp_quality_to_string(rehab_udp_quality_t quality);

#ifdef __cplusplus
}
#endif
