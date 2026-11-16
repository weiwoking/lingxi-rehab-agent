// SPDX-License-Identifier: Apache-2.0

#include "rehab_tof_startup_core.h"

#include <stddef.h>

rehab_tof_startup_run_result_t rehab_tof_startup_run(
    const rehab_tof_startup_policy_t *policy,
    const rehab_tof_startup_ops_t *ops)
{
    rehab_tof_startup_run_result_t run = {
        .outcome = REHAB_TOF_STARTUP_INVALID_ARGUMENT,
    };
    if (policy == NULL || ops == NULL || policy->max_attempts == 0U ||
        ops->attempt == NULL || ops->cleanup_failed_attempt == NULL ||
        ops->wait_retry_delay == NULL || ops->stop_requested == NULL) {
        return run;
    }

    for (uint8_t attempt = 1U; attempt <= policy->max_attempts; ++attempt) {
        if (ops->stop_requested(ops->context)) {
            run.outcome = REHAB_TOF_STARTUP_STOPPED;
            return run;
        }

        run.attempts = attempt;
        rehab_tof_startup_attempt_result_t result =
            ops->attempt(ops->context, attempt, policy->max_attempts);
        if (result.mapped_error == 0) {
            if (ops->stop_requested(ops->context)) {
                run.cleanup_error =
                    ops->cleanup_failed_attempt(ops->context);
                run.outcome = run.cleanup_error == 0
                    ? REHAB_TOF_STARTUP_STOPPED
                    : REHAB_TOF_STARTUP_CLEANUP_FAILED;
                return run;
            }
            run.outcome = REHAB_TOF_STARTUP_SUCCESS;
            return run;
        }

        run.last_failure = result;
        run.cleanup_error = ops->cleanup_failed_attempt(ops->context);
        bool stopped = ops->stop_requested(ops->context);
        bool retrying = run.cleanup_error == 0 && !stopped &&
                        result.retryable && attempt < policy->max_attempts;
        if (ops->failure_observed != NULL) {
            ops->failure_observed(ops->context,
                                  attempt,
                                  policy->max_attempts,
                                  &result,
                                  retrying,
                                  run.cleanup_error);
        }
        if (run.cleanup_error != 0) {
            run.outcome = REHAB_TOF_STARTUP_CLEANUP_FAILED;
            return run;
        }
        if (stopped) {
            run.outcome = REHAB_TOF_STARTUP_STOPPED;
            return run;
        }
        if (!retrying) {
            run.outcome = REHAB_TOF_STARTUP_FAILED;
            return run;
        }
        if (!ops->wait_retry_delay(ops->context, policy->retry_delay_ms) ||
            ops->stop_requested(ops->context)) {
            run.outcome = REHAB_TOF_STARTUP_STOPPED;
            return run;
        }
    }

    run.outcome = REHAB_TOF_STARTUP_FAILED;
    return run;
}
