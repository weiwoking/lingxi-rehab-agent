// SPDX-License-Identifier: Apache-2.0
#include <math.h>
#include <string.h>

#include "unity.h"
#include "../src/rehab_target_follow_priv.h"

static rehab_target_follow_observation_t observation(
    bool found, rehab_target_zone_t zone, bool distance_valid, float distance_cm)
{
    rehab_target_follow_observation_t value = {
        .found = found,
        .zone = zone,
        .center_valid = found,
        .center_x = zone == REHAB_TARGET_ZONE_LEFT ? 0.1f
            : (zone == REHAB_TARGET_ZONE_RIGHT ? 0.9f : 0.5f),
        .distance_valid = distance_valid,
        .has_distance_cm = distance_valid,
        .distance_cm = distance_cm,
        .object_id = 1,
        .confidence = 0.96f,
    };
    return value;
}

static rehab_cloud_fusion_result_t valid_result(void)
{
    rehab_cloud_fusion_result_t result = {
        .ok = true,
        .response_json_valid = true,
        .capture_id_match = true,
        .cloud_valid = true,
        .has_pairing = true,
    };
    strlcpy(result.schema_version, "vision_fusion.v1",
            sizeof(result.schema_version));
    strlcpy(result.cloud_status, "ok", sizeof(result.cloud_status));
    return result;
}

static void add_detection(rehab_cloud_fusion_result_t *result,
                          const char *class_name,
                          int object_id,
                          float confidence,
                          float center_x,
                          const char *zone,
                          bool distance_valid,
                          bool has_distance,
                          float distance_cm)
{
    rehab_cloud_fusion_detection_t *item =
        &result->detections[result->detection_count++];
    item->present = true;
    item->has_object_id = true;
    item->object_id = object_id;
    strlcpy(item->class_name, class_name, sizeof(item->class_name));
    item->has_confidence = true;
    item->confidence = confidence;
    item->has_center_normalized = true;
    item->center_normalized.x = center_x;
    item->center_normalized.y = 0.5f;
    strlcpy(item->horizontal_zone, zone, sizeof(item->horizontal_zone));
    item->distance_valid = distance_valid;
    item->has_distance_cm = has_distance;
    item->distance_cm = distance_cm;
}

static esp_err_t decide(const rehab_target_follow_observation_t *value,
                        uint32_t *confirm_streak,
                        bool *hold_latched,
                        rehab_target_follow_damping_t *damping,
                        rehab_target_follow_decision_t *decision)
{
    return rehab_target_follow_decide_observation(
        value, confirm_streak, hold_latched, damping, decision);
}

static esp_err_t decide_confirmed(float center_x,
                                  bool distance_valid,
                                  bool has_distance_cm,
                                  float distance_cm,
                                  rehab_target_follow_damping_t *damping,
                                  rehab_target_follow_decision_t *decision)
{
    uint32_t streak = 2;
    bool hold = false;
    rehab_target_follow_observation_t value =
        observation(true, REHAB_TARGET_ZONE_CENTER, distance_valid, distance_cm);
    value.center_x = center_x;
    value.center_valid = true;
    value.has_distance_cm = has_distance_cm;
    return decide(&value, &streak, &hold, damping, decision);
}

