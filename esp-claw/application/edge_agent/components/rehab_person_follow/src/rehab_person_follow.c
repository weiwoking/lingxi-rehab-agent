// SPDX-License-Identifier: Apache-2.0
#include "rehab_person_follow_priv.h"
#include "rehab_business_audio.h"

#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "person_follow";

static uint32_t person_follow_elapsed_ms(int64_t started_us)
{
    int64_t elapsed = esp_timer_get_time() - started_us;
    if (elapsed <= 0) {
        return 0U;
    }
    uint64_t value = (uint64_t)elapsed / 1000U;
    return value > UINT32_MAX ? UINT32_MAX : (uint32_t)value;
}

void rehab_person_follow_log_stack(const char *stage)
{
    UBaseType_t free_bytes = uxTaskGetStackHighWaterMark(NULL);
    ESP_LOGI(TAG, "PF_STACK stage=%s free_bytes=%u",
             stage != NULL ? stage : "unknown", (unsigned)free_bytes);
}

__attribute__((weak)) BaseType_t rehab_person_follow_task_create(
    TaskFunction_t task_function,
    const char *task_name,
    uint32_t stack_depth,
    void *task_argument,
    UBaseType_t priority,
    TaskHandle_t *out_task)
{
    return xTaskCreate(task_function, task_name, stack_depth, task_argument,
                       priority, out_task);
}

static bool person_follow_lock(rehab_person_follow_handle_t handle)
{
    return handle != NULL && handle->lock != NULL &&
           xSemaphoreTake(handle->lock, portMAX_DELAY) == pdTRUE;
}

static void person_follow_unlock(rehab_person_follow_handle_t handle)
{
    xSemaphoreGive(handle->lock);
}

static uint32_t person_follow_next_generation(uint32_t generation)
{
    ++generation;
    return generation == 0U ? 1U : generation;
}

static bool person_follow_config_valid(
    const rehab_person_follow_config_t *config)
{
    return config != NULL && config->car != NULL &&
           config->perception_provider <=
               REHAB_PERSON_FOLLOW_PERCEPTION_REAL &&
           config->mock_mode <=
               REHAB_PERSON_FOLLOW_MOCK_TECHNICAL_ERROR &&
           config->pc_endpoint != NULL && config->pc_endpoint[0] != '\0' &&
           strnlen(config->pc_endpoint, REHAB_PERSON_FOLLOW_ENDPOINT_MAX) <
               REHAB_PERSON_FOLLOW_ENDPOINT_MAX &&
           config->http_timeout_ms >= 100U &&
           config->http_timeout_ms <= 60000U &&
           config->camera_timeout_ms >= 10U &&
           config->camera_timeout_ms <= 60000U &&
           config->loop_interval_ms >= 10U &&
           config->loop_interval_ms <= 60000U &&
           config->max_detections > 0U &&
           config->max_detections <= PERSON_FOLLOW_MAX_DETECTIONS_LIMIT &&
           config->max_runtime_ms >= 100U &&
           config->max_runtime_ms <= 3600000U &&
           config->max_consecutive_errors > 0U &&
           config->max_consecutive_errors <= 32U &&
           config->action_speed >= 1U && config->action_speed <= 3U &&
           config->turn_hold_ms >= REHAB_CAR_HOLD_MIN_MS &&
           config->turn_hold_ms <= REHAB_CAR_HOLD_MAX_MS &&
           config->forward_hold_ms >= REHAB_CAR_HOLD_MIN_MS &&
           config->forward_hold_ms <= REHAB_CAR_HOLD_MAX_MS &&
           config->fallback_post_action_settle_ms <= 60000U &&
           config->forward_post_action_settle_ms > 0U &&
           config->forward_post_action_settle_ms <= 60000U &&
           config->turn_post_action_settle_ms > 0U &&
           config->turn_post_action_settle_ms <= 60000U;
}

bool rehab_person_follow_generation_current(
    rehab_person_follow_handle_t handle, uint32_t generation)
{
    bool current = false;
    if (person_follow_lock(handle)) {
        current = !handle->cancel_requested &&
                  handle->generation == generation;
        person_follow_unlock(handle);
    }
    return current;
}

static bool person_follow_wait_cancelable(
    rehab_person_follow_handle_t handle,
    uint32_t generation,
    uint32_t delay_ms)
{
    (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(delay_ms));
    bool keep_running = false;
    if (person_follow_lock(handle)) {
        keep_running = !handle->cancel_requested &&
                       handle->generation == generation;
        person_follow_unlock(handle);
    }
    return keep_running;
}

__attribute__((weak)) esp_err_t rehab_person_follow_car_move(
    rehab_car_handle_t car,
    const rehab_car_control_lease_t *lease,
    rehab_car_direction_t direction,
    uint8_t speed_level,
    uint32_t hold_ms)
{
    return rehab_car_control_move(car, lease, direction, speed_level, hold_ms);
}

__attribute__((weak)) esp_err_t rehab_person_follow_car_stop(
    rehab_car_handle_t car,
    const rehab_car_control_lease_t *lease)
{
    return rehab_car_control_stop(car, lease);
}

static uint32_t person_follow_remaining_ms(int64_t deadline_us, int64_t now_us)
{
    if (deadline_us <= now_us) {
        return 0U;
    }
    uint64_t remaining_us = (uint64_t)(deadline_us - now_us);
    uint64_t remaining_ms = (remaining_us + 999U) / 1000U;
    return remaining_ms > UINT32_MAX ? UINT32_MAX : (uint32_t)remaining_ms;
}

static uint32_t person_follow_age_ms(int64_t timestamp_us, int64_t now_us)
{
    if (timestamp_us <= 0 || now_us <= timestamp_us) {
        return 0U;
    }
    uint64_t age_ms = (uint64_t)(now_us - timestamp_us) / 1000U;
    return age_ms > UINT32_MAX ? UINT32_MAX : (uint32_t)age_ms;
}

bool rehab_person_follow_rolling_segment_allowed(
    rehab_person_follow_handle_t handle, uint32_t generation, int64_t now_us)
{
    bool allowed = false;
    if (person_follow_lock(handle)) {
        allowed = PERSON_FOLLOW_ROLLING_FORWARD_ENABLED &&
                  handle->config.action_enabled && handle->running &&
                  !handle->cancel_requested && !handle->rolling_exit_requested &&
                  handle->generation == generation &&
                  handle->rolling_generation == generation &&
                  handle->owner_acquired && handle->forward_rolling &&
                  now_us <= handle->forward_lease_deadline_us;
        person_follow_unlock(handle);
    }
    return allowed;
}

bool rehab_person_follow_rolling_segment_finished(
    rehab_person_follow_handle_t handle, uint32_t generation,
    esp_err_t result, int64_t now_us)
{
    bool continue_rolling = false;
    bool lease_expired = false;
    TaskHandle_t worker_task = NULL;
    if (person_follow_lock(handle)) {
        handle->rolling_task_busy = false;
        bool current = !handle->cancel_requested &&
                       handle->generation == generation &&
                       handle->rolling_generation == generation;
        if (result == ESP_OK && current) {
            if (handle->forward_segments_completed < UINT32_MAX) {
                ++handle->forward_segments_completed;
            }
            handle->decision_context.has_forwarded_once = true;
            handle->last_action = REHAB_PERSON_FOLLOW_ACTION_FORWARD;
            handle->last_car_result = ESP_OK;
            handle->motion_unconfirmed = false;
        } else if (result != ESP_OK && current && handle->forward_rolling) {
            handle->forward_rolling = false;
            handle->forward_lease_deadline_us = 0;
            strlcpy(handle->rolling_stop_reason, "forward_segment_failed",
                    sizeof(handle->rolling_stop_reason));
            handle->last_car_result = result;
            strlcpy(handle->last_error, "car_action_failed",
                    sizeof(handle->last_error));
        }
        if (current && handle->forward_rolling &&
            now_us > handle->forward_lease_deadline_us) {
            handle->forward_rolling = false;
            handle->forward_lease_deadline_us = 0;
            strlcpy(handle->rolling_stop_reason, "lease_expired",
                    sizeof(handle->rolling_stop_reason));
            lease_expired = true;
        }
        continue_rolling = current && handle->forward_rolling &&
                           now_us <= handle->forward_lease_deadline_us &&
                           handle->owner_acquired;
        worker_task = handle->worker_task;
        person_follow_unlock(handle);
    }
    if (worker_task != NULL && worker_task != (TaskHandle_t)1) {
        xTaskNotifyGive(worker_task);
    }
    if (lease_expired && handle->rolling_lease_timer != NULL) {
        (void)xTimerStop(handle->rolling_lease_timer, 0);
    }
    return continue_rolling;
}

