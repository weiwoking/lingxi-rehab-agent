/* SPDX-License-Identifier: Apache-2.0 */
#include "rehab_score_bridge_priv.h"

#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "rehab_sensor_recording.h"

static const char *TAG = "rehab_score";

typedef struct {
    char session_id[REHAB_SCORE_SESSION_ID_LEN];
} score_event_t;

static QueueHandle_t s_queue;
static TaskHandle_t s_worker_task;
static bool s_initialized;
static rehab_score_success_observer_t s_success_observer;
static void *s_success_observer_ctx;

static esp_err_t status_timestamp(const rehab_score_session_t *session,
                                  char out[REHAB_SCORE_TIMESTAMP_LEN])
{
    esp_err_t err = rehab_score_storage_format_now(out);
    if (err == ESP_OK) {
        return ESP_OK;
    }
    if (!session || !session->task.end_time[0] ||
            strlcpy(out, session->task.end_time, REHAB_SCORE_TIMESTAMP_LEN) >=
            REHAB_SCORE_TIMESTAMP_LEN) {
        return err;
    }
    return ESP_OK;
}

esp_err_t rehab_score_bridge_set_success_observer(
    rehab_score_success_observer_t observer,
    void *user_ctx)
{
    if (observer == NULL || s_initialized || s_success_observer != NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    s_success_observer = observer;
    s_success_observer_ctx = user_ctx;
    return ESP_OK;
}

static esp_err_t update_status(const rehab_score_session_t *session,
                               rehab_score_status_t *status,
                               rehab_score_state_t state,
                               const char *error)
{
    status->state = state;
    strlcpy(status->last_error, error ? error : "", sizeof(status->last_error));
    esp_err_t err = status_timestamp(session, status->updated_at);
    if (err == ESP_OK) {
        err = rehab_score_storage_write_status(session->session_path, status);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "status write failed for %.16s: %s",
                 session->session_id, esp_err_to_name(err));
    }
    return err;
}

static esp_err_t finish_failed(const rehab_score_session_t *session,
                               rehab_score_status_t *status,
                               const char *error)
{
    return update_status(session, status, REHAB_SCORE_STATE_FAILED, error);
}

static bool state_can_poll(rehab_score_state_t state)
{
    return state == REHAB_SCORE_STATE_ACCEPTED ||
           state == REHAB_SCORE_STATE_SCORING;
}

static bool submission_error_is_terminal(const char *error_code)
{
    return error_code &&
           (strcmp(error_code, "invalid_task_contract") == 0 ||
            strcmp(error_code, "session_content_conflict") == 0 ||
            strcmp(error_code, "csv_size_mismatch") == 0 ||
            strcmp(error_code, "csv_sha256_mismatch") == 0 ||
            strcmp(error_code, "invalid_csv_header") == 0 ||
            strcmp(error_code, "invalid_csv_row") == 0 ||
            strcmp(error_code, "invalid_csv_log_seq") == 0 ||
            strcmp(error_code, "csv_log_seq_mismatch") == 0 ||
            strcmp(error_code, "csv_empty") == 0);
}