TEST_CASE("target follow constants remain frozen", "[target_follow]")
{
    TEST_ASSERT_EQUAL_STRING("person", FOLLOW_TARGET_CLASS_NAME);
    TEST_ASSERT_EQUAL_UINT32(1, FOLLOW_SPEED_LEVEL);
    TEST_ASSERT_EQUAL_UINT32(250, FOLLOW_SEARCH_ROTATE_MS);
    TEST_ASSERT_EQUAL_FLOAT(0.08f, FOLLOW_CENTER_DEADBAND);
    TEST_ASSERT_EQUAL_FLOAT(0.18f, FOLLOW_ALIGN_NEAR_ERROR_MAX);
    TEST_ASSERT_EQUAL_FLOAT(0.30f, FOLLOW_ALIGN_MEDIUM_ERROR_MAX);
    TEST_ASSERT_EQUAL_UINT32(100, FOLLOW_ALIGN_NEAR_MS);
    TEST_ASSERT_EQUAL_UINT32(140, FOLLOW_ALIGN_MEDIUM_MS);
    TEST_ASSERT_EQUAL_UINT32(200, FOLLOW_ALIGN_FAR_MS);
    TEST_ASSERT_EQUAL_UINT32(200, FOLLOW_ALIGN_BASE_MS);
    TEST_ASSERT_EQUAL_UINT32(140, FOLLOW_ALIGN_DAMPED_MS);
    TEST_ASSERT_EQUAL_UINT32(100, FOLLOW_ALIGN_MIN_MS);
    TEST_ASSERT_EQUAL_UINT32(300, FOLLOW_APPROACH_FORWARD_MS);
    TEST_ASSERT_EQUAL_FLOAT(80.0f, FOLLOW_APPROACH_NEAR_MAX_CM);
    TEST_ASSERT_EQUAL_FLOAT(120.0f, FOLLOW_APPROACH_MEDIUM_MAX_CM);
    TEST_ASSERT_EQUAL_UINT32(120, FOLLOW_APPROACH_NEAR_MS);
    TEST_ASSERT_EQUAL_UINT32(200, FOLLOW_APPROACH_MEDIUM_MS);
    TEST_ASSERT_EQUAL_UINT32(300, FOLLOW_APPROACH_FAR_MS);
    TEST_ASSERT_EQUAL_UINT32(200, FOLLOW_MOVEMENT_SETTLE_MS);
    TEST_ASSERT_EQUAL_UINT32(500, FOLLOW_PERCEPTION_RETRY_MS);
    TEST_ASSERT_EQUAL_UINT32(50, FOLLOW_CAPTURE_RECOVERY_POLL_MS);
    TEST_ASSERT_EQUAL_UINT32(3000, FOLLOW_CAPTURE_RECOVERY_MAX_MS);
    TEST_ASSERT_EQUAL_UINT32(100,
                             FOLLOW_CAPTURE_POST_QUIESCENT_SETTLE_MS);
    TEST_ASSERT_EQUAL_UINT32(500, FOLLOW_HOLD_RECHECK_MS);
    TEST_ASSERT_EQUAL_FLOAT(50.0f, FOLLOW_STOP_DISTANCE_CM);
    TEST_ASSERT_EQUAL_FLOAT(60.0f, FOLLOW_RESUME_DISTANCE_CM);
    TEST_ASSERT_EQUAL_UINT32(3, FOLLOW_MAX_CONSECUTIVE_PERCEPTION_ERRORS);
}

TEST_CASE("caller timeout is not quiescent while coordinator remains active",
          "[target_follow]")
{
    const rehab_capture_status_t status = {
        .busy = false,
        .packet_lease = false,
        .request_pending = false,
        .coordinator_active = true,
        .tof_quiescent = true,
        .camera_quiescent = true,
    };
    TEST_ASSERT_FALSE(rehab_capture_status_is_quiescent(&status));
    TEST_ASSERT_EQUAL(REHAB_TARGET_CAPTURE_RECOVERY_WAIT,
        rehab_target_follow_evaluate_capture_recovery(
            ESP_OK, &status, 1700, FOLLOW_CAPTURE_RECOVERY_MAX_MS));
}

TEST_CASE("coordinator cleanup makes complete capture pipeline quiescent",
          "[target_follow]")
{
    const rehab_capture_status_t status = {
        .busy = false,
        .packet_lease = false,
        .request_pending = false,
        .coordinator_active = false,
        .capture_quiescent = true,
        .tof_quiescent = true,
        .camera_quiescent = true,
    };
    TEST_ASSERT_TRUE(rehab_capture_status_is_quiescent(&status));
    TEST_ASSERT_EQUAL(REHAB_TARGET_CAPTURE_RECOVERY_COMPLETE,
        rehab_target_follow_evaluate_capture_recovery(
            ESP_OK, &status, 1750, FOLLOW_CAPTURE_RECOVERY_MAX_MS));
}

TEST_CASE("pending capture request prevents false quiescence",
          "[target_follow]")
{
    const rehab_capture_status_t status = {
        .request_pending = true,
    };
    TEST_ASSERT_FALSE(rehab_capture_status_is_quiescent(&status));
}

TEST_CASE("capture recovery times out without another perception error",
          "[target_follow]")
{
    const rehab_capture_status_t status = {
        .coordinator_active = true,
        .tof_quiescent = true,
        .camera_quiescent = true,
    };
    uint32_t errors = 1;
    TEST_ASSERT_EQUAL(REHAB_TARGET_CAPTURE_RECOVERY_TIMEOUT,
        rehab_target_follow_evaluate_capture_recovery(
            ESP_OK, &status, FOLLOW_CAPTURE_RECOVERY_MAX_MS,
            FOLLOW_CAPTURE_RECOVERY_MAX_MS));
    TEST_ASSERT_EQUAL_UINT32(1, errors);
}

