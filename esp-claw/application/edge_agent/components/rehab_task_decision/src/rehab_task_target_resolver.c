// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_target_resolver.h"

#include <inttypes.h>
#include <math.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "rehab_task_decision_policy.h"

#define TARGET_SCORE_TIE_EPSILON 0.000001

typedef struct {
    const cJSON *object;
    const char *object_id;
    const char *class_name;
    bool geometry_valid;
    bool full_extent_visible;
    bool not_touches_border;
    double detection_confidence;
    double support_height_mm;
    double height_confidence;
} target_candidate_t;

static const char *TAG = "rehab_target_resolver";

static const cJSON *nested_item(const cJSON *object, const char *group,
                                const char *field)
{
    const cJSON *nested = cJSON_GetObjectItemCaseSensitive(object, group);
    return cJSON_IsObject(nested)
        ? cJSON_GetObjectItemCaseSensitive(nested, field) : NULL;
}

static double confidence_value(const cJSON *object)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(
        object, "confidence");
    if (!cJSON_IsNumber(value) || !isfinite(value->valuedouble)) {
        return 0.0;
    }
    if (value->valuedouble < 0.0) {
        return 0.0;
    }
    return value->valuedouble > 1.0 ? 1.0 : value->valuedouble;
}

double rehab_task_target_height_score(double support_height_mm,
                                       bool support_height_available)
{
    if (!support_height_available || !isfinite(support_height_mm)) {
        return 0.0;
    }
    const char *plane = rehab_task_policy_height_plane(support_height_mm);
    return plane != NULL
        ? 100.0 * rehab_task_policy_height_confidence(
              support_height_mm, plane)
        : 0.0;
}

bool rehab_task_target_task_has_mapping(const char *task_id)
{
    return rehab_task_policy_find(task_id) != NULL;
}

static bool object_in_group(const cJSON *object,
                            const rehab_task_policy_definition_t *task)
{
    const cJSON *class_name = cJSON_GetObjectItemCaseSensitive(
        object, "class_name");
    const char *group = cJSON_IsString(class_name) &&
        class_name->valuestring != NULL
        ? rehab_task_policy_semantic_group(class_name->valuestring) : NULL;
    return group != NULL && task != NULL &&
        strcmp(group, task->semantic_group) == 0;
}

static bool candidate_read(const char *task_id, const cJSON *object,
                           target_candidate_t *candidate)
{
    const rehab_task_policy_definition_t *task =
        rehab_task_policy_find(task_id);
    if (!object_in_group(object, task)) {
        return false;
    }
    const cJSON *object_id = cJSON_GetObjectItemCaseSensitive(
        object, "object_id");
    const cJSON *class_name = cJSON_GetObjectItemCaseSensitive(
        object, "class_name");
    const cJSON *geometry = cJSON_GetObjectItemCaseSensitive(
        object, "geometry");
    const cJSON *geometry_valid = cJSON_IsObject(geometry)
        ? cJSON_GetObjectItemCaseSensitive(geometry, "valid") : NULL;
    const cJSON *support_height = cJSON_IsObject(geometry)
        ? cJSON_GetObjectItemCaseSensitive(
              geometry, "support_height_mm") : NULL;
    if (!cJSON_IsString(object_id) || object_id->valuestring == NULL ||
        !cJSON_IsString(class_name) || class_name->valuestring == NULL ||
        !cJSON_IsTrue(geometry_valid) ||
        !cJSON_IsNumber(support_height) ||
        !isfinite(support_height->valuedouble)) {
        return false;
    }
    const double height_confidence =
        rehab_task_policy_height_confidence(
            support_height->valuedouble, task->plane);
    if (height_confidence <= 0.0) {
        return false;
    }
    memset(candidate, 0, sizeof(*candidate));
    candidate->object = object;
    candidate->object_id = object_id->valuestring;
    candidate->class_name = class_name->valuestring;
    candidate->geometry_valid = true;
    candidate->full_extent_visible = cJSON_IsTrue(
        nested_item(object, "visibility", "full_extent_visible"));
    candidate->not_touches_border = cJSON_IsFalse(
        nested_item(object, "visibility", "touches_border"));
    candidate->detection_confidence = confidence_value(object);
    candidate->support_height_mm = support_height->valuedouble;
    candidate->height_confidence = height_confidence;
    return true;
}

