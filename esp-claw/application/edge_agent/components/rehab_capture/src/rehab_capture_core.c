// SPDX-License-Identifier: Apache-2.0

#include "rehab_capture_priv.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"

#define CAPTURE_COORDINATOR_STACK 6144U
#define CAPTURE_CAMERA_STACK      4096U
#define CAPTURE_TASK_PRIORITY     4U
#define CAPTURE_CAMERA_TIMEOUT_MS 1500U
#define CAPTURE_TOF_TIMEOUT_MS    3500U
#define CAPTURE_JOIN_TIMEOUT_MS   2200U

static const char *TAG = "rehab_capture";
rehab_capture_context_t g_rehab_capture;

static void capture_set_error_locked(const char *error)
{
    strlcpy(g_rehab_capture.status.last_error,
            error != NULL ? error : "unknown",
            sizeof(g_rehab_capture.status.last_error));
}

static bool capture_local_quiescent_locked(void)
{
    return !g_rehab_capture.busy &&
           !g_rehab_capture.waiter_active &&
           !g_rehab_capture.request_pending &&
           !g_rehab_capture.coordinator_active &&
           !g_rehab_capture.packet_lease;
}

static bool capture_camera_contract_valid(
    const rehab_follow_camera_raw_frame_t *frame)
{
    return frame->data != NULL &&
           frame->length == REHAB_CAPTURE_IMAGE_BYTES &&
           frame->width == REHAB_CAPTURE_IMAGE_WIDTH &&
           frame->height == REHAB_CAPTURE_IMAGE_HEIGHT &&
           frame->stride == REHAB_CAPTURE_IMAGE_STRIDE &&
           frame->pixel_format == REHAB_FOLLOW_CAMERA_PIXEL_FORMAT_RGB565 &&
           frame->byte_order == REHAB_FOLLOW_CAMERA_BYTE_ORDER_LITTLE_ENDIAN &&
           frame->generation != 0 && frame->timestamp_us > 0;
}

static esp_err_t capture_select_nearest(
    const rehab_tof_window_result_t *window,
    int64_t image_timestamp_us,
    uint8_t *selected_index)
{
    if (window == NULL || selected_index == NULL || image_timestamp_us <= 0 ||
        window->actual_candidate_count == 0 ||
        window->actual_candidate_count > REHAB_TOF_MAX_CANDIDATE_FRAMES) {
        return ESP_ERR_INVALID_ARG;
    }
    bool found = false;
    uint64_t best_delta = UINT64_MAX;
    int64_t best_timestamp = INT64_MIN;
    uint8_t best_index = 0;
    for (uint8_t i = 0; i < window->actual_candidate_count; ++i) {
        const rehab_tof_candidate_frame_t *candidate = &window->candidates[i];
        if (candidate->tof_frame_seq == 0 || candidate->timestamp_us <= 0) {
            continue;
        }
        int64_t signed_delta = candidate->timestamp_us - image_timestamp_us;
        uint64_t delta = signed_delta < 0
            ? (uint64_t)(-signed_delta) : (uint64_t)signed_delta;
        if (!found || delta < best_delta ||
            (delta == best_delta && candidate->timestamp_us > best_timestamp)) {
            found = true;
            best_delta = delta;
            best_timestamp = candidate->timestamp_us;
            best_index = i;
        }
    }
    if (!found) {
        return ESP_ERR_NOT_FOUND;
    }
    *selected_index = best_index;
    return ESP_OK;
}

static void capture_warmup_notify(void *ctx)
{
    uint64_t request_seq = (uint64_t)(uintptr_t)ctx;
    TaskHandle_t camera_task = NULL;
    xSemaphoreTake(g_rehab_capture.lock, portMAX_DELAY);
    if (!g_rehab_capture.stop_requested &&
        g_rehab_capture.coordinator_active &&
        g_rehab_capture.active_request_seq == request_seq) {
        g_rehab_capture.camera_started = true;
        camera_task = g_rehab_capture.camera_task;
    }
    xSemaphoreGive(g_rehab_capture.lock);
    if (camera_task != NULL) {
        xTaskNotifyGive(camera_task);
    }
}

