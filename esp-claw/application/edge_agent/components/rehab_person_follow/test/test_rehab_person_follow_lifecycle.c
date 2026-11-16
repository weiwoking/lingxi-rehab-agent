// SPDX-License-Identifier: Apache-2.0
#include <string.h>

#include "unity.h"
#include "../src/rehab_person_follow_priv.h"

static bool s_fail_task_create;
static uint32_t s_rolling_task_create_count;

BaseType_t rehab_person_follow_task_create(
    TaskFunction_t task_function,
    const char *task_name,
    uint32_t stack_depth,
    void *task_argument,
    UBaseType_t priority,
    TaskHandle_t *out_task)
{
    if (s_fail_task_create) {
        return pdFAIL;
    }
    if (task_name != NULL && strcmp(task_name, "person_follow_roll") == 0) {
        ++s_rolling_task_create_count;
    }
    return xTaskCreate(task_function, task_name, stack_depth, task_argument,
                       priority, out_task);
}

static rehab_person_follow_config_t valid_config(void)
{
    rehab_person_follow_config_t config =
        REHAB_PERSON_FOLLOW_CONFIG_DEFAULT((rehab_car_handle_t)1);
    return config;
}

static rehab_car_handle_t create_loopback_car(void)
{
    const rehab_car_config_t config = {
        .target_ipv4 = "127.0.0.1",
        .target_port = REHAB_CAR_UDP_PORT,
    };
    rehab_car_handle_t car = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_create(&config, &car));
    return car;
}

static rehab_person_follow_handle_t create_with_car(
    rehab_car_handle_t car,
    rehab_person_follow_mock_mode_t mode,
    uint32_t interval_ms,
    uint32_t runtime_ms)
{
    rehab_person_follow_config_t config =
        REHAB_PERSON_FOLLOW_CONFIG_DEFAULT(car);
    config.mock_mode = mode;
    config.pc_endpoint = "http://127.0.0.1/api/v1/person-follow/detect";
    config.http_timeout_ms = 1000;
    config.camera_timeout_ms = 1000;
    config.loop_interval_ms = interval_ms;
    config.max_runtime_ms = runtime_ms;
    rehab_person_follow_handle_t handle = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_create(&config, &handle));
    return handle;
}

TEST_CASE("person follow create delete is local and idle",
          "[person_follow][lifecycle]")
{
    rehab_person_follow_config_t config = valid_config();
    rehab_person_follow_handle_t handle = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_create(&config, &handle));
    rehab_person_follow_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_get_status(handle, &status));
    TEST_ASSERT_TRUE(status.initialized);
    TEST_ASSERT_FALSE(status.running);
    TEST_ASSERT_TRUE(status.worker_done);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_STATE_IDLE, status.state);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_delete(handle));
}

TEST_CASE("person follow rejects invalid config and accepts action enable",
          "[person_follow][lifecycle]")
{
    rehab_person_follow_handle_t handle = NULL;
    rehab_person_follow_config_t config = valid_config();
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
                      rehab_person_follow_create(NULL, &handle));
    config.car = NULL;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
                      rehab_person_follow_create(&config, &handle));
    config = valid_config();
    config.action_enabled = true;
    TEST_ASSERT_EQUAL(ESP_OK,
                      rehab_person_follow_create(&config, &handle));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_delete(handle));
}

TEST_CASE("person follow status safety facts remain conservative",
          "[person_follow][lifecycle]")
{
    rehab_person_follow_config_t config = valid_config();
    rehab_person_follow_handle_t handle = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_create(&config, &handle));
    rehab_person_follow_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_get_status(handle, &status));
    TEST_ASSERT_FALSE(status.action_enabled);
    TEST_ASSERT_FALSE(status.forward_attempted);
    TEST_ASSERT_FALSE(status.motion_unconfirmed);
    TEST_ASSERT_FALSE(status.blocked_unknown);
    TEST_ASSERT_EQUAL_UINT32(0, status.post_action_wait_ms);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_APPROACH_NORMAL_FOLLOW,
                      status.approach_phase);
    TEST_ASSERT_FALSE(status.first_align_completed);
    TEST_ASSERT_EQUAL_UINT32(
        REHAB_PERSON_FOLLOW_ALIGN_STABLE_FRAMES_REQUIRED,
        status.align_stable_required);
    TEST_ASSERT_FALSE(status.arrived);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_delete(handle));
}

TEST_CASE("person follow owner is appended and named without renumbering",
          "[person_follow][owner]")
{
    TEST_ASSERT_EQUAL(0, REHAB_CAR_CONTROL_OWNER_NONE);
    TEST_ASSERT_EQUAL(1, REHAB_CAR_CONTROL_OWNER_DEMO);
    TEST_ASSERT_EQUAL(2, REHAB_CAR_CONTROL_OWNER_TARGET_FOLLOW);
    TEST_ASSERT_EQUAL(3, REHAB_CAR_CONTROL_OWNER_ENVIRONMENT_SCAN);
    TEST_ASSERT_EQUAL(4, REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW);
    TEST_ASSERT_EQUAL_STRING("person_follow", rehab_car_control_owner_name(
        REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW));
}

