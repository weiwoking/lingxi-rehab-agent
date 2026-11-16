// SPDX-License-Identifier: Apache-2.0
#include "rehab_person_follow.h"

#include <stdarg.h>
#include <stdio.h>

#include "cJSON.h"
#include "claw_cap.h"
#include "esp_log.h"

static const char *TAG = "person_follow_cap";
static rehab_person_follow_handle_t s_person_follow;

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

static esp_err_t status_execute(const char *input,
                                const claw_cap_call_context_t *ctx,
                                char *output, size_t output_size)
{
    (void)ctx;
    if (!cap_empty_object(input)) {
        return cap_error(output, output_size, "empty_object_required",
                         ESP_ERR_INVALID_ARG);
    }
    rehab_person_follow_status_t status = {0};
    esp_err_t err = rehab_person_follow_get_status(s_person_follow, &status);
    if (err != ESP_OK) {
        return cap_error(output, output_size, "status_unavailable", err);
    }
    return cap_write(output, output_size,
        "{\"schema_version\":\"rehab.person_follow.status.v1\","
        "\"initialized\":%s,\"running\":%s,\"state\":\"%s\","
        "\"generation\":%u,\"cancel_requested\":%s,"
        "\"worker_alive\":%s,\"worker_done\":%s,"
        "\"owner_acquired\":%s,\"action_enabled\":%s,"
        "\"perception_mode\":\"%s\",\"mock_mode\":\"%s\","
        "\"last_http_status\":%d,\"last_jpeg_bytes\":%u,"
        "\"last_detection_count\":%u,\"last_dedup_count\":%u,"
        "\"last_error\":\"%s\",\"last_reason\":\"%s\","
        "\"last_target_found\":%s,\"last_center_x\":%.3f,"
        "\"last_confidence\":%.3f,\"last_zone\":\"%s\","
        "\"last_perception_ms\":%u,\"last_planned_action\":\"%s\","
        "\"last_action\":\"%s\",\"last_bbox_width_ratio\":%.3f,"
        "\"last_person_center_x_ratio\":%.3f,"
        "\"last_left_background_ratio\":%.3f,"
        "\"last_right_background_ratio\":%.3f,"
        "\"approach_phase\":\"%s\","
        "\"first_align_completed\":%s,"
        "\"active_align_gate\":\"%s\","
        "\"align_stable_count\":%u,"
        "\"align_stable_required\":%u,"
        "\"first_align_trigger_frame_id\":%u,"
        "\"final_align_trigger_frame_id\":%u,"
        "\"align_turn_hold_ms\":%u,"
        "\"align_last_turn_direction\":\"%s\","
        "\"align_overshoot_count\":%u,"
        "\"final_forward_attempted_frame_id\":%u,"
        "\"arrived\":%s,\"arrival_reason\":\"%s\","
        "\"post_action_wait_ms\":%u,"
        "\"last_car_result\":\"%s\","
        "\"forward_attempted\":%s,\"motion_unconfirmed\":%s,"
        "\"blocked_unknown\":false,\"shutdown_pending\":%s,"
        "\"has_forwarded_once\":%s,\"fine_turn_mode\":%s,"
        "\"pending_target_turn_check\":%s,"
        "\"last_target_turn_action\":\"%s\","
        "\"next_turn_hold_ms\":%u,"
        "\"target_acquired_once\":%s,"
        "\"startup_observation_active\":%s,"
        "\"startup_observation_remaining_ms\":%u,"
        "\"startup_observation_exit_reason\":\"%s\","
        "\"forward_rolling\":%s,\"forward_lease_remaining_ms\":%u,"
        "\"forward_segments_completed\":%u,"
        "\"last_center_confirmed_age_ms\":%u,"
        "\"rolling_stop_reason\":\"%s\","
        "\"rolling_task_alive\":%s,\"rolling_task_busy\":%s,"
        "\"runtime_ms\":%u,"
        "\"snapshot_seq\":%llu,\"snapshot_valid\":%s,"
        "\"image_current\":%s,\"image_snapshot_seq\":%llu,"
        "\"image_format\":\"%s\",\"image_width\":%u,"
        "\"image_height\":%u,\"image_length\":%u,"
        "\"camera_generation\":%llu,\"capture_timestamp_us\":%lld,"
        "\"target_found\":%s,\"raw_count\":%u,\"dedup_count\":%u,"
        "\"selected_valid\":%s,\"selected_bbox_x1\":%.1f,"
        "\"selected_bbox_y1\":%.1f,\"selected_bbox_x2\":%.1f,"
        "\"selected_bbox_y2\":%.1f,\"selected_center_x\":%.3f,"
        "\"selected_center_y\":%.3f,\"confidence\":%.3f,"
        "\"bbox_width_ratio\":%.3f,"
        "\"person_center_x_ratio\":%.3f,"
        "\"left_background_ratio\":%.3f,"
        "\"right_background_ratio\":%.3f,"
        "\"snapshot_approach_phase\":\"%s\","
        "\"snapshot_first_align_completed\":%s,"
        "\"snapshot_active_align_gate\":\"%s\","
        "\"snapshot_align_stable_count\":%u,"
        "\"snapshot_align_stable_required\":%u,"
        "\"snapshot_first_align_trigger_frame_id\":%u,"
        "\"snapshot_final_align_trigger_frame_id\":%u,"
        "\"snapshot_align_turn_hold_ms\":%u,"
        "\"snapshot_align_last_turn_direction\":\"%s\","
        "\"snapshot_align_overshoot_count\":%u,"
        "\"snapshot_arrived\":%s,"
        "\"snapshot_arrival_reason\":\"%s\","
        "\"zone\":\"%s\",\"planned_action\":\"%s\","
        "\"actual_action\":\"%s\",\"action_reason\":\"%s\","
        "\"speed\":%u,\"hold_ms\":%u,\"snapshot_post_action_wait_ms\":%u,"
        "\"snapshot_has_forwarded_once\":%s,"
        "\"snapshot_fine_turn_mode\":%s,"
        "\"snapshot_overshoot_reverse\":%s,"
        "\"snapshot_pending_target_turn_check\":%s,"
        "\"snapshot_last_target_turn_action\":\"%s\","
        "\"snapshot_next_turn_hold_ms\":%u,"
        "\"snapshot_target_acquired_once\":%s,"
        "\"snapshot_startup_observation_active\":%s,"
        "\"snapshot_startup_observation_remaining_ms\":%u,"
        "\"snapshot_startup_observation_exit_reason\":\"%s\","
        "\"snapshot_forward_rolling\":%s,"
        "\"snapshot_forward_lease_remaining_ms\":%u,"
        "\"snapshot_forward_segments_completed\":%u,"
        "\"snapshot_last_center_confirmed_age_ms\":%u,"
        "\"snapshot_rolling_stop_reason\":\"%s\","
        "\"snapshot_rolling_task_alive\":%s,"
        "\"snapshot_rolling_task_busy\":%s,"
        "\"snapshot_owner_acquired\":%s,\"snapshot_car_result\":\"%s\","
        "\"camera_ms\":%u,\"jpeg_bytes\":%u,\"http_ms\":%u,"
        "\"http_status\":%d,\"pc_inference_ms\":%.1f,"
        "\"pc_total_ms\":%.1f,\"loop_total_ms\":%u,"
        "\"error_stage\":\"%s\",\"snapshot_error\":\"%s\","
        "\"snapshot_worker_generation\":%u,"
        "\"snapshot_cancel_requested\":%s}",
        status.initialized ? "true" : "false",
        status.running ? "true" : "false",
        rehab_person_follow_state_name(status.state),
        (unsigned)status.generation,
        status.cancel_requested ? "true" : "false",
        status.worker_alive ? "true" : "false",
        status.worker_done ? "true" : "false",
        status.owner_acquired ? "true" : "false",
        status.action_enabled ? "true" : "false",
        rehab_person_follow_perception_provider_name(
            status.perception_provider),
        rehab_person_follow_mock_mode_name(status.mock_mode),
        status.last_http_status, (unsigned)status.last_jpeg_bytes,
        (unsigned)status.last_detection_count,
        (unsigned)status.last_dedup_count,
        status.last_error, status.last_reason,
        status.last_target_found ? "true" : "false",
        status.last_center_x, status.last_confidence,
        rehab_person_follow_target_zone_name(status.last_zone),
        (unsigned)status.last_perception_ms,
        rehab_person_follow_action_name(status.last_planned_action),
        rehab_person_follow_action_name(status.last_action),
        status.last_bbox_width_ratio,
        status.last_person_center_x_ratio,
        status.last_left_background_ratio,
        status.last_right_background_ratio,
        rehab_person_follow_approach_phase_name(status.approach_phase),
        status.first_align_completed ? "true" : "false",
        rehab_person_follow_align_gate_name(status.active_align_gate),
        (unsigned)status.align_stable_count,
        (unsigned)status.align_stable_required,
        (unsigned)status.first_align_trigger_frame_id,
        (unsigned)status.final_align_trigger_frame_id,
        (unsigned)status.align_turn_hold_ms,
        rehab_person_follow_action_name(
            status.align_last_turn_direction),
        (unsigned)status.align_overshoot_count,
        (unsigned)status.final_forward_attempted_frame_id,
        status.arrived ? "true" : "false",
        rehab_person_follow_arrival_reason_name(status.arrival_reason),
        (unsigned)status.post_action_wait_ms,
        esp_err_to_name(status.last_car_result),
        status.forward_attempted ? "true" : "false",
        status.motion_unconfirmed ? "true" : "false",
        status.shutdown_pending ? "true" : "false",
        status.has_forwarded_once ? "true" : "false",
        status.fine_turn_mode ? "true" : "false",
        status.pending_target_turn_check ? "true" : "false",
        rehab_person_follow_action_name(status.last_target_turn_action),
        (unsigned)status.next_turn_hold_ms,
        status.target_acquired_once ? "true" : "false",
        status.startup_observation_active ? "true" : "false",
        (unsigned)status.startup_observation_remaining_ms,
        rehab_person_follow_startup_observation_exit_reason_name(
            status.startup_observation_exit_reason),
        status.forward_rolling ? "true" : "false",
        (unsigned)status.forward_lease_remaining_ms,
        (unsigned)status.forward_segments_completed,
        (unsigned)status.last_center_confirmed_age_ms,
        status.rolling_stop_reason,
        status.rolling_task_alive ? "true" : "false",
        status.rolling_task_busy ? "true" : "false",
        (unsigned)status.runtime_ms,
        (unsigned long long)status.snapshot.snapshot_seq,
        status.snapshot.valid ? "true" : "false",
        status.snapshot.image_current ? "true" : "false",
        (unsigned long long)status.snapshot.image_snapshot_seq,
        rehab_person_follow_snapshot_image_format_name(
            status.snapshot.image_format),
        (unsigned)status.snapshot.width,
        (unsigned)status.snapshot.height,
        (unsigned)status.snapshot.image_length,
        (unsigned long long)status.snapshot.camera_generation,
        (long long)status.snapshot.capture_timestamp_us,
        status.snapshot.target_found ? "true" : "false",
        (unsigned)status.snapshot.raw_count,
        (unsigned)status.snapshot.dedup_count,
        status.snapshot.selected_valid ? "true" : "false",
        status.snapshot.bbox_x1, status.snapshot.bbox_y1,
        status.snapshot.bbox_x2, status.snapshot.bbox_y2,
        status.snapshot.center_x, status.snapshot.center_y,
        status.snapshot.confidence,
        status.snapshot.bbox_width_ratio,
        status.snapshot.person_center_x_ratio,
        status.snapshot.left_background_ratio,
        status.snapshot.right_background_ratio,
        rehab_person_follow_approach_phase_name(
            status.snapshot.approach_phase),
        status.snapshot.first_align_completed ? "true" : "false",
        rehab_person_follow_align_gate_name(
            status.snapshot.active_align_gate),
        (unsigned)status.snapshot.align_stable_count,
        (unsigned)status.snapshot.align_stable_required,
        (unsigned)status.snapshot.first_align_trigger_frame_id,
        (unsigned)status.snapshot.final_align_trigger_frame_id,
        (unsigned)status.snapshot.align_turn_hold_ms,
        rehab_person_follow_action_name(
            status.snapshot.align_last_turn_direction),
        (unsigned)status.snapshot.align_overshoot_count,
        status.snapshot.arrived ? "true" : "false",
        rehab_person_follow_arrival_reason_name(
            status.snapshot.arrival_reason),
        rehab_person_follow_target_zone_name(status.snapshot.zone),
        rehab_person_follow_action_name(status.snapshot.planned_action),
        rehab_person_follow_action_name(status.snapshot.actual_action),
        status.snapshot.action_reason,
        (unsigned)status.snapshot.speed,
        (unsigned)status.snapshot.hold_ms,
        (unsigned)status.snapshot.post_action_wait_ms,
        status.snapshot.has_forwarded_once ? "true" : "false",
        status.snapshot.fine_turn_mode ? "true" : "false",
        status.snapshot.overshoot_reverse ? "true" : "false",
        status.snapshot.pending_target_turn_check ? "true" : "false",
        rehab_person_follow_action_name(
            status.snapshot.last_target_turn_action),
        (unsigned)status.snapshot.next_turn_hold_ms,
        status.snapshot.target_acquired_once ? "true" : "false",
        status.snapshot.startup_observation_active ? "true" : "false",
        (unsigned)status.snapshot.startup_observation_remaining_ms,
        rehab_person_follow_startup_observation_exit_reason_name(
            status.snapshot.startup_observation_exit_reason),
        status.snapshot.forward_rolling ? "true" : "false",
        (unsigned)status.snapshot.forward_lease_remaining_ms,
        (unsigned)status.snapshot.forward_segments_completed,
        (unsigned)status.snapshot.last_center_confirmed_age_ms,
        status.snapshot.rolling_stop_reason,
        status.snapshot.rolling_task_alive ? "true" : "false",
        status.snapshot.rolling_task_busy ? "true" : "false",
        status.snapshot.owner_acquired ? "true" : "false",
        esp_err_to_name(status.snapshot.last_car_result),
        (unsigned)status.snapshot.camera_ms,
        (unsigned)status.snapshot.jpeg_bytes,
        (unsigned)status.snapshot.http_ms,
        status.snapshot.http_status,
        status.snapshot.pc_inference_ms,
        status.snapshot.pc_total_ms,
        (unsigned)status.snapshot.loop_total_ms,
        status.snapshot.error_stage,
        status.snapshot.last_error,
        (unsigned)status.snapshot.worker_generation,
        status.snapshot.cancel_requested ? "true" : "false");
}