static void capture_camera_task(void *argument)
{
    (void)argument;
    for (;;) {
        (void)ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        if (g_rehab_capture.stop_requested) {
            break;
        }
        uint64_t request_seq = g_rehab_capture.active_request_seq;
        rehab_follow_camera_raw_frame_t frame = {0};
        esp_err_t result = rehab_follow_camera_raw_snapshot_acquire(
            &frame, CAPTURE_CAMERA_TIMEOUT_MS);

        bool accepted = false;
        xSemaphoreTake(g_rehab_capture.lock, portMAX_DELAY);
        if (g_rehab_capture.coordinator_active &&
            g_rehab_capture.active_request_seq == request_seq) {
            g_rehab_capture.camera_result = result;
            if (result == ESP_OK) {
                g_rehab_capture.camera_frame = frame;
            }
            accepted = true;
        }
        xSemaphoreGive(g_rehab_capture.lock);
        if (!accepted && result == ESP_OK) {
            rehab_follow_camera_raw_snapshot_release(&frame);
        }
        if (accepted) {
            xSemaphoreGive(g_rehab_capture.camera_done);
        }
    }
    xSemaphoreGive(g_rehab_capture.camera_stopped);
    vTaskDelete(NULL);
}

static void capture_finish_request(esp_err_t result, const char *error,
                                   bool publish_packet)
{
    bool notify_waiter;
    xSemaphoreTake(g_rehab_capture.lock, portMAX_DELAY);
    g_rehab_capture.request_result = result;
    notify_waiter = g_rehab_capture.waiter_active;
    g_rehab_capture.packet_lease =
        result == ESP_OK && publish_packet && notify_waiter;
    g_rehab_capture.status.packet_lease = g_rehab_capture.packet_lease;
    g_rehab_capture.busy = false;
    g_rehab_capture.status.busy = false;
    g_rehab_capture.coordinator_active = false;
    g_rehab_capture.status.coordinator_active = false;
    g_rehab_capture.status.capture_quiescent =
        capture_local_quiescent_locked();
    g_rehab_capture.status.pipeline_quiescent =
        g_rehab_capture.status.capture_quiescent;
    if (notify_waiter) {
        if (result == ESP_OK) {
            ++g_rehab_capture.status.success_count;
        } else {
            ++g_rehab_capture.status.error_count;
        }
    }
    capture_set_error_locked(error);
    xSemaphoreGive(g_rehab_capture.lock);
    if (notify_waiter) {
        xSemaphoreGive(g_rehab_capture.request_done);
    }
}

static void capture_update_success_status(const rehab_capture_packet_t *packet)
{
    xSemaphoreTake(g_rehab_capture.lock, portMAX_DELAY);
    g_rehab_capture.status.last_image_seq = packet->image_seq;
    g_rehab_capture.status.last_tof_seq = packet->tof_seq;
    g_rehab_capture.status.last_actual_candidate_count =
        packet->actual_candidate_count;
    g_rehab_capture.status.last_selected_index = packet->selected_index;
    g_rehab_capture.status.last_signed_offset_us = packet->signed_offset_us;
    g_rehab_capture.status.last_absolute_offset_us = packet->absolute_offset_us;
    g_rehab_capture.status.last_offset_ms = packet->offset_ms;
    g_rehab_capture.status.last_pair_within_limit = packet->pair_within_limit;
    g_rehab_capture.status.last_ready_for_cloud = packet->ready_for_cloud;
    g_rehab_capture.status.last_packet_content_length = packet->content_length;
    xSemaphoreGive(g_rehab_capture.lock);
}

