/* SPDX-License-Identifier: Apache-2.0 */
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "rehab_fatigue_rules.h"

static void test_score_boundaries(void)
{
    assert(rehab_fatigue_rules_score_delta(49.99, 1) == 5);
    assert(rehab_fatigue_rules_score_delta(50.00, 1) == 8);
    assert(rehab_fatigue_rules_score_delta(70.00, 3) == 8);
    assert(rehab_fatigue_rules_score_delta(70.01, 3) == 5);
    assert(rehab_fatigue_rules_score_delta(49.99, 4) == 8);
    assert(rehab_fatigue_rules_score_delta(50.00, 4) == 12);
    assert(rehab_fatigue_rules_score_delta(70.00, 9) == 12);
    assert(rehab_fatigue_rules_score_delta(70.01, 9) == 8);
    assert(rehab_fatigue_rules_score_delta(NAN, 1) == 0);
}

static void test_natural_day_rollover(void)
{
    rehab_fatigue_rules_state_t state;
    rehab_fatigue_rules_reset(&state);
    memcpy(state.count_date, "20260807", REHAB_FATIGUE_DATE_LEN);
    state.count_today = 3;

    assert(!rehab_fatigue_rules_rollover_day(&state, "20260807"));
    assert(state.count_today == 3);
    assert(rehab_fatigue_rules_rollover_day(&state, "20260808"));
    assert(strcmp(state.count_date, "20260808") == 0);
    assert(state.count_today == 0);
    assert(!rehab_fatigue_rules_rollover_day(&state, "TASK0004"));
    assert(strcmp(state.count_date, "20260808") == 0);
    assert(state.count_today == 0);
}

static void test_events_and_restart_dedup(void)
{
    rehab_fatigue_rules_state_t state;
    rehab_fatigue_rules_reset(&state);
    assert(rehab_fatigue_rules_apply_score(&state, "20260722_TASK005", 60.0,
                                           "20260722", 1000) ==
           REHAB_FATIGUE_EVENT_APPLIED);
    assert(state.fatigue == 8 && state.count_today == 1);
    assert(rehab_fatigue_rules_apply_score(&state, "20260722_TASK005", 60.0,
                                           "20260722", 1001) ==
           REHAB_FATIGUE_EVENT_DUPLICATE);
    assert(state.fatigue == 8 && state.count_today == 1);

    rehab_fatigue_rules_state_t restarted = state;
    assert(rehab_fatigue_rules_apply_score(&restarted, "20260722_TASK005", 60.0,
                                           "20260722", 1002) ==
           REHAB_FATIGUE_EVENT_DUPLICATE);
    assert(restarted.fatigue == 8 && restarted.count_today == 1);

    assert(rehab_fatigue_rules_apply_score(&restarted, "20260722_TASK003", 40.0,
                                           "20260722", 1003) ==
           REHAB_FATIGUE_EVENT_APPLIED);
    assert(rehab_fatigue_rules_apply_score(&restarted, "20260722_TASK009", 80.0,
                                           "20260722", 1004) ==
           REHAB_FATIGUE_EVENT_APPLIED);
    assert(restarted.count_today == 3 && restarted.fatigue == 18);
    assert(rehab_fatigue_rules_apply_score(&restarted, "20260723_TASK001", 60.0,
                                           "20260723", 1005) ==
           REHAB_FATIGUE_EVENT_APPLIED);
    assert(strcmp(restarted.count_date, "20260723") == 0);
    assert(restarted.count_today == 1 && restarted.latest_delta == 8);
}

static void test_dedup_eviction(void)
{
    rehab_fatigue_rules_state_t state;
    rehab_fatigue_rules_reset(&state);
    char session[REHAB_FATIGUE_SESSION_ID_LEN];
    for (int index = 0; index <= REHAB_FATIGUE_DEDUP_CAPACITY; ++index) {
        int written = snprintf(session, sizeof(session),
                               "20260722_TASK%03d", index);
        assert(written == 16);
        assert(rehab_fatigue_rules_apply_score(&state, session, 40.0,
                                               "20260722", 2000 + index) ==
               REHAB_FATIGUE_EVENT_APPLIED);
    }
    assert(state.counted_session_count == REHAB_FATIGUE_DEDUP_CAPACITY);
    assert(!rehab_fatigue_rules_contains_session(&state,
                                                 "20260722_TASK000"));
    assert(rehab_fatigue_rules_contains_session(&state,
                                                "20260722_TASK032"));
}

static void test_decay_and_gate(void)
{
    const int64_t anchor = 1000;
    const struct {
        int64_t elapsed;
        int expected_fatigue;
        int64_t expected_anchor;
    } cases[] = {
        {59, 40, anchor},
        {60, 35, anchor + 60},
        {119, 35, anchor + 60},
        {120, 30, anchor + 120},
        {140, 30, anchor + 120},
    };
    for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); ++index) {
        rehab_fatigue_rules_state_t state;
        rehab_fatigue_rules_reset(&state);
        state.fatigue = 40;
        state.idle_since_epoch = anchor;
        (void)rehab_fatigue_rules_apply_decay(&state,
                                              anchor + cases[index].elapsed,
                                              true);
        assert(state.fatigue == cases[index].expected_fatigue);
        assert(state.idle_since_epoch == cases[index].expected_anchor);
    }

    rehab_fatigue_rules_state_t paused;
    rehab_fatigue_rules_reset(&paused);
    paused.fatigue = 40;
    paused.idle_since_epoch = anchor;
    paused.decay_paused = true;
    assert(rehab_fatigue_rules_apply_decay(&paused, anchor + 600, true) == 0);
    assert(paused.fatigue == 40 && paused.idle_since_epoch == anchor);

    rehab_fatigue_rules_state_t clamp;
    rehab_fatigue_rules_reset(&clamp);
    clamp.fatigue = 3;
    clamp.idle_since_epoch = anchor;
    assert(rehab_fatigue_rules_apply_decay(&clamp, anchor + 60, true) == 3);
    assert(clamp.fatigue == 0);
}

static void test_training_limit(void)
{
    assert(rehab_fatigue_rules_training_allowed(29));
    assert(rehab_fatigue_rules_training_allowed(30));
    assert(rehab_fatigue_rules_training_allowed(31));
    assert(rehab_fatigue_rules_training_allowed(39));
    assert(!rehab_fatigue_rules_training_allowed(40));
    assert(!rehab_fatigue_rules_training_allowed(41));
    assert(rehab_fatigue_rules_minutes_until_allowed(29) == 0);
    assert(rehab_fatigue_rules_minutes_until_allowed(30) == 0);
    assert(rehab_fatigue_rules_minutes_until_allowed(39) == 0);
    assert(rehab_fatigue_rules_minutes_until_allowed(40) == 1);
    assert(rehab_fatigue_rules_minutes_until_allowed(41) == 1);
    assert(rehab_fatigue_rules_minutes_until_allowed(45) == 2);
}

int main(void)
{
    test_score_boundaries();
    test_natural_day_rollover();
    test_events_and_restart_dedup();
    test_dedup_eviction();
    test_decay_and_gate();
    test_training_limit();
    puts("rehab fatigue rules: all tests passed");
    return 0;
}