static esp_err_t start_execute(const char *input,
                               const claw_cap_call_context_t *ctx,
                               char *output, size_t output_size)
{
    (void)ctx;
    if (!cap_empty_object(input)) {
        return cap_error(output, output_size, "empty_object_required",
                         ESP_ERR_INVALID_ARG);
    }
    esp_err_t err = rehab_person_follow_start(s_person_follow);
    if (err != ESP_OK) {
        return cap_error(output, output_size, "start_rejected", err);
    }
    rehab_person_follow_status_t status = {0};
    (void)rehab_person_follow_get_status(s_person_follow, &status);
    return cap_write(output, output_size,
        "{\"accepted\":true,\"code\":\"started\","
        "\"generation\":%u,\"state\":\"%s\","
        "\"action_enabled\":%s,\"perception_provider\":\"%s\","
        "\"message\":\"person-follow started\"}",
        (unsigned)status.generation,
        rehab_person_follow_state_name(status.state),
        status.action_enabled ? "true" : "false",
        rehab_person_follow_perception_provider_name(
            status.perception_provider));
}

static esp_err_t stop_execute(const char *input,
                              const claw_cap_call_context_t *ctx,
                              char *output, size_t output_size)
{
    (void)ctx;
    if (!cap_empty_object(input)) {
        return cap_error(output, output_size, "empty_object_required",
                         ESP_ERR_INVALID_ARG);
    }
    esp_err_t err = rehab_person_follow_stop(s_person_follow);
    if (err != ESP_OK) {
        return cap_error(output, output_size, "stop_failed", err);
    }
    rehab_person_follow_status_t status = {0};
    (void)rehab_person_follow_get_status(s_person_follow, &status);
    return cap_write(output, output_size,
        "{\"accepted\":true,\"code\":\"stopped\","
        "\"stop_attempted\":true,\"state\":\"IDLE\","
        "\"action_enabled\":%s}",
        status.action_enabled ? "true" : "false");
}

