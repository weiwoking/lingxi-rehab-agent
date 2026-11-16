/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "rehab_sensor_recording.h"
#include "sensor_packet_v5.h"

#define REHAB_SENSOR_UDP_PORT                  5005
#define REHAB_SENSOR_SOCKET_TIMEOUT_MS         500
#define REHAB_SENSOR_NODE_ONLINE_TIMEOUT_MS    1000
#define REHAB_SENSOR_ANOMALY_THRESHOLD_MS      10000
#define REHAB_SENSOR_OUTPUT_RATE_HZ            50
#define REHAB_SENSOR_OUTPUT_PERIOD_MS          20
#define REHAB_SENSOR_QUEUE_LENGTH              64
#define REHAB_SENSOR_CSV_LINE_BUFFER_SIZE      4096
#define REHAB_SENSOR_STDIO_BUFFER_SIZE         8192
#define REHAB_SENSOR_FLUSH_INTERVAL_MS         1000
#define REHAB_SENSOR_FSYNC_INTERVAL_MS         5000
#define REHAB_SENSOR_RX_TASK_STACK_BYTES       6144
#define REHAB_SENSOR_RX_TASK_PRIORITY          5
#define REHAB_SENSOR_WRITER_TASK_STACK_BYTES   8192
#define REHAB_SENSOR_WRITER_TASK_PRIORITY      4
#define REHAB_SENSOR_SUMMARY_INTERVAL_MS       10000
#define REHAB_SENSOR_UDP_MIN_WINDOW_RECEIVED   10
#define REHAB_SENSOR_WRITER_STALL_MS            10000
#define REHAB_SENSOR_CHANGED_RECENT_MS         2000
#define REHAB_SENSOR_SOCKET_RETRY_MS           1000
#define REHAB_SENSOR_SESSION_ROOT              "/sdcard/rehab_sessions"

#define REHAB_SENSOR_NODE_COUNT 2
#define REHAB_SENSOR_NODE1_INDEX 0
#define REHAB_SENSOR_NODE2_INDEX 1

typedef enum {
    REHAB_PACKET_OK = 0,
    REHAB_PACKET_BAD_LENGTH,
    REHAB_PACKET_BAD_MAGIC,
    REHAB_PACKET_BAD_VERSION,
    REHAB_PACKET_BAD_SIZE,
    REHAB_PACKET_BAD_NODE,
    REHAB_PACKET_BAD_FLAGS,
    REHAB_PACKET_BAD_CHECKSUM,
    REHAB_PACKET_NON_FINITE,
} rehab_packet_validation_t;

typedef enum {
    REHAB_SEQ_BASELINE = 0,
    REHAB_SEQ_CONTIGUOUS,
    REHAB_SEQ_GAP,
    REHAB_SEQ_DUPLICATE,
    REHAB_SEQ_OUT_OF_ORDER,
    REHAB_SEQ_REBASELINE,
} rehab_seq_result_t;

typedef struct {
    sensor_packet_t packet;
    int64_t rx_us;
    uint32_t generation;
} rehab_record_item_t;

typedef struct {
    bool seen;
    bool online;
    bool has_latest;
    int64_t last_rx_us;
    int64_t last_tx_ts_us;
    uint32_t last_tx_seq;
    sensor_packet_t latest;
    rehab_sensor_node_status_t public_status;
    uint64_t session_gap_count;
    int64_t pps_window_start_us;
    uint32_t pps_window_packets;
    int64_t udp_window_start_us;
    uint64_t udp_window_received;
    uint64_t udp_window_gap;
    bool udp_window_has_baseline;
} rehab_node_runtime_t;

typedef struct {
    bool initialized;
    bool flag_valid;
    uint32_t last_update_count;
    int64_t last_ts_us;
    int64_t last_metadata_advance_us;
    int64_t last_value_change_us;
    sensor_imu_state_t latest;
} rehab_imu_runtime_t;

typedef struct {
    bool initialized;
    bool flag_valid;
    int64_t last_window_end_us;
    int64_t last_metadata_advance_us;
    int64_t group_1_last_change_us;
    int64_t group_2_last_change_us;
    sensor_emg_state_t latest;
} rehab_emg_runtime_t;

typedef struct {
    bool has_packet;
    sensor_packet_t packet;
    int64_t rx_us;
} rehab_writer_node_t;

typedef struct {
    bool valid;
    uint32_t age_ms;
    float roll_deg;
    float pitch_deg;
    float yaw_deg;
    float gyro_x_dps;
    float gyro_y_dps;
    float gyro_z_dps;
    float acc_x_g;
    float acc_y_g;
    float acc_z_g;
    float lin_acc_x_g;
    float lin_acc_y_g;
    float lin_acc_z_g;
} rehab_fused_imu_t;

typedef struct {
    bool valid;
    uint16_t sample_count;
    int32_t latest[4];
    float rms[4];
    float mav[4];
    int32_t peak[4];
} rehab_fused_emg_t;

