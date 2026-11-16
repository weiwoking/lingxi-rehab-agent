// SPDX-License-Identifier: Apache-2.0
#include "rehab_scan_transfer_priv.h"

#include <errno.h>
#include <inttypes.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "wifi_manager.h"

static const char *TAG = "scan_transfer";

static bool lock_take(rehab_scan_transfer_handle_t handle)
{
    return xSemaphoreTake(handle->lock, portMAX_DELAY) == pdTRUE;
}

bool rehab_scan_transfer_is_active(rehab_scan_transfer_state_t state)
{
    return state == REHAB_SCAN_TRANSFER_STATE_UPLOADING ||
           state == REHAB_SCAN_TRANSFER_STATE_WAITING_PROCESSING ||
           state == REHAB_SCAN_TRANSFER_STATE_RECEIVING_RESULT ||
           state == REHAB_SCAN_TRANSFER_STATE_ABORTING;
}

const char *rehab_scan_transfer_state_name(rehab_scan_transfer_state_t state)
{
    static const char *const names[] = {
        "idle", "uploading", "waiting_processing", "receiving_result",
        "completed", "failed", "aborting",
    };
    return state <= REHAB_SCAN_TRANSFER_STATE_ABORTING ? names[state] : "unknown";
}

bool rehab_scan_transfer_abort_requested(rehab_scan_transfer_handle_t handle)
{
    return handle != NULL && handle->abort_requested;
}

void rehab_scan_transfer_add_sent(rehab_scan_transfer_handle_t handle, size_t bytes)
{
    if (lock_take(handle)) {
        handle->status.bytes_sent += bytes;
        xSemaphoreGive(handle->lock);
    }
}

void rehab_scan_transfer_set_http(rehab_scan_transfer_handle_t handle, int status)
{
    if (lock_take(handle)) {
        handle->status.last_http_status = status;
        xSemaphoreGive(handle->lock);
    }
}

void rehab_scan_transfer_set_state(rehab_scan_transfer_handle_t handle,
                                   rehab_scan_transfer_state_t state)
{
    if (lock_take(handle)) {
        handle->status.state = state;
        xSemaphoreGive(handle->lock);
    }
}

void rehab_scan_transfer_fail(rehab_scan_transfer_handle_t handle,
                              esp_err_t error, int saved_errno,
                              const char *error_code)
{
    if (lock_take(handle)) {
        bool aborted = handle->abort_requested;
        handle->status.state = REHAB_SCAN_TRANSFER_STATE_FAILED;
        handle->status.last_error = error;
        handle->status.last_errno = saved_errno;
        handle->status.aborted = aborted;
        strlcpy(handle->status.error_code,
                aborted ? "E_ABORTED" : (error_code ? error_code : "E_TRANSFER"),
                sizeof(handle->status.error_code));
        xSemaphoreGive(handle->lock);
    }
}

static void copy_job_status(rehab_scan_transfer_handle_t handle,
                            const rehab_scan_transfer_job_t *job)
{
    if (lock_take(handle)) {
        strlcpy(handle->status.service_job_id, job->service_job_id,
                sizeof(handle->status.service_job_id));
        if (job->pipeline_job_id[0] != '\0') {
            strlcpy(handle->status.pipeline_job_id, job->pipeline_job_id,
                    sizeof(handle->status.pipeline_job_id));
        }
        xSemaphoreGive(handle->lock);
    }
}

static esp_err_t wait_abortable(rehab_scan_transfer_handle_t handle,
                                uint32_t milliseconds)
{
    uint32_t elapsed = 0;
    while (elapsed < milliseconds) {
        if (rehab_scan_transfer_abort_requested(handle)) {
            return ESP_ERR_INVALID_STATE;
        }
        uint32_t slice = milliseconds - elapsed > 100U ? 100U : milliseconds - elapsed;
        vTaskDelay(pdMS_TO_TICKS(slice));
        elapsed += slice;
    }
    return ESP_OK;
}

