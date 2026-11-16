// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "rehab_car.h"
#include "rehab_scan_capture.h"
#include "rehab_scan_transfer.h"

#ifdef __cplusplus
extern "C" {
#endif

#define REHAB_ENVIRONMENT_SCAN_DIRECTIONS        11U
#define REHAB_ENVIRONMENT_SCAN_WARMUP_FRAMES      5U
#define REHAB_ENVIRONMENT_SCAN_ROTATE_SPEED        3U
#define REHAB_ENVIRONMENT_SCAN_ROTATE_MS         500U
#define REHAB_ENVIRONMENT_SCAN_SETTLE_MS         200U
#define REHAB_ENVIRONMENT_SCAN_WORKER_STACK_SIZE 8192U
#define REHAB_ENVIRONMENT_SCAN_WORKER_PRIORITY     5U
#define REHAB_ENVIRONMENT_SCAN_ERROR_MAX          96U

typedef struct rehab_environment_scan *rehab_environment_scan_handle_t;

typedef enum {
    REHAB_ENVIRONMENT_SCAN_STATE_IDLE = 0,
    REHAB_ENVIRONMENT_SCAN_STATE_ACQUIRING_LEASE,
    REHAB_ENVIRONMENT_SCAN_STATE_WARMING_CAMERA,
    REHAB_ENVIRONMENT_SCAN_STATE_ROTATING,
    REHAB_ENVIRONMENT_SCAN_STATE_STOPPING,
    REHAB_ENVIRONMENT_SCAN_STATE_SETTLING,
    REHAB_ENVIRONMENT_SCAN_STATE_CAPTURING,
    REHAB_ENVIRONMENT_SCAN_STATE_WRITING,
    REHAB_ENVIRONMENT_SCAN_STATE_COMMITTING_SCAN,
    REHAB_ENVIRONMENT_SCAN_STATE_UPLOADING,
    REHAB_ENVIRONMENT_SCAN_STATE_ANALYZING,
    REHAB_ENVIRONMENT_SCAN_STATE_DECISION_READY,
    REHAB_ENVIRONMENT_SCAN_STATE_CANCELLING,
    REHAB_ENVIRONMENT_SCAN_STATE_CANCELLED,
    REHAB_ENVIRONMENT_SCAN_STATE_FAILED,
} rehab_environment_scan_state_t;

typedef struct {
    rehab_car_handle_t car;
    rehab_scan_capture_handle_t capture;
    rehab_scan_transfer_handle_t transfer;
    uint32_t worker_stack_size;
    UBaseType_t worker_priority;
} rehab_environment_scan_config_t;

#define REHAB_ENVIRONMENT_SCAN_CONFIG_DEFAULT(car_handle, capture_handle, transfer_handle) { \
    .car = (car_handle), \
    .capture = (capture_handle), \
    .transfer = (transfer_handle), \
    .worker_stack_size = REHAB_ENVIRONMENT_SCAN_WORKER_STACK_SIZE, \
    .worker_priority = REHAB_ENVIRONMENT_SCAN_WORKER_PRIORITY, \
}

typedef struct {
    rehab_environment_scan_state_t state;
    bool active;
    bool cancel_requested;
    char scan_id[REHAB_SCAN_CAPTURE_SCAN_ID_MAX];
    uint32_t generation;
    uint32_t direction_index;
    uint32_t directions_completed;
    uint32_t directions_total;
    uint32_t warmup_completed;
    uint32_t warmup_total;
    char final_path[REHAB_SCAN_CAPTURE_PATH_MAX];
    rehab_scan_transfer_state_t upload_state;
    bool retryable;
    char upload_error[REHAB_SCAN_TRANSFER_ERROR_CODE_MAX];
    uint64_t package_length;
    char package_sha256[REHAB_SCAN_TRANSFER_SHA256_HEX_MAX];
    char scene_result_path[REHAB_SCAN_TRANSFER_PATH_MAX];
    esp_err_t last_error_code;
    char last_error[REHAB_ENVIRONMENT_SCAN_ERROR_MAX];
} rehab_environment_scan_status_t;

esp_err_t rehab_environment_scan_create(
    const rehab_environment_scan_config_t *config,
    rehab_environment_scan_handle_t *out_handle);
esp_err_t rehab_environment_scan_start(
    rehab_environment_scan_handle_t handle,
    bool *out_busy,
    rehab_environment_scan_status_t *out_status);
esp_err_t rehab_environment_scan_get_status(
    rehab_environment_scan_handle_t handle,
    rehab_environment_scan_status_t *out_status);
esp_err_t rehab_environment_scan_cancel(
    rehab_environment_scan_handle_t handle,
    bool *out_already_idle,
    rehab_environment_scan_status_t *out_status);
esp_err_t rehab_environment_scan_console_register(
    rehab_environment_scan_handle_t handle);
esp_err_t rehab_environment_scan_register_capabilities(
    rehab_environment_scan_handle_t handle);
void rehab_environment_scan_delete(rehab_environment_scan_handle_t handle);
const char *rehab_environment_scan_state_name(
    rehab_environment_scan_state_t state);

#ifdef __cplusplus
}
#endif