static void capture_coordinator_task(void *argument)
{
    (void)argument;
    for (;;) {
        (void)ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        if (g_rehab_capture.stop_requested) {
            break;
        }
        xSemaphoreTake(g_rehab_capture.lock, portMAX_DELAY);
        g_rehab_capture.request_pending = false;
        g_rehab_capture.status.request_pending = false;
        g_rehab_capture.coordinator_active = true;
        g_rehab_capture.status.coordinator_active = true;
        g_rehab_capture.status.pipeline_quiescent = false;
        uint64_t request_seq = g_rehab_capture.active_request_seq;
        xSemaphoreGive(g_rehab_capture.lock);
        int64_t started_us = esp_timer_get_time();
        char capture_id[REHAB_CAPTURE_ID_MAX];
        snprintf(capture_id, sizeof(capture_id), "CAP%08" PRIu64, request_seq);
        while (xSemaphoreTake(g_rehab_capture.camera_done, 0) == pdTRUE) {
        }
        g_rehab_capture.camera_started = false;
        g_rehab_capture.camera_result = ESP_ERR_NOT_FINISHED;
        memset(&g_rehab_capture.camera_frame, 0,
               sizeof(g_rehab_capture.camera_frame));

        const rehab_tof_window_config_t config = {
            .candidate_count = REHAB_CAPTURE_TOF_CANDIDATE_COUNT,
            .warmup_notify = capture_warmup_notify,
            .warmup_notify_ctx = (void *)(uintptr_t)request_seq,
        };
        esp_err_t tof_result = rehab_tof_window_acquire(
            &config, &g_rehab_capture.tof_window, CAPTURE_TOF_TIMEOUT_MS);

        esp_err_t camera_result = ESP_ERR_INVALID_STATE;
        if (g_rehab_capture.camera_started) {
            if (xSemaphoreTake(g_rehab_capture.camera_done,
                               pdMS_TO_TICKS(CAPTURE_JOIN_TIMEOUT_MS)) == pdTRUE) {
                xSemaphoreTake(g_rehab_capture.lock, portMAX_DELAY);
                camera_result = g_rehab_capture.camera_result;
                xSemaphoreGive(g_rehab_capture.lock);
            } else {
                camera_result = ESP_ERR_TIMEOUT;
                xSemaphoreTake(g_rehab_capture.lock, portMAX_DELAY);
                ++g_rehab_capture.status.camera_timeout_count;
                xSemaphoreGive(g_rehab_capture.lock);
            }
        }

        if (tof_result != ESP_OK || camera_result != ESP_OK) {
            if (g_rehab_capture.camera_frame.lease_token != 0) {
                rehab_follow_camera_raw_snapshot_release(
                    &g_rehab_capture.camera_frame);
            }
            const char *error = "capture_failed";
            if (tof_result == ESP_ERR_TIMEOUT) {
                error = "tof_candidate_timeout";
                xSemaphoreTake(g_rehab_capture.lock, portMAX_DELAY);
                ++g_rehab_capture.status.tof_timeout_count;
                xSemaphoreGive(g_rehab_capture.lock);
            } else if (tof_result != ESP_OK) {
                error = g_rehab_capture.camera_started
                    ? "tof_candidate_failed" : "tof_warmup_failed";
            } else if (camera_result == ESP_ERR_TIMEOUT) {
                error = "camera_timeout";
            } else {
                error = "camera_capture_failed";
            }
            capture_finish_request(tof_result != ESP_OK ? tof_result : camera_result,
                                   error, false);
            continue;
        }

        if (!capture_camera_contract_valid(&g_rehab_capture.camera_frame)) {
            rehab_follow_camera_raw_snapshot_release(&g_rehab_capture.camera_frame);
            capture_finish_request(ESP_ERR_INVALID_SIZE,
                                   "camera_raw_length_invalid", false);
            continue;
        }
        uint8_t selected_index = 0;
        esp_err_t result = capture_select_nearest(
            &g_rehab_capture.tof_window,
            g_rehab_capture.camera_frame.timestamp_us,
            &selected_index);
        if (result != ESP_OK) {
            rehab_follow_camera_raw_snapshot_release(&g_rehab_capture.camera_frame);
            capture_finish_request(result, "no_valid_tof_frame", false);
            continue;
        }

        uint32_t token = ++g_rehab_capture.packet_token;
        if (token == 0) {
            token = ++g_rehab_capture.packet_token;
        }
        result = rehab_capture_build_packet(
            &g_rehab_capture.packet,
            g_rehab_capture.image_slot,
            &g_rehab_capture.camera_frame,
            &g_rehab_capture.tof_window,
            selected_index,
            capture_id,
            token,
            (uint64_t)(esp_timer_get_time() - started_us));
        rehab_follow_camera_raw_snapshot_release(&g_rehab_capture.camera_frame);
        memset(&g_rehab_capture.camera_frame, 0,
               sizeof(g_rehab_capture.camera_frame));
        if (result != ESP_OK) {
            capture_finish_request(result,
                result == ESP_ERR_INVALID_SIZE ? "tof_json_overflow"
                                               : "packet_build_failed",
                false);
            continue;
        }

        capture_update_success_status(&g_rehab_capture.packet);
        ESP_LOGI(TAG,
            "CAPTURE_PAIR capture_id=%s image_seq=%" PRIu64
            " tof_candidates=%u selected_index=%u tof_seq=%" PRIu64
            " image_ts_us=%" PRId64 " tof_ts_us=%" PRId64
            " signed_offset_us=%" PRId64 " offset_ms=%" PRIu32
            " within_limit=%d image_crc32=%" PRIu32
            " tof_json_bytes=%u multipart_bytes=%u total_us=%" PRIu64,
            g_rehab_capture.packet.capture_id,
            g_rehab_capture.packet.image_seq,
            g_rehab_capture.packet.actual_candidate_count,
            g_rehab_capture.packet.selected_index,
            g_rehab_capture.packet.tof_seq,
            g_rehab_capture.packet.image_timestamp_us,
            g_rehab_capture.packet.tof_timestamp_us,
            g_rehab_capture.packet.signed_offset_us,
            g_rehab_capture.packet.offset_ms,
            g_rehab_capture.packet.pair_within_limit,
            g_rehab_capture.packet.image_crc32,
            (unsigned)g_rehab_capture.packet.tof_json_length,
            (unsigned)g_rehab_capture.packet.content_length,
            g_rehab_capture.packet.total_us);
        capture_finish_request(ESP_OK,
            g_rehab_capture.packet.pair_within_limit ? "none"
                                                     : "pair_time_exceeded",
            true);
    }
    xSemaphoreGive(g_rehab_capture.coordinator_stopped);
    vTaskDelete(NULL);
}

