// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "cJSON.h"

typedef struct {
    const char *decision_id;
    uint32_t generation;
    const char *source_scan_id;
    const char *source_scene_sha256;
    const char *task_id;
    const char *task_name;
    const char *reason;
    double score;
    bool target_available;
    const char *target_object_id;
} rehab_task_details_authority_t;

bool rehab_task_decision_details_binding_matches(
    const cJSON *details,
    const rehab_task_details_authority_t *authority);
