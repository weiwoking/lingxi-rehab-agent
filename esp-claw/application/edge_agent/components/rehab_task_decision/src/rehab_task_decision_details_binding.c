// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_details_binding.h"

#include <math.h>
#include <string.h>

static const cJSON *field(const cJSON *object, const char *name)
{
    return cJSON_IsObject(object)
        ? cJSON_GetObjectItemCaseSensitive(object, name) : NULL;
}

static bool same_string(const cJSON *item, const char *expected)
{
    return expected != NULL && cJSON_IsString(item) &&
        item->valuestring != NULL && strcmp(item->valuestring, expected) == 0;
}

bool rehab_task_decision_details_binding_matches(
    const cJSON *details,
    const rehab_task_details_authority_t *authority)
{
    const cJSON *selected = field(details, "selected_task");
    const cJSON *target = field(details, "target_details");
    const cJSON *generation = field(details, "generation");
    const cJSON *score = field(selected, "score");
    if (authority == NULL || authority->decision_id == NULL ||
        authority->source_scan_id == NULL ||
        authority->source_scene_sha256 == NULL || authority->task_id == NULL ||
        authority->task_name == NULL || authority->reason == NULL ||
        !same_string(field(details, "decision_id"), authority->decision_id) ||
        !cJSON_IsNumber(generation) || !isfinite(generation->valuedouble) ||
        generation->valuedouble != (double)authority->generation ||
        !same_string(field(details, "source_scan_id"),
                     authority->source_scan_id) ||
        !same_string(field(details, "source_scene_sha256"),
                     authority->source_scene_sha256) ||
        !same_string(field(selected, "task_id"), authority->task_id) ||
        !same_string(field(selected, "task_name"), authority->task_name) ||
        !same_string(field(selected, "reason"), authority->reason) ||
        !cJSON_IsNumber(score) || !isfinite(score->valuedouble) ||
        fabs(score->valuedouble - authority->score) > 0.000001) {
        return false;
    }
    const cJSON *object_id = field(target, "object_id");
    return authority->target_available
        ? same_string(object_id, authority->target_object_id)
        : cJSON_IsNull(object_id);
}
