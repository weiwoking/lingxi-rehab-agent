// SPDX-License-Identifier: Apache-2.0
#include "rehab_environment_scan.h"
#include "rehab_business_audio.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_console.h"
#include "esp_log.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "rehab_follow.h"

#define ENV_SCAN_POLL_MS 50U
#define ENV_SCAN_DELETE_WAIT_MS 3000U

static const char *TAG = "rehab_env_scan";

typedef struct {
    rehab_environment_scan_handle_t owner;
    rehab_scan_capture_status_t capture_status;
    rehab_scan_transfer_status_t transfer_status;
    char scan_id[REHAB_SCAN_CAPTURE_SCAN_ID_MAX];
    uint32_t generation;
} rehab_environment_scan_worker_context_t;

struct rehab_environment_scan {
    rehab_environment_scan_config_t config;
    SemaphoreHandle_t lock;
    SemaphoreHandle_t worker_started;
    SemaphoreHandle_t worker_done;
    TaskHandle_t worker;
    rehab_environment_scan_worker_context_t *worker_context;
    rehab_car_control_lease_t lease;
    bool lease_acquired;
    bool console_registered;
    esp_err_t startup_result;
    rehab_environment_scan_status_t status;
    uint32_t generation;
};

static bool state_active(rehab_environment_scan_state_t state)
{
    switch (state) {
    case REHAB_ENVIRONMENT_SCAN_STATE_ACQUIRING_LEASE:
    case REHAB_ENVIRONMENT_SCAN_STATE_WARMING_CAMERA:
    case REHAB_ENVIRONMENT_SCAN_STATE_ROTATING:
    case REHAB_ENVIRONMENT_SCAN_STATE_STOPPING:
    case REHAB_ENVIRONMENT_SCAN_STATE_SETTLING:
    case REHAB_ENVIRONMENT_SCAN_STATE_CAPTURING:
    case REHAB_ENVIRONMENT_SCAN_STATE_WRITING:
    case REHAB_ENVIRONMENT_SCAN_STATE_COMMITTING_SCAN:
    case REHAB_ENVIRONMENT_SCAN_STATE_UPLOADING:
    case REHAB_ENVIRONMENT_SCAN_STATE_ANALYZING:
    case REHAB_ENVIRONMENT_SCAN_STATE_CANCELLING:
        return true;
    case REHAB_ENVIRONMENT_SCAN_STATE_IDLE:
    case REHAB_ENVIRONMENT_SCAN_STATE_DECISION_READY:
    case REHAB_ENVIRONMENT_SCAN_STATE_CANCELLED:
    case REHAB_ENVIRONMENT_SCAN_STATE_FAILED:
    default:
        return false;
    }
}

const char *rehab_environment_scan_state_name(
    rehab_environment_scan_state_t state)
{
    switch (state) {
    case REHAB_ENVIRONMENT_SCAN_STATE_IDLE: return "IDLE";
    case REHAB_ENVIRONMENT_SCAN_STATE_ACQUIRING_LEASE: return "ACQUIRING_LEASE";
    case REHAB_ENVIRONMENT_SCAN_STATE_WARMING_CAMERA: return "WARMING_CAMERA";
    case REHAB_ENVIRONMENT_SCAN_STATE_ROTATING: return "ROTATING";
    case REHAB_ENVIRONMENT_SCAN_STATE_STOPPING: return "STOPPING";
    case REHAB_ENVIRONMENT_SCAN_STATE_SETTLING: return "SETTLING";
    case REHAB_ENVIRONMENT_SCAN_STATE_CAPTURING: return "CAPTURING";
    case REHAB_ENVIRONMENT_SCAN_STATE_WRITING: return "WRITING";
    case REHAB_ENVIRONMENT_SCAN_STATE_COMMITTING_SCAN: return "COMMITTING_SCAN";
    case REHAB_ENVIRONMENT_SCAN_STATE_UPLOADING: return "UPLOADING";
    case REHAB_ENVIRONMENT_SCAN_STATE_ANALYZING: return "ANALYZING";
    case REHAB_ENVIRONMENT_SCAN_STATE_DECISION_READY: return "DECISION_READY";
    case REHAB_ENVIRONMENT_SCAN_STATE_CANCELLING: return "CANCELLING";
    case REHAB_ENVIRONMENT_SCAN_STATE_CANCELLED: return "CANCELLED";
    case REHAB_ENVIRONMENT_SCAN_STATE_FAILED: return "FAILED";
    default: return "UNKNOWN";
    }
}

