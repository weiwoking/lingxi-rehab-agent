// SPDX-License-Identifier: Apache-2.0
#include "rehab_target_follow_priv.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "claw_cap.h"
#include "esp_log.h"

static const char *TAG = "target_follow_cap";
static rehab_target_follow_handle_t s_follow;

static esp_err_t cap_write(char *output, size_t output_size,
                           const char *format, ...)
{
    va_list args;
    va_start(args, format);
    int written = vsnprintf(output, output_size, format, args);
    va_end(args);
    return written >= 0 && (size_t)written < output_size
        ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

static bool cap_empty_object(const char *input)
{
    cJSON *root = cJSON_Parse(input != NULL ? input : "{}");
    bool valid = root != NULL && cJSON_IsObject(root) &&
                 cJSON_GetArraySize(root) == 0;
    cJSON_Delete(root);
    return valid;
}

static esp_err_t cap_error(char *output, size_t output_size,
                           const char *code, esp_err_t err)
{
    esp_err_t render = cap_write(output, output_size,
        "{\"accepted\":false,\"code\":\"%s\",\"error\":\"%s\"}",
        code, esp_err_to_name(err));
    return render == ESP_OK ? err : render;
}

static esp_err_t follow_status_execute(const char *input,
                                       const claw_cap_call_context_t *ctx,
                                       char *output, size_t output_size)
{
    (void)ctx;
    if (!cap_empty_object(input)) {
        return cap_error(output, output_size, "empty_object_required",
                         ESP_ERR_INVALID_ARG);
    }
    rehab_target_follow_status_t status;
    esp_err_t err = rehab_target_follow_get_status(s_follow, &status);
    if (err != ESP_OK) {
        return cap_error(output, output_size, "status_unavailable", err);
    }
    return cap_write(output, output_size,
        "{\"schema_version\":\"rehab.target_follow.status.v1\","
        "\"target\":\"%s\",\"state\":\"%s\",\"worker_active\":%s,"
        "\"perception_in_flight\":%s,\"cancel_requested\":%s,"
        "\"capture_recovery\":%s,\"retry_delay_ms\":%u,"
        "\"car_owner\":\"%s\",\"confirm_streak\":%u,"
        "\"last_target_zone\":\"%s\",\"last_center_valid\":%s,"
        "\"last_center_x\":%.3f,\"last_error_x\":%.3f,"
        "\"last_distance_valid\":%s,\"last_distance_cm\":%.2f,"
        "\"last_action\":\"%s\",\"last_action_hold_ms\":%u,"
        "\"last_decision_reason\":\"%s\",\"overshoot\":%u,"
        "\"last_align_ms\":%u,\"align_overshoot_level\":%u,"
        "\"last_align_duration_ms\":%u,"
        "\"consecutive_perception_errors\":%u,\"last_error\":\"%s\","
        "\"root_error\":\"%s\",\"last_stop\":\"%s\"}",
        status.target,
        rehab_target_follow_state_name(status.state),
        status.worker_active ? "true" : "false",
        status.perception_in_flight ? "true" : "false",
        status.cancel_requested ? "true" : "false",
        status.capture_recovery ? "true" : "false",
        (unsigned)status.retry_delay_ms,
        rehab_car_control_owner_name(status.car_owner),
        (unsigned)status.confirm_streak, status.last_target_zone,
        status.last_center_valid ? "true" : "false",
        status.last_center_x, status.last_error_x,
        status.last_distance_valid ? "true" : "false",
        status.last_distance_cm, status.last_action,
        (unsigned)status.last_action_hold_ms, status.last_decision_reason,
        (unsigned)status.align_overshoot_level,
        (unsigned)status.last_align_duration_ms,
        (unsigned)status.align_overshoot_level,
        (unsigned)status.last_align_duration_ms,
        (unsigned)status.consecutive_perception_errors, status.last_error,
        status.root_error, esp_err_to_name(status.last_stop_result));
}

static esp_err_t follow_stop_execute(const char *input,
                                     const claw_cap_call_context_t *ctx,
                                     char *output, size_t output_size)
{
    (void)ctx;
    if (!cap_empty_object(input)) {
        return cap_error(output, output_size, "empty_object_required",
                         ESP_ERR_INVALID_ARG);
    }
    esp_err_t err = rehab_target_follow_stop(s_follow);
    if (err != ESP_OK) {
        return cap_error(output, output_size, "stop_failed", err);
    }
    rehab_target_follow_status_t status = {0};
    (void)rehab_target_follow_get_status(s_follow, &status);
    return cap_write(output, output_size,
        "{\"accepted\":true,\"code\":\"stopped\","
        "\"stop_attempted\":true,\"target\":\"%s\","
        "\"state\":\"%s\",\"worker_active\":%s,"
        "\"perception_in_flight\":%s,\"car_owner\":\"%s\","
        "\"last_stop\":\"%s\"}",
        status.target,
        rehab_target_follow_state_name(status.state),
        status.worker_active ? "true" : "false",
        status.perception_in_flight ? "true" : "false",
        rehab_car_control_owner_name(status.car_owner),
        esp_err_to_name(status.last_stop_result));
}

static esp_err_t follow_start_execute(const char *input,
                                      const claw_cap_call_context_t *ctx,
                                      char *output, size_t output_size)
{
    (void)ctx;
    if (!cap_empty_object(input)) {
        return cap_error(output, output_size, "empty_object_required",
                         ESP_ERR_INVALID_ARG);
    }
    esp_err_t err = rehab_target_follow_start(s_follow);
    if (err != ESP_OK) {
        rehab_target_follow_status_t status = {0};
        esp_err_t status_err = rehab_target_follow_get_status(s_follow, &status);
        if (status_err != ESP_OK) {
            return cap_error(output, output_size, "start_rejected", err);
        }
        const char *code = "start_rejected";
        if (status.worker_active) {
            code = "already_running";
        } else if (strcmp(status.last_error, "perception_not_ready") == 0) {
            code = "not_ready";
        } else if (status.state != REHAB_TARGET_FOLLOW_STATE_IDLE) {
            code = "state_not_idle";
        }
        esp_err_t render = cap_write(output, output_size,
            "{\"accepted\":false,\"code\":\"%s\",\"error\":\"%s\","
            "\"target\":\"%s\",\"state\":\"%s\","
            "\"worker_active\":%s,\"last_error\":\"%s\"}",
            code, esp_err_to_name(err), status.target,
            rehab_target_follow_state_name(status.state),
            status.worker_active ? "true" : "false", status.last_error);
        return render == ESP_OK ? err : render;
    }
    return cap_write(output, output_size,
        "{\"accepted\":true,\"code\":\"started\",\"state\":\"SENSE\","
        "\"target\":\"%s\",\"boot_autostart\":false}",
        FOLLOW_TARGET_CLASS_NAME);
}

static const claw_cap_descriptor_t s_safe_descriptors[] = {
    {
        .id = "follow_target_status",
        .name = "follow_target_status",
        .family = "rehab_target_follow",
        .description = "Read the local configured-target follow worker status without motion.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM,
        .input_schema_json = "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = follow_status_execute,
    },
    {
        .id = "follow_target_stop",
        .name = "follow_target_stop",
        .family = "rehab_target_follow",
        .description = "Immediately STOP the car and cooperatively cancel configured-target follow.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM |
                     CLAW_CAP_FLAG_RESTRICTED |
                     CLAW_CAP_FLAG_ROOT_AGENT_ONLY,
        .input_schema_json = "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = follow_stop_execute,
    },
};

static const claw_cap_descriptor_t s_start_descriptors[] = {
    {
        .id = "follow_target_start",
        .name = "follow_target_start",
        .family = "rehab_target_follow",
        .description = "Start the high-level local configured-target follow worker once; the worker performs all later sensing and bounded motion locally.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM |
                     CLAW_CAP_FLAG_RESTRICTED |
                     CLAW_CAP_FLAG_ROOT_AGENT_ONLY,
        .input_schema_json = "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = follow_start_execute,
    },
};

static const claw_cap_group_t s_safe_group = {
    .group_id = "rehab_target_follow_safe",
    .plugin_name = "Rehab Target Follow Safe",
    .version = "1",
    .descriptors = s_safe_descriptors,
    .descriptor_count = sizeof(s_safe_descriptors) / sizeof(s_safe_descriptors[0]),
};

static const claw_cap_group_t s_start_group = {
    .group_id = "rehab_target_follow_start",
    .plugin_name = "Rehab Target Follow Start",
    .version = "1",
    .descriptors = s_start_descriptors,
    .descriptor_count = sizeof(s_start_descriptors) / sizeof(s_start_descriptors[0]),
};

esp_err_t rehab_target_follow_register_capabilities(rehab_target_follow_handle_t handle)
{
    if (handle == NULL || s_follow != NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    s_follow = handle;
    esp_err_t err = claw_cap_register_group(&s_safe_group);
    if (err != ESP_OK) {
        s_follow = NULL;
        return err;
    }
    err = claw_cap_register_group(&s_start_group);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "start group registration failed: %s", esp_err_to_name(err));
        return err;
    }
    ESP_LOGI(TAG,
             "registered safe_group=%s start_group=%s visibility=session_skill",
             s_safe_group.group_id, s_start_group.group_id);
    return ESP_OK;
}