TEST_CASE("start double start stop double stop and delete while running",
          "[person_follow][lifecycle]")
{
    rehab_car_handle_t car = create_loopback_car();
    rehab_person_follow_handle_t handle = create_with_car(
        car, REHAB_PERSON_FOLLOW_MOCK_NO_TARGET, 1000, 5000);
    handle->decision_context.has_forwarded_once = true;
    handle->decision_context.fine_turn_mode = true;
    handle->decision_context.turn_hold_ms = 100U;
    handle->decision_context.turn_check_pending = true;
    handle->decision_context.last_successful_turn_direction =
        REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_start(handle));
    rehab_person_follow_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_get_status(handle, &status));
    TEST_ASSERT_TRUE(status.startup_observation_active);
    TEST_ASSERT_GREATER_THAN_UINT32(
        0U, status.startup_observation_remaining_ms);
    TEST_ASSERT_LESS_OR_EQUAL_UINT32(
        REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_GRACE_MS,
        status.startup_observation_remaining_ms);
    TEST_ASSERT_EQUAL(
        REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_NONE,
        status.startup_observation_exit_reason);
    TEST_ASSERT_TRUE(status.worker_alive);
    rehab_car_status_t started_car_status = {0};
    TEST_ASSERT_EQUAL(
        ESP_OK, rehab_car_get_status(car, &started_car_status));
    TEST_ASSERT_EQUAL_STRING("STOP", started_car_status.last_command);
    TEST_ASSERT_FALSE(status.has_forwarded_once);
    TEST_ASSERT_FALSE(status.fine_turn_mode);
    TEST_ASSERT_FALSE(status.pending_target_turn_check);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE,
                      status.last_target_turn_action);
    TEST_ASSERT_EQUAL_UINT32(250U, status.next_turn_hold_ms);
    TEST_ASSERT_EQUAL_UINT32(250U, status.turn_hold_ms);
    TEST_ASSERT_FALSE(status.turn_check_pending);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE,
                      rehab_person_follow_start(handle));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE,
                      rehab_person_follow_delete(handle));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_stop(handle));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_stop(handle));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_get_status(handle, &status));
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_STATE_IDLE, status.state);
    TEST_ASSERT_TRUE(status.worker_done);
    TEST_ASSERT_FALSE(status.owner_acquired);
    TEST_ASSERT_FALSE(status.startup_observation_active);
    TEST_ASSERT_EQUAL(
        REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_STOPPED,
        status.startup_observation_exit_reason);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_delete(handle));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("legacy and environment owners reject new worker creation",
          "[person_follow][owner]")
{
    const rehab_car_control_owner_t owners[] = {
        REHAB_CAR_CONTROL_OWNER_TARGET_FOLLOW,
        REHAB_CAR_CONTROL_OWNER_ENVIRONMENT_SCAN,
    };
    for (size_t i = 0; i < 2; ++i) {
        rehab_car_handle_t car = create_loopback_car();
        rehab_car_control_lease_t lease = {0};
        TEST_ASSERT_EQUAL(ESP_OK, rehab_car_control_acquire(
            car, owners[i], NULL, NULL, &lease));
        rehab_person_follow_handle_t handle = create_with_car(
            car, REHAB_PERSON_FOLLOW_MOCK_NO_TARGET, 10, 1000);
        TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE,
                          rehab_person_follow_start(handle));
        rehab_person_follow_status_t status = {0};
        TEST_ASSERT_EQUAL(ESP_OK,
                          rehab_person_follow_get_status(handle, &status));
        TEST_ASSERT_FALSE(status.worker_alive);
        TEST_ASSERT_EQUAL_UINT32(250U, status.next_turn_hold_ms);
        TEST_ASSERT_EQUAL(ESP_OK, rehab_car_control_release(car, &lease));
        TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_delete(handle));
        TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
    }
}

