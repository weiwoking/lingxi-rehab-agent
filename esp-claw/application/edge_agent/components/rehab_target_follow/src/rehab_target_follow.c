// SPDX-License-Identifier: Apache-2.0
#include "rehab_target_follow_priv.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_console.h"
#include "esp_log.h"

#define FOLLOW_WORKER_STACK       8192U
#define FOLLOW_WORKER_PRIORITY       4U
#define FOLLOW_DELETE_WAIT_MS    (FOLLOW_FUSION_TIMEOUT_MS + 3000U)

static const char *TAG = "target_follow";

static bool follow_lock(rehab_target_follow_handle_t handle)
{
    return handle != NULL && handle->lock != NULL &&
           xSemaphoreTake(handle->lock, portMAX_DELAY) == pdTRUE;
}

static void follow_unlock(rehab_target_follow_handle_t handle)
{
    xSemaphoreGive(handle->lock);
}

const char *rehab_target_follow_state_name(rehab_target_follow_state_t state)
{
    switch (state) {
    case REHAB_TARGET_FOLLOW_STATE_IDLE: return "IDLE";
    case REHAB_TARGET_FOLLOW_STATE_SENSE: return "SENSE";
    case REHAB_TARGET_FOLLOW_STATE_SEARCH: return "SEARCH";
    case REHAB_TARGET_FOLLOW_STATE_CONFIRM: return "CONFIRM";
    case REHAB_TARGET_FOLLOW_STATE_ALIGN: return "ALIGN";
    case REHAB_TARGET_FOLLOW_STATE_APPROACH: return "APPROACH";
    case REHAB_TARGET_FOLLOW_STATE_HOLD: return "HOLD";
    case REHAB_TARGET_FOLLOW_STATE_ERROR: return "ERROR";
    case REHAB_TARGET_FOLLOW_STATE_CANCELLED: return "CANCELLED";
    default: return "UNKNOWN";
    }
}

static bool follow_cancelled(rehab_target_follow_handle_t handle,
                             uint32_t generation)
{
    bool cancelled = true;
    if (follow_lock(handle)) {
        cancelled = handle->cancel_requested ||
                    handle->run_generation != generation;
        follow_unlock(handle);
    }
    return cancelled;
}

static bool follow_wait_cancelable(rehab_target_follow_handle_t handle,
                                   uint32_t generation,
                                   uint32_t delay_ms)
{
    if (delay_ms != 0U) {
        (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(delay_ms));
    }
    return !follow_cancelled(handle, generation);
}

static void follow_reset_tracking_locked(rehab_target_follow_handle_t handle)
{
    handle->confirm_streak = 0;
    handle->hold_latched = false;
    handle->planned_zone = REHAB_TARGET_ZONE_NONE;
    handle->planned_movement_duration_ms = 0;
    rehab_target_follow_damping_reset(&handle->damping);
}

static void follow_preempted(void *ctx)
{
    rehab_target_follow_handle_t handle = ctx;
    TaskHandle_t task = NULL;
    if (handle == NULL || !follow_lock(handle)) {
        return;
    }
    if (handle->worker_active) {
        handle->cancel_requested = true;
        ++handle->run_generation;
        handle->state = REHAB_TARGET_FOLLOW_STATE_CANCELLED;
        follow_reset_tracking_locked(handle);
        strlcpy(handle->last_action, "stop", sizeof(handle->last_action));
        handle->last_action_hold_ms = 0;
        strlcpy(handle->last_decision_reason, "car_owner_preempted",
                sizeof(handle->last_decision_reason));
        task = handle->worker_task;
    }
    follow_unlock(handle);
    if (task != NULL && task != (TaskHandle_t)1) {
        xTaskNotifyGive(task);
    }
}

static esp_err_t follow_owned_stop(rehab_target_follow_handle_t handle)
{
    esp_err_t err = rehab_car_control_stop(handle->car, &handle->car_lease);
    if (follow_lock(handle)) {
        handle->last_stop_result = err;
        follow_unlock(handle);
    }
    return err;
}

static bool follow_error_needs_capture_recovery(const char *error)
{
    return error != NULL &&
           (strcmp(error, "capture_failed") == 0 ||
            strcmp(error, "capture_timeout") == 0);
}

static void follow_log_perception(const char *result,
                                  uint32_t error_count,
                                  uint32_t retry_delay_ms,
                                  bool capture_recovery,
                                  const char *last_error)
{
    ESP_LOGI(TAG,
             "perception_result=%s error_count=%u retry_delay_ms=%u "
             "capture_recovery=%d last_error=%s",
             result, (unsigned)error_count, (unsigned)retry_delay_ms,
             capture_recovery, last_error != NULL ? last_error : "unknown");
}

