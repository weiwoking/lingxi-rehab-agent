// SPDX-License-Identifier: Apache-2.0
#include <string.h>

#include "unity.h"
#include "../src/rehab_person_follow_priv.h"

static bool s_mock_car_calls;
static esp_err_t s_move_result;
static uint32_t s_move_calls;
static uint32_t s_stop_calls;
static rehab_car_direction_t s_direction;
static uint8_t s_speed;
static uint32_t s_hold_ms;
static char s_command_log[16];
static size_t s_command_log_length;

static void record_command(char command)
{
    if (s_command_log_length + 1U < sizeof(s_command_log)) {
        s_command_log[s_command_log_length++] = command;
        s_command_log[s_command_log_length] = '\0';
    }
}

esp_err_t rehab_person_follow_car_move(
    rehab_car_handle_t car,
    const rehab_car_control_lease_t *lease,
    rehab_car_direction_t direction,
    uint8_t speed_level,
    uint32_t hold_ms)
{
    if (!s_mock_car_calls) {
        return rehab_car_control_move(car, lease, direction,
                                      speed_level, hold_ms);
    }
    ++s_move_calls;
    s_direction = direction;
    s_speed = speed_level;
    s_hold_ms = hold_ms;
    record_command(direction == REHAB_CAR_DIRECTION_FORWARD ? 'F' :
                   direction == REHAB_CAR_DIRECTION_ROTATE_CCW ? 'L' : 'R');
    return s_move_result;
}

esp_err_t rehab_person_follow_car_stop(
    rehab_car_handle_t car,
    const rehab_car_control_lease_t *lease)
{
    if (!s_mock_car_calls) {
        return rehab_car_control_stop(car, lease);
    }
    ++s_stop_calls;
    record_command('S');
    return ESP_OK;
}

static rehab_person_follow_handle_t action_handle(bool enabled)
{
    rehab_person_follow_config_t config =
        REHAB_PERSON_FOLLOW_CONFIG_DEFAULT((rehab_car_handle_t)1);
    config.action_enabled = enabled;
    rehab_person_follow_handle_t handle = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_create(&config, &handle));
    handle->generation = 7U;
    handle->owner_acquired = true;
    handle->car_lease.owner = REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW;
    handle->car_lease.token = 11U;
    s_mock_car_calls = true;
    s_move_result = ESP_OK;
    s_move_calls = 0U;
    s_stop_calls = 0U;
    s_direction = REHAB_CAR_DIRECTION_STOP;
    s_speed = 0U;
    s_hold_ms = 0U;
    memset(s_command_log, 0, sizeof(s_command_log));
    s_command_log_length = 0U;
    return handle;
}

static void delete_action_handle(rehab_person_follow_handle_t handle)
{
    handle->owner_acquired = false;
    s_mock_car_calls = false;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_delete(handle));
}

static void execute_and_expect(
    rehab_person_follow_action_t action,
    rehab_car_direction_t direction,
    uint32_t planned_hold_ms)
{
    rehab_person_follow_handle_t handle = action_handle(true);
    uint32_t hold_ms = 0U;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_execute_action(
        handle, 7U, action, planned_hold_ms, &hold_ms));
    TEST_ASSERT_EQUAL_UINT32(1U, s_move_calls);
    TEST_ASSERT_EQUAL_UINT32(0U, s_stop_calls);
    TEST_ASSERT_EQUAL(direction, s_direction);
    TEST_ASSERT_EQUAL_UINT8(3U, s_speed);
    TEST_ASSERT_EQUAL_UINT32(planned_hold_ms, s_hold_ms);
    TEST_ASSERT_EQUAL_UINT32(planned_hold_ms, hold_ms);
    delete_action_handle(handle);
}