static esp_err_t make_job_suffix(char *out, size_t out_size,
                                 const char *service_job_id,
                                 const char *tail)
{
    int count = snprintf(out, out_size, "/api/v1/scan/jobs/%s%s",
                         service_job_id, tail ? tail : "");
    return count > 0 && (size_t)count < out_size ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

static bool generation_is_current(rehab_scan_transfer_handle_t handle,
                                  uint32_t generation)
{
    bool current = false;
    if (lock_take(handle)) {
        current = handle->status.generation == generation &&
            handle->requested_generation == generation &&
            !handle->abort_requested;
        xSemaphoreGive(handle->lock);
    }
    return current;
}

static void transfer_worker(void *argument)
{
    rehab_scan_transfer_handle_t handle = argument;
    esp_err_t err = ESP_OK;
    int saved_errno = 0;
    const char *error_code = "E_TRANSFER";
    uint8_t *stream = heap_caps_malloc(handle->config.stream_buffer_bytes,
                                        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (stream == NULL) {
        stream = heap_caps_malloc(handle->config.stream_buffer_bytes, MALLOC_CAP_8BIT);
    }
    char boundary[REHAB_SCAN_TRANSFER_BOUNDARY_MAX];
    size_t content_length = 0;
    rehab_scan_transfer_http_response_t response = {0};
    rehab_scan_transfer_job_t job = {0};
    char service_error[REHAB_SCAN_TRANSFER_ERROR_CODE_MAX] = {0};

    if (stream == NULL) {
        err = ESP_ERR_NO_MEM;
        error_code = "E_NO_MEMORY";
        goto done;
    }
    err = rehab_scan_transfer_make_boundary(boundary, sizeof(boundary));
    if (err != ESP_OK) {
        error_code = "E_BOUNDARY";
        goto done;
    }
    err = rehab_scan_transfer_multipart_length(&handle->plan, boundary,
                                                handle->config.max_upload_bytes,
                                                &content_length);
    if (err != ESP_OK) {
        error_code = "E_UPLOAD_TOO_LARGE";
        goto done;
    }
    err = rehab_scan_transfer_http_upload(handle, &handle->plan, boundary,
                                           content_length, stream, &response);
    if (err != ESP_OK) {
        error_code = "E_UPLOAD_TRANSPORT";
        goto done;
    }
    if (response.http_status < 200 || response.http_status >= 300) {
        err = ESP_FAIL;
        error_code = rehab_scan_transfer_parse_error_code(
            response.body, response.body_length, service_error, sizeof(service_error))
            ? service_error : response.http_status == 503 ? "E_SERVICE_BUSY" : "E_UPLOAD_HTTP";
        goto done;
    }
    err = rehab_scan_transfer_parse_job(response.body, response.body_length,
                                         handle->plan.scan_id, &job);
    rehab_scan_transfer_http_response_free(&response);
    if (err != ESP_OK) {
        error_code = "E_SERVICE_SCHEMA";
        goto done;
    }
    copy_job_status(handle, &job);
    if (rehab_scan_transfer_abort_requested(handle)) {
        err = ESP_ERR_INVALID_STATE;
        goto done;
    }
    if (handle->mode == REHAB_SCAN_TRANSFER_MODE_UPLOAD_ONLY) {
        if (lock_take(handle)) {
            handle->status.state = REHAB_SCAN_TRANSFER_STATE_COMPLETED;
            handle->status.last_error = ESP_OK;
            handle->status.last_errno = 0;
            handle->status.error_code[0] = '\0';
            xSemaphoreGive(handle->lock);
        }
        goto done;
    }
    if (handle->mode == REHAB_SCAN_TRANSFER_MODE_AUTO_DECISION) {
        uint32_t generation = handle->status.generation;
        rehab_scan_transfer_set_state(
            handle, REHAB_SCAN_TRANSFER_STATE_WAITING_PROCESSING);
        int64_t decision_deadline_us = esp_timer_get_time() +
            (int64_t)handle->config.total_wait_timeout_ms * 1000LL;
        rehab_scan_transfer_decision_status_t decision = {0};
        while (true) {
            if (!generation_is_current(handle, generation)) {
                err = ESP_ERR_INVALID_STATE;
                error_code = "E_STALE_GENERATION";
                goto done;
            }
            if (esp_timer_get_time() >= decision_deadline_us) {
                err = ESP_ERR_TIMEOUT;
                error_code = "E_PIPELINE_TIMEOUT";
                goto done;
            }
            char status_suffix[224];
            int count = snprintf(
                status_suffix, sizeof(status_suffix),
                "/api/v1/scans/%s/decision-status",
                handle->plan.scan_id);
            if (count <= 0 || (size_t)count >= sizeof(status_suffix)) {
                err = ESP_ERR_INVALID_SIZE;
                error_code = "E_STATUS_SCHEMA";
                goto done;
            }
            err = rehab_scan_transfer_http_get_json(
                handle, status_suffix, &response);
            if (err != ESP_OK || response.http_status != 200) {
                error_code = "E_STATUS_HTTP";
                goto done;
            }
            err = rehab_scan_transfer_parse_decision_status(
                response.body, response.body_length,
                handle->plan.scan_id, &decision);
            rehab_scan_transfer_http_response_free(&response);
            if (err != ESP_OK) {
                error_code = "E_STATUS_SCHEMA";
                goto done;
            }
            if (lock_take(handle)) {
                handle->status.poll_count++;
                xSemaphoreGive(handle->lock);
            }
            if (strcmp(decision.state, "failed") == 0) {
                err = ESP_FAIL;
                if (decision.error_code[0] != '\0') {
                    strlcpy(service_error, decision.error_code,
                            sizeof(service_error));
                    error_code = service_error;
                } else {
                    error_code = "E_PIPELINE_FAILED";
                }
                goto done;
            }
            if (strcmp(decision.state, "decision_ready") == 0) break;
            if (strcmp(decision.state, "queued") != 0 &&
                strcmp(decision.state, "processing") != 0 &&
                strcmp(decision.state, "not_found") != 0) {
                err = ESP_ERR_INVALID_RESPONSE;
                error_code = "E_STATUS_SCHEMA";
                goto done;
            }
            err = wait_abortable(
                handle, handle->config.poll_interval_ms);
            if (err != ESP_OK) {
                error_code = "E_ABORTED";
                goto done;
            }
        }

        rehab_scan_transfer_set_state(
            handle, REHAB_SCAN_TRANSFER_STATE_RECEIVING_RESULT);
        char temporary[REHAB_SCAN_TRANSFER_PATH_MAX];
        char final[REHAB_SCAN_TRANSFER_PATH_MAX];
        err = rehab_scan_transfer_prepare_latest_scene_paths(
            handle, temporary, sizeof(temporary), final, sizeof(final),
            &saved_errno);
        if (err != ESP_OK) {
            error_code = "E_DECISION_STORAGE";
            goto done;
        }
        char package_suffix[224];
        int count = snprintf(
            package_suffix, sizeof(package_suffix),
            "/api/v1/scans/%s/decision-package",
            handle->plan.scan_id);
        if (count <= 0 || (size_t)count >= sizeof(package_suffix)) {
            err = ESP_ERR_INVALID_SIZE;
            error_code = "E_DECISION_SCHEMA";
            goto done;
        }
        size_t package_length = 0;
        int package_http = 0;
        char downloaded_sha256[REHAB_SCAN_TRANSFER_SHA256_HEX_MAX] = {0};
        err = rehab_scan_transfer_http_download_decision(
            handle, package_suffix, temporary, stream,
            decision.package_length, decision.package_sha256,
            &package_length, downloaded_sha256,
            &package_http, &saved_errno);
        rehab_scan_transfer_set_http(handle, package_http);
        if (err != ESP_OK) {
            error_code = err == ESP_ERR_INVALID_CRC
                ? "E_DECISION_SHA256"
                : err == ESP_ERR_INVALID_SIZE
                    ? "E_DECISION_TOO_LARGE"
                    : "E_DECISION_DOWNLOAD";
            goto done;
        }
        char *package_json = NULL;
        size_t loaded_length = 0;
        err = rehab_scan_transfer_load_file(
            temporary, handle->config.max_decision_bytes,
            &package_json, &loaded_length, &saved_errno);
        if (err == ESP_OK && loaded_length != package_length) {
            err = ESP_ERR_INVALID_SIZE;
        }
        if (err == ESP_OK) {
            err = rehab_scan_transfer_validate_decision_package(
                package_json, loaded_length, handle->plan.scan_id);
        }
        heap_caps_free(package_json);
        if (err != ESP_OK) {
            (void)unlink(temporary);
            error_code = "E_DECISION_SCHEMA";
            goto done;
        }
        if (!generation_is_current(handle, generation)) {
            (void)unlink(temporary);
            err = ESP_ERR_INVALID_STATE;
            error_code = "E_STALE_GENERATION";
            goto done;
        }
        err = rehab_scan_transfer_commit_latest_scene(
            temporary, final, &saved_errno);
        if (err != ESP_OK) {
            error_code = "E_DECISION_STORAGE";
            goto done;
        }
        if (lock_take(handle)) {
            handle->status.state = REHAB_SCAN_TRANSFER_STATE_COMPLETED;
            handle->status.result_bytes = package_length;
            handle->status.last_error = ESP_OK;
            handle->status.last_errno = 0;
            handle->status.error_code[0] = '\0';
            strlcpy(handle->status.package_sha256, downloaded_sha256,
                    sizeof(handle->status.package_sha256));
            strlcpy(handle->status.result_path, final,
                    sizeof(handle->status.result_path));
            xSemaphoreGive(handle->lock);
        }
        ESP_LOGI(TAG,
                 "decision_ready scan_id=%s generation=%" PRIu32
                 " bytes=%u sha256=%s path=%s",
                 handle->plan.scan_id, generation,
                 (unsigned)package_length, downloaded_sha256, final);
        goto done;
    }
    rehab_scan_transfer_set_state(handle, REHAB_SCAN_TRANSFER_STATE_WAITING_PROCESSING);

    int64_t deadline_us = esp_timer_get_time() +
        (int64_t)handle->config.total_wait_timeout_ms * 1000LL;
    while (true) {
        if (rehab_scan_transfer_abort_requested(handle)) {
            err = ESP_ERR_INVALID_STATE;
            goto done;
        }
        if (esp_timer_get_time() >= deadline_us) {
            err = ESP_ERR_TIMEOUT;
            error_code = "E_PIPELINE_TIMEOUT";
            goto done;
        }
        char suffix[128];
        err = make_job_suffix(suffix, sizeof(suffix), job.service_job_id, NULL);
        if (err == ESP_OK) {
            err = rehab_scan_transfer_http_get_json(handle, suffix, &response);
        }
        if (err != ESP_OK || response.http_status != 200) {
            error_code = err == ESP_OK && rehab_scan_transfer_parse_error_code(
                response.body, response.body_length, service_error, sizeof(service_error))
                ? service_error : "E_STATUS_HTTP";
            goto done;
        }
        rehab_scan_transfer_job_t polled = {0};
        err = rehab_scan_transfer_parse_job(response.body, response.body_length,
                                             handle->plan.scan_id, &polled);
        rehab_scan_transfer_http_response_free(&response);
        if (err != ESP_OK || strcmp(polled.service_job_id, job.service_job_id) != 0 ||
            (job.pipeline_job_id[0] != '\0' && polled.pipeline_job_id[0] != '\0' &&
             strcmp(job.pipeline_job_id, polled.pipeline_job_id) != 0)) {
            err = ESP_ERR_INVALID_RESPONSE;
            error_code = "E_STATUS_SCHEMA";
            goto done;
        }
        job = polled;
        copy_job_status(handle, &job);
        if (lock_take(handle)) {
            handle->status.poll_count++;
            xSemaphoreGive(handle->lock);
        }
        if (strcmp(job.state, "failed") == 0) {
            err = ESP_FAIL;
            error_code = "E_PIPELINE_FAILED";
            goto done;
        }
        if (strcmp(job.state, "completed") == 0) {
            if (job.pipeline_job_id[0] == '\0' ||
                (strcmp(job.pipeline_result_status, "success") != 0 &&
                 strcmp(job.pipeline_result_status, "partial_success") != 0 &&
                 strcmp(job.pipeline_result_status, "rejected") != 0)) {
                err = ESP_ERR_INVALID_RESPONSE;
                error_code = "E_STATUS_SCHEMA";
                goto done;
            }
            break;
        }
        if (strcmp(job.state, "accepted") != 0 && strcmp(job.state, "running") != 0) {
            err = ESP_ERR_INVALID_RESPONSE;
            error_code = "E_STATUS_SCHEMA";
            goto done;
        }
        err = wait_abortable(handle, handle->config.poll_interval_ms);
        if (err != ESP_OK) {
            goto done;
        }
    }

    rehab_scan_transfer_set_state(handle, REHAB_SCAN_TRANSFER_STATE_RECEIVING_RESULT);
    char temporary[REHAB_SCAN_TRANSFER_PATH_MAX];
    char final[REHAB_SCAN_TRANSFER_PATH_MAX];
    err = rehab_scan_transfer_prepare_result_paths(handle, handle->plan.scan_id,
                                                    temporary, sizeof(temporary),
                                                    final, sizeof(final), &saved_errno);
    if (err != ESP_OK) {
        error_code = saved_errno == EEXIST ? "E_RESULT_EXISTS" : "E_RESULT_STORAGE";
        goto done;
    }
    char suffix[128];
    err = make_job_suffix(suffix, sizeof(suffix), job.service_job_id, "/result");
    size_t result_length = 0;
    int result_http = 0;
    if (err == ESP_OK) {
        err = rehab_scan_transfer_http_download_result(handle, suffix, temporary,
                                                        stream, &result_length,
                                                        &result_http, &saved_errno);
    }
    rehab_scan_transfer_set_http(handle, result_http);
    if (err != ESP_OK) {
        error_code = result_http == 409 ? "E_RESULT_NOT_READY" :
                     err == ESP_ERR_INVALID_SIZE ? "E_RESULT_TOO_LARGE" :
                     "E_RESULT_DOWNLOAD";
        goto done;
    }
    char *result_json = NULL;
    size_t loaded_length = 0;
    err = rehab_scan_transfer_load_file(temporary, handle->config.max_result_bytes,
                                        &result_json, &loaded_length, &saved_errno);
    if (err == ESP_OK && loaded_length != result_length) {
        err = ESP_ERR_INVALID_SIZE;
    }
    if (err == ESP_OK) {
        err = rehab_scan_transfer_validate_result(result_json, loaded_length,
                                                  handle->plan.scan_id,
                                                  job.pipeline_job_id,
                                                  handle->config.max_objects);
    }
    heap_caps_free(result_json);
    if (err != ESP_OK) {
        (void)unlink(temporary);
        error_code = "E_RESULT_SCHEMA";
        goto done;
    }
    err = rehab_scan_transfer_commit_result(temporary, final, &saved_errno);
    if (err != ESP_OK) {
        error_code = saved_errno == EEXIST ? "E_RESULT_EXISTS" : "E_RESULT_STORAGE";
        goto done;
    }
    if (lock_take(handle)) {
        handle->status.state = REHAB_SCAN_TRANSFER_STATE_COMPLETED;
        handle->status.result_bytes = result_length;
        handle->status.last_error = ESP_OK;
        handle->status.last_errno = 0;
        handle->status.error_code[0] = '\0';
        strlcpy(handle->status.result_path, final, sizeof(handle->status.result_path));
        xSemaphoreGive(handle->lock);
    }

done:
    rehab_scan_transfer_http_response_free(&response);
    heap_caps_free(stream);
    if (err != ESP_OK) {
        rehab_scan_transfer_fail(handle, err, saved_errno, error_code);
    }
    if (lock_take(handle)) {
        handle->worker = NULL;
        xSemaphoreGive(handle->lock);
    }
    vTaskDelete(NULL);
}

esp_err_t rehab_scan_transfer_create(const rehab_scan_transfer_config_t *config,
                                     rehab_scan_transfer_handle_t *out_handle)
{
    if (config == NULL || out_handle == NULL || config->scan_root_path == NULL ||
        config->result_root_path == NULL || config->server_base_url == NULL ||
        config->stream_buffer_bytes != 16384U || config->max_frames == 0 ||
        config->max_frames > 11 || config->max_upload_bytes == 0 ||
        config->max_upload_bytes > 6291456U || config->max_json_part_bytes == 0 ||
        config->max_json_part_bytes > 65536U || config->max_result_bytes == 0 ||
        config->max_result_bytes > 262144U || config->max_objects == 0 ||
        config->max_decision_bytes != REHAB_SCAN_TRANSFER_DECISION_MAX_BYTES ||
        config->max_objects > 100U || config->poll_interval_ms == 0 ||
        config->total_wait_timeout_ms == 0 || config->upload_timeout_ms == 0 ||
        config->request_timeout_ms == 0 || config->worker_stack_size == 0 ||
        strncmp(config->server_base_url, "http://", 7) != 0) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_handle = NULL;
    rehab_scan_transfer_handle_t handle = calloc(1, sizeof(*handle));
    if (handle == NULL) {
        return ESP_ERR_NO_MEM;
    }
    handle->config = *config;
    if (strlcpy(handle->scan_root_path, config->scan_root_path,
                sizeof(handle->scan_root_path)) >= sizeof(handle->scan_root_path) ||
        strlcpy(handle->result_root_path, config->result_root_path,
                sizeof(handle->result_root_path)) >= sizeof(handle->result_root_path) ||
        strlcpy(handle->server_base_url, config->server_base_url,
                sizeof(handle->server_base_url)) >= sizeof(handle->server_base_url)) {
        free(handle);
        return ESP_ERR_INVALID_SIZE;
    }
    if (config->latest_scene_result_path != NULL &&
        strlcpy(handle->latest_scene_result_path,
                config->latest_scene_result_path,
                sizeof(handle->latest_scene_result_path)) >=
                sizeof(handle->latest_scene_result_path)) {
        free(handle);
        return ESP_ERR_INVALID_SIZE;
    }
    handle->config.scan_root_path = handle->scan_root_path;
    handle->config.result_root_path = handle->result_root_path;
    handle->config.latest_scene_result_path = handle->latest_scene_result_path;
    handle->config.server_base_url = handle->server_base_url;
    handle->lock = xSemaphoreCreateMutex();
    if (handle->lock == NULL) {
        free(handle);
        return ESP_ERR_NO_MEM;
    }
    handle->status.state = REHAB_SCAN_TRANSFER_STATE_IDLE;
    *out_handle = handle;
    return ESP_OK;
}

esp_err_t rehab_scan_transfer_start_mode(rehab_scan_transfer_handle_t handle,
                                         const char *selector,
                                         rehab_scan_transfer_mode_t mode,
                                         char *out_scan_id,
                                         size_t out_scan_id_size)
{
    if (handle == NULL || selector == NULL || out_scan_id == NULL ||
        out_scan_id_size == 0 ||
        (mode != REHAB_SCAN_TRANSFER_MODE_FULL_PIPELINE &&
         mode != REHAB_SCAN_TRANSFER_MODE_UPLOAD_ONLY &&
         mode != REHAB_SCAN_TRANSFER_MODE_AUTO_DECISION)) {
        return ESP_ERR_INVALID_ARG;
    }
    wifi_manager_status_t wifi = {0};
    wifi_manager_get_status(&wifi);
    if (!wifi.sta_connected) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!lock_take(handle)) {
        return ESP_FAIL;
    }
    if (rehab_scan_transfer_is_active(handle->status.state) || handle->worker != NULL) {
        xSemaphoreGive(handle->lock);
        return ESP_ERR_INVALID_STATE;
    }
    if (mode == REHAB_SCAN_TRANSFER_MODE_AUTO_DECISION &&
        (handle->requested_generation == 0U ||
         handle->latest_scene_result_path[0] == '\0')) {
        xSemaphoreGive(handle->lock);
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreGive(handle->lock);

    rehab_scan_transfer_plan_t *plan = heap_caps_calloc(
        1, sizeof(*plan), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (plan == NULL) {
        plan = heap_caps_calloc(1, sizeof(*plan), MALLOC_CAP_8BIT);
    }
    if (plan == NULL) {
        return ESP_ERR_NO_MEM;
    }

    esp_err_t err = rehab_scan_transfer_select_and_validate(handle, selector, plan);
    if (err != ESP_OK) {
        goto cleanup;
    }
    if (strlcpy(out_scan_id, plan->scan_id, out_scan_id_size) >= out_scan_id_size) {
        err = ESP_ERR_INVALID_SIZE;
        goto cleanup;
    }
    if (!lock_take(handle)) {
        err = ESP_FAIL;
        goto cleanup;
    }
    if (rehab_scan_transfer_is_active(handle->status.state) || handle->worker != NULL) {
        xSemaphoreGive(handle->lock);
        err = ESP_ERR_INVALID_STATE;
        goto cleanup;
    }
    memset(&handle->status, 0, sizeof(handle->status));
    handle->status.state = REHAB_SCAN_TRANSFER_STATE_UPLOADING;
    strlcpy(handle->status.scan_id, plan->scan_id, sizeof(handle->status.scan_id));
    handle->plan = *plan;
    handle->mode = mode;
    handle->status.generation =
        mode == REHAB_SCAN_TRANSFER_MODE_AUTO_DECISION
            ? handle->requested_generation : 0U;
    handle->abort_requested = false;
    BaseType_t created = xTaskCreate(transfer_worker, "scan_transfer",
                                     handle->config.worker_stack_size, handle,
                                     handle->config.worker_priority, &handle->worker);
    if (created != pdPASS) {
        handle->worker = NULL;
        handle->status.state = REHAB_SCAN_TRANSFER_STATE_FAILED;
        handle->status.last_error = ESP_ERR_NO_MEM;
        strlcpy(handle->status.error_code, "E_WORKER_CREATE",
                sizeof(handle->status.error_code));
        xSemaphoreGive(handle->lock);
        err = ESP_ERR_NO_MEM;
        goto cleanup;
    }
    xSemaphoreGive(handle->lock);
    err = ESP_OK;

cleanup:
    heap_caps_free(plan);
    return err;
}

esp_err_t rehab_scan_transfer_start_auto_decision(
    rehab_scan_transfer_handle_t handle,
    const char *scan_id,
    uint32_t generation,
    char *out_scan_id,
    size_t out_scan_id_size)
{
    if (handle == NULL || generation == 0U) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!lock_take(handle)) {
        return ESP_FAIL;
    }
    if (handle->latest_scene_result_path[0] == '\0') {
        xSemaphoreGive(handle->lock);
        return ESP_ERR_INVALID_STATE;
    }
    if (rehab_scan_transfer_is_active(handle->status.state) ||
        handle->worker != NULL) {
        xSemaphoreGive(handle->lock);
        return ESP_ERR_INVALID_STATE;
    }
    handle->requested_generation = generation;
    xSemaphoreGive(handle->lock);
    esp_err_t err = rehab_scan_transfer_start_mode(
        handle, scan_id, REHAB_SCAN_TRANSFER_MODE_AUTO_DECISION,
        out_scan_id, out_scan_id_size);
    if (err != ESP_OK && lock_take(handle)) {
        if (handle->worker == NULL) {
            handle->requested_generation = 0U;
        }
        xSemaphoreGive(handle->lock);
    }
    return err;
}

esp_err_t rehab_scan_transfer_start(rehab_scan_transfer_handle_t handle,
                                    const char *selector, char *out_scan_id,
                                    size_t out_scan_id_size)
{
    return rehab_scan_transfer_start_mode(
        handle, selector, REHAB_SCAN_TRANSFER_MODE_FULL_PIPELINE,
        out_scan_id, out_scan_id_size);
}

esp_err_t rehab_scan_transfer_get_status(rehab_scan_transfer_handle_t handle,
                                         rehab_scan_transfer_status_t *out_status)
{
    if (handle == NULL || out_status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!lock_take(handle)) {
        return ESP_FAIL;
    }
    *out_status = handle->status;
    xSemaphoreGive(handle->lock);
    return ESP_OK;
}

esp_err_t rehab_scan_transfer_abort(rehab_scan_transfer_handle_t handle)
{
    if (handle == NULL || !lock_take(handle)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!rehab_scan_transfer_is_active(handle->status.state)) {
        xSemaphoreGive(handle->lock);
        return ESP_ERR_INVALID_STATE;
    }
    handle->abort_requested = true;
    handle->status.state = REHAB_SCAN_TRANSFER_STATE_ABORTING;
    xSemaphoreGive(handle->lock);
    return ESP_OK;
}

void rehab_scan_transfer_delete(rehab_scan_transfer_handle_t handle)
{
    if (handle == NULL) {
        return;
    }
    if (lock_take(handle)) {
        handle->deleting = true;
        if (rehab_scan_transfer_is_active(handle->status.state)) {
            handle->abort_requested = true;
            handle->status.state = REHAB_SCAN_TRANSFER_STATE_ABORTING;
        }
        xSemaphoreGive(handle->lock);
    }
    uint32_t wait_ms = handle->config.upload_timeout_ms + handle->config.request_timeout_ms + 1000U;
    for (uint32_t elapsed = 0; elapsed < wait_ms; elapsed += 50U) {
        if (lock_take(handle)) {
            bool finished = handle->worker == NULL;
            xSemaphoreGive(handle->lock);
            if (finished) {
                vSemaphoreDelete(handle->lock);
                free(handle);
                return;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    ESP_LOGE(TAG, "delete timed out; preserving handle still owned by worker");
}
