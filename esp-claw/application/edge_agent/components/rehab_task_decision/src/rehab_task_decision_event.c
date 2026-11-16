// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_priv.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "claw_event_publisher.h"
#include "esp_log.h"
#include "esp_timer.h"

#define DECISION_EVENT_MESSAGE_MAX 896U
#define DECISION_WATCHDOG_STACK 4096U
#define DECISION_WATCHDOG_PRIORITY 3U
#define DECISION_WATCHDOG_SLICE_MS 250U
#define DECISION_WATCHDOG_TIMEOUT_MS 120000U
#define DECISION_WATCHDOG_CANCEL_MS 2000U

static const char *TAG = "rehab_decision_evt";
static const char s_failure_message[] =
    "本次任务决策未完成，请重新发送“请帮我决定一个任务”并重新扫描。";

typedef struct {
    rehab_task_decision_handle_t handle;
    uint32_t generation;
} decision_watchdog_ctx_t;

static uint64_t decision_now_ms(void)
{
    return (uint64_t)(esp_timer_get_time() / 1000);
}

static bool event_lock(rehab_task_decision_handle_t handle)
{
    return xSemaphoreTake(handle->lock, pdMS_TO_TICKS(1000)) == pdTRUE;
}

static void event_unlock(rehab_task_decision_handle_t handle)
{
    xSemaphoreGive(handle->lock);
}

static bool decision_pending_state(rehab_task_decision_state_t state)
{
    return state == REHAB_TASK_DECISION_STATE_DECIDING ||
           state == REHAB_TASK_DECISION_STATE_VALIDATING ||
           state == REHAB_TASK_DECISION_STATE_COMMITTING;
}

static void decision_watchdog_task(void *arg)
{
    decision_watchdog_ctx_t *ctx = arg;
    rehab_task_decision_handle_t handle = ctx->handle;
    uint32_t generation = ctx->generation;
    free(ctx);
    bool timed_out = false;
    char channel[sizeof(handle->notification_channel)] = {0};
    char chat_id[sizeof(handle->notification_chat_id)] = {0};

    while (true) {
        if (!event_lock(handle)) {
            if (ulTaskNotifyTake(
                    pdTRUE,
                    pdMS_TO_TICKS(DECISION_WATCHDOG_SLICE_MS)) > 0) {
                break;
            }
            continue;
        }
        bool active = !handle->deleting &&
            handle->decision_watchdog_generation == generation &&
            handle->status.generation == generation &&
            decision_pending_state(handle->status.state);
        uint64_t deadline = handle->status.decision_started_at_ms +
                            handle->status.decision_deadline_ms;
        if (active && decision_now_ms() >= deadline) {
            timed_out = true;
            strlcpy(channel, handle->notification_channel,
                    sizeof(channel));
            strlcpy(chat_id, handle->notification_chat_id,
                    sizeof(chat_id));
            handle->status.state = REHAB_TASK_DECISION_STATE_ERROR;
            strlcpy(handle->status.last_error,
                    "decision_watchdog_timeout",
                    sizeof(handle->status.last_error));
        }
        event_unlock(handle);
        if (!active || timed_out) {
            break;
        }
        if (ulTaskNotifyTake(pdTRUE,
                             pdMS_TO_TICKS(DECISION_WATCHDOG_SLICE_MS)) > 0) {
            break;
        }
    }

    if (timed_out) {
        ESP_LOGE(TAG,
                 "DECISION_WATCHDOG_TIMEOUT generation=%" PRIu32,
                 generation);
        (void)rehab_task_decision_fail_to_idle(handle, generation,
                                               "decision_watchdog_timeout");
        if (channel[0] != '\0' && chat_id[0] != '\0') {
            esp_err_t notify_err = rehab_task_decision_send_direct_message(
                channel, chat_id, s_failure_message);
            ESP_LOGI(TAG,
                     "DECISION_FAILURE_NOTIFY generation=%" PRIu32
                     " result=%s",
                     generation, esp_err_to_name(notify_err));
        }
    }

    if (event_lock(handle)) {
        handle->decision_watchdog_task = NULL;
        handle->decision_watchdog_generation = 0;
        event_unlock(handle);
    }
    xSemaphoreGive(handle->decision_watchdog_done);
    vTaskDelete(NULL);
}

