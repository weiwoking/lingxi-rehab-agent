// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "rehab_decision_trace.h"

void rehab_decision_trace_reset(rehab_decision_trace_t *trace);
void rehab_decision_trace_begin(
    rehab_decision_trace_t *trace,
    uint32_t generation,
    const char *decision_id,
    uint64_t now_monotonic_ms);
bool rehab_decision_trace_transition(
    rehab_decision_trace_t *trace,
    uint32_t generation,
    rehab_decision_trace_stage_t stage,
    uint64_t now_monotonic_ms,
    const char *failure_code);
void rehab_decision_trace_project(
    const rehab_decision_trace_t *trace,
    uint64_t now_monotonic_ms,
    rehab_decision_trace_t *out_snapshot);