esp_err_t rehab_capture_init(void)
{
    if (g_rehab_capture.initialized) {
        return ESP_ERR_INVALID_STATE;
    }
    memset(&g_rehab_capture, 0, sizeof(g_rehab_capture));
    g_rehab_capture.lock = xSemaphoreCreateMutexStatic(
        &g_rehab_capture.lock_storage);
    g_rehab_capture.request_done = xSemaphoreCreateBinaryStatic(
        &g_rehab_capture.request_done_storage);
    g_rehab_capture.camera_done = xSemaphoreCreateBinaryStatic(
        &g_rehab_capture.camera_done_storage);
    g_rehab_capture.coordinator_stopped = xSemaphoreCreateBinaryStatic(
        &g_rehab_capture.coordinator_stopped_storage);
    g_rehab_capture.camera_stopped = xSemaphoreCreateBinaryStatic(
        &g_rehab_capture.camera_stopped_storage);
    if (g_rehab_capture.lock == NULL || g_rehab_capture.request_done == NULL ||
        g_rehab_capture.camera_done == NULL ||
        g_rehab_capture.coordinator_stopped == NULL ||
        g_rehab_capture.camera_stopped == NULL) {
        return ESP_ERR_NO_MEM;
    }
    g_rehab_capture.image_slot = heap_caps_malloc(
        REHAB_CAPTURE_IMAGE_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (g_rehab_capture.image_slot == NULL) {
        return ESP_ERR_NO_MEM;
    }
    g_rehab_capture.status.configured_candidate_count =
        REHAB_CAPTURE_TOF_CANDIDATE_COUNT;
    g_rehab_capture.status.max_candidate_count =
        REHAB_TOF_MAX_CANDIDATE_FRAMES;
    g_rehab_capture.status.max_pair_offset_ms =
        REHAB_CAPTURE_MAX_PAIR_OFFSET_MS;
    g_rehab_capture.status.next_capture_seq = 1;
    g_rehab_capture.status.capture_quiescent = true;
    g_rehab_capture.status.pipeline_quiescent = true;
    capture_set_error_locked("none");

    if (xTaskCreate(capture_camera_task, "capture_camera",
                    CAPTURE_CAMERA_STACK, NULL, CAPTURE_TASK_PRIORITY,
                    &g_rehab_capture.camera_task) != pdPASS ||
        xTaskCreate(capture_coordinator_task, "capture_coord",
                    CAPTURE_COORDINATOR_STACK, NULL, CAPTURE_TASK_PRIORITY,
                    &g_rehab_capture.coordinator_task) != pdPASS) {
        g_rehab_capture.stop_requested = true;
        if (g_rehab_capture.camera_task != NULL) {
            xTaskNotifyGive(g_rehab_capture.camera_task);
        }
        return ESP_ERR_NO_MEM;
    }
    g_rehab_capture.initialized = true;
    g_rehab_capture.status.ready = true;
    ESP_LOGI(TAG,
             "CAPTURE_READY candidate_count=%u max_candidates=%u image_bytes=%u",
             REHAB_CAPTURE_TOF_CANDIDATE_COUNT,
             REHAB_TOF_MAX_CANDIDATE_FRAMES,
             REHAB_CAPTURE_IMAGE_BYTES);
    return ESP_OK;
}

void rehab_capture_deinit(void)
{
    if (!g_rehab_capture.initialized) {
        return;
    }
    xSemaphoreTake(g_rehab_capture.lock, portMAX_DELAY);
    g_rehab_capture.stop_requested = true;
    bool packet_lease = g_rehab_capture.packet_lease;
    xSemaphoreGive(g_rehab_capture.lock);
    xTaskNotifyGive(g_rehab_capture.coordinator_task);
    xTaskNotifyGive(g_rehab_capture.camera_task);
    if (xSemaphoreTake(g_rehab_capture.coordinator_stopped,
                       pdMS_TO_TICKS(5000)) != pdTRUE ||
        xSemaphoreTake(g_rehab_capture.camera_stopped,
                       pdMS_TO_TICKS(5000)) != pdTRUE || packet_lease) {
        return;
    }
    heap_caps_free(g_rehab_capture.image_slot);
    g_rehab_capture.image_slot = NULL;
    g_rehab_capture.initialized = false;
    g_rehab_capture.status.ready = false;
}

bool rehab_capture_is_ready(void)
{
    return g_rehab_capture.initialized && !g_rehab_capture.stop_requested &&
           rehab_follow_camera_is_ready() && rehab_tof_is_ready();
}

esp_err_t rehab_capture_get_status(rehab_capture_status_t *status)
{
    if (status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (g_rehab_capture.lock == NULL) {
        memset(status, 0, sizeof(*status));
        return ESP_OK;
    }
    xSemaphoreTake(g_rehab_capture.lock, portMAX_DELAY);
    *status = g_rehab_capture.status;
    status->busy = g_rehab_capture.busy;
    status->waiter_active = g_rehab_capture.waiter_active;
    status->request_pending = g_rehab_capture.request_pending;
    status->coordinator_active = g_rehab_capture.coordinator_active;
    status->packet_lease = g_rehab_capture.packet_lease;
    status->capture_quiescent = capture_local_quiescent_locked();
    xSemaphoreGive(g_rehab_capture.lock);

    rehab_tof_status_t tof_status = {0};
    esp_err_t tof_status_result = rehab_tof_get_status(&tof_status);
    status->tof_ready = rehab_tof_is_ready();
    status->tof_request_pending = tof_status_result == ESP_OK &&
                                  tof_status.request_pending;
    status->tof_window_active = tof_status_result == ESP_OK &&
                                tof_status.window_active;
    status->tof_quiescent = tof_status_result == ESP_OK &&
                            rehab_tof_status_is_quiescent(&tof_status);

    rehab_follow_camera_status_t camera_status = {0};
    esp_err_t camera_status_result =
        rehab_follow_camera_get_status(&camera_status);
    status->camera_ready = rehab_follow_camera_is_ready();
    status->camera_request_pending = camera_status_result == ESP_OK &&
                                     camera_status.snapshot_request_pending;
    status->camera_snapshot_active = camera_status_result == ESP_OK &&
                                     camera_status.snapshot_active;
    status->camera_quiescent = camera_status_result == ESP_OK &&
                               camera_status.pipeline_quiescent;
    status->pipeline_quiescent = status->capture_quiescent &&
                                 status->tof_quiescent &&
                                 status->camera_quiescent;
    status->ready = g_rehab_capture.initialized &&
        !g_rehab_capture.stop_requested && status->camera_ready && status->tof_ready;
    return ESP_OK;
}

bool rehab_capture_status_is_quiescent(const rehab_capture_status_t *status)
{
    return status != NULL &&
           !status->busy &&
           !status->waiter_active &&
           !status->request_pending &&
           !status->coordinator_active &&
           !status->packet_lease &&
           status->capture_quiescent &&
           !status->tof_request_pending &&
           !status->tof_window_active &&
           status->tof_quiescent &&
           !status->camera_request_pending &&
           !status->camera_snapshot_active &&
           status->camera_quiescent;
}

static bool capture_quiescence_wait(
    rehab_capture_quiescence_wait_fn wait_fn,
    void *wait_ctx,
    uint32_t wait_ms)
{
    if (wait_ms == 0) {
        return true;
    }
    if (wait_fn != NULL) {
        return wait_fn(wait_ctx, wait_ms);
    }
    TickType_t ticks = pdMS_TO_TICKS(wait_ms);
    vTaskDelay(ticks == 0 ? 1 : ticks);
    return true;
}

esp_err_t rehab_capture_wait_for_quiescence(
    uint32_t timeout_ms,
    uint32_t poll_ms,
    uint32_t settle_ms,
    rehab_capture_quiescence_wait_fn wait_fn,
    void *wait_ctx,
    uint32_t *waited_ms,
    rehab_capture_status_t *last_status)
{
    if (timeout_ms == 0 || poll_ms == 0) {
        return ESP_ERR_INVALID_ARG;
    }

    uint32_t elapsed_ms = 0;
    rehab_capture_status_t status = {0};
    esp_err_t status_result = ESP_ERR_NOT_FINISHED;
    while (elapsed_ms < timeout_ms) {
        uint32_t step_ms = timeout_ms - elapsed_ms;
        if (step_ms > poll_ms) {
            step_ms = poll_ms;
        }
        if (!capture_quiescence_wait(wait_fn, wait_ctx, step_ms)) {
            if (waited_ms != NULL) {
                *waited_ms = elapsed_ms;
            }
            if (last_status != NULL) {
                *last_status = status;
            }
            return ESP_ERR_INVALID_STATE;
        }
        elapsed_ms += step_ms;

        memset(&status, 0, sizeof(status));
        status_result = rehab_capture_get_status(&status);
        if (status_result != ESP_OK ||
            !rehab_capture_status_is_quiescent(&status)) {
            continue;
        }

        uint32_t confirm_ms = timeout_ms - elapsed_ms;
        if (confirm_ms > settle_ms) {
            confirm_ms = settle_ms;
        }
        if (!capture_quiescence_wait(wait_fn, wait_ctx, confirm_ms)) {
            if (waited_ms != NULL) {
                *waited_ms = elapsed_ms;
            }
            if (last_status != NULL) {
                *last_status = status;
            }
            return ESP_ERR_INVALID_STATE;
        }
        elapsed_ms += confirm_ms;
        memset(&status, 0, sizeof(status));
        status_result = rehab_capture_get_status(&status);
        if (status_result == ESP_OK &&
            rehab_capture_status_is_quiescent(&status)) {
            if (waited_ms != NULL) {
                *waited_ms = elapsed_ms;
            }
            if (last_status != NULL) {
                *last_status = status;
            }
            return ESP_OK;
        }
    }

    if (waited_ms != NULL) {
        *waited_ms = elapsed_ms;
    }
    if (last_status != NULL) {
        *last_status = status;
    }
    return ESP_ERR_TIMEOUT;
}

esp_err_t rehab_capture_packet_acquire(rehab_capture_packet_t *packet,
                                       uint32_t timeout_ms)
{
    if (packet == NULL || timeout_ms == 0 || g_rehab_capture.lock == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(packet, 0, sizeof(*packet));
    xSemaphoreTake(g_rehab_capture.lock, portMAX_DELAY);
    if (!g_rehab_capture.initialized || g_rehab_capture.stop_requested ||
        !rehab_follow_camera_is_ready() || !rehab_tof_is_ready()) {
        capture_set_error_locked("provider_not_ready");
        xSemaphoreGive(g_rehab_capture.lock);
        return ESP_ERR_INVALID_STATE;
    }
    if (g_rehab_capture.busy || g_rehab_capture.waiter_active ||
        g_rehab_capture.request_pending ||
        g_rehab_capture.coordinator_active || g_rehab_capture.packet_lease) {
        capture_set_error_locked(g_rehab_capture.packet_lease
                                 ? "packet_busy" : "capture_busy");
        xSemaphoreGive(g_rehab_capture.lock);
        return ESP_ERR_NOT_FINISHED;
    }
    while (xSemaphoreTake(g_rehab_capture.request_done, 0) == pdTRUE) {
    }
    g_rehab_capture.busy = true;
    g_rehab_capture.status.busy = true;
    g_rehab_capture.request_pending = true;
    g_rehab_capture.status.request_pending = true;
    g_rehab_capture.status.capture_quiescent = false;
    g_rehab_capture.status.pipeline_quiescent = false;
    g_rehab_capture.waiter_active = true;
    g_rehab_capture.request_result = ESP_ERR_NOT_FINISHED;
    g_rehab_capture.active_request_seq =
        g_rehab_capture.status.next_capture_seq++;
    ++g_rehab_capture.status.request_count;
    snprintf(g_rehab_capture.status.last_capture_id,
             sizeof(g_rehab_capture.status.last_capture_id),
             "CAP%08" PRIu64, g_rehab_capture.active_request_seq);
    xSemaphoreGive(g_rehab_capture.lock);
    xTaskNotifyGive(g_rehab_capture.coordinator_task);

    TickType_t ticks = pdMS_TO_TICKS(timeout_ms);
    if (ticks == 0) {
        ticks = 1;
    }
    if (xSemaphoreTake(g_rehab_capture.request_done, ticks) != pdTRUE) {
        xSemaphoreTake(g_rehab_capture.lock, portMAX_DELAY);
        g_rehab_capture.waiter_active = false;
        g_rehab_capture.busy = false;
        g_rehab_capture.status.busy = false;
        ++g_rehab_capture.status.caller_timeout_count;
        ++g_rehab_capture.status.error_count;
        capture_set_error_locked("caller_timeout");
        if (g_rehab_capture.request_result != ESP_ERR_NOT_FINISHED) {
            g_rehab_capture.packet_lease = false;
            g_rehab_capture.status.packet_lease = false;
        }
        g_rehab_capture.status.pipeline_quiescent =
            capture_local_quiescent_locked();
        g_rehab_capture.status.capture_quiescent =
            g_rehab_capture.status.pipeline_quiescent;
        xSemaphoreGive(g_rehab_capture.lock);
        return ESP_ERR_TIMEOUT;
    }

    xSemaphoreTake(g_rehab_capture.lock, portMAX_DELAY);
    esp_err_t result = g_rehab_capture.request_result;
    if (result == ESP_OK && g_rehab_capture.packet_lease) {
        *packet = g_rehab_capture.packet;
    }
    g_rehab_capture.waiter_active = false;
    g_rehab_capture.status.pipeline_quiescent =
        capture_local_quiescent_locked();
    g_rehab_capture.status.capture_quiescent =
        g_rehab_capture.status.pipeline_quiescent;
    xSemaphoreGive(g_rehab_capture.lock);
    return result;
}

void rehab_capture_packet_release(rehab_capture_packet_t *packet)
{
    if (packet == NULL || packet->lease_token == 0 ||
        g_rehab_capture.lock == NULL) {
        return;
    }
    xSemaphoreTake(g_rehab_capture.lock, portMAX_DELAY);
    if (g_rehab_capture.packet_lease &&
        packet->lease_token == g_rehab_capture.packet_token) {
        g_rehab_capture.packet_lease = false;
        g_rehab_capture.status.packet_lease = false;
    }
    g_rehab_capture.status.pipeline_quiescent =
        capture_local_quiescent_locked();
    g_rehab_capture.status.capture_quiescent =
        g_rehab_capture.status.pipeline_quiescent;
    xSemaphoreGive(g_rehab_capture.lock);
    memset(packet, 0, sizeof(*packet));
}
