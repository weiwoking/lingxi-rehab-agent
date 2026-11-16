#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "esp_err.h"

#define REHAB_TASK_DECISION_DECISION_ID_MAX 64U
#define REHAB_TASK_DECISION_SCAN_ID_MAX 64U
#define REHAB_TASK_DECISION_ERROR_MAX 48U

typedef struct fake_decision *rehab_task_decision_handle_t;

typedef enum {
    REHAB_TASK_DECISION_COORDINATOR_IDLE = 0,
    REHAB_TASK_DECISION_COORDINATOR_PREPARING,
    REHAB_TASK_DECISION_COORDINATOR_DECIDING,
    REHAB_TASK_DECISION_COORDINATOR_VALIDATING,
    REHAB_TASK_DECISION_COORDINATOR_COMMITTING,
    REHAB_TASK_DECISION_COORDINATOR_SELECTED,
    REHAB_TASK_DECISION_COORDINATOR_FAILED,
    REHAB_TASK_DECISION_COORDINATOR_CANCELLED,
} rehab_task_decision_coordinator_state_t;

typedef struct {
    rehab_task_decision_coordinator_state_t state;
    bool active;
    uint32_t generation;
    char decision_id[REHAB_TASK_DECISION_DECISION_ID_MAX];
    char source_scan_id[REHAB_TASK_DECISION_SCAN_ID_MAX];
    char last_error[REHAB_TASK_DECISION_ERROR_MAX];
} rehab_task_decision_coordinator_status_t;

typedef enum {
    REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI = 0,
    REHAB_TASK_DECISION_START_SOURCE_AGENT,
    REHAB_TASK_DECISION_START_SOURCE_CONSOLE,
    REHAB_TASK_DECISION_START_SOURCE_WECHAT,
    REHAB_TASK_DECISION_START_SOURCE_UNKNOWN,
} rehab_task_decision_start_source_t;

typedef enum {
    REHAB_TASK_DECISION_START_OUTCOME_STARTED = 0,
    REHAB_TASK_DECISION_START_OUTCOME_ATTACHED_BUSY,
    REHAB_TASK_DECISION_START_OUTCOME_REJECTED,
    REHAB_TASK_DECISION_START_OUTCOME_TERMINAL_FAILURE,
} rehab_task_decision_start_outcome_t;

typedef struct {
    rehab_task_decision_start_outcome_t outcome;
    rehab_task_decision_start_source_t source;
    bool created_new_generation;
    uint32_t generation;
    rehab_task_decision_coordinator_state_t state;
    bool active;
    char decision_id[REHAB_TASK_DECISION_DECISION_ID_MAX];
    char scan_id[REHAB_TASK_DECISION_SCAN_ID_MAX];
    char last_error[REHAB_TASK_DECISION_ERROR_MAX];
} rehab_task_decision_start_result_t;

static inline size_t strlcpy(char *destination, const char *source, size_t size)
{
    const size_t length = strlen(source);
    if (size > 0U) {
        const size_t copied = length < size - 1U ? length : size - 1U;
        memcpy(destination, source, copied);
        destination[copied] = '\0';
    }
    return length;
}

esp_err_t rehab_task_decision_coordinator_get_status(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_coordinator_status_t *out_status);
const char *rehab_task_decision_coordinator_state_name(
    rehab_task_decision_coordinator_state_t state);
esp_err_t rehab_task_decision_start_confirmed(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_start_source_t source,
    rehab_task_decision_start_result_t *out_result,
    rehab_task_decision_coordinator_status_t *out_status);
const char *rehab_task_decision_start_source_name(
    rehab_task_decision_start_source_t source);
const char *rehab_task_decision_start_outcome_name(
    rehab_task_decision_start_outcome_t outcome);