static void follow_set_error(rehab_target_follow_handle_t handle,
                             const char *error,
                             bool retryable)
{
    (void)follow_owned_stop(handle);
    uint32_t error_count = 0;
    uint32_t retry_delay_ms = 0;
    bool capture_recovery = false;
    char last_error[sizeof(handle->last_error)];
    if (!follow_lock(handle)) {
        return;
    }
    follow_reset_tracking_locked(handle);
    strlcpy(handle->last_error, error != NULL ? error : "unknown",
            sizeof(handle->last_error));
    strlcpy(handle->root_error, handle->last_error,
            sizeof(handle->root_error));
    handle->state = rehab_target_follow_apply_perception_result(
        REHAB_TARGET_PERCEPTION_RESULT_REAL_FAILURE, retryable,
        &handle->consecutive_perception_errors);
    if (handle->state == REHAB_TARGET_FOLLOW_STATE_ERROR) {
        handle->next_sense_delay_ms = 0;
        handle->capture_recovery = false;
        handle->last_retry_delay_ms = 0;
    } else if (follow_error_needs_capture_recovery(handle->last_error)) {
        handle->next_sense_delay_ms = 0;
        handle->capture_recovery = true;
        handle->last_retry_delay_ms = FOLLOW_CAPTURE_RECOVERY_MAX_MS;
    } else {
        handle->next_sense_delay_ms = FOLLOW_PERCEPTION_RETRY_MS;
        handle->capture_recovery = false;
        handle->last_retry_delay_ms = FOLLOW_PERCEPTION_RETRY_MS;
    }
    error_count = handle->consecutive_perception_errors;
    retry_delay_ms = handle->last_retry_delay_ms;
    capture_recovery = handle->capture_recovery;
    strlcpy(last_error, handle->last_error, sizeof(last_error));
    follow_unlock(handle);
    follow_log_perception("real_failure", error_count, retry_delay_ms,
                          capture_recovery, last_error);
}

static void follow_set_busy_cleanup(rehab_target_follow_handle_t handle)
{
    (void)follow_owned_stop(handle);
    uint32_t error_count = 0;
    char last_error[sizeof(handle->last_error)];
    if (!follow_lock(handle)) {
        return;
    }
    handle->state = rehab_target_follow_apply_perception_result(
        REHAB_TARGET_PERCEPTION_RESULT_BUSY_CLEANUP, true,
        &handle->consecutive_perception_errors);
    follow_reset_tracking_locked(handle);
    handle->next_sense_delay_ms = 0;
    handle->capture_recovery = true;
    handle->last_retry_delay_ms = FOLLOW_CAPTURE_RECOVERY_MAX_MS;
    error_count = handle->consecutive_perception_errors;
    strlcpy(last_error, handle->last_error, sizeof(last_error));
    follow_unlock(handle);
    follow_log_perception("busy_cleanup", error_count,
                          FOLLOW_CAPTURE_RECOVERY_MAX_MS, true, last_error);
}

static bool follow_upload_error_retryable(esp_err_t err,
                                          const rehab_cloud_fusion_result_t *result)
{
    if (result->has_error) {
        return result->error_retryable;
    }
    if (err == ESP_ERR_INVALID_RESPONSE || err == ESP_ERR_INVALID_ARG ||
        err == ESP_ERR_INVALID_SIZE) {
        return false;
    }
    return true;
}

static void follow_record_observation_locked(
    rehab_target_follow_handle_t handle,
    const rehab_target_follow_observation_t *observation)
{
    handle->last_target_zone = observation->found
        ? observation->zone : REHAB_TARGET_ZONE_NONE;
    handle->last_center_valid = observation->found && observation->center_valid;
    handle->last_center_x = observation->center_x;
    handle->last_error_x = handle->last_center_valid
        ? observation->center_x - 0.5f : 0.0f;
    handle->last_distance_valid = observation->found && observation->distance_valid;
    handle->last_distance_cm = observation->distance_cm;
    handle->multiple_zxl_anomaly = observation->multiple_zxl;
}

static void follow_record_decision_locked(
    rehab_target_follow_handle_t handle,
    const rehab_target_follow_decision_t *decision)
{
    strlcpy(handle->last_action,
            rehab_target_follow_action_name(decision->direction),
            sizeof(handle->last_action));
    handle->last_action_hold_ms = decision->movement_duration_ms;
    strlcpy(handle->last_decision_reason,
            decision->reason != NULL ? decision->reason : "unknown",
            sizeof(handle->last_decision_reason));
}