TEST_CASE("active ToF window blocks end to end capture quiescence",
          "[target_follow]")
{
    const rehab_tof_status_t tof_status = {
        .window_active = true,
    };
    const rehab_capture_status_t status = {
        .capture_quiescent = true,
        .tof_window_active = true,
        .tof_quiescent = false,
        .camera_quiescent = true,
    };
    TEST_ASSERT_FALSE(rehab_tof_status_is_quiescent(&tof_status));
    TEST_ASSERT_FALSE(rehab_capture_status_is_quiescent(&status));
    TEST_ASSERT_EQUAL(REHAB_TARGET_CAPTURE_RECOVERY_WAIT,
        rehab_target_follow_evaluate_capture_recovery(
            ESP_OK, &status, 1650, FOLLOW_CAPTURE_RECOVERY_MAX_MS));
}

TEST_CASE("ToF provider is quiescent only after pending and active clear",
          "[target_follow]")
{
    rehab_tof_status_t tof_status = {
        .request_pending = true,
    };
    TEST_ASSERT_FALSE(rehab_tof_status_is_quiescent(&tof_status));
    tof_status.request_pending = false;
    tof_status.window_active = true;
    TEST_ASSERT_FALSE(rehab_tof_status_is_quiescent(&tof_status));
    tof_status.window_active = false;
    TEST_ASSERT_TRUE(rehab_tof_status_is_quiescent(&tof_status));
}

TEST_CASE("ToF cleanup plus post settle gates the next fusion cycle",
          "[target_follow]")
{
    const rehab_capture_status_t active = {
        .capture_quiescent = true,
        .tof_window_active = true,
        .camera_quiescent = true,
    };
    const rehab_capture_status_t quiescent = {
        .capture_quiescent = true,
        .tof_quiescent = true,
        .camera_quiescent = true,
        .pipeline_quiescent = true,
    };
    TEST_ASSERT_EQUAL(REHAB_TARGET_CAPTURE_RECOVERY_WAIT,
        rehab_target_follow_evaluate_capture_recovery(
            ESP_OK, &active, 50, FOLLOW_CAPTURE_RECOVERY_MAX_MS));
    TEST_ASSERT_EQUAL(REHAB_TARGET_CAPTURE_RECOVERY_WAIT,
        rehab_target_follow_evaluate_capture_recovery(
            ESP_OK, &active, 1650, FOLLOW_CAPTURE_RECOVERY_MAX_MS));
    TEST_ASSERT_EQUAL(REHAB_TARGET_CAPTURE_RECOVERY_COMPLETE,
        rehab_target_follow_evaluate_capture_recovery(
            ESP_OK, &quiescent, 1700, FOLLOW_CAPTURE_RECOVERY_MAX_MS));
    TEST_ASSERT_EQUAL_UINT32(1800,
        1700 + FOLLOW_CAPTURE_POST_QUIESCENT_SETTLE_MS);
}

TEST_CASE("camera provider activity also blocks end to end quiescence",
          "[target_follow]")
{
    const rehab_capture_status_t status = {
        .capture_quiescent = true,
        .tof_quiescent = true,
        .camera_snapshot_active = true,
        .camera_quiescent = false,
    };
    TEST_ASSERT_FALSE(rehab_capture_status_is_quiescent(&status));
}

TEST_CASE("active ToF at recovery deadline times out one error cycle",
          "[target_follow]")
{
    const rehab_capture_status_t status = {
        .capture_quiescent = true,
        .tof_window_active = true,
        .camera_quiescent = true,
    };
    uint32_t errors = 1;
    TEST_ASSERT_EQUAL(REHAB_TARGET_CAPTURE_RECOVERY_TIMEOUT,
        rehab_target_follow_evaluate_capture_recovery(
            ESP_OK, &status, FOLLOW_CAPTURE_RECOVERY_MAX_MS,
            FOLLOW_CAPTURE_RECOVERY_MAX_MS));
    TEST_ASSERT_EQUAL_UINT32(1, errors);
}

TEST_CASE("first person confirms while stopped", "[target_follow]")
{
    uint32_t streak = 0;
    bool hold = false;
    rehab_target_follow_damping_t damping;
    rehab_target_follow_damping_reset(&damping);
    rehab_target_follow_decision_t decision;
    rehab_target_follow_observation_t value =
        observation(true, REHAB_TARGET_ZONE_LEFT, true, 100.0f);

    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT32(1, streak);
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_CONFIRM, decision.next_state);
    TEST_ASSERT_TRUE(decision.stop_required);
    TEST_ASSERT_EQUAL_UINT32(0, decision.movement_duration_ms);
}

