/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define REHAB_SCORE_SESSION_ID_LEN  17
#define REHAB_SCORE_JOB_ID_LEN      17
#define REHAB_SCORE_TIMESTAMP_LEN   40
#define REHAB_SCORE_LAST_ERROR_LEN  48
#define REHAB_SCORE_TASK_NAME_LEN   97
#define REHAB_SCORE_INVALID_REASON_LEN 96
#define REHAB_SCORE_DATA_QUALITY_LEN   32
#define REHAB_SCORE_WARNINGS_LEN       256
#define REHAB_SCORE_VERSION_LEN        32
#define REHAB_SCORE_MODEL_VERSION_LEN  64

typedef enum {
    REHAB_SCORE_STATE_PENDING = 0,
    REHAB_SCORE_STATE_UPLOADING,
    REHAB_SCORE_STATE_ACCEPTED,
    REHAB_SCORE_STATE_SCORING,
    REHAB_SCORE_STATE_SUCCEEDED,
    REHAB_SCORE_STATE_FAILED,
} rehab_score_state_t;

typedef struct {
    char session_id[REHAB_SCORE_SESSION_ID_LEN];
    rehab_score_state_t state;
    uint32_t attempt;
    char pc_job_id[REHAB_SCORE_JOB_ID_LEN];
    char updated_at[REHAB_SCORE_TIMESTAMP_LEN];
    char last_error[REHAB_SCORE_LAST_ERROR_LEN];
} rehab_score_status_t;

typedef enum {
    REHAB_SCORE_QUERY_ERROR_NONE = 0,
    REHAB_SCORE_QUERY_ERROR_INVALID_SESSION_REF,
    REHAB_SCORE_QUERY_ERROR_SESSION_NOT_FOUND,
    REHAB_SCORE_QUERY_ERROR_SESSION_REF_AMBIGUOUS,
    REHAB_SCORE_QUERY_ERROR_STATUS_MISSING,
    REHAB_SCORE_QUERY_ERROR_STATUS_INVALID,
    REHAB_SCORE_QUERY_ERROR_RESULT_MISSING,
    REHAB_SCORE_QUERY_ERROR_RESULT_INVALID,
    REHAB_SCORE_QUERY_ERROR_STORAGE_NOT_READY,
    REHAB_SCORE_QUERY_ERROR_INTERNAL,
} rehab_score_query_error_t;

typedef struct {
    rehab_score_query_error_t error;
    char session_id[REHAB_SCORE_SESSION_ID_LEN];
    char task_name[REHAB_SCORE_TASK_NAME_LEN];

    rehab_score_state_t state;
    uint32_t attempt;
    char pc_job_id[REHAB_SCORE_JOB_ID_LEN];
    char updated_at[REHAB_SCORE_TIMESTAMP_LEN];
    char last_error[REHAB_SCORE_LAST_ERROR_LEN];

    bool result_available;
    bool valid;
    char invalid_reason[REHAB_SCORE_INVALID_REASON_LEN];

    bool average_score_available;
    double average_score;
    bool max_score_available;
    double max_score;
    bool min_score_available;
    double min_score;
    uint32_t repetition_count;
    uint32_t target_repetition_count;
    bool consistency_score_available;
    double consistency_score;

    char data_quality_level[REHAB_SCORE_DATA_QUALITY_LEN];
    uint32_t warning_count;
    bool warnings_truncated;
    char warnings_summary[REHAB_SCORE_WARNINGS_LEN];
    bool compensation_detected;
    uint32_t compensation_action_count;

    char score_version[REHAB_SCORE_VERSION_LEN];
    char model_version[REHAB_SCORE_MODEL_VERSION_LEN];
    char completed_at[REHAB_SCORE_TIMESTAMP_LEN];
} rehab_score_query_result_t;

/** Best-effort notification after result and succeeded status are persisted. */
typedef void (*rehab_score_success_observer_t)(const char *session_id,
                                               double average_score,
                                               void *user_ctx);

esp_err_t rehab_score_bridge_init(void);

/** Register the single score-success observer before bridge initialization. */
esp_err_t rehab_score_bridge_set_success_observer(
    rehab_score_success_observer_t observer,
    void *user_ctx);

esp_err_t rehab_score_bridge_notify_finalized(const char *session_id,
                                               const char *session_path);

esp_err_t rehab_score_bridge_get_status(const char *session_id,
                                        rehab_score_status_t *out_status);

esp_err_t rehab_score_bridge_get_latest_status(rehab_score_status_t *out_status);

/**
 * Read one persisted score status/result from the SD card.
 *
 * session_ref may be NULL/empty (latest valid session), a full
 * YYYYMMDD_TASKNNN id, or a unique TASKNNN suffix.  This API never performs
 * network I/O, never queues work, and never modifies session files.
 */
esp_err_t rehab_score_bridge_query(const char *session_ref,
                                   rehab_score_query_result_t *out_result);

esp_err_t rehab_score_bridge_retry(const char *session_id);

const char *rehab_score_state_to_string(rehab_score_state_t state);
const char *rehab_score_query_error_to_string(rehab_score_query_error_t error);

#ifdef __cplusplus
}
#endif
