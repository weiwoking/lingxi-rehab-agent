// SPDX-License-Identifier: Apache-2.0
#include "rehab_person_follow_priv.h"

#include <math.h>

static float intersection_over_union(
    const rehab_person_follow_detection_t *a,
    const rehab_person_follow_detection_t *b)
{
    float left = fmaxf(a->x1, b->x1);
    float top = fmaxf(a->y1, b->y1);
    float right = fminf(a->x2, b->x2);
    float bottom = fminf(a->y2, b->y2);
    float intersection = fmaxf(0.0f, right - left) *
                         fmaxf(0.0f, bottom - top);
    float area_a = (a->x2 - a->x1) * (a->y2 - a->y1);
    float area_b = (b->x2 - b->x1) * (b->y2 - b->y1);
    float union_area = area_a + area_b - intersection;
    return union_area > 0.0f ? intersection / union_area : 0.0f;
}

static bool duplicate_better(const rehab_person_follow_detection_t *candidate,
                             const rehab_person_follow_detection_t *current)
{
    if (candidate->confidence != current->confidence) {
        return candidate->confidence > current->confidence;
    }
    return candidate->area_normalized > current->area_normalized;
}

uint32_t rehab_person_follow_deduplicate(
    const rehab_person_follow_detection_t *input,
    uint32_t input_count,
    rehab_person_follow_detection_t *output,
    uint32_t output_capacity)
{
    if (input == NULL || output == NULL || output_capacity == 0U) {
        return 0U;
    }
    uint32_t kept = 0U;
    for (uint32_t i = 0; i < input_count; ++i) {
        bool duplicate = false;
        for (uint32_t j = 0; j < kept; ++j) {
            if (intersection_over_union(&input[i], &output[j]) >= 0.70f) {
                if (duplicate_better(&input[i], &output[j])) {
                    output[j] = input[i];
                }
                duplicate = true;
                break;
            }
        }
        if (!duplicate && kept < output_capacity) {
            output[kept++] = input[i];
        }
    }
    return kept;
}

static bool initial_better(const rehab_person_follow_detection_t *candidate,
                           const rehab_person_follow_detection_t *current,
                           bool central)
{
    if (central && candidate->area_normalized != current->area_normalized) {
        return candidate->area_normalized > current->area_normalized;
    }
    float candidate_distance = fabsf(candidate->center_x - 0.5f);
    float current_distance = fabsf(current->center_x - 0.5f);
    if (!central && candidate_distance != current_distance) {
        return candidate_distance < current_distance;
    }
    return candidate->confidence > current->confidence;
}

static uint32_t initial_index(
    const rehab_person_follow_detection_t *detections,
    uint32_t count)
{
    bool has_central = false;
    uint32_t best = 0U;
    for (uint32_t i = 0; i < count; ++i) {
        bool central = detections[i].center_x >= 0.25f &&
                       detections[i].center_x <= 0.75f;
        if (central && !has_central) {
            best = i;
            has_central = true;
        } else if (central == has_central &&
                   initial_better(&detections[i], &detections[best],
                                  has_central)) {
            best = i;
        }
    }
    return best;
}

bool rehab_person_follow_select_target(
    const rehab_person_follow_detection_t *detections,
    uint32_t detection_count,
    bool last_center_valid,
    float last_center_x,
    rehab_person_follow_detection_t *out_selected)
{
    if (detections == NULL || detection_count == 0U ||
        out_selected == NULL) {
        return false;
    }
    uint32_t selected = initial_index(detections, detection_count);
    if (last_center_valid && isfinite(last_center_x)) {
        uint32_t nearest = 0U;
        float nearest_distance = fabsf(detections[0].center_x - last_center_x);
        for (uint32_t i = 1; i < detection_count; ++i) {
            float distance = fabsf(detections[i].center_x - last_center_x);
            if (distance < nearest_distance ||
                (distance == nearest_distance &&
                 detections[i].confidence > detections[nearest].confidence)) {
                nearest = i;
                nearest_distance = distance;
            }
        }
        if (nearest_distance <= 0.30f) {
            selected = nearest;
        }
    }
    *out_selected = detections[selected];
    return true;
}

rehab_person_follow_target_zone_t rehab_person_follow_classify_target(
    bool target_found, float center_x)
{
    if (!target_found || !isfinite(center_x)) {
        return REHAB_PERSON_FOLLOW_TARGET_NO_TARGET;
    }
    if (center_x < PERSON_FOLLOW_LEFT_BOUNDARY) {
        return REHAB_PERSON_FOLLOW_TARGET_LEFT;
    }
    if (center_x > PERSON_FOLLOW_RIGHT_BOUNDARY) {
        return REHAB_PERSON_FOLLOW_TARGET_RIGHT;
    }
    return REHAB_PERSON_FOLLOW_TARGET_CENTER;
}

const char *rehab_person_follow_target_zone_name(
    rehab_person_follow_target_zone_t zone)
{
    switch (zone) {
    case REHAB_PERSON_FOLLOW_TARGET_LEFT: return "LEFT";
    case REHAB_PERSON_FOLLOW_TARGET_CENTER: return "CENTER";
    case REHAB_PERSON_FOLLOW_TARGET_RIGHT: return "RIGHT";
    default: return "NO_TARGET";
    }
}