esp_err_t rehab_task_decision_watchdog_start(
    rehab_task_decision_handle_t handle,
    uint32_t generation)
{
    if (handle == NULL || generation == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!event_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (handle->decision_watchdog_task != NULL) {
        bool same = handle->decision_watchdog_generation == generation;
        event_unlock(handle);
        return same ? ESP_OK : ESP_ERR_INVALID_STATE;
    }
    (void)xSemaphoreTake(handle->decision_watchdog_done, 0);
    decision_watchdog_ctx_t *ctx = calloc(1, sizeof(*ctx));
    if (ctx == NULL) {
        event_unlock(handle);
        return ESP_ERR_NO_MEM;
    }
    ctx->handle = handle;
    ctx->generation = generation;
    handle->decision_watchdog_task = (TaskHandle_t)1;
    handle->decision_watchdog_generation = generation;
    event_unlock(handle);

    TaskHandle_t task = NULL;
    if (xTaskCreate(decision_watchdog_task, "decision_watchdog",
                    DECISION_WATCHDOG_STACK, ctx,
                    DECISION_WATCHDOG_PRIORITY, &task) != pdPASS) {
        free(ctx);
        if (event_lock(handle)) {
            handle->decision_watchdog_task = NULL;
            handle->decision_watchdog_generation = 0;
            event_unlock(handle);
        }
        return ESP_ERR_NO_MEM;
    }
    if (event_lock(handle)) {
        if (handle->decision_watchdog_task == (TaskHandle_t)1) {
            handle->decision_watchdog_task = task;
        }
        event_unlock(handle);
    }
    return ESP_OK;
}

esp_err_t rehab_task_decision_watchdog_cancel(
    rehab_task_decision_handle_t handle,
    uint32_t timeout_ms)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!event_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    TaskHandle_t task = handle->decision_watchdog_task;
    event_unlock(handle);
    if (task == NULL) {
        return ESP_OK;
    }
    if (task == (TaskHandle_t)1) {
        vTaskDelay(pdMS_TO_TICKS(1));
        if (!event_lock(handle)) {
            return ESP_ERR_TIMEOUT;
        }
        task = handle->decision_watchdog_task;
        event_unlock(handle);
    }
    if (task != NULL && task != (TaskHandle_t)1) {
        xTaskNotifyGive(task);
    }
    uint32_t wait_ms = timeout_ms > 0 ? timeout_ms :
                       DECISION_WATCHDOG_CANCEL_MS;
    return xSemaphoreTake(handle->decision_watchdog_done,
                          pdMS_TO_TICKS(wait_ms)) == pdTRUE
        ? ESP_OK : ESP_ERR_TIMEOUT;
}

