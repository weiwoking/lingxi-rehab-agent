// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stddef.h>

#include "esp_err.h"

esp_err_t rehab_task_decision_formal_input_artifact_save_latest(
    const char *formal_input,
    size_t formal_input_bytes);
