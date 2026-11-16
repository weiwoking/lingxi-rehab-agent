// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_priv.h"

#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "claw_cap.h"
#include "esp_log.h"

static const char *TAG = "rehab_decision_cap";
static rehab_task_decision_handle_t s_decision;

static esp_err_t cap_finish(cJSON *root, char *output, size_t output_size,
                            esp_err_t semantic_result)
{
    if (root == NULL) {
        return ESP_ERR_NO_MEM;
    }
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (json == NULL) {
        return ESP_ERR_NO_MEM;
    }
    size_t length = strlen(json);
    if (length >= output_size) {
        cJSON_free(json);
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(output, json, length + 1U);
    cJSON_free(json);
    return semantic_result;
}

static bool cap_empty_object(const char *input)
{
    cJSON *root = cJSON_Parse(input != NULL ? input : "{}");
    bool valid = root != NULL && cJSON_IsObject(root) &&
                 cJSON_GetArraySize(root) == 0;
    cJSON_Delete(root);
    return valid;
}

static const char *cap_confirmation_error(const char *input)
{
    cJSON *root = cJSON_Parse(input != NULL ? input : "{}");
    if (root == NULL || !cJSON_IsObject(root)) {
        cJSON_Delete(root);
        return "confirmation_required";
    }
    cJSON *confirmation = cJSON_GetObjectItemCaseSensitive(
        root, "confirmation");
    if (confirmation == NULL) {
        cJSON_Delete(root);
        return "confirmation_required";
    }
    bool valid = cJSON_GetArraySize(root) == 1 &&
                 cJSON_IsString(confirmation) &&
                 confirmation->valuestring != NULL &&
                 strcmp(confirmation->valuestring, "开始扫描") == 0;
    cJSON_Delete(root);
    return valid ? NULL : "confirmation_invalid";
}

static cJSON *cap_error_root(const char *code, esp_err_t err)
{
    cJSON *root = cJSON_CreateObject();
    if (root != NULL) {
        cJSON_AddBoolToObject(root, "accepted", false);
        cJSON_AddStringToObject(root, "code", code);
        cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
    }
    return root;
}

static void cap_add_status(cJSON *root,
                           const rehab_task_decision_status_t *status,
                           bool include_environment)
{
    cJSON_AddStringToObject(root, "schema_version",
                            "rehab.environment_scan.status.v4");
    cJSON_AddBoolToObject(root, "initialized", true);
    cJSON_AddBoolToObject(root, "boot_autostart", false);
    cJSON_AddStringToObject(root, "state_authority",
                            "device_ram_current_boot");
    cJSON_AddBoolToObject(root, "chat_history_authoritative", false);
    cJSON_AddStringToObject(root, "job_id", status->job_id);
    cJSON_AddNumberToObject(root, "generation", status->generation);
    cJSON_AddStringToObject(root, "state",
                            rehab_task_decision_state_name(status->state));
    cJSON_AddBoolToObject(root, "worker_active", status->worker_active);
    cJSON_AddBoolToObject(root, "perception_in_flight",
                          status->perception_in_flight);
    cJSON_AddBoolToObject(root, "cancel_requested",
                          status->cancel_requested);
    cJSON_AddBoolToObject(root, "fusion_ready", status->fusion_ready);
    cJSON_AddBoolToObject(root, "final_state_frozen",
                          status->final_state_frozen);
    cJSON_AddBoolToObject(root, "deadline_exceeded",
                          status->deadline_exceeded);
    cJSON_AddBoolToObject(root, "start_ack_requested",
                          status->start_ack_requested);
    cJSON_AddBoolToObject(root, "start_ack_attempted",
                          status->start_ack_attempted);
    cJSON_AddBoolToObject(root, "start_ack_sent",
                          status->start_ack_sent);
    cJSON_AddStringToObject(root, "start_ack_error",
                            status->start_ack_error);
    cJSON_AddBoolToObject(root, "notification_requested",
                          status->notification_requested);
    cJSON_AddBoolToObject(root, "notification_attempted",
                          status->notification_attempted);
    cJSON_AddBoolToObject(root, "notification_sent",
                          status->notification_sent);
    cJSON_AddStringToObject(root, "notification_error",
                            status->notification_error);
    cJSON_AddBoolToObject(root, "decision_trigger_requested",
                          status->decision_trigger_requested);
    cJSON_AddBoolToObject(root, "decision_trigger_attempted",
                          status->decision_trigger_attempted);
    cJSON_AddBoolToObject(root, "decision_trigger_published",
                          status->decision_trigger_published);
    cJSON_AddStringToObject(root, "decision_trigger_error",
                            status->decision_trigger_error);
    cJSON_AddNumberToObject(root, "decision_started_at",
                            (double)status->decision_started_at_ms);
    cJSON_AddNumberToObject(root, "decision_deadline_ms",
                            status->decision_deadline_ms);
    cJSON_AddBoolToObject(root, "selected_task_available",
                          status->selected_task_available);
    cJSON_AddNumberToObject(root, "started_at", (double)status->started_at_ms);
    cJSON_AddStringToObject(root, "clock", "monotonic_ms_since_boot");
    cJSON_AddNumberToObject(root, "elapsed_ms", status->elapsed_ms);
    cJSON_AddNumberToObject(root, "deadline_ms", status->deadline_ms);
    cJSON_AddNumberToObject(root, "planned_observation_count",
                            status->planned_observation_count);
    cJSON_AddNumberToObject(root, "current_step", status->current_step);
    cJSON_AddNumberToObject(root, "requested_observation_count",
                            status->requested_observation_count);
    cJSON_AddNumberToObject(root, "completed_observation_count",
                            status->completed_observation_count);
    cJSON_AddNumberToObject(root, "object_observation_count",
                            status->object_observation_count);
    cJSON_AddNumberToObject(root, "empty_observation_count",
                            status->empty_observation_count);
    cJSON_AddNumberToObject(root, "technical_failure_count",
                            status->technical_failure_count);
    cJSON_AddStringToObject(root, "observation_count_invariant",
                            "completed=object+empty+technical_failure");
    cJSON_AddNumberToObject(root, "valid_observation_count",
                            status->valid_observation_count);
    cJSON_AddNumberToObject(root, "rejected_observation_count",
                            status->rejected_observation_count);
    cJSON_AddNumberToObject(root, "last_rejection_step",
                            status->last_rejection_step);
    cJSON_AddStringToObject(root, "last_rejection_stage",
                            status->last_rejection_stage);
    cJSON_AddStringToObject(root, "last_rejection_error",
                            status->last_rejection_error);
    cJSON_AddNumberToObject(root, "last_rejection_http_status",
                            status->last_rejection_http_status);
    cJSON_AddStringToObject(root, "last_capture_id",
                            status->last_capture_id);
    cJSON_AddStringToObject(root, "last_error", status->last_error);
    cJSON_AddStringToObject(root, "last_stop_result",
                            esp_err_to_name(status->last_stop_result));
    cJSON_AddStringToObject(root, "car_owner",
                            rehab_car_control_owner_name(status->car_owner));

    cJSON *rejections = cJSON_AddObjectToObject(root, "rejection_reasons");
    if (rejections != NULL) {
        cJSON_AddNumberToObject(rejections, "no_detections",
                                status->rejection_no_detections_count);
        cJSON_AddNumberToObject(rejections, "aggregate_capacity",
                                status->rejection_aggregate_capacity_count);
        cJSON_AddNumberToObject(rejections, "capture_failed",
                                status->rejection_capture_failed_count);
        cJSON_AddNumberToObject(rejections, "fusion_timeout",
                                status->rejection_fusion_timeout_count);
        cJSON_AddNumberToObject(rejections, "fusion_invalid",
                                status->rejection_fusion_invalid_count);
        cJSON_AddNumberToObject(rejections, "late_result",
                                status->rejection_late_result_count);
    }
    if (!include_environment) {
        return;
    }

    cJSON *summary = cJSON_AddObjectToObject(root, "summary");
    if (summary != NULL) {
        cJSON_AddNumberToObject(summary, "class_count", status->class_count);
        cJSON_AddStringToObject(summary, "identity_semantics",
            "occurrence_counts_only_not_unique_objects");
        cJSON_AddStringToObject(summary, "height_semantics",
            "placeholder_observation_only_not_used_for_decision");
    }
    cJSON *classes = cJSON_AddArrayToObject(root, "environment_summary");
    if (classes == NULL) {
        return;
    }
    cJSON *observations = cJSON_AddArrayToObject(root,
                                                 "observations");
    if (observations != NULL) {
        for (size_t i = 0; i < status->observation_summary_count; ++i) {
            const rehab_environment_observation_summary_t *item =
                &status->observations[i];
            cJSON *entry = cJSON_CreateObject();
            if (entry == NULL) {
                break;
            }
            cJSON_AddItemToArray(observations, entry);
            cJSON_AddStringToObject(entry, "capture_id", item->capture_id);
            cJSON_AddNumberToObject(entry, "observed_at",
                                    (double)item->observed_at_ms);
            cJSON_AddStringToObject(entry, "clock",
                                    "monotonic_ms_since_boot");
            cJSON_AddNumberToObject(entry, "step", item->step);
            cJSON_AddBoolToObject(entry, "valid", item->valid);
            cJSON_AddBoolToObject(entry, "pairing_valid",
                                  item->pairing_valid);
            cJSON_AddStringToObject(entry, "pc_status",
                                    item->cloud_status);
            cJSON_AddBoolToObject(entry, "pc_valid", item->cloud_valid);
            cJSON_AddNumberToObject(entry, "detection_count",
                                    item->detection_count);
            cJSON_AddStringToObject(entry, "result_class",
                                    item->result_class);
            cJSON_AddStringToObject(entry, "error_stage",
                                    item->error_stage);
            cJSON_AddStringToObject(entry, "error_code",
                                    item->error_code);
            cJSON_AddNumberToObject(entry, "http_status",
                                    item->http_status);
        }
    }
    for (size_t i = 0; i < status->class_count; ++i) {
        const rehab_environment_class_summary_t *item = &status->classes[i];
        cJSON *entry = cJSON_CreateObject();
        if (entry == NULL) {
            break;
        }
        cJSON_AddItemToArray(classes, entry);
        cJSON_AddStringToObject(entry, "class_name", item->class_name);
        cJSON_AddNumberToObject(entry, "seen_occurrences", item->seen_count);
        cJSON_AddStringToObject(entry, "observation_state",
            item->seen_count > 1 ? "observed_repeatedly" :
                                   "observed_once");
        cJSON_AddNumberToObject(entry, "confidence_samples",
                                item->confidence_count);
        cJSON_AddNumberToObject(entry, "max_confidence",
                                item->max_confidence);
        cJSON_AddNumberToObject(entry, "valid_distance_samples",
                                item->valid_distance_count);
        cJSON_AddNumberToObject(entry, "last_distance_cm",
                                item->last_distance_cm);
        cJSON_AddNumberToObject(entry, "min_distance_cm",
                                item->min_distance_cm);
        cJSON_AddNumberToObject(entry, "max_distance_cm",
                                item->max_distance_cm);
        cJSON_AddNumberToObject(entry, "mean_distance_cm",
                                item->mean_distance_cm);
        cJSON *zones = cJSON_AddObjectToObject(entry, "zones");
        if (zones != NULL) {
            cJSON_AddNumberToObject(zones, "left", item->left_count);
            cJSON_AddNumberToObject(zones, "center", item->center_count);
            cJSON_AddNumberToObject(zones, "right", item->right_count);
            cJSON_AddNumberToObject(zones, "unknown",
                                    item->unknown_zone_count);
        }
        cJSON_AddNumberToObject(entry, "first_seen_at",
                                (double)item->first_seen_at_ms);
        cJSON_AddNumberToObject(entry, "last_seen_at",
                                (double)item->last_seen_at_ms);
        cJSON_AddStringToObject(entry, "clock",
                                "monotonic_ms_since_boot");
        cJSON *sources = cJSON_AddArrayToObject(entry, "source_capture_ids");
        if (sources != NULL) {
            for (uint8_t source = 0;
                 source < item->source_capture_count; ++source) {
                cJSON_AddItemToArray(sources,
                    cJSON_CreateString(item->source_capture_ids[source]));
            }
        }
        cJSON_AddBoolToObject(entry, "source_capture_overflow",
                              item->source_capture_overflow);
        cJSON_AddBoolToObject(entry, "object_height_valid",
                              item->last_object_height_valid);
        cJSON_AddNumberToObject(entry, "object_height_cm",
                                item->last_object_height_cm);
        cJSON_AddBoolToObject(entry, "bottom_height_valid",
                              item->last_bottom_height_valid);
        cJSON_AddNumberToObject(entry, "bottom_height_cm",
                                item->last_bottom_height_cm);
        cJSON_AddBoolToObject(entry, "geometry_quality_valid",
                              item->last_geometry_quality_valid);
        cJSON_AddNumberToObject(entry, "geometry_quality",
                                item->last_geometry_quality);
        cJSON_AddNumberToObject(entry, "tof_zone_count",
                                item->last_tof_zone_count);
        cJSON *tof_zones = cJSON_AddArrayToObject(entry,
                                                  "source_tof_zone_ids");
        if (tof_zones != NULL) {
            for (uint8_t zone_id = 0; zone_id < 64U; ++zone_id) {
                if ((item->source_tof_zone_mask &
                     (UINT64_C(1) << zone_id)) != 0) {
                    cJSON_AddItemToArray(tof_zones,
                                         cJSON_CreateNumber(zone_id));
                }
            }
        }
        cJSON_AddNumberToObject(entry, "unique_tof_zone_count",
                                item->unique_tof_zone_count);
        cJSON_AddBoolToObject(entry, "source_tof_zone_overflow",
                              item->source_tof_zone_overflow);
    }
}

static esp_err_t scan_status_execute(const char *input,
                                     const claw_cap_call_context_t *ctx,
                                     char *output, size_t output_size)
{
    if (s_decision == NULL) {
        return cap_finish(cap_error_root("not_initialized",
                                         ESP_ERR_INVALID_STATE),
                          output, output_size, ESP_ERR_INVALID_STATE);
    }
    if (!cap_empty_object(input)) {
        return cap_finish(cap_error_root("empty_object_required",
                                         ESP_ERR_INVALID_ARG),
                          output, output_size, ESP_ERR_INVALID_ARG);
    }
    rehab_task_decision_status_t *status = calloc(1, sizeof(*status));
    if (status == NULL) {
        return cap_finish(cap_error_root("status_unavailable",
                                         ESP_ERR_NO_MEM),
                          output, output_size, ESP_ERR_NO_MEM);
    }
    esp_err_t err = rehab_task_decision_scan_get_status(s_decision, status);
    if (err != ESP_OK) {
        free(status);
        return cap_finish(cap_error_root("status_unavailable", err),
                          output, output_size, err);
    }
    cJSON *root = cJSON_CreateObject();
    if (root != NULL) {
        cap_add_status(root, status, true);
    }
    free(status);
    return cap_finish(root, output, output_size, ESP_OK);
}

static esp_err_t scan_start_execute(const char *input,
                                    const claw_cap_call_context_t *ctx,
                                    char *output, size_t output_size)
{
    if (s_decision == NULL) {
        return cap_finish(cap_error_root("not_initialized",
                                         ESP_ERR_INVALID_STATE),
                          output, output_size, ESP_ERR_INVALID_STATE);
    }
    const char *confirmation_error = cap_confirmation_error(input);
    if (confirmation_error != NULL) {
        return cap_finish(cap_error_root(confirmation_error,
                                         ESP_ERR_INVALID_ARG),
                          output, output_size, ESP_ERR_INVALID_ARG);
    }
    const char *source_channel = NULL;
    const char *source_chat_id = NULL;
    if (ctx != NULL &&
        (ctx->caller == CLAW_CAP_CALLER_AGENT ||
         ctx->caller == CLAW_CAP_CALLER_SUB_AGENT) &&
        ctx->channel != NULL && ctx->chat_id != NULL) {
        source_channel = ctx->channel;
        source_chat_id = ctx->chat_id;
    }
    esp_err_t err = rehab_task_decision_scan_start(
        s_decision, source_channel, source_chat_id);
    rehab_task_decision_status_t *status = calloc(1, sizeof(*status));
    if (status == NULL) {
        return cap_finish(cap_error_root("status_unavailable",
                                         ESP_ERR_NO_MEM),
                          output, output_size, ESP_ERR_NO_MEM);
    }
    esp_err_t status_err = rehab_task_decision_scan_get_status(s_decision,
                                                                status);
    if (status_err != ESP_OK) {
        free(status);
        return cap_finish(cap_error_root("status_unavailable", status_err),
                          output, output_size, status_err);
    }
    cJSON *root = err == ESP_OK ? cJSON_CreateObject()
                                : cap_error_root(status->last_error, err);
    if (root != NULL) {
        if (err == ESP_OK) {
            cJSON_AddBoolToObject(root, "accepted", true);
            cJSON_AddStringToObject(root, "code", "started");
            cJSON_AddStringToObject(root, "message",
                                    "start request accepted; rely on deterministic device notifications for start and completion state");
            cJSON_AddBoolToObject(root, "suppress_duplicate_start_reply",
                                  true);
            cJSON_AddBoolToObject(root, "boot_autostart", false);
        }
        cap_add_status(root, status, false);
    }
    free(status);
    return cap_finish(root, output, output_size, err);
}

static esp_err_t scan_cancel_execute(const char *input,
                                     const claw_cap_call_context_t *ctx,
                                     char *output, size_t output_size)
{
    (void)ctx;
    if (s_decision == NULL) {
        return cap_finish(cap_error_root("not_initialized",
                                         ESP_ERR_INVALID_STATE),
                          output, output_size, ESP_ERR_INVALID_STATE);
    }
    if (!cap_empty_object(input)) {
        return cap_finish(cap_error_root("empty_object_required",
                                         ESP_ERR_INVALID_ARG),
                          output, output_size, ESP_ERR_INVALID_ARG);
    }
    bool already_idle = false;
    esp_err_t err = rehab_task_decision_scan_cancel(s_decision,
                                                     &already_idle);
    if (err != ESP_OK) {
        return cap_finish(cap_error_root("stop_failed", err),
                          output, output_size, err);
    }
    rehab_task_decision_status_t *status = calloc(1, sizeof(*status));
    if (status == NULL) {
        return cap_finish(cap_error_root("status_unavailable",
                                         ESP_ERR_NO_MEM),
                          output, output_size, ESP_ERR_NO_MEM);
    }
    err = rehab_task_decision_scan_get_status(s_decision, status);
    if (err != ESP_OK) {
        free(status);
        return cap_finish(cap_error_root("status_unavailable", err),
                          output, output_size, err);
    }
    cJSON *root = cJSON_CreateObject();
    if (root != NULL) {
        cJSON_AddBoolToObject(root, "accepted", true);
        cJSON_AddStringToObject(root, "code",
            already_idle ? "already_idle" :
            (status->worker_active ? "cancellation_requested" :
                                     "cancelled"));
        cJSON_AddBoolToObject(root, "stop_attempted", !already_idle);
        cJSON_AddBoolToObject(root, "owner_release_attempted", !already_idle);
        cap_add_status(root, status, false);
    }
    free(status);
    return cap_finish(root, output, output_size, ESP_OK);
}

static esp_err_t decision_selected_get_execute(
    const char *input,
    const claw_cap_call_context_t *ctx,
    char *output,
    size_t output_size)
{
    (void)ctx;
    if (s_decision == NULL) {
        return cap_finish(cap_error_root("not_initialized",
                                         ESP_ERR_INVALID_STATE),
                          output, output_size, ESP_ERR_INVALID_STATE);
    }
    if (!cap_empty_object(input)) {
        return cap_finish(cap_error_root("empty_object_required",
                                         ESP_ERR_INVALID_ARG),
                          output, output_size, ESP_ERR_INVALID_ARG);
    }
    return rehab_task_decision_selected_render(s_decision, output,
                                                output_size);
}

static esp_err_t decision_selected_clear_execute(
    const char *input,
    const claw_cap_call_context_t *ctx,
    char *output,
    size_t output_size)
{
    (void)ctx;
    if (s_decision == NULL) {
        return cap_finish(cap_error_root("not_initialized",
                                         ESP_ERR_INVALID_STATE),
                          output, output_size, ESP_ERR_INVALID_STATE);
    }
    if (!cap_empty_object(input)) {
        return cap_finish(cap_error_root("empty_object_required",
                                         ESP_ERR_INVALID_ARG),
                          output, output_size, ESP_ERR_INVALID_ARG);
    }
    rehab_task_decision_status_t *status = calloc(1, sizeof(*status));
    if (status == NULL) {
        return cap_finish(cap_error_root("status_unavailable",
                                         ESP_ERR_NO_MEM),
                          output, output_size, ESP_ERR_NO_MEM);
    }
    esp_err_t err = rehab_task_decision_scan_get_status(s_decision, status);
    bool busy = err == ESP_OK &&
        (status->worker_active ||
         status->state == REHAB_TASK_DECISION_STATE_DECIDING ||
         status->state == REHAB_TASK_DECISION_STATE_VALIDATING ||
         status->state == REHAB_TASK_DECISION_STATE_COMMITTING);
    uint32_t generation = status->generation;
    free(status);
    if (err != ESP_OK) {
        return cap_finish(cap_error_root("status_unavailable", err),
                          output, output_size, err);
    }
    if (busy) {
        return cap_finish(cap_error_root("decision_busy",
                                         ESP_ERR_INVALID_STATE),
                          output, output_size, ESP_ERR_INVALID_STATE);
    }
    err = rehab_task_decision_fail_to_idle(s_decision, generation,
                                           "selected_task_cleared");
    cJSON *root = err == ESP_OK ? cJSON_CreateObject()
                                : cap_error_root("selected_task_clear_failed",
                                                 err);
    if (root != NULL && err == ESP_OK) {
        cJSON_AddBoolToObject(root, "cleared", true);
        cJSON_AddBoolToObject(root, "available", false);
    }
    return cap_finish(root, output, output_size, err);
}

static const claw_cap_descriptor_t s_descriptors[] = {
    {
        .id = "rehab_task_decision_scan_start",
        .name = "rehab_task_decision_scan_start",
        .family = "rehab_task_decision",
        .description = "Start one bounded local environment scan only after a distinct second user confirmation. The capability synchronously attempts the direct IM start acknowledgement before releasing the worker. When suppress_duplicate_start_reply=true, never generate another 'scan started' status reply; use only the neutral accepted wording if a text reply is unavoidable.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_RESTRICTED |
                     CLAW_CAP_FLAG_ROOT_AGENT_ONLY,
        .input_schema_json = "{\"type\":\"object\",\"properties\":{\"confirmation\":{\"type\":\"string\",\"enum\":[\"开始扫描\"]}},\"required\":[\"confirmation\"],\"additionalProperties\":false}",
        .execute = scan_start_execute,
    },
    {
        .id = "rehab_task_decision_scan_status",
        .name = "rehab_task_decision_scan_status",
        .family = "rehab_task_decision",
        .description = "Read the authoritative in-memory device scan status without motion; if this call fails, report status as unavailable and never guess from chat history.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = 0,
        .input_schema_json = "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = scan_status_execute,
    },
    {
        .id = "rehab_task_decision_scan_cancel",
        .name = "rehab_task_decision_scan_cancel",
        .family = "rehab_task_decision",
        .description = "Request STOP and owner release for the active environment scan; never infer cancellation success from chat history.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_RESTRICTED |
                     CLAW_CAP_FLAG_ROOT_AGENT_ONLY,
        .input_schema_json = "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = scan_cancel_execute,
    },
    {
        .id = "rehab_task_decision_selected_get",
        .name = "rehab_task_decision_selected_get",
        .family = "rehab_task_decision",
        .description = "Read the authoritative selected_task.json without consuming or deleting it; returns available=false when no valid selected task exists.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_ROOT_AGENT_ONLY,
        .input_schema_json = "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = decision_selected_get_execute,
    },
    {
        .id = "rehab_task_decision_selected_clear",
        .name = "rehab_task_decision_selected_clear",
        .family = "rehab_task_decision",
        .description = "Explicitly invalidate the current selected task when no scan or decision is active; it does not consume a training task.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_RESTRICTED |
                     CLAW_CAP_FLAG_ROOT_AGENT_ONLY,
        .input_schema_json = "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = decision_selected_clear_execute,
    },
};

static const claw_cap_group_t s_group = {
    .group_id = "rehab_task_decision",
    .plugin_name = "Rehab Task Decision",
    .version = "4",
    .descriptors = s_descriptors,
    .descriptor_count = sizeof(s_descriptors) / sizeof(s_descriptors[0]),
};

esp_err_t rehab_task_decision_register_capabilities(
    rehab_task_decision_handle_t handle)
{
    if (handle == NULL || s_decision != NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    s_decision = handle;
    esp_err_t err = claw_cap_register_group(&s_group);
    if (err != ESP_OK) {
        s_decision = NULL;
        return err;
    }
    ESP_LOGI(TAG, "registered group=%s visibility=internal_only",
             s_group.group_id);
    err = rehab_task_decision_coordinator_register_capabilities(handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "coordinator capability registration failed: %s",
                 esp_err_to_name(err));
        return err;
    }
    return ESP_OK;
}
