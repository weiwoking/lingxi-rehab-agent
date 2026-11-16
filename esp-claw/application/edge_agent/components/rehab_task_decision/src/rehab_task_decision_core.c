// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_priv.h"

#include "esp_console.h"

#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "cJSON.h"
#include "claw_cap.h"
#include "rehab_capture.h"
#include "rehab_tof.h"

#define SCAN_WORKER_STACK             8192U
#define SCAN_WORKER_PRIORITY             4U
#define SCAN_ROTATE_SPEED_LEVEL           1U
#define SCAN_ROTATE_MS                  1000U
#define SCAN_SETTLE_MS                   200U
#define SCAN_FUSION_TIMEOUT_MS          8000U
#define SCAN_MIN_FUSION_WINDOW_MS       1000U
#define SCAN_MIN_ROUND_BUDGET_MS \
    (SCAN_ROTATE_MS + SCAN_SETTLE_MS + SCAN_MIN_FUSION_WINDOW_MS)
#define SCAN_MAX_STEPS REHAB_TASK_DECISION_SCAN_OBSERVATION_COUNT
#define SCAN_WAIT_SLICE_MS                25U
#define SCAN_DELETE_WAIT_MS            10000U
#define SCAN_NOTIFICATION_MESSAGE_MAX   2048U
#define SCAN_NOTIFICATION_OUTPUT_MAX     256U

static const char *TAG = "rehab_task_decision";

static void scan_finish_planned_observations_locked(
    rehab_task_decision_handle_t handle);

static uint64_t scan_now_ms(void)
{
    return (uint64_t)(esp_timer_get_time() / 1000);
}

static bool scan_lock(rehab_task_decision_handle_t handle)
{
    return xSemaphoreTake(handle->lock, pdMS_TO_TICKS(1000)) == pdTRUE;
}

static void scan_unlock(rehab_task_decision_handle_t handle)
{
    xSemaphoreGive(handle->lock);
}

static bool scan_car_lock(rehab_task_decision_handle_t handle)
{
    return xSemaphoreTake(handle->car_operation_lock,
                          pdMS_TO_TICKS(1000)) == pdTRUE;
}

static void scan_car_unlock(rehab_task_decision_handle_t handle)
{
    xSemaphoreGive(handle->car_operation_lock);
}

static void scan_update_elapsed_locked(rehab_task_decision_handle_t handle,
                                       uint64_t now_ms)
{
    if (handle->status.started_at_ms == 0 ||
        now_ms <= handle->status.started_at_ms) {
        handle->status.elapsed_ms = 0;
        return;
    }
    uint64_t elapsed = now_ms - handle->status.started_at_ms;
    handle->status.elapsed_ms = elapsed > UINT32_MAX
        ? UINT32_MAX : (uint32_t)elapsed;
}

static bool scan_is_current_locked(rehab_task_decision_handle_t handle,
                                   uint32_t generation)
{
    return handle->status.worker_active &&
           !handle->cancel_requested &&
           handle->run_generation == generation;
}

static void scan_error_locked(rehab_task_decision_handle_t handle,
                              const char *code)
{
    handle->status.state = REHAB_TASK_DECISION_STATE_ERROR;
    strlcpy(handle->status.last_error, code,
            sizeof(handle->status.last_error));
}

static void scan_set_start_rejection(rehab_task_decision_handle_t handle,
                                     const char *code)
{
    if (scan_lock(handle)) {
        if (!handle->status.worker_active &&
            handle->status.state !=
                REHAB_TASK_DECISION_STATE_READY_SELECTED) {
            handle->status.state = REHAB_TASK_DECISION_STATE_ERROR;
        }
        strlcpy(handle->status.last_error, code,
                sizeof(handle->status.last_error));
        scan_unlock(handle);
    }
}

static void scan_preempted(void *ctx)
{
    rehab_task_decision_handle_t handle = ctx;
    if (handle == NULL || !scan_lock(handle)) {
        return;
    }
    if (handle->status.worker_active) {
        handle->cancel_requested = true;
        ++handle->run_generation;
        if (handle->run_generation == 0) {
            ++handle->run_generation;
        }
        handle->status.cancel_requested = true;
        handle->status.state = REHAB_TASK_DECISION_STATE_CANCELLED;
        strlcpy(handle->status.last_error, "cancelled",
                sizeof(handle->status.last_error));
    }
    TaskHandle_t worker = handle->worker_task;
    scan_unlock(handle);
    if (worker != NULL && worker != (TaskHandle_t)1) {
        xTaskNotifyGive(worker);
    }
}

static bool scan_wait_cancelable(rehab_task_decision_handle_t handle,
                                 uint32_t generation,
                                 uint32_t wait_ms)
{
    uint64_t until_ms = scan_now_ms() + wait_ms;
    while (scan_now_ms() < until_ms) {
        uint64_t now_ms = scan_now_ms();
        if (!scan_lock(handle)) {
            return false;
        }
        scan_update_elapsed_locked(handle, now_ms);
        bool current = scan_is_current_locked(handle, generation);
        bool before_deadline =
            handle->status.elapsed_ms < handle->status.deadline_ms;
        scan_unlock(handle);
        if (!current || !before_deadline) {
            return false;
        }
        uint64_t remaining = until_ms - now_ms;
        uint32_t slice = remaining > SCAN_WAIT_SLICE_MS
            ? SCAN_WAIT_SLICE_MS : (uint32_t)remaining;
        (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(slice));
    }
    return true;
}

typedef struct {
    rehab_task_decision_handle_t handle;
    uint32_t generation;
} scan_capture_wait_ctx_t;

static bool scan_capture_wait_slice(void *ctx, uint32_t wait_ms)
{
    scan_capture_wait_ctx_t *wait_ctx = ctx;
    return wait_ctx != NULL &&
        scan_wait_cancelable(wait_ctx->handle, wait_ctx->generation, wait_ms);
}