typedef struct {
    uint64_t log_seq;
    uint64_t time_ms;
    uint16_t output_rate_hz;
    uint32_t flags;
    uint64_t node_rx[2];
    uint32_t node_tx_seq[2];
    uint64_t node_tx_gap[2];
    uint32_t node_age_ms[2];
    rehab_fused_emg_t emg;
    rehab_fused_imu_t imu[4];
    uint8_t sanitized_node_mask;
} rehab_fused_row_t;

typedef struct {
    SemaphoreHandle_t mutex;
    QueueHandle_t queue;
    TaskHandle_t rx_task;
    TaskHandle_t writer_task;
    TaskHandle_t stop_waiter;
    bool init_attempted;
    bool initialized;
    bool recording;
    bool writer_alive;
    bool writer_ready;
    uint32_t enqueue_inflight;
    rehab_training_state_t state;
    uint32_t generation;
    char storage_base[REHAB_TRAINING_STORAGE_BASE_LEN];
    rehab_node_runtime_t nodes[REHAB_SENSOR_NODE_COUNT];
    rehab_emg_runtime_t emg;
    rehab_imu_runtime_t imus[4];
    rehab_writer_node_t writer_seed[REHAB_SENSOR_NODE_COUNT];
    uint64_t next_log_seq;
    uint64_t rows_written;
    uint64_t start_log_seq;
    uint64_t end_log_seq;
    uint64_t queue_drop_count;
    uint32_t queue_high_water;
    uint64_t write_error_count;
    uint64_t writer_last_progress_ms;
    int64_t session_start_us;
    int64_t session_stop_us;
    int64_t session_start_epoch;
    int64_t latest_finalized_end_epoch;
    bool session_clock_fallback;
    int64_t anomaly_start_us;
    bool required_sensors_ok;
    bool anomaly_active;
    bool anomaly_over_10s;
    FILE *csv_file;
    char *stdio_buffer;
    char task_name[REHAB_TRAINING_TASK_NAME_MAX_BYTES + 1];
    char session_path[REHAB_TRAINING_SESSION_PATH_LEN];
    char csv_path[REHAB_TRAINING_FILE_PATH_LEN];
    char csv_name[48];
    char task_json_path[REHAB_TRAINING_FILE_PATH_LEN];
    char start_time[40];
    char requested_end_reason[24];
    esp_err_t last_error_code;
    int last_errno;
    char last_error[REHAB_TRAINING_ERROR_MESSAGE_LEN];
    bool last_result_exists;
    rehab_training_result_t last_result;
} rehab_sensor_runtime_t;

extern rehab_sensor_runtime_t g_rehab_sensor;
extern const char REHAB_FUSED_CSV_HEADER[];

uint16_t rehab_sensor_packet_checksum(const sensor_packet_t *packet);
rehab_packet_validation_t rehab_sensor_packet_validate(const void *datagram,
                                                       size_t datagram_length,
                                                       sensor_packet_t *packet);
rehab_seq_result_t rehab_sensor_classify_sequence(bool has_baseline,
                                                  bool was_offline,
                                                  uint32_t previous_seq,
                                                  int64_t previous_tx_us,
                                                  uint32_t new_seq,
                                                  int64_t new_tx_us,
                                                  uint32_t *gap);
rehab_udp_quality_t rehab_sensor_classify_udp_quality_pct(float gap_ratio_pct);
rehab_udp_quality_t rehab_sensor_calculate_udp_quality(bool has_baseline,
                                                       uint64_t received_packets,
                                                       uint64_t gap_packets,
                                                       float *gap_ratio_pct);
bool rehab_sensor_session_is_active(rehab_training_state_t state, bool file_open);
bool rehab_sensor_last_completed_matches(bool exists,
                                         const rehab_training_result_t *last_completed,
                                         rehab_training_state_t state,
                                         uint32_t generation);

esp_err_t rehab_sensor_lock(void);
void rehab_sensor_unlock(void);
uint32_t rehab_sensor_age_ms(int64_t now_us, int64_t then_us);
uint32_t rehab_sensor_saturating_add_u32(uint32_t left, uint32_t right);
void rehab_sensor_refresh_health_locked(int64_t now_us);
void rehab_sensor_set_error_locked(esp_err_t err, int system_errno, const char *message);
void rehab_sensor_clear_error_locked(void);
void rehab_sensor_rx_task(void *arg);
void rehab_training_writer_task(void *arg);

void rehab_sensor_build_fused_row(const rehab_writer_node_t writer_nodes[2],
                                  const rehab_sensor_node_status_t node_status[2],
                                  const uint64_t session_gap[2],
                                  int64_t now_us,
                                  uint64_t log_seq,
                                  rehab_fused_row_t *row);
int rehab_sensor_format_csv_row(char *buffer,
                                size_t buffer_size,
                                const rehab_fused_row_t *row);
char *rehab_sensor_render_task_json(const char *task_name,
                                    const char *csv_name,
                                    const char *start_time,
                                    const char *end_time,
                                    uint64_t start_log_seq,
                                    uint64_t end_log_seq,
                                    const char *end_reason);
bool rehab_sensor_writer_allows_periodic_row(rehab_training_state_t state);

bool rehab_sensor_valid_task_name(const char *task_name);
bool rehab_sensor_valid_end_reason(const char *reason);
void rehab_sensor_fill_result_locked(rehab_training_result_t *result, bool accepted);