static void follow_log_decision(
    const char *capture_id,
    rehab_target_follow_state_t state_before,
    uint32_t confirm_streak,
    const rehab_target_follow_observation_t *observation,
    const rehab_target_follow_decision_t *decision,
    uint8_t overshoot_level)
{
    bool center_valid = observation->found && observation->center_valid;
    float center_x = center_valid ? observation->center_x : 0.0f;
    float error_x = center_valid ? center_x - 0.5f : 0.0f;
    bool distance_valid = observation->found && observation->distance_valid &&
                          observation->has_distance_cm;
    ESP_LOGI(TAG,
             "FOLLOW_DECISION target=%s capture_id=%s state_before=%s "
             "confirm_streak=%u target_found=%d center_valid=%d center_x=%.3f "
             "error_x=%.3f distance_valid=%d distance_cm=%.2f action=%s "
             "hold_ms=%u reason=%s overshoot_level=%u base_align_ms=%u "
             "final_align_ms=%u",
             FOLLOW_TARGET_CLASS_NAME,
             capture_id != NULL && capture_id[0] != '\0' ? capture_id : "none",
             rehab_target_follow_state_name(state_before),
             (unsigned)confirm_streak, observation->found, center_valid,
             center_x, error_x, distance_valid, observation->distance_cm,
             rehab_target_follow_action_name(decision->direction),
             (unsigned)decision->movement_duration_ms,
             decision->reason != NULL ? decision->reason : "unknown",
             (unsigned)overshoot_level, (unsigned)decision->base_align_ms,
             (unsigned)decision->final_align_ms);
}

static void follow_process_observation(
    rehab_target_follow_handle_t handle,
    const rehab_target_follow_observation_t *observation,
    const char *capture_id)
{
    uint32_t error_count = 0;
    uint32_t confirm_streak = 0;
    uint8_t overshoot_level = 0;
    rehab_target_follow_state_t state_before = REHAB_TARGET_FOLLOW_STATE_SENSE;
    char last_error[sizeof(handle->last_error)];
    if (!follow_lock(handle)) {
        return;
    }
    state_before = handle->state;
    follow_record_observation_locked(handle, observation);
    rehab_target_follow_decision_t decision = {0};
    esp_err_t err = rehab_target_follow_decide_observation(
        observation, &handle->confirm_streak, &handle->hold_latched,
        &handle->damping, &decision);
    if (err == ESP_OK) {
        (void)rehab_target_follow_apply_perception_result(
            REHAB_TARGET_PERCEPTION_RESULT_SUCCESS, true,
            &handle->consecutive_perception_errors);
        handle->capture_recovery = false;
        handle->last_retry_delay_ms = 0;
        strlcpy(handle->last_error, observation->found && observation->multiple_zxl
                ? "multiple_zxl_anomaly" : "none", sizeof(handle->last_error));
        strlcpy(handle->root_error, "none", sizeof(handle->root_error));
        handle->state = decision.next_state;
        handle->next_sense_delay_ms = decision.next_sense_delay_ms;
        handle->planned_zone = observation->zone;
        handle->planned_movement_duration_ms = decision.movement_duration_ms;
        error_count = handle->consecutive_perception_errors;
        strlcpy(last_error, handle->last_error, sizeof(last_error));
    }
    follow_record_decision_locked(handle, &decision);
    confirm_streak = handle->confirm_streak;
    overshoot_level = handle->damping.align_overshoot_level;
    follow_unlock(handle);
    follow_log_decision(capture_id, state_before, confirm_streak, observation,
                        &decision, overshoot_level);
    if (err != ESP_OK) {
        follow_set_error(handle,
                         decision.reason != NULL ? decision.reason
                                                 : "decision_invalid",
                         true);
    } else {
        follow_log_perception("success", error_count, 0, false, last_error);
        if (decision.stop_required) {
            (void)follow_owned_stop(handle);
        }
    }
}

static void follow_log_rejected_decision(
    rehab_target_follow_handle_t handle,
    const char *capture_id,
    const char *reason)
{
    const rehab_target_follow_observation_t observation = {0};
    const rehab_target_follow_decision_t decision = {
        .next_state = REHAB_TARGET_FOLLOW_STATE_SENSE,
        .stop_required = true,
        .direction = REHAB_CAR_DIRECTION_STOP,
        .reason = reason,
    };
    rehab_target_follow_state_t state_before = REHAB_TARGET_FOLLOW_STATE_SENSE;
    uint32_t confirm_streak = 0;
    uint8_t overshoot_level = 0;
    if (follow_lock(handle)) {
        state_before = handle->state;
        follow_record_decision_locked(handle, &decision);
        confirm_streak = handle->confirm_streak;
        overshoot_level = handle->damping.align_overshoot_level;
        follow_unlock(handle);
    }
    follow_log_decision(capture_id, state_before, confirm_streak, &observation,
                        &decision, overshoot_level);
}

static bool follow_car_owner_valid(rehab_target_follow_handle_t handle)
{
    rehab_car_status_t status = {0};
    return rehab_car_get_status(handle->car, &status) == ESP_OK &&
           status.control_owner == REHAB_CAR_CONTROL_OWNER_TARGET_FOLLOW &&
           status.control_owner_token == handle->car_lease.token &&
           handle->car_lease.owner == REHAB_CAR_CONTROL_OWNER_TARGET_FOLLOW;
}

static void follow_cancel_owner_lost(rehab_target_follow_handle_t handle,
                                     const char *capture_id)
{
    follow_log_rejected_decision(handle, capture_id, "car_owner_invalid");
    (void)follow_owned_stop(handle);
    if (follow_lock(handle)) {
        handle->cancel_requested = true;
        ++handle->run_generation;
        handle->state = REHAB_TARGET_FOLLOW_STATE_CANCELLED;
        follow_reset_tracking_locked(handle);
        follow_unlock(handle);
    }
}

