// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_priv.h"

#include <string.h>
#include <strings.h>

static rehab_environment_class_summary_t *find_or_add_class(
    rehab_task_decision_status_t *status,
    const char *class_name)
{
    for (size_t i = 0; i < status->class_count; ++i) {
        if (strcmp(status->classes[i].class_name, class_name) == 0) {
            return &status->classes[i];
        }
    }
    if (status->class_count >= REHAB_TASK_DECISION_MAX_CLASSES) {
        return NULL;
    }
    rehab_environment_class_summary_t *summary =
        &status->classes[status->class_count++];
    memset(summary, 0, sizeof(*summary));
    strlcpy(summary->class_name, class_name, sizeof(summary->class_name));
    return summary;
}

static void add_capture_source(rehab_environment_class_summary_t *summary,
                               const char *capture_id)
{
    if (capture_id == NULL || capture_id[0] == '\0') {
        return;
    }
    for (uint8_t i = 0; i < summary->source_capture_count; ++i) {
        if (strcmp(summary->source_capture_ids[i], capture_id) == 0) {
            return;
        }
    }
    if (summary->source_capture_count >=
        REHAB_TASK_DECISION_CAPTURE_SOURCES_PER_CLASS) {
        summary->source_capture_overflow = true;
        return;
    }
    strlcpy(summary->source_capture_ids[summary->source_capture_count],
            capture_id,
            sizeof(summary->source_capture_ids[summary->source_capture_count]));
    ++summary->source_capture_count;
}

static void add_zone(rehab_environment_class_summary_t *summary,
                     const char *zone)
{
    if (strcasecmp(zone, "left") == 0) {
        ++summary->left_count;
    } else if (strcasecmp(zone, "center") == 0) {
        ++summary->center_count;
    } else if (strcasecmp(zone, "right") == 0) {
        ++summary->right_count;
    } else {
        ++summary->unknown_zone_count;
    }
}

uint32_t rehab_environment_aggregate_apply(
    rehab_task_decision_status_t *status,
    const rehab_cloud_fusion_result_t *result,
    uint64_t observed_at_ms)
{
    if (status == NULL || result == NULL) {
        return 0;
    }
    bool class_seen[REHAB_TASK_DECISION_MAX_CLASSES] = {0};
    const rehab_cloud_fusion_detection_t *representative[
        REHAB_TASK_DECISION_MAX_CLASSES] = {0};
    uint32_t accepted = 0;
    for (uint32_t i = 0; i < result->detection_count; ++i) {
        const rehab_cloud_fusion_detection_t *detection =
            &result->detections[i];
        if (!detection->present || detection->class_name[0] == '\0') {
            continue;
        }
        rehab_environment_class_summary_t *summary =
            find_or_add_class(status, detection->class_name);
        if (summary == NULL) {
            continue;
        }
        size_t summary_index = (size_t)(summary - status->classes);
        ++accepted;
        if (!class_seen[summary_index]) {
            class_seen[summary_index] = true;
            representative[summary_index] = detection;
            ++summary->seen_count;
            if (summary->first_seen_at_ms == 0) {
                summary->first_seen_at_ms = observed_at_ms;
            }
            summary->last_seen_at_ms = observed_at_ms;
            add_capture_source(summary, result->capture_id);
        } else {
            const rehab_cloud_fusion_detection_t *current =
                representative[summary_index];
            if ((!current->has_confidence && detection->has_confidence) ||
                (current->has_confidence && detection->has_confidence &&
                 detection->confidence > current->confidence)) {
                representative[summary_index] = detection;
            }
        }

        if (detection->has_confidence) {
            if (summary->confidence_count == 0 ||
                detection->confidence > summary->max_confidence) {
                summary->max_confidence = detection->confidence;
            }
            ++summary->confidence_count;
        }
        if (detection->has_distance_cm && detection->distance_valid) {
            uint32_t old_count = summary->valid_distance_count;
            if (old_count == 0) {
                summary->min_distance_cm = detection->distance_cm;
                summary->max_distance_cm = detection->distance_cm;
                summary->mean_distance_cm = detection->distance_cm;
            } else {
                if (detection->distance_cm < summary->min_distance_cm) {
                    summary->min_distance_cm = detection->distance_cm;
                }
                if (detection->distance_cm > summary->max_distance_cm) {
                    summary->max_distance_cm = detection->distance_cm;
                }
                summary->mean_distance_cm =
                    ((summary->mean_distance_cm * (float)old_count) +
                     detection->distance_cm) / (float)(old_count + 1U);
            }
            ++summary->valid_distance_count;
            summary->last_distance_cm = detection->distance_cm;
        }

        summary->last_object_height_valid =
            detection->has_object_height_cm &&
            detection->object_height_valid;
        if (summary->last_object_height_valid) {
            summary->last_object_height_cm = detection->object_height_cm;
        }
        summary->last_bottom_height_valid =
            detection->has_bottom_height_from_ground_cm &&
            detection->bottom_height_valid;
        if (summary->last_bottom_height_valid) {
            summary->last_bottom_height_cm =
                detection->bottom_height_from_ground_cm;
        }
        summary->last_geometry_quality_valid =
            detection->has_geometry_quality;
        if (summary->last_geometry_quality_valid) {
            summary->last_geometry_quality = detection->geometry_quality;
        }
        summary->last_tof_zone_count = detection->tof_zone_count;
        for (uint32_t zone = 0; zone < detection->tof_zone_count; ++zone) {
            uint8_t zone_id = detection->tof_zone_ids[zone];
            if (zone_id >= 64U) {
                summary->source_tof_zone_overflow = true;
                continue;
            }
            uint64_t bit = UINT64_C(1) << zone_id;
            if ((summary->source_tof_zone_mask & bit) == 0) {
                summary->source_tof_zone_mask |= bit;
                ++summary->unique_tof_zone_count;
            }
        }
    }
    for (size_t i = 0; i < status->class_count; ++i) {
        if (class_seen[i] && representative[i] != NULL) {
            add_zone(&status->classes[i],
                     representative[i]->horizontal_zone);
        }
    }
    return accepted;
}
