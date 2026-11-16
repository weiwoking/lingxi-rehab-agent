// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define REHAB_FOLLOW_CAMERA_DEVICE_PATH_MAX 64
#define REHAB_FOLLOW_CAMERA_ERROR_MAX       128
#define REHAB_FOLLOW_CAMERA_DRIVER_BUFS     3
#define REHAB_FOLLOW_CAMERA_RAW_SLOTS       2
#define REHAB_FOLLOW_CAMERA_JPEG_SLOTS      2

typedef enum {
    REHAB_FOLLOW_CAMERA_UNINITIALIZED = 0,
    REHAB_FOLLOW_CAMERA_CONFLICT,
    REHAB_FOLLOW_CAMERA_STARTING,
    REHAB_FOLLOW_CAMERA_RUNNING,
    REHAB_FOLLOW_CAMERA_STOPPING,
    REHAB_FOLLOW_CAMERA_STOPPED,
    REHAB_FOLLOW_CAMERA_NO_MEMORY,
    REHAB_FOLLOW_CAMERA_ERROR,
} rehab_follow_camera_state_t;

typedef enum {
    REHAB_FOLLOW_CAMERA_PIXEL_FORMAT_RGB565 = 1,
} rehab_follow_camera_pixel_format_t;

typedef enum {
    REHAB_FOLLOW_CAMERA_BYTE_ORDER_LITTLE_ENDIAN = 1,
} rehab_follow_camera_byte_order_t;

typedef struct {
    const char *device_path;
    const char *enabled_lua_modules;
    uint32_t preview_interval_ms;
    uint32_t capture_timeout_ms;
    uint32_t reopen_backoff_ms;
    uint32_t max_reopen_attempts;
    uint8_t jpeg_quality;
} rehab_follow_camera_config_t;

#define REHAB_FOLLOW_CAMERA_CONFIG_DEFAULT() { \
    .device_path = NULL,                         \
    .enabled_lua_modules = NULL,                 \
    .preview_interval_ms = 500,                  \
    .capture_timeout_ms = 1000,                  \
    .reopen_backoff_ms = 1000,                   \
    .max_reopen_attempts = 3,                    \
    .jpeg_quality = 60,                          \
}

