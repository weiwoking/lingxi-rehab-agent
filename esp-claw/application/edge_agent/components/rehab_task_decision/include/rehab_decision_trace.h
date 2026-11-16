// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define REHAB_DECISION_TRACE_DECISION_ID_MAX 64U
#define REHAB_DECISION_TRACE_FAILURE_CODE_MAX 32U
#define REHAB_DECISION_TRACE_ELAPSED_MAX_SEC 999U

typedef enum {
    REHAB_DECISION_TRACE_IDLE = 0,
    REHAB_DECISION_TRACE_PREPARING,
    REHAB_DECISION_TRACE_INPUT_READY,
    REHAB_DECISION_TRACE_CONTEXT_READY,
    REHAB_DECISION_TRACE_MODEL_WAIT,
    REHAB_DECISION_TRACE_TOOL_CALL,
    REHAB_DECISION_TRACE_VALIDATING,
    REHAB_DECISION_TRACE_COMMITTING,
    REHAB_DECISION_TRACE_SUCCESS,
    REHAB_DECISION_TRACE_FAILED,
} rehab_decision_trace_stage_t;

/**
 * Bounded, non-sensitive projection of one formal decision execution.
 * It deliberately contains no prompt, reasoning, tool arguments, or user data.
 */
typedef struct {
    rehab_decision_trace_stage_t stage;
    uint64_t start_monotonic_ms;
    uint64_t stage_start_monotonic_ms;
    uint64_t end_monotonic_ms;
    uint32_t elapsed_sec;
    uint32_t stage_seq;
    char decision_id[REHAB_DECISION_TRACE_DECISION_ID_MAX];
    uint32_t generation;
    bool active;
    char failure_code[REHAB_DECISION_TRACE_FAILURE_CODE_MAX];
} rehab_decision_trace_t;

const char *rehab_decision_trace_stage_name(
    rehab_decision_trace_stage_t stage);

#ifdef __cplusplus
}
#endif