bool rehab_task_target_object_is_compatible(const char *task_id,
                                             const cJSON *object)
{
    target_candidate_t candidate = {0};
    return candidate_read(task_id, object, &candidate);
}

static bool candidate_better(const target_candidate_t *candidate,
                             const target_candidate_t *current)
{
    if (current->object == NULL) {
        return true;
    }
    if (fabs(candidate->height_confidence - current->height_confidence) >
        TARGET_SCORE_TIE_EPSILON) {
        return candidate->height_confidence > current->height_confidence;
    }
    if (candidate->geometry_valid != current->geometry_valid) {
        return candidate->geometry_valid;
    }
    if (candidate->full_extent_visible != current->full_extent_visible) {
        return candidate->full_extent_visible;
    }
    if (candidate->not_touches_border != current->not_touches_border) {
        return candidate->not_touches_border;
    }
    if (fabs(candidate->detection_confidence -
             current->detection_confidence) > TARGET_SCORE_TIE_EPSILON) {
        return candidate->detection_confidence >
            current->detection_confidence;
    }
    return strcmp(candidate->object_id, current->object_id) < 0;
}

esp_err_t rehab_task_target_resolve(
    const cJSON *objects,
    const char *task_id,
    const char *llm_target_object_id,
    const char *decision_id,
    rehab_task_target_resolution_t *out_resolution)
{
    if (!cJSON_IsArray(objects) || task_id == NULL ||
        out_resolution == NULL || rehab_task_policy_find(task_id) == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    (void)llm_target_object_id;
    memset(out_resolution, 0, sizeof(*out_resolution));
    const int64_t started_us = esp_timer_get_time();
    const rehab_task_policy_definition_t *task =
        rehab_task_policy_find(task_id);
    ESP_LOGI(TAG,
             "TARGET_RESOLVE_START decision_id=%s task_id=%s candidate_count=%d",
             decision_id != NULL ? decision_id : "none", task_id,
             cJSON_GetArraySize(objects));
    target_candidate_t best = {0};
    const cJSON *object = NULL;
    cJSON_ArrayForEach(object, objects) {
        if (object_in_group(object, task)) {
            ++out_resolution->semantic_group_count;
        }
        target_candidate_t candidate = {0};
        if (!candidate_read(task_id, object, &candidate)) {
            continue;
        }
        ++out_resolution->compatible_count;
        ESP_LOGI(TAG,
                 "TARGET_CANDIDATE decision_id=%s task_id=%s object_id=%s raw_class=%s support_height_mm=%.1f height_confidence=%.3f",
                 decision_id != NULL ? decision_id : "none", task_id,
                 candidate.object_id, candidate.class_name,
                 candidate.support_height_mm,
                 candidate.height_confidence);
        if (candidate_better(&candidate, &best)) {
            best = candidate;
        }
    }
    int64_t elapsed_us = esp_timer_get_time() - started_us;
    out_resolution->elapsed_ms = (uint32_t)(
        ((elapsed_us > 0 ? elapsed_us : 0) + 999) / 1000);
    if (best.object == NULL) {
        ESP_LOGI(TAG,
                 "TARGET_RESOLVE_NONE decision_id=%s task_id=%s group_count=%u compatible_count=%u elapsed_ms=%" PRIu32,
                 decision_id != NULL ? decision_id : "none", task_id,
                 (unsigned)out_resolution->semantic_group_count,
                 (unsigned)out_resolution->compatible_count,
                 out_resolution->elapsed_ms);
        return ESP_OK;
    }
    out_resolution->object = best.object;
    out_resolution->object_id = best.object_id;
    out_resolution->score = best.height_confidence;
    out_resolution->support_height_mm = best.support_height_mm;
    out_resolution->height_confidence = best.height_confidence;
    ESP_LOGI(TAG,
             "TARGET_RESOLVED decision_id=%s task_id=%s object_id=%s raw_class=%s support_height_mm=%.1f height_confidence=%.3f elapsed_ms=%" PRIu32,
             decision_id != NULL ? decision_id : "none", task_id,
             best.object_id, best.class_name, best.support_height_mm,
             best.height_confidence, out_resolution->elapsed_ms);
    return ESP_OK;
}
