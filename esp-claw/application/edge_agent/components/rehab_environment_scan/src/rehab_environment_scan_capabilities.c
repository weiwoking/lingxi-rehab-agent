// SPDX-License-Identifier: Apache-2.0
#include "rehab_environment_scan.h"

#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "claw_cap.h"
#include "esp_log.h"

static const char *TAG = "env_scan_cap";
static rehab_environment_scan_handle_t s_environment_scan;

static bool input_is_empty_object(const char *input)
{
    cJSON *root = cJSON_Parse(input != NULL ? input : "{}");
    const bool valid = root != NULL && cJSON_IsObject(root) &&
                       cJSON_GetArraySize(root) == 0;
    cJSON_Delete(root);
    return valid;
}

static esp_err_t render_json(cJSON *root, char *output, size_t output_size)
{
    if (root == NULL || output == NULL || output_size == 0) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }

    char *rendered = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (rendered == NULL) {
        return ESP_ERR_NO_MEM;
    }

    const size_t required = strlen(rendered) + 1;
    if (required > output_size) {
        cJSON_free(rendered);
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(output, rendered, required);
    cJSON_free(rendered);
    return ESP_OK;
}

static esp_err_t render_call_error(char *output, size_t output_size,
                                   const char *code, esp_err_t err)
{
    cJSON *root = cJSON_CreateObject();
    if (root == NULL ||
        cJSON_AddBoolToObject(root, "ok", false) == NULL ||
        cJSON_AddBoolToObject(root, "accepted", false) == NULL ||
        cJSON_AddBoolToObject(root, "busy", false) == NULL ||
        cJSON_AddStringToObject(root, "code", code) == NULL ||
        cJSON_AddStringToObject(root, "error", esp_err_to_name(err)) == NULL) {
        cJSON_Delete(root);
        return ESP_ERR_NO_MEM;
    }
    const esp_err_t render_err = render_json(root, output, output_size);
    return render_err == ESP_OK ? err : render_err;
}

static esp_err_t render_snapshot(const char *action,
                                 const char *reason,
                                 esp_err_t coordinator_err,
                                 bool accepted,
                                 bool busy,
                                 bool already_idle,
                                 const rehab_environment_scan_status_t *status,
                                 char *output,
                                 size_t output_size)
{
    cJSON *root = cJSON_CreateObject();
    if (root == NULL ||
        cJSON_AddStringToObject(root, "schema_version",
                               "rehab.environment_scan.v1") == NULL ||
        cJSON_AddStringToObject(root, "action", action) == NULL ||
        cJSON_AddBoolToObject(root, "ok", coordinator_err == ESP_OK) == NULL ||
        cJSON_AddBoolToObject(root, "accepted", accepted) == NULL ||
        cJSON_AddBoolToObject(root, "busy", busy) == NULL ||
        cJSON_AddBoolToObject(root, "already_idle", already_idle) == NULL ||
        cJSON_AddStringToObject(root, "error",
                               esp_err_to_name(coordinator_err)) == NULL) {
        cJSON_Delete(root);
        return ESP_ERR_NO_MEM;
    }

    if (status != NULL) {
        const char *snapshot_reason = reason;
        if (coordinator_err != ESP_OK && status->last_error[0] != '\0') {
            snapshot_reason = status->last_error;
        }
        if (cJSON_AddStringToObject(
                root, "state",
                rehab_environment_scan_state_name(status->state)) == NULL ||
            cJSON_AddBoolToObject(root, "active", status->active) == NULL ||
            cJSON_AddBoolToObject(root, "cancel_requested",
                                  status->cancel_requested) == NULL ||
            cJSON_AddStringToObject(root, "scan_id", status->scan_id) == NULL ||
            cJSON_AddNumberToObject(root, "generation",
                                    status->generation) == NULL ||
            cJSON_AddNumberToObject(root, "warmup_done",
                                    status->warmup_completed) == NULL ||
            cJSON_AddNumberToObject(root, "warmup_total",
                                    status->warmup_total) == NULL ||
            cJSON_AddNumberToObject(root, "directions_done",
                                    status->directions_completed) == NULL ||
            cJSON_AddNumberToObject(root, "directions_total",
                                    status->directions_total) == NULL ||
            cJSON_AddNumberToObject(root, "direction_index",
                                    status->direction_index) == NULL ||
            cJSON_AddStringToObject(
                root, "upload_state",
                rehab_scan_transfer_state_name(status->upload_state)) == NULL ||
            cJSON_AddNumberToObject(root, "package_length",
                                    status->package_length) == NULL ||
            cJSON_AddStringToObject(root, "package_sha256",
                                    status->package_sha256) == NULL ||
            cJSON_AddStringToObject(root, "scene_result_path",
                                    status->scene_result_path) == NULL ||
            cJSON_AddBoolToObject(root, "retryable", status->retryable) == NULL ||
            cJSON_AddNumberToObject(root, "last_error_code",
                                    status->last_error_code) == NULL ||
            cJSON_AddStringToObject(root, "reason",
                                    snapshot_reason != NULL
                                        ? snapshot_reason : "") == NULL) {
            cJSON_Delete(root);
            return ESP_ERR_NO_MEM;
        }
    } else if (cJSON_AddStringToObject(
                   root, "reason", reason != NULL ? reason : "") == NULL) {
        cJSON_Delete(root);
        return ESP_ERR_NO_MEM;
    }

    const esp_err_t render_err = render_json(root, output, output_size);
    return render_err == ESP_OK ? coordinator_err : render_err;
}

