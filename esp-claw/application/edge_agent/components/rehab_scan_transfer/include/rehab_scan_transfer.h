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

#define REHAB_SCAN_TRANSFER_SCAN_ID_MAX        129
#define REHAB_SCAN_TRANSFER_SERVICE_JOB_ID_MAX 37
#define REHAB_SCAN_TRANSFER_PIPELINE_JOB_ID_MAX 129
#define REHAB_SCAN_TRANSFER_PATH_MAX           384
#define REHAB_SCAN_TRANSFER_ERROR_CODE_MAX     64
#define REHAB_SCAN_TRANSFER_SHA256_HEX_MAX     65
#define REHAB_SCAN_TRANSFER_DECISION_MAX_BYTES 65536U

typedef struct rehab_scan_transfer *rehab_scan_transfer_handle_t;

typedef struct {
    const char *scan_root_path;
    const char *result_root_path;
    const char *server_base_url;
    const char *latest_scene_result_path;
    uint32_t upload_timeout_ms;
    uint32_t request_timeout_ms;
    uint32_t poll_interval_ms;
    uint32_t total_wait_timeout_ms;
    size_t stream_buffer_bytes;
    size_t max_upload_bytes;
    size_t max_json_part_bytes;
    size_t max_result_bytes;
    size_t max_decision_bytes;
    uint32_t max_frames;
    uint32_t max_objects;
    uint32_t worker_stack_size;
    UBaseType_t worker_priority;
} rehab_scan_transfer_config_t;

#define REHAB_SCAN_TRANSFER_CONFIG_DEFAULT(scan_root, result_root) { \
    .scan_root_path = (scan_root), \
    .result_root_path = (result_root), \
    .server_base_url = "http://192.168.137.1:8030", \
    .latest_scene_result_path = NULL, \
    .upload_timeout_ms = 30000U, \
    .request_timeout_ms = 10000U, \
    .poll_interval_ms = 5000U, \
    .total_wait_timeout_ms = 300000U, \
    .stream_buffer_bytes = 16384U, \
    .max_upload_bytes = 6291456U, \
    .max_json_part_bytes = 65536U, \
    .max_result_bytes = 262144U, \
    .max_decision_bytes = REHAB_SCAN_TRANSFER_DECISION_MAX_BYTES, \
    .max_frames = 11U, \
    .max_objects = 100U, \
    .worker_stack_size = 12288U, \
    .worker_priority = 5U, \
}

typedef enum {
    REHAB_SCAN_TRANSFER_STATE_IDLE = 0,
    REHAB_SCAN_TRANSFER_STATE_UPLOADING,
    REHAB_SCAN_TRANSFER_STATE_WAITING_PROCESSING,
    REHAB_SCAN_TRANSFER_STATE_RECEIVING_RESULT,
    REHAB_SCAN_TRANSFER_STATE_COMPLETED,
    REHAB_SCAN_TRANSFER_STATE_FAILED,
    REHAB_SCAN_TRANSFER_STATE_ABORTING,
} rehab_scan_transfer_state_t;

typedef enum {
    REHAB_SCAN_TRANSFER_MODE_FULL_PIPELINE = 0,
    REHAB_SCAN_TRANSFER_MODE_UPLOAD_ONLY,
    REHAB_SCAN_TRANSFER_MODE_AUTO_DECISION,
} rehab_scan_transfer_mode_t;

typedef struct {
    rehab_scan_transfer_state_t state;
    char scan_id[REHAB_SCAN_TRANSFER_SCAN_ID_MAX];
    char service_job_id[REHAB_SCAN_TRANSFER_SERVICE_JOB_ID_MAX];
    char pipeline_job_id[REHAB_SCAN_TRANSFER_PIPELINE_JOB_ID_MAX];
    uint64_t bytes_sent;
    uint64_t result_bytes;
    uint32_t generation;
    uint32_t poll_count;
    int last_http_status;
    esp_err_t last_error;
    int last_errno;
    bool aborted;
    char error_code[REHAB_SCAN_TRANSFER_ERROR_CODE_MAX];
    char package_sha256[REHAB_SCAN_TRANSFER_SHA256_HEX_MAX];
    char result_path[REHAB_SCAN_TRANSFER_PATH_MAX];
} rehab_scan_transfer_status_t;

esp_err_t rehab_scan_transfer_create(
    const rehab_scan_transfer_config_t *config,
    rehab_scan_transfer_handle_t *out_handle);
esp_err_t rehab_scan_transfer_start(
    rehab_scan_transfer_handle_t handle,
    const char *scan_id_or_latest,
    char *out_scan_id,
    size_t out_scan_id_size);
esp_err_t rehab_scan_transfer_start_mode(
    rehab_scan_transfer_handle_t handle,
    const char *scan_id_or_latest,
    rehab_scan_transfer_mode_t mode,
    char *out_scan_id,
    size_t out_scan_id_size);
esp_err_t rehab_scan_transfer_start_auto_decision(
    rehab_scan_transfer_handle_t handle,
    const char *scan_id,
    uint32_t generation,
    char *out_scan_id,
    size_t out_scan_id_size);
esp_err_t rehab_scan_transfer_get_status(
    rehab_scan_transfer_handle_t handle,
    rehab_scan_transfer_status_t *out_status);
esp_err_t rehab_scan_transfer_abort(rehab_scan_transfer_handle_t handle);
esp_err_t rehab_scan_transfer_console_register(rehab_scan_transfer_handle_t handle);
void rehab_scan_transfer_delete(rehab_scan_transfer_handle_t handle);
const char *rehab_scan_transfer_state_name(rehab_scan_transfer_state_t state);

#ifdef __cplusplus
}
#endif