static void set_state(rehab_environment_scan_handle_t handle,
                      rehab_environment_scan_state_t state)
{
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    handle->status.state = state;
    handle->status.active = state_active(state);
    xSemaphoreGive(handle->lock);
}

static bool cancel_requested(rehab_environment_scan_handle_t handle)
{
    bool requested = true;
    if (xSemaphoreTake(handle->lock, portMAX_DELAY) == pdTRUE) {
        requested = handle->status.cancel_requested;
        xSemaphoreGive(handle->lock);
    }
    return requested;
}

static void log_stack_high_water(rehab_environment_scan_handle_t handle,
                                 const char *point)
{
    TaskHandle_t worker;
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    worker = handle->worker;
    xSemaphoreGive(handle->lock);
    if (worker == NULL) {
        return;
    }
    size_t remaining_bytes =
        (size_t)uxTaskGetStackHighWaterMark(worker) * sizeof(StackType_t);
    ESP_LOGI(TAG,
             "stack_high_water point=\"%s\" remaining_bytes=%u",
             point, (unsigned)remaining_bytes);
}

static void set_terminal_failure(rehab_environment_scan_handle_t handle,
                                 esp_err_t err,
                                 const char *reason)
{
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    bool cancelled = handle->status.cancel_requested;
    handle->status.state = cancelled
        ? REHAB_ENVIRONMENT_SCAN_STATE_CANCELLED
        : REHAB_ENVIRONMENT_SCAN_STATE_FAILED;
    handle->status.active = false;
    handle->status.last_error_code = err;
    strlcpy(handle->status.last_error,
            reason != NULL ? reason : "unknown",
            sizeof(handle->status.last_error));
    xSemaphoreGive(handle->lock);
}

static esp_err_t cooperative_wait(rehab_environment_scan_handle_t handle,
                                  uint32_t wait_ms)
{
    uint32_t waited_ms = 0;
    while (waited_ms < wait_ms) {
        if (cancel_requested(handle)) {
            return ESP_ERR_INVALID_STATE;
        }
        uint32_t slice_ms = wait_ms - waited_ms;
        if (slice_ms > ENV_SCAN_POLL_MS) {
            slice_ms = ENV_SCAN_POLL_MS;
        }
        vTaskDelay(pdMS_TO_TICKS(slice_ms));
        waited_ms += slice_ms;
    }
    return ESP_OK;
}

static esp_err_t guided_prepare(void *ctx)
{
    rehab_environment_scan_handle_t handle = ctx;
    set_state(handle, REHAB_ENVIRONMENT_SCAN_STATE_WARMING_CAMERA);
    for (uint32_t i = 0; i < REHAB_ENVIRONMENT_SCAN_WARMUP_FRAMES; ++i) {
        if (cancel_requested(handle)) {
            return ESP_ERR_INVALID_STATE;
        }
        rehab_follow_camera_raw_frame_t frame = {0};
        esp_err_t err = rehab_follow_camera_raw_snapshot_acquire(
            &frame, REHAB_SCAN_CAPTURE_DEFAULT_CAMERA_TIMEOUT_MS);
        if (err != ESP_OK) {
            return err;
        }
        bool valid = frame.data != NULL && frame.length > 0 &&
                     frame.lease_token != 0;
        rehab_follow_camera_raw_snapshot_release(&frame);
        if (!valid) {
            return ESP_ERR_INVALID_RESPONSE;
        }
        xSemaphoreTake(handle->lock, portMAX_DELAY);
        handle->status.warmup_completed = i + 1U;
        xSemaphoreGive(handle->lock);
    }
    log_stack_high_water(handle, "after warmup");
    return ESP_OK;
}

