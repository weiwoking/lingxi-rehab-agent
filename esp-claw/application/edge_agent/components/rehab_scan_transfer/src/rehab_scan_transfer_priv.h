// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "rehab_scan_transfer.h"

#include <stdio.h>
#include <sys/stat.h>

#include "cJSON.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#define REHAB_SCAN_TRANSFER_MAX_PARTS          34U
#define REHAB_SCAN_TRANSFER_BOUNDARY_MAX       64U
#define REHAB_SCAN_TRANSFER_FIELD_MAX          48U
#define REHAB_SCAN_TRANSFER_RESPONSE_MAX       16384U
#define REHAB_SCAN_TRANSFER_IMAGE_BYTES        460800U
#define REHAB_SCAN_TRANSFER_SCAN_JSON_MAX      16384U

typedef struct {
    char path[REHAB_SCAN_TRANSFER_PATH_MAX];
    char field[REHAB_SCAN_TRANSFER_FIELD_MAX];
    const char *filename;
    const char *content_type;
    uint64_t size;
} rehab_scan_transfer_part_t;

typedef struct {
    char scan_id[REHAB_SCAN_TRANSFER_SCAN_ID_MAX];
    char scan_path[REHAB_SCAN_TRANSFER_PATH_MAX];
    uint32_t frame_count;
    size_t part_count;
    uint64_t raw_bytes;
    rehab_scan_transfer_part_t parts[REHAB_SCAN_TRANSFER_MAX_PARTS];
} rehab_scan_transfer_plan_t;

typedef struct {
    int http_status;
    char *body;
    size_t body_length;
} rehab_scan_transfer_http_response_t;

typedef struct {
    char service_job_id[REHAB_SCAN_TRANSFER_SERVICE_JOB_ID_MAX];
    char pipeline_job_id[REHAB_SCAN_TRANSFER_PIPELINE_JOB_ID_MAX];
    char scan_id[REHAB_SCAN_TRANSFER_SCAN_ID_MAX];
    char state[24];
    char pipeline_result_status[24];
} rehab_scan_transfer_job_t;

typedef struct {
    char scan_id[REHAB_SCAN_TRANSFER_SCAN_ID_MAX];
    char state[24];
    bool package_ready;
    size_t package_length;
    char package_sha256[REHAB_SCAN_TRANSFER_SHA256_HEX_MAX];
    char error_code[REHAB_SCAN_TRANSFER_ERROR_CODE_MAX];
} rehab_scan_transfer_decision_status_t;

struct rehab_scan_transfer {
    rehab_scan_transfer_config_t config;
    char scan_root_path[REHAB_SCAN_TRANSFER_PATH_MAX];
    char result_root_path[REHAB_SCAN_TRANSFER_PATH_MAX];
    char latest_scene_result_path[REHAB_SCAN_TRANSFER_PATH_MAX];
    char server_base_url[192];
    SemaphoreHandle_t lock;
    TaskHandle_t worker;
    volatile bool abort_requested;
    bool deleting;
    bool console_registered;
    rehab_scan_transfer_mode_t mode;
    uint32_t requested_generation;
    rehab_scan_transfer_status_t status;
    rehab_scan_transfer_plan_t plan;
};

bool rehab_scan_transfer_is_active(rehab_scan_transfer_state_t state);
bool rehab_scan_transfer_abort_requested(rehab_scan_transfer_handle_t handle);
void rehab_scan_transfer_add_sent(rehab_scan_transfer_handle_t handle, size_t bytes);
void rehab_scan_transfer_set_http(rehab_scan_transfer_handle_t handle, int status);
void rehab_scan_transfer_set_state(rehab_scan_transfer_handle_t handle,
                                   rehab_scan_transfer_state_t state);
void rehab_scan_transfer_fail(rehab_scan_transfer_handle_t handle,
                              esp_err_t error, int saved_errno,
                              const char *error_code);

esp_err_t rehab_scan_transfer_select_and_validate(
    rehab_scan_transfer_handle_t handle, const char *selector,
    rehab_scan_transfer_plan_t *out_plan);

esp_err_t rehab_scan_transfer_make_boundary(char *out, size_t out_size);
esp_err_t rehab_scan_transfer_multipart_length(
    const rehab_scan_transfer_plan_t *plan, const char *boundary,
    size_t maximum, size_t *out_length);
esp_err_t rehab_scan_transfer_write_multipart(
    rehab_scan_transfer_handle_t handle, void *http_client,
    const rehab_scan_transfer_plan_t *plan, const char *boundary,
    uint8_t *buffer, size_t buffer_size, size_t expected_length);

esp_err_t rehab_scan_transfer_http_upload(
    rehab_scan_transfer_handle_t handle,
    const rehab_scan_transfer_plan_t *plan, const char *boundary,
    size_t content_length, uint8_t *stream_buffer,
    rehab_scan_transfer_http_response_t *out_response);
esp_err_t rehab_scan_transfer_http_get_json(
    rehab_scan_transfer_handle_t handle, const char *suffix,
    rehab_scan_transfer_http_response_t *out_response);
esp_err_t rehab_scan_transfer_http_download_result(
    rehab_scan_transfer_handle_t handle, const char *suffix,
    const char *temporary_path, uint8_t *stream_buffer,
    size_t *out_length, int *out_http_status, int *out_errno);
esp_err_t rehab_scan_transfer_http_download_decision(
    rehab_scan_transfer_handle_t handle, const char *suffix,
    const char *temporary_path, uint8_t *stream_buffer,
    size_t expected_length, const char *expected_sha256,
    size_t *out_length, char out_sha256[REHAB_SCAN_TRANSFER_SHA256_HEX_MAX],
    int *out_http_status, int *out_errno);
void rehab_scan_transfer_http_response_free(
    rehab_scan_transfer_http_response_t *response);

esp_err_t rehab_scan_transfer_parse_job(const char *json, size_t length,
                                        const char *expected_scan_id,
                                        rehab_scan_transfer_job_t *out_job);
bool rehab_scan_transfer_parse_error_code(const char *json, size_t length,
                                          char *out_code, size_t out_size);
esp_err_t rehab_scan_transfer_validate_result(const char *json, size_t length,
                                              const char *scan_id,
                                              const char *pipeline_job_id,
                                              uint32_t max_objects);
esp_err_t rehab_scan_transfer_parse_decision_status(
    const char *json, size_t length, const char *expected_scan_id,
    rehab_scan_transfer_decision_status_t *out_status);
esp_err_t rehab_scan_transfer_validate_decision_package(
    const char *json, size_t length, const char *scan_id);

esp_err_t rehab_scan_transfer_prepare_result_paths(
    rehab_scan_transfer_handle_t handle, const char *scan_id,
    char *temporary, size_t temporary_size,
    char *final, size_t final_size, int *out_errno);
esp_err_t rehab_scan_transfer_load_file(const char *path, size_t maximum,
                                        char **out_bytes, size_t *out_length,
                                        int *out_errno);
esp_err_t rehab_scan_transfer_commit_result(const char *temporary,
                                            const char *final,
                                            int *out_errno);
esp_err_t rehab_scan_transfer_prepare_latest_scene_paths(
    rehab_scan_transfer_handle_t handle,
    char *temporary, size_t temporary_size,
    char *final, size_t final_size, int *out_errno);
esp_err_t rehab_scan_transfer_commit_latest_scene(
    const char *temporary, const char *final, int *out_errno);
