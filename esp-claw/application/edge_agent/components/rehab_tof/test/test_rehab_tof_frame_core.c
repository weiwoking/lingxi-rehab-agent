// SPDX-License-Identifier: Apache-2.0

#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>

#include "rehab_tof_frame_core.h"
#include "rehab_tof_startup_core.h"

typedef struct {
    rehab_tof_startup_attempt_result_t results[3];
    uint8_t result_count;
    uint8_t attempt_calls;
    uint8_t cleanup_calls;
    uint8_t wait_calls;
    uint8_t live_resources;
    bool stopped;
    bool stop_during_wait;
    rehab_tof_startup_attempt_result_t observed_failure;
} startup_fixture_t;

static rehab_tof_startup_attempt_result_t startup_attempt(
    void *context,
    uint8_t attempt,
    uint8_t max_attempts)
{
    (void)max_attempts;
    startup_fixture_t *fixture = context;
    ++fixture->attempt_calls;
    ++fixture->live_resources;
    assert(attempt <= fixture->result_count);
    return fixture->results[attempt - 1U];
}

static int startup_cleanup(void *context)
{
    startup_fixture_t *fixture = context;
    ++fixture->cleanup_calls;
    assert(fixture->live_resources > 0U);
    --fixture->live_resources;
    return 0;
}

static bool startup_wait(void *context, uint32_t delay_ms)
{
    startup_fixture_t *fixture = context;
    assert(delay_ms == 50U);
    ++fixture->wait_calls;
    if (fixture->stop_during_wait) {
        fixture->stopped = true;
        return false;
    }
    return true;
}

static bool startup_stopped(void *context)
{
    return ((startup_fixture_t *)context)->stopped;
}

static void startup_observed(
    void *context,
    uint8_t attempt,
    uint8_t max_attempts,
    const rehab_tof_startup_attempt_result_t *failure,
    bool retrying,
    int cleanup_error)
{
    (void)attempt;
    (void)max_attempts;
    (void)retrying;
    assert(cleanup_error == 0);
    ((startup_fixture_t *)context)->observed_failure = *failure;
}

static rehab_tof_startup_run_result_t run_startup(startup_fixture_t *fixture)
{
    const rehab_tof_startup_policy_t policy = {
        .max_attempts = 3U,
        .retry_delay_ms = 50U,
    };
    const rehab_tof_startup_ops_t ops = {
        .context = fixture,
        .attempt = startup_attempt,
        .cleanup_failed_attempt = startup_cleanup,
        .wait_retry_delay = startup_wait,
        .stop_requested = startup_stopped,
        .failure_observed = startup_observed,
    };
    return rehab_tof_startup_run(&policy, &ops);
}

static void test_startup_retry(void)
{
    startup_fixture_t invalid_state_then_ok = {
        .results = {
            {.mapped_error = 0x105, .raw_error = 0x103,
             .operation = "vl53l5cx_is_alive", .retryable = true},
            {0},
        },
        .result_count = 2U,
    };
    rehab_tof_startup_run_result_t run =
        run_startup(&invalid_state_then_ok);
    assert(run.outcome == REHAB_TOF_STARTUP_SUCCESS);
    assert(run.attempts == 2U);
    assert(invalid_state_then_ok.cleanup_calls == 1U);
    assert(invalid_state_then_ok.wait_calls == 1U);
    assert(invalid_state_then_ok.live_resources == 1U);
    assert(invalid_state_then_ok.observed_failure.raw_error == 0x103);
    assert(invalid_state_then_ok.observed_failure.mapped_error == 0x105);

    startup_fixture_t timeout_then_ok = {
        .results = {
            {.mapped_error = -1, .raw_error = 0x107,
             .operation = "vl53l5cx_init", .retryable = true},
            {0},
        },
        .result_count = 2U,
    };
    run = run_startup(&timeout_then_ok);
    assert(run.outcome == REHAB_TOF_STARTUP_SUCCESS);
    assert(run.attempts == 2U && timeout_then_ok.cleanup_calls == 1U);

    startup_fixture_t all_fail = {
        .results = {
            {.mapped_error = -1, .raw_error = 0x107,
             .operation = "attempt1", .retryable = true},
            {.mapped_error = -1, .raw_error = 0x107,
             .operation = "attempt2", .retryable = true},
            {.mapped_error = -1, .raw_error = 0x107,
             .operation = "attempt3", .retryable = true},
        },
        .result_count = 3U,
    };
    run = run_startup(&all_fail);
    assert(run.outcome == REHAB_TOF_STARTUP_FAILED);
    assert(run.attempts == 3U && all_fail.cleanup_calls == 3U);
    assert(all_fail.live_resources == 0U && all_fail.wait_calls == 2U);

    startup_fixture_t fatal = {
        .results = {
            {.mapped_error = 0x101, .raw_error = 0x101,
             .operation = "allocation", .retryable = false},
        },
        .result_count = 1U,
    };
    run = run_startup(&fatal);
    assert(run.outcome == REHAB_TOF_STARTUP_FAILED);
    assert(run.attempts == 1U && fatal.cleanup_calls == 1U);
    assert(fatal.wait_calls == 0U && fatal.live_resources == 0U);

    startup_fixture_t stopped = {
        .results = {
            {.mapped_error = -1, .raw_error = 0x107,
             .operation = "transport", .retryable = true},
        },
        .result_count = 1U,
        .stop_during_wait = true,
    };
    run = run_startup(&stopped);
    assert(run.outcome == REHAB_TOF_STARTUP_STOPPED);
    assert(stopped.attempt_calls == 1U && stopped.cleanup_calls == 1U);
    assert(stopped.live_resources == 0U);
}

