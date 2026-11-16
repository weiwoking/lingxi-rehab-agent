// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "rehab_task_decision.h"

esp_err_t rehab_task_decision_reasoning_artifact_save_latest(
    const rehab_task_decision_coordinator_status_t *status,
    const char *reasoning_content);