static const char *follow_capture_blocking_stage(
    esp_err_t status_result,
    const rehab_capture_status_t *status)
{
    if (status_result != ESP_OK || status == NULL) {
        return "capture_status";
    }
    if (!status->capture_quiescent) {
        return "capture";
    }
    if (!status->tof_quiescent) {
        return "tof_window";
    }
    if (!status->camera_quiescent) {
        return "camera_snapshot";
    }
    return "unknown";
}

typedef struct {
    rehab_target_follow_handle_t handle;
    uint32_t generation;
} follow_capture_wait_ctx_t;

static bool follow_capture_wait_slice(void *ctx, uint32_t wait_ms)
{
    follow_capture_wait_ctx_t *wait_ctx = ctx;
    return wait_ctx != NULL &&
        follow_wait_cancelable(wait_ctx->handle, wait_ctx->generation, wait_ms);
}

static bool follow_wait_capture_recovery(rehab_target_follow_handle_t handle,
                                         uint32_t generation)
{
    bool recovery = false;
    if (follow_lock(handle)) {
        recovery = handle->capture_recovery;
        follow_unlock(handle);
    }
    if (!recovery) {
        return true;
    }

    ESP_LOGI(TAG,
             "capture_recovery_begin max_wait_ms=%u poll_ms=%u settle_ms=%u",
             (unsigned)FOLLOW_CAPTURE_RECOVERY_MAX_MS,
             (unsigned)FOLLOW_CAPTURE_RECOVERY_POLL_MS,
             (unsigned)FOLLOW_CAPTURE_POST_QUIESCENT_SETTLE_MS);
    uint32_t waited_ms = 0;
    rehab_capture_status_t capture_status = {0};
    follow_capture_wait_ctx_t wait_ctx = {
        .handle = handle,
        .generation = generation,
    };
    esp_err_t wait_result = rehab_capture_wait_for_quiescence(
        FOLLOW_CAPTURE_RECOVERY_MAX_MS,
        FOLLOW_CAPTURE_RECOVERY_POLL_MS,
        FOLLOW_CAPTURE_POST_QUIESCENT_SETTLE_MS,
        follow_capture_wait_slice, &wait_ctx, &waited_ms, &capture_status);
    if (wait_result == ESP_ERR_INVALID_STATE ||
        follow_cancelled(handle, generation)) {
        return false;
    }

    uint32_t error_count = 0;
    char root_error[sizeof(handle->root_error)];
    strlcpy(root_error, "unknown", sizeof(root_error));
    if (wait_result == ESP_OK && follow_lock(handle)) {
        if (handle->cancel_requested || handle->run_generation != generation) {
            follow_unlock(handle);
            return false;
        }
        handle->capture_recovery = false;
        handle->last_retry_delay_ms = 0;
        error_count = handle->consecutive_perception_errors;
        strlcpy(root_error, handle->root_error, sizeof(root_error));
        follow_unlock(handle);
        ESP_LOGI(TAG,
                 "capture_recovery_complete waited_ms=%u error_count=%u "
                 "root_error=%s capture_recovery=0",
                 (unsigned)waited_ms, (unsigned)error_count, root_error);
        return true;
    }

    (void)follow_owned_stop(handle);
    if (follow_lock(handle)) {
        handle->capture_recovery = false;
        handle->next_sense_delay_ms = 0;
        handle->last_retry_delay_ms = 0;
        handle->state = REHAB_TARGET_FOLLOW_STATE_ERROR;
        strlcpy(handle->last_error, "capture_recovery_timeout",
                sizeof(handle->last_error));
        error_count = handle->consecutive_perception_errors;
        strlcpy(root_error, handle->root_error, sizeof(root_error));
        follow_unlock(handle);
    }
    esp_err_t status_result = rehab_capture_get_status(&capture_status);
    ESP_LOGE(TAG,
             "capture_recovery_timeout capture_busy=%d packet_lease=%d "
             "request_pending=%d capture_coordinator_active=%d "
             "capture_quiescent=%d tof_request_pending=%d "
             "tof_window_active=%d tof_quiescent=%d "
             "camera_request_pending=%d camera_snapshot_active=%d "
             "camera_quiescent=%d "
             "pipeline_quiescent=%d waited_ms=%u max_wait_ms=%u "
             "error_count=%u root_error=%s capture_recovery=0 "
             "blocking_stage=%s",
             capture_status.busy, capture_status.packet_lease,
             capture_status.request_pending, capture_status.coordinator_active,
             capture_status.capture_quiescent,
             capture_status.tof_request_pending,
             capture_status.tof_window_active,
             capture_status.tof_quiescent,
             capture_status.camera_request_pending,
             capture_status.camera_snapshot_active,
             capture_status.camera_quiescent,
             status_result == ESP_OK &&
                 rehab_capture_status_is_quiescent(&capture_status),
             (unsigned)waited_ms,
             (unsigned)FOLLOW_CAPTURE_RECOVERY_MAX_MS,
             (unsigned)error_count, root_error,
             follow_capture_blocking_stage(status_result, &capture_status));
    return false;
}

