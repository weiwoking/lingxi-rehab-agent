/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define REHAB_FATIGUE_STATUS_DATE_LEN       9
#define REHAB_FATIGUE_STATUS_SESSION_ID_LEN 17
#define REHAB_FATIGUE_STATUS_LIMIT           40
#define REHAB_FATIGUE_STATUS_RECOVERY_RATE   5
#define REHAB_FATIGUE_STATUS_SCHEMA_VERSION  "rehab.fatigue.v1"

typedef struct {
    bool available;
    int fatigue;
    bool training_allowed;
    char count_date[REHAB_FATIGUE_STATUS_DATE_LEN];
    uint32_t count_today;
    char latest_session_id[REHAB_FATIGUE_STATUS_SESSION_ID_LEN];
    bool latest_score_available;
    double latest_score;
    int latest_delta;
    size_t counted_session_count;
    bool decay_paused;
    bool time_trusted;
    uint32_t minutes_until_allowed;
} rehab_fatigue_status_t;

typedef struct {
    bool available;
    bool blocked;
    int fatigue;
    uint32_t minutes_until_allowed;
} rehab_fatigue_gate_t;

esp_err_t rehab_fatigue_init(void);
esp_err_t rehab_fatigue_on_score_succeeded(const char *session_id,
                                           double average_score);
esp_err_t rehab_fatigue_before_training(rehab_fatigue_gate_t *out_gate);
esp_err_t rehab_fatigue_on_training_started(void);
esp_err_t rehab_fatigue_on_training_stopped(void);
esp_err_t rehab_fatigue_get_status(rehab_fatigue_status_t *out_status);

#ifdef __cplusplus
}
#endif