TEST_CASE("worker create failure stops releases owner and remains recoverable",
          "[person_follow][lifecycle]")
{
    rehab_car_handle_t car = create_loopback_car();
    rehab_person_follow_handle_t handle = create_with_car(
        car, REHAB_PERSON_FOLLOW_MOCK_NO_TARGET, 10, 1000);
    s_fail_task_create = true;
    TEST_ASSERT_EQUAL(ESP_ERR_NO_MEM, rehab_person_follow_start(handle));
    s_fail_task_create = false;
    rehab_person_follow_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_get_status(handle, &status));
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_STATE_ERROR, status.state);
    TEST_ASSERT_FALSE(status.worker_alive);
    TEST_ASSERT_FALSE(status.owner_acquired);
    rehab_car_status_t car_status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &car_status));
    TEST_ASSERT_EQUAL(REHAB_CAR_CONTROL_OWNER_NONE,
                      car_status.control_owner);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_stop(handle));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_delete(handle));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("stop wakes worker wait and leaves exact STOP as last command",
          "[person_follow][lifecycle][stop]")
{
    rehab_car_handle_t car = create_loopback_car();
    rehab_person_follow_handle_t handle = create_with_car(
        car, REHAB_PERSON_FOLLOW_MOCK_TARGET_CENTER, 60000, 5000);
    handle->config.action_enabled = true;
    uint32_t rolling_creates_before = s_rolling_task_create_count;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_start(handle));
    TEST_ASSERT_EQUAL_UINT32(rolling_creates_before + 1U,
                             s_rolling_task_create_count);
    bool motion_seen = false;
    for (uint32_t waited = 0; waited < 250; waited += 10) {
        rehab_car_status_t car_status = {0};
        TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &car_status));
        if (car_status.motion_active) {
            motion_seen = true;
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    TEST_ASSERT_TRUE(motion_seen);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_stop(handle));
    rehab_car_status_t car_status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &car_status));
    TEST_ASSERT_EQUAL_STRING("STOP", car_status.last_command);
    rehab_person_follow_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_get_status(handle, &status));
    TEST_ASSERT_TRUE(status.worker_done);
    TEST_ASSERT_TRUE(status.action_enabled);
    TEST_ASSERT_FALSE(status.forward_rolling);
    TEST_ASSERT_FALSE(status.rolling_task_alive);
    TEST_ASSERT_FALSE(status.rolling_task_busy);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_delete(handle));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("each start owns one fresh rolling task and generation",
          "[person_follow][lifecycle][rolling]")
{
    rehab_car_handle_t car = create_loopback_car();
    rehab_person_follow_handle_t handle = create_with_car(
        car, REHAB_PERSON_FOLLOW_MOCK_NO_TARGET, 1000, 5000);
    handle->config.action_enabled = true;
    uint32_t creates_before = s_rolling_task_create_count;

    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_start(handle));
    rehab_person_follow_status_t first = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_get_status(handle, &first));
    TEST_ASSERT_TRUE(first.rolling_task_alive);
    TEST_ASSERT_EQUAL_UINT32(creates_before + 1U,
                             s_rolling_task_create_count);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE,
                      rehab_person_follow_start(handle));
    TEST_ASSERT_EQUAL_UINT32(creates_before + 1U,
                             s_rolling_task_create_count);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_stop(handle));

    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_start(handle));
    rehab_person_follow_status_t second = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_get_status(handle, &second));
    TEST_ASSERT_NOT_EQUAL(first.generation, second.generation);
    TEST_ASSERT_TRUE(second.startup_observation_active);
    TEST_ASSERT_GREATER_THAN_UINT32(
        0U, second.startup_observation_remaining_ms);
    TEST_ASSERT_TRUE(second.rolling_task_alive);
    TEST_ASSERT_EQUAL_UINT32(creates_before + 2U,
                             s_rolling_task_create_count);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_stop(handle));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_delete(handle));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("global stop preempts owner and stale generation converges idle",
          "[person_follow][lifecycle][owner]")
{
    rehab_car_handle_t car = create_loopback_car();
    rehab_person_follow_handle_t handle = create_with_car(
        car, REHAB_PERSON_FOLLOW_MOCK_NO_TARGET, 1000, 5000);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_start(handle));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_stop(car));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_stop(handle));
    rehab_person_follow_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_get_status(handle, &status));
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_STATE_IDLE, status.state);
    TEST_ASSERT_TRUE(status.worker_done);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_delete(handle));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("runtime limit ends worker without issuing move",
          "[person_follow][lifecycle][runtime]")
{
    rehab_car_handle_t car = create_loopback_car();
    rehab_person_follow_handle_t handle = create_with_car(
        car, REHAB_PERSON_FOLLOW_MOCK_TARGET_CENTER, 10, 100);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_start(handle));
    rehab_person_follow_status_t status = {0};
    for (uint32_t waited = 0; waited < 500; waited += 10) {
        TEST_ASSERT_EQUAL(ESP_OK,
                          rehab_person_follow_get_status(handle, &status));
        if (status.worker_done) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    TEST_ASSERT_TRUE(status.worker_done);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_STATE_ERROR, status.state);
    TEST_ASSERT_EQUAL_STRING("runtime_limit", status.last_error);
    TEST_ASSERT_FALSE(status.action_enabled);
    TEST_ASSERT_TRUE(status.forward_attempted);
    TEST_ASSERT_TRUE(status.motion_unconfirmed);
    TEST_ASSERT_FALSE(status.blocked_unknown);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_stop(handle));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_delete(handle));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}