static esp_err_t scan_owned_stop(rehab_task_decision_handle_t handle)
{
    if (!scan_car_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    esp_err_t result = rehab_car_control_stop(handle->car,
                                               &handle->car_lease);
    scan_car_unlock(handle);
    return result;
}

static esp_err_t scan_stop_and_release(rehab_task_decision_handle_t handle)
{
    if (!scan_car_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    esp_err_t stop_result = rehab_car_control_stop(handle->car,
                                                   &handle->car_lease);
    if (stop_result == ESP_ERR_INVALID_STATE) {
        if (handle->car_lease.token != 0) {
            esp_err_t fallback_stop_result = rehab_car_stop(handle->car);
            scan_car_unlock(handle);
            return fallback_stop_result == ESP_OK
                ? ESP_ERR_INVALID_STATE : fallback_stop_result;
        }
        scan_car_unlock(handle);
        return ESP_OK;
    }
    if (stop_result != ESP_OK) {
        stop_result = rehab_car_stop(handle->car);
        scan_car_unlock(handle);
        return stop_result;
    }
    esp_err_t release_result = rehab_car_control_release(handle->car,
                                                         &handle->car_lease);
    if (release_result != ESP_OK && release_result != ESP_ERR_INVALID_STATE) {
        esp_err_t fallback_stop_result = rehab_car_stop(handle->car);
        stop_result = fallback_stop_result == ESP_OK
            ? release_result : fallback_stop_result;
    }
    scan_car_unlock(handle);
    return stop_result;
}

static esp_err_t scan_execute_rotation(rehab_task_decision_handle_t handle,
                                       uint32_t generation)
{
    if (!scan_car_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (!scan_lock(handle)) {
        scan_car_unlock(handle);
        return ESP_ERR_TIMEOUT;
    }
    bool current = scan_is_current_locked(handle, generation);
    scan_unlock(handle);
    if (!current) {
        scan_car_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t result = rehab_car_control_move(
        handle->car, &handle->car_lease,
        REHAB_CAR_DIRECTION_ROTATE_CCW,
        SCAN_ROTATE_SPEED_LEVEL, SCAN_ROTATE_MS);
    scan_car_unlock(handle);
    return result;
}

static bool scan_capture_pipeline_failure(
    const rehab_cloud_fusion_result_t *result)
{
    if (result == NULL) {
        return false;
    }
    const char *code = result->last_error;
    return strcmp(code, "capture_failed") == 0 ||
           strcmp(code, "capture_busy") == 0 ||
           strcmp(code, "packet_busy") == 0 ||
           strcmp(code, "pair_not_ready") == 0;
}

static const char *scan_fusion_error_code(
    esp_err_t err,
    const rehab_cloud_fusion_result_t *result)
{
    if (scan_capture_pipeline_failure(result)) {
        return "capture_failed";
    }
    if (err == ESP_ERR_TIMEOUT) {
        return "fusion_timeout";
    }
    return "fusion_invalid";
}

static const char *scan_fusion_error_stage(
    esp_err_t err,
    const rehab_cloud_fusion_result_t *result)
{
    const char *code = result != NULL ? result->last_error : NULL;
    if (scan_capture_pipeline_failure(result)) {
        return "local_capture";
    }
    if (err == ESP_ERR_TIMEOUT ||
        (code != NULL && strstr(code, "timeout") != NULL)) {
        return "timeout";
    }
    if (code != NULL &&
        (strstr(code, "json") != NULL ||
         strstr(code, "parse") != NULL ||
         strstr(code, "schema") != NULL ||
         strstr(code, "contract") != NULL ||
         strcmp(code, "capture_id_mismatch") == 0)) {
        return "pc_parse";
    }
    if (code != NULL &&
        (strstr(code, "http") != NULL ||
         strstr(code, "response") != NULL ||
         strstr(code, "wifi") != NULL ||
         strstr(code, "time_not_synchronized") != NULL)) {
        return "pc_upload";
    }
    return "fusion_result";
}

static const char *scan_fusion_error_detail(
    esp_err_t err,
    const rehab_cloud_fusion_result_t *result)
{
    if (result != NULL && result->last_error[0] != '\0' &&
        strcmp(result->last_error, "none") != 0 &&
        strcmp(result->last_error, "unknown") != 0) {
        return result->last_error;
    }
    if (err != ESP_OK) {
        return esp_err_to_name(err);
    }
    return "fusion_invalid";
}

static bool scan_fusion_result_valid(
    esp_err_t err,
    const rehab_cloud_fusion_result_t *result)
{
    return err == ESP_OK && result != NULL && result->ok &&
           result->transport_ok && result->response_json_valid &&
           result->capture_id_match && result->cloud_valid;
}

static bool scan_wait_capture_quiescence(
    rehab_task_decision_handle_t handle,
    uint32_t generation,
    uint32_t after_step)
{
    if (!scan_lock(handle)) {
        return false;
    }
    scan_update_elapsed_locked(handle, scan_now_ms());
    if (!scan_is_current_locked(handle, generation)) {
        scan_unlock(handle);
        return false;
    }
    uint32_t remaining_ms = handle->status.deadline_ms >
            handle->status.elapsed_ms
        ? handle->status.deadline_ms - handle->status.elapsed_ms : 0;
    uint32_t timeout_ms = remaining_ms < REHAB_CAPTURE_QUIESCENCE_TIMEOUT_MS
        ? remaining_ms : REHAB_CAPTURE_QUIESCENCE_TIMEOUT_MS;
    scan_unlock(handle);

    ESP_LOGI(TAG,
             "SCAN_CAPTURE_QUIESCENCE_BEGIN generation=%" PRIu32
             " after_step=%" PRIu32 " timeout_ms=%" PRIu32,
             generation, after_step, timeout_ms);

    uint32_t waited_ms = 0;
    rehab_capture_status_t capture_status = {0};
    scan_capture_wait_ctx_t wait_ctx = {
        .handle = handle,
        .generation = generation,
    };
    esp_err_t wait_result = timeout_ms == 0 ? ESP_ERR_TIMEOUT :
        rehab_capture_wait_for_quiescence(
            timeout_ms,
            REHAB_CAPTURE_QUIESCENCE_POLL_MS,
            REHAB_CAPTURE_QUIESCENCE_SETTLE_MS,
            scan_capture_wait_slice, &wait_ctx, &waited_ms, &capture_status);

    if (!scan_lock(handle)) {
        return false;
    }
    scan_update_elapsed_locked(handle, scan_now_ms());
    bool current = scan_is_current_locked(handle, generation);
    bool before_deadline =
        handle->status.elapsed_ms < handle->status.deadline_ms;
    if (!current) {
        scan_unlock(handle);
        return false;
    }
    if (wait_result == ESP_OK && before_deadline) {
        scan_unlock(handle);
        ESP_LOGI(TAG,
                 "SCAN_CAPTURE_QUIESCENCE_OK generation=%" PRIu32
                 " after_step=%" PRIu32 " wait_ms=%" PRIu32,
                 generation, after_step, waited_ms);
        return true;
    }
    scan_finish_planned_observations_locked(handle);
    scan_unlock(handle);
    esp_err_t log_result = before_deadline ? wait_result : ESP_ERR_TIMEOUT;
    ESP_LOGE(TAG,
             "SCAN_CAPTURE_QUIESCENCE_TIMEOUT generation=%" PRIu32
             " after_step=%" PRIu32 " wait_ms=%" PRIu32 " err=%s",
             generation, after_step, waited_ms, esp_err_to_name(log_result));
    return false;
}

static void scan_finish_deadline_locked(rehab_task_decision_handle_t handle)
{
    handle->status.deadline_exceeded = true;
    if (handle->status.valid_observation_count > 0) {
        handle->status.state = REHAB_TASK_DECISION_STATE_COMPLETED;
        strlcpy(handle->status.last_error, "none",
                sizeof(handle->status.last_error));
    } else {
        handle->status.state = REHAB_TASK_DECISION_STATE_DEADLINE_EXCEEDED;
        strlcpy(handle->status.last_error, "deadline_exceeded",
                sizeof(handle->status.last_error));
    }
}

static const char *scan_notification_cap(const char *channel)
{
    if (channel != NULL && strcmp(channel, "wechat") == 0) {
        return "wechat_send_message";
    }
    if (channel != NULL && strcmp(channel, "web") == 0) {
        return "local_send_message";
    }
    return NULL;
}

static bool scan_message_append(char *message, size_t message_size,
                                size_t *offset, const char *format, ...)
{
    if (message == NULL || offset == NULL || *offset >= message_size) {
        return false;
    }
    va_list args;
    va_start(args, format);
    int written = vsnprintf(message + *offset, message_size - *offset,
                            format, args);
    va_end(args);
    if (written < 0 || (size_t)written >= message_size - *offset) {
        message[message_size - 1U] = '\0';
        return false;
    }
    *offset += (size_t)written;
    return true;
}

static const char *scan_error_description(const char *code)
{
    if (strcmp(code, "capture_failed") == 0) {
        return "Camera/ToF 采集或配对失败";
    }
    if (strcmp(code, "fusion_timeout") == 0) {
        return "Camera+ToF+PC 融合超时";
    }
    if (strcmp(code, "fusion_invalid") == 0) {
        return "融合结果无效";
    }
    if (strcmp(code, "movement_failed") == 0) {
        return "车辆旋转动作失败";
    }
    if (strcmp(code, "stop_failed") == 0) {
        return "车辆安全停止失败";
    }
    return "扫描内部错误";
}

static const char *scan_error_stage_description(const char *stage)
{
    if (strcmp(stage, "local_capture") == 0) {
        return "本地采集";
    }
    if (strcmp(stage, "pc_upload") == 0) {
        return "PC上传";
    }
    if (strcmp(stage, "pc_parse") == 0) {
        return "PC解析";
    }
    if (strcmp(stage, "timeout") == 0) {
        return "超时";
    }
    if (strcmp(stage, "environment_result") == 0) {
        return "环境结果";
    }
    return "融合结果";
}

static const char *scan_primary_zone(
    const rehab_environment_class_summary_t *item)
{
    uint32_t highest = item->left_count;
    const char *zone = item->left_count > 0 ? "左侧" : NULL;
    if (item->center_count > highest) {
        highest = item->center_count;
        zone = "中央";
    }
    if (item->right_count > highest) {
        zone = "右侧";
    }
    return zone;
}

static bool scan_build_notification_message(
    const rehab_task_decision_status_t *status,
    char *message, size_t message_size)
{
    size_t offset = 0;
    if (status->state == REHAB_TASK_DECISION_STATE_CANCELLED) {
        return scan_message_append(message, message_size, &offset,
                                   "扫描已取消，小车已停止。\n任务：%s",
                                   status->job_id);
    }
    if (status->state == REHAB_TASK_DECISION_STATE_COMPLETED) {
        if (!scan_message_append(message, message_size, &offset,
                                 "扫描已完成，共计划 %u 次观察：\n"
                                 "- 检测到物体：%u 次\n"
                                 "- 未检测到目标：%u 次\n"
                                 "- 采集/通信失败：%u 次\n",
                                 (unsigned)status->planned_observation_count,
                                 (unsigned)status->object_observation_count,
                                 (unsigned)status->empty_observation_count,
                                 (unsigned)status->technical_failure_count)) {
            return false;
        }
        if (status->class_count > 0 &&
            !scan_message_append(message, message_size, &offset,
                                 "\n检测到：\n")) {
            return false;
        }
        for (size_t i = 0; i < status->class_count; ++i) {
            const rehab_environment_class_summary_t *item =
                &status->classes[i];
            const char *zone = scan_primary_zone(item);
            if (!scan_message_append(message, message_size, &offset,
                                     "- %s：在 %u 次观察中出现",
                                     item->class_name,
                                     (unsigned)item->seen_count)) {
                return false;
            }
            if (item->confidence_count > 0 &&
                !scan_message_append(message, message_size, &offset,
                                     "，最高置信度 %.2f",
                                     (double)item->max_confidence)) {
                return false;
            }
            if (zone != NULL &&
                !scan_message_append(message, message_size, &offset,
                                     "，主要位于%s", zone)) {
                return false;
            }
            if (item->valid_distance_count > 0 &&
                !scan_message_append(message, message_size, &offset,
                                     "，最近有效距离约 %.0f cm",
                                     (double)item->min_distance_cm)) {
                return false;
            }
            if (!scan_message_append(message, message_size, &offset, "\n")) {
                return false;
            }
        }
        if (status->technical_failure_count > 0 &&
            !scan_message_append(
                message, message_size, &offset,
                "\n最后一次失败：第 %u 轮，阶段=%s，错误=%s。\n",
                (unsigned)status->last_rejection_step,
                scan_error_stage_description(status->last_rejection_stage),
                status->last_rejection_error)) {
            return false;
        }
        return scan_message_append(
            message, message_size, &offset,
            "\n说明：出现次数表示多轮观察中出现该类别的轮次数，不代表唯一物体数量。\n"
            "环境摘要已冻结，系统将继续生成任务建议；训练尚未开始。");
    }
    if (status->state == REHAB_TASK_DECISION_STATE_DEADLINE_EXCEEDED) {
        return scan_message_append(
            message, message_size, &offset,
            "扫描已结束，但没有获得有效环境观察。\n\n"
            "计划观察：%u 次\n检测到物体：%u 次\n"
            "未检测到目标：%u 次\n采集/通信失败：%u 次\n"
            "最后错误阶段：%s\n最后错误：%s\n"
            "原因：达到扫描截止时间（deadline_exceeded）。",
            (unsigned)status->planned_observation_count,
            (unsigned)status->object_observation_count,
            (unsigned)status->empty_observation_count,
            (unsigned)status->technical_failure_count,
            status->last_rejection_stage[0]
                ? scan_error_stage_description(status->last_rejection_stage)
                : "超时",
            status->last_rejection_error[0]
                ? status->last_rejection_error : status->last_error);
    }
    if (status->state == REHAB_TASK_DECISION_STATE_NO_VALID_OBSERVATION) {
        return scan_message_append(
            message, message_size, &offset,
            "扫描已结束，但没有获得有效环境观察。\n\n"
            "计划观察：%u 次\n检测到物体：%u 次\n"
            "未检测到目标：%u 次\n采集/通信失败：%u 次\n"
            "最后错误阶段：%s\n最后错误：%s",
            (unsigned)status->planned_observation_count,
            (unsigned)status->object_observation_count,
            (unsigned)status->empty_observation_count,
            (unsigned)status->technical_failure_count,
            status->last_rejection_stage[0]
                ? scan_error_stage_description(status->last_rejection_stage)
                : "环境结果",
            status->last_rejection_error[0]
                ? status->last_rejection_error : status->last_error);
    }
    return scan_message_append(
        message, message_size, &offset,
        "扫描已结束，但扫描失败。\n任务：%s\n原因：%s（%s）。\n"
        "请检查 Camera、ToF、VisionPC、网络和小车状态后重试。",
        status->job_id,
        scan_error_description(status->last_error),
        status->last_error);
}

esp_err_t rehab_task_decision_send_direct_message(const char *channel,
                                                   const char *chat_id,
                                                   const char *message)
{
    const char *cap_name = scan_notification_cap(channel);
    if (cap_name == NULL || chat_id == NULL || chat_id[0] == '\0' ||
        message == NULL || message[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    char *output = calloc(1, SCAN_NOTIFICATION_OUTPUT_MAX);
    if (output == NULL) {
        return ESP_ERR_NO_MEM;
    }
    cJSON *root = cJSON_CreateObject();
    if (root == NULL ||
        cJSON_AddStringToObject(root, "chat_id", chat_id) == NULL ||
        cJSON_AddStringToObject(root, "message", message) == NULL) {
        cJSON_Delete(root);
        free(output);
        return ESP_ERR_NO_MEM;
    }
    char *input = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (input == NULL) {
        free(output);
        return ESP_ERR_NO_MEM;
    }
    claw_cap_call_context_t call_ctx = {
        .channel = channel,
        .chat_id = chat_id,
        .target_channel = channel,
        .target_chat_id = chat_id,
        .source_cap = "rehab_task_decision",
        .caller = CLAW_CAP_CALLER_SYSTEM,
    };
    esp_err_t err = claw_cap_call(cap_name, input, &call_ctx, output,
                                  SCAN_NOTIFICATION_OUTPUT_MAX);
    cJSON_free(input);
    free(output);
    return err;
}

static esp_err_t scan_send_start_ack(uint32_t generation,
                                     const char *channel,
                                     const char *chat_id)
{
    esp_err_t err = rehab_task_decision_send_direct_message(
        channel, chat_id,
        "扫描已启动。小车将进行最多 3 次短时旋转与环境观察，完成后会自动发送结果。");
    ESP_LOGI(TAG,
             "SCAN_START_ACK generation=%" PRIu32
             " channel=%s result=%s",
             generation, channel != NULL ? channel : "none",
             esp_err_to_name(err));
    return err;
}

static esp_err_t scan_send_completion_notification(
    const rehab_task_decision_status_t *status,
    const char *channel, const char *chat_id)
{
    char *message = calloc(1, SCAN_NOTIFICATION_MESSAGE_MAX);
    if (message == NULL) {
        return ESP_ERR_NO_MEM;
    }
    if (!scan_build_notification_message(status, message,
                                          SCAN_NOTIFICATION_MESSAGE_MAX)) {
        free(message);
        return ESP_ERR_INVALID_SIZE;
    }
    esp_err_t err = rehab_task_decision_send_direct_message(channel, chat_id,
                                                             message);
    ESP_LOGI(TAG,
             "SCAN_NOTIFY generation=%" PRIu32 " channel=%s result=%s",
             status->generation, channel, esp_err_to_name(err));
    free(message);
    return err;
}

esp_err_t rehab_task_decision_fail_to_idle(
    rehab_task_decision_handle_t handle,
    uint32_t generation,
    const char *reason)
{
    if (handle == NULL || reason == NULL || reason[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    if (!scan_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (generation != 0 && handle->status.generation != generation) {
        scan_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    /* Keep generation validation and transient staging cleanup in one
     * critical section.  A late failure must never touch the authoritative
     * current selection.  No selected-task helper acquires handle->lock
     * while holding selected_task_lock. */
    (void)rehab_car_stop(handle->car);
    esp_err_t storage_err =
        rehab_task_decision_selected_invalidate(handle);
    cJSON_free(handle->last_decision_result);
    handle->last_decision_result = NULL;
    handle->last_decision_generation = 0;
    handle->validation_generation = 0;
    handle->validation_failure_count = 0;
    handle->decision_context_generation = 0;
    handle->decision_context_request_id = 0;
    handle->decision_rules_generation = 0;
    handle->decision_rules_request_id = 0;
    ++handle->run_generation;
    if (handle->run_generation == 0) {
        ++handle->run_generation;
    }
    bool fusion_ready = handle->fusion_ready;
    memset(&handle->status, 0, sizeof(handle->status));
    handle->status.state = REHAB_TASK_DECISION_STATE_IDLE;
    handle->status.fusion_ready = fusion_ready;
    handle->status.deadline_ms = REHAB_TASK_DECISION_SCAN_TIMEOUT_MS;
    handle->status.planned_observation_count = SCAN_MAX_STEPS;
    handle->status.last_stop_result = ESP_OK;
    strlcpy(handle->status.last_error,
            storage_err == ESP_OK ? reason : "selected_task_clear_failed",
            sizeof(handle->status.last_error));
    strlcpy(handle->status.start_ack_error, "not_requested",
            sizeof(handle->status.start_ack_error));
    strlcpy(handle->status.notification_error, "not_requested",
            sizeof(handle->status.notification_error));
    strlcpy(handle->status.decision_trigger_error, "not_requested",
            sizeof(handle->status.decision_trigger_error));
    scan_unlock(handle);
    ESP_LOGW(TAG,
             "DECISION_ABORT generation=%" PRIu32
             " reason=%s storage=%s state=IDLE",
             generation, reason, esp_err_to_name(storage_err));
    return storage_err;
}

static void scan_add_observation_summary_locked(
    rehab_task_decision_handle_t handle,
    const rehab_cloud_fusion_result_t *result,
    uint64_t observed_at_ms,
    bool valid,
    const char *error_stage,
    const char *error_code)
{
    if (handle->status.observation_summary_count >=
        REHAB_TASK_DECISION_MAX_OBSERVATION_SUMMARIES) {
        return;
    }
    rehab_environment_observation_summary_t *summary =
        &handle->status.observations[
            handle->status.observation_summary_count++];
    strlcpy(summary->capture_id, result->capture_id,
            sizeof(summary->capture_id));
    summary->observed_at_ms = observed_at_ms;
    summary->step = handle->status.current_step;
    summary->valid = valid;
    summary->pairing_valid = result->has_pairing &&
        result->cloud_pair_offset_ms <= result->cloud_max_pair_offset_ms;
    summary->cloud_valid = result->cloud_valid;
    strlcpy(summary->cloud_status, result->cloud_status,
            sizeof(summary->cloud_status));
    summary->detection_count = result->detection_count;
    strlcpy(summary->result_class,
            valid ? (result->detection_count > 0 ? "object" : "empty")
                  : "technical_failure",
            sizeof(summary->result_class));
    strlcpy(summary->error_stage, error_stage,
            sizeof(summary->error_stage));
    strlcpy(summary->error_code, error_code,
            sizeof(summary->error_code));
    summary->http_status = result->http_status;
}

static void scan_record_rejection_locked(
    rehab_task_decision_handle_t handle,
    uint32_t step,
    esp_err_t err,
    const rehab_cloud_fusion_result_t *result,
    uint64_t observed_at_ms)
{
    const char *category = scan_fusion_error_code(err, result);
    const char *stage = scan_fusion_error_stage(err, result);
    const char *detail = scan_fusion_error_detail(err, result);

    ++handle->status.rejected_observation_count;
    ++handle->status.technical_failure_count;
    if (strcmp(category, "capture_failed") == 0) {
        ++handle->status.rejection_capture_failed_count;
    } else if (strcmp(category, "fusion_timeout") == 0) {
        ++handle->status.rejection_fusion_timeout_count;
    } else {
        ++handle->status.rejection_fusion_invalid_count;
    }
    handle->status.last_rejection_step = step;
    strlcpy(handle->status.last_rejection_stage, stage,
            sizeof(handle->status.last_rejection_stage));
    strlcpy(handle->status.last_rejection_error, detail,
            sizeof(handle->status.last_rejection_error));
    handle->status.last_rejection_http_status = result->http_status;
    strlcpy(handle->status.last_error, detail,
            sizeof(handle->status.last_error));
    scan_add_observation_summary_locked(handle, result, observed_at_ms,
                                        false, stage, detail);
    ESP_LOGW(TAG,
             "SCAN_STEP_REJECTED generation=%" PRIu32
             " step=%" PRIu32 " stage=%s err=%s http_status=%d",
             handle->status.generation, step, stage, detail,
             result->http_status);
}

static void scan_finish_planned_observations_locked(
    rehab_task_decision_handle_t handle)
{
    if (handle->status.valid_observation_count > 0) {
        handle->status.state = REHAB_TASK_DECISION_STATE_COMPLETED;
        strlcpy(handle->status.last_error, "none",
                sizeof(handle->status.last_error));
    } else {
        handle->status.state =
            REHAB_TASK_DECISION_STATE_NO_VALID_OBSERVATION;
        strlcpy(handle->status.last_error, "no_valid_observation",
                sizeof(handle->status.last_error));
    }
}

static void scan_worker(void *arg)
{
    rehab_task_decision_handle_t handle = arg;
    uint32_t generation = 0;
    if (scan_lock(handle)) {
        generation = handle->status.generation;
        scan_unlock(handle);
    }

    for (uint32_t step = 1; step <= SCAN_MAX_STEPS; ++step) {
        uint64_t now_ms = scan_now_ms();
        if (!scan_lock(handle)) {
            break;
        }
        scan_update_elapsed_locked(handle, now_ms);
        if (!scan_is_current_locked(handle, generation)) {
            scan_unlock(handle);
            break;
        }
        uint32_t round_remaining_ms = handle->status.deadline_ms >
                handle->status.elapsed_ms
            ? handle->status.deadline_ms - handle->status.elapsed_ms : 0;
        if (round_remaining_ms < SCAN_MIN_ROUND_BUDGET_MS) {
            scan_finish_deadline_locked(handle);
            scan_unlock(handle);
            break;
        }
        handle->status.current_step = step;
        handle->status.state = REHAB_TASK_DECISION_STATE_ROTATING;
        scan_unlock(handle);

        ESP_LOGI(TAG,
                 "SCAN_STEP_BEGIN generation=%" PRIu32
                 " step=%" PRIu32 "/%u speed=%u rotate_ms=%u",
                 generation, step, (unsigned)SCAN_MAX_STEPS,
                 (unsigned)SCAN_ROTATE_SPEED_LEVEL,
                 (unsigned)SCAN_ROTATE_MS);

        esp_err_t err = scan_execute_rotation(handle, generation);
        if (!scan_lock(handle)) {
            break;
        }
        if (!scan_is_current_locked(handle, generation)) {
            scan_unlock(handle);
            break;
        }
        if (err != ESP_OK) {
            scan_error_locked(handle, "movement_failed");
            scan_unlock(handle);
            break;
        }
        handle->status.state = REHAB_TASK_DECISION_STATE_STOPPING;
        scan_unlock(handle);

        err = scan_owned_stop(handle);
        if (!scan_lock(handle)) {
            break;
        }
        if (!scan_is_current_locked(handle, generation)) {
            scan_unlock(handle);
            break;
        }
        handle->status.last_stop_result = err;
        if (err != ESP_OK) {
            scan_error_locked(handle, "stop_failed");
            scan_unlock(handle);
            break;
        }
        handle->status.state = REHAB_TASK_DECISION_STATE_SETTLING;
        scan_unlock(handle);

        if (!scan_wait_cancelable(handle, generation, SCAN_SETTLE_MS)) {
            if (scan_lock(handle)) {
                if (scan_is_current_locked(handle, generation)) {
                    scan_update_elapsed_locked(handle, scan_now_ms());
                    scan_finish_deadline_locked(handle);
                }
                scan_unlock(handle);
            }
            break;
        }

        if (!scan_lock(handle)) {
            break;
        }
        scan_update_elapsed_locked(handle, scan_now_ms());
        if (!scan_is_current_locked(handle, generation)) {
            scan_unlock(handle);
            break;
        }
        uint32_t remaining_ms = handle->status.deadline_ms >
                handle->status.elapsed_ms
            ? handle->status.deadline_ms - handle->status.elapsed_ms : 0;
        if (remaining_ms < SCAN_MIN_FUSION_WINDOW_MS) {
            scan_finish_deadline_locked(handle);
            scan_unlock(handle);
            break;
        }
        uint32_t fusion_timeout_ms = remaining_ms < SCAN_FUSION_TIMEOUT_MS
            ? remaining_ms : SCAN_FUSION_TIMEOUT_MS;
        handle->status.state = REHAB_TASK_DECISION_STATE_SENSING;
        handle->status.perception_in_flight = true;
        ++handle->status.requested_observation_count;
        scan_unlock(handle);

        memset(handle->fusion_result, 0, sizeof(*handle->fusion_result));
        ESP_LOGI(TAG,
                 "SCAN_PERCEPTION_BEGIN generation=%" PRIu32
                 " step=%" PRIu32
                 " api=rehab_cloud_fusion_upload_once timeout_ms=%" PRIu32,
                 generation, step, fusion_timeout_ms);
        err = rehab_cloud_fusion_upload_once(handle->fusion_result,
                                              fusion_timeout_ms);
        if (scan_fusion_result_valid(err, handle->fusion_result)) {
            ESP_LOGI(TAG,
                     "SCAN_PERCEPTION_OK capture_id=%s detections=%" PRIu32,
                     handle->fusion_result->capture_id,
                     handle->fusion_result->detection_count);
        } else {
            ESP_LOGE(TAG,
                     "SCAN_PERCEPTION_FAILED generation=%" PRIu32
                     " step=%" PRIu32 " stage=%s err=%s http_status=%d",
                     generation, step,
                     scan_fusion_error_stage(err, handle->fusion_result),
                     scan_fusion_error_detail(err, handle->fusion_result),
                     handle->fusion_result->http_status);
        }
        now_ms = scan_now_ms();

        if (!scan_lock(handle)) {
            break;
        }
        handle->status.perception_in_flight = false;
        scan_update_elapsed_locked(handle, now_ms);
        if (!scan_is_current_locked(handle, generation)) {
            scan_unlock(handle);
            break;
        }
        ++handle->status.completed_observation_count;
        if (handle->status.elapsed_ms >= handle->status.deadline_ms) {
            ++handle->status.rejected_observation_count;
            ++handle->status.technical_failure_count;
            ++handle->status.rejection_late_result_count;
            handle->status.last_rejection_step = step;
            strlcpy(handle->status.last_rejection_stage, "timeout",
                    sizeof(handle->status.last_rejection_stage));
            strlcpy(handle->status.last_rejection_error, "late_result",
                    sizeof(handle->status.last_rejection_error));
            handle->status.last_rejection_http_status =
                handle->fusion_result->http_status;
            scan_add_observation_summary_locked(
                handle, handle->fusion_result, now_ms, false,
                "timeout", "late_result");
            ESP_LOGW(TAG,
                     "SCAN_STEP_REJECTED generation=%" PRIu32
                     " step=%" PRIu32 " stage=timeout err=late_result"
                     " http_status=%d",
                     generation, step, handle->fusion_result->http_status);
            scan_finish_deadline_locked(handle);
            scan_unlock(handle);
            break;
        }
        if (!scan_fusion_result_valid(err, handle->fusion_result)) {
            bool capture_failure =
                scan_capture_pipeline_failure(handle->fusion_result);
            scan_record_rejection_locked(handle, step, err,
                                          handle->fusion_result, now_ms);
            bool planned_complete = step == SCAN_MAX_STEPS;
            if (planned_complete && !capture_failure) {
                scan_finish_planned_observations_locked(handle);
            }
            scan_unlock(handle);
            if (capture_failure && !scan_wait_capture_quiescence(
                    handle, generation, step)) {
                break;
            }
            if (planned_complete) {
                if (capture_failure && scan_lock(handle)) {
                    if (scan_is_current_locked(handle, generation)) {
                        scan_finish_planned_observations_locked(handle);
                    }
                    scan_unlock(handle);
                }
                break;
            }
            continue;
        }

        handle->status.state = REHAB_TASK_DECISION_STATE_AGGREGATING;
        strlcpy(handle->status.last_capture_id,
                handle->fusion_result->capture_id,
                sizeof(handle->status.last_capture_id));
        uint32_t accepted = rehab_environment_aggregate_apply(
            &handle->status, handle->fusion_result, now_ms);
        if (accepted < handle->fusion_result->detection_count) {
            handle->status.rejection_aggregate_capacity_count +=
                handle->fusion_result->detection_count - accepted;
        }
        ++handle->status.valid_observation_count;
        if (handle->fusion_result->detection_count == 0) {
            ++handle->status.empty_observation_count;
            scan_add_observation_summary_locked(
                handle, handle->fusion_result, now_ms, true,
                "none", "none");
            ESP_LOGI(TAG,
                     "SCAN_PERCEPTION_EMPTY capture_id=%s detections=0",
                     handle->fusion_result->capture_id);
        } else {
            ++handle->status.object_observation_count;
            scan_add_observation_summary_locked(
                handle, handle->fusion_result, now_ms, true,
                "none", "none");
        }

        if (step == SCAN_MAX_STEPS) {
            scan_finish_planned_observations_locked(handle);
            scan_unlock(handle);
            break;
        }
        scan_unlock(handle);
    }

    esp_err_t stop_result = scan_stop_and_release(handle);
    rehab_task_decision_status_t *notification_status = NULL;
    char notification_channel[sizeof(handle->notification_channel)] = {0};
    char notification_chat_id[sizeof(handle->notification_chat_id)] = {0};
    bool scan_succeeded = false;
    bool notification_required = false;
    if (scan_lock(handle)) {
        if (stop_result != ESP_OK) {
            handle->status.last_stop_result = stop_result;
            scan_error_locked(handle, "stop_failed");
        } else {
            handle->status.last_stop_result = ESP_OK;
            if (handle->cancel_requested ||
                handle->run_generation != generation) {
                handle->status.state = REHAB_TASK_DECISION_STATE_CANCELLED;
                strlcpy(handle->status.last_error, "cancelled",
                        sizeof(handle->status.last_error));
            }
        }
        if (handle->status.state !=
                       REHAB_TASK_DECISION_STATE_COMPLETED &&
            handle->status.state != REHAB_TASK_DECISION_STATE_CANCELLED &&
            handle->status.state !=
                       REHAB_TASK_DECISION_STATE_DEADLINE_EXCEEDED &&
            handle->status.state !=
                       REHAB_TASK_DECISION_STATE_NO_VALID_OBSERVATION &&
            handle->status.state != REHAB_TASK_DECISION_STATE_ERROR) {
            if (handle->status.valid_observation_count > 0) {
                handle->status.state = REHAB_TASK_DECISION_STATE_COMPLETED;
                strlcpy(handle->status.last_error, "none",
                        sizeof(handle->status.last_error));
            } else {
                handle->status.state =
                    REHAB_TASK_DECISION_STATE_NO_VALID_OBSERVATION;
                strlcpy(handle->status.last_error, "no_valid_observation",
                        sizeof(handle->status.last_error));
            }
        }
        scan_update_elapsed_locked(handle, scan_now_ms());
        handle->status.final_state_frozen = true;
        handle->status.worker_active = false;
        handle->status.perception_in_flight = false;
        handle->status.cancel_requested = false;
        handle->cancel_requested = false;
        scan_succeeded = handle->status.state ==
            REHAB_TASK_DECISION_STATE_COMPLETED;
        notification_required = handle->status.notification_requested;
        ESP_LOGI(TAG,
                 "SCAN_FINAL generation=%" PRIu32
                 " state=%s planned=%" PRIu32 " completed=%" PRIu32
                 " object=%" PRIu32 " empty=%" PRIu32
                 " technical_failure=%" PRIu32
                 " valid=%" PRIu32 " rejected=%" PRIu32,
                 generation,
                 rehab_task_decision_state_name(handle->status.state),
                 handle->status.planned_observation_count,
                 handle->status.completed_observation_count,
                 handle->status.object_observation_count,
                 handle->status.empty_observation_count,
                 handle->status.technical_failure_count,
                 handle->status.valid_observation_count,
                 handle->status.rejected_observation_count);
        if (handle->status.final_state_frozen &&
            handle->status.notification_requested &&
            !handle->status.notification_attempted) {
            handle->status.notification_attempted = true;
            notification_status = malloc(sizeof(*notification_status));
            if (notification_status != NULL) {
                memcpy(notification_status, &handle->status,
                       sizeof(*notification_status));
                strlcpy(notification_channel, handle->notification_channel,
                        sizeof(notification_channel));
                strlcpy(notification_chat_id, handle->notification_chat_id,
                        sizeof(notification_chat_id));
            } else {
                strlcpy(handle->status.notification_error,
                        "notification_snapshot_no_memory",
                        sizeof(handle->status.notification_error));
            }
        }
        scan_unlock(handle);
    }
    bool notification_succeeded = !notification_required;
    if (notification_status != NULL) {
        esp_err_t notification_result = scan_send_completion_notification(
            notification_status, notification_channel,
            notification_chat_id);
        if (scan_lock(handle)) {
            if (handle->status.generation == notification_status->generation) {
                handle->status.notification_sent =
                    notification_result == ESP_OK;
                strlcpy(handle->status.notification_error,
                        notification_result == ESP_OK
                            ? "none" : esp_err_to_name(notification_result),
                        sizeof(handle->status.notification_error));
            }
            scan_unlock(handle);
        }
        notification_succeeded = notification_result == ESP_OK;
        if (notification_result == ESP_OK &&
            notification_status->state ==
                REHAB_TASK_DECISION_STATE_COMPLETED) {
            (void)rehab_task_decision_publish_event(
                handle, notification_status->generation,
                notification_channel, notification_chat_id);
        }
        free(notification_status);
    }
    if (!scan_succeeded || !notification_succeeded) {
        (void)rehab_task_decision_fail_to_idle(
            handle, generation,
            scan_succeeded ? "scan_notification_failed" : "scan_failed");
    }
    xSemaphoreGive(handle->worker_done);
    vTaskDelete(NULL);
}

const char *rehab_task_decision_state_name(rehab_task_decision_state_t state)
{
    switch (state) {
    case REHAB_TASK_DECISION_STATE_IDLE: return "IDLE";
    case REHAB_TASK_DECISION_STATE_ACQUIRING_OWNER: return "ACQUIRING_OWNER";
    case REHAB_TASK_DECISION_STATE_ROTATING: return "ROTATING";
    case REHAB_TASK_DECISION_STATE_STOPPING: return "STOPPING";
    case REHAB_TASK_DECISION_STATE_SETTLING: return "SETTLING";
    case REHAB_TASK_DECISION_STATE_SENSING: return "SENSING";
    case REHAB_TASK_DECISION_STATE_AGGREGATING: return "AGGREGATING";
    case REHAB_TASK_DECISION_STATE_COMPLETED: return "COMPLETED";
    case REHAB_TASK_DECISION_STATE_DECIDING: return "DECIDING";
    case REHAB_TASK_DECISION_STATE_VALIDATING: return "VALIDATING";
    case REHAB_TASK_DECISION_STATE_COMMITTING: return "COMMITTING";
    case REHAB_TASK_DECISION_STATE_READY_SELECTED: return "READY_SELECTED";
    case REHAB_TASK_DECISION_STATE_CANCELLED: return "CANCELLED";
    case REHAB_TASK_DECISION_STATE_DEADLINE_EXCEEDED:
        return "DEADLINE_EXCEEDED";
    case REHAB_TASK_DECISION_STATE_NO_VALID_OBSERVATION:
        return "NO_VALID_OBSERVATION";
    case REHAB_TASK_DECISION_STATE_ERROR: return "ERROR";
    default: return "UNKNOWN";
    }
}

esp_err_t rehab_task_decision_create(
    const rehab_task_decision_config_t *config,
    rehab_task_decision_handle_t *ret_handle)
{
    if (config == NULL || config->car == NULL || ret_handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *ret_handle = NULL;
    rehab_task_decision_handle_t handle = calloc(1, sizeof(*handle));
    if (handle == NULL) {
        return ESP_ERR_NO_MEM;
    }
    handle->lock = xSemaphoreCreateMutex();
    handle->car_operation_lock = xSemaphoreCreateMutex();
    handle->worker_done = xSemaphoreCreateBinary();
    handle->selected_task_lock = xSemaphoreCreateMutex();
    handle->decision_watchdog_done = xSemaphoreCreateBinary();
    handle->fusion_result = calloc(1, sizeof(*handle->fusion_result));
    if (handle->lock == NULL || handle->car_operation_lock == NULL ||
        handle->worker_done == NULL || handle->selected_task_lock == NULL ||
        handle->decision_watchdog_done == NULL ||
        handle->fusion_result == NULL) {
        if (handle->lock != NULL) vSemaphoreDelete(handle->lock);
        if (handle->car_operation_lock != NULL) {
            vSemaphoreDelete(handle->car_operation_lock);
        }
        if (handle->worker_done != NULL) vSemaphoreDelete(handle->worker_done);
        if (handle->selected_task_lock != NULL) {
            vSemaphoreDelete(handle->selected_task_lock);
        }
        if (handle->decision_watchdog_done != NULL) {
            vSemaphoreDelete(handle->decision_watchdog_done);
        }
        free(handle->fusion_result);
        free(handle);
        return ESP_ERR_NO_MEM;
    }
    handle->car = config->car;
    handle->fusion_ready = config->fusion_ready;
    handle->training_active = config->training_active;
    handle->training_active_ctx = config->training_active_ctx;
    handle->status.state = REHAB_TASK_DECISION_STATE_IDLE;
    handle->status.fusion_ready = config->fusion_ready;
    handle->status.deadline_ms = REHAB_TASK_DECISION_SCAN_TIMEOUT_MS;
    handle->status.planned_observation_count = SCAN_MAX_STEPS;
    handle->status.last_stop_result = ESP_ERR_NOT_FINISHED;
    strlcpy(handle->status.last_error, "none",
            sizeof(handle->status.last_error));
    strlcpy(handle->status.notification_error, "none",
            sizeof(handle->status.notification_error));
    strlcpy(handle->status.start_ack_error, "none",
            sizeof(handle->status.start_ack_error));
    strlcpy(handle->status.decision_trigger_error, "none",
            sizeof(handle->status.decision_trigger_error));
    esp_err_t selected_err = rehab_task_decision_rules_initialize(handle);
    if (selected_err == ESP_OK) {
        selected_err = rehab_task_decision_selected_initialize(handle);
    }
    if (selected_err == ESP_OK) {
        selected_err = rehab_task_decision_coordinator_initialize(handle,
                                                                  config);
    }
    if (selected_err != ESP_OK) {
        rehab_task_decision_coordinator_release(handle);
        rehab_task_decision_rules_release(handle);
        vSemaphoreDelete(handle->decision_watchdog_done);
        vSemaphoreDelete(handle->selected_task_lock);
        vSemaphoreDelete(handle->worker_done);
        vSemaphoreDelete(handle->car_operation_lock);
        vSemaphoreDelete(handle->lock);
        free(handle->fusion_result);
        free(handle);
        return selected_err;
    }
    *ret_handle = handle;
    return ESP_OK;
}

esp_err_t rehab_task_decision_scan_start(rehab_task_decision_handle_t handle,
                                         const char *source_channel,
                                         const char *source_chat_id)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!scan_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (!handle->status.worker_active && handle->worker_task != NULL &&
        xSemaphoreTake(handle->worker_done, 0) == pdTRUE) {
        handle->worker_task = NULL;
    }
    bool decision_pending =
        handle->status.state == REHAB_TASK_DECISION_STATE_DECIDING ||
        handle->status.state == REHAB_TASK_DECISION_STATE_VALIDATING ||
        handle->status.state == REHAB_TASK_DECISION_STATE_COMMITTING;
    if (handle->deleting || handle->status.worker_active ||
        handle->worker_task != NULL || decision_pending) {
        strlcpy(handle->status.last_error, "scan_already_running",
                sizeof(handle->status.last_error));
        scan_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    scan_unlock(handle);
    if (!handle->fusion_ready) {
        scan_set_start_rejection(handle, "fusion_not_ready");
        return ESP_ERR_INVALID_STATE;
    }

    rehab_capture_status_t *capture = calloc(1, sizeof(*capture));
    if (capture == NULL) {
        scan_set_start_rejection(handle, "internal_error");
        return ESP_ERR_NO_MEM;
    }
    esp_err_t capture_err = rehab_capture_get_status(capture);
    bool capture_ready = capture_err == ESP_OK &&
                         capture->configured_candidate_count > 0 &&
                         capture->camera_ready;
    bool tof_ready = capture_err == ESP_OK && capture->tof_ready &&
                     rehab_tof_is_ready();
    free(capture);
    if (!capture_ready) {
        scan_set_start_rejection(handle, "capture_not_ready");
        return ESP_ERR_INVALID_STATE;
    }
    if (!tof_ready) {
        scan_set_start_rejection(handle, "tof_not_ready");
        return ESP_ERR_INVALID_STATE;
    }
    if (!rehab_capture_is_ready()) {
        scan_set_start_rejection(handle, "capture_not_ready");
        return ESP_ERR_INVALID_STATE;
    }

    esp_err_t reset_err = rehab_task_decision_watchdog_cancel(handle, 2000U);
    if (reset_err != ESP_OK) {
        scan_set_start_rejection(handle, "decision_watchdog_busy");
        return reset_err;
    }
    reset_err = rehab_task_decision_selected_invalidate(handle);
    if (reset_err != ESP_OK) {
        scan_set_start_rejection(handle, "selected_task_staging_cleanup_failed");
        return reset_err;
    }

    char start_ack_channel[sizeof(handle->notification_channel)] = {0};
    char start_ack_chat_id[sizeof(handle->notification_chat_id)] = {0};
    if (!scan_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (handle->deleting || handle->status.worker_active) {
        strlcpy(handle->status.last_error, "scan_already_running",
                sizeof(handle->status.last_error));
        scan_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    ++handle->run_generation;
    if (handle->run_generation == 0) {
        ++handle->run_generation;
    }
    memset(&handle->status, 0, sizeof(handle->status));
    cJSON_free(handle->last_decision_result);
    handle->last_decision_result = NULL;
    handle->last_decision_generation = 0;
    handle->validation_generation = 0;
    handle->validation_failure_count = 0;
    handle->decision_context_generation = 0;
    handle->decision_context_request_id = 0;
    handle->decision_rules_generation = 0;
    handle->decision_rules_request_id = 0;
    handle->status.state = REHAB_TASK_DECISION_STATE_ACQUIRING_OWNER;
    handle->status.worker_active = true;
    handle->status.fusion_ready = handle->fusion_ready;
    handle->status.generation = handle->run_generation;
    handle->status.started_at_ms = scan_now_ms();
    handle->status.deadline_ms = REHAB_TASK_DECISION_SCAN_TIMEOUT_MS;
    handle->status.planned_observation_count = SCAN_MAX_STEPS;
    handle->status.last_stop_result = ESP_ERR_NOT_FINISHED;
    const char *notification_cap = scan_notification_cap(source_channel);
    handle->status.notification_requested = notification_cap != NULL &&
        source_chat_id != NULL && source_chat_id[0] != '\0';
    handle->status.decision_trigger_requested =
        handle->status.notification_requested;
    handle->status.start_ack_requested =
        handle->status.notification_requested;
    strlcpy(handle->status.start_ack_error,
            handle->status.start_ack_requested ? "none" : "not_requested",
            sizeof(handle->status.start_ack_error));
    strlcpy(handle->status.notification_error, "none",
            sizeof(handle->status.notification_error));
    strlcpy(handle->status.decision_trigger_error,
            handle->status.decision_trigger_requested
                ? "none" : "not_requested",
            sizeof(handle->status.decision_trigger_error));
    strlcpy(handle->notification_channel,
            handle->status.notification_requested ? source_channel : "",
            sizeof(handle->notification_channel));
    strlcpy(handle->notification_chat_id,
            handle->status.notification_requested ? source_chat_id : "",
            sizeof(handle->notification_chat_id));
    strlcpy(start_ack_channel, handle->notification_channel,
            sizeof(start_ack_channel));
    strlcpy(start_ack_chat_id, handle->notification_chat_id,
            sizeof(start_ack_chat_id));
    snprintf(handle->status.job_id, sizeof(handle->status.job_id),
             "scan-%08" PRIx32 "-%" PRIu64,
             handle->status.generation, handle->status.started_at_ms);
    strlcpy(handle->status.last_error, "none",
            sizeof(handle->status.last_error));
    handle->worker_task = (TaskHandle_t)1;
    handle->cancel_requested = false;
    uint32_t generation = handle->status.generation;
    scan_unlock(handle);

    bool owner_acquired = false;
    bool owner_stop_failed = false;
    esp_err_t err = rehab_car_control_acquire(
        handle->car, REHAB_CAR_CONTROL_OWNER_ENVIRONMENT_SCAN,
        scan_preempted, handle, &handle->car_lease);
    if (err == ESP_OK) {
        owner_acquired = true;
        err = scan_owned_stop(handle);
        owner_stop_failed = err != ESP_OK;
    }
    bool cancelled_before_worker = false;
    if (err == ESP_OK) {
        if (!scan_lock(handle)) {
            err = ESP_ERR_TIMEOUT;
        } else {
            cancelled_before_worker =
                !scan_is_current_locked(handle, generation);
            scan_unlock(handle);
            if (cancelled_before_worker) {
                err = ESP_ERR_INVALID_STATE;
            }
        }
    }
    if (err != ESP_OK) {
        (void)scan_stop_and_release(handle);
        if (scan_lock(handle)) {
            handle->status.worker_active = false;
            handle->worker_task = NULL;
            handle->status.cancel_requested = false;
            handle->cancel_requested = false;
            if (cancelled_before_worker) {
                handle->status.state = REHAB_TASK_DECISION_STATE_CANCELLED;
                strlcpy(handle->status.last_error, "cancelled",
                        sizeof(handle->status.last_error));
            } else {
                handle->status.state = REHAB_TASK_DECISION_STATE_ERROR;
                strlcpy(handle->status.last_error,
                        owner_stop_failed ? "stop_failed" :
                        (owner_acquired ? "internal_error" :
                         (err == ESP_ERR_INVALID_STATE ? "car_busy" :
                          "owner_acquire_failed")),
                        sizeof(handle->status.last_error));
            }
            scan_unlock(handle);
        }
        (void)rehab_task_decision_fail_to_idle(
            handle, generation,
            cancelled_before_worker ? "cancelled" :
            (owner_stop_failed ? "stop_failed" :
             (owner_acquired ? "internal_error" :
              (err == ESP_ERR_INVALID_STATE ? "car_busy" :
               "owner_acquire_failed"))));
        return err;
    }

    bool start_ack_requested = start_ack_channel[0] != '\0' &&
        start_ack_chat_id[0] != '\0';
    if (start_ack_requested) {
        bool start_ack_allowed = false;
        if (scan_lock(handle)) {
            if (scan_is_current_locked(handle, generation)) {
                handle->status.start_ack_attempted = true;
                start_ack_allowed = true;
            }
            scan_unlock(handle);
        }
        if (start_ack_allowed) {
            esp_err_t start_ack_result = scan_send_start_ack(
                generation, start_ack_channel, start_ack_chat_id);
            if (scan_lock(handle)) {
                if (handle->status.generation == generation) {
                    handle->status.start_ack_sent = start_ack_result == ESP_OK;
                    strlcpy(handle->status.start_ack_error,
                            start_ack_result == ESP_OK
                                ? "none" : esp_err_to_name(start_ack_result),
                            sizeof(handle->status.start_ack_error));
                }
                scan_unlock(handle);
            }
        }
    }

    cancelled_before_worker = false;
    if (!scan_lock(handle)) {
        err = ESP_ERR_TIMEOUT;
    } else {
        scan_update_elapsed_locked(handle, scan_now_ms());
        cancelled_before_worker =
            !scan_is_current_locked(handle, generation);
        scan_unlock(handle);
        if (cancelled_before_worker) {
            err = ESP_ERR_INVALID_STATE;
        }
    }
    if (err != ESP_OK) {
        (void)scan_stop_and_release(handle);
        if (scan_lock(handle)) {
            handle->status.worker_active = false;
            handle->worker_task = NULL;
            handle->status.cancel_requested = false;
            handle->cancel_requested = false;
            handle->status.state = cancelled_before_worker
                ? REHAB_TASK_DECISION_STATE_CANCELLED
                : REHAB_TASK_DECISION_STATE_ERROR;
            strlcpy(handle->status.last_error,
                    cancelled_before_worker ? "cancelled" : "internal_error",
                    sizeof(handle->status.last_error));
            scan_unlock(handle);
        }
        (void)rehab_task_decision_fail_to_idle(
            handle, generation,
            cancelled_before_worker ? "cancelled" : "internal_error");
        return err;
    }

    TaskHandle_t worker = NULL;
    if (xTaskCreate(scan_worker, "environment_scan", SCAN_WORKER_STACK,
                    handle, SCAN_WORKER_PRIORITY, &worker) != pdPASS) {
        (void)scan_stop_and_release(handle);
        if (scan_lock(handle)) {
            handle->status.worker_active = false;
            handle->worker_task = NULL;
            scan_error_locked(handle, "internal_error");
            scan_unlock(handle);
        }
        (void)rehab_task_decision_fail_to_idle(handle, generation,
                                               "internal_error");
        return ESP_ERR_NO_MEM;
    }
    if (scan_lock(handle)) {
        if (handle->worker_task == (TaskHandle_t)1) {
            handle->worker_task = worker;
        }
        scan_unlock(handle);
    }
    ESP_LOGI(TAG,
             "SCAN_START accepted=1 generation=%" PRIu32
             " deadline_ms=%u max_steps=%u",
             generation,
             (unsigned)REHAB_TASK_DECISION_SCAN_TIMEOUT_MS,
             (unsigned)SCAN_MAX_STEPS);
    return ESP_OK;
}

esp_err_t rehab_task_decision_scan_cancel(rehab_task_decision_handle_t handle,
                                          bool *already_idle)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (already_idle != NULL) {
        *already_idle = false;
    }
    if (!scan_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (!handle->status.worker_active) {
        if (already_idle != NULL) {
            *already_idle = true;
        }
        scan_unlock(handle);
        return ESP_OK;
    }
    handle->cancel_requested = true;
    ++handle->run_generation;
    if (handle->run_generation == 0) {
        ++handle->run_generation;
    }
    handle->status.cancel_requested = true;
    handle->status.state = REHAB_TASK_DECISION_STATE_CANCELLED;
    strlcpy(handle->status.last_error, "cancelled",
            sizeof(handle->status.last_error));
    TaskHandle_t worker = handle->worker_task;
    scan_unlock(handle);

    esp_err_t stop_result = scan_stop_and_release(handle);
    if (scan_lock(handle)) {
        handle->status.last_stop_result = stop_result;
        scan_unlock(handle);
    }
    if (worker != NULL && worker != (TaskHandle_t)1) {
        xTaskNotifyGive(worker);
    }
    return stop_result;
}

esp_err_t rehab_task_decision_scan_get_status(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_status_t *status)
{
    if (handle == NULL || status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!scan_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    scan_update_elapsed_locked(handle, scan_now_ms());
    memcpy(status, &handle->status, sizeof(*status));
    scan_unlock(handle);

    rehab_car_status_t *car_status = calloc(1, sizeof(*car_status));
    if (car_status == NULL) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = rehab_car_get_status(handle->car, car_status);
    if (err == ESP_OK) {
        status->car_owner = car_status->control_owner;
    }
    free(car_status);
    return err;
}

esp_err_t rehab_task_decision_delete(rehab_task_decision_handle_t handle)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    rehab_task_decision_coordinator_status_t coordinator = {0};
    esp_err_t coordinator_err =
        rehab_task_decision_coordinator_get_status(handle, &coordinator);
    if (coordinator_err == ESP_OK && coordinator.active) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!scan_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    handle->deleting = true;
    scan_unlock(handle);
    esp_err_t watchdog_err = rehab_task_decision_watchdog_cancel(handle,
                                                                 2000U);
    if (watchdog_err != ESP_OK) {
        if (scan_lock(handle)) {
            handle->deleting = false;
            scan_unlock(handle);
        }
        return watchdog_err;
    }
    bool already_idle = false;
    esp_err_t err = rehab_task_decision_scan_cancel(handle, &already_idle);
    if (err != ESP_OK) {
        if (scan_lock(handle)) {
            handle->deleting = false;
            scan_unlock(handle);
        }
        return err;
    }
    bool worker_exists = false;
    if (scan_lock(handle)) {
        worker_exists = handle->worker_task != NULL;
        scan_unlock(handle);
    }
    if (worker_exists && xSemaphoreTake(handle->worker_done,
            pdMS_TO_TICKS(SCAN_DELETE_WAIT_MS)) != pdTRUE) {
        if (scan_lock(handle)) {
            handle->deleting = false;
            scan_unlock(handle);
        }
        return ESP_ERR_TIMEOUT;
    }
    if (scan_lock(handle)) {
        handle->worker_task = NULL;
        scan_unlock(handle);
    }
    vSemaphoreDelete(handle->worker_done);
    vSemaphoreDelete(handle->decision_watchdog_done);
    if (handle->coordinator_console_registered) {
        (void)esp_console_cmd_deregister("rehab_task_decision");
    }
    rehab_task_decision_coordinator_release(handle);
    vSemaphoreDelete(handle->selected_task_lock);
    vSemaphoreDelete(handle->car_operation_lock);
    vSemaphoreDelete(handle->lock);
    free(handle->fusion_result);
    cJSON_free(handle->last_decision_result);
    rehab_task_decision_rules_release(handle);
    free(handle);
    return ESP_OK;
}