static void follow_sense_once(rehab_target_follow_handle_t handle,
                              uint32_t generation)
{
    uint32_t delay_ms = 0;
    if (follow_lock(handle)) {
        delay_ms = handle->next_sense_delay_ms;
        handle->next_sense_delay_ms = 0;
        follow_unlock(handle);
    }
    if (!follow_wait_cancelable(handle, generation, delay_ms)) {
        return;
    }
    if (!follow_wait_capture_recovery(handle, generation)) {
        return;
    }

    if (follow_lock(handle)) {
        handle->perception_in_flight = true;
        handle->state = REHAB_TARGET_FOLLOW_STATE_SENSE;
        follow_unlock(handle);
    }
    esp_err_t err = rehab_cloud_fusion_upload_once(handle->fusion_result,
                                                    FOLLOW_FUSION_TIMEOUT_MS);
    if (follow_lock(handle)) {
        handle->perception_in_flight = false;
        follow_unlock(handle);
    }

    /* Cooperative cancellation boundary: discard the completed result before
     * any decision when STOP changed the run generation or revoked the lease. */
    if (follow_cancelled(handle, generation)) {
        return;
    }
    if (!follow_car_owner_valid(handle)) {
        follow_cancel_owner_lost(handle, handle->fusion_result->capture_id);
        return;
    }
    rehab_target_perception_result_t perception_result =
        rehab_target_follow_classify_perception_result(
            err, handle->fusion_result->ok, handle->fusion_result->last_error);
    if (perception_result != REHAB_TARGET_PERCEPTION_RESULT_SUCCESS) {
        if (perception_result == REHAB_TARGET_PERCEPTION_RESULT_BUSY_CLEANUP) {
            follow_set_busy_cleanup(handle);
            return;
        }
        const char *error = handle->fusion_result->last_error[0]
            ? handle->fusion_result->last_error : esp_err_to_name(err);
        follow_set_error(handle, error,
                         follow_upload_error_retryable(err, handle->fusion_result));
        return;
    }

    rehab_target_follow_observation_t observation = {0};
    char validation_error[64];
    err = rehab_target_follow_select_observation(handle->fusion_result,
                                                  &observation,
                                                  validation_error,
                                                  sizeof(validation_error));
    if (err != ESP_OK) {
        follow_log_rejected_decision(handle, handle->fusion_result->capture_id,
                                     validation_error);
        bool retryable = strcmp(validation_error, "detections_truncated") == 0;
        follow_set_error(handle, validation_error, retryable);
        return;
    }
    follow_process_observation(handle, &observation,
                               handle->fusion_result->capture_id);
}

static void follow_move_failure(rehab_target_follow_handle_t handle,
                                esp_err_t err,
                                uint32_t generation)
{
    if (follow_cancelled(handle, generation)) {
        return;
    }
    char error[96];
    snprintf(error, sizeof(error), "car_move_failed:%s", esp_err_to_name(err));
    follow_set_error(handle, error, false);
}

static bool follow_execute_move(rehab_target_follow_handle_t handle,
                                uint32_t generation,
                                rehab_car_direction_t direction,
                                uint32_t duration_ms)
{
    esp_err_t err = rehab_car_control_move(handle->car, &handle->car_lease,
                                           direction, FOLLOW_SPEED_LEVEL,
                                           duration_ms);
    if (err != ESP_OK) {
        follow_move_failure(handle, err, generation);
        return false;
    }
    if (!follow_wait_cancelable(handle, generation, FOLLOW_MOVEMENT_SETTLE_MS)) {
        return false;
    }
    if (follow_lock(handle)) {
        handle->state = REHAB_TARGET_FOLLOW_STATE_SENSE;
        follow_unlock(handle);
    }
    return true;
}