static esp_err_t download_result(const char *base_url,
                                 const rehab_score_session_t *session,
                                 rehab_score_status_t *status,
                                 char error_code[REHAB_SCORE_LAST_ERROR_LEN])
{
    char *json = NULL;
    size_t length = 0;
    bool average_available = false;
    double average_score = 0.0;
    esp_err_t err = rehab_score_http_get_result(base_url,
                                                session,
                                                status->pc_job_id,
                                                &json,
                                                &length,
                                                error_code);
    if (err == ESP_OK) {
        err = rehab_score_storage_write_result(session, json, length, status->pc_job_id);
        if (err != ESP_OK) {
            strlcpy(error_code, "sd_write_failed", REHAB_SCORE_LAST_ERROR_LEN);
        } else if (s_success_observer != NULL) {
            rehab_score_query_result_t *parsed = calloc(1, sizeof(*parsed));
            if (parsed != NULL) {
                esp_err_t parse_err = rehab_score_contract_parse_result(
                    json, length, session->session_id, status->pc_job_id,
                    session->task.task_name, parsed);
                if (parse_err == ESP_OK && parsed->valid &&
                        parsed->average_score_available) {
                    average_available = true;
                    average_score = parsed->average_score;
                } else {
                    ESP_LOGW(TAG,
                             "fatigue observer skipped for %.16s: %s",
                             session->session_id,
                             parse_err != ESP_OK ? "result invalid" :
                             (!parsed->valid ? "score invalid" :
                              "average unavailable"));
                }
                free(parsed);
            } else {
                ESP_LOGW(TAG,
                         "fatigue observer skipped for %.16s: out of memory",
                         session->session_id);
            }
        }
    }
    free(json);
    if (err == ESP_OK) {
        err = update_status(session, status, REHAB_SCORE_STATE_SUCCEEDED, NULL);
    }
    if (err == ESP_OK && average_available && s_success_observer != NULL) {
        s_success_observer(session->session_id,
                           average_score,
                           s_success_observer_ctx);
    }
    return err;
}

static esp_err_t poll_job(const rehab_score_session_t *session,
                          rehab_score_status_t *status)
{
    int64_t started_us = esp_timer_get_time();
    while ((uint64_t)((esp_timer_get_time() - started_us) / 1000) <
           REHAB_SCORE_POLL_TIMEOUT_MS) {
        char base_url[REHAB_SCORE_URL_LEN] = {0};
        rehab_score_url_source_t url_source = REHAB_SCORE_URL_SOURCE_STA_GATEWAY;
        esp_err_t err = rehab_score_resolve_base_url(base_url,
                                                     sizeof(base_url),
                                                     &url_source);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "score endpoint unavailable while polling: source=%s error=%s",
                     rehab_score_url_source_to_string(url_source),
                     esp_err_to_name(err));
            vTaskDelay(pdMS_TO_TICKS(REHAB_SCORE_RETRY_INTERVAL_MS));
            continue;
        }
        rehab_score_job_response_t remote = {0};
        char error_code[REHAB_SCORE_LAST_ERROR_LEN] = {0};
        err = rehab_score_http_get_status(base_url,
                                          status->pc_job_id,
                                          &remote,
                                          error_code);
        if (err != ESP_OK) {
            if (strcmp(error_code, "job_not_found") == 0) {
                return ESP_ERR_NOT_FOUND;
            }
            if (strcmp(error_code, "job_status_failed") == 0) {
                return finish_failed(session, status, error_code);
            }
            vTaskDelay(pdMS_TO_TICKS(REHAB_SCORE_RETRY_INTERVAL_MS));
            continue;
        }
        if (remote.state == REHAB_SCORE_STATE_FAILED) {
            return finish_failed(session,
                                 status,
                                 remote.error_code[0]
                                     ? remote.error_code
                                     : "scoring_failed");
        }
        if (remote.state == REHAB_SCORE_STATE_SUCCEEDED) {
            err = download_result(base_url, session, status, error_code);
            if (err == ESP_OK) {
                return ESP_OK;
            }
            if (strcmp(error_code, "result_contract_invalid") == 0 ||
                    strcmp(error_code, "sd_write_failed") == 0) {
                return finish_failed(session, status, error_code);
            }
            vTaskDelay(pdMS_TO_TICKS(REHAB_SCORE_RETRY_INTERVAL_MS));
            continue;
        }
        if (state_can_poll(remote.state) && remote.state != status->state) {
            err = update_status(session, status, remote.state, NULL);
            if (err != ESP_OK) {
                return err;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(REHAB_SCORE_RETRY_INTERVAL_MS));
    }
    return finish_failed(session, status, "scoring_timeout");
}

