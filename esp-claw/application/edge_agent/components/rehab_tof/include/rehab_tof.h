// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define REHAB_TOF_ZONE_COUNT 64
#define REHAB_TOF_ERROR_MAX  96
#define REHAB_TOF_MAX_CANDIDATE_FRAMES 8

typedef void (*rehab_tof_warmup_notify_fn)(void *ctx);

typedef struct {
    uint8_t candidate_count;
    rehab_tof_warmup_notify_fn warmup_notify;
    void *warmup_notify_ctx;
} rehab_tof_window_config_t;

typedef struct {
    uint64_t tof_frame_seq;
    int64_t timestamp_us;
    int16_t distance_mm[REHAB_TOF_ZONE_COUNT];
    uint8_t status_code[REHAB_TOF_ZONE_COUNT];
    uint8_t valid_zone_count;
    uint8_t usable_zone_count;
    uint8_t high_confidence_zone_count;
    uint8_t status_5_count;
    uint8_t status_6_count;
    uint8_t status_9_count;
    uint8_t status_255_count;
} rehab_tof_candidate_frame_t;

typedef struct {
    uint8_t actual_candidate_count;
    rehab_tof_candidate_frame_t candidates[REHAB_TOF_MAX_CANDIDATE_FRAMES];
    uint8_t warmup_status_5_count;
    uint8_t warmup_status_6_count;
    uint8_t warmup_status_9_count;
    uint8_t warmup_status_255_count;
    uint64_t request_generation;
    uint32_t total_time_ms;
} rehab_tof_window_result_t;

typedef enum {
    REHAB_TOF_UNINITIALIZED = 0,
    REHAB_TOF_STARTING,
    REHAB_TOF_READY,
    REHAB_TOF_STOPPING,
    REHAB_TOF_STOPPED,
    REHAB_TOF_ERROR,
} rehab_tof_state_t;

typedef struct {
    int i2c_port;
    int sda_gpio;
    int scl_gpio;
    uint32_t i2c_frequency_hz;
    uint8_t ranging_frequency_hz;
    uint32_t ready_poll_interval_ms;
    uint32_t ready_timeout_ms;
    uint16_t diagnostic_log_every_n_frames;
    uint8_t startup_max_attempts;
    uint32_t startup_retry_delay_ms;
} rehab_tof_config_t;

#define REHAB_TOF_CONFIG_DEFAULT() { \
    .i2c_port = 1,                    \
    .sda_gpio = 21,                   \
    .scl_gpio = 22,                   \
    .i2c_frequency_hz = 400000,       \
    .ranging_frequency_hz = 15,       \
    .ready_poll_interval_ms = 10,     \
    .ready_timeout_ms = 2500,         \
    .diagnostic_log_every_n_frames = 15, \
    .startup_max_attempts = 3,        \
    .startup_retry_delay_ms = 50,     \
}

typedef struct {
    uint64_t generation;
    int64_t timestamp_us;
    uint32_t total_time_ms;
    uint8_t resolution_width;
    uint8_t resolution_height;
    int16_t distance_mm[REHAB_TOF_ZONE_COUNT];
    uint8_t status_code[REHAB_TOF_ZONE_COUNT];
    uint8_t valid_zone_count;
    uint8_t usable_zone_count;
    uint8_t high_confidence_zone_count;
    uint8_t status_5_count;
    uint8_t status_6_count;
    uint8_t status_9_count;
    uint8_t status_255_count;
    uint8_t warmup_status_5_count;
    uint8_t warmup_status_6_count;
    uint8_t warmup_status_9_count;
    uint8_t warmup_status_255_count;
    uint8_t frames_consumed_per_snapshot;
    int16_t minimum_valid_distance_mm;
    int16_t maximum_valid_distance_mm;
} rehab_tof_snapshot_t;