static void follow_worker(void *arg)
{
    rehab_target_follow_handle_t handle = arg;
    uint32_t generation = 0;
    if (follow_lock(handle)) {
        generation = handle->run_generation;
        follow_unlock(handle);
    }

    for (;;) {
        if (follow_cancelled(handle, generation)) {
            break;
        }
        rehab_target_follow_state_t state = REHAB_TARGET_FOLLOW_STATE_ERROR;
        rehab_target_zone_t planned_zone = REHAB_TARGET_ZONE_NONE;
        uint32_t movement_ms = 0;
        if (follow_lock(handle)) {
            state = handle->state;
            planned_zone = handle->planned_zone;
            movement_ms = handle->planned_movement_duration_ms;
            follow_unlock(handle);
        }
        switch (state) {
        case REHAB_TARGET_FOLLOW_STATE_SENSE:
            follow_sense_once(handle, generation);
            break;
        case REHAB_TARGET_FOLLOW_STATE_CONFIRM:
            if (follow_lock(handle)) {
                handle->state = REHAB_TARGET_FOLLOW_STATE_SENSE;
                follow_unlock(handle);
            }
            break;
        case REHAB_TARGET_FOLLOW_STATE_SEARCH:
            (void)follow_execute_move(handle, generation,
                                      REHAB_CAR_DIRECTION_ROTATE_CCW,
                                      movement_ms);
            break;
        case REHAB_TARGET_FOLLOW_STATE_ALIGN:
            (void)follow_execute_move(handle, generation,
                planned_zone == REHAB_TARGET_ZONE_LEFT
                    ? REHAB_CAR_DIRECTION_ROTATE_CCW
                    : REHAB_CAR_DIRECTION_ROTATE_CW,
                movement_ms);
            break;
        case REHAB_TARGET_FOLLOW_STATE_APPROACH:
            (void)follow_execute_move(handle, generation,
                                      REHAB_CAR_DIRECTION_FORWARD,
                                      movement_ms);
            break;
        case REHAB_TARGET_FOLLOW_STATE_HOLD:
            if (follow_wait_cancelable(handle, generation,
                                       FOLLOW_HOLD_RECHECK_MS) &&
                follow_lock(handle)) {
                handle->state = REHAB_TARGET_FOLLOW_STATE_SENSE;
                follow_unlock(handle);
            }
            break;
        case REHAB_TARGET_FOLLOW_STATE_ERROR:
        case REHAB_TARGET_FOLLOW_STATE_CANCELLED:
        case REHAB_TARGET_FOLLOW_STATE_IDLE:
        default:
            goto finished;
        }
    }

finished:
    {
        esp_err_t stop_result = rehab_car_control_stop(handle->car,
                                                       &handle->car_lease);
        (void)rehab_car_control_release(handle->car, &handle->car_lease);
        if (follow_lock(handle)) {
            if (stop_result != ESP_ERR_INVALID_STATE) {
                handle->last_stop_result = stop_result;
            }
            bool cancelled = handle->cancel_requested ||
                             handle->run_generation != generation;
            if (cancelled) {
                handle->state = REHAB_TARGET_FOLLOW_STATE_IDLE;
            }
            handle->worker_active = false;
            handle->perception_in_flight = false;
            handle->cancel_requested = false;
            handle->capture_recovery = false;
            handle->worker_task = NULL;
            follow_unlock(handle);
        }
        xSemaphoreGive(handle->worker_done);
        vTaskDelete(NULL);
    }
}