static esp_err_t guided_before_frame(void *ctx, uint32_t sequence)
{
    rehab_environment_scan_handle_t handle = ctx;
    if (cancel_requested(handle)) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    handle->status.direction_index = sequence;
    xSemaphoreGive(handle->lock);

    if (sequence > 0U) {
        set_state(handle, REHAB_ENVIRONMENT_SCAN_STATE_ROTATING);
        esp_err_t move_err = rehab_car_control_move(
            handle->config.car, &handle->lease,
            REHAB_CAR_DIRECTION_ROTATE_CCW,
            REHAB_ENVIRONMENT_SCAN_ROTATE_SPEED,
            REHAB_ENVIRONMENT_SCAN_ROTATE_MS);

        set_state(handle, REHAB_ENVIRONMENT_SCAN_STATE_STOPPING);
        esp_err_t stop_err = rehab_car_control_stop(
            handle->config.car, &handle->lease);
        if (move_err != ESP_OK) {
            return move_err;
        }
        if (stop_err != ESP_OK) {
            return stop_err;
        }
        if (cancel_requested(handle)) {
            return ESP_ERR_INVALID_STATE;
        }
        set_state(handle, REHAB_ENVIRONMENT_SCAN_STATE_SETTLING);
        esp_err_t wait_err = cooperative_wait(
            handle, REHAB_ENVIRONMENT_SCAN_SETTLE_MS);
        if (wait_err != ESP_OK) {
            return wait_err;
        }
    }
    set_state(handle, REHAB_ENVIRONMENT_SCAN_STATE_CAPTURING);
    return ESP_OK;
}

static void guided_before_write(void *ctx, uint32_t sequence)
{
    (void)sequence;
    set_state(ctx, REHAB_ENVIRONMENT_SCAN_STATE_WRITING);
}

static void guided_after_frame(void *ctx, uint32_t sequence)
{
    rehab_environment_scan_handle_t handle = ctx;
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    handle->status.direction_index = sequence;
    handle->status.directions_completed = sequence + 1U;
    xSemaphoreGive(handle->lock);
    if (sequence == 0U || sequence == 5U || sequence == 10U) {
        char point[32];
        snprintf(point, sizeof(point), "after formal frame %u",
                 (unsigned)sequence);
        log_stack_high_water(handle, point);
    }
}

static void guided_before_commit(void *ctx)
{
    rehab_environment_scan_handle_t handle = ctx;
    set_state(handle, REHAB_ENVIRONMENT_SCAN_STATE_COMMITTING_SCAN);
    log_stack_high_water(handle, "before final commit");
}

static void copy_transfer_status(
    rehab_environment_scan_handle_t handle,
    const rehab_scan_transfer_status_t *transfer)
{
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    handle->status.upload_state = transfer->state;
    handle->status.package_length = transfer->result_bytes;
    strlcpy(handle->status.package_sha256, transfer->package_sha256,
            sizeof(handle->status.package_sha256));
    strlcpy(handle->status.scene_result_path, transfer->result_path,
            sizeof(handle->status.scene_result_path));
    if (transfer->error_code[0] != '\0') {
        strlcpy(handle->status.upload_error, transfer->error_code,
                sizeof(handle->status.upload_error));
    }
    xSemaphoreGive(handle->lock);
}

static void stop_release(rehab_environment_scan_handle_t handle)
{
    rehab_car_control_lease_t lease;
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    if (!handle->lease_acquired) {
        xSemaphoreGive(handle->lock);
        return;
    }
    lease = handle->lease;
    xSemaphoreGive(handle->lock);

    (void)rehab_car_control_stop(handle->config.car, &lease);
    (void)rehab_car_control_release(handle->config.car, &lease);

    xSemaphoreTake(handle->lock, portMAX_DELAY);
    handle->lease_acquired = false;
    memset(&handle->lease, 0, sizeof(handle->lease));
    xSemaphoreGive(handle->lock);
}

static void signal_worker_started(rehab_environment_scan_handle_t handle,
                                  esp_err_t result)
{
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    handle->startup_result = result;
    xSemaphoreGive(handle->lock);
    xSemaphoreGive(handle->worker_started);
}

