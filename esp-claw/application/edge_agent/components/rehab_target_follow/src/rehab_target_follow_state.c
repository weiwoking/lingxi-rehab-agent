// SPDX-License-Identifier: Apache-2.0
#include "rehab_target_follow_priv.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

void rehab_target_follow_damping_reset(rehab_target_follow_damping_t *damping)
{
    if (damping == NULL) {
        return;
    }
    damping->last_non_center_zone = REHAB_TARGET_ZONE_NONE;
    damping->last_align_duration_ms = FOLLOW_ALIGN_BASE_MS;
    damping->align_overshoot_level = 0;
    damping->center_streak = 0;
}

void rehab_target_follow_damping_observe_center(rehab_target_follow_damping_t *damping)
{
    if (damping == NULL) {
        return;
    }
    if (damping->center_streak < UINT8_MAX) {
        ++damping->center_streak;
    }
    if (damping->center_streak >= 2U) {
        rehab_target_follow_damping_reset(damping);
    }
}

uint32_t rehab_target_follow_damping_prepare_align(
    rehab_target_follow_damping_t *damping,
    rehab_target_zone_t zone,
    uint32_t base_align_ms)
{
    if (damping == NULL ||
        (zone != REHAB_TARGET_ZONE_LEFT && zone != REHAB_TARGET_ZONE_RIGHT)) {
        return base_align_ms;
    }
    bool crossed =
        (damping->last_non_center_zone == REHAB_TARGET_ZONE_LEFT &&
         zone == REHAB_TARGET_ZONE_RIGHT) ||
        (damping->last_non_center_zone == REHAB_TARGET_ZONE_RIGHT &&
         zone == REHAB_TARGET_ZONE_LEFT);
    if (crossed) {
        if (damping->align_overshoot_level == 0U) {
            damping->align_overshoot_level = 1U;
        } else {
            damping->align_overshoot_level = 2U;
        }
    }
    uint32_t overshoot_cap_ms = FOLLOW_ALIGN_FAR_MS;
    if (damping->align_overshoot_level == 1U) {
        overshoot_cap_ms = FOLLOW_ALIGN_MEDIUM_MS;
    } else if (damping->align_overshoot_level >= 2U) {
        overshoot_cap_ms = FOLLOW_ALIGN_NEAR_MS;
    }
    damping->last_align_duration_ms = base_align_ms < overshoot_cap_ms
        ? base_align_ms : overshoot_cap_ms;
    damping->last_non_center_zone = zone;
    damping->center_streak = 0;
    return damping->last_align_duration_ms;
}

const char *rehab_target_follow_zone_name(rehab_target_zone_t zone)
{
    switch (zone) {
    case REHAB_TARGET_ZONE_LEFT:
        return "left";
    case REHAB_TARGET_ZONE_CENTER:
        return "center";
    case REHAB_TARGET_ZONE_RIGHT:
        return "right";
    default:
        return "none";
    }
}

const char *rehab_target_follow_action_name(rehab_car_direction_t direction)
{
    switch (direction) {
    case REHAB_CAR_DIRECTION_FORWARD:
        return "forward";
    case REHAB_CAR_DIRECTION_ROTATE_CCW:
        return "rotate_ccw";
    case REHAB_CAR_DIRECTION_ROTATE_CW:
        return "rotate_cw";
    default:
        return "stop";
    }
}