esp_err_t rehab_target_follow_create(const rehab_target_follow_config_t *config,
                                     rehab_target_follow_handle_t *out_handle)
{
    if (config == NULL || config->car == NULL || out_handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_handle = NULL;
    rehab_target_follow_handle_t handle = calloc(1, sizeof(*handle));
    if (handle == NULL) {
        return ESP_ERR_NO_MEM;
    }
    handle->lock = xSemaphoreCreateMutex();
    handle->worker_done = xSemaphoreCreateBinary();
    handle->fusion_result = calloc(1, sizeof(*handle->fusion_result));
    if (handle->lock == NULL || handle->worker_done == NULL ||
        handle->fusion_result == NULL) {
        if (handle->lock != NULL) vSemaphoreDelete(handle->lock);
        if (handle->worker_done != NULL) vSemaphoreDelete(handle->worker_done);
        free(handle->fusion_result);
        free(handle);
        return ESP_ERR_NO_MEM;
    }
    handle->car = config->car;
    handle->state = REHAB_TARGET_FOLLOW_STATE_IDLE;
    handle->last_stop_result = ESP_ERR_NOT_FINISHED;
    strlcpy(handle->last_action, "none", sizeof(handle->last_action));
    strlcpy(handle->last_decision_reason, "none",
            sizeof(handle->last_decision_reason));
    strlcpy(handle->last_error, "none", sizeof(handle->last_error));
    strlcpy(handle->root_error, "none", sizeof(handle->root_error));
    rehab_target_follow_damping_reset(&handle->damping);
    *out_handle = handle;
    return ESP_OK;
}

esp_err_t rehab_target_follow_start(rehab_target_follow_handle_t handle)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!rehab_capture_is_ready()) {
        if (follow_lock(handle)) {
            strlcpy(handle->last_error, "perception_not_ready",
                    sizeof(handle->last_error));
            follow_unlock(handle);
        }
        return ESP_ERR_INVALID_STATE;
    }
    if (!follow_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (handle->worker_active || handle->state != REHAB_TARGET_FOLLOW_STATE_IDLE) {
        follow_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    while (xSemaphoreTake(handle->worker_done, 0) == pdTRUE) {
    }
    handle->worker_active = true;
    handle->worker_task = (TaskHandle_t)1;
    handle->cancel_requested = false;
    ++handle->run_generation;
    if (handle->run_generation == 0) ++handle->run_generation;
    handle->state = REHAB_TARGET_FOLLOW_STATE_SENSE;
    handle->next_sense_delay_ms = 0;
    handle->last_retry_delay_ms = 0;
    handle->capture_recovery = false;
    handle->consecutive_perception_errors = 0;
    handle->last_target_zone = REHAB_TARGET_ZONE_NONE;
    handle->last_center_valid = false;
    handle->last_center_x = 0.0f;
    handle->last_error_x = 0.0f;
    handle->last_distance_valid = false;
    handle->last_distance_cm = 0.0f;
    follow_reset_tracking_locked(handle);
    strlcpy(handle->last_action, "stop", sizeof(handle->last_action));
    handle->last_action_hold_ms = 0;
    strlcpy(handle->last_decision_reason, "start",
            sizeof(handle->last_decision_reason));
    strlcpy(handle->last_error, "none", sizeof(handle->last_error));
    strlcpy(handle->root_error, "none", sizeof(handle->root_error));
    follow_unlock(handle);

    esp_err_t err = rehab_car_control_acquire(
        handle->car, REHAB_CAR_CONTROL_OWNER_TARGET_FOLLOW,
        follow_preempted, handle, &handle->car_lease);
    if (err == ESP_OK) {
        err = follow_owned_stop(handle);
    }
    if (err != ESP_OK) {
        (void)rehab_car_control_release(handle->car, &handle->car_lease);
        if (follow_lock(handle)) {
            handle->worker_active = false;
            handle->worker_task = NULL;
            handle->state = REHAB_TARGET_FOLLOW_STATE_IDLE;
            strlcpy(handle->last_error, "car_owner_unavailable",
                    sizeof(handle->last_error));
            follow_unlock(handle);
        }
        return err;
    }

    TaskHandle_t task = NULL;
    if (xTaskCreate(follow_worker, "target_follow", FOLLOW_WORKER_STACK,
                    handle, FOLLOW_WORKER_PRIORITY, &task) != pdPASS) {
        (void)rehab_car_control_stop(handle->car, &handle->car_lease);
        (void)rehab_car_control_release(handle->car, &handle->car_lease);
        if (follow_lock(handle)) {
            handle->worker_active = false;
            handle->worker_task = NULL;
            handle->state = REHAB_TARGET_FOLLOW_STATE_IDLE;
            strlcpy(handle->last_error, "worker_create_failed",
                    sizeof(handle->last_error));
            follow_unlock(handle);
        }
        return ESP_ERR_NO_MEM;
    }
    if (follow_lock(handle)) {
        if (handle->worker_task == (TaskHandle_t)1) {
            handle->worker_task = task;
        }
        follow_unlock(handle);
    }
    ESP_LOGI(TAG, "FOLLOW_START accepted=1 target=%s fusion_timeout_ms=%u",
             FOLLOW_TARGET_CLASS_NAME, (unsigned)FOLLOW_FUSION_TIMEOUT_MS);
    return ESP_OK;
}

esp_err_t rehab_target_follow_stop(rehab_target_follow_handle_t handle)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    /* Required priority: the first state-changing action is always car STOP. */
    esp_err_t stop_result = rehab_car_stop(handle->car);
    TaskHandle_t task = NULL;
    if (follow_lock(handle)) {
        handle->last_stop_result = stop_result;
        if (handle->worker_active) {
            handle->cancel_requested = true;
            ++handle->run_generation;
            handle->state = REHAB_TARGET_FOLLOW_STATE_CANCELLED;
            follow_reset_tracking_locked(handle);
            task = handle->worker_task;
        } else {
            handle->state = REHAB_TARGET_FOLLOW_STATE_IDLE;
            handle->cancel_requested = false;
            follow_reset_tracking_locked(handle);
        }
        strlcpy(handle->last_action, "stop", sizeof(handle->last_action));
        handle->last_action_hold_ms = 0;
        strlcpy(handle->last_decision_reason, "stopped",
                sizeof(handle->last_decision_reason));
        follow_unlock(handle);
    }
    if (task != NULL && task != (TaskHandle_t)1) {
        xTaskNotifyGive(task);
    }
    return stop_result;
}

