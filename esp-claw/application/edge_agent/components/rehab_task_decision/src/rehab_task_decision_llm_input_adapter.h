// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "esp_err.h"

#define REHAB_TASK_DECISION_LLM_INPUT_SCHEMA_VERSION \
    "rehab_task_decision_llm_input.v1"

/**
 * Create the model-facing compact projection of one frozen v4 formal input.
 *
 * The source string is never modified.  On success, out_compact owns a
 * cJSON-allocated string and must be released with cJSON_free().
 */
esp_err_t rehab_task_decision_llm_input_compact(
    const char *formal_input,
    char **out_compact);