static void environment_worker(void *ctx)
{
    rehab_environment_scan_worker_context_t *worker_context = ctx;
    rehab_environment_scan_handle_t handle = worker_context->owner;
    esp_err_t err = ESP_OK;
    const char *reason = "capture_failed";
    bool startup_signalled = false;
    bool capture_started = false;
    bool final_committed = false;

    log_stack_high_water(handle, "task start");
    if (cancel_requested(handle)) {
        err = ESP_ERR_INVALID_STATE;
        reason = "cancelled_before_lease";
        goto failed;
    }

    err = rehab_car_control_acquire(
        handle->config.car, REHAB_CAR_CONTROL_OWNER_ENVIRONMENT_SCAN,
        NULL, NULL, &handle->lease);
    if (err != ESP_OK) {
        reason = "vehicle_busy";
        goto failed;
    }
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    handle->lease_acquired = true;
    xSemaphoreGive(handle->lock);
    if (cancel_requested(handle)) {
        err = ESP_ERR_INVALID_STATE;
        reason = "cancelled_after_lease";
        goto failed;
    }

    const rehab_scan_capture_guided_request_t request = {
        .frame_count = REHAB_ENVIRONMENT_SCAN_DIRECTIONS,
        .prepare = guided_prepare,
        .before_frame = guided_before_frame,
        .before_write = guided_before_write,
        .after_frame = guided_after_frame,
        .before_commit = guided_before_commit,
        .ctx = handle,
    };
    err = rehab_scan_capture_start_guided(
        handle->config.capture, &request,
        worker_context->scan_id, sizeof(worker_context->scan_id));
    if (err != ESP_OK) {
        reason = "capture_start_failed";
        goto failed;
    }
    capture_started = true;
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    strlcpy(handle->status.scan_id, worker_context->scan_id,
            sizeof(handle->status.scan_id));
    xSemaphoreGive(handle->lock);
    signal_worker_started(handle, ESP_OK);
    startup_signalled = true;
    (void)rehab_business_audio_notify(REHAB_BUSINESS_AUDIO_ENV_SCAN_STARTED);

    while (true) {
        err = rehab_scan_capture_get_status(
            handle->config.capture, &worker_context->capture_status);
        if (err != ESP_OK) {
            reason = "capture_status_failed";
            goto failed;
        }
        if (worker_context->capture_status.state ==
            REHAB_SCAN_CAPTURE_STATE_COMPLETED) {
            break;
        }
        if (worker_context->capture_status.state ==
            REHAB_SCAN_CAPTURE_STATE_FAILED) {
            err = worker_context->capture_status.last_error != ESP_OK
                ? worker_context->capture_status.last_error : ESP_FAIL;
            reason = worker_context->capture_status.aborted
                ? "capture_cancelled" : "capture_failed";
            goto failed;
        }
        vTaskDelay(pdMS_TO_TICKS(ENV_SCAN_POLL_MS));
    }

    final_committed = true;
    stop_release(handle);
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    strlcpy(handle->status.final_path,
            worker_context->capture_status.final_path,
            sizeof(handle->status.final_path));
    handle->status.retryable = true;
    xSemaphoreGive(handle->lock);

    if (cancel_requested(handle)) {
        err = ESP_ERR_INVALID_STATE;
        reason = "cancelled_before_upload";
        goto failed;
    }

    set_state(handle, REHAB_ENVIRONMENT_SCAN_STATE_UPLOADING);
    log_stack_high_water(handle, "before upload submit");
    char submitted_scan_id[REHAB_SCAN_TRANSFER_SCAN_ID_MAX];
    err = rehab_scan_transfer_start_auto_decision(
        handle->config.transfer, worker_context->scan_id,
        worker_context->generation,
        submitted_scan_id, sizeof(submitted_scan_id));
    if (err != ESP_OK ||
        strcmp(submitted_scan_id, worker_context->scan_id) != 0) {
        if (err == ESP_OK) {
            err = ESP_ERR_INVALID_RESPONSE;
        }
        xSemaphoreTake(handle->lock, portMAX_DELAY);
        handle->status.upload_state = REHAB_SCAN_TRANSFER_STATE_FAILED;
        strlcpy(handle->status.upload_error, "E_UPLOAD_SUBMIT",
                sizeof(handle->status.upload_error));
        xSemaphoreGive(handle->lock);
        reason = "upload_submit_failed";
        goto failed;
    }

    while (true) {
        if (cancel_requested(handle)) {
            (void)rehab_scan_transfer_abort(handle->config.transfer);
        }
        err = rehab_scan_transfer_get_status(
            handle->config.transfer, &worker_context->transfer_status);
        if (err != ESP_OK) {
            reason = "upload_status_failed";
            goto failed;
        }
        copy_transfer_status(handle, &worker_context->transfer_status);
        if (worker_context->transfer_status.state ==
                REHAB_SCAN_TRANSFER_STATE_WAITING_PROCESSING ||
            worker_context->transfer_status.state ==
                REHAB_SCAN_TRANSFER_STATE_RECEIVING_RESULT) {
            set_state(handle, REHAB_ENVIRONMENT_SCAN_STATE_ANALYZING);
        }
        if (worker_context->transfer_status.state ==
            REHAB_SCAN_TRANSFER_STATE_COMPLETED) {
            break;
        }
        if (worker_context->transfer_status.state ==
            REHAB_SCAN_TRANSFER_STATE_FAILED) {
            err = worker_context->transfer_status.last_error != ESP_OK
                ? worker_context->transfer_status.last_error : ESP_FAIL;
            reason = worker_context->transfer_status.aborted
                ? "upload_cancelled" : "upload_failed";
            goto failed;
        }
        vTaskDelay(pdMS_TO_TICKS(ENV_SCAN_POLL_MS));
    }

    if (cancel_requested(handle)) {
        err = ESP_ERR_INVALID_STATE;
        reason = "upload_cancelled";
        goto failed;
    }
    log_stack_high_water(handle, "after upload completion");
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    handle->status.state = REHAB_ENVIRONMENT_SCAN_STATE_DECISION_READY;
    handle->status.active = false;
    handle->status.retryable = false;
    handle->status.last_error_code = ESP_OK;
    handle->status.last_error[0] = '\0';
    handle->status.upload_error[0] = '\0';
    xSemaphoreGive(handle->lock);
    (void)rehab_business_audio_notify(
        REHAB_BUSINESS_AUDIO_ENV_SCAN_COMPLETED);
    goto done;

failed:
    if (!startup_signalled) {
        signal_worker_started(handle, err);
        startup_signalled = true;
    }
    if (capture_started && !final_committed) {
        (void)rehab_scan_capture_abort(handle->config.capture);
    }
    stop_release(handle);
    if (final_committed) {
        log_stack_high_water(handle, "after upload completion");
    }
    set_terminal_failure(handle, err, reason);

done:
    log_stack_high_water(handle, "before worker exit");
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    handle->worker = NULL;
    handle->worker_context = NULL;
    xSemaphoreGive(handle->lock);
    free(worker_context);
    xSemaphoreGive(handle->worker_done);
    vTaskDelete(NULL);
}