esp_err_t rehab_target_follow_get_status(rehab_target_follow_handle_t handle,
                                         rehab_target_follow_status_t *status)
{
    if (handle == NULL || status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(status, 0, sizeof(*status));
    if (!follow_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    strlcpy(status->target, FOLLOW_TARGET_CLASS_NAME, sizeof(status->target));
    status->state = handle->state;
    status->worker_active = handle->worker_active;
    status->perception_in_flight = handle->perception_in_flight;
    status->cancel_requested = handle->cancel_requested;
    status->capture_recovery = handle->capture_recovery;
    status->retry_delay_ms = handle->last_retry_delay_ms;
    status->confirm_streak = handle->confirm_streak;
    strlcpy(status->last_target_zone,
            rehab_target_follow_zone_name(handle->last_target_zone),
            sizeof(status->last_target_zone));
    status->last_center_valid = handle->last_center_valid;
    status->last_center_x = handle->last_center_x;
    status->last_error_x = handle->last_error_x;
    status->last_distance_valid = handle->last_distance_valid;
    status->last_distance_cm = handle->last_distance_cm;
    strlcpy(status->last_action, handle->last_action,
            sizeof(status->last_action));
    status->last_action_hold_ms = handle->last_action_hold_ms;
    strlcpy(status->last_decision_reason, handle->last_decision_reason,
            sizeof(status->last_decision_reason));
    status->align_overshoot_level = handle->damping.align_overshoot_level;
    status->last_align_duration_ms = handle->damping.last_align_duration_ms;
    status->consecutive_perception_errors = handle->consecutive_perception_errors;
    status->multiple_zxl_anomaly = handle->multiple_zxl_anomaly;
    strlcpy(status->last_error, handle->last_error, sizeof(status->last_error));
    strlcpy(status->root_error, handle->root_error, sizeof(status->root_error));
    status->last_stop_result = handle->last_stop_result;
    follow_unlock(handle);

    rehab_car_status_t car_status = {0};
    if (rehab_car_get_status(handle->car, &car_status) == ESP_OK) {
        status->car_owner = car_status.control_owner;
    }
    return ESP_OK;
}

static int follow_console_handler(void *ctx, int argc, char **argv)
{
    rehab_target_follow_handle_t handle = ctx;
    if (handle == NULL || argc != 2) {
        ESP_LOGW(TAG, "usage: follow_target <status|start|stop>");
        return 1;
    }
    if (strcmp(argv[1], "start") == 0) {
        return rehab_target_follow_start(handle) == ESP_OK ? 0 : 1;
    }
    if (strcmp(argv[1], "stop") == 0) {
        return rehab_target_follow_stop(handle) == ESP_OK ? 0 : 1;
    }
    if (strcmp(argv[1], "status") == 0) {
        rehab_target_follow_status_t status;
        if (rehab_target_follow_get_status(handle, &status) != ESP_OK) {
            return 1;
        }
        ESP_LOGI(TAG,
            "target=%s state=%s worker_active=%d perception_in_flight=%d "
            "cancel_requested=%d "
            "capture_recovery=%d retry_delay_ms=%u car_owner=%s "
            "confirm_streak=%u last_target_zone=%s "
            "last_center_valid=%d last_center_x=%.3f last_error_x=%.3f "
            "last_distance_valid=%d last_distance_cm=%.2f overshoot=%u "
            "last_action=%s last_action_hold_ms=%u last_decision_reason=%s "
            "last_align_ms=%u errors=%u last_error=%s root_error=%s "
            "last_stop=%s",
            status.target,
            rehab_target_follow_state_name(status.state), status.worker_active,
            status.perception_in_flight, status.cancel_requested,
            status.capture_recovery, (unsigned)status.retry_delay_ms,
            rehab_car_control_owner_name(status.car_owner),
            (unsigned)status.confirm_streak, status.last_target_zone,
            status.last_center_valid, status.last_center_x,
            status.last_error_x,
            status.last_distance_valid, status.last_distance_cm,
            (unsigned)status.align_overshoot_level,
            status.last_action, (unsigned)status.last_action_hold_ms,
            status.last_decision_reason,
            (unsigned)status.last_align_duration_ms,
            (unsigned)status.consecutive_perception_errors, status.last_error,
            status.root_error,
            esp_err_to_name(status.last_stop_result));
        return 0;
    }
    ESP_LOGW(TAG, "usage: follow_target <status|start|stop>");
    return 1;
}

esp_err_t rehab_target_follow_register_console(rehab_target_follow_handle_t handle)
{
    if (handle == NULL || handle->console_registered) {
        return ESP_ERR_INVALID_ARG;
    }
    const esp_console_cmd_t command = {
        .command = "follow_target",
        .help = "Fixed configured-target low-speed discrete follow: status/start/stop. No timing overrides.",
        .hint = "<status|start|stop>",
        .func_w_context = follow_console_handler,
        .context = handle,
    };
    esp_err_t err = esp_console_cmd_register(&command);
    if (err == ESP_OK) {
        handle->console_registered = true;
    }
    return err;
}

esp_err_t rehab_target_follow_delete(rehab_target_follow_handle_t handle)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    bool active = false;
    if (follow_lock(handle)) {
        active = handle->worker_active;
        follow_unlock(handle);
    }
    if (active) {
        (void)rehab_target_follow_stop(handle);
        if (xSemaphoreTake(handle->worker_done,
                           pdMS_TO_TICKS(FOLLOW_DELETE_WAIT_MS)) != pdTRUE) {
            return ESP_ERR_TIMEOUT;
        }
    }
    if (handle->console_registered) {
        (void)esp_console_cmd_deregister("follow_target");
    }
    free(handle->fusion_result);
    vSemaphoreDelete(handle->worker_done);
    vSemaphoreDelete(handle->lock);
    free(handle);
    return ESP_OK;
}
