// SPDX-License-Identifier: Apache-2.0
#include "rehab_decision_trace_internal.h"

#include <string.h>

static uint32_t trace_elapsed_sec(uint64_t start_ms, uint64_t end_ms)
{
    if (end_ms < start_ms) {
        return 0U;
    }
    const uint64_t elapsed = (end_ms - start_ms) / 1000U;
    return elapsed > REHAB_DECISION_TRACE_ELAPSED_MAX_SEC
        ? REHAB_DECISION_TRACE_ELAPSED_MAX_SEC : (uint32_t)elapsed;
}

static void bounded_copy(char *destination, size_t destination_size,
                         const char *source)
{
    if (destination == NULL || destination_size == 0U) {
        return;
    }
    const char *text = source != NULL ? source : "";
    size_t length = 0U;
    while (length + 1U < destination_size && text[length] != '\0') {
        ++length;
    }
    memcpy(destination, text, length);
    destination[length] = '\0';
}

void rehab_decision_trace_reset(rehab_decision_trace_t *trace)
{
    if (trace == NULL) {
        return;
    }
    memset(trace, 0, sizeof(*trace));
    trace->stage = REHAB_DECISION_TRACE_IDLE;
}

void rehab_decision_trace_begin(rehab_decision_trace_t *trace,
                                uint32_t generation,
                                const char *decision_id,
                                uint64_t now_monotonic_ms)
{
    if (trace == NULL || generation == 0U || decision_id == NULL ||
        decision_id[0] == '\0') {
        return;
    }
    rehab_decision_trace_reset(trace);
    trace->stage = REHAB_DECISION_TRACE_PREPARING;
    trace->start_monotonic_ms = now_monotonic_ms;
    trace->stage_start_monotonic_ms = now_monotonic_ms;
    trace->stage_seq = 1U;
    trace->generation = generation;
    trace->active = true;
    bounded_copy(trace->decision_id, sizeof(trace->decision_id), decision_id);
}

bool rehab_decision_trace_transition(rehab_decision_trace_t *trace,
                                     uint32_t generation,
                                     rehab_decision_trace_stage_t stage,
                                     uint64_t now_monotonic_ms,
                                     const char *failure_code)
{
    if (trace == NULL || generation == 0U ||
        trace->generation != generation || !trace->active ||
        stage <= REHAB_DECISION_TRACE_IDLE ||
        stage > REHAB_DECISION_TRACE_FAILED ||
        stage == trace->stage) {
        return false;
    }
    trace->stage = stage;
    trace->stage_start_monotonic_ms = now_monotonic_ms;
    ++trace->stage_seq;
    trace->elapsed_sec = trace_elapsed_sec(
        trace->start_monotonic_ms, now_monotonic_ms);
    if (stage == REHAB_DECISION_TRACE_SUCCESS ||
        stage == REHAB_DECISION_TRACE_FAILED) {
        trace->end_monotonic_ms = now_monotonic_ms;
        trace->active = false;
        bounded_copy(trace->failure_code, sizeof(trace->failure_code),
                     stage == REHAB_DECISION_TRACE_FAILED
                         ? failure_code : "");
    }
    return true;
}

void rehab_decision_trace_project(const rehab_decision_trace_t *trace,
                                  uint64_t now_monotonic_ms,
                                  rehab_decision_trace_t *out_snapshot)
{
    if (trace == NULL || out_snapshot == NULL) {
        return;
    }
    memcpy(out_snapshot, trace, sizeof(*out_snapshot));
    if (out_snapshot->generation == 0U) {
        return;
    }
    const uint64_t effective_end = out_snapshot->active
        ? now_monotonic_ms : out_snapshot->end_monotonic_ms;
    out_snapshot->elapsed_sec = trace_elapsed_sec(
        out_snapshot->start_monotonic_ms, effective_end);
}

const char *rehab_decision_trace_stage_name(
    rehab_decision_trace_stage_t stage)
{
    switch (stage) {
    case REHAB_DECISION_TRACE_IDLE: return "IDLE";
    case REHAB_DECISION_TRACE_PREPARING: return "PREPARING";
    case REHAB_DECISION_TRACE_INPUT_READY: return "INPUT_READY";
    case REHAB_DECISION_TRACE_CONTEXT_READY: return "CONTEXT_READY";
    case REHAB_DECISION_TRACE_MODEL_WAIT: return "MODEL_WAIT";
    case REHAB_DECISION_TRACE_TOOL_CALL: return "TOOL_CALL";
    case REHAB_DECISION_TRACE_VALIDATING: return "VALIDATING";
    case REHAB_DECISION_TRACE_COMMITTING: return "COMMITTING";
    case REHAB_DECISION_TRACE_SUCCESS: return "SUCCESS";
    case REHAB_DECISION_TRACE_FAILED: return "FAILED";
    default: return "UNKNOWN";
    }
}
