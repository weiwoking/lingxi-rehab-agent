/* SPDX-License-Identifier: Apache-2.0 */
#include "rehab_fatigue_rules.h"

#include <ctype.h>
#include <math.h>
#include <string.h>

void rehab_fatigue_rules_reset(rehab_fatigue_rules_state_t *state)
{
    if (state != NULL) {
        memset(state, 0, sizeof(*state));
    }
}

static bool valid_date(const char date[REHAB_FATIGUE_DATE_LEN])
{
    if (date == NULL || strlen(date) != 8) {
        return false;
    }
    for (size_t index = 0; index < 8; ++index) {
        if (!isdigit((unsigned char)date[index])) {
            return false;
        }
    }
    return true;
}

bool rehab_fatigue_rules_rollover_day(
    rehab_fatigue_rules_state_t *state,
    const char date[REHAB_FATIGUE_DATE_LEN])
{
    if (state == NULL || !valid_date(date)) {
        return false;
    }
    if (strcmp(state->count_date, date) == 0) {
        return false;
    }
    memcpy(state->count_date, date, REHAB_FATIGUE_DATE_LEN);
    state->count_today = 0;
    return true;
}

int rehab_fatigue_rules_score_delta(double average_score,
                                    uint32_t ordinal_today)
{
    if (!isfinite(average_score) || ordinal_today == 0) {
        return 0;
    }
    bool middle_band = average_score >= 50.0 && average_score <= 70.0;
    if (ordinal_today <= 3) {
        return middle_band ? 8 : 5;
    }
    return middle_band ? 12 : 8;
}

bool rehab_fatigue_rules_extract_session_date(
    const char *session_id,
    char out_date[REHAB_FATIGUE_DATE_LEN])
{
    if (session_id == NULL || out_date == NULL ||
            strlen(session_id) != REHAB_FATIGUE_SESSION_ID_LEN - 1U ||
            memcmp(session_id + 8, "_TASK", 5) != 0) {
        return false;
    }
    for (size_t index = 0; index < 8; ++index) {
        if (!isdigit((unsigned char)session_id[index])) {
            return false;
        }
    }
    for (size_t index = 13; index < 16; ++index) {
        if (!isdigit((unsigned char)session_id[index])) {
            return false;
        }
    }
    memcpy(out_date, session_id, 8);
    out_date[8] = '\0';
    return true;
}

bool rehab_fatigue_rules_contains_session(
    const rehab_fatigue_rules_state_t *state,
    const char *session_id)
{
    if (state == NULL || session_id == NULL) {
        return false;
    }
    for (size_t index = 0; index < state->counted_session_count; ++index) {
        if (strcmp(state->counted_session_ids[index], session_id) == 0) {
            return true;
        }
    }
    return false;
}

static void append_session(rehab_fatigue_rules_state_t *state,
                           const char *session_id)
{
    if (state->counted_session_count == REHAB_FATIGUE_DEDUP_CAPACITY) {
        memmove(state->counted_session_ids[0],
                state->counted_session_ids[1],
                (REHAB_FATIGUE_DEDUP_CAPACITY - 1U) *
                    REHAB_FATIGUE_SESSION_ID_LEN);
        state->counted_session_count--;
    }
    memcpy(state->counted_session_ids[state->counted_session_count],
           session_id,
           REHAB_FATIGUE_SESSION_ID_LEN);
    state->counted_session_count++;
}

rehab_fatigue_event_result_t rehab_fatigue_rules_apply_score(
    rehab_fatigue_rules_state_t *state,
    const char *session_id,
    double average_score,
    const char date[REHAB_FATIGUE_DATE_LEN],
    int64_t updated_at_epoch)
{
    char session_date[REHAB_FATIGUE_DATE_LEN];
    if (state == NULL || date == NULL || strlen(date) != 8 ||
            !rehab_fatigue_rules_extract_session_date(session_id, session_date) ||
            !isfinite(average_score) || !valid_date(date)) {
        return REHAB_FATIGUE_EVENT_INVALID;
    }
    if (rehab_fatigue_rules_contains_session(state, session_id)) {
        return REHAB_FATIGUE_EVENT_DUPLICATE;
    }
    if (strcmp(state->count_date, date) != 0 &&
            !rehab_fatigue_rules_rollover_day(state, date)) {
        return REHAB_FATIGUE_EVENT_INVALID;
    }
    uint32_t ordinal = state->count_today < 3U
                           ? state->count_today + 1U : 4U;
    int delta = rehab_fatigue_rules_score_delta(average_score, ordinal);
    if (delta <= 0) {
        return REHAB_FATIGUE_EVENT_INVALID;
    }
    state->fatigue += delta;
    if (state->fatigue > 100) {
        state->fatigue = 100;
    }
    if (state->count_today < UINT32_MAX) {
        state->count_today++;
    }
    memcpy(state->latest_session_id,
           session_id,
           REHAB_FATIGUE_SESSION_ID_LEN);
    state->latest_score_available = true;
    state->latest_score = average_score;
    state->latest_delta = delta;
    state->updated_at_epoch = updated_at_epoch;
    append_session(state, session_id);
    return REHAB_FATIGUE_EVENT_APPLIED;
}

int rehab_fatigue_rules_apply_decay(rehab_fatigue_rules_state_t *state,
                                    int64_t now_epoch,
                                    bool time_trusted)
{
    if (state == NULL || state->decay_paused || !time_trusted ||
            now_epoch <= 0) {
        return 0;
    }
    if (state->idle_since_epoch <= 0 || now_epoch < state->idle_since_epoch) {
        state->idle_since_epoch = now_epoch;
        return 0;
    }
    int64_t full_minutes = (now_epoch - state->idle_since_epoch) / 60;
    if (full_minutes <= 0) {
        return 0;
    }
    int64_t requested = full_minutes * REHAB_FATIGUE_RECOVERY_PER_MINUTE;
    int recovered = requested > state->fatigue ? state->fatigue : (int)requested;
    state->fatigue -= recovered;
    state->idle_since_epoch += full_minutes * 60;
    state->updated_at_epoch = now_epoch;
    return recovered;
}

bool rehab_fatigue_rules_training_allowed(int fatigue)
{
    return fatigue < REHAB_FATIGUE_LIMIT;
}

uint32_t rehab_fatigue_rules_minutes_until_allowed(int fatigue)
{
    if (rehab_fatigue_rules_training_allowed(fatigue)) {
        return 0;
    }
    return (uint32_t)((fatigue - REHAB_FATIGUE_LIMIT +
                       REHAB_FATIGUE_RECOVERY_PER_MINUTE) /
                      REHAB_FATIGUE_RECOVERY_PER_MINUTE);
}