static esp_err_t start_execute(const char *input,
                               const claw_cap_call_context_t *ctx,
                               char *output,
                               size_t output_size)
{
    (void)ctx;
    if (!input_is_empty_object(input)) {
        return render_call_error(output, output_size,
                                 "empty_object_required",
                                 ESP_ERR_INVALID_ARG);
    }

    rehab_environment_scan_status_t status = {0};
    bool busy = false;
    const esp_err_t err = rehab_environment_scan_start(
        s_environment_scan, &busy, &status);
    return render_snapshot("start",
                           busy ? "already_running" : "started",
                           err,
                           err == ESP_OK && !busy,
                           busy,
                           false,
                           &status,
                           output,
                           output_size);
}

static esp_err_t status_execute(const char *input,
                                const claw_cap_call_context_t *ctx,
                                char *output,
                                size_t output_size)
{
    (void)ctx;
    if (!input_is_empty_object(input)) {
        return render_call_error(output, output_size,
                                 "empty_object_required",
                                 ESP_ERR_INVALID_ARG);
    }

    rehab_environment_scan_status_t status = {0};
    const esp_err_t err = rehab_environment_scan_get_status(
        s_environment_scan, &status);
    return render_snapshot("status",
                           err == ESP_OK ? "current_snapshot"
                                         : "status_unavailable",
                           err,
                           false,
                           err == ESP_OK && status.active,
                           err == ESP_OK && !status.active,
                           err == ESP_OK ? &status : NULL,
                           output,
                           output_size);
}

static esp_err_t cancel_execute(const char *input,
                                const claw_cap_call_context_t *ctx,
                                char *output,
                                size_t output_size)
{
    (void)ctx;
    if (!input_is_empty_object(input)) {
        return render_call_error(output, output_size,
                                 "empty_object_required",
                                 ESP_ERR_INVALID_ARG);
    }

    rehab_environment_scan_status_t status = {0};
    bool already_idle = false;
    const esp_err_t err = rehab_environment_scan_cancel(
        s_environment_scan, &already_idle, &status);
    return render_snapshot("cancel",
                           already_idle ? "already_inactive"
                                        : "cancel_requested",
                           err,
                           err == ESP_OK && !already_idle,
                           false,
                           already_idle,
                           &status,
                           output,
                           output_size);
}

static const claw_cap_descriptor_t s_descriptors[] = {
    {
        .id = "rehab_environment_scan_start",
        .name = "rehab_environment_scan_start",
        .family = "rehab_environment_scan",
        .description = "Start environment collection, upload, and analysis asynchronously. This tool never selects a rehabilitation task. If a scan is already active, return its current progress without starting another worker.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM |
                     CLAW_CAP_FLAG_RESTRICTED |
                     CLAW_CAP_FLAG_ROOT_AGENT_ONLY,
        .input_schema_json =
        "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = start_execute,
    },
    {
        .id = "rehab_environment_scan_status",
        .name = "rehab_environment_scan_status",
        .family = "rehab_environment_scan",
        .description = "Return environment collection and upload progress only, including scan id, warmup and capture state. This is not task-decision status and never selects a rehabilitation task.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM,
        .input_schema_json =
        "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = status_execute,
    },
    {
        .id = "rehab_environment_scan_cancel",
        .name = "rehab_environment_scan_cancel",
        .family = "rehab_environment_scan",
        .description = "Cancel environment collection only. This tool does not manage task decisions and never deletes prior scan or selected-task data.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM |
                     CLAW_CAP_FLAG_RESTRICTED |
                     CLAW_CAP_FLAG_ROOT_AGENT_ONLY,
        .input_schema_json =
        "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = cancel_execute,
    },
};

static const claw_cap_group_t s_group = {
    .group_id = "rehab_environment_scan",
    .plugin_name = "Rehab Environment Scan",
    .version = "1",
    .descriptors = s_descriptors,
    .descriptor_count = sizeof(s_descriptors) / sizeof(s_descriptors[0]),
};

esp_err_t rehab_environment_scan_register_capabilities(
    rehab_environment_scan_handle_t handle)
{
    if (handle == NULL || s_environment_scan != NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    s_environment_scan = handle;
    const esp_err_t err = claw_cap_register_group(&s_group);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "capability registration failed: %s",
                 esp_err_to_name(err));
        s_environment_scan = NULL;
        return err;
    }

    ESP_LOGI(TAG, "registered environment-scan capability group count=%u",
             (unsigned)s_group.descriptor_count);
    return ESP_OK;
}