static const claw_cap_descriptor_t s_safe_descriptors[] = {
    {
        .id = "person_follow_status",
        .name = "person_follow_status",
        .family = "rehab_person_follow",
        .description = "Read person-follow perception and discrete-action status.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM,
        .input_schema_json = "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = status_execute,
    },
    {
        .id = "person_follow_stop",
        .name = "person_follow_stop",
        .family = "rehab_person_follow",
        .description = "STOP-first cancellation of the independent person-perception worker.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM |
                     CLAW_CAP_FLAG_RESTRICTED |
                     CLAW_CAP_FLAG_ROOT_AGENT_ONLY,
        .input_schema_json = "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = stop_execute,
    },
};

static const claw_cap_descriptor_t s_start_descriptors[] = {
    {
        .id = "person_follow_start",
        .name = "person_follow_start",
        .family = "rehab_person_follow",
        .description = "Start camera-to-PC person-follow perception and configured discrete actions.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM |
                     CLAW_CAP_FLAG_RESTRICTED |
                     CLAW_CAP_FLAG_ROOT_AGENT_ONLY,
        .input_schema_json = "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = start_execute,
    },
};

static const claw_cap_group_t s_safe_group = {
    .group_id = "rehab_person_follow_safe",
    .plugin_name = "Rehab Person Follow Safe",
    .version = "1",
    .descriptors = s_safe_descriptors,
    .descriptor_count = sizeof(s_safe_descriptors) / sizeof(s_safe_descriptors[0]),
};

static const claw_cap_group_t s_start_group = {
    .group_id = "rehab_person_follow_start",
    .plugin_name = "Rehab Person Follow Start",
    .version = "1",
    .descriptors = s_start_descriptors,
    .descriptor_count = sizeof(s_start_descriptors) / sizeof(s_start_descriptors[0]),
};

esp_err_t rehab_person_follow_register_capabilities(
    rehab_person_follow_handle_t handle)
{
    if (handle == NULL || s_person_follow != NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    s_person_follow = handle;
    esp_err_t err = claw_cap_register_group(&s_safe_group);
    if (err != ESP_OK) {
        s_person_follow = NULL;
        return err;
    }
    err = claw_cap_register_group(&s_start_group);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "start group registration failed: %s",
                 esp_err_to_name(err));
        (void)claw_cap_unregister_group(s_safe_group.group_id, 1000U);
        s_person_follow = NULL;
        return err;
    }
    ESP_LOGI(TAG, "registered person-follow capability groups");
    return ESP_OK;
}
