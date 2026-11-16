// SPDX-License-Identifier: Apache-2.0
#include <string.h>

#include "unity.h"
#include "../src/rehab_person_follow_priv.h"

static rehab_person_follow_handle_t snapshot_handle(void)
{
    rehab_person_follow_config_t config =
        REHAB_PERSON_FOLLOW_CONFIG_DEFAULT((rehab_car_handle_t)1);
    rehab_person_follow_handle_t handle = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_create(&config, &handle));
    handle->running = true;
    handle->state = REHAB_PERSON_FOLLOW_STATE_SEARCH;
    handle->generation = 9U;
    return handle;
}

static void snapshot_handle_delete(rehab_person_follow_handle_t handle)
{
    handle->running = false;
    handle->state = REHAB_PERSON_FOLLOW_STATE_IDLE;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_delete(handle));
}

static rehab_person_follow_snapshot_info_t no_target_info(void)
{
    rehab_person_follow_snapshot_info_t info = {
        .target_found = false,
        .selected_valid = false,
        .zone = REHAB_PERSON_FOLLOW_TARGET_NO_TARGET,
        .planned_action = REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT,
        .actual_action = REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT,
        .speed = 3U,
        .hold_ms = 250U,
        .next_turn_hold_ms = 250U,
        .turn_hold_ms = 250U,
        .startup_observation_active = true,
        .startup_observation_remaining_ms = 800U,
        .startup_observation_exit_reason =
            REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_NONE,
        .post_action_wait_ms = 40U,
        .owner_acquired = true,
        .last_car_result = ESP_OK,
        .http_status = 200,
    };
    strlcpy(info.action_reason, "no_target_search_right",
            sizeof(info.action_reason));
    strlcpy(info.error_stage, "none", sizeof(info.error_stage));
    strlcpy(info.last_error, "none", sizeof(info.last_error));
    return info;
}

TEST_CASE("person follow snapshot publishes same-frame JPEG and no-target action",
          "[person_follow][snapshot][same_frame]")
{
    rehab_person_follow_handle_t handle = snapshot_handle();
    static const uint8_t jpeg[] = {0xff, 0xd8, 0x11, 0x22, 0xff, 0xd9};
    uint32_t token = 0U;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_snapshot_stage(
        handle, 9U, jpeg, sizeof(jpeg), 77U, 123456, &token));
    rehab_person_follow_snapshot_info_t info = no_target_info();
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_snapshot_publish(
        handle, 9U, token, &info));

    rehab_person_follow_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_get_status(handle, &status));
    TEST_ASSERT_EQUAL_UINT64(1U, status.snapshot.snapshot_seq);
    TEST_ASSERT_TRUE(status.snapshot.image_current);
    TEST_ASSERT_EQUAL_UINT64(status.snapshot.snapshot_seq,
                             status.snapshot.image_snapshot_seq);
    TEST_ASSERT_FALSE(status.snapshot.target_found);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_TARGET_NO_TARGET,
                      status.snapshot.zone);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT,
                      status.snapshot.planned_action);
    TEST_ASSERT_EQUAL_UINT32(250U, status.snapshot.hold_ms);
    TEST_ASSERT_EQUAL_UINT32(250U, status.snapshot.next_turn_hold_ms);
    TEST_ASSERT_EQUAL_UINT32(250U, status.snapshot.turn_hold_ms);
    TEST_ASSERT_TRUE(status.snapshot.startup_observation_active);
    TEST_ASSERT_EQUAL_UINT32(
        800U, status.snapshot.startup_observation_remaining_ms);
    TEST_ASSERT_EQUAL(
        REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_NONE,
        status.snapshot.startup_observation_exit_reason);

    rehab_person_follow_snapshot_frame_t frame = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_snapshot_acquire(
        handle, status.snapshot.snapshot_seq, &frame));
    TEST_ASSERT_EQUAL_UINT32(sizeof(jpeg), frame.info.image_length);
    TEST_ASSERT_EQUAL_UINT8_ARRAY(jpeg, frame.image_data, sizeof(jpeg));
    rehab_person_follow_snapshot_release(handle, &frame);
    snapshot_handle_delete(handle);
}