static esp_err_t decision_enter_pending(rehab_task_decision_handle_t handle,
                                        uint32_t generation,
                                        bool start_watchdog)
{
    if (!event_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    bool current = handle->status.generation == generation &&
        (handle->status.state == REHAB_TASK_DECISION_STATE_COMPLETED ||
         handle->status.state == REHAB_TASK_DECISION_STATE_DECIDING) &&
        handle->status.final_state_frozen &&
        !handle->status.worker_active &&
        !handle->status.perception_in_flight;
    if (!current) {
        event_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    handle->status.state = REHAB_TASK_DECISION_STATE_DECIDING;
    if (handle->status.decision_started_at_ms == 0) {
        handle->status.decision_started_at_ms = decision_now_ms();
    }
    handle->status.decision_deadline_ms = DECISION_WATCHDOG_TIMEOUT_MS;
    event_unlock(handle);
    if (!start_watchdog) {
        return ESP_OK;
    }
    esp_err_t err = rehab_task_decision_watchdog_start(handle, generation);
    if (err != ESP_OK) {
        (void)rehab_task_decision_fail_to_idle(handle, generation,
                                               "decision_watchdog_start_failed");
    }
    return err;
}

esp_err_t rehab_task_decision_context_record(
    rehab_task_decision_handle_t handle,
    uint32_t generation,
    uint32_t request_id)
{
    if (handle == NULL || generation == 0 || request_id == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = decision_enter_pending(handle, generation, true);
    if (err != ESP_OK) {
        return err;
    }
    if (!event_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (handle->status.generation != generation ||
        handle->status.state != REHAB_TASK_DECISION_STATE_DECIDING ||
        handle->decision_context_generation != 0 ||
        handle->decision_context_request_id != 0 ||
        handle->decision_rules_generation != 0 ||
        handle->decision_rules_request_id != 0) {
        event_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    handle->decision_context_generation = generation;
    handle->decision_context_request_id = request_id;
    event_unlock(handle);
    return ESP_OK;
}

static void event_record_result(rehab_task_decision_handle_t handle,
                                uint32_t generation,
                                esp_err_t result)
{
    if (!event_lock(handle)) {
        return;
    }
    if (handle->status.generation == generation) {
        handle->status.decision_trigger_published = result == ESP_OK;
        strlcpy(handle->status.decision_trigger_error,
                result == ESP_OK ? "none" : esp_err_to_name(result),
                sizeof(handle->status.decision_trigger_error));
    }
    event_unlock(handle);
}

esp_err_t rehab_task_decision_publish_event(
    rehab_task_decision_handle_t handle,
    uint32_t generation,
    const char *channel,
    const char *chat_id)
{
    if (handle == NULL || channel == NULL || channel[0] == '\0' ||
        chat_id == NULL || chat_id[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    if (!event_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    bool current = handle->status.generation == generation &&
        handle->status.state == REHAB_TASK_DECISION_STATE_COMPLETED &&
        handle->status.final_state_frozen &&
        !handle->status.worker_active &&
        !handle->status.perception_in_flight;
    if (!current) {
        uint32_t actual_generation = handle->status.generation;
        event_unlock(handle);
        ESP_LOGW(TAG,
                 "DECISION_STALE generation=%" PRIu32
                 " current_generation=%" PRIu32,
                 generation, actual_generation);
        return ESP_ERR_INVALID_STATE;
    }
    if (handle->decision_trigger_attempt_generation == generation) {
        event_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    handle->decision_trigger_attempt_generation = generation;
    handle->status.decision_trigger_attempted = true;
    strlcpy(handle->status.decision_trigger_error, "none",
            sizeof(handle->status.decision_trigger_error));
    event_unlock(handle);

    esp_err_t result = decision_enter_pending(handle, generation, false);
    if (result != ESP_OK) {
        event_record_result(handle, generation, result);
        (void)rehab_task_decision_send_direct_message(channel, chat_id,
                                                       s_failure_message);
        return result;
    }
    ESP_LOGI(TAG,
             "DECISION_TRIGGER generation=%" PRIu32 " channel=%s",
             generation, channel);
    char *message = calloc(1, DECISION_EVENT_MESSAGE_MAX);
    if (message == NULL) {
        result = ESP_ERR_NO_MEM;
    } else {
        int written = snprintf(
            message, DECISION_EVENT_MESSAGE_MAX,
            "[内部事件 decision_required] 环境摘要已发送，当前冻结 scan_generation=%" PRIu32
            "。请激活 rehab-task-decision Skill，调用 "
            "rehab_task_decision_formal_input_get({\"source\":\"wechat\"}) 一次，"
            "依据完整 frozen v4 package 完成五任务 DeepSeek 业务决策，再调用 "
            "rehab_task_decision_commit。仅当 retry_allowed=true 时可修正 commit 参数一次，"
            "不得重新读取 formal input、读取旧 context/rules、自行写 selected、再次扫描"
            "或启动训练。",
            generation);
        if (written < 0 || written >= (int)DECISION_EVENT_MESSAGE_MAX) {
            result = ESP_ERR_INVALID_SIZE;
        } else {
            char message_id[48] = {0};
            snprintf(message_id, sizeof(message_id), "decision-%08" PRIx32,
                     generation);
            result = claw_event_router_publish_message(
                "rehab_task_decision", channel, chat_id, message,
                "rehab_task_decision", message_id);
        }
    }
    free(message);
    if (result == ESP_OK) {
        result = rehab_task_decision_watchdog_start(handle, generation);
        if (result != ESP_OK) {
            (void)rehab_task_decision_fail_to_idle(
                handle, generation, "decision_watchdog_start_failed");
        }
    }
    event_record_result(handle, generation, result);
    if (result != ESP_OK) {
        (void)rehab_task_decision_fail_to_idle(handle, generation,
                                               "decision_event_publish_failed");
        (void)rehab_task_decision_send_direct_message(channel, chat_id,
                                                       s_failure_message);
    }
    ESP_LOGI(TAG,
             "DECISION_NOTIFY generation=%" PRIu32
             " channel=%s result=%s",
             generation, channel, esp_err_to_name(result));
    return result;
}
