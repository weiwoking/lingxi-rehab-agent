// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"

#ifdef __cplusplus
extern "C" {
#endif

#define REHAB_SCAN_CAPTURE_SCAN_ID_MAX 129
#define REHAB_SCAN_CAPTURE_PATH_MAX    320
#define REHAB_SCAN_CAPTURE_DEFAULT_FRAMES      5U
#define REHAB_SCAN_CAPTURE_DEFAULT_INTERVAL_MS 1000U
#define REHAB_SCAN_CAPTURE_DEFAULT_CAMERA_TIMEOUT_MS 1000U
#define REHAB_SCAN_CAPTURE_DEFAULT_TOF_AFTER_TIMEOUT_MS 100U
#define REHAB_SCAN_CAPTURE_DEFAULT_WORKER_STACK_SIZE 8192U
#define REHAB_SCAN_CAPTURE_DEFAULT_WORKER_PRIORITY 5U

typedef struct rehab_scan_capture *rehab_scan_capture_handle_t;

typedef struct {
    const char *scan_root_path;
    uint32_t default_frame_count;
    uint32_t default_interval_ms;
    uint32_t camera_timeout_ms;
    uint32_t tof_after_timeout_ms;
    uint32_t worker_stack_size;
    UBaseType_t worker_priority;
} rehab_scan_capture_config_t;

#define REHAB_SCAN_CAPTURE_CONFIG_DEFAULT(root_path) { \
    .scan_root_path = (root_path), \
    .default_frame_count = REHAB_SCAN_CAPTURE_DEFAULT_FRAMES, \
    .default_interval_ms = REHAB_SCAN_CAPTURE_DEFAULT_INTERVAL_MS, \
    .camera_timeout_ms = REHAB_SCAN_CAPTURE_DEFAULT_CAMERA_TIMEOUT_MS, \
    .tof_after_timeout_ms = REHAB_SCAN_CAPTURE_DEFAULT_TOF_AFTER_TIMEOUT_MS, \
    .worker_stack_size = REHAB_SCAN_CAPTURE_DEFAULT_WORKER_STACK_SIZE, \
    .worker_priority = REHAB_SCAN_CAPTURE_DEFAULT_WORKER_PRIORITY, \
}

typedef struct {
    uint32_t frame_count;
    uint32_t interval_ms;
} rehab_scan_capture_request_t;

typedef esp_err_t (*rehab_scan_capture_prepare_cb_t)(void *ctx);
typedef esp_err_t (*rehab_scan_capture_before_frame_cb_t)(void *ctx,
                                                          uint32_t sequence);
typedef void (*rehab_scan_capture_frame_cb_t)(void *ctx, uint32_t sequence);
typedef void (*rehab_scan_capture_before_commit_cb_t)(void *ctx);

/**
 * Optional orchestration hooks around the existing pair/storage worker.
 * prepare runs before the incomplete directory is created. after_frame runs
 * only after image.rgb565, tof.json, and frame.json have been persisted.
 * before_commit runs after the complete manifest is written and immediately
 * before the incomplete directory is renamed to its final name.
 */
typedef struct {
    uint32_t frame_count;
    rehab_scan_capture_prepare_cb_t prepare;
    rehab_scan_capture_before_frame_cb_t before_frame;
    rehab_scan_capture_frame_cb_t before_write;
    rehab_scan_capture_frame_cb_t after_frame;
    rehab_scan_capture_before_commit_cb_t before_commit;
    void *ctx;
} rehab_scan_capture_guided_request_t;

typedef enum {
    REHAB_SCAN_CAPTURE_STATE_IDLE = 0,
    REHAB_SCAN_CAPTURE_STATE_CAPTURING,
    REHAB_SCAN_CAPTURE_STATE_COMMITTING,
    REHAB_SCAN_CAPTURE_STATE_COMPLETED,
    REHAB_SCAN_CAPTURE_STATE_FAILED,
    REHAB_SCAN_CAPTURE_STATE_ABORTING,
} rehab_scan_capture_state_t;

typedef struct {
    rehab_scan_capture_state_t state;
    char scan_id[REHAB_SCAN_CAPTURE_SCAN_ID_MAX];
    uint32_t requested_frames;
    uint32_t committed_frames;
    esp_err_t last_error;
    int last_errno;
    uint32_t failed_frame_sequence;
    bool aborted;
    char final_path[REHAB_SCAN_CAPTURE_PATH_MAX];
} rehab_scan_capture_status_t;

esp_err_t rehab_scan_capture_create(
    const rehab_scan_capture_config_t *config,
    rehab_scan_capture_handle_t *out_handle);
esp_err_t rehab_scan_capture_start(
    rehab_scan_capture_handle_t handle,
    const rehab_scan_capture_request_t *request,
    char *out_scan_id,
    size_t out_scan_id_size);
esp_err_t rehab_scan_capture_start_guided(
    rehab_scan_capture_handle_t handle,
    const rehab_scan_capture_guided_request_t *request,
    char *out_scan_id,
    size_t out_scan_id_size);
esp_err_t rehab_scan_capture_get_status(
    rehab_scan_capture_handle_t handle,
    rehab_scan_capture_status_t *out_status);
esp_err_t rehab_scan_capture_abort(rehab_scan_capture_handle_t handle);
esp_err_t rehab_scan_capture_console_register(rehab_scan_capture_handle_t handle);
void rehab_scan_capture_delete(rehab_scan_capture_handle_t handle);
const char *rehab_scan_capture_state_name(rehab_scan_capture_state_t state);

#ifdef __cplusplus
}
#endif