typedef struct {
    uint64_t sequence;
    int64_t timestamp_us;
    uint8_t resolution_width;
    uint8_t resolution_height;
    int16_t distance_mm[REHAB_TOF_ZONE_COUNT];
    uint16_t range_sigma_mm[REHAB_TOF_ZONE_COUNT];
    uint8_t target_status[REHAB_TOF_ZONE_COUNT];
    uint8_t nb_target_detected[REHAB_TOF_ZONE_COUNT];
    uint32_t data_ready_wait_us;
    uint32_t get_ranging_data_us;
    uint32_t i2c_read_us;
    uint32_t parse_and_copy_us;
    uint32_t provider_publish_us;
    uint32_t i2c_packet_bytes;
} rehab_tof_extended_frame_t;

typedef struct {
    rehab_tof_state_t state;
    bool ready;
    bool snapshot_busy;
    bool request_pending;
    bool window_active;
    bool pipeline_quiescent;
    uint64_t generation;
    uint64_t request_count;
    uint64_t success_count;
    uint64_t error_count;
    int64_t timestamp_us;
    uint32_t total_time_ms;
    uint8_t resolution_width;
    uint8_t resolution_height;
    int16_t distance_mm[REHAB_TOF_ZONE_COUNT];
    uint8_t status_code[REHAB_TOF_ZONE_COUNT];
    uint8_t valid_zone_count;
    uint8_t usable_zone_count;
    uint8_t high_confidence_zone_count;
    uint8_t status_5_count;
    uint8_t status_6_count;
    uint8_t status_9_count;
    uint8_t status_255_count;
    uint8_t warmup_status_5_count;
    uint8_t warmup_status_6_count;
    uint8_t warmup_status_9_count;
    uint8_t warmup_status_255_count;
    uint8_t frames_consumed_per_snapshot;
    int16_t minimum_valid_distance_mm;
    int16_t maximum_valid_distance_mm;
    char last_error[REHAB_TOF_ERROR_MAX];
    esp_err_t last_esp_err;
    int i2c_port;
    int sda_gpio;
    int scl_gpio;
    uint32_t i2c_frequency_hz;
    uint8_t ranging_frequency_hz;
    bool continuous_ranging;
    bool extended_frame_valid;
    uint8_t startup_frames_discarded;
    uint64_t extended_sequence;
    uint32_t last_data_ready_wait_us;
    uint32_t last_get_ranging_data_us;
    uint32_t last_i2c_read_us;
    uint32_t last_parse_and_copy_us;
    uint32_t last_provider_publish_us;
    uint32_t last_i2c_packet_bytes;
    uint32_t i2c_read_error_count;
    uint32_t i2c_write_error_count;
    uint8_t startup_attempts;
    uint8_t startup_max_attempts;
    uint32_t startup_retry_count;
    esp_err_t last_startup_raw_err;
    esp_err_t last_startup_mapped_err;
    char last_startup_operation[REHAB_TOF_ERROR_MAX];
} rehab_tof_status_t;

esp_err_t rehab_tof_init(const rehab_tof_config_t *config);
esp_err_t rehab_tof_start(void);
esp_err_t rehab_tof_snapshot_acquire(rehab_tof_snapshot_t *snapshot,
                                     uint32_t timeout_ms);
esp_err_t rehab_tof_window_acquire(const rehab_tof_window_config_t *config,
                                   rehab_tof_window_result_t *result,
                                   uint32_t timeout_ms);
esp_err_t rehab_tof_get_status(rehab_tof_status_t *status);
esp_err_t rehab_tof_extended_get_latest(rehab_tof_extended_frame_t *frame,
                                        uint64_t after_sequence);
esp_err_t rehab_tof_extended_wait_next(rehab_tof_extended_frame_t *frame,
                                       uint64_t after_sequence,
                                       uint32_t timeout_ms);
bool rehab_tof_status_is_quiescent(const rehab_tof_status_t *status);
esp_err_t rehab_tof_stop(void);
void rehab_tof_deinit(void);
bool rehab_tof_is_ready(void);
const char *rehab_tof_state_name(rehab_tof_state_t state);

#ifdef __cplusplus
}
#endif