esp_err_t rehab_target_follow_decide_observation(
    const rehab_target_follow_observation_t *observation,
    uint32_t *confirm_streak,
    bool *hold_latched,
    rehab_target_follow_damping_t *damping,
    rehab_target_follow_decision_t *decision)
{
    if (observation == NULL || confirm_streak == NULL ||
        hold_latched == NULL || damping == NULL || decision == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(decision, 0, sizeof(*decision));
    decision->next_state = REHAB_TARGET_FOLLOW_STATE_SENSE;
    decision->direction = REHAB_CAR_DIRECTION_STOP;
    decision->reason = "undecided";

    if (!observation->found) {
        *confirm_streak = 0;
        *hold_latched = false;
        rehab_target_follow_damping_reset(damping);
        decision->next_state = REHAB_TARGET_FOLLOW_STATE_SEARCH;
        decision->direction = REHAB_CAR_DIRECTION_ROTATE_CCW;
        decision->movement_duration_ms = FOLLOW_SEARCH_ROTATE_MS;
        decision->reason = "target_not_found_search";
        return ESP_OK;
    }

    if (!observation->center_valid || !isfinite(observation->center_x) ||
        observation->center_x < 0.0f || observation->center_x > 1.0f) {
        decision->stop_required = true;
        decision->reason = "center_invalid";
        return ESP_ERR_INVALID_RESPONSE;
    }
    if ((observation->has_distance_cm &&
         (!isfinite(observation->distance_cm) ||
          observation->distance_cm <= 0.0f)) ||
        (observation->distance_valid && !observation->has_distance_cm)) {
        decision->stop_required = true;
        decision->reason = "distance_invalid";
        return ESP_ERR_INVALID_RESPONSE;
    }

    float error_x = observation->center_x - 0.5f;
    float absolute_error_x = fabsf(error_x);
    rehab_target_zone_t center_zone = REHAB_TARGET_ZONE_CENTER;
    if (error_x < -FOLLOW_CENTER_DEADBAND) {
        center_zone = REHAB_TARGET_ZONE_LEFT;
    } else if (error_x > FOLLOW_CENTER_DEADBAND) {
        center_zone = REHAB_TARGET_ZONE_RIGHT;
    }

    if (*confirm_streak < 2U) {
        ++*confirm_streak;
    }
    if (center_zone == REHAB_TARGET_ZONE_CENTER) {
        rehab_target_follow_damping_observe_center(damping);
    } else {
        damping->center_streak = 0;
    }
    if (*confirm_streak < 2U) {
        decision->next_state = REHAB_TARGET_FOLLOW_STATE_CONFIRM;
        decision->stop_required = true;
        decision->reason = "first_confirmation";
        return ESP_OK;
    }

    if (center_zone != REHAB_TARGET_ZONE_CENTER) {
        *hold_latched = false;
        decision->next_state = REHAB_TARGET_FOLLOW_STATE_ALIGN;
        decision->direction = center_zone == REHAB_TARGET_ZONE_LEFT
            ? REHAB_CAR_DIRECTION_ROTATE_CCW
            : REHAB_CAR_DIRECTION_ROTATE_CW;
        if (absolute_error_x <= FOLLOW_ALIGN_NEAR_ERROR_MAX) {
            decision->base_align_ms = FOLLOW_ALIGN_NEAR_MS;
            decision->reason = "align_near";
        } else if (absolute_error_x <= FOLLOW_ALIGN_MEDIUM_ERROR_MAX) {
            decision->base_align_ms = FOLLOW_ALIGN_MEDIUM_MS;
            decision->reason = "align_medium";
        } else {
            decision->base_align_ms = FOLLOW_ALIGN_FAR_MS;
            decision->reason = "align_far";
        }
        decision->final_align_ms = rehab_target_follow_damping_prepare_align(
            damping, center_zone, decision->base_align_ms);
        decision->movement_duration_ms = decision->final_align_ms;
        return ESP_OK;
    }

    if (!observation->distance_valid || !observation->has_distance_cm ||
        !isfinite(observation->distance_cm) ||
        observation->distance_cm <= 0.0f) {
        decision->stop_required = true;
        decision->reason = "distance_invalid";
        return ESP_ERR_INVALID_RESPONSE;
    }
    if (observation->distance_cm <= FOLLOW_STOP_DISTANCE_CM) {
        *hold_latched = true;
        decision->next_state = REHAB_TARGET_FOLLOW_STATE_HOLD;
        decision->stop_required = true;
        decision->reason = "distance_hold";
        return ESP_OK;
    }
    if (observation->distance_cm <= FOLLOW_RESUME_DISTANCE_CM) {
        decision->stop_required = true;
        decision->reason = "distance_deadband";
        if (*hold_latched) {
            decision->next_state = REHAB_TARGET_FOLLOW_STATE_HOLD;
        } else {
            decision->next_sense_delay_ms = FOLLOW_HOLD_RECHECK_MS;
        }
        return ESP_OK;
    }

    {
        *hold_latched = false;
        decision->next_state = REHAB_TARGET_FOLLOW_STATE_APPROACH;
        decision->direction = REHAB_CAR_DIRECTION_FORWARD;
        if (observation->distance_cm <= FOLLOW_APPROACH_NEAR_MAX_CM) {
            decision->movement_duration_ms = FOLLOW_APPROACH_NEAR_MS;
            decision->reason = "approach_near";
        } else if (observation->distance_cm <= FOLLOW_APPROACH_MEDIUM_MAX_CM) {
            decision->movement_duration_ms = FOLLOW_APPROACH_MEDIUM_MS;
            decision->reason = "approach_medium";
        } else {
            decision->movement_duration_ms = FOLLOW_APPROACH_FAR_MS;
            decision->reason = "approach_far";
        }
    }
    return ESP_OK;
}

rehab_target_follow_state_t rehab_target_follow_record_perception_error(
    bool retryable, uint32_t *consecutive_errors)
{
    if (consecutive_errors == NULL) {
        return REHAB_TARGET_FOLLOW_STATE_ERROR;
    }
    if (*consecutive_errors < UINT32_MAX) {
        ++*consecutive_errors;
    }
    return retryable &&
                   *consecutive_errors < FOLLOW_MAX_CONSECUTIVE_PERCEPTION_ERRORS
        ? REHAB_TARGET_FOLLOW_STATE_SENSE
        : REHAB_TARGET_FOLLOW_STATE_ERROR;
}

rehab_target_perception_result_t rehab_target_follow_classify_perception_result(
    esp_err_t err, bool result_ok, const char *last_error)
{
    if (err == ESP_OK && result_ok) {
        return REHAB_TARGET_PERCEPTION_RESULT_SUCCESS;
    }
    bool named_busy = last_error != NULL &&
        (strcmp(last_error, "capture_busy") == 0 ||
         strcmp(last_error, "packet_busy") == 0 ||
         strcmp(last_error, "upload_busy") == 0);
    if (err == ESP_ERR_NOT_FINISHED || named_busy) {
        return REHAB_TARGET_PERCEPTION_RESULT_BUSY_CLEANUP;
    }
    return REHAB_TARGET_PERCEPTION_RESULT_REAL_FAILURE;
}

rehab_target_follow_state_t rehab_target_follow_apply_perception_result(
    rehab_target_perception_result_t result,
    bool retryable,
    uint32_t *consecutive_errors)
{
    if (consecutive_errors == NULL) {
        return REHAB_TARGET_FOLLOW_STATE_ERROR;
    }
    if (result == REHAB_TARGET_PERCEPTION_RESULT_SUCCESS) {
        *consecutive_errors = 0;
        return REHAB_TARGET_FOLLOW_STATE_SENSE;
    }
    if (result == REHAB_TARGET_PERCEPTION_RESULT_BUSY_CLEANUP) {
        return REHAB_TARGET_FOLLOW_STATE_SENSE;
    }
    return rehab_target_follow_record_perception_error(retryable,
                                                        consecutive_errors);
}

rehab_target_capture_recovery_result_t
rehab_target_follow_evaluate_capture_recovery(
    esp_err_t status_result,
    const rehab_capture_status_t *status,
    uint32_t waited_ms,
    uint32_t max_wait_ms)
{
    if (status_result == ESP_OK &&
        rehab_capture_status_is_quiescent(status)) {
        return REHAB_TARGET_CAPTURE_RECOVERY_COMPLETE;
    }
    return waited_ms >= max_wait_ms
        ? REHAB_TARGET_CAPTURE_RECOVERY_TIMEOUT
        : REHAB_TARGET_CAPTURE_RECOVERY_WAIT;
}

static rehab_target_zone_t parse_zone(const char *zone)
{
    if (strcmp(zone, "left") == 0) {
        return REHAB_TARGET_ZONE_LEFT;
    }
    if (strcmp(zone, "center") == 0) {
        return REHAB_TARGET_ZONE_CENTER;
    }
    if (strcmp(zone, "right") == 0) {
        return REHAB_TARGET_ZONE_RIGHT;
    }
    return REHAB_TARGET_ZONE_NONE;
}

static bool better_candidate(const rehab_cloud_fusion_detection_t *candidate,
                             const rehab_cloud_fusion_detection_t *current)
{
    if (candidate->confidence != current->confidence) {
        return candidate->confidence > current->confidence;
    }
    float candidate_center = fabsf(candidate->center_normalized.x - 0.5f);
    float current_center = fabsf(current->center_normalized.x - 0.5f);
    if (candidate_center != current_center) {
        return candidate_center < current_center;
    }
    return candidate->object_id < current->object_id;
}

esp_err_t rehab_target_follow_select_observation(
    const rehab_cloud_fusion_result_t *result,
    rehab_target_follow_observation_t *observation,
    char *error,
    size_t error_size)
{
    if (result == NULL || observation == NULL || error == NULL || error_size == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(observation, 0, sizeof(*observation));
    strlcpy(error, "none", error_size);
    if (!result->ok || !result->response_json_valid || !result->capture_id_match ||
        strcmp(result->schema_version, "vision_fusion.v1") != 0 ||
        strcmp(result->cloud_status, "ok") != 0 || !result->cloud_valid ||
        !result->has_pairing) {
        strlcpy(error, "fusion_contract_invalid", error_size);
        return ESP_ERR_INVALID_RESPONSE;
    }
    if (result->detections_truncated ||
        result->detection_count > REHAB_CLOUD_FUSION_MAX_DETECTIONS) {
        strlcpy(error, "detections_truncated", error_size);
        return ESP_ERR_INVALID_SIZE;
    }

    const rehab_cloud_fusion_detection_t *selected = NULL;
    uint32_t matches = 0;
    for (uint32_t i = 0; i < result->detection_count; ++i) {
        const rehab_cloud_fusion_detection_t *candidate = &result->detections[i];
        if (!candidate->present ||
            strcmp(candidate->class_name, FOLLOW_TARGET_CLASS_NAME) != 0) {
            continue;
        }
        ++matches;
        if (selected == NULL || better_candidate(candidate, selected)) {
            selected = candidate;
        }
    }
    if (selected == NULL) {
        return ESP_OK;
    }
    if (!selected->has_object_id || !selected->has_confidence ||
        !isfinite(selected->confidence) ||
        selected->confidence < 0.0f || selected->confidence > 1.0f ||
        !selected->has_center_normalized ||
        !isfinite(selected->center_normalized.x) ||
        !isfinite(selected->center_normalized.y) ||
        selected->center_normalized.x < 0.0f || selected->center_normalized.x > 1.0f ||
        selected->center_normalized.y < 0.0f || selected->center_normalized.y > 1.0f) {
        strlcpy(error, "target_numeric_invalid", error_size);
        return ESP_ERR_INVALID_RESPONSE;
    }
    if (parse_zone(selected->horizontal_zone) == REHAB_TARGET_ZONE_NONE) {
        strlcpy(error, "target_zone_invalid", error_size);
        return ESP_ERR_INVALID_RESPONSE;
    }
    if (selected->has_distance_cm &&
        (!isfinite(selected->distance_cm) || selected->distance_cm <= 0.0f)) {
        strlcpy(error, "target_distance_invalid", error_size);
        return ESP_ERR_INVALID_RESPONSE;
    }
    if (selected->distance_valid && !selected->has_distance_cm) {
        strlcpy(error, "target_distance_missing", error_size);
        return ESP_ERR_INVALID_RESPONSE;
    }

    observation->found = true;
    observation->center_valid = true;
    observation->center_x = selected->center_normalized.x;
    float error_x = observation->center_x - 0.5f;
    observation->zone = error_x < -FOLLOW_CENTER_DEADBAND
        ? REHAB_TARGET_ZONE_LEFT
        : (error_x > FOLLOW_CENTER_DEADBAND
            ? REHAB_TARGET_ZONE_RIGHT : REHAB_TARGET_ZONE_CENTER);
    observation->distance_valid = selected->distance_valid && selected->has_distance_cm;
    observation->has_distance_cm = selected->has_distance_cm;
    observation->distance_cm = selected->distance_cm;
    observation->multiple_zxl = matches > 1U;
    observation->object_id = selected->object_id;
    observation->confidence = selected->confidence;
    return ESP_OK;
}