TEST_CASE("second consecutive person permits first left align", "[target_follow]")
{
    uint32_t streak = 1;
    bool hold = false;
    rehab_target_follow_damping_t damping;
    rehab_target_follow_damping_reset(&damping);
    rehab_target_follow_decision_t decision;
    rehab_target_follow_observation_t value =
        observation(true, REHAB_TARGET_ZONE_LEFT, true, 100.0f);

    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT32(2, streak);
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_ALIGN, decision.next_state);
    TEST_ASSERT_EQUAL(REHAB_CAR_DIRECTION_ROTATE_CCW, decision.direction);
    TEST_ASSERT_EQUAL_UINT32(200, decision.movement_duration_ms);
}

TEST_CASE("no person resets confirmation and enters bounded search", "[target_follow]")
{
    uint32_t streak = 2;
    bool hold = true;
    rehab_target_follow_damping_t damping;
    rehab_target_follow_damping_reset(&damping);
    (void)rehab_target_follow_damping_prepare_align(
        &damping, REHAB_TARGET_ZONE_LEFT, FOLLOW_ALIGN_FAR_MS);
    rehab_target_follow_decision_t decision;
    rehab_target_follow_observation_t value =
        observation(false, REHAB_TARGET_ZONE_NONE, false, 0.0f);

    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT32(0, streak);
    TEST_ASSERT_FALSE(hold);
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_SEARCH, decision.next_state);
    TEST_ASSERT_EQUAL(REHAB_CAR_DIRECTION_ROTATE_CCW, decision.direction);
    TEST_ASSERT_EQUAL_UINT32(250, decision.movement_duration_ms);
    TEST_ASSERT_EQUAL(REHAB_TARGET_ZONE_NONE, damping.last_non_center_zone);
}

TEST_CASE("right target aligns clockwise", "[target_follow]")
{
    uint32_t streak = 2;
    bool hold = false;
    rehab_target_follow_damping_t damping;
    rehab_target_follow_damping_reset(&damping);
    rehab_target_follow_decision_t decision;
    rehab_target_follow_observation_t value =
        observation(true, REHAB_TARGET_ZONE_RIGHT, true, 80.0f);

    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_EQUAL(REHAB_CAR_DIRECTION_ROTATE_CW, decision.direction);
}

TEST_CASE("center beyond resume distance approaches forward", "[target_follow]")
{
    uint32_t streak = 2;
    bool hold = true;
    rehab_target_follow_damping_t damping;
    rehab_target_follow_damping_reset(&damping);
    rehab_target_follow_decision_t decision;
    rehab_target_follow_observation_t value =
        observation(true, REHAB_TARGET_ZONE_CENTER, true, 60.1f);

    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_FALSE(hold);
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_APPROACH, decision.next_state);
    TEST_ASSERT_EQUAL(REHAB_CAR_DIRECTION_FORWARD, decision.direction);
    TEST_ASSERT_EQUAL_UINT32(120, decision.movement_duration_ms);
}

TEST_CASE("center at stop distance enters hold", "[target_follow]")
{
    uint32_t streak = 2;
    bool hold = false;
    rehab_target_follow_damping_t damping;
    rehab_target_follow_damping_reset(&damping);
    rehab_target_follow_decision_t decision;
    rehab_target_follow_observation_t value =
        observation(true, REHAB_TARGET_ZONE_CENTER, true, 50.0f);

    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_TRUE(hold);
    TEST_ASSERT_TRUE(decision.stop_required);
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_HOLD, decision.next_state);
}

TEST_CASE("distance deadband stays stopped", "[target_follow]")
{
    uint32_t streak = 2;
    bool hold = false;
    rehab_target_follow_damping_t damping;
    rehab_target_follow_damping_reset(&damping);
    rehab_target_follow_decision_t decision;
    rehab_target_follow_observation_t value =
        observation(true, REHAB_TARGET_ZONE_CENTER, true, 55.0f);

    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_FALSE(hold);
    TEST_ASSERT_TRUE(decision.stop_required);
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_SENSE, decision.next_state);
    TEST_ASSERT_EQUAL_UINT32(500, decision.next_sense_delay_ms);
}

TEST_CASE("hold latch stays stopped through deadband", "[target_follow]")
{
    uint32_t streak = 2;
    bool hold = true;
    rehab_target_follow_damping_t damping;
    rehab_target_follow_damping_reset(&damping);
    rehab_target_follow_decision_t decision;
    rehab_target_follow_observation_t value =
        observation(true, REHAB_TARGET_ZONE_CENTER, true, 55.0f);

    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_TRUE(hold);
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_HOLD, decision.next_state);
}