static void test_frame_summary(void)
{
    rehab_tof_frame_payload_t payload = {0};
    for (size_t i = 0; i < REHAB_TOF_FRAME_ZONE_COUNT; ++i) {
        payload.target_status[i] = 255U;
    }
    rehab_tof_frame_summary_t summary;
    rehab_tof_frame_summarize(&payload, &summary);
    assert(summary.valid_zones == 0U && summary.target0_zones == 64U);
    assert(summary.status255_zones == 64U);
    assert(summary.distance_min_mm == -1 && summary.sigma_min_mm == -1);

    payload.distance_mm[0] = 100;
    payload.range_sigma_mm[0] = 10U;
    payload.target_status[0] = 5U;
    payload.nb_target_detected[0] = 1U;
    payload.distance_mm[1] = 200;
    payload.range_sigma_mm[1] = 20U;
    payload.target_status[1] = 6U;
    payload.nb_target_detected[1] = 1U;
    payload.distance_mm[2] = 300;
    payload.range_sigma_mm[2] = 0U;
    payload.target_status[2] = 9U;
    payload.nb_target_detected[2] = 2U;
    payload.distance_mm[3] = 400;
    payload.range_sigma_mm[3] = 40U;
    payload.target_status[3] = 255U;
    payload.nb_target_detected[3] = 1U;
    payload.distance_mm[4] = 500;
    payload.range_sigma_mm[4] = 50U;
    payload.target_status[4] = 5U;
    payload.distance_mm[5] = 600;
    payload.range_sigma_mm[5] = 0U;
    payload.target_status[5] = 4U;
    payload.nb_target_detected[5] = 1U;
    rehab_tof_frame_payload_t original = payload;

    rehab_tof_frame_summarize(&payload, &summary);
    assert(summary.target0_zones == 59U && summary.target1_zones == 4U);
    assert(summary.target_gt1_zones == 1U);
    assert(summary.status5_zones == 2U && summary.status6_zones == 1U);
    assert(summary.status9_zones == 1U && summary.status255_zones == 59U);
    assert(summary.status_other_zones == 1U);
    assert(summary.valid_zones == 3U && summary.sigma_zero_zones == 2U);
    assert(summary.distance_min_mm == 100 &&
           summary.distance_median_mm == 200 &&
           summary.distance_max_mm == 300);
    assert(summary.sigma_min_mm == 0 && summary.sigma_median_mm == 10 &&
           summary.sigma_max_mm == 20);
    assert(memcmp(&payload, &original, sizeof(payload)) == 0);

    payload.target_status[3] = 5U;
    rehab_tof_frame_summarize(&payload, &summary);
    assert(summary.valid_zones == 4U);
    assert(summary.distance_median_mm == 250);
    assert(summary.sigma_median_mm == 15);

    memset(&payload, 0, sizeof(payload));
    for (size_t i = 0; i < REHAB_TOF_FRAME_ZONE_COUNT; ++i) {
        payload.distance_mm[i] = (int16_t)(100 + (int)i);
        payload.target_status[i] = 5U;
        payload.nb_target_detected[i] = 1U;
    }
    rehab_tof_frame_summarize(&payload, &summary);
    assert(summary.valid_zones == 64U && summary.sigma_zero_zones == 64U);
    assert(summary.sigma_min_mm == 0 && summary.sigma_median_mm == 0 &&
           summary.sigma_max_mm == 0);
}

static rehab_tof_frame_payload_t make_frame(int base)
{
    rehab_tof_frame_payload_t frame = {0};
    for (size_t i = 0; i < REHAB_TOF_FRAME_ZONE_COUNT; ++i) {
        frame.distance_mm[i] = (int16_t)(base + (int)i);
        frame.range_sigma_mm[i] = (uint16_t)(base + 100 + (int)i);
        frame.target_status[i] = (uint8_t)((i % 2U) == 0U ? 5U : 9U);
        frame.nb_target_detected[i] = 1U;
    }
    return frame;
}

static void test_diag_log_gate(void)
{
    const int64_t period_us = INT64_C(10000000);
    const int64_t first_frame_us = INT64_C(1234567);
    rehab_tof_diag_log_gate_t gate = {0};

    assert(rehab_tof_diag_log_gate_due(&gate, first_frame_us, period_us));
    assert(!rehab_tof_diag_log_gate_due(
        &gate, first_frame_us + INT64_C(9999000), period_us));
    assert(rehab_tof_diag_log_gate_due(
        &gate, first_frame_us + period_us, period_us));
    assert(rehab_tof_diag_log_gate_due(
        &gate, first_frame_us + 2 * period_us, period_us));

    gate.has_logged = true;
    gate.last_log_us = INT64_MAX - period_us;
    assert(!rehab_tof_diag_log_gate_due(&gate, INT64_MAX - 1, period_us));
    assert(rehab_tof_diag_log_gate_due(&gate, INT64_MAX, period_us));
}