static esp_err_t submit_job(const rehab_score_session_t *session,
                            rehab_score_status_t *status)
{
    while (status->attempt < REHAB_SCORE_MAX_ATTEMPTS) {
        status->attempt++;
        esp_err_t err = update_status(session,
                                      status,
                                      REHAB_SCORE_STATE_UPLOADING,
                                      NULL);
        if (err != ESP_OK) {
            return err;
        }

        rehab_score_job_response_t remote = {0};
        char error_code[REHAB_SCORE_LAST_ERROR_LEN] = {0};
        char base_url[REHAB_SCORE_URL_LEN] = {0};
        rehab_score_url_source_t url_source = REHAB_SCORE_URL_SOURCE_STA_GATEWAY;
        err = rehab_score_resolve_base_url(base_url,
                                           sizeof(base_url),
                                           &url_source);
        if (err != ESP_OK) {
            strlcpy(error_code,
                    url_source == REHAB_SCORE_URL_SOURCE_STA_GATEWAY
                        ? "pc_gateway_unavailable"
                        : "job_create_failed",
                    sizeof(error_code));
        } else {
            ESP_LOGI(TAG, "score endpoint source=%s url=%s",
                     rehab_score_url_source_to_string(url_source), base_url);
            err = rehab_score_http_check_health(base_url, error_code);
        }
        if (err == ESP_OK) {
            err = rehab_score_http_create_job(base_url, session, &remote, error_code);
        }
        if (err == ESP_OK) {
            strlcpy(status->pc_job_id, remote.job_id, sizeof(status->pc_job_id));
            if (remote.upload_required) {
                err = rehab_score_http_upload_csv(base_url,
                                                  session,
                                                  &remote,
                                                  error_code);
            }
        }
        if (err == ESP_OK) {
            rehab_score_state_t next = state_can_poll(remote.state)
                                           ? remote.state
                                           : REHAB_SCORE_STATE_ACCEPTED;
            err = update_status(session, status, next, NULL);
            if (err != ESP_OK) {
                return err;
            }
            err = poll_job(session, status);
            if (err != ESP_ERR_NOT_FOUND) {
                return err;
            }
            status->pc_job_id[0] = '\0';
            strlcpy(error_code, "job_not_found", sizeof(error_code));
        }
        if (submission_error_is_terminal(error_code)) {
            return finish_failed(session, status, error_code);
        }

        if (status->attempt >= REHAB_SCORE_MAX_ATTEMPTS) {
            return finish_failed(session,
                                 status,
                                 error_code[0] ? error_code : "job_create_failed");
        }
        status->pc_job_id[0] = '\0';
        err = update_status(session,
                            status,
                            REHAB_SCORE_STATE_PENDING,
                            error_code[0] ? error_code : "job_create_failed");
        if (err != ESP_OK) {
            return err;
        }
        vTaskDelay(pdMS_TO_TICKS(REHAB_SCORE_RETRY_INTERVAL_MS));
    }
    return finish_failed(session, status, "job_create_failed");
}

static esp_err_t session_path_for(const char *session_id,
                                  char output[REHAB_SCORE_PATH_LEN])
{
    int written = snprintf(output,
                           REHAB_SCORE_PATH_LEN,
                           "%s/%s",
                           REHAB_SCORE_SESSION_ROOT,
                           session_id);
    return written > 0 && written < REHAB_SCORE_PATH_LEN
               ? ESP_OK
               : ESP_ERR_INVALID_SIZE;
}

