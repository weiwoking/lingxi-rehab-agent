/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "rehab_score_bridge.h"

#define REHAB_SCORE_SESSION_ROOT       "/sdcard/rehab_sessions"
#define REHAB_SCORE_TASK_MAX_BYTES     (16U * 1024U)
#define REHAB_SCORE_CSV_MAX_BYTES      (128U * 1024U * 1024U)
#define REHAB_SCORE_STATUS_MAX_BYTES   (4U * 1024U)
#define REHAB_SCORE_RESULT_MAX_BYTES   (64U * 1024U)
#define REHAB_SCORE_RESPONSE_MAX_BYTES (8U * 1024U)
#define REHAB_SCORE_CHUNK_BYTES        (16U * 1024U)
#define REHAB_SCORE_QUEUE_LENGTH       8U
#define REHAB_SCORE_RETRY_INTERVAL_MS  10000U
#define REHAB_SCORE_MAX_ATTEMPTS       5U
#define REHAB_SCORE_POLL_TIMEOUT_MS    (30U * 60U * 1000U)
#define REHAB_SCORE_WORKER_PRIORITY    2U
#define REHAB_SCORE_WORKER_STACK       6144U
#define REHAB_SCORE_AUTO_PORT          8020U

#define REHAB_SCORE_CSV_FILE_LEN       32
#define REHAB_SCORE_PATH_LEN           96
#define REHAB_SCORE_URL_LEN            128
#define REHAB_SCORE_SHA256_HEX_LEN     65

typedef struct {
    char task_name[REHAB_SCORE_TASK_NAME_LEN];
    char csv_file[REHAB_SCORE_CSV_FILE_LEN];
    char start_time[REHAB_SCORE_TIMESTAMP_LEN];
    char end_time[REHAB_SCORE_TIMESTAMP_LEN];
    uint64_t start_log_seq;
    uint64_t end_log_seq;
    char end_reason[24];
} rehab_score_task_t;

typedef struct {
    char session_id[REHAB_SCORE_SESSION_ID_LEN];
    char session_path[REHAB_SCORE_PATH_LEN];
    char task_path[REHAB_SCORE_PATH_LEN];
    char csv_path[REHAB_SCORE_PATH_LEN];
    uint64_t csv_size;
    char csv_sha256[REHAB_SCORE_SHA256_HEX_LEN];
    rehab_score_task_t task;
} rehab_score_session_t;

typedef struct {
    char job_id[REHAB_SCORE_JOB_ID_LEN];
    rehab_score_state_t state;
    bool upload_required;
    char error_code[REHAB_SCORE_LAST_ERROR_LEN];
} rehab_score_job_response_t;

typedef enum {
    REHAB_SCORE_RECOVERY_CREATE_PENDING = 0,
    REHAB_SCORE_RECOVERY_SUBMIT,
    REHAB_SCORE_RECOVERY_RESET_PENDING,
    REHAB_SCORE_RECOVERY_POLL,
    REHAB_SCORE_RECOVERY_VALIDATE_RESULT,
    REHAB_SCORE_RECOVERY_HOLD_FAILED,
} rehab_score_recovery_action_t;

typedef enum {
    REHAB_SCORE_URL_SOURCE_MANUAL = 0,
    REHAB_SCORE_URL_SOURCE_STA_GATEWAY,
} rehab_score_url_source_t;

bool rehab_score_contract_session_id(const char *value);
bool rehab_score_contract_csv_file(const char *value);
bool rehab_score_contract_base_url(const char *value,
                                   char *normalized,
                                   size_t normalized_size);
esp_err_t rehab_score_contract_parse_task(const char *json,
                                          rehab_score_task_t *out_task);
esp_err_t rehab_score_contract_parse_status(const char *json,
                                            rehab_score_status_t *out_status);
esp_err_t rehab_score_contract_parse_job_response(const char *json,
                                                  const char *session_id,
                                                  rehab_score_job_response_t *out);
esp_err_t rehab_score_contract_parse_job_status(const char *json,
                                                const char *session_id,
                                                rehab_score_job_response_t *out);