TEST_CASE("successful action settle follows actual action not hold duration",
          "[person_follow][action][settle]")
{
    typedef struct {
        rehab_person_follow_action_t action;
        uint32_t planned_hold_ms;
        uint32_t expected_hold_ms;
        uint32_t expected_settle_ms;
    } settle_case_t;
    static const settle_case_t cases[] = {
        {REHAB_PERSON_FOLLOW_ACTION_FORWARD, 500U, 500U, 10U},
        {REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT, 250U, 250U, 40U},
        {REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT, 225U, 225U, 40U},
        {REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT, 250U, 250U, 40U},
        /* Turn actions retain the planned unified-ladder hold. */
        {REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT, 125U, 125U, 40U},
        {REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT, 100U, 100U, 40U},
        /* FORWARD after target turns still uses configured 500 ms. */
        {REHAB_PERSON_FOLLOW_ACTION_FORWARD, 200U, 200U, 10U},
    };
    rehab_person_follow_handle_t handle = action_handle(true);
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        uint32_t hold_ms = 0U;
        TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_execute_action(
            handle, 7U, cases[i].action, cases[i].planned_hold_ms,
            &hold_ms));
        TEST_ASSERT_EQUAL_UINT32(cases[i].expected_hold_ms, hold_ms);
        TEST_ASSERT_EQUAL_UINT32(
            cases[i].expected_settle_ms,
            rehab_person_follow_post_action_settle_ms(
                &handle->config, cases[i].action, ESP_OK, true));
    }
    delete_action_handle(handle);
}

TEST_CASE("none failure and stale action keep fallback settle",
          "[person_follow][action][settle][failure]")
{
    rehab_person_follow_handle_t handle = action_handle(true);
    TEST_ASSERT_EQUAL_UINT32(
        100U, rehab_person_follow_post_action_settle_ms(
                  &handle->config, REHAB_PERSON_FOLLOW_ACTION_NONE,
                  ESP_OK, true));
    TEST_ASSERT_EQUAL_UINT32(
        100U, rehab_person_follow_post_action_settle_ms(
                  &handle->config, REHAB_PERSON_FOLLOW_ACTION_FORWARD,
                  ESP_FAIL, true));
    TEST_ASSERT_EQUAL_UINT32(
        100U, rehab_person_follow_post_action_settle_ms(
                  &handle->config, REHAB_PERSON_FOLLOW_ACTION_FORWARD,
                  ESP_OK, false));
    delete_action_handle(handle);
}

TEST_CASE("action disabled never calls car", "[person_follow][action]")
{
    rehab_person_follow_handle_t handle = action_handle(false);
    uint32_t hold_ms = 0U;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE,
        rehab_person_follow_execute_action(
            handle, 7U, REHAB_PERSON_FOLLOW_ACTION_FORWARD, 500U,
            &hold_ms));
    TEST_ASSERT_EQUAL_UINT32(0U, s_move_calls);
    TEST_ASSERT_EQUAL_UINT32(0U, s_stop_calls);
    delete_action_handle(handle);
}

TEST_CASE("left target turn uses CCW speed 3 for 250ms",
          "[person_follow][action]")
{
    execute_and_expect(REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT,
                       REHAB_CAR_DIRECTION_ROTATE_CCW, 250U);
}

TEST_CASE("right target turn uses CW speed 3 for 250ms",
          "[person_follow][action]")
{
    execute_and_expect(REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT,
                       REHAB_CAR_DIRECTION_ROTATE_CW, 250U);
}

TEST_CASE("no target search uses CW speed 3 for 250ms",
          "[person_follow][action]")
{
    execute_and_expect(REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT,
                       REHAB_CAR_DIRECTION_ROTATE_CW, 250U);
}

TEST_CASE("final timed forward honors a continuous 1600 ms hold",
           "[person_follow][action]")
{
    execute_and_expect(REHAB_PERSON_FOLLOW_ACTION_FORWARD,
                       REHAB_CAR_DIRECTION_FORWARD, 1600U);
}

