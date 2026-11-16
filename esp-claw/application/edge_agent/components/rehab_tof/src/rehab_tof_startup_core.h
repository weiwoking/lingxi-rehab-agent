// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    int mapped_error;
    int raw_error;
    const char *operation;
    bool retryable;
} rehab_tof_startup_attempt_result_t;

typedef enum {
    REHAB_TOF_STARTUP_SUCCESS = 0,
    REHAB_TOF_STARTUP_FAILED,
    REHAB_TOF_STARTUP_STOPPED,
    REHAB_TOF_STARTUP_CLEANUP_FAILED,
    REHAB_TOF_STARTUP_INVALID_ARGUMENT,
} rehab_tof_startup_outcome_t;

typedef struct {
    uint8_t max_attempts;
    uint32_t retry_delay_ms;
} rehab_tof_startup_policy_t;

typedef struct {
    void *context;
    rehab_tof_startup_attempt_result_t (*attempt)(void *context,
                                                   uint8_t attempt,
                                                   uint8_t max_attempts);
    int (*cleanup_failed_attempt)(void *context);
    bool (*wait_retry_delay)(void *context, uint32_t delay_ms);
    bool (*stop_requested)(void *context);
    void (*failure_observed)(void *context,
                             uint8_t attempt,
                             uint8_t max_attempts,
                             const rehab_tof_startup_attempt_result_t *failure,
                             bool retrying,
                             int cleanup_error);
} rehab_tof_startup_ops_t;

typedef struct {
    rehab_tof_startup_outcome_t outcome;
    uint8_t attempts;
    int cleanup_error;
    rehab_tof_startup_attempt_result_t last_failure;
} rehab_tof_startup_run_result_t;

rehab_tof_startup_run_result_t rehab_tof_startup_run(
    const rehab_tof_startup_policy_t *policy,
    const rehab_tof_startup_ops_t *ops);