static void test_diag_gate_does_not_change_publish_count(void)
{
    const int64_t period_us = INT64_C(10000000);
    rehab_tof_diag_log_gate_t gate = {0};
    rehab_tof_frame_collector_t collector = {0};
    rehab_tof_frame_payload_t frame = make_frame(3000);
    uint64_t sequence = 0;
    uint32_t publish_count = 0U;

    rehab_tof_frame_collector_start(&collector, 0U);
    for (int64_t frame_index = 0; frame_index < 301; ++frame_index) {
        assert(rehab_tof_frame_collector_ingest(&collector, &frame, &sequence) ==
               REHAB_TOF_FRAME_PUBLISHED);
        ++publish_count;
        (void)rehab_tof_diag_log_gate_due(
            &gate, frame_index * INT64_C(66667), period_us);
    }
    assert(publish_count == 301U);
    assert(sequence == publish_count);
}

int main(void)
{
    test_startup_retry();
    test_frame_summary();
    test_diag_log_gate();
    test_diag_gate_does_not_change_publish_count();
    rehab_tof_frame_collector_t collector = {0};
    rehab_tof_frame_payload_t first = make_frame(1000);
    rehab_tof_frame_payload_t second = make_frame(2000);
    rehab_tof_frame_payload_t copied = {0};
    uint64_t sequence = 0;

    rehab_tof_frame_collector_start(&collector, 1U);
    assert(rehab_tof_frame_collector_ingest(&collector, &first, &sequence) ==
           REHAB_TOF_FRAME_STARTUP_DISCARDED);
    assert(!collector.latest_valid);
    assert(rehab_tof_frame_collector_ingest(&collector, &first, &sequence) ==
           REHAB_TOF_FRAME_PUBLISHED);
    assert(sequence == 1U);
    assert(rehab_tof_frame_collector_copy_latest(&collector, 0U, &copied, &sequence));
    assert(sequence == 1U);
    assert(memcmp(&first, &copied, sizeof(first)) == 0);
    for (size_t i = 0; i < REHAB_TOF_FRAME_ZONE_COUNT; ++i) {
        assert(copied.distance_mm[i] == first.distance_mm[i]);
        assert(copied.range_sigma_mm[i] == first.range_sigma_mm[i]);
        assert(copied.target_status[i] == first.target_status[i]);
        assert(copied.nb_target_detected[i] == first.nb_target_detected[i]);
    }
    assert(!rehab_tof_frame_collector_copy_latest(&collector, 1U, &copied, &sequence));

    assert(rehab_tof_frame_collector_ingest(&collector, &second, &sequence) ==
           REHAB_TOF_FRAME_PUBLISHED);
    assert(sequence == 2U);
    assert(rehab_tof_frame_collector_copy_latest(&collector, 1U, &copied, &sequence));
    assert(memcmp(&second, &copied, sizeof(second)) == 0);
    assert(copied.distance_mm[0] == 2000);
    assert(copied.range_sigma_mm[63] == 2163);

    assert(rehab_tof_frame_validate_io(0, 0, 448U, 448U) == REHAB_TOF_IO_OK);
    assert(rehab_tof_frame_validate_io(1, 0x107, 448U, 0U) ==
           REHAB_TOF_IO_TIMEOUT);
    assert(rehab_tof_frame_validate_io(1, 0x108, 448U, 0U) ==
           REHAB_TOF_IO_NACK);
    assert(rehab_tof_frame_validate_io(0, 0, 448U, 447U) ==
           REHAB_TOF_IO_SHORT_READ);
    assert(rehab_tof_frame_validate_io(1, 1, 448U, 0U) ==
           REHAB_TOF_IO_DRIVER_ERROR);

    rehab_tof_frame_collector_stop(&collector);
    assert(!collector.latest_valid);
    assert(!rehab_tof_frame_collector_copy_latest(&collector, 0U, &copied, &sequence));
    assert(rehab_tof_frame_collector_ingest(&collector, &first, &sequence) ==
           REHAB_TOF_FRAME_REJECTED);

    /* A bounded stream recovery invalidates stale data but not sequence order. */
    rehab_tof_frame_collector_start(&collector, 1U);
    assert(!collector.latest_valid);
    assert(rehab_tof_frame_collector_ingest(&collector, &first, &sequence) ==
           REHAB_TOF_FRAME_STARTUP_DISCARDED);
    assert(rehab_tof_frame_collector_ingest(&collector, &second, &sequence) ==
           REHAB_TOF_FRAME_PUBLISHED);
    assert(sequence == 3U);
    assert(rehab_tof_frame_collector_copy_latest(&collector, 2U, &copied,
                                                  &sequence));
    assert(memcmp(&second, &copied, sizeof(second)) == 0);

    puts("rehab_tof_frame_core: all tests passed");
    return 0;
}