TEST_CASE("invalid center distance fails closed", "[target_follow]")
{
    uint32_t streak = 2;
    bool hold = false;
    rehab_target_follow_damping_t damping;
    rehab_target_follow_damping_reset(&damping);
    rehab_target_follow_decision_t decision;
    rehab_target_follow_observation_t value =
        observation(true, REHAB_TARGET_ZONE_CENTER, false, 0.0f);

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
                      decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_TRUE(decision.stop_required);
    TEST_ASSERT_EQUAL_UINT32(0, decision.movement_duration_ms);
}

TEST_CASE("third consecutive retryable perception error enters error",
          "[target_follow]")
{
    uint32_t errors = 0;
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_SENSE,
                      rehab_target_follow_record_perception_error(true, &errors));
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_SENSE,
                      rehab_target_follow_record_perception_error(true, &errors));
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_ERROR,
                      rehab_target_follow_record_perception_error(true, &errors));
    TEST_ASSERT_EQUAL_UINT32(3, errors);
}

TEST_CASE("nonretryable perception error enters error immediately",
          "[target_follow]")
{
    uint32_t errors = 0;
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_ERROR,
                      rehab_target_follow_record_perception_error(false, &errors));
    TEST_ASSERT_EQUAL_UINT32(1, errors);
}

TEST_CASE("capture cleanup busy does not multiply one real failure",
          "[target_follow]")
{
    uint32_t errors = 0;
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_SENSE,
        rehab_target_follow_apply_perception_result(
            REHAB_TARGET_PERCEPTION_RESULT_REAL_FAILURE, true, &errors));
    TEST_ASSERT_EQUAL_UINT32(1, errors);
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_SENSE,
        rehab_target_follow_apply_perception_result(
            REHAB_TARGET_PERCEPTION_RESULT_BUSY_CLEANUP, true, &errors));
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_SENSE,
        rehab_target_follow_apply_perception_result(
            REHAB_TARGET_PERCEPTION_RESULT_BUSY_CLEANUP, true, &errors));
    TEST_ASSERT_EQUAL_UINT32(1, errors);
}

TEST_CASE("three independent real failures enter error", "[target_follow]")
{
    uint32_t errors = 0;
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_SENSE,
        rehab_target_follow_apply_perception_result(
            REHAB_TARGET_PERCEPTION_RESULT_REAL_FAILURE, true, &errors));
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_SENSE,
        rehab_target_follow_apply_perception_result(
            REHAB_TARGET_PERCEPTION_RESULT_REAL_FAILURE, true, &errors));
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_ERROR,
        rehab_target_follow_apply_perception_result(
            REHAB_TARGET_PERCEPTION_RESULT_REAL_FAILURE, true, &errors));
    TEST_ASSERT_EQUAL_UINT32(3, errors);
}

TEST_CASE("complete perception success clears real error count",
          "[target_follow]")
{
    uint32_t errors = 2;
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_SENSE,
        rehab_target_follow_apply_perception_result(
            REHAB_TARGET_PERCEPTION_RESULT_SUCCESS, true, &errors));
    TEST_ASSERT_EQUAL_UINT32(0, errors);
}

TEST_CASE("not finished and named busy classify as cleanup",
          "[target_follow]")
{
    TEST_ASSERT_EQUAL(REHAB_TARGET_PERCEPTION_RESULT_BUSY_CLEANUP,
        rehab_target_follow_classify_perception_result(
            ESP_ERR_NOT_FINISHED, false, "capture_busy"));
    TEST_ASSERT_EQUAL(REHAB_TARGET_PERCEPTION_RESULT_BUSY_CLEANUP,
        rehab_target_follow_classify_perception_result(
            ESP_FAIL, false, "packet_busy"));
    TEST_ASSERT_EQUAL(REHAB_TARGET_PERCEPTION_RESULT_BUSY_CLEANUP,
        rehab_target_follow_classify_perception_result(
            ESP_FAIL, false, "upload_busy"));
    TEST_ASSERT_EQUAL(REHAB_TARGET_PERCEPTION_RESULT_REAL_FAILURE,
        rehab_target_follow_classify_perception_result(
            ESP_ERR_TIMEOUT, false, "capture_failed"));
    TEST_ASSERT_EQUAL(REHAB_TARGET_PERCEPTION_RESULT_SUCCESS,
        rehab_target_follow_classify_perception_result(
            ESP_OK, true, "none"));
}

TEST_CASE("hold target off center permits alignment not approach",
          "[target_follow]")
{
    uint32_t streak = 2;
    bool hold = true;
    rehab_target_follow_damping_t damping;
    rehab_target_follow_damping_reset(&damping);
    rehab_target_follow_decision_t decision;
    rehab_target_follow_observation_t value =
        observation(true, REHAB_TARGET_ZONE_RIGHT, true, 45.0f);

    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_FALSE(hold);
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_ALIGN, decision.next_state);
    TEST_ASSERT_EQUAL(REHAB_CAR_DIRECTION_ROTATE_CW, decision.direction);
}