esp_err_t rehab_environment_scan_create(
    const rehab_environment_scan_config_t *config,
    rehab_environment_scan_handle_t *out_handle)
{
    if (config == NULL || out_handle == NULL || config->car == NULL ||
        config->capture == NULL || config->transfer == NULL ||
        config->worker_stack_size == 0 ||
        config->worker_priority == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_handle = NULL;
    rehab_environment_scan_handle_t handle = calloc(1, sizeof(*handle));
    if (handle == NULL) {
        return ESP_ERR_NO_MEM;
    }
    handle->config = *config;
    handle->lock = xSemaphoreCreateMutex();
    handle->worker_started = xSemaphoreCreateBinary();
    handle->worker_done = xSemaphoreCreateBinary();
    if (handle->lock == NULL || handle->worker_started == NULL ||
        handle->worker_done == NULL) {
        if (handle->worker_done != NULL) vSemaphoreDelete(handle->worker_done);
        if (handle->worker_started != NULL) vSemaphoreDelete(handle->worker_started);
        if (handle->lock != NULL) vSemaphoreDelete(handle->lock);
        free(handle);
        return ESP_ERR_NO_MEM;
    }
    handle->status.state = REHAB_ENVIRONMENT_SCAN_STATE_IDLE;
    handle->status.directions_total = REHAB_ENVIRONMENT_SCAN_DIRECTIONS;
    handle->status.warmup_total = REHAB_ENVIRONMENT_SCAN_WARMUP_FRAMES;
    *out_handle = handle;
    return ESP_OK;
}

esp_err_t rehab_environment_scan_get_status(
    rehab_environment_scan_handle_t handle,
    rehab_environment_scan_status_t *out_status)
{
    if (handle == NULL || out_status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    *out_status = handle->status;
    xSemaphoreGive(handle->lock);
    return ESP_OK;
}

esp_err_t rehab_environment_scan_start(
    rehab_environment_scan_handle_t handle,
    bool *out_busy,
    rehab_environment_scan_status_t *out_status)
{
    if (handle == NULL || out_busy == NULL || out_status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_busy = false;
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    if (handle->worker != NULL || state_active(handle->status.state)) {
        *out_busy = true;
        *out_status = handle->status;
        xSemaphoreGive(handle->lock);
        return ESP_OK;
    }
    rehab_environment_scan_worker_context_t *worker_context =
        calloc(1, sizeof(*worker_context));
    if (worker_context == NULL) {
        handle->status.state = REHAB_ENVIRONMENT_SCAN_STATE_FAILED;
        handle->status.active = false;
        handle->status.last_error_code = ESP_ERR_NO_MEM;
        strlcpy(handle->status.last_error, "worker_context_allocation_failed",
                sizeof(handle->status.last_error));
        *out_status = handle->status;
        xSemaphoreGive(handle->lock);
        return ESP_ERR_NO_MEM;
    }
    worker_context->owner = handle;
    handle->generation++;
    if (handle->generation == 0U) {
        handle->generation = 1U;
    }
    worker_context->generation = handle->generation;
    memset(&handle->status, 0, sizeof(handle->status));
    handle->status.state = REHAB_ENVIRONMENT_SCAN_STATE_ACQUIRING_LEASE;
    handle->status.active = true;
    handle->status.directions_total = REHAB_ENVIRONMENT_SCAN_DIRECTIONS;
    handle->status.warmup_total = REHAB_ENVIRONMENT_SCAN_WARMUP_FRAMES;
    handle->status.generation = handle->generation;
    handle->startup_result = ESP_ERR_NOT_FINISHED;
    handle->worker_context = worker_context;
    (void)xSemaphoreTake(handle->worker_started, 0);
    (void)xSemaphoreTake(handle->worker_done, 0);
    BaseType_t task_result = xTaskCreate(
        environment_worker, "rehab_env_scan", handle->config.worker_stack_size,
        worker_context, handle->config.worker_priority, &handle->worker);
    if (task_result != pdPASS) {
        handle->worker_context = NULL;
        handle->status.state = REHAB_ENVIRONMENT_SCAN_STATE_FAILED;
        handle->status.active = false;
        handle->status.last_error_code = ESP_ERR_NO_MEM;
        strlcpy(handle->status.last_error, "worker_create_failed",
                sizeof(handle->status.last_error));
        *out_status = handle->status;
        xSemaphoreGive(handle->lock);
        free(worker_context);
        return ESP_ERR_NO_MEM;
    }
    xSemaphoreGive(handle->lock);

    xSemaphoreTake(handle->worker_started, portMAX_DELAY);
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    esp_err_t startup_result = handle->startup_result;
    *out_status = handle->status;
    xSemaphoreGive(handle->lock);
    return startup_result;
}

esp_err_t rehab_environment_scan_cancel(
    rehab_environment_scan_handle_t handle,
    bool *out_already_idle,
    rehab_environment_scan_status_t *out_status)
{
    if (handle == NULL || out_already_idle == NULL || out_status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    rehab_car_control_lease_t lease;
    bool have_lease;
    bool was_uploading;
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    *out_already_idle = !state_active(handle->status.state);
    if (*out_already_idle) {
        *out_status = handle->status;
        xSemaphoreGive(handle->lock);
        return ESP_OK;
    }
    handle->status.cancel_requested = true;
    was_uploading =
        handle->status.state == REHAB_ENVIRONMENT_SCAN_STATE_UPLOADING ||
        handle->status.state == REHAB_ENVIRONMENT_SCAN_STATE_ANALYZING;
    handle->status.state = REHAB_ENVIRONMENT_SCAN_STATE_CANCELLING;
    have_lease = handle->lease_acquired;
    lease = handle->lease;
    *out_status = handle->status;
    xSemaphoreGive(handle->lock);

    if (was_uploading) {
        (void)rehab_scan_transfer_abort(handle->config.transfer);
    } else {
        (void)rehab_scan_capture_abort(handle->config.capture);
    }
    if (have_lease) {
        (void)rehab_car_control_stop(handle->config.car, &lease);
    }
    return ESP_OK;
}

static int environment_console_handler(void *ctx, int argc, char **argv)
{
    rehab_environment_scan_handle_t handle = ctx;
    if (handle == NULL || argc != 2) {
        printf("error=usage usage='rehab_env_scan <start|status|cancel>'\n");
        return 1;
    }
    rehab_environment_scan_status_t status;
    if (strcmp(argv[1], "start") == 0) {
        bool busy = false;
        esp_err_t err = rehab_environment_scan_start(handle, &busy, &status);
        if (err != ESP_OK) {
            printf("error=%s code=%d state=%s\n", esp_err_to_name(err),
                   (int)err, rehab_environment_scan_state_name(status.state));
            return 1;
        }
        printf("state=%s busy=%d scan_id=%s\n",
               rehab_environment_scan_state_name(status.state),
               busy, status.scan_id);
        return busy ? 1 : 0;
    }
    if (strcmp(argv[1], "status") == 0) {
        esp_err_t err = rehab_environment_scan_get_status(handle, &status);
        if (err != ESP_OK) {
            printf("error=%s code=%d\n", esp_err_to_name(err), (int)err);
            return 1;
        }
        printf("state=%s active=%d scan_id=%s generation=%u warmup=%u/%u "
               "directions=%u/%u direction=%u cancel=%d "
               "final_path=%s upload_state=%s upload_error=%s retryable=%d "
               "package_length=%u package_sha256=%s scene_result_path=%s "
               "last_error=%d reason=%s\n",
               rehab_environment_scan_state_name(status.state), status.active,
               status.scan_id, (unsigned)status.generation,
               (unsigned)status.warmup_completed,
               (unsigned)status.warmup_total,
               (unsigned)status.directions_completed,
               (unsigned)status.directions_total,
               (unsigned)status.direction_index, status.cancel_requested,
               status.final_path,
               rehab_scan_transfer_state_name(status.upload_state),
               status.upload_error, status.retryable,
               (unsigned)status.package_length, status.package_sha256,
               status.scene_result_path,
               (int)status.last_error_code, status.last_error);
        return 0;
    }
    if (strcmp(argv[1], "cancel") == 0) {
        bool already_idle = false;
        esp_err_t err = rehab_environment_scan_cancel(
            handle, &already_idle, &status);
        if (err != ESP_OK) {
            printf("error=%s code=%d\n", esp_err_to_name(err), (int)err);
            return 1;
        }
        printf("state=%s already_idle=%d scan_id=%s\n",
               rehab_environment_scan_state_name(status.state),
               already_idle, status.scan_id);
        return 0;
    }
    printf("error=usage usage='rehab_env_scan <start|status|cancel>'\n");
    return 1;
}

esp_err_t rehab_environment_scan_console_register(
    rehab_environment_scan_handle_t handle)
{
    if (handle == NULL || handle->console_registered) {
        return ESP_ERR_INVALID_ARG;
    }
    const esp_console_cmd_t command = {
        .command = "rehab_env_scan",
        .help = "Run the local 11-direction RGB+ToF SD scan",
        .hint = "<start|status|cancel>",
        .func_w_context = environment_console_handler,
        .context = handle,
    };
    esp_err_t err = esp_console_cmd_register(&command);
    if (err == ESP_OK) {
        handle->console_registered = true;
    }
    return err;
}

void rehab_environment_scan_delete(rehab_environment_scan_handle_t handle)
{
    if (handle == NULL) {
        return;
    }
    bool already_idle = false;
    rehab_environment_scan_status_t status;
    (void)rehab_environment_scan_cancel(handle, &already_idle, &status);
    xSemaphoreTake(handle->lock, portMAX_DELAY);
    bool worker_alive = handle->worker != NULL;
    xSemaphoreGive(handle->lock);
    if (worker_alive &&
        xSemaphoreTake(handle->worker_done,
                       pdMS_TO_TICKS(ENV_SCAN_DELETE_WAIT_MS)) != pdTRUE) {
        ESP_LOGE(TAG, "delete timed out; handle retained to protect active worker");
        return;
    }
    stop_release(handle);
    if (handle->console_registered) {
        (void)esp_console_cmd_deregister("rehab_env_scan");
    }
    vSemaphoreDelete(handle->worker_done);
    vSemaphoreDelete(handle->worker_started);
    vSemaphoreDelete(handle->lock);
    free(handle);
}
