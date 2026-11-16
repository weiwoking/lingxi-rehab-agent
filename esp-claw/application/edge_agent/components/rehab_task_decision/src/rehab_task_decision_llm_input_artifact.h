// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stddef.h>

#include "esp_err.h"

esp_err_t rehab_task_decision_llm_input_artifact_save_latest(
    const char *llm_input,
    size_t llm_input_bytes);
