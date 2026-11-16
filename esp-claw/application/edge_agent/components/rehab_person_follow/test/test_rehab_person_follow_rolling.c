// SPDX-License-Identifier: Apache-2.0
#include <string.h>

#include "unity.h"
#include "../src/rehab_person_follow_priv.h"

static rehab_person_follow_handle_t rolling_state_handle(bool action_enabled)
{
    rehab_person_follow_config_t config =
        REHAB_PERSON_FOLLOW_CONFIG_DEFAULT((rehab_car_handle_t)1);
    config.action_enabled = action_enabled;
    rehab_person_follow_handle_t handle = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_create(&config, &handle));
    handle->generation = 7U;
    handle->rolling_generation = 7U;
    handle->running = true;
    handle->owner_acquired = true;
    handle->rolling_task_alive = action_enabled;
    handle->rolling_task = action_enabled ? (TaskHandle_t)1 : NULL;
    handle->car_lease.owner = REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW;
    handle->car_lease.token = 11U;
    return handle;
}

static void rolling_state_delete(rehab_person_follow_handle_t handle)
{
    (void)xTimerStop(handle->rolling_lease_timer, 0);
    handle->running = false;
    handle->owner_acquired = false;
    handle->rolling_task_alive = false;
    handle->rolling_task_busy = false;
    handle->rolling_task = NULL;
    handle->forward_rolling = false;
    memset(&handle->car_lease, 0, sizeof(handle->car_lease));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_delete(handle));
}

TEST_CASE("CENTER starts and renews one 900ms rolling lease",
          "[person_follow][rolling][center]")
{
    rehab_person_follow_handle_t handle = rolling_state_handle(true);
    TaskHandle_t task = handle->rolling_task;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_rolling_confirm_center(
        handle, 7U, 1000000));
    TEST_ASSERT_TRUE(handle->forward_rolling);
    TEST_ASSERT_EQUAL_INT64(1900000, handle->forward_lease_deadline_us);
    TEST_ASSERT_EQUAL_PTR(task, handle->rolling_task);

    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_rolling_confirm_center(
        handle, 7U, 1600000));
    TEST_ASSERT_EQUAL_INT64(2500000, handle->forward_lease_deadline_us);
    TEST_ASSERT_EQUAL_PTR(task, handle->rolling_task);
    rolling_state_delete(handle);
}

TEST_CASE("rolling completes consecutive 500ms segments without settle state",
          "[person_follow][rolling][segments]")
{
    rehab_person_follow_handle_t handle = rolling_state_handle(true);
    handle->decision_context.turn_hold_ms = 225U;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_rolling_confirm_center(
        handle, 7U, 1000000));
    handle->forward_lease_deadline_us = 4000000;
    for (uint32_t i = 0; i < 3U; ++i) {
        int64_t now_us = 1100000 + (int64_t)i * 500000;
        TEST_ASSERT_TRUE(rehab_person_follow_rolling_segment_allowed(
            handle, 7U, now_us));
        handle->rolling_task_busy = true;
        TEST_ASSERT_TRUE(rehab_person_follow_rolling_segment_finished(
            handle, 7U, ESP_OK, now_us + 500000));
        TEST_ASSERT_FALSE(handle->rolling_task_busy);
    }
    TEST_ASSERT_EQUAL_UINT32(3U, handle->forward_segments_completed);
    TEST_ASSERT_TRUE(handle->decision_context.has_forwarded_once);
    TEST_ASSERT_FALSE(handle->decision_context.turn_check_pending);
    TEST_ASSERT_EQUAL_UINT32(225U,
                             handle->decision_context.turn_hold_ms);
    rolling_state_delete(handle);
}

TEST_CASE("lease expiry prevents another segment and records STOP reason",
          "[person_follow][rolling][lease]")
{
    rehab_person_follow_handle_t handle = rolling_state_handle(true);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_rolling_confirm_center(
        handle, 7U, 1000000));
    handle->owner_acquired = false; /* Pure state test: suppress transport. */
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_rolling_preempt(
        handle, 7U, "lease_expired", false));
    TEST_ASSERT_FALSE(handle->forward_rolling);
    TEST_ASSERT_EQUAL_INT64(0, handle->forward_lease_deadline_us);
    TEST_ASSERT_EQUAL_STRING("lease_expired", handle->rolling_stop_reason);
    TEST_ASSERT_FALSE(rehab_person_follow_rolling_segment_allowed(
        handle, 7U, 1900001));
    rolling_state_delete(handle);
}

TEST_CASE("LEFT RIGHT NO_TARGET and technical failures invalidate rolling",
          "[person_follow][rolling][preempt]")
{
    const char *reasons[] = {
        "target_left", "target_right", "no_target",
        "http", "camera", "json", "first_align_stop", "final_align_stop",
    };
    for (size_t i = 0; i < sizeof(reasons) / sizeof(reasons[0]); ++i) {
        rehab_person_follow_handle_t handle = rolling_state_handle(true);
        TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_rolling_confirm_center(
            handle, 7U, 1000000));
        handle->owner_acquired = false; /* Pure state test: suppress transport. */
        TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_rolling_preempt(
            handle, 7U, reasons[i], false));
        TEST_ASSERT_FALSE(handle->forward_rolling);
        TEST_ASSERT_EQUAL_STRING(reasons[i], handle->rolling_stop_reason);
        rolling_state_delete(handle);
    }
}

TEST_CASE("disabled actions and stale generations cannot start rolling",
          "[person_follow][rolling][safety]")
{
    rehab_person_follow_handle_t disabled = rolling_state_handle(false);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE,
        rehab_person_follow_rolling_confirm_center(disabled, 7U, 1000000));
    TEST_ASSERT_FALSE(disabled->forward_rolling);
    rolling_state_delete(disabled);

    rehab_person_follow_handle_t stale = rolling_state_handle(true);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE,
        rehab_person_follow_rolling_confirm_center(stale, 6U, 1000000));
    TEST_ASSERT_FALSE(stale->forward_rolling);
    rolling_state_delete(stale);
}
