// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "cJSON.h"
#include "esp_err.h"

typedef struct {
    const cJSON *object;
    const char *object_id;
    size_t semantic_group_count;
    size_t compatible_count;
    double score;
    double support_height_mm;
    double height_confidence;
    uint32_t elapsed_ms;
} rehab_task_target_resolution_t;

/* Demo-only target scoring.  This is deliberately local and replaceable; it
 * is not a clinical rehabilitation rule. */
double rehab_task_target_height_score(double support_height_mm,
                                       bool support_height_available);

bool rehab_task_target_task_has_mapping(const char *task_id);

bool rehab_task_target_object_is_compatible(const char *task_id,
                                             const cJSON *object);

esp_err_t rehab_task_target_resolve(
    const cJSON *objects,
    const char *task_id,
    const char *llm_target_object_id,
    const char *decision_id,
    rehab_task_target_resolution_t *out_resolution);