static esp_err_t process_session(const char *session_id, bool validate_succeeded)
{
    char session_path[REHAB_SCORE_PATH_LEN];
    esp_err_t err = session_path_for(session_id, session_path);
    if (err != ESP_OK) {
        return err;
    }
    rehab_score_status_t *status = calloc(1, sizeof(*status));
    if (!status) {
        return ESP_ERR_NO_MEM;
    }
    bool pre_exists = false;
    err = rehab_score_storage_load_status(session_path, status, &pre_exists);
    if (err == ESP_OK && pre_exists && status->state == REHAB_SCORE_STATE_FAILED) {
        free(status);
        return ESP_OK;
    }
    if (err == ESP_OK && pre_exists &&
            status->state == REHAB_SCORE_STATE_SUCCEEDED && !validate_succeeded) {
        free(status);
        return ESP_OK;
    }

    rehab_score_session_t *session = calloc(1, sizeof(*session));
    if (!session) {
        free(status);
        return ESP_ERR_NO_MEM;
    }
    err = rehab_score_storage_prepare_session(session_id, session);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "skip invalid finalized session %.16s: %s",
                 session_id, esp_err_to_name(err));
        free(session);
        free(status);
        return err;
    }
    (void)rehab_score_storage_recover_atomic_files(session);

    memset(status, 0, sizeof(*status));
    bool exists = false;
    err = rehab_score_storage_load_status(session->session_path, status, &exists);
    if (err != ESP_OK) {
        free(session);
        free(status);
        return err;
    }
    rehab_score_recovery_action_t recovery =
        rehab_score_contract_recovery_action(exists, status->state);
    if (recovery == REHAB_SCORE_RECOVERY_CREATE_PENDING) {
        err = update_status(session, status, REHAB_SCORE_STATE_PENDING, NULL);
    } else if (recovery == REHAB_SCORE_RECOVERY_RESET_PENDING) {
        status->pc_job_id[0] = '\0';
        err = update_status(session,
                            status,
                            REHAB_SCORE_STATE_PENDING,
                            "p4_restart_during_upload");
    } else if (recovery == REHAB_SCORE_RECOVERY_VALIDATE_RESULT) {
        err = rehab_score_storage_validate_result_file(session, status->pc_job_id);
        if (err != ESP_OK) {
            err = finish_failed(session, status, "result_contract_invalid");
        }
        free(session);
        free(status);
        return err;
    } else if (recovery == REHAB_SCORE_RECOVERY_HOLD_FAILED) {
        free(session);
        free(status);
        return ESP_OK;
    }
    if (err != ESP_OK) {
        free(session);
        free(status);
        return err;
    }
    if (state_can_poll(status->state) && status->pc_job_id[0]) {
        err = poll_job(session, status);
        if (err == ESP_ERR_NOT_FOUND) {
            status->pc_job_id[0] = '\0';
            err = update_status(session,
                                status,
                                REHAB_SCORE_STATE_PENDING,
                                "job_not_found");
            if (err == ESP_OK) {
                err = submit_job(session, status);
            }
        }
    } else {
        err = submit_job(session, status);
    }
    free(session);
    free(status);
    return err;
}

static void scan_sessions(bool validate_succeeded)
{
    DIR *directory = opendir(REHAB_SCORE_SESSION_ROOT);
    if (!directory) {
        return;
    }
    struct dirent *entry;
    while ((entry = readdir(directory)) != NULL) {
        if (rehab_score_contract_session_id(entry->d_name)) {
            (void)process_session(entry->d_name, validate_succeeded);
        }
    }
    closedir(directory);
}

static void score_worker(void *context)
{
    (void)context;
    scan_sessions(true);
    score_event_t event;
    for (;;) {
        if (xQueueReceive(s_queue,
                          &event,
                          pdMS_TO_TICKS(REHAB_SCORE_RETRY_INTERVAL_MS)) == pdTRUE) {
            (void)process_session(event.session_id, true);
        } else {
            scan_sessions(false);
        }
    }
}

esp_err_t rehab_score_bridge_notify_finalized(const char *session_id,
                                               const char *session_path)
{
    if (!s_initialized || !s_queue) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!rehab_score_contract_session_id(session_id) || !session_path) {
        return ESP_ERR_INVALID_ARG;
    }
    char expected[REHAB_SCORE_PATH_LEN];
    if (session_path_for(session_id, expected) != ESP_OK ||
            strcmp(expected, session_path) != 0) {
        return ESP_ERR_INVALID_ARG;
    }
    score_event_t event = {0};
    strlcpy(event.session_id, session_id, sizeof(event.session_id));
    return xQueueSend(s_queue, &event, 0) == pdTRUE ? ESP_OK : ESP_ERR_NO_MEM;
}