TEST_CASE("alignment crossing damps 200 140 100 and stays 100", "[target_follow]")
{
    rehab_target_follow_damping_t damping;
    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL_UINT32(200, rehab_target_follow_damping_prepare_align(
                                      &damping, REHAB_TARGET_ZONE_LEFT, 200));
    TEST_ASSERT_EQUAL_UINT32(140, rehab_target_follow_damping_prepare_align(
                                      &damping, REHAB_TARGET_ZONE_RIGHT, 200));
    TEST_ASSERT_EQUAL_UINT32(100, rehab_target_follow_damping_prepare_align(
                                      &damping, REHAB_TARGET_ZONE_LEFT, 200));
    TEST_ASSERT_EQUAL_UINT32(100, rehab_target_follow_damping_prepare_align(
                                      &damping, REHAB_TARGET_ZONE_RIGHT, 200));
    TEST_ASSERT_EQUAL_UINT32(100, rehab_target_follow_damping_prepare_align(
                                      &damping, REHAB_TARGET_ZONE_RIGHT, 200));
    TEST_ASSERT_EQUAL_UINT8(2, damping.align_overshoot_level);
}

TEST_CASE("two center observations reset alignment damping", "[target_follow]")
{
    rehab_target_follow_damping_t damping;
    rehab_target_follow_damping_reset(&damping);
    (void)rehab_target_follow_damping_prepare_align(
        &damping, REHAB_TARGET_ZONE_LEFT, FOLLOW_ALIGN_FAR_MS);
    (void)rehab_target_follow_damping_prepare_align(
        &damping, REHAB_TARGET_ZONE_RIGHT, FOLLOW_ALIGN_FAR_MS);
    rehab_target_follow_damping_observe_center(&damping);
    TEST_ASSERT_EQUAL_UINT32(140, damping.last_align_duration_ms);
    rehab_target_follow_damping_observe_center(&damping);
    TEST_ASSERT_EQUAL_UINT8(0, damping.align_overshoot_level);
    TEST_ASSERT_EQUAL_UINT32(200, damping.last_align_duration_ms);
    TEST_ASSERT_EQUAL_UINT32(200, rehab_target_follow_damping_prepare_align(
                                      &damping, REHAB_TARGET_ZONE_LEFT, 200));
}

TEST_CASE("other class never selects as temporary target", "[target_follow]")
{
    rehab_cloud_fusion_result_t result = valid_result();
    add_detection(&result, "cat", 1, 0.99f, 0.5f, "center",
                  true, true, 100.0f);
    rehab_target_follow_observation_t selected;
    char error[64];
    TEST_ASSERT_EQUAL(ESP_OK, rehab_target_follow_select_observation(
                                  &result, &selected, error, sizeof(error)));
    TEST_ASSERT_FALSE(selected.found);
}

TEST_CASE("zxl is not selected in temporary person mode", "[target_follow]")
{
    rehab_cloud_fusion_result_t result = valid_result();
    add_detection(&result, "zxl", 1, 0.99f, 0.5f, "center",
                  true, true, 100.0f);
    rehab_target_follow_observation_t selected;
    char error[64];
    TEST_ASSERT_EQUAL(ESP_OK, rehab_target_follow_select_observation(
                                  &result, &selected, error, sizeof(error)));
    TEST_ASSERT_FALSE(selected.found);
}

TEST_CASE("multiple person selection is deterministic", "[target_follow]")
{
    rehab_cloud_fusion_result_t result = valid_result();
    add_detection(&result, FOLLOW_TARGET_CLASS_NAME, 9, 0.96f, 0.2f, "left",
                  true, true, 90.0f);
    add_detection(&result, FOLLOW_TARGET_CLASS_NAME, 8, 0.97f, 0.1f, "left",
                  true, true, 80.0f);
    add_detection(&result, FOLLOW_TARGET_CLASS_NAME, 3, 0.97f, 0.6f, "center",
                  true, true, 70.0f);
    add_detection(&result, FOLLOW_TARGET_CLASS_NAME, 2, 0.97f, 0.6f, "center",
                  true, true, 60.0f);
    rehab_target_follow_observation_t selected;
    char error[64];
    TEST_ASSERT_EQUAL(ESP_OK, rehab_target_follow_select_observation(
                                  &result, &selected, error, sizeof(error)));
    TEST_ASSERT_TRUE(selected.found);
    TEST_ASSERT_TRUE(selected.multiple_zxl);
    TEST_ASSERT_EQUAL_INT(2, selected.object_id);
    TEST_ASSERT_EQUAL(REHAB_TARGET_ZONE_RIGHT, selected.zone);
}

