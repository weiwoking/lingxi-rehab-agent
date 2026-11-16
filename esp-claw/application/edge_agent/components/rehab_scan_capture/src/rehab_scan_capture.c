// SPDX-License-Identifier: Apache-2.0
#include "rehab_scan_capture_priv.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "esp_console.h"
#include "esp_heap_caps.h"
#include "esp_log.h"

static const char *TAG = "rehab_scan";

static bool state_active(rehab_scan_capture_state_t state)
{
    return state == REHAB_SCAN_CAPTURE_STATE_CAPTURING ||
           state == REHAB_SCAN_CAPTURE_STATE_COMMITTING ||
           state == REHAB_SCAN_CAPTURE_STATE_ABORTING;
}

const char *rehab_scan_capture_state_name(rehab_scan_capture_state_t state)
{
    switch (state) {
    case REHAB_SCAN_CAPTURE_STATE_IDLE: return "idle";
    case REHAB_SCAN_CAPTURE_STATE_CAPTURING: return "capturing";
    case REHAB_SCAN_CAPTURE_STATE_COMMITTING: return "committing";
    case REHAB_SCAN_CAPTURE_STATE_COMPLETED: return "completed";
    case REHAB_SCAN_CAPTURE_STATE_FAILED: return "failed";
    case REHAB_SCAN_CAPTURE_STATE_ABORTING: return "aborting";
    default: return "unknown";
    }
}

bool rehab_scan_capture_abort_requested(rehab_scan_capture_handle_t handle)
{
    bool requested = true;
    if (handle != NULL && xSemaphoreTake(handle->lock, portMAX_DELAY) == pdTRUE) {
        requested = handle->abort_requested;
        xSemaphoreGive(handle->lock);
    }
    return requested;
}

void rehab_scan_capture_set_failure(rehab_scan_capture_handle_t handle,
                                    esp_err_t error,
                                    int saved_errno,
                                    uint32_t sequence,
                                    bool aborted)
{
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    handle->status.state = REHAB_SCAN_CAPTURE_STATE_FAILED;
    handle->status.last_error = error;
    handle->status.last_errno = saved_errno;
    handle->status.failed_frame_sequence = sequence;
    handle->status.aborted = aborted;
    handle->status.final_path[0] = '\0';
    xSemaphoreGive(handle->lock);
}

static esp_err_t write_frame(rehab_scan_capture_handle_t handle,
                             const char *working_path,
                             const uint8_t *image_buffer,
                             const rehab_scan_frame_record_t *record,
                             int *out_errno)
{
    char frame_path[REHAB_SCAN_CAPTURE_PATH_MAX];
    esp_err_t err = rehab_scan_capture_make_frame_dir(working_path,
                                                      record->sequence,
                                                      frame_path,
                                                      sizeof(frame_path));
    if (err != ESP_OK) {
        *out_errno = errno;
        return err;
    }
    err = rehab_scan_capture_write_binary_new_atomic(frame_path, "image.rgb565",
                                                     image_buffer,
                                                     REHAB_SCAN_IMAGE_BYTES,
                                                     out_errno);
    if (err != ESP_OK || rehab_scan_capture_abort_requested(handle)) {
        return err != ESP_OK ? err : ESP_ERR_INVALID_STATE;
    }

    cJSON *tof_json = rehab_scan_capture_make_tof_json(record);
    if (tof_json == NULL) {
        return ESP_ERR_NO_MEM;
    }
    err = rehab_scan_capture_write_json_new_atomic(frame_path, "tof.json",
                                                   tof_json, out_errno);
    cJSON_Delete(tof_json);
    if (err != ESP_OK || rehab_scan_capture_abort_requested(handle)) {
        return err != ESP_OK ? err : ESP_ERR_INVALID_STATE;
    }

    cJSON *frame_json = rehab_scan_capture_make_frame_json(record);
    if (frame_json == NULL) {
        return ESP_ERR_NO_MEM;
    }
    err = rehab_scan_capture_write_json_new_atomic(frame_path, "frame.json",
                                                   frame_json, out_errno);
    cJSON_Delete(frame_json);
    return err;
}