TEST_CASE("person follow final approach snapshot stays atomic",
          "[person_follow][snapshot][selected]")
{
    rehab_person_follow_handle_t handle = snapshot_handle();
    static const uint8_t jpeg[] = {0xff, 0xd8, 0x33, 0xff, 0xd9};
    uint32_t token = 0U;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_snapshot_stage(
        handle, 9U, jpeg, sizeof(jpeg), 88U, 200000, &token));
    rehab_person_follow_snapshot_info_t info = no_target_info();
    info.target_found = true;
    info.selected_valid = true;
    info.bbox_x1 = 100.0f;
    info.bbox_y1 = 0.0f;
    info.bbox_x2 = 500.0f;
    info.bbox_y2 = 359.0f;
    info.center_x = 0.46875f;
    info.center_y = 0.4986f;
    info.confidence = 0.91f;
    info.bbox_width_ratio = 0.625f;
    info.person_center_x_ratio = 0.46875f;
    info.left_background_ratio = 0.15625f;
    info.right_background_ratio = 0.21875f;
    info.zone = REHAB_PERSON_FOLLOW_TARGET_CENTER;
    info.approach_phase = REHAB_PERSON_FOLLOW_APPROACH_FINAL_FORWARD;
    info.align_turn_hold_ms = 250U;
    info.planned_action = REHAB_PERSON_FOLLOW_ACTION_FORWARD;
    info.actual_action = REHAB_PERSON_FOLLOW_ACTION_FORWARD;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_snapshot_publish(
        handle, 9U, token, &info));
    TEST_ASSERT_TRUE(handle->snapshot_info.selected_valid);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.625f,
                             handle->snapshot_info.bbox_width_ratio);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_APPROACH_FINAL_FORWARD,
                      handle->snapshot_info.approach_phase);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_FORWARD,
                      handle->snapshot_info.actual_action);
    snapshot_handle_delete(handle);
}

TEST_CASE("person follow HTTP failure retains old image without false bbox",
          "[person_follow][snapshot][http_error]")
{
    rehab_person_follow_handle_t handle = snapshot_handle();
    static const uint8_t jpeg[] = {0xff, 0xd8, 0x44, 0xff, 0xd9};
    uint32_t token = 0U;
    rehab_person_follow_snapshot_info_t info = no_target_info();
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_snapshot_stage(
        handle, 9U, jpeg, sizeof(jpeg), 99U, 300000, &token));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_snapshot_publish(
        handle, 9U, token, &info));
    uint64_t image_seq = handle->snapshot_info.image_snapshot_seq;

    memset(&info, 0, sizeof(info));
    info.zone = REHAB_PERSON_FOLLOW_TARGET_NO_TARGET;
    info.planned_action = REHAB_PERSON_FOLLOW_ACTION_NONE;
    info.actual_action = REHAB_PERSON_FOLLOW_ACTION_NONE;
    strlcpy(info.error_stage, "http", sizeof(info.error_stage));
    strlcpy(info.last_error, "http_timeout", sizeof(info.last_error));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_snapshot_publish(
        handle, 9U, 0U, &info));
    TEST_ASSERT_EQUAL_UINT64(2U, handle->snapshot_info.snapshot_seq);
    TEST_ASSERT_FALSE(handle->snapshot_info.image_current);
    TEST_ASSERT_EQUAL_UINT64(image_seq,
                             handle->snapshot_info.image_snapshot_seq);
    TEST_ASSERT_FALSE(handle->snapshot_info.selected_valid);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE,
                      handle->snapshot_info.actual_action);
    snapshot_handle_delete(handle);
}

TEST_CASE("person follow snapshot bounds generation and reader lease are safe",
          "[person_follow][snapshot][bounds]")
{
    rehab_person_follow_handle_t handle = snapshot_handle();
    static const uint8_t jpeg[] = {0xff, 0xd8, 0x55, 0xff, 0xd9};
    uint32_t token = 0U;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_SIZE,
        rehab_person_follow_snapshot_stage(
            handle, 9U, jpeg,
            REHAB_PERSON_FOLLOW_SNAPSHOT_IMAGE_MAX + 1U,
            1U, 1, &token));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_snapshot_stage(
        handle, 9U, jpeg, sizeof(jpeg), 1U, 1, &token));
    rehab_person_follow_snapshot_info_t info = no_target_info();
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_snapshot_publish(
        handle, 9U, token, &info));

    rehab_person_follow_snapshot_frame_t frame = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_snapshot_acquire(
        handle, 1U, &frame));
    TEST_ASSERT_EQUAL(ESP_OK,
        rehab_person_follow_snapshot_stage(
            handle, 9U, jpeg, sizeof(jpeg), 2U, 2, &token));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_snapshot_publish(
        handle, 9U, token, &info));
    rehab_person_follow_snapshot_frame_t second_frame = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_snapshot_acquire(
        handle, 2U, &second_frame));
    TEST_ASSERT_EQUAL(ESP_ERR_NOT_FINISHED,
        rehab_person_follow_snapshot_stage(
            handle, 9U, jpeg, sizeof(jpeg), 3U, 3, &token));
    rehab_person_follow_snapshot_release(handle, &second_frame);
    rehab_person_follow_snapshot_release(handle, &frame);

    handle->cancel_requested = true;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE,
        rehab_person_follow_snapshot_publish(handle, 9U, 0U, &info));
    TEST_ASSERT_EQUAL_UINT64(2U, handle->snapshot_sequence);
    handle->cancel_requested = false;
    snapshot_handle_delete(handle);
}