TEST_CASE("truncated detections fail closed", "[target_follow]")
{
    rehab_cloud_fusion_result_t result = valid_result();
    result.detections_truncated = true;
    rehab_target_follow_observation_t selected;
    char error[64];
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_SIZE,
                      rehab_target_follow_select_observation(
                          &result, &selected, error, sizeof(error)));
    TEST_ASSERT_EQUAL_STRING("detections_truncated", error);
}

TEST_CASE("invalid zone and distance fail closed", "[target_follow]")
{
    rehab_cloud_fusion_result_t result = valid_result();
    add_detection(&result, FOLLOW_TARGET_CLASS_NAME, 1, 0.96f, 0.5f, "CENTER",
                  true, true, -1.0f);
    rehab_target_follow_observation_t selected;
    char error[64];
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
                      rehab_target_follow_select_observation(
                          &result, &selected, error, sizeof(error)));
    TEST_ASSERT_EQUAL_STRING("target_zone_invalid", error);
}

TEST_CASE("center error boundaries select exact alignment tiers",
          "[target_follow]")
{
    rehab_target_follow_damping_t damping;
    rehab_target_follow_decision_t decision;

    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.50f, true, true, 70.0f, &damping, &decision));
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_APPROACH, decision.next_state);
    TEST_ASSERT_EQUAL(REHAB_CAR_DIRECTION_FORWARD, decision.direction);

    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.58f, true, true, 200.0f, &damping, &decision));
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_APPROACH, decision.next_state);

    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.5801f, true, true, 200.0f, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT32(100, decision.movement_duration_ms);
    TEST_ASSERT_EQUAL_UINT32(100, decision.base_align_ms);

    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.68f, true, true, 200.0f, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT32(100, decision.movement_duration_ms);

    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.6801f, true, true, 200.0f, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT32(140, decision.movement_duration_ms);

    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.80f, true, true, 200.0f, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT32(140, decision.movement_duration_ms);

    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.8001f, true, true, 200.0f, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT32(200, decision.movement_duration_ms);
}

TEST_CASE("center error sign selects CCW left and CW right",
          "[target_follow]")
{
    rehab_target_follow_damping_t damping;
    rehab_target_follow_decision_t decision;
    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.30f, true, true, 200.0f, &damping, &decision));
    TEST_ASSERT_EQUAL(REHAB_CAR_DIRECTION_ROTATE_CCW, decision.direction);

    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.70f, true, true, 200.0f, &damping, &decision));
    TEST_ASSERT_EQUAL(REHAB_CAR_DIRECTION_ROTATE_CW, decision.direction);
}

TEST_CASE("crossing caps current error tier at 140 then 100",
          "[target_follow]")
{
    uint32_t streak = 2;
    bool hold = false;
    rehab_target_follow_damping_t damping;
    rehab_target_follow_damping_reset(&damping);
    rehab_target_follow_decision_t decision;
    rehab_target_follow_observation_t value =
        observation(true, REHAB_TARGET_ZONE_LEFT, true, 200.0f);

    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT32(200, decision.base_align_ms);
    TEST_ASSERT_EQUAL_UINT32(200, decision.final_align_ms);
    value.center_x = 0.90f;
    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT8(1, damping.align_overshoot_level);
    TEST_ASSERT_EQUAL_UINT32(140, decision.final_align_ms);
    value.center_x = 0.10f;
    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT8(2, damping.align_overshoot_level);
    TEST_ASSERT_EQUAL_UINT32(100, decision.final_align_ms);
}

TEST_CASE("same side recomputes error tier while retaining overshoot cap",
          "[target_follow]")
{
    rehab_target_follow_damping_t damping;
    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL_UINT32(200, rehab_target_follow_damping_prepare_align(
        &damping, REHAB_TARGET_ZONE_LEFT, 200));
    TEST_ASSERT_EQUAL_UINT32(140, rehab_target_follow_damping_prepare_align(
        &damping, REHAB_TARGET_ZONE_RIGHT, 200));
    TEST_ASSERT_EQUAL_UINT32(100, rehab_target_follow_damping_prepare_align(
        &damping, REHAB_TARGET_ZONE_RIGHT, 100));
    TEST_ASSERT_EQUAL_UINT8(1, damping.align_overshoot_level);
}