esp_err_t rehab_person_follow_rolling_preempt(
    rehab_person_follow_handle_t handle, uint32_t generation,
    const char *reason, bool wait_idle)
{
    if (handle == NULL || reason == NULL || reason[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    TaskHandle_t rolling_task = NULL;
    rehab_car_control_lease_t lease = {0};
    bool stop_needed = false;
    if (!person_follow_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (handle->generation != generation ||
        handle->rolling_generation != generation) {
        person_follow_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    stop_needed = handle->config.action_enabled && handle->owner_acquired &&
                  (handle->forward_rolling || handle->rolling_task_busy);
    handle->forward_rolling = false;
    handle->forward_lease_deadline_us = 0;
    strlcpy(handle->rolling_stop_reason, reason,
            sizeof(handle->rolling_stop_reason));
    lease = handle->car_lease;
    rolling_task = handle->rolling_task;
    person_follow_unlock(handle);

    if (handle->rolling_lease_timer != NULL) {
        (void)xTimerStop(handle->rolling_lease_timer, 0);
    }
    esp_err_t stop_result = stop_needed
        ? rehab_person_follow_car_stop(handle->car, &lease) : ESP_OK;
    if (rolling_task != NULL && rolling_task != (TaskHandle_t)1) {
        xTaskNotifyGive(rolling_task);
    }
    if (!wait_idle || rolling_task == xTaskGetCurrentTaskHandle()) {
        return stop_result;
    }

    TickType_t deadline = xTaskGetTickCount() +
                          pdMS_TO_TICKS(PERSON_FOLLOW_ROLLING_IDLE_WAIT_MS);
    for (;;) {
        bool busy = false;
        if (person_follow_lock(handle)) {
            busy = handle->rolling_task_busy;
            person_follow_unlock(handle);
        }
        if (!busy) {
            return stop_result;
        }
        if ((int32_t)(xTaskGetTickCount() - deadline) >= 0) {
            return ESP_ERR_TIMEOUT;
        }
        (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(10U));
    }
}

esp_err_t rehab_person_follow_rolling_confirm_center(
    rehab_person_follow_handle_t handle, uint32_t generation, int64_t now_us)
{
    if (handle == NULL || now_us < 0) {
        return ESP_ERR_INVALID_ARG;
    }
    TaskHandle_t rolling_task = NULL;
    if (!person_follow_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (!PERSON_FOLLOW_ROLLING_FORWARD_ENABLED ||
        !handle->config.action_enabled || !handle->running ||
        handle->cancel_requested || handle->generation != generation ||
        handle->rolling_generation != generation ||
        !handle->owner_acquired || !handle->rolling_task_alive) {
        person_follow_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    handle->forward_rolling = true;
    handle->last_center_confirmed_us = now_us;
    handle->forward_lease_deadline_us = now_us +
        (int64_t)PERSON_FOLLOW_ROLLING_FORWARD_LEASE_MS * 1000;
    strlcpy(handle->rolling_stop_reason, "center_lease_active",
            sizeof(handle->rolling_stop_reason));
    rolling_task = handle->rolling_task;
    person_follow_unlock(handle);

    TickType_t lease_ticks =
        pdMS_TO_TICKS(PERSON_FOLLOW_ROLLING_FORWARD_LEASE_MS);
    if (lease_ticks == 0) {
        lease_ticks = 1;
    }
    if (xTimerChangePeriod(handle->rolling_lease_timer, lease_ticks,
                           pdMS_TO_TICKS(10U)) != pdPASS) {
        (void)rehab_person_follow_rolling_preempt(
            handle, generation, "lease_timer_failed", false);
        return ESP_ERR_NO_MEM;
    }
    if (rolling_task != NULL && rolling_task != (TaskHandle_t)1) {
        xTaskNotifyGive(rolling_task);
    }
    return ESP_OK;
}

static void person_follow_rolling_lease_expired(TimerHandle_t timer)
{
    rehab_person_follow_handle_t handle = pvTimerGetTimerID(timer);
    uint32_t generation = 0U;
    int64_t now_us = esp_timer_get_time();
    uint32_t remaining_ms = 0U;
    bool active = false;
    if (person_follow_lock(handle)) {
        generation = handle->rolling_generation;
        active = handle->forward_rolling && !handle->cancel_requested &&
                 handle->generation == generation;
        remaining_ms = person_follow_remaining_ms(
            handle->forward_lease_deadline_us, now_us);
        person_follow_unlock(handle);
    }
    if (!active) {
        return;
    }
    if (remaining_ms > 0U) {
        TickType_t ticks = pdMS_TO_TICKS(remaining_ms);
        (void)xTimerChangePeriod(timer, ticks == 0 ? 1 : ticks, 0);
        return;
    }
    (void)rehab_person_follow_rolling_preempt(
        handle, generation, "lease_expired", false);
}

static void person_follow_rolling_worker(void *arg)
{
    rehab_person_follow_handle_t handle = arg;
    uint32_t generation = 0U;
    if (person_follow_lock(handle)) {
        generation = handle->rolling_generation;
        handle->rolling_task = xTaskGetCurrentTaskHandle();
        person_follow_unlock(handle);
    }
    for (;;) {
        (void)ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        for (;;) {
            int64_t now_us = esp_timer_get_time();
            if (!rehab_person_follow_rolling_segment_allowed(
                    handle, generation, now_us)) {
                break;
            }
            rehab_car_control_lease_t lease = {0};
            uint8_t speed = 0U;
            bool begin = false;
            if (person_follow_lock(handle)) {
                begin = !handle->rolling_task_busy &&
                        handle->forward_rolling &&
                        !handle->rolling_exit_requested &&
                        !handle->cancel_requested &&
                        handle->generation == generation &&
                        handle->rolling_generation == generation &&
                        now_us <= handle->forward_lease_deadline_us &&
                        handle->owner_acquired;
                if (begin) {
                    handle->rolling_task_busy = true;
                    lease = handle->car_lease;
                    speed = handle->config.action_speed;
                }
                person_follow_unlock(handle);
            }
            if (!begin) {
                break;
            }
            esp_err_t result = rehab_person_follow_car_move(
                handle->car, &lease, REHAB_CAR_DIRECTION_FORWARD, speed,
                PERSON_FOLLOW_ROLLING_FORWARD_SEGMENT_MS);
            bool keep_rolling = rehab_person_follow_rolling_segment_finished(
                handle, generation, result, esp_timer_get_time());
            ESP_LOGI(TAG,
                     "PF_ROLLING gen=%u segment_ms=%u result=%s continue=%u",
                     (unsigned)generation,
                     (unsigned)PERSON_FOLLOW_ROLLING_FORWARD_SEGMENT_MS,
                     esp_err_to_name(result), keep_rolling ? 1U : 0U);
            if (!keep_rolling) {
                break;
            }
        }
        bool exit_requested = true;
        if (person_follow_lock(handle)) {
            exit_requested = handle->rolling_exit_requested ||
                             handle->cancel_requested ||
                             handle->generation != generation ||
                             handle->rolling_generation != generation;
            person_follow_unlock(handle);
        }
        if (exit_requested) {
            break;
        }
    }
    TaskHandle_t worker_task = NULL;
    if (person_follow_lock(handle)) {
        handle->forward_rolling = false;
        handle->rolling_task_busy = false;
        handle->rolling_task_alive = false;
        handle->rolling_task = NULL;
        worker_task = handle->worker_task;
        person_follow_unlock(handle);
    }
    if (worker_task != NULL && worker_task != (TaskHandle_t)1) {
        xTaskNotifyGive(worker_task);
    }
    xSemaphoreGive(handle->rolling_done_event);
    vTaskDelete(NULL);
}

static esp_err_t person_follow_shutdown_rolling(
    rehab_person_follow_handle_t handle, uint32_t generation,
    const char *reason)
{
    TaskHandle_t rolling_task = NULL;
    bool wait_task = false;
    if (!person_follow_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (handle->rolling_generation == generation) {
        bool was_active = handle->forward_rolling ||
                          handle->rolling_task_busy;
        handle->forward_rolling = false;
        handle->forward_lease_deadline_us = 0;
        handle->rolling_exit_requested = true;
        if (was_active || handle->rolling_stop_reason[0] == '\0') {
            strlcpy(handle->rolling_stop_reason, reason,
                    sizeof(handle->rolling_stop_reason));
        }
        rolling_task = handle->rolling_task;
        wait_task = handle->rolling_task_alive;
    }
    person_follow_unlock(handle);
    if (handle->rolling_lease_timer != NULL) {
        (void)xTimerStop(handle->rolling_lease_timer, 0);
    }
    if (rolling_task != NULL && rolling_task != (TaskHandle_t)1) {
        xTaskNotifyGive(rolling_task);
    }
    if (wait_task && rolling_task != xTaskGetCurrentTaskHandle() &&
        xSemaphoreTake(handle->rolling_done_event,
                       pdMS_TO_TICKS(PERSON_FOLLOW_STOP_WAIT_MS)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

esp_err_t rehab_person_follow_execute_action(
    rehab_person_follow_handle_t handle,
    uint32_t generation,
    rehab_person_follow_action_t action,
    uint32_t planned_hold_ms,
    uint32_t *out_hold_ms)
{
    if (handle == NULL || out_hold_ms == NULL ||
        action <= REHAB_PERSON_FOLLOW_ACTION_NONE ||
        action > REHAB_PERSON_FOLLOW_ACTION_FORWARD) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_hold_ms = 0U;
    rehab_person_follow_config_t config = {0};
    rehab_car_control_lease_t lease = {0};
    if (!person_follow_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (!handle->config.action_enabled || handle->cancel_requested ||
        handle->generation != generation || !handle->owner_acquired) {
        person_follow_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    config = handle->config;
    lease = handle->car_lease;
    person_follow_unlock(handle);

    rehab_car_direction_t direction = REHAB_CAR_DIRECTION_STOP;
    switch (action) {
    case REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT:
        if (planned_hold_ms < REHAB_CAR_HOLD_MIN_MS ||
            planned_hold_ms > REHAB_CAR_HOLD_MAX_MS) {
            return ESP_ERR_INVALID_ARG;
        }
        direction = REHAB_CAR_DIRECTION_ROTATE_CCW;
        *out_hold_ms = planned_hold_ms;
        break;
    case REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT:
    case REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT:
        if (planned_hold_ms < REHAB_CAR_HOLD_MIN_MS ||
            planned_hold_ms > REHAB_CAR_HOLD_MAX_MS) {
            return ESP_ERR_INVALID_ARG;
        }
        direction = REHAB_CAR_DIRECTION_ROTATE_CW;
        *out_hold_ms = planned_hold_ms;
        break;
    case REHAB_PERSON_FOLLOW_ACTION_FORWARD:
        if (planned_hold_ms < REHAB_CAR_HOLD_MIN_MS ||
            planned_hold_ms > REHAB_CAR_HOLD_MAX_MS) {
            return ESP_ERR_INVALID_ARG;
        }
        direction = REHAB_CAR_DIRECTION_FORWARD;
        *out_hold_ms = planned_hold_ms;
        break;
    default:
        return ESP_ERR_INVALID_ARG;
    }

    esp_err_t result = rehab_person_follow_car_move(
        handle->car, &lease, direction, config.action_speed, *out_hold_ms);
    if (result != ESP_OK) {
        (void)rehab_person_follow_car_stop(handle->car, &lease);
    }
    return result;
}

uint32_t rehab_person_follow_post_action_settle_ms(
    const rehab_person_follow_config_t *config,
    rehab_person_follow_action_t executed_action,
    esp_err_t action_result,
    bool generation_current)
{
    if (config == NULL) {
        return 0U;
    }
    if (action_result != ESP_OK || !generation_current) {
        return config->fallback_post_action_settle_ms;
    }
    switch (executed_action) {
    case REHAB_PERSON_FOLLOW_ACTION_FORWARD:
        return config->forward_post_action_settle_ms;
    case REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT:
    case REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT:
    case REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT:
        return config->turn_post_action_settle_ms;
    default:
        return config->fallback_post_action_settle_ms;
    }
}

static void person_follow_preempted(void *ctx)
{
    rehab_person_follow_handle_t handle = ctx;
    TaskHandle_t task = NULL;
    TaskHandle_t rolling_task = NULL;
    if (!person_follow_lock(handle)) {
        return;
    }
    if (handle->running || handle->start_in_progress) {
        handle->cancel_requested = true;
        handle->generation = person_follow_next_generation(handle->generation);
        handle->startup_observation_active = false;
        handle->startup_observation_exit_reason =
            REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_GENERATION_CHANGED;
        handle->state = REHAB_PERSON_FOLLOW_STATE_STOPPING;
        handle->shutdown_pending = handle->worker_alive;
        handle->forward_rolling = false;
        handle->forward_lease_deadline_us = 0;
        handle->rolling_exit_requested = true;
        strlcpy(handle->rolling_stop_reason, "car_owner_preempted",
                sizeof(handle->rolling_stop_reason));
        strlcpy(handle->last_reason, "car_owner_preempted",
                sizeof(handle->last_reason));
        task = handle->worker_task;
        rolling_task = handle->rolling_task;
    }
    person_follow_unlock(handle);
    if (task != NULL && task != (TaskHandle_t)1) {
        xTaskNotifyGive(task);
    }
    if (rolling_task != NULL && rolling_task != (TaskHandle_t)1) {
        xTaskNotifyGive(rolling_task);
    }
}

static void person_follow_publish_round(
    rehab_person_follow_handle_t handle,
    uint32_t generation,
    int64_t loop_started_us,
    const rehab_person_follow_perception_result_t *perception,
    const rehab_person_follow_observation_t *observation,
    const rehab_person_follow_decision_t *decision,
    esp_err_t observe_result,
    esp_err_t decision_result,
    rehab_person_follow_action_t actual_action,
    esp_err_t action_result,
    uint32_t hold_ms,
    uint32_t post_action_wait_ms)
{
    rehab_person_follow_snapshot_info_t info = {0};
    info.width = PERSON_FOLLOW_IMAGE_WIDTH;
    info.height = PERSON_FOLLOW_IMAGE_HEIGHT;
    info.camera_generation = perception->camera_generation;
    info.capture_timestamp_us = perception->capture_timestamp_us;
    info.raw_count = perception->raw_detection_count;
    info.dedup_count = perception->dedup_detection_count;
    info.target_found = observe_result == ESP_OK && observation->valid &&
                        observation->target_found;
    info.selected_valid = info.target_found;
    if (info.selected_valid) {
        info.bbox_x1 = observation->bbox_x1;
        info.bbox_y1 = observation->bbox_y1;
        info.bbox_x2 = observation->bbox_x2;
        info.bbox_y2 = observation->bbox_y2;
        info.center_x = observation->center_x;
        info.center_y = observation->center_y;
        info.confidence = observation->confidence;
    }
    info.bbox_width_ratio = decision->bbox_width_ratio;
    info.person_center_x_ratio = decision->person_center_x_ratio;
    info.left_background_ratio = decision->left_background_ratio;
    info.right_background_ratio = decision->right_background_ratio;
    info.zone = perception->zone;
    info.planned_action = decision->action;
    info.actual_action = actual_action;
    strlcpy(info.action_reason,
            decision->reason != NULL ? decision->reason : "unknown",
            sizeof(info.action_reason));
    info.speed = handle->config.action_speed;
    info.hold_ms = hold_ms;
    info.post_action_wait_ms = post_action_wait_ms;
    info.last_car_result = action_result;
    info.overshoot_reverse = decision->overshoot_reverse;
    info.camera_ms = perception->camera_ms;
    info.jpeg_bytes = perception->jpeg_bytes;
    info.http_ms = perception->http_ms;
    info.http_status = perception->http_status;
    info.pc_inference_ms = perception->pc_inference_ms;
    info.pc_total_ms = perception->pc_total_ms;
    info.loop_total_ms = person_follow_elapsed_ms(loop_started_us);
    info.worker_generation = generation;

    uint32_t stage_token = perception->image_stage_token;
    if (observe_result != ESP_OK) {
        strlcpy(info.error_stage,
                perception->error_stage[0] ? perception->error_stage : "observe",
                sizeof(info.error_stage));
        strlcpy(info.last_error,
                perception->error_code[0] ? perception->error_code
                                          : esp_err_to_name(observe_result),
                sizeof(info.last_error));
        info.target_found = false;
        info.selected_valid = false;
        info.zone = REHAB_PERSON_FOLLOW_TARGET_NO_TARGET;
        info.actual_action = REHAB_PERSON_FOLLOW_ACTION_NONE;
        stage_token = 0U;
    } else if (decision_result != ESP_OK) {
        strlcpy(info.error_stage, "decision", sizeof(info.error_stage));
        strlcpy(info.last_error, "decision_invalid", sizeof(info.last_error));
        info.actual_action = REHAB_PERSON_FOLLOW_ACTION_NONE;
        rehab_person_follow_snapshot_discard_stage(handle, stage_token);
        stage_token = 0U;
    } else if (action_result != ESP_OK) {
        strlcpy(info.error_stage, "car", sizeof(info.error_stage));
        strlcpy(info.last_error, "car_action_failed", sizeof(info.last_error));
    } else {
        strlcpy(info.error_stage, "none", sizeof(info.error_stage));
        strlcpy(info.last_error, "none", sizeof(info.last_error));
    }

    if (person_follow_lock(handle)) {
        info.owner_acquired = handle->owner_acquired;
        info.cancel_requested = handle->cancel_requested;
        info.has_forwarded_once =
            handle->decision_context.has_forwarded_once;
        info.fine_turn_mode = handle->decision_context.fine_turn_mode;
        info.pending_target_turn_check =
            handle->decision_context.turn_check_pending;
        info.last_target_turn_action =
            handle->decision_context.last_successful_turn_direction;
        info.next_turn_hold_ms =
            handle->decision_context.turn_hold_ms;
        info.approach_phase = handle->decision_context.approach_phase;
        info.first_align_completed =
            handle->decision_context.first_align_completed;
        info.active_align_gate =
            handle->decision_context.active_align_gate;
        info.align_stable_count =
            handle->decision_context.align_stable_count;
        info.align_stable_required =
            REHAB_PERSON_FOLLOW_ALIGN_STABLE_FRAMES_REQUIRED;
        info.first_align_trigger_frame_id =
            handle->decision_context.first_align_trigger_frame_id;
        info.final_align_trigger_frame_id =
            handle->decision_context.final_align_trigger_frame_id;
        info.align_turn_hold_ms =
            handle->decision_context.turn_hold_ms;
        info.align_last_turn_direction =
            handle->decision_context.last_successful_turn_direction;
        info.align_overshoot_count =
            handle->decision_context.align_overshoot_count;
        info.turn_hold_ms = handle->decision_context.turn_hold_ms;
        info.target_acquired_once =
            handle->decision_context.target_acquired_once;
        info.turn_check_pending =
            handle->decision_context.turn_check_pending;
        info.last_successful_turn_direction =
            handle->decision_context.last_successful_turn_direction;
        info.final_forward_attempted_frame_id =
            handle->decision_context.final_forward_attempted_frame_id;
        info.arrived = handle->decision_context.approach_phase ==
            REHAB_PERSON_FOLLOW_APPROACH_ARRIVED;
        info.arrival_reason = handle->decision_context.arrival_reason;
        int64_t now_us = esp_timer_get_time();
        info.forward_rolling = handle->forward_rolling;
        info.forward_lease_remaining_ms = person_follow_remaining_ms(
            handle->forward_lease_deadline_us, now_us);
        info.forward_segments_completed =
            handle->forward_segments_completed;
        info.last_center_confirmed_age_ms = person_follow_age_ms(
            handle->last_center_confirmed_us, now_us);
        strlcpy(info.rolling_stop_reason, handle->rolling_stop_reason,
                sizeof(info.rolling_stop_reason));
        info.rolling_task_alive = handle->rolling_task_alive;
        info.rolling_task_busy = handle->rolling_task_busy;
        info.startup_observation_active =
            handle->startup_observation_active;
        info.startup_observation_remaining_ms =
            handle->startup_observation_active
                ? person_follow_remaining_ms(
                    handle->startup_observation_started_us +
                        (int64_t)REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_GRACE_MS *
                            1000,
                    now_us)
                : 0U;
        info.startup_observation_exit_reason =
            handle->startup_observation_exit_reason;
        person_follow_unlock(handle);
    }
    esp_err_t publish_result = rehab_person_follow_snapshot_publish(
        handle, generation, stage_token, &info);
    if (publish_result != ESP_OK && stage_token != 0U) {
        rehab_person_follow_snapshot_discard_stage(handle, stage_token);
    }
}

static void person_follow_worker(void *arg)
{
    rehab_person_follow_handle_t handle = arg;
    uint32_t generation = 0U;
    if (person_follow_lock(handle)) {
        generation = handle->generation;
        handle->worker_task = xTaskGetCurrentTaskHandle();
        if (!handle->cancel_requested) {
            handle->state = REHAB_PERSON_FOLLOW_STATE_SENSE;
        }
        handle->decision_context.state = handle->state;
        handle->decision_context.generation = generation;
        person_follow_unlock(handle);
    }
    rehab_person_follow_log_stack("worker_start");

    for (;;) {
        int64_t loop_started_us = esp_timer_get_time();
        bool cancelled = true;
        uint32_t current_generation = 0U;
        uint32_t runtime_ms = 0U;
        if (person_follow_lock(handle)) {
            current_generation = handle->generation;
            cancelled = handle->cancel_requested ||
                        current_generation != generation;
            int64_t elapsed_us = esp_timer_get_time() - handle->started_at_us;
            runtime_ms = elapsed_us > 0 ? (uint32_t)(elapsed_us / 1000) : 0U;
            handle->runtime_ms = runtime_ms;
            person_follow_unlock(handle);
        }
        if (cancelled) {
            break;
        }
        if (runtime_ms >= handle->config.max_runtime_ms &&
            handle->decision_context.approach_phase !=
                REHAB_PERSON_FOLLOW_APPROACH_ARRIVED) {
            if (person_follow_lock(handle)) {
                handle->state = REHAB_PERSON_FOLLOW_STATE_ERROR;
                handle->last_planned_action = REHAB_PERSON_FOLLOW_ACTION_NONE;
                strlcpy(handle->last_error, "runtime_limit",
                        sizeof(handle->last_error));
                strlcpy(handle->last_reason, "runtime_limit_stop",
                        sizeof(handle->last_reason));
                person_follow_unlock(handle);
            }
            (void)rehab_person_follow_rolling_preempt(
                handle, generation, "runtime_limit", true);
            break;
        }

        rehab_person_follow_observation_t observation = {0};
        rehab_person_follow_perception_result_t perception = {0};
        uint32_t sequence = handle->frame_sequence++;
        esp_err_t observe_result = ESP_OK;
        if (handle->config.perception_provider ==
            REHAB_PERSON_FOLLOW_PERCEPTION_REAL) {
            observe_result = rehab_person_follow_perceive(
                handle, generation, sequence, &perception);
            observation = perception.observation;
            if (observe_result == ESP_ERR_INVALID_STATE &&
                !rehab_person_follow_generation_current(handle, generation)) {
                break;
            }
        } else {
            observe_result = rehab_person_follow_mock_observe(
                handle->config.mock_mode, handle->mock_sequence++,
                &observation);
            perception.zone = rehab_person_follow_classify_target(
                observation.valid && observation.target_found,
                observation.center_x);
        }
        if (observe_result != ESP_OK) {
            observation.valid = false;
            const char *stop_reason = perception.error_stage[0] != '\0'
                ? perception.error_stage : "perception_failure";
            (void)rehab_person_follow_rolling_preempt(
                handle, generation, stop_reason, true);
        }

        rehab_person_follow_decision_t decision = {0};
        esp_err_t decision_result = ESP_ERR_TIMEOUT;
        bool log_startup_no_target_suppression = false;
        bool startup_target_found = false;
        bool startup_grace_expired = false;
        if (person_follow_lock(handle)) {
            current_generation = handle->generation;
            bool startup_was_active = handle->startup_observation_active;
            bool suppressed = rehab_person_follow_suppress_startup_no_target(
                handle, generation, esp_timer_get_time(), observe_result,
                &observation, &decision,
                &log_startup_no_target_suppression);
            if (suppressed) {
                decision_result = ESP_OK;
            } else {
                startup_grace_expired = startup_was_active &&
                    !handle->startup_observation_active &&
                    handle->startup_observation_exit_reason ==
                        REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_GRACE_EXPIRED_NO_TARGET;
                decision_result = rehab_person_follow_decide(
                    &handle->decision_context, &observation,
                    sequence, current_generation, handle->cancel_requested,
                    handle->config.max_consecutive_errors,
                    handle->config.turn_hold_ms,
                    handle->config.forward_hold_ms, &decision);
                rehab_person_follow_accept_startup_target(
                    handle, generation, observe_result, &observation,
                    decision_result);
                startup_target_found = startup_was_active &&
                    !handle->startup_observation_active &&
                    handle->startup_observation_exit_reason ==
                        REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_TARGET_FOUND;
            }
            person_follow_unlock(handle);
        }
        if (log_startup_no_target_suppression) {
            ESP_LOGI(TAG,
                     "startup observation suppressing NO_TARGET gen=%u "
                     "grace_ms=%u",
                     (unsigned)generation,
                     (unsigned)REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_GRACE_MS);
        }
        if (startup_target_found) {
            ESP_LOGI(TAG,
                     "startup observation ended early: target found gen=%u",
                     (unsigned)generation);
        }
        if (decision.first_target_acquired) {
            ESP_LOGI(TAG,
                     "FIRST_TARGET_ACQUIRED gen=%u frame=%u zone=%s",
                     (unsigned)generation, (unsigned)sequence,
                     rehab_person_follow_target_zone_name(perception.zone));
            ESP_LOGI(TAG,
                     "UNIFIED_TURN_ACTIVE gen=%u hold_ms=%u",
                     (unsigned)generation,
                     (unsigned)handle->decision_context.turn_hold_ms);
        }
        if (startup_grace_expired) {
            if (handle->config.action_enabled) {
                ESP_LOGI(TAG,
                         "INITIAL_SEARCH_RIGHT_FIXED gen=%u hold_ms=%u "
                         "target_acquired_once=false",
                         (unsigned)generation,
                         (unsigned)handle->decision_context.turn_hold_ms);
            } else {
                ESP_LOGI(TAG,
                         "startup observation expired: valid NO_TARGET "
                         "action disabled gen=%u",
                         (unsigned)generation);
            }
        }
        if (decision_result != ESP_OK) {
            (void)rehab_person_follow_rolling_preempt(
                handle, generation, "decision_failure", true);
        }
        if (decision.reason != NULL &&
            (strcmp(decision.reason, "first_align_trigger_width") == 0 ||
             strcmp(decision.reason, "final_align_trigger_width") == 0)) {
            ESP_LOGI(TAG,
                     "%s trigger gen=%u frame=%u width=%.3f "
                     "inherit_hold_ms=%u",
                     rehab_person_follow_align_gate_name(
                         handle->decision_context.active_align_gate),
                     (unsigned)generation, (unsigned)sequence,
                     decision.bbox_width_ratio,
                     (unsigned)handle->decision_context.turn_hold_ms);
        } else if (decision.align_turn) {
            ESP_LOGI(TAG,
                     "%s_ALIGN decision gen=%u frame=%u center=%.3f "
                     "action=%s hold_ms=%u mode=%s overshoots=%u",
                     rehab_person_follow_align_gate_name(
                         handle->decision_context.active_align_gate),
                     (unsigned)generation, (unsigned)sequence,
                     decision.person_center_x_ratio,
                     rehab_person_follow_action_name(decision.action),
                     (unsigned)decision.hold_ms,
                     decision.reason,
                     (unsigned)handle->decision_context.align_overshoot_count);
        } else if (decision.reason != NULL &&
                   strstr(decision.reason, "_stable_1_of_2") != NULL) {
            ESP_LOGI(TAG, "%s stable 1/2 gen=%u frame=%u",
                     rehab_person_follow_approach_phase_name(
                         handle->decision_context.approach_phase),
                     (unsigned)generation, (unsigned)sequence);
        } else if (decision.reason != NULL &&
                   strcmp(decision.reason,
                          "first_align_stable_2_of_2_complete") == 0) {
            ESP_LOGI(TAG,
                     "FIRST_ALIGN stable 2/2 complete; NORMAL_FOLLOW resumed "
                     "gen=%u frame=%u",
                     (unsigned)generation, (unsigned)sequence);
        } else if (decision.reason != NULL &&
                   strcmp(decision.reason,
                          "final_align_stable_2_of_2_final_forward") == 0) {
            ESP_LOGI(TAG, "FINAL_ALIGN stable 2/2 gen=%u frame=%u",
                     (unsigned)generation, (unsigned)sequence);
        }
        if (decision.align_stable_reset) {
            ESP_LOGI(TAG,
                     "%s_ALIGN stable count reset gen=%u frame=%u reason=%s",
                     rehab_person_follow_align_gate_name(
                         handle->decision_context.active_align_gate),
                     (unsigned)generation, (unsigned)sequence,
                     decision.align_stable_reset_reason != NULL
                         ? decision.align_stable_reset_reason : "unknown");
        }
        if (person_follow_lock(handle)) {
            if (handle->generation == generation &&
                !handle->cancel_requested) {
                handle->state = decision.next_state;
                handle->last_target_found = observation.valid &&
                                                   observation.target_found;
                handle->last_center_x = observation.center_x;
                handle->last_confidence = observation.confidence;
                handle->last_zone = perception.zone;
                handle->last_http_status = perception.http_status;
                handle->last_jpeg_bytes = perception.jpeg_bytes;
                handle->last_detection_count =
                    perception.raw_detection_count;
                handle->last_dedup_count =
                    perception.dedup_detection_count;
                handle->last_perception_ms = perception.perception_ms;
                handle->last_bbox_width_ratio = decision.bbox_width_ratio;
                handle->last_person_center_x_ratio =
                    decision.person_center_x_ratio;
                handle->last_left_background_ratio =
                    decision.left_background_ratio;
                handle->last_right_background_ratio =
                    decision.right_background_ratio;
                if (observe_result == ESP_OK && observation.target_found) {
                    handle->last_selected_center_valid = true;
                    handle->last_selected_center_x = observation.center_x;
                }
                handle->last_planned_action = decision.action;
                if (decision.forward_attempted) {
                    handle->forward_attempted = true;
                    handle->motion_unconfirmed = true;
                }
                handle->blocked_unknown = false;
                strlcpy(handle->last_reason,
                        decision.reason != NULL ? decision.reason : "unknown",
                        sizeof(handle->last_reason));
                if (decision_result != ESP_OK) {
                    strlcpy(handle->last_error, "decision_invalid",
                            sizeof(handle->last_error));
                } else if (!observation.valid) {
                    strlcpy(handle->last_error,
                            perception.error_code[0] != '\0'
                                ? perception.error_code
                                : "mock_technical_error",
                            sizeof(handle->last_error));
                } else {
                    strlcpy(handle->last_error, "none",
                            sizeof(handle->last_error));
                }
            }
            person_follow_unlock(handle);
        }

        if (handle->config.perception_provider ==
            REHAB_PERSON_FOLLOW_PERCEPTION_REAL) {
            if (observe_result == ESP_OK) {
                ESP_LOGI(TAG,
                         "PF_FRAME gen=%u seq=%u jpeg_bytes=%u http_ms=%u "
                         "total_ms=%u http_status=%d raw=%u dedup=%u "
                         "selected=%u center_x=%.3f zone=%s conf=%.3f",
                         (unsigned)generation, (unsigned)sequence,
                         (unsigned)perception.jpeg_bytes,
                         (unsigned)perception.http_ms,
                         (unsigned)perception.perception_ms,
                         perception.http_status,
                         (unsigned)perception.raw_detection_count,
                         (unsigned)perception.dedup_detection_count,
                         observation.target_found ? 1U : 0U,
                         observation.center_x,
                         rehab_person_follow_target_zone_name(perception.zone),
                         observation.confidence);
            } else {
                ESP_LOGW(TAG,
                         "PF_ERROR gen=%u seq=%u stage=%s code=%s "
                         "http_status=%d jpeg_bytes=%u total_ms=%u",
                         (unsigned)generation, (unsigned)sequence,
                         perception.error_stage[0] != '\0'
                             ? perception.error_stage : "observe",
                         perception.error_code[0] != '\0'
                             ? perception.error_code : esp_err_to_name(observe_result),
                         perception.http_status,
                         (unsigned)perception.jpeg_bytes,
                         (unsigned)perception.perception_ms);
            }
        }

        if (decision.next_state == REHAB_PERSON_FOLLOW_STATE_ERROR ||
            decision.next_state == REHAB_PERSON_FOLLOW_STATE_STOPPING) {
            person_follow_publish_round(
                handle, generation, loop_started_us, &perception,
                &observation, &decision, observe_result, decision_result,
                REHAB_PERSON_FOLLOW_ACTION_NONE, ESP_OK, 0U, 0U);
            break;
        }

        esp_err_t action_result = ESP_OK;
        uint32_t hold_ms = 0U;
        rehab_person_follow_action_t executed_action =
            REHAB_PERSON_FOLLOW_ACTION_NONE;
        bool rolling_forward_dispatched = false;
        if (handle->config.action_enabled && decision.stop_rolling) {
            action_result = rehab_person_follow_rolling_preempt(
                handle, generation,
                decision.final_forward ? "final_forward_ready"
                    : decision.next_state ==
                            REHAB_PERSON_FOLLOW_STATE_FIRST_ALIGN
                        ? "first_align_stop"
                    : decision.next_state ==
                            REHAB_PERSON_FOLLOW_STATE_FINAL_ALIGN
                        ? "final_align_stop"
                        : "approach_phase_stop",
                true);
            if (action_result == ESP_OK &&
                decision.reason != NULL &&
                (strcmp(decision.reason, "first_align_trigger_width") == 0 ||
                 strcmp(decision.reason, "final_align_trigger_width") == 0)) {
                ESP_LOGI(TAG,
                         "%s_ALIGN rolling STOP complete gen=%u frame=%u",
                         handle->decision_context.active_align_gate ==
                                 REHAB_PERSON_FOLLOW_ALIGN_GATE_FIRST
                             ? "FIRST" : "FINAL",
                         (unsigned)generation, (unsigned)sequence);
            }
        }
        if (decision.align_trigger &&
            (!handle->config.action_enabled || action_result == ESP_OK) &&
            person_follow_lock(handle)) {
            if (handle->generation == generation &&
                !handle->cancel_requested) {
                rehab_person_follow_commit_align_stop(
                    &handle->decision_context, &decision, true);
            }
            person_follow_unlock(handle);
        }
        if (handle->config.action_enabled &&
            decision.action != REHAB_PERSON_FOLLOW_ACTION_NONE &&
            action_result == ESP_OK) {
            if (!rehab_person_follow_generation_current(handle, generation)) {
                rehab_person_follow_snapshot_discard_stage(
                    handle, perception.image_stage_token);
                break;
            }
            executed_action = decision.action;
            if (decision.action == REHAB_PERSON_FOLLOW_ACTION_FORWARD &&
                PERSON_FOLLOW_ROLLING_FORWARD_ENABLED &&
                !decision.final_forward) {
                hold_ms = PERSON_FOLLOW_ROLLING_FORWARD_SEGMENT_MS;
                action_result = rehab_person_follow_rolling_confirm_center(
                    handle, generation, esp_timer_get_time());
                rolling_forward_dispatched = action_result == ESP_OK;
            } else {
                const char *stop_reason =
                    decision.action == REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT
                        ? "target_left"
                    : decision.action == REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT
                        ? "target_right"
                    : decision.action == REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT
                        ? "no_target"
                        : "non_forward_action";
                if (!decision.stop_rolling) {
                    action_result = rehab_person_follow_rolling_preempt(
                        handle, generation, stop_reason, true);
                }
                if (action_result == ESP_OK) {
                    if (decision.final_forward) {
                        ESP_LOGI(TAG,
                                 "FINAL_FORWARD start gen=%u frame=%u hold_ms=%u",
                                 (unsigned)generation, (unsigned)sequence,
                                 (unsigned)decision.hold_ms);
                    }
                    action_result = rehab_person_follow_execute_action(
                        handle, generation, decision.action, decision.hold_ms,
                        &hold_ms);
                }
            }
        }
        bool generation_current = false;
        bool action_committed = false;
        bool arrived_committed = false;
        uint32_t post_action_wait_ms = handle->config.action_enabled
            ? handle->config.fallback_post_action_settle_ms
            : handle->config.loop_interval_ms;
        if (person_follow_lock(handle)) {
            generation_current = handle->generation == generation &&
                                 !handle->cancel_requested;
            action_committed = generation_current &&
                executed_action != REHAB_PERSON_FOLLOW_ACTION_NONE &&
                action_result == ESP_OK;
            if (handle->config.action_enabled) {
                post_action_wait_ms = rolling_forward_dispatched ? 0U :
                    rehab_person_follow_post_action_settle_ms(
                        &handle->config, executed_action, action_result,
                        generation_current);
            }
            if (generation_current) {
                rehab_person_follow_commit_action(
                    &handle->decision_context, &decision, action_committed);
                if (decision.final_forward) {
                    if (action_committed) {
                        handle->state = REHAB_PERSON_FOLLOW_STATE_ARRIVED;
                        arrived_committed = true;
                        strlcpy(handle->last_reason, "arrived_locked",
                                sizeof(handle->last_reason));
                    } else {
                        handle->state =
                            REHAB_PERSON_FOLLOW_STATE_FINAL_ALIGN;
                        strlcpy(handle->last_reason,
                                "final_forward_failed_wait_fresh_frame",
                                sizeof(handle->last_reason));
                    }
                } else if (decision.direct_arrival) {
                    handle->state = REHAB_PERSON_FOLLOW_STATE_ARRIVED;
                    arrived_committed = true;
                    strlcpy(handle->last_reason,
                            "both_margins_missing_centered",
                            sizeof(handle->last_reason));
                }
                handle->last_action = action_committed
                    ? executed_action : REHAB_PERSON_FOLLOW_ACTION_NONE;
                handle->last_post_action_wait_ms = post_action_wait_ms;
                handle->last_car_result = action_result;
                if (action_committed &&
                    executed_action == REHAB_PERSON_FOLLOW_ACTION_FORWARD) {
                    handle->motion_unconfirmed = false;
                }
                if (action_result != ESP_OK) {
                    strlcpy(handle->last_error, "car_action_failed",
                            sizeof(handle->last_error));
                }
            }
            person_follow_unlock(handle);
        }
        if (arrived_committed) {
            (void)rehab_business_audio_notify(
                REHAB_BUSINESS_AUDIO_PERSON_FOLLOW_ARRIVED);
        }
        rehab_person_follow_action_t actual_action = action_committed
            ? executed_action : REHAB_PERSON_FOLLOW_ACTION_NONE;
        if (decision.final_forward && action_committed) {
            ESP_LOGI(TAG,
                     "FINAL_FORWARD success gen=%u frame=%u; ARRIVED locked",
                     (unsigned)generation, (unsigned)sequence);
        }
        ESP_LOGI(TAG,
                 "PERSON_FOLLOW_ACTION zone=%s bbox_width_ratio=%.3f "
                 "center_ratio=%.3f left_margin=%.3f right_margin=%.3f "
                 "approach_phase=%s "
                 "planned_action=%s actual_action=%s speed=%u "
                 "action_hold_ms=%u post_action_settle_ms=%u result=%s "
                 "has_forwarded_once=%u "
                 "fine_turn_mode=%u overshoot_reverse=%u pending_turn=%u "
                 "last_target_turn=%s next_turn_hold_ms=%u",
                 rehab_person_follow_target_zone_name(perception.zone),
                 decision.bbox_width_ratio,
                 decision.person_center_x_ratio,
                 decision.left_background_ratio,
                 decision.right_background_ratio,
                 rehab_person_follow_approach_phase_name(
                     handle->decision_context.approach_phase),
                 rehab_person_follow_action_name(decision.action),
                 rehab_person_follow_action_name(actual_action),
                 (unsigned)handle->config.action_speed,
                 (unsigned)hold_ms,
                 (unsigned)post_action_wait_ms,
                 handle->config.action_enabled
                     ? esp_err_to_name(action_result) : "disabled",
                 handle->decision_context.has_forwarded_once ? 1U : 0U,
                 handle->decision_context.fine_turn_mode ? 1U : 0U,
                 decision.overshoot_reverse ? 1U : 0U,
                 handle->decision_context.turn_check_pending ? 1U : 0U,
                 rehab_person_follow_action_name(
                     handle->decision_context.last_successful_turn_direction),
                 (unsigned)handle->decision_context.turn_hold_ms);
        person_follow_publish_round(
            handle, generation, loop_started_us, &perception, &observation,
            &decision, observe_result, decision_result, actual_action,
            action_result, hold_ms, post_action_wait_ms);
        if (action_result == ESP_ERR_INVALID_STATE &&
            !rehab_person_follow_generation_current(handle, generation)) {
            break;
        }
        if (!person_follow_wait_cancelable(
                handle, generation, post_action_wait_ms)) {
            break;
        }
    }

    bool rolling_active = false;
    if (person_follow_lock(handle)) {
        rolling_active = handle->forward_rolling ||
                         handle->rolling_task_busy;
        person_follow_unlock(handle);
    }
    if (rolling_active) {
        (void)rehab_person_follow_rolling_preempt(
            handle, generation, "worker_exit", true);
    }
    (void)person_follow_shutdown_rolling(
        handle, generation, "worker_exit");
    esp_err_t owned_stop = rehab_car_control_stop(handle->car,
                                                   &handle->car_lease);
    esp_err_t release_result = rehab_car_control_release(handle->car,
                                                          &handle->car_lease);
    if (release_result == ESP_ERR_INVALID_STATE) {
        memset(&handle->car_lease, 0, sizeof(handle->car_lease));
    }
    if (person_follow_lock(handle)) {
        bool cancelled = handle->cancel_requested ||
                         handle->generation != generation;
        if (cancelled) {
            handle->state = REHAB_PERSON_FOLLOW_STATE_CANCELLED;
        } else if (handle->state != REHAB_PERSON_FOLLOW_STATE_ERROR) {
            handle->state = REHAB_PERSON_FOLLOW_STATE_ERROR;
            strlcpy(handle->last_error, "worker_ended_unexpectedly",
                    sizeof(handle->last_error));
        }
        if (owned_stop != ESP_OK && owned_stop != ESP_ERR_INVALID_STATE) {
            strlcpy(handle->last_error, "cleanup_stop_failed",
                    sizeof(handle->last_error));
        }
        handle->running = false;
        if (handle->startup_observation_active) {
            handle->startup_observation_active = false;
            handle->startup_observation_exit_reason =
                REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_STOPPED;
        }
        handle->worker_alive = false;
        handle->worker_done = true;
        handle->owner_acquired = false;
        handle->shutdown_pending = false;
        handle->worker_task = NULL;
        person_follow_unlock(handle);
    }
    xSemaphoreGive(handle->worker_done_event);
    vTaskDelete(NULL);
}

esp_err_t rehab_person_follow_create(
    const rehab_person_follow_config_t *config,
    rehab_person_follow_handle_t *out_handle)
{
    if (out_handle == NULL || !person_follow_config_valid(config)) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_handle = NULL;
    rehab_person_follow_handle_t handle = calloc(1, sizeof(*handle));
    if (handle == NULL) {
        return ESP_ERR_NO_MEM;
    }
    handle->lock = xSemaphoreCreateMutex();
    handle->worker_done_event = xSemaphoreCreateBinary();
    handle->rolling_done_event = xSemaphoreCreateBinary();
    handle->rolling_lease_timer = xTimerCreate(
        "pf_roll_lease",
        pdMS_TO_TICKS(PERSON_FOLLOW_ROLLING_FORWARD_LEASE_MS),
        pdFALSE, handle, person_follow_rolling_lease_expired);
    if (handle->lock == NULL || handle->worker_done_event == NULL ||
        handle->rolling_done_event == NULL ||
        handle->rolling_lease_timer == NULL) {
        if (handle->rolling_lease_timer != NULL) {
            (void)xTimerDelete(handle->rolling_lease_timer, 0);
        }
        if (handle->rolling_done_event != NULL) {
            vSemaphoreDelete(handle->rolling_done_event);
        }
        if (handle->worker_done_event != NULL) {
            vSemaphoreDelete(handle->worker_done_event);
        }
        if (handle->lock != NULL) {
            vSemaphoreDelete(handle->lock);
        }
        free(handle);
        return ESP_ERR_NO_MEM;
    }
    handle->car = config->car;
    handle->config = *config;
    strlcpy(handle->endpoint_url, config->pc_endpoint,
            sizeof(handle->endpoint_url));
    handle->config.pc_endpoint = handle->endpoint_url;
    handle->response_buffer = heap_caps_calloc(
        1, PERSON_FOLLOW_RESPONSE_MAX + 1U,
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    handle->workspace = heap_caps_calloc(
        1, sizeof(*handle->workspace),
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    handle->snapshot_active_index = -1;
    size_t psram_before_snapshots = heap_caps_get_free_size(
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    for (size_t i = 0; i < PERSON_FOLLOW_SNAPSHOT_SLOTS; ++i) {
        handle->snapshot_slots[i].data = heap_caps_malloc(
            REHAB_PERSON_FOLLOW_SNAPSHOT_IMAGE_MAX,
            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    bool snapshot_buffers_ready = true;
    for (size_t i = 0; i < PERSON_FOLLOW_SNAPSHOT_SLOTS; ++i) {
        snapshot_buffers_ready = snapshot_buffers_ready &&
                                 handle->snapshot_slots[i].data != NULL;
    }
    if (handle->response_buffer == NULL || handle->workspace == NULL ||
        !snapshot_buffers_ready) {
        for (size_t i = 0; i < PERSON_FOLLOW_SNAPSHOT_SLOTS; ++i) {
            heap_caps_free(handle->snapshot_slots[i].data);
        }
        heap_caps_free(handle->workspace);
        heap_caps_free(handle->response_buffer);
        (void)xTimerDelete(handle->rolling_lease_timer, 0);
        vSemaphoreDelete(handle->rolling_done_event);
        vSemaphoreDelete(handle->worker_done_event);
        vSemaphoreDelete(handle->lock);
        free(handle);
        return ESP_ERR_NO_MEM;
    }
    size_t psram_after_snapshots = heap_caps_get_free_size(
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    ESP_LOGI(TAG,
             "PF_SNAPSHOT_MEMORY slots=%u bytes_each=%u psram_before=%u psram_after=%u",
             (unsigned)PERSON_FOLLOW_SNAPSHOT_SLOTS,
             (unsigned)REHAB_PERSON_FOLLOW_SNAPSHOT_IMAGE_MAX,
             (unsigned)psram_before_snapshots,
             (unsigned)psram_after_snapshots);
    handle->initialized = true;
    handle->state = REHAB_PERSON_FOLLOW_STATE_IDLE;
    handle->decision_context.state = REHAB_PERSON_FOLLOW_STATE_IDLE;
    rehab_person_follow_reset_adaptive_turn(
        &handle->decision_context, handle->config.turn_hold_ms);
    handle->worker_done = true;
    strlcpy(handle->rolling_stop_reason, "not_started",
            sizeof(handle->rolling_stop_reason));
    handle->last_planned_action = REHAB_PERSON_FOLLOW_ACTION_NONE;
    handle->last_action = REHAB_PERSON_FOLLOW_ACTION_NONE;
    handle->last_car_result = ESP_OK;
    strlcpy(handle->last_error, "none", sizeof(handle->last_error));
    strlcpy(handle->last_reason, "created", sizeof(handle->last_reason));
    *out_handle = handle;
    return ESP_OK;
}

static void person_follow_start_rollback(rehab_person_follow_handle_t handle,
                                         const char *error)
{
    uint32_t generation = 0U;
    if (person_follow_lock(handle)) {
        generation = handle->generation;
        handle->forward_rolling = false;
        handle->rolling_exit_requested = true;
        person_follow_unlock(handle);
    }
    (void)person_follow_shutdown_rolling(handle, generation,
                                         "start_rollback");
    (void)rehab_car_control_stop(handle->car, &handle->car_lease);
    (void)rehab_car_control_release(handle->car, &handle->car_lease);
    memset(&handle->car_lease, 0, sizeof(handle->car_lease));
    if (person_follow_lock(handle)) {
        handle->running = false;
        handle->start_in_progress = false;
        handle->worker_alive = false;
        handle->worker_done = true;
        handle->owner_acquired = false;
        handle->rolling_task_alive = false;
        handle->rolling_task_busy = false;
        handle->rolling_task = NULL;
        handle->startup_observation_active = false;
        handle->startup_observation_exit_reason =
            REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_STOPPED;
        handle->state = REHAB_PERSON_FOLLOW_STATE_ERROR;
        strlcpy(handle->last_error, error, sizeof(handle->last_error));
        person_follow_unlock(handle);
    }
}

esp_err_t rehab_person_follow_start(rehab_person_follow_handle_t handle)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!person_follow_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (!handle->initialized || handle->state != REHAB_PERSON_FOLLOW_STATE_IDLE ||
        handle->running || handle->start_in_progress || handle->worker_alive ||
        handle->owner_acquired || handle->rolling_task_alive ||
        handle->rolling_task != NULL) {
        person_follow_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    handle->start_in_progress = true;
    handle->cancel_requested = false;
    person_follow_unlock(handle);

    rehab_car_control_lease_t lease = {0};
    esp_err_t err = rehab_car_control_acquire(
        handle->car, REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW,
        person_follow_preempted, handle, &lease);
    if (err != ESP_OK) {
        if (person_follow_lock(handle)) {
            handle->start_in_progress = false;
            strlcpy(handle->last_error, "car_owner_busy",
                    sizeof(handle->last_error));
            strlcpy(handle->last_reason, "start_rejected_owner_busy",
                    sizeof(handle->last_reason));
            person_follow_unlock(handle);
        }
        return err;
    }
    handle->car_lease = lease;
    err = rehab_car_control_stop(handle->car, &handle->car_lease);
    if (err != ESP_OK) {
        person_follow_start_rollback(handle, "baseline_stop_failed");
        return err;
    }

    while (xSemaphoreTake(handle->worker_done_event, 0) == pdTRUE) {
    }
    while (xSemaphoreTake(handle->rolling_done_event, 0) == pdTRUE) {
    }
    if (!person_follow_lock(handle)) {
        person_follow_start_rollback(handle, "start_lock_failed");
        return ESP_ERR_TIMEOUT;
    }
    if (handle->cancel_requested) {
        person_follow_unlock(handle);
        person_follow_start_rollback(handle, "start_cancelled");
        return ESP_ERR_INVALID_STATE;
    }
    handle->generation = person_follow_next_generation(handle->generation);
    memset(&handle->decision_context, 0, sizeof(handle->decision_context));
    handle->decision_context.generation = handle->generation;
    handle->decision_context.state = REHAB_PERSON_FOLLOW_STATE_STARTING;
    rehab_person_follow_reset_adaptive_turn(
        &handle->decision_context, handle->config.turn_hold_ms);
    handle->mock_sequence = 0U;
    handle->frame_sequence = 1U;
    handle->started_at_us = esp_timer_get_time();
    handle->startup_observation_started_us = handle->started_at_us;
    handle->startup_observation_active = true;
    handle->startup_observation_no_target_logged = false;
    handle->startup_observation_exit_reason =
        REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_NONE;
    handle->runtime_ms = 0U;
    handle->state = REHAB_PERSON_FOLLOW_STATE_STARTING;
    handle->running = true;
    handle->start_in_progress = false;
    handle->worker_alive = true;
    handle->worker_done = false;
    handle->owner_acquired = true;
    handle->shutdown_pending = false;
    handle->forward_rolling = false;
    handle->rolling_task_busy = false;
    handle->rolling_exit_requested = false;
    handle->rolling_generation = handle->generation;
    handle->forward_segments_completed = 0U;
    handle->last_center_confirmed_us = 0;
    handle->forward_lease_deadline_us = 0;
    strlcpy(handle->rolling_stop_reason, "waiting_for_center",
            sizeof(handle->rolling_stop_reason));
    handle->last_target_found = false;
    handle->last_center_x = 0.0f;
    handle->last_confidence = 0.0f;
    handle->last_selected_center_valid = false;
    handle->last_selected_center_x = 0.0f;
    rehab_person_follow_snapshot_reset_for_start(handle);
    handle->last_zone = REHAB_PERSON_FOLLOW_TARGET_NO_TARGET;
    handle->last_http_status = 0;
    handle->last_jpeg_bytes = 0U;
    handle->last_detection_count = 0U;
    handle->last_dedup_count = 0U;
    handle->last_perception_ms = 0U;
    handle->last_planned_action = REHAB_PERSON_FOLLOW_ACTION_NONE;
    handle->last_action = REHAB_PERSON_FOLLOW_ACTION_NONE;
    handle->last_bbox_width_ratio = 0.0f;
    handle->last_person_center_x_ratio = 0.0f;
    handle->last_left_background_ratio = 0.0f;
    handle->last_right_background_ratio = 0.0f;
    handle->last_post_action_wait_ms = 0U;
    handle->last_car_result = ESP_OK;
    handle->forward_attempted = false;
    handle->motion_unconfirmed = false;
    handle->blocked_unknown = false;
    strlcpy(handle->last_error, "none", sizeof(handle->last_error));
    strlcpy(handle->last_reason, "static_baseline_established",
            sizeof(handle->last_reason));
    handle->worker_task = (TaskHandle_t)1;
    handle->rolling_task_alive = handle->config.action_enabled &&
                                 PERSON_FOLLOW_ROLLING_FORWARD_ENABLED;
    handle->rolling_task = handle->rolling_task_alive ? (TaskHandle_t)1 : NULL;
    person_follow_unlock(handle);

    TaskHandle_t rolling_task = NULL;
    if (handle->config.action_enabled && PERSON_FOLLOW_ROLLING_FORWARD_ENABLED &&
        rehab_person_follow_task_create(
            person_follow_rolling_worker, "person_follow_roll",
            PERSON_FOLLOW_ROLLING_TASK_STACK, handle,
            PERSON_FOLLOW_ROLLING_TASK_PRIORITY, &rolling_task) != pdPASS) {
        if (person_follow_lock(handle)) {
            handle->rolling_task_alive = false;
            handle->rolling_task = NULL;
            person_follow_unlock(handle);
        }
        person_follow_start_rollback(handle, "rolling_task_create_failed");
        return ESP_ERR_NO_MEM;
    }
    if (rolling_task != NULL && person_follow_lock(handle)) {
        if (handle->rolling_task == (TaskHandle_t)1) {
            handle->rolling_task = rolling_task;
        }
        person_follow_unlock(handle);
    }

    TaskHandle_t task = NULL;
    if (rehab_person_follow_task_create(
            person_follow_worker, "person_follow", PERSON_FOLLOW_WORKER_STACK,
            handle, PERSON_FOLLOW_WORKER_PRIORITY, &task) != pdPASS) {
        person_follow_start_rollback(handle, "worker_create_failed");
        return ESP_ERR_NO_MEM;
    }
    if (person_follow_lock(handle)) {
        if (handle->worker_task == (TaskHandle_t)1) {
            handle->worker_task = task;
        }
        person_follow_unlock(handle);
    }
    ESP_LOGI(TAG,
             "started generation=%u provider=%s mock_mode=%s "
             "action_enabled=%u owner=person_follow "
             "startup_observation_grace_ms=%u",
             (unsigned)handle->generation,
             rehab_person_follow_perception_provider_name(
                 handle->config.perception_provider),
             rehab_person_follow_mock_mode_name(handle->config.mock_mode),
              handle->config.action_enabled ? 1U : 0U,
               (unsigned)REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_GRACE_MS);

    (void)rehab_business_audio_notify(
        REHAB_BUSINESS_AUDIO_PERSON_FOLLOW_STARTED);

    return ESP_OK;
}

esp_err_t rehab_person_follow_stop(rehab_person_follow_handle_t handle)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    TaskHandle_t task = NULL;
    TaskHandle_t rolling_task = NULL;
    bool wait_worker = false;
    bool wait_rolling = false;
    if (!person_follow_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    wait_worker = handle->worker_alive;
    wait_rolling = handle->rolling_task_alive && !handle->worker_alive;
    if (handle->running || handle->start_in_progress || handle->worker_alive ||
        handle->owner_acquired) {
        handle->cancel_requested = true;
        handle->generation = person_follow_next_generation(handle->generation);
        handle->startup_observation_active = false;
        handle->startup_observation_exit_reason =
            REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_STOPPED;
        handle->state = REHAB_PERSON_FOLLOW_STATE_STOPPING;
        handle->shutdown_pending = handle->worker_alive;
        task = handle->worker_task;
        handle->forward_rolling = false;
        handle->forward_lease_deadline_us = 0;
        handle->rolling_exit_requested = true;
        strlcpy(handle->rolling_stop_reason, "stop_requested",
                sizeof(handle->rolling_stop_reason));
        rolling_task = handle->rolling_task;
    }
    handle->last_planned_action = REHAB_PERSON_FOLLOW_ACTION_NONE;
    strlcpy(handle->last_reason, "stop_requested",
            sizeof(handle->last_reason));
    person_follow_unlock(handle);

    if (handle->rolling_lease_timer != NULL) {
        (void)xTimerStop(handle->rolling_lease_timer, 0);
    }
    /* Global STOP invalidates the car generation and preempts a timed move. */
    esp_err_t stop_result = rehab_car_stop(handle->car);
    if (task != NULL && task != (TaskHandle_t)1) {
        xTaskNotifyGive(task);
    }
    if (rolling_task != NULL && rolling_task != (TaskHandle_t)1) {
        xTaskNotifyGive(rolling_task);
    }
    if (wait_rolling &&
        xSemaphoreTake(handle->rolling_done_event,
                       pdMS_TO_TICKS(PERSON_FOLLOW_STOP_WAIT_MS)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    if (wait_worker &&
        xSemaphoreTake(handle->worker_done_event,
                       pdMS_TO_TICKS(PERSON_FOLLOW_STOP_WAIT_MS)) != pdTRUE) {
        if (person_follow_lock(handle)) {
            handle->state = REHAB_PERSON_FOLLOW_STATE_STOPPING;
            handle->shutdown_pending = true;
            strlcpy(handle->last_error, "worker_stop_timeout",
                    sizeof(handle->last_error));
            person_follow_unlock(handle);
        }
        return ESP_ERR_TIMEOUT;
    }
    if (person_follow_lock(handle)) {
        handle->state = REHAB_PERSON_FOLLOW_STATE_IDLE;
        handle->running = false;
        handle->start_in_progress = false;
        handle->cancel_requested = false;
        handle->worker_alive = false;
        handle->worker_done = true;
        handle->owner_acquired = false;
        handle->shutdown_pending = false;
        handle->worker_task = NULL;
        handle->forward_rolling = false;
        handle->rolling_task_alive = false;
        handle->rolling_task_busy = false;
        handle->rolling_exit_requested = false;
        handle->rolling_task = NULL;
        handle->forward_lease_deadline_us = 0;
        strlcpy(handle->rolling_stop_reason, "stop_requested",
                sizeof(handle->rolling_stop_reason));
        memset(&handle->car_lease, 0, sizeof(handle->car_lease));
        strlcpy(handle->last_error,
                stop_result == ESP_OK ? "none" : "global_stop_failed",
                sizeof(handle->last_error));
        handle->last_car_result = stop_result;
        strlcpy(handle->last_reason, "stopped_idle",
                sizeof(handle->last_reason));
        person_follow_unlock(handle);
    }
    return stop_result;
}

esp_err_t rehab_person_follow_get_status(
    rehab_person_follow_handle_t handle,
    rehab_person_follow_status_t *out_status)
{
    if (handle == NULL || out_status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!person_follow_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    memset(out_status, 0, sizeof(*out_status));
    out_status->initialized = handle->initialized;
    out_status->running = handle->running;
    out_status->state = handle->state;
    out_status->generation = handle->generation;
    out_status->cancel_requested = handle->cancel_requested;
    out_status->worker_alive = handle->worker_alive;
    out_status->worker_done = handle->worker_done;
    out_status->owner_acquired = handle->owner_acquired;
    out_status->action_enabled = handle->config.action_enabled;
    out_status->perception_provider = handle->config.perception_provider;
    out_status->mock_mode = handle->config.mock_mode;
    out_status->last_http_status = handle->last_http_status;
    out_status->last_jpeg_bytes = handle->last_jpeg_bytes;
    out_status->last_detection_count = handle->last_detection_count;
    out_status->last_dedup_count = handle->last_dedup_count;
    strlcpy(out_status->last_error, handle->last_error,
            sizeof(out_status->last_error));
    strlcpy(out_status->last_reason, handle->last_reason,
            sizeof(out_status->last_reason));
    out_status->last_target_found = handle->last_target_found;
    out_status->last_center_x = handle->last_center_x;
    out_status->last_confidence = handle->last_confidence;
    out_status->last_zone = handle->last_zone;
    out_status->last_perception_ms = handle->last_perception_ms;
    out_status->last_planned_action = handle->last_planned_action;
    out_status->last_action = handle->last_action;
    out_status->last_bbox_width_ratio = handle->last_bbox_width_ratio;
    out_status->last_person_center_x_ratio =
        handle->last_person_center_x_ratio;
    out_status->last_left_background_ratio =
        handle->last_left_background_ratio;
    out_status->last_right_background_ratio =
        handle->last_right_background_ratio;
    out_status->approach_phase =
        handle->decision_context.approach_phase;
    out_status->first_align_completed =
        handle->decision_context.first_align_completed;
    out_status->active_align_gate =
        handle->decision_context.active_align_gate;
    out_status->align_stable_count =
        handle->decision_context.align_stable_count;
    out_status->align_stable_required =
        REHAB_PERSON_FOLLOW_ALIGN_STABLE_FRAMES_REQUIRED;
    out_status->first_align_trigger_frame_id =
        handle->decision_context.first_align_trigger_frame_id;
    out_status->final_align_trigger_frame_id =
        handle->decision_context.final_align_trigger_frame_id;
    out_status->align_turn_hold_ms =
        handle->decision_context.turn_hold_ms;
    out_status->align_last_turn_direction =
        handle->decision_context.last_successful_turn_direction;
    out_status->align_overshoot_count =
        handle->decision_context.align_overshoot_count;
    out_status->turn_hold_ms = handle->decision_context.turn_hold_ms;
    out_status->target_acquired_once =
        handle->decision_context.target_acquired_once;
    out_status->turn_check_pending =
        handle->decision_context.turn_check_pending;
    out_status->last_successful_turn_direction =
        handle->decision_context.last_successful_turn_direction;
    out_status->final_forward_attempted_frame_id =
        handle->decision_context.final_forward_attempted_frame_id;
    out_status->arrived = handle->decision_context.approach_phase ==
        REHAB_PERSON_FOLLOW_APPROACH_ARRIVED;
    out_status->arrival_reason =
        handle->decision_context.arrival_reason;
    out_status->post_action_wait_ms = handle->last_post_action_wait_ms;
    out_status->last_car_result = handle->last_car_result;
    out_status->forward_attempted = handle->forward_attempted;
    out_status->motion_unconfirmed = handle->motion_unconfirmed;
    out_status->blocked_unknown = handle->blocked_unknown;
    out_status->shutdown_pending = handle->shutdown_pending;
    out_status->has_forwarded_once =
        handle->decision_context.has_forwarded_once;
    out_status->fine_turn_mode = handle->decision_context.fine_turn_mode;
    out_status->pending_target_turn_check =
        handle->decision_context.turn_check_pending;
    out_status->last_target_turn_action =
        handle->decision_context.last_successful_turn_direction;
    out_status->next_turn_hold_ms =
        handle->decision_context.turn_hold_ms;
    int64_t now_us = esp_timer_get_time();
    out_status->forward_rolling = handle->forward_rolling;
    out_status->forward_lease_remaining_ms = person_follow_remaining_ms(
        handle->forward_lease_deadline_us, now_us);
    out_status->forward_segments_completed =
        handle->forward_segments_completed;
    out_status->last_center_confirmed_age_ms = person_follow_age_ms(
        handle->last_center_confirmed_us, now_us);
    strlcpy(out_status->rolling_stop_reason, handle->rolling_stop_reason,
            sizeof(out_status->rolling_stop_reason));
    out_status->rolling_task_alive = handle->rolling_task_alive;
    out_status->rolling_task_busy = handle->rolling_task_busy;
    out_status->startup_observation_active =
        handle->startup_observation_active;
    out_status->startup_observation_remaining_ms =
        handle->startup_observation_active
            ? person_follow_remaining_ms(
                handle->startup_observation_started_us +
                    (int64_t)REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_GRACE_MS *
                        1000,
                now_us)
            : 0U;
    out_status->startup_observation_exit_reason =
        handle->startup_observation_exit_reason;
    out_status->runtime_ms = handle->runtime_ms;
    out_status->snapshot = handle->snapshot_info;
    person_follow_unlock(handle);
    return ESP_OK;
}

esp_err_t rehab_person_follow_delete(rehab_person_follow_handle_t handle)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!person_follow_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    bool allowed = handle->initialized &&
                   handle->state == REHAB_PERSON_FOLLOW_STATE_IDLE &&
                   !handle->running && !handle->start_in_progress &&
                   !handle->worker_alive && !handle->owner_acquired &&
                   handle->worker_task == NULL && !handle->rolling_task_alive &&
                   !handle->rolling_task_busy && handle->rolling_task == NULL &&
                   rehab_person_follow_snapshot_leases_idle(handle);
    person_follow_unlock(handle);
    if (!allowed) {
        return ESP_ERR_INVALID_STATE;
    }
    (void)xTimerDelete(handle->rolling_lease_timer, 0);
    vSemaphoreDelete(handle->rolling_done_event);
    vSemaphoreDelete(handle->worker_done_event);
    vSemaphoreDelete(handle->lock);
    for (size_t i = 0; i < PERSON_FOLLOW_SNAPSHOT_SLOTS; ++i) {
        heap_caps_free(handle->snapshot_slots[i].data);
    }
    heap_caps_free(handle->workspace);
    heap_caps_free(handle->response_buffer);
    free(handle);
    return ESP_OK;
}
