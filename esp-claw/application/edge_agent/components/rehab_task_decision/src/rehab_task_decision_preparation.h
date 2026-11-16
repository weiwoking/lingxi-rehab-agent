// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "cJSON.h"
#include "esp_err.h"

#define REHAB_TASK_DECISION_PREPARATION_SCHEMA_VERSION \
    "rehab_task_decision_preparation.v1.1"

typedef struct {
    bool blocked;
    double difficulty;
    double compensation;
    double frequency;
    double perception;
    double fixed_total;
    double validation_tolerance;
} rehab_task_decision_prepared_candidate_t;

/** Calculate only machine-deterministic fields from one frozen formal v4. */
esp_err_t rehab_task_decision_preparation_calculate(
    const cJSON *formal,
    size_t candidate_ref,
    rehab_task_decision_prepared_candidate_t *out_prepared);

/** Add the compact-v1.1 frozen preparation projection. */
esp_err_t rehab_task_decision_preparation_add(cJSON *compact,
                                              const cJSON *formal);
