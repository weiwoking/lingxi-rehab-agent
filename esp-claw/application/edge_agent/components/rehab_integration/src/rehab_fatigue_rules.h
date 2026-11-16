/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define REHAB_FATIGUE_SCHEMA_VERSION       "rehab.fatigue.v1"
#define REHAB_FATIGUE_LIMIT                40
#define REHAB_FATIGUE_RECOVERY_PER_MINUTE  5
#define REHAB_FATIGUE_DEDUP_CAPACITY       32
#define REHAB_FATIGUE_SESSION_ID_LEN       17
#define REHAB_FATIGUE_DATE_LEN             9

typedef struct {
    int fatigue;
    char count_date[REHAB_FATIGUE_DATE_LEN];
    uint32_t count_today;
    char counted_session_ids[REHAB_FATIGUE_DEDUP_CAPACITY]
                            [REHAB_FATIGUE_SESSION_ID_LEN];
    size_t counted_session_count;
    int64_t idle_since_epoch;
    bool decay_paused;
    char latest_session_id[REHAB_FATIGUE_SESSION_ID_LEN];
    bool latest_score_available;
    double latest_score;
    int latest_delta;
    int64_t updated_at_epoch;
} rehab_fatigue_rules_state_t;

typedef enum {
    REHAB_FATIGUE_EVENT_APPLIED = 0,
    REHAB_FATIGUE_EVENT_DUPLICATE,
    REHAB_FATIGUE_EVENT_INVALID,
} rehab_fatigue_event_result_t;

void rehab_fatigue_rules_reset(rehab_fatigue_rules_state_t *state);
bool rehab_fatigue_rules_rollover_day(
    rehab_fatigue_rules_state_t *state,
    const char date[REHAB_FATIGUE_DATE_LEN]);
int rehab_fatigue_rules_score_delta(double average_score,
                                    uint32_t ordinal_today);
bool rehab_fatigue_rules_extract_session_date(
    const char *session_id,
    char out_date[REHAB_FATIGUE_DATE_LEN]);
bool rehab_fatigue_rules_contains_session(
    const rehab_fatigue_rules_state_t *state,
    const char *session_id);
rehab_fatigue_event_result_t rehab_fatigue_rules_apply_score(
    rehab_fatigue_rules_state_t *state,
    const char *session_id,
    double average_score,
    const char date[REHAB_FATIGUE_DATE_LEN],
    int64_t updated_at_epoch);
int rehab_fatigue_rules_apply_decay(rehab_fatigue_rules_state_t *state,
                                    int64_t now_epoch,
                                    bool time_trusted);
bool rehab_fatigue_rules_training_allowed(int fatigue);
uint32_t rehab_fatigue_rules_minutes_until_allowed(int fatigue);

