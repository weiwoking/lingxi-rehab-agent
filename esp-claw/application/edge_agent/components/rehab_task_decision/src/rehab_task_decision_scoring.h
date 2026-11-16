// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "cJSON.h"
#include "esp_err.h"

/** Add the complete production scoring policy to a DeepSeek input root. */
esp_err_t rehab_task_scoring_add_policy(cJSON *root);

/**
 * Validate output structure/schema, type/finite/range/ref, fixed candidate_ref
 * order and completeness, blocked shape and provenance, and frozen hard-gate
 * facts, plus prepared-value consistency for the four fixed components and
 * independently recomputed final_raw/score for the chosen advice. The winner ref
 * must identify an available output candidate. P4 never interprets free text,
 * chooses advice, ranks candidates, selects the highest score, or applies a
 * tie-break. Presentation wording is never scoring authority. This function
 * never creates, replaces, clamps, normalizes, or otherwise repairs DeepSeek
 * output.
 */
bool rehab_task_scoring_validate(const cJSON *input,
                                 cJSON *output,
                                 char *error,
                                 size_t error_size);