static void recorder_finalized(const char *session_id,
                               const char *session_path,
                               void *user_context)
{
    (void)user_context;
    esp_err_t err = rehab_score_bridge_notify_finalized(session_id, session_path);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "finalize event dropped for %.16s: %s",
                 session_id ? session_id : "", esp_err_to_name(err));
    }
}

bool rehab_score_bridge_is_initialized(void)
{
    return s_initialized;
}

esp_err_t rehab_score_bridge_get_status(const char *session_id,
                                        rehab_score_status_t *out_status)
{
    if (!rehab_score_contract_session_id(session_id) || !out_status) {
        return ESP_ERR_INVALID_ARG;
    }
    char path[REHAB_SCORE_PATH_LEN];
    esp_err_t err = session_path_for(session_id, path);
    bool exists = false;
    if (err == ESP_OK) {
        err = rehab_score_storage_load_status(path, out_status, &exists);
    }
    if (err == ESP_OK && exists) {
        strlcpy(out_status->session_id, session_id, sizeof(out_status->session_id));
    }
    return err == ESP_OK && !exists ? ESP_ERR_NOT_FOUND : err;
}

esp_err_t rehab_score_bridge_get_latest_status(rehab_score_status_t *out_status)
{
    if (!out_status) {
        return ESP_ERR_INVALID_ARG;
    }
    char session_id[REHAB_SCORE_SESSION_ID_LEN];
    esp_err_t err = rehab_score_storage_find_latest(session_id);
    return err == ESP_OK ? rehab_score_bridge_get_status(session_id, out_status) : err;
}

esp_err_t rehab_score_bridge_retry(const char *session_id)
{
    if (!s_initialized) {
        return ESP_ERR_INVALID_STATE;
    }
    rehab_score_status_t *status = calloc(1, sizeof(*status));
    if (!status) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = rehab_score_bridge_get_status(session_id, status);
    if (err != ESP_OK) {
        free(status);
        return err;
    }
    if (status->state != REHAB_SCORE_STATE_FAILED) {
        free(status);
        return ESP_ERR_INVALID_STATE;
    }
    rehab_score_session_t *session = calloc(1, sizeof(*session));
    if (!session) {
        free(status);
        return ESP_ERR_NO_MEM;
    }
    err = rehab_score_storage_load_session_metadata(session_id, session);
    if (err != ESP_OK) {
        free(session);
        free(status);
        return err;
    }
    status->state = REHAB_SCORE_STATE_PENDING;
    status->attempt = 0;
    status->pc_job_id[0] = '\0';
    status->last_error[0] = '\0';
    err = update_status(session, status, REHAB_SCORE_STATE_PENDING, NULL);
    if (err == ESP_OK) {
        err = rehab_score_bridge_notify_finalized(session_id, session->session_path);
    }
    free(session);
    free(status);
    return err;
}

esp_err_t rehab_score_bridge_init(void)
{
    if (s_initialized) {
        return ESP_OK;
    }
    s_queue = xQueueCreate(REHAB_SCORE_QUEUE_LENGTH, sizeof(score_event_t));
    if (!s_queue) {
        return ESP_ERR_NO_MEM;
    }
    s_initialized = true;
    if (xTaskCreate(score_worker,
                    "rehab_score",
                    REHAB_SCORE_WORKER_STACK,
                    NULL,
                    REHAB_SCORE_WORKER_PRIORITY,
                    &s_worker_task) != pdPASS) {
        s_initialized = false;
        vQueueDelete(s_queue);
        s_queue = NULL;
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = rehab_training_register_finalized_callback(recorder_finalized, NULL);
    if (err != ESP_OK) {
        s_initialized = false;
        vTaskDelete(s_worker_task);
        s_worker_task = NULL;
        vQueueDelete(s_queue);
        s_queue = NULL;
        return err;
    }
    ESP_LOGI(TAG, "score bridge initialized (manual-or-STA-gateway resolver)");
    return ESP_OK;
}