static void scan_worker(void *context)
{
    rehab_scan_capture_handle_t handle = context;
    uint8_t *image_buffer = NULL;
    char working_path[REHAB_SCAN_CAPTURE_PATH_MAX] = {0};
    char final_path[REHAB_SCAN_CAPTURE_PATH_MAX] = {0};
    char scan_json_path[REHAB_SCAN_CAPTURE_PATH_MAX] = {0};
    esp_err_t err = ESP_OK;
    int saved_errno = 0;
    uint32_t sequence = 0;
    bool aborted = false;
    bool scan_json_written = false;
    TickType_t scan_start = xTaskGetTickCount();
    rehab_scan_capture_prepare_cb_t prepare_cb;
    rehab_scan_capture_before_frame_cb_t before_frame_cb;
    rehab_scan_capture_frame_cb_t before_write_cb;
    rehab_scan_capture_frame_cb_t after_frame_cb;
    rehab_scan_capture_before_commit_cb_t before_commit_cb;
    void *guided_ctx;

    xSemaphoreTake(handle->lock, portMAX_DELAY);
    prepare_cb = handle->prepare_cb;
    before_frame_cb = handle->before_frame_cb;
    before_write_cb = handle->before_write_cb;
    after_frame_cb = handle->after_frame_cb;
    before_commit_cb = handle->before_commit_cb;
    guided_ctx = handle->guided_ctx;
    xSemaphoreGive(handle->lock);

    image_buffer = heap_caps_malloc(REHAB_SCAN_IMAGE_BYTES,
                                    MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (image_buffer == NULL) {
        err = ESP_ERR_NO_MEM;
        goto cleanup;
    }
    if (prepare_cb != NULL) {
        err = prepare_cb(guided_ctx);
        if (err != ESP_OK) {
            aborted = rehab_scan_capture_abort_requested(handle);
            goto cleanup;
        }
    }
    err = rehab_scan_capture_prepare_working(handle, working_path,
                                             sizeof(working_path), final_path,
                                             sizeof(final_path));
    if (err != ESP_OK) {
        saved_errno = errno;
        goto cleanup;
    }

    uint32_t requested_frames;
    uint32_t interval_ms;
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    requested_frames = handle->status.requested_frames;
    interval_ms = handle->request_interval_ms;
    xSemaphoreGive(handle->lock);

    for (sequence = 0; sequence < requested_frames; ++sequence) {
        if (rehab_scan_capture_abort_requested(handle)) {
            aborted = true;
            err = ESP_ERR_INVALID_STATE;
            goto cleanup;
        }
        if (before_frame_cb != NULL) {
            err = before_frame_cb(guided_ctx, sequence);
            if (err != ESP_OK) {
                aborted = rehab_scan_capture_abort_requested(handle);
                goto cleanup;
            }
        }
        rehab_scan_frame_record_t record = {0};
        err = rehab_scan_capture_pair_frame(handle, sequence, image_buffer, &record);
        if (err != ESP_OK) {
            aborted = rehab_scan_capture_abort_requested(handle);
            goto cleanup;
        }
        if (before_write_cb != NULL) {
            before_write_cb(guided_ctx, sequence);
        }
        err = write_frame(handle, working_path, image_buffer, &record, &saved_errno);
        if (err != ESP_OK) {
            aborted = rehab_scan_capture_abort_requested(handle);
            goto cleanup;
        }
        xSemaphoreTake(handle->lock, portMAX_DELAY);
        ++handle->status.committed_frames;
        xSemaphoreGive(handle->lock);
        if (after_frame_cb != NULL) {
            after_frame_cb(guided_ctx, sequence);
        }
        ESP_LOGI(TAG,
                 "frame=%u camera_ts=%lld tof_ts=%lld pair_delta_us=%llu before_seq=%llu after_seq=%llu",
                 (unsigned)sequence, (long long)record.camera_timestamp_us,
                 (long long)record.tof.timestamp_us,
                 (unsigned long long)record.pair_delta_us,
                 (unsigned long long)record.provider_before_sequence,
                 (unsigned long long)record.provider_after_sequence);

        if (before_frame_cb == NULL && sequence + 1U < requested_frames) {
            TickType_t deadline = scan_start +
                pdMS_TO_TICKS(interval_ms) * (sequence + 1U);
            while ((int32_t)(deadline - xTaskGetTickCount()) > 0) {
                if (rehab_scan_capture_abort_requested(handle)) {
                    aborted = true;
                    err = ESP_ERR_INVALID_STATE;
                    goto cleanup;
                }
                TickType_t remaining = deadline - xTaskGetTickCount();
                TickType_t slice = pdMS_TO_TICKS(REHAB_SCAN_ABORT_POLL_MS);
                vTaskDelay(remaining < slice ? remaining : slice);
            }
        }
    }

    if (rehab_scan_capture_abort_requested(handle)) {
        aborted = true;
        err = ESP_ERR_INVALID_STATE;
        goto cleanup;
    }
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    bool manifest_complete =
        handle->status.committed_frames == handle->status.requested_frames;
    xSemaphoreGive(handle->lock);
    if (!manifest_complete) {
        err = ESP_ERR_INVALID_RESPONSE;
        goto cleanup;
    }
    int written = snprintf(scan_json_path, sizeof(scan_json_path), "%s/scan.json",
                           working_path);
    if (written <= 0 || written >= (int)sizeof(scan_json_path)) {
        err = ESP_ERR_INVALID_SIZE;
        goto cleanup;
    }
    cJSON *scan_json = rehab_scan_capture_make_scan_json(handle);
    if (scan_json == NULL) {
        err = ESP_ERR_NO_MEM;
        goto cleanup;
    }
    err = rehab_scan_capture_write_json_new_atomic(working_path, "scan.json",
                                                   scan_json, &saved_errno);
    cJSON_Delete(scan_json);
    if (err != ESP_OK) {
        goto cleanup;
    }
    scan_json_written = true;
    if (before_commit_cb != NULL) {
        before_commit_cb(guided_ctx);
    }

    /* The final rename is the commit point. Holding the status lock makes an
     * abort either win before the rename or observe the completed scan. */
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    if (handle->abort_requested) {
        aborted = true;
        err = ESP_ERR_INVALID_STATE;
        xSemaphoreGive(handle->lock);
        goto cleanup;
    }
    handle->status.state = REHAB_SCAN_CAPTURE_STATE_COMMITTING;
    err = rehab_scan_capture_commit_directory(working_path, final_path, &saved_errno);
    if (err != ESP_OK) {
        xSemaphoreGive(handle->lock);
        goto cleanup;
    }
    handle->status.last_error = ESP_OK;
    handle->status.last_errno = 0;
    handle->status.failed_frame_sequence = UINT32_MAX;
    handle->status.aborted = false;
    strlcpy(handle->status.final_path, final_path,
            sizeof(handle->status.final_path));
    xSemaphoreGive(handle->lock);

cleanup:
    heap_caps_free(image_buffer);
    if (err != ESP_OK) {
        if (scan_json_written) {
            (void)unlink(scan_json_path);
        }
        rehab_scan_capture_set_failure(handle, err, saved_errno, sequence, aborted);
    }
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    handle->worker = NULL;
    if (err == ESP_OK) {
        handle->status.state = REHAB_SCAN_CAPTURE_STATE_COMPLETED;
    }
    handle->prepare_cb = NULL;
    handle->before_frame_cb = NULL;
    handle->before_write_cb = NULL;
    handle->after_frame_cb = NULL;
    handle->before_commit_cb = NULL;
    handle->guided_ctx = NULL;
    xSemaphoreGive(handle->lock);
    xSemaphoreGive(handle->worker_done);
    vTaskDelete(NULL);
}

static esp_err_t validate_config(const rehab_scan_capture_config_t *config)
{
    if (config == NULL || config->scan_root_path == NULL ||
        config->scan_root_path[0] != '/' ||
        config->default_frame_count < REHAB_SCAN_MIN_FRAMES ||
        config->default_frame_count > REHAB_SCAN_MAX_FRAMES ||
        config->default_interval_ms < REHAB_SCAN_MIN_INTERVAL_MS ||
        config->default_interval_ms > REHAB_SCAN_MAX_INTERVAL_MS ||
        config->camera_timeout_ms == 0 || config->tof_after_timeout_ms == 0 ||
        config->worker_stack_size == 0 || config->worker_priority == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    return strnlen(config->scan_root_path, REHAB_SCAN_CAPTURE_PATH_MAX) <
        REHAB_SCAN_CAPTURE_PATH_MAX ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

esp_err_t rehab_scan_capture_create(const rehab_scan_capture_config_t *config,
                                    rehab_scan_capture_handle_t *out_handle)
{
    if (out_handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_handle = NULL;
    esp_err_t err = validate_config(config);
    if (err != ESP_OK) {
        return err;
    }
    rehab_scan_capture_handle_t handle = calloc(1, sizeof(*handle));
    if (handle == NULL) {
        return ESP_ERR_NO_MEM;
    }
    strlcpy(handle->scan_root_path, config->scan_root_path,
            sizeof(handle->scan_root_path));
    handle->config = *config;
    handle->config.scan_root_path = handle->scan_root_path;
    handle->lock = xSemaphoreCreateMutex();
    handle->worker_done = xSemaphoreCreateBinary();
    handle->status.state = REHAB_SCAN_CAPTURE_STATE_IDLE;
    handle->status.failed_frame_sequence = UINT32_MAX;
    if (handle->lock == NULL || handle->worker_done == NULL) {
        if (handle->worker_done != NULL) {
            vSemaphoreDelete(handle->worker_done);
        }
        if (handle->lock != NULL) {
            vSemaphoreDelete(handle->lock);
        }
        free(handle);
        return ESP_ERR_NO_MEM;
    }
    *out_handle = handle;
    return ESP_OK;
}

static esp_err_t path_exists(const char *path, bool *exists)
{
    struct stat status;
    if (stat(path, &status) == 0) {
        *exists = true;
        return ESP_OK;
    }
    if (errno == ENOENT) {
        *exists = false;
        return ESP_OK;
    }
    return ESP_FAIL;
}

static esp_err_t make_scan_identity(rehab_scan_capture_handle_t handle, time_t now)
{
    struct tm utc;
    if (gmtime_r(&now, &utc) == NULL ||
        strftime(handle->created_at, sizeof(handle->created_at),
                 "%Y-%m-%dT%H:%M:%SZ", &utc) == 0) {
        return ESP_ERR_INVALID_STATE;
    }
    if (handle->last_scan_second != now) {
        handle->last_scan_second = now;
        handle->next_suffix = 0;
    }
    for (uint32_t attempt = 0; attempt < REHAB_SCAN_ID_ATTEMPTS; ++attempt) {
        uint32_t suffix = handle->next_suffix++;
        if (suffix > 999U) {
            return ESP_ERR_NOT_FOUND;
        }
        int written = snprintf(handle->status.scan_id,
                               sizeof(handle->status.scan_id),
                               "scan_%04d%02d%02d_%02d%02d%02d_%03u",
                               utc.tm_year + 1900, utc.tm_mon + 1, utc.tm_mday,
                               utc.tm_hour, utc.tm_min, utc.tm_sec,
                               (unsigned)suffix);
        if (written <= 0 || written >= (int)sizeof(handle->status.scan_id)) {
            return ESP_ERR_INVALID_SIZE;
        }
        char final_path[REHAB_SCAN_CAPTURE_PATH_MAX];
        char working_path[REHAB_SCAN_CAPTURE_PATH_MAX];
        written = snprintf(final_path, sizeof(final_path), "%s/%s",
                           handle->scan_root_path, handle->status.scan_id);
        int working_written = snprintf(working_path, sizeof(working_path),
                                       "%s/%s.incomplete",
                                       handle->scan_root_path,
                                       handle->status.scan_id);
        if (written <= 0 || written >= (int)sizeof(final_path) ||
            working_written <= 0 || working_written >= (int)sizeof(working_path)) {
            return ESP_ERR_INVALID_SIZE;
        }
        bool final_exists = false;
        bool working_exists = false;
        esp_err_t err = path_exists(final_path, &final_exists);
        if (err == ESP_OK) {
            err = path_exists(working_path, &working_exists);
        }
        if (err != ESP_OK) {
            return err;
        }
        if (!final_exists && !working_exists) {
            return ESP_OK;
        }
    }
    return ESP_ERR_NOT_FOUND;
}

static esp_err_t start_capture(
    rehab_scan_capture_handle_t handle,
    uint32_t frames,
    uint32_t interval_ms,
    const rehab_scan_capture_guided_request_t *guided,
    char *out_scan_id,
    size_t out_scan_id_size)
{
    if (handle == NULL || out_scan_id == NULL || out_scan_id_size == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (frames < REHAB_SCAN_MIN_FRAMES || frames > REHAB_SCAN_MAX_FRAMES ||
        interval_ms < REHAB_SCAN_MIN_INTERVAL_MS ||
        interval_ms > REHAB_SCAN_MAX_INTERVAL_MS) {
        return ESP_ERR_INVALID_ARG;
    }
    time_t now = time(NULL);
    if ((int64_t)now < REHAB_SCAN_MIN_VALID_EPOCH) {
        return ESP_ERR_INVALID_STATE;
    }

    xSemaphoreTake(handle->lock, portMAX_DELAY);
    if (state_active(handle->status.state) || handle->worker != NULL) {
        xSemaphoreGive(handle->lock);
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t err = make_scan_identity(handle, now);
    if (err != ESP_OK) {
        xSemaphoreGive(handle->lock);
        return err;
    }
    size_t id_length = strlen(handle->status.scan_id);
    if (id_length + 1 > out_scan_id_size) {
        xSemaphoreGive(handle->lock);
        return ESP_ERR_INVALID_SIZE;
    }
    handle->request_interval_ms = interval_ms;
    handle->status.state = REHAB_SCAN_CAPTURE_STATE_CAPTURING;
    handle->status.requested_frames = frames;
    handle->status.committed_frames = 0;
    handle->status.last_error = ESP_OK;
    handle->status.last_errno = 0;
    handle->status.failed_frame_sequence = UINT32_MAX;
    handle->status.aborted = false;
    handle->status.final_path[0] = '\0';
    handle->abort_requested = false;
    handle->prepare_cb = guided != NULL ? guided->prepare : NULL;
    handle->before_frame_cb = guided != NULL ? guided->before_frame : NULL;
    handle->before_write_cb = guided != NULL ? guided->before_write : NULL;
    handle->after_frame_cb = guided != NULL ? guided->after_frame : NULL;
    handle->before_commit_cb = guided != NULL ? guided->before_commit : NULL;
    handle->guided_ctx = guided != NULL ? guided->ctx : NULL;
    (void)xSemaphoreTake(handle->worker_done, 0);
    strlcpy(out_scan_id, handle->status.scan_id, out_scan_id_size);
    BaseType_t task_result = xTaskCreate(scan_worker, "rehab_scan",
                                         handle->config.worker_stack_size,
                                         handle, handle->config.worker_priority,
                                         &handle->worker);
    if (task_result != pdPASS) {
        handle->worker = NULL;
        handle->status.state = REHAB_SCAN_CAPTURE_STATE_FAILED;
        handle->status.last_error = ESP_ERR_NO_MEM;
        out_scan_id[0] = '\0';
        xSemaphoreGive(handle->lock);
        return ESP_ERR_NO_MEM;
    }
    xSemaphoreGive(handle->lock);
    return ESP_OK;
}

esp_err_t rehab_scan_capture_start(rehab_scan_capture_handle_t handle,
                                   const rehab_scan_capture_request_t *request,
                                   char *out_scan_id,
                                   size_t out_scan_id_size)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    uint32_t frames = request != NULL && request->frame_count != 0
        ? request->frame_count : handle->config.default_frame_count;
    uint32_t interval_ms = request != NULL && request->interval_ms != 0
        ? request->interval_ms : handle->config.default_interval_ms;
    return start_capture(handle, frames, interval_ms, NULL,
                         out_scan_id, out_scan_id_size);
}

esp_err_t rehab_scan_capture_start_guided(
    rehab_scan_capture_handle_t handle,
    const rehab_scan_capture_guided_request_t *request,
    char *out_scan_id,
    size_t out_scan_id_size)
{
    if (request == NULL || request->frame_count == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    return start_capture(handle, request->frame_count,
                         handle != NULL ? handle->config.default_interval_ms : 0,
                         request, out_scan_id, out_scan_id_size);
}

esp_err_t rehab_scan_capture_get_status(rehab_scan_capture_handle_t handle,
                                        rehab_scan_capture_status_t *out_status)
{
    if (handle == NULL || out_status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    *out_status = handle->status;
    out_status->scan_id[sizeof(out_status->scan_id) - 1] = '\0';
    out_status->final_path[sizeof(out_status->final_path) - 1] = '\0';
    xSemaphoreGive(handle->lock);
    return ESP_OK;
}

esp_err_t rehab_scan_capture_abort(rehab_scan_capture_handle_t handle)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    if (!state_active(handle->status.state) || handle->worker == NULL) {
        xSemaphoreGive(handle->lock);
        return ESP_ERR_INVALID_STATE;
    }
    handle->abort_requested = true;
    handle->status.state = REHAB_SCAN_CAPTURE_STATE_ABORTING;
    xSemaphoreGive(handle->lock);
    return ESP_OK;
}

void rehab_scan_capture_delete(rehab_scan_capture_handle_t handle)
{
    if (handle == NULL) {
        return;
    }
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    bool active = handle->worker != NULL;
    if (active) {
        handle->abort_requested = true;
        handle->status.state = REHAB_SCAN_CAPTURE_STATE_ABORTING;
    }
    xSemaphoreGive(handle->lock);
    if (active && xSemaphoreTake(handle->worker_done,
                                 pdMS_TO_TICKS(REHAB_SCAN_DELETE_WAIT_MS)) != pdTRUE) {
        ESP_LOGE(TAG, "delete timed out; handle retained to protect active worker");
        return;
    }
    if (handle->console_registered) {
        (void)esp_console_cmd_deregister("rehab_scan");
    }
    vSemaphoreDelete(handle->worker_done);
    vSemaphoreDelete(handle->lock);
    free(handle);
}