typedef struct {
    rehab_follow_camera_state_t state;
    char last_error[REHAB_FOLLOW_CAMERA_ERROR_MAX];
    esp_err_t last_esp_err;
    int last_errno;
    bool owner_conflict;
    bool fd_valid;
    bool streaming;
    char device_path[REHAB_FOLLOW_CAMERA_DEVICE_PATH_MAX];
    uint32_t width;
    uint32_t height;
    uint32_t pixel_format;
    char pixel_format_fourcc[5];
    char pixel_format_name[16];
    char byte_order[16];
    uint32_t bytesperline;
    uint32_t sizeimage;
    size_t compact_payload_bytes;
    uint32_t source_width;
    uint32_t source_height;
    uint32_t source_pixel_format;
    char source_pixel_format_fourcc[5];
    char source_pixel_format_name[16];
    char source_byte_order[16];
    uint32_t source_bytesperline;
    uint32_t source_sizeimage;
    size_t source_payload_bytes;
    uint32_t output_width;
    uint32_t output_height;
    char output_pixel_format[16];
    char output_byte_order[16];
    uint32_t output_bytesperline;
    size_t output_payload_bytes;
    char scaler_mode[24];
    uint32_t driver_buffer_count;
    size_t driver_buffer_lengths[REHAB_FOLLOW_CAMERA_DRIVER_BUFS];
    uint32_t last_bytesused;
    uint64_t frame_generation;
    uint64_t capture_count;
    uint64_t dq_error_count;
    uint64_t q_error_count;
    uint64_t copy_error_count;
    uint64_t jpeg_error_count;
    uint64_t reopen_count;
    uint32_t retry_count;
    int64_t last_frame_timestamp_us;
    uint64_t last_interval_us;
    uint64_t min_interval_us;
    uint64_t max_interval_us;
    uint64_t avg_interval_us;
    uint64_t jpeg_generation;
    uint64_t jpeg_publish_count;
    uint64_t preview_drop_count;
    int64_t jpeg_timestamp_us;
    bool snapshot_busy;
    bool snapshot_request_pending;
    bool snapshot_active;
    bool pipeline_quiescent;
    uint64_t snapshot_request_count;
    uint64_t snapshot_success_count;
    uint64_t snapshot_error_count;
    uint64_t raw_snapshot_request_count;
    uint64_t raw_snapshot_success_count;
    uint64_t jpeg_snapshot_request_count;
    uint64_t jpeg_snapshot_success_count;
    int64_t last_snapshot_timestamp_us;
    uint64_t last_snapshot_total_us;
    uint64_t last_snapshot_jpeg_us;
    /* capture: STREAMON through successful source DQBUF. */
    uint64_t capture_us_latest;
    uint64_t capture_us_max;
    uint64_t capture_us_average;
    /* downscale: source mmap to the fixed output raw slot. */
    uint64_t downscale_us_latest;
    uint64_t downscale_us_max;
    uint64_t downscale_us_average;
    /* jpeg: JPEG encoding only; raw-only requests do not add samples. */
    uint64_t jpeg_us_latest;
    uint64_t jpeg_us_max;
    uint64_t jpeg_us_average;
    /* snapshot_total: complete lifetime of one accepted request. */
    uint64_t snapshot_total_us_latest;
    uint64_t snapshot_total_us_max;
    uint64_t snapshot_total_us_average;
    size_t raw_slot_capacity_each;
    size_t raw_slot_total_bytes;
    uint32_t raw_slot_lease_counts[REHAB_FOLLOW_CAMERA_RAW_SLOTS];
    size_t jpeg_slot_capacity_each;
    size_t jpeg_slot_total_bytes;
    uint32_t jpeg_slot_lease_counts[REHAB_FOLLOW_CAMERA_JPEG_SLOTS];
    uint32_t preview_interval_ms;
    size_t psram_free_bytes;
    size_t psram_min_free_bytes;
    size_t psram_largest_free_block;
    size_t internal_free_bytes;
    size_t internal_min_free_bytes;
    size_t internal_largest_free_block;
} rehab_follow_camera_status_t;

typedef struct {
    const uint8_t *data;
    size_t length;
    size_t capacity;
    uint64_t generation;
    int64_t timestamp_us;
    uint32_t lease_token;
} rehab_follow_preview_frame_t;

typedef struct {
    const uint8_t *data;
    size_t length;
    uint32_t width;
    uint32_t height;
    uint32_t stride;
    rehab_follow_camera_pixel_format_t pixel_format;
    rehab_follow_camera_byte_order_t byte_order;
    uint64_t generation;
    int64_t timestamp_us;
    uint32_t lease_token;
} rehab_follow_camera_raw_frame_t;

esp_err_t rehab_follow_camera_init(const rehab_follow_camera_config_t *config);
esp_err_t rehab_follow_camera_start(void);
esp_err_t rehab_follow_camera_stop(void);
void rehab_follow_camera_deinit(void);
bool rehab_follow_camera_is_ready(void);
esp_err_t rehab_follow_camera_get_status(rehab_follow_camera_status_t *status);

esp_err_t rehab_follow_camera_snapshot_acquire(rehab_follow_preview_frame_t *frame,
                                               uint32_t timeout_ms);
esp_err_t rehab_follow_camera_raw_snapshot_acquire(
    rehab_follow_camera_raw_frame_t *frame,
    uint32_t timeout_ms);
void rehab_follow_camera_raw_snapshot_release(rehab_follow_camera_raw_frame_t *frame);
esp_err_t rehab_follow_camera_preview_acquire(rehab_follow_preview_frame_t *frame);
void rehab_follow_camera_preview_release(rehab_follow_preview_frame_t *frame);

const char *rehab_follow_camera_state_name(rehab_follow_camera_state_t state);

#ifdef __cplusplus
}
#endif