TEST_CASE("two centered decisions reset damping before next far alignment",
          "[target_follow]")
{
    uint32_t streak = 2;
    bool hold = false;
    rehab_target_follow_damping_t damping;
    rehab_target_follow_damping_reset(&damping);
    rehab_target_follow_decision_t decision;
    rehab_target_follow_observation_t value =
        observation(true, REHAB_TARGET_ZONE_LEFT, true, 100.0f);
    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    value.center_x = 0.90f;
    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT8(1, damping.align_overshoot_level);
    value.center_x = 0.50f;
    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT8(0, damping.align_overshoot_level);
    value.center_x = 0.90f;
    TEST_ASSERT_EQUAL(ESP_OK, decide(&value, &streak, &hold, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT32(200, decision.final_align_ms);
}

TEST_CASE("distance boundaries select hold stop and approach tiers",
          "[target_follow]")
{
    rehab_target_follow_damping_t damping;
    rehab_target_follow_decision_t decision;

    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.50f, true, true, 50.0f, &damping, &decision));
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_HOLD, decision.next_state);
    TEST_ASSERT_TRUE(decision.stop_required);

    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.50f, true, true, 60.0f, &damping, &decision));
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_SENSE, decision.next_state);
    TEST_ASSERT_TRUE(decision.stop_required);

    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.50f, true, true, 60.1f, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT32(120, decision.movement_duration_ms);
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.50f, true, true, 80.0f, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT32(120, decision.movement_duration_ms);
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.50f, true, true, 80.1f, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT32(200, decision.movement_duration_ms);
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.50f, true, true, 120.0f, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT32(200, decision.movement_duration_ms);
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.50f, true, true, 120.1f, &damping, &decision));
    TEST_ASSERT_EQUAL_UINT32(300, decision.movement_duration_ms);
}

TEST_CASE("off center target aligns before any far distance approach",
          "[target_follow]")
{
    rehab_target_follow_damping_t damping;
    rehab_target_follow_damping_reset(&damping);
    rehab_target_follow_decision_t decision;
    TEST_ASSERT_EQUAL(ESP_OK, decide_confirmed(
        0.70f, true, true, 500.0f, &damping, &decision));
    TEST_ASSERT_EQUAL(REHAB_TARGET_FOLLOW_STATE_ALIGN, decision.next_state);
    TEST_ASSERT_EQUAL(REHAB_CAR_DIRECTION_ROTATE_CW, decision.direction);
    TEST_ASSERT_NOT_EQUAL(REHAB_CAR_DIRECTION_FORWARD, decision.direction);
}

TEST_CASE("illegal center and distance fields fail closed",
          "[target_follow]")
{
    rehab_target_follow_damping_t damping;
    rehab_target_follow_decision_t decision;
    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE, decide_confirmed(
        NAN, true, true, 100.0f, &damping, &decision));
    TEST_ASSERT_TRUE(decision.stop_required);
    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE, decide_confirmed(
        1.01f, true, true, 100.0f, &damping, &decision));
    TEST_ASSERT_TRUE(decision.stop_required);
    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE, decide_confirmed(
        0.50f, true, false, 100.0f, &damping, &decision));
    TEST_ASSERT_TRUE(decision.stop_required);
    rehab_target_follow_damping_reset(&damping);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE, decide_confirmed(
        0.50f, true, true, INFINITY, &damping, &decision));
    TEST_ASSERT_TRUE(decision.stop_required);
}

TEST_CASE("selected center x overrides horizontal zone for control",
          "[target_follow]")
{
    rehab_cloud_fusion_result_t result = valid_result();
    add_detection(&result, FOLLOW_TARGET_CLASS_NAME, 1, 0.96f, 0.75f,
                  "left", true, true, 100.0f);
    rehab_target_follow_observation_t selected;
    char error[64];
    TEST_ASSERT_EQUAL(ESP_OK, rehab_target_follow_select_observation(
                                  &result, &selected, error, sizeof(error)));
    TEST_ASSERT_EQUAL(REHAB_TARGET_ZONE_RIGHT, selected.zone);
}

TEST_CASE("selected target missing or nonfinite center fails closed",
          "[target_follow]")
{
    rehab_cloud_fusion_result_t result = valid_result();
    add_detection(&result, FOLLOW_TARGET_CLASS_NAME, 1, 0.96f, 0.50f,
                  "center", true, true, 100.0f);
    result.detections[0].has_center_normalized = false;
    rehab_target_follow_observation_t selected;
    char error[64];
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        rehab_target_follow_select_observation(
            &result, &selected, error, sizeof(error)));
    TEST_ASSERT_EQUAL_STRING("target_numeric_invalid", error);

    result.detections[0].has_center_normalized = true;
    result.detections[0].center_normalized.x = NAN;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        rehab_target_follow_select_observation(
            &result, &selected, error, sizeof(error)));
    TEST_ASSERT_EQUAL_STRING("target_numeric_invalid", error);
}