TEST_CASE("stale generation never calls car", "[person_follow][action]")
{
    rehab_person_follow_handle_t handle = action_handle(true);
    uint32_t hold_ms = 0U;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE,
        rehab_person_follow_execute_action(
            handle, 8U, REHAB_PERSON_FOLLOW_ACTION_FORWARD, 500U,
            &hold_ms));
    TEST_ASSERT_EQUAL_UINT32(0U, s_move_calls);
    TEST_ASSERT_EQUAL_UINT32(0U, s_stop_calls);
    delete_action_handle(handle);
}

TEST_CASE("cancelled generation never calls car", "[person_follow][action]")
{
    rehab_person_follow_handle_t handle = action_handle(true);
    handle->cancel_requested = true;
    uint32_t hold_ms = 0U;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE,
        rehab_person_follow_execute_action(
            handle, 7U, REHAB_PERSON_FOLLOW_ACTION_FORWARD, 500U,
            &hold_ms));
    TEST_ASSERT_EQUAL_UINT32(0U, s_move_calls);
    TEST_ASSERT_EQUAL_UINT32(0U, s_stop_calls);
    handle->cancel_requested = false;
    delete_action_handle(handle);
}

TEST_CASE("move failure immediately attempts controlled stop",
          "[person_follow][action][failure]")
{
    rehab_person_follow_handle_t handle = action_handle(true);
    s_move_result = ESP_FAIL;
    uint32_t hold_ms = 0U;
    TEST_ASSERT_EQUAL(ESP_FAIL, rehab_person_follow_execute_action(
        handle, 7U, REHAB_PERSON_FOLLOW_ACTION_FORWARD, 500U,
        &hold_ms));
    TEST_ASSERT_EQUAL_UINT32(1U, s_move_calls);
    TEST_ASSERT_EQUAL_UINT32(1U, s_stop_calls);
    delete_action_handle(handle);
}

TEST_CASE("rolling preemption orders STOP before turn or search writer",
          "[person_follow][action][rolling][single_writer]")
{
    const struct {
        rehab_person_follow_action_t action;
        const char *reason;
        const char *commands;
        uint32_t hold_ms;
    } cases[] = {
        { REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT, "target_left", "SL", 250U },
        { REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT, "target_right", "SR", 250U },
        { REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT, "no_target", "SR", 250U },
    };
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        rehab_person_follow_handle_t handle = action_handle(true);
        handle->running = true;
        handle->rolling_generation = 7U;
        handle->rolling_task_alive = true;
        handle->rolling_task = (TaskHandle_t)1;
        handle->forward_rolling = true;
        handle->forward_lease_deadline_us = INT64_MAX;
        TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_rolling_preempt(
            handle, 7U, cases[i].reason, false));
        uint32_t hold_ms = 0U;
        TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_execute_action(
            handle, 7U, cases[i].action, cases[i].hold_ms, &hold_ms));
        TEST_ASSERT_EQUAL_STRING(cases[i].commands, s_command_log);
        TEST_ASSERT_FALSE(handle->forward_rolling);
        handle->running = false;
        handle->rolling_task_alive = false;
        handle->rolling_task = NULL;
        delete_action_handle(handle);
    }
}

TEST_CASE("rotation uses planned adaptive hold while forward remains configured",
          "[person_follow][action][adaptive]")
{
    rehab_person_follow_handle_t handle = action_handle(true);
    uint32_t hold_ms = 0U;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_execute_action(
        handle, 7U, REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT, 225U,
        &hold_ms));
    TEST_ASSERT_EQUAL_UINT32(225U, s_hold_ms);
    TEST_ASSERT_EQUAL_UINT32(225U, hold_ms);

    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_execute_action(
        handle, 7U, REHAB_PERSON_FOLLOW_ACTION_FORWARD, 200U,
        &hold_ms));
    TEST_ASSERT_EQUAL_UINT32(200U, s_hold_ms);
    TEST_ASSERT_EQUAL_UINT32(200U, hold_ms);
    delete_action_handle(handle);
}