esp_err_t rehab_score_contract_parse_error_code(
    const char *json,
    char error_code[REHAB_SCORE_LAST_ERROR_LEN]);
esp_err_t rehab_score_contract_validate_result(const char *json,
                                               size_t length,
                                               const char *session_id,
                                               const char *job_id,
                                               const char *task_name);
esp_err_t rehab_score_contract_parse_result(const char *json,
                                            size_t length,
                                            const char *session_id,
                                            const char *job_id,
                                            const char *task_name,
                                            rehab_score_query_result_t *out_result);
rehab_score_recovery_action_t rehab_score_contract_recovery_action(
    bool status_exists,
    rehab_score_state_t state);
esp_err_t rehab_score_contract_validate_health(const char *json,
                                               size_t length,
                                               bool *out_ready);

bool rehab_score_gateway_is_private(uint8_t first_octet,
                                    uint8_t second_octet);
esp_err_t rehab_score_format_gateway_url(uint8_t first_octet,
                                         uint8_t second_octet,
                                         uint8_t third_octet,
                                         uint8_t fourth_octet,
                                         char *out_url,
                                         size_t out_size);
esp_err_t rehab_score_resolve_base_url(char *out_url,
                                       size_t out_size,
                                       rehab_score_url_source_t *out_source);
const char *rehab_score_url_source_to_string(rehab_score_url_source_t source);

esp_err_t rehab_score_storage_prepare_session(const char *session_id,
                                              rehab_score_session_t *out_session);
esp_err_t rehab_score_storage_load_session_metadata(const char *session_id,
                                                    rehab_score_session_t *out_session);
esp_err_t rehab_score_storage_load_status(const char *session_path,
                                          rehab_score_status_t *out_status,
                                          bool *out_exists);
esp_err_t rehab_score_storage_write_status(const char *session_path,
                                           const rehab_score_status_t *status);
esp_err_t rehab_score_storage_write_result(const rehab_score_session_t *session,
                                           const char *json,
                                           size_t length,
                                           const char *job_id);
esp_err_t rehab_score_storage_validate_result_file(const rehab_score_session_t *session,
                                                   const char *job_id);
esp_err_t rehab_score_storage_recover_atomic_files(const rehab_score_session_t *session);
esp_err_t rehab_score_storage_read_task_bytes(const rehab_score_session_t *session,
                                              char **out_bytes,
                                              size_t *out_length);
esp_err_t rehab_score_storage_read_result(const rehab_score_session_t *session,
                                          const char *job_id,
                                          rehab_score_query_result_t *out_result);
esp_err_t rehab_score_storage_find_latest(char out_session_id[REHAB_SCORE_SESSION_ID_LEN]);
esp_err_t rehab_score_storage_format_now(char out[REHAB_SCORE_TIMESTAMP_LEN]);

bool rehab_score_bridge_is_initialized(void);

esp_err_t rehab_score_http_create_job(const char *base_url,
                                      const rehab_score_session_t *session,
                                      rehab_score_job_response_t *out,
                                      char error_code[REHAB_SCORE_LAST_ERROR_LEN]);
esp_err_t rehab_score_http_check_health(const char *base_url,
                                        char error_code[REHAB_SCORE_LAST_ERROR_LEN]);
esp_err_t rehab_score_http_upload_csv(const char *base_url,
                                      const rehab_score_session_t *session,
                                      rehab_score_job_response_t *out,
                                      char error_code[REHAB_SCORE_LAST_ERROR_LEN]);
esp_err_t rehab_score_http_get_status(const char *base_url,
                                      const char *job_id,
                                      rehab_score_job_response_t *out,
                                      char error_code[REHAB_SCORE_LAST_ERROR_LEN]);
esp_err_t rehab_score_http_get_result(const char *base_url,
                                      const rehab_score_session_t *session,
                                      const char *job_id,
                                      char **out_json,
                                      size_t *out_length,
                                      char error_code[REHAB_SCORE_LAST_ERROR_LEN]);
