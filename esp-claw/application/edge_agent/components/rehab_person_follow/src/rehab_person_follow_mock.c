// SPDX-License-Identifier: Apache-2.0
#include "rehab_person_follow_priv.h"

#include <string.h>

static void mock_target(rehab_person_follow_observation_t *observation,
                        float center_x)
{
    observation->valid = true;
    observation->target_found = true;
    observation->confidence = PERSON_FOLLOW_MOCK_CONFIDENCE;
    observation->center_x = center_x;
    observation->center_y = 0.50f;
    observation->bbox_x1 = (center_x - 0.10f) * PERSON_FOLLOW_IMAGE_WIDTH;
    observation->bbox_y1 = 0.20f * PERSON_FOLLOW_IMAGE_HEIGHT;
    observation->bbox_x2 = (center_x + 0.10f) * PERSON_FOLLOW_IMAGE_WIDTH;
    observation->bbox_y2 = 0.80f * PERSON_FOLLOW_IMAGE_HEIGHT;
    observation->area_normalized = 0.12f;
}

esp_err_t rehab_person_follow_mock_observe(
    rehab_person_follow_mock_mode_t mode,
    uint32_t sequence,
    rehab_person_follow_observation_t *out_observation)
{
    if (out_observation == NULL ||
        mode > REHAB_PERSON_FOLLOW_MOCK_TECHNICAL_ERROR) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_observation, 0, sizeof(*out_observation));
    out_observation->mock_sequence = sequence;
    switch (mode) {
    case REHAB_PERSON_FOLLOW_MOCK_NO_TARGET:
        out_observation->valid = true;
        return ESP_OK;
    case REHAB_PERSON_FOLLOW_MOCK_TARGET_LEFT:
        mock_target(out_observation, PERSON_FOLLOW_LEFT_CENTER_X);
        return ESP_OK;
    case REHAB_PERSON_FOLLOW_MOCK_TARGET_CENTER:
        mock_target(out_observation, PERSON_FOLLOW_CENTER_CENTER_X);
        return ESP_OK;
    case REHAB_PERSON_FOLLOW_MOCK_TARGET_RIGHT:
        mock_target(out_observation, PERSON_FOLLOW_RIGHT_CENTER_X);
        return ESP_OK;
    case REHAB_PERSON_FOLLOW_MOCK_ALTERNATING_LEFT_RIGHT:
        mock_target(out_observation, (sequence & 1U) == 0U
            ? PERSON_FOLLOW_LEFT_CENTER_X : PERSON_FOLLOW_RIGHT_CENTER_X);
        return ESP_OK;
    case REHAB_PERSON_FOLLOW_MOCK_TECHNICAL_ERROR:
        out_observation->valid = false;
        return ESP_FAIL;
    default:
        return ESP_ERR_INVALID_ARG;
    }
}

const char *rehab_person_follow_mock_mode_name(
    rehab_person_follow_mock_mode_t mode)
{
    switch (mode) {
    case REHAB_PERSON_FOLLOW_MOCK_NO_TARGET: return "no_target";
    case REHAB_PERSON_FOLLOW_MOCK_TARGET_LEFT: return "target_left";
    case REHAB_PERSON_FOLLOW_MOCK_TARGET_CENTER: return "target_center";
    case REHAB_PERSON_FOLLOW_MOCK_TARGET_RIGHT: return "target_right";
    case REHAB_PERSON_FOLLOW_MOCK_ALTERNATING_LEFT_RIGHT:
        return "alternating_left_right";
    case REHAB_PERSON_FOLLOW_MOCK_TECHNICAL_ERROR:
        return "technical_error";
    default: return "unknown";
    }
}

const char *rehab_person_follow_perception_provider_name(
    rehab_person_follow_perception_provider_t provider)
{
    switch (provider) {
    case REHAB_PERSON_FOLLOW_PERCEPTION_MOCK: return "mock";
    case REHAB_PERSON_FOLLOW_PERCEPTION_REAL: return "real";
    default: return "unknown";
    }
}
