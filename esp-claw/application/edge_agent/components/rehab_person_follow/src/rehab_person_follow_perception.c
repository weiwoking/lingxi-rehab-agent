// SPDX-License-Identifier: Apache-2.0
#include "rehab_person_follow_priv.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"

__attribute__((weak)) esp_err_t rehab_person_follow_camera_acquire(
    rehab_follow_preview_frame_t *frame, uint32_t timeout_ms)
{
    return rehab_follow_camera_snapshot_acquire(frame, timeout_ms);
}

__attribute__((weak)) void rehab_person_follow_camera_release(
    rehab_follow_preview_frame_t *frame)
{
    rehab_follow_camera_preview_release(frame);
}

static uint32_t elapsed_ms(int64_t started_us)
{
    int64_t elapsed = esp_timer_get_time() - started_us;
    if (elapsed <= 0) {
        return 0U;
    }
    uint64_t value = (uint64_t)elapsed / 1000U;
    return value > UINT32_MAX ? UINT32_MAX : (uint32_t)value;
}

static void perception_error(rehab_person_follow_perception_result_t *result,
                             const char *stage, const char *code)
{
    strlcpy(result->error_stage, stage, sizeof(result->error_stage));
    strlcpy(result->error_code, code, sizeof(result->error_code));
}

esp_err_t rehab_person_follow_perceive(
    rehab_person_follow_handle_t handle,
    uint32_t generation,
    uint32_t sequence,
    rehab_person_follow_perception_result_t *out_result)
{
    if (handle == NULL || out_result == NULL ||
        handle->response_buffer == NULL || handle->workspace == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_result, 0, sizeof(*out_result));
    int64_t started_us = esp_timer_get_time();
    int64_t camera_started_us = started_us;
    rehab_follow_preview_frame_t frame = {0};
    bool frame_acquired = false;
    esp_err_t result = rehab_person_follow_camera_acquire(
        &frame, handle->config.camera_timeout_ms);
    if (result != ESP_OK) {
        perception_error(out_result, "camera",
                         result == ESP_ERR_NOT_FINISHED
                             ? "camera_busy" : "camera_acquire_failed");
        out_result->perception_ms = elapsed_ms(started_us);
        return result;
    }
    frame_acquired = true;
    out_result->camera_ms = elapsed_ms(camera_started_us);
    out_result->jpeg_bytes = frame.length;
    out_result->camera_generation = frame.generation;
    out_result->capture_timestamp_us = frame.timestamp_us;
    bool frame_bounds_valid = frame.data != NULL && frame.length >= 4U &&
                              frame.capacity >= frame.length;
    bool soi_valid = frame_bounds_valid &&
                     frame.data[0] == 0xffU && frame.data[1] == 0xd8U;
    bool eoi_valid = frame_bounds_valid &&
                     frame.data[frame.length - 2U] == 0xffU &&
                     frame.data[frame.length - 1U] == 0xd9U;
    ESP_LOGI("person_follow",
             "PF_JPEG actual_length=%u capacity=%u soi=%u eoi=%u",
             (unsigned)frame.length, (unsigned)frame.capacity,
             soi_valid ? 1U : 0U, eoi_valid ? 1U : 0U);
    rehab_person_follow_log_stack("camera_done");

    rehab_person_follow_request_meta_t request = {
        .generation = generation,
        .capture_timestamp_us = frame.timestamp_us,
    };
    int frame_id_length = snprintf(request.frame_id, sizeof(request.frame_id),
                                   "PF-%u-%u", (unsigned)generation,
                                   (unsigned)sequence);
    char metadata[320];
    int metadata_length = snprintf(
        metadata, sizeof(metadata),
        "{\"schema_version\":\"person_follow_request.v1\","
        "\"frame_id\":\"%s\",\"frame_generation\":%u,"
        "\"capture_timestamp_us\":%lld,"
        "\"capture_clock_domain\":\"esp_timer_monotonic\","
        "\"target_class\":\"person\"}",
        request.frame_id, (unsigned)generation,
        (long long)request.capture_timestamp_us);
    if (frame_id_length <= 0 ||
        (size_t)frame_id_length >= sizeof(request.frame_id) ||
        metadata_length <= 0 || (size_t)metadata_length >= sizeof(metadata) ||
        !frame_bounds_valid || frame.timestamp_us < 0) {
        perception_error(out_result, "camera", "jpeg_frame_invalid");
        result = ESP_ERR_INVALID_RESPONSE;
        goto cleanup;
    }
    if (!soi_valid || !eoi_valid) {
        perception_error(out_result, "camera", "jpeg_marker_invalid");
        result = ESP_ERR_INVALID_RESPONSE;
        goto cleanup;
    }
    if (frame.length > PERSON_FOLLOW_JPEG_MAX_BYTES) {
        perception_error(out_result, "camera", "jpeg_too_large");
        result = ESP_ERR_INVALID_SIZE;
        goto cleanup;
    }

    esp_err_t stage_result = rehab_person_follow_snapshot_stage(
        handle, generation, frame.data, frame.length, frame.generation,
        frame.timestamp_us, &out_result->image_stage_token);
    if (stage_result != ESP_OK) {
        ESP_LOGW("person_follow",
                 "PF_SNAPSHOT_STAGE skipped gen=%u seq=%u reason=%s",
                 (unsigned)generation, (unsigned)sequence,
                 esp_err_to_name(stage_result));
        out_result->image_stage_token = 0U;
    }

    size_t response_length = 0U;
    rehab_person_follow_log_stack("http_before");
    result = rehab_person_follow_http_post(
        handle->endpoint_url, handle->config.http_timeout_ms,
        frame.data, frame.length, metadata, handle->response_buffer,
        PERSON_FOLLOW_RESPONSE_MAX + 1U, &response_length,
        &out_result->http_status, &out_result->http_ms);
    rehab_person_follow_log_stack("http_after");
    if (result != ESP_OK) {
        perception_error(out_result, "http",
                         result == ESP_ERR_TIMEOUT ? "http_timeout"
                                                   : "http_transport_failed");
        goto cleanup;
    }

cleanup:
    if (frame_acquired) {
        rehab_person_follow_camera_release(&frame);
        frame_acquired = false;
    }
    if (result != ESP_OK) {
        rehab_person_follow_snapshot_discard_stage(
            handle, out_result->image_stage_token);
        out_result->image_stage_token = 0U;
        out_result->perception_ms = elapsed_ms(started_us);
        return result;
    }
    if (!rehab_person_follow_generation_current(handle, generation)) {
        rehab_person_follow_snapshot_discard_stage(
            handle, out_result->image_stage_token);
        out_result->image_stage_token = 0U;
        perception_error(out_result, "parse", "generation_stale");
        out_result->perception_ms = elapsed_ms(started_us);
        return ESP_ERR_INVALID_STATE;
    }
    if (out_result->http_status != 200) {
        rehab_person_follow_parse_error_code(
            handle->response_buffer, out_result->error_code,
            sizeof(out_result->error_code));
        strlcpy(out_result->error_stage, "http",
                sizeof(out_result->error_stage));
        rehab_person_follow_snapshot_discard_stage(
            handle, out_result->image_stage_token);
        out_result->image_stage_token = 0U;
        out_result->perception_ms = elapsed_ms(started_us);
        return ESP_FAIL;
    }

    rehab_person_follow_response_t *parsed = &handle->workspace->parsed;
    result = rehab_person_follow_parse_response(
        handle->response_buffer, &request, handle->config.max_detections,
        parsed, out_result->error_code, sizeof(out_result->error_code));
    if (result != ESP_OK) {
        strlcpy(out_result->error_stage, "parse",
                sizeof(out_result->error_stage));
        rehab_person_follow_snapshot_discard_stage(
            handle, out_result->image_stage_token);
        out_result->image_stage_token = 0U;
        out_result->perception_ms = elapsed_ms(started_us);
        return result;
    }
    out_result->raw_detection_count = parsed->detection_count;
    out_result->pc_inference_ms = parsed->pc_inference_ms;
    out_result->pc_total_ms = parsed->pc_total_ms;
    rehab_person_follow_detection_t *deduplicated =
        handle->workspace->deduplicated;
    out_result->dedup_detection_count = rehab_person_follow_deduplicate(
        parsed->detections, parsed->detection_count, deduplicated,
        handle->config.max_detections);

    rehab_person_follow_detection_t selected = {0};
    bool selected_valid = rehab_person_follow_select_target(
        deduplicated, out_result->dedup_detection_count,
        handle->last_selected_center_valid, handle->last_selected_center_x,
        &selected);
    out_result->zone = rehab_person_follow_classify_target(
        selected_valid, selected.center_x);
    out_result->observation.valid = true;
    out_result->observation.target_found = selected_valid;
    if (selected_valid) {
        out_result->observation.confidence = selected.confidence;
        out_result->observation.center_x = selected.center_x;
        out_result->observation.center_y = selected.center_y;
        out_result->observation.bbox_x1 = selected.x1;
        out_result->observation.bbox_y1 = selected.y1;
        out_result->observation.bbox_x2 = selected.x2;
        out_result->observation.bbox_y2 = selected.y2;
        out_result->observation.area_normalized = selected.area_normalized;
    }
    strlcpy(out_result->error_stage, "none", sizeof(out_result->error_stage));
    strlcpy(out_result->error_code, "none", sizeof(out_result->error_code));
    out_result->perception_ms = elapsed_ms(started_us);
    rehab_person_follow_log_stack("parse_done");
    (void)response_length;
    return ESP_OK;
}
