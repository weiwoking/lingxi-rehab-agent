// SPDX-License-Identifier: Apache-2.0
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "rehab_decision_trace_internal.h"

static void test_stage_transition_and_elapsed(void)
{
    rehab_decision_trace_t trace;
    rehab_decision_trace_begin(&trace, 7U, "decision-007", 1000U);
    assert(trace.stage == REHAB_DECISION_TRACE_PREPARING);
    assert(trace.active);
    assert(trace.stage_seq == 1U);
    assert(strcmp(trace.decision_id, "decision-007") == 0);

    assert(rehab_decision_trace_transition(
        &trace, 7U, REHAB_DECISION_TRACE_INPUT_READY, 1999U, NULL));
    assert(trace.elapsed_sec == 0U);
    assert(rehab_decision_trace_transition(
        &trace, 7U, REHAB_DECISION_TRACE_CONTEXT_READY, 2000U, NULL));
    assert(rehab_decision_trace_transition(
        &trace, 7U, REHAB_DECISION_TRACE_MODEL_WAIT, 3000U, NULL));
    assert(trace.elapsed_sec == 2U);
    assert(trace.stage_seq == 4U);

    rehab_decision_trace_t snapshot = {0};
    rehab_decision_trace_project(&trace, 88000U, &snapshot);
    assert(snapshot.elapsed_sec == 87U);
    assert(snapshot.active);
}

static void test_success_freeze_and_generation_mismatch(void)
{
    rehab_decision_trace_t trace;
    rehab_decision_trace_begin(&trace, 9U, "decision-009", 1000U);
    assert(rehab_decision_trace_transition(
        &trace, 9U, REHAB_DECISION_TRACE_MODEL_WAIT, 2000U, NULL));
    const uint32_t seq = trace.stage_seq;
    assert(!rehab_decision_trace_transition(
        &trace, 10U, REHAB_DECISION_TRACE_TOOL_CALL, 3000U, NULL));
    assert(trace.stage == REHAB_DECISION_TRACE_MODEL_WAIT);
    assert(trace.stage_seq == seq);
    assert(rehab_decision_trace_transition(
        &trace, 9U, REHAB_DECISION_TRACE_SUCCESS, 88000U, NULL));
    assert(!trace.active);
    assert(trace.elapsed_sec == 87U);
    assert(trace.end_monotonic_ms == 88000U);

    rehab_decision_trace_t snapshot = {0};
    rehab_decision_trace_project(&trace, 999000U, &snapshot);
    assert(snapshot.elapsed_sec == 87U);
    assert(!rehab_decision_trace_transition(
        &trace, 9U, REHAB_DECISION_TRACE_FAILED, 999000U, "late_failure"));
    assert(trace.stage == REHAB_DECISION_TRACE_SUCCESS);
}

static void test_failed_freeze_and_elapsed_cap(void)
{
    rehab_decision_trace_t trace;
    rehab_decision_trace_begin(&trace, 11U, "decision-011", 500U);
    assert(rehab_decision_trace_transition(
        &trace, 11U, REHAB_DECISION_TRACE_FAILED, 3000U,
        "formal_root_timeout"));
    assert(trace.stage == REHAB_DECISION_TRACE_FAILED);
    assert(!trace.active);
    assert(trace.elapsed_sec == 2U);
    assert(strcmp(trace.failure_code, "formal_root_timeout") == 0);

    rehab_decision_trace_t snapshot = {0};
    rehab_decision_trace_project(&trace, 1000000U, &snapshot);
    assert(snapshot.elapsed_sec == 2U);

    rehab_decision_trace_begin(&trace, 12U, "decision-012", 1000U);
    rehab_decision_trace_project(&trace, 2000000U, &snapshot);
    assert(snapshot.elapsed_sec == REHAB_DECISION_TRACE_ELAPSED_MAX_SEC);
}

int main(void)
{
    test_stage_transition_and_elapsed();
    test_success_freeze_and_generation_mismatch();
    test_failed_freeze_and_elapsed_cap();
    assert(strcmp(rehab_decision_trace_stage_name(
        REHAB_DECISION_TRACE_MODEL_WAIT), "MODEL_WAIT") == 0);
    puts("decision-trace host tests passed");
    return 0;
}
