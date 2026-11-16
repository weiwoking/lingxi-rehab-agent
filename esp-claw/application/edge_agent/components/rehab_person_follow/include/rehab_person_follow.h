// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "rehab_car.h"

#ifdef __cplusplus
extern "C" {
#endif

#define REHAB_PERSON_FOLLOW_DEFAULT_MOCK_INTERVAL_MS 250U
#define REHAB_PERSON_FOLLOW_DEFAULT_MAX_RUNTIME_MS 600000U
#define REHAB_PERSON_FOLLOW_DEFAULT_MAX_ERRORS 3U
#define REHAB_PERSON_FOLLOW_TURN_INITIAL_MS 250U
#define REHAB_PERSON_FOLLOW_TURN_STEP_MS 25U
#define REHAB_PERSON_FOLLOW_TURN_MIN_MS 100U
/* Compatibility aliases project the single turn-duration authority. */
#define REHAB_PERSON_FOLLOW_TARGET_TURN_INITIAL_MS \
    REHAB_PERSON_FOLLOW_TURN_INITIAL_MS
#define REHAB_PERSON_FOLLOW_TARGET_TURN_STEP_MS \
    REHAB_PERSON_FOLLOW_TURN_STEP_MS
#define REHAB_PERSON_FOLLOW_TARGET_TURN_MIN_MS \
    REHAB_PERSON_FOLLOW_TURN_MIN_MS
#define REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_GRACE_MS 1000U
#define REHAB_PERSON_FOLLOW_FIRST_ALIGN_TRIGGER_WIDTH_RATIO 0.25f
#define REHAB_PERSON_FOLLOW_FINAL_APPROACH_TRIGGER_WIDTH_RATIO 0.60f
#define REHAB_PERSON_FOLLOW_FIRST_ALIGN_LEFT_BOUNDARY 0.40f
#define REHAB_PERSON_FOLLOW_FIRST_ALIGN_RIGHT_BOUNDARY 0.60f
#define REHAB_PERSON_FOLLOW_FINAL_ALIGN_LEFT_BOUNDARY 0.45f
#define REHAB_PERSON_FOLLOW_FINAL_ALIGN_RIGHT_BOUNDARY 0.55f
#define REHAB_PERSON_FOLLOW_FINAL_ALIGN_SIDE_MARGIN_RATIO 0.02f
#define REHAB_PERSON_FOLLOW_ALIGN_STABLE_FRAMES_REQUIRED 2U
#define REHAB_PERSON_FOLLOW_FINAL_FORWARD_HOLD_MS 1300U
#define REHAB_PERSON_FOLLOW_ENDPOINT_MAX 320U
#define REHAB_PERSON_FOLLOW_SNAPSHOT_ERROR_STAGE_MAX 16U
#define REHAB_PERSON_FOLLOW_SNAPSHOT_IMAGE_MAX (128U * 1024U)
#define REHAB_PERSON_FOLLOW_ROLLING_STOP_REASON_MAX 48U

typedef struct rehab_person_follow *rehab_person_follow_handle_t;

typedef enum {
    REHAB_PERSON_FOLLOW_MOCK_NO_TARGET = 0,
    REHAB_PERSON_FOLLOW_MOCK_TARGET_LEFT,
    REHAB_PERSON_FOLLOW_MOCK_TARGET_CENTER,
    REHAB_PERSON_FOLLOW_MOCK_TARGET_RIGHT,
    REHAB_PERSON_FOLLOW_MOCK_ALTERNATING_LEFT_RIGHT,
    REHAB_PERSON_FOLLOW_MOCK_TECHNICAL_ERROR,
} rehab_person_follow_mock_mode_t;

typedef enum {
    REHAB_PERSON_FOLLOW_PERCEPTION_MOCK = 0,
    REHAB_PERSON_FOLLOW_PERCEPTION_REAL,
} rehab_person_follow_perception_provider_t;

typedef enum {
    REHAB_PERSON_FOLLOW_TARGET_NO_TARGET = 0,
    REHAB_PERSON_FOLLOW_TARGET_LEFT,
    REHAB_PERSON_FOLLOW_TARGET_CENTER,
    REHAB_PERSON_FOLLOW_TARGET_RIGHT,
} rehab_person_follow_target_zone_t;

typedef enum {
    REHAB_PERSON_FOLLOW_STATE_IDLE = 0,
    REHAB_PERSON_FOLLOW_STATE_STARTING,
    REHAB_PERSON_FOLLOW_STATE_SENSE,
    REHAB_PERSON_FOLLOW_STATE_CONFIRM,
    REHAB_PERSON_FOLLOW_STATE_SEARCH,
    REHAB_PERSON_FOLLOW_STATE_ALIGN,
    REHAB_PERSON_FOLLOW_STATE_FOLLOW_FORWARD,
    REHAB_PERSON_FOLLOW_STATE_TARGET_LOST,
    REHAB_PERSON_FOLLOW_STATE_RECOVER,
    REHAB_PERSON_FOLLOW_STATE_STOPPING,
    REHAB_PERSON_FOLLOW_STATE_CANCELLED,
    REHAB_PERSON_FOLLOW_STATE_ERROR,
    REHAB_PERSON_FOLLOW_STATE_FIRST_ALIGN,
    REHAB_PERSON_FOLLOW_STATE_FINAL_ALIGN,
    REHAB_PERSON_FOLLOW_STATE_FINAL_FORWARD,
    REHAB_PERSON_FOLLOW_STATE_ARRIVED,
} rehab_person_follow_state_t;

typedef enum {
    REHAB_PERSON_FOLLOW_ACTION_NONE = 0,
    REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT,
    REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT,
    REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT,
    REHAB_PERSON_FOLLOW_ACTION_FORWARD,
} rehab_person_follow_action_t;

typedef enum {
    REHAB_PERSON_FOLLOW_APPROACH_NORMAL_FOLLOW = 0,
    REHAB_PERSON_FOLLOW_APPROACH_FIRST_ALIGN,
    REHAB_PERSON_FOLLOW_APPROACH_FINAL_ALIGN,
    REHAB_PERSON_FOLLOW_APPROACH_FINAL_FORWARD,
    REHAB_PERSON_FOLLOW_APPROACH_ARRIVED,
} rehab_person_follow_approach_phase_t;

typedef enum {
    REHAB_PERSON_FOLLOW_ALIGN_GATE_NONE = 0,
    REHAB_PERSON_FOLLOW_ALIGN_GATE_FIRST,
    REHAB_PERSON_FOLLOW_ALIGN_GATE_FINAL,
} rehab_person_follow_align_gate_t;

typedef enum {
    REHAB_PERSON_FOLLOW_ARRIVAL_NONE = 0,
    REHAB_PERSON_FOLLOW_ARRIVAL_FINAL_FORWARD_SUCCEEDED,
    REHAB_PERSON_FOLLOW_ARRIVAL_BOTH_MARGINS_MISSING_CENTERED,
} rehab_person_follow_arrival_reason_t;

typedef enum {
    REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_NONE = 0,
    REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_TARGET_FOUND,
    REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_GRACE_EXPIRED_NO_TARGET,
    REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_STOPPED,
    REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_GENERATION_CHANGED,
} rehab_person_follow_startup_observation_exit_reason_t;

typedef enum {
    REHAB_PERSON_FOLLOW_SNAPSHOT_IMAGE_NONE = 0,
    REHAB_PERSON_FOLLOW_SNAPSHOT_IMAGE_JPEG,
} rehab_person_follow_snapshot_image_format_t;

typedef struct {
    uint64_t snapshot_seq;
    bool valid;
    bool image_current;
    uint64_t image_snapshot_seq;
    rehab_person_follow_snapshot_image_format_t image_format;
    uint32_t width;
    uint32_t height;
    size_t image_length;
    uint64_t camera_generation;
    int64_t capture_timestamp_us;

    bool target_found;
    uint32_t raw_count;
    uint32_t dedup_count;
    bool selected_valid;
    float bbox_x1;
    float bbox_y1;
    float bbox_x2;
    float bbox_y2;
    float center_x;
    float center_y;
    float confidence;
    float bbox_width_ratio;
    float person_center_x_ratio;
    float left_background_ratio;
    float right_background_ratio;
    rehab_person_follow_target_zone_t zone;

    rehab_person_follow_approach_phase_t approach_phase;
    bool first_align_completed;
    rehab_person_follow_align_gate_t active_align_gate;
    uint32_t align_stable_count;
    uint32_t align_stable_required;
    uint32_t first_align_trigger_frame_id;
    uint32_t final_align_trigger_frame_id;
    uint32_t align_turn_hold_ms;
    rehab_person_follow_action_t align_last_turn_direction;
    uint32_t align_overshoot_count;
    uint32_t turn_hold_ms;
    bool target_acquired_once;
    bool turn_check_pending;
    rehab_person_follow_action_t last_successful_turn_direction;
    uint32_t final_forward_attempted_frame_id;
    bool arrived;
    rehab_person_follow_arrival_reason_t arrival_reason;

    rehab_person_follow_action_t planned_action;
    rehab_person_follow_action_t actual_action;
    char action_reason[64];
    uint8_t speed;
    uint32_t hold_ms;
    uint32_t post_action_wait_ms;
    bool owner_acquired;
    esp_err_t last_car_result;
    bool has_forwarded_once;
    bool fine_turn_mode;
    bool overshoot_reverse;
    bool pending_target_turn_check;
    rehab_person_follow_action_t last_target_turn_action;
    uint32_t next_turn_hold_ms;
    bool forward_rolling;
    uint32_t forward_lease_remaining_ms;
    uint32_t forward_segments_completed;
    uint32_t last_center_confirmed_age_ms;
    char rolling_stop_reason[REHAB_PERSON_FOLLOW_ROLLING_STOP_REASON_MAX];
    bool rolling_task_alive;
    bool rolling_task_busy;
    bool startup_observation_active;
    uint32_t startup_observation_remaining_ms;
    rehab_person_follow_startup_observation_exit_reason_t
        startup_observation_exit_reason;

    uint32_t camera_ms;
    size_t jpeg_bytes;
    uint32_t http_ms;
    int http_status;
    float pc_inference_ms;
    float pc_total_ms;
    uint32_t loop_total_ms;

    char error_stage[REHAB_PERSON_FOLLOW_SNAPSHOT_ERROR_STAGE_MAX];
    char last_error[64];
    uint32_t worker_generation;
    bool cancel_requested;
} rehab_person_follow_snapshot_info_t;

typedef struct {
    rehab_person_follow_snapshot_info_t info;
    const uint8_t *image_data;
    uint32_t image_lease_token;
} rehab_person_follow_snapshot_frame_t;

typedef struct {
    rehab_car_handle_t car;
    rehab_person_follow_perception_provider_t perception_provider;
    rehab_person_follow_mock_mode_t mock_mode;
    const char *pc_endpoint;
    uint32_t http_timeout_ms;
    uint32_t camera_timeout_ms;
    uint32_t loop_interval_ms;
    uint32_t max_detections;
    uint32_t max_runtime_ms;
    uint32_t max_consecutive_errors;
    bool action_enabled;
    uint8_t action_speed;
    uint32_t turn_hold_ms;
    uint32_t forward_hold_ms;
    uint32_t fallback_post_action_settle_ms;
    uint32_t forward_post_action_settle_ms;
    uint32_t turn_post_action_settle_ms;
} rehab_person_follow_config_t;

#define REHAB_PERSON_FOLLOW_CONFIG_DEFAULT(car_handle) { \
    .car = (car_handle), \
    .perception_provider = REHAB_PERSON_FOLLOW_PERCEPTION_MOCK, \
    .mock_mode = REHAB_PERSON_FOLLOW_MOCK_NO_TARGET, \
    .pc_endpoint = "http://192.168.137.1:8000/api/v1/person-follow/detect", \
    .http_timeout_ms = 2000U, \
    .camera_timeout_ms = 1000U, \
    .loop_interval_ms = REHAB_PERSON_FOLLOW_DEFAULT_MOCK_INTERVAL_MS, \
    .max_detections = 16U, \
    .max_runtime_ms = REHAB_PERSON_FOLLOW_DEFAULT_MAX_RUNTIME_MS, \
    .max_consecutive_errors = REHAB_PERSON_FOLLOW_DEFAULT_MAX_ERRORS, \
    .action_enabled = false, \
    .action_speed = 3U, \
    .turn_hold_ms = REHAB_PERSON_FOLLOW_TARGET_TURN_INITIAL_MS, \
    .forward_hold_ms = 500U, \
    .fallback_post_action_settle_ms = 100U, \
    .forward_post_action_settle_ms = 10U, \
    .turn_post_action_settle_ms = 40U, \
}

typedef struct {
    bool initialized;
    bool running;
    rehab_person_follow_state_t state;
    uint32_t generation;
    bool cancel_requested;
    bool worker_alive;
    bool worker_done;
    bool owner_acquired;
    bool action_enabled;
    rehab_person_follow_perception_provider_t perception_provider;
    rehab_person_follow_mock_mode_t mock_mode;
    int last_http_status;
    size_t last_jpeg_bytes;
    uint32_t last_detection_count;
    uint32_t last_dedup_count;
    char last_error[64];
    char last_reason[64];
    bool last_target_found;
    float last_center_x;
    float last_confidence;
    rehab_person_follow_target_zone_t last_zone;
    uint32_t last_perception_ms;
    rehab_person_follow_action_t last_planned_action;
    rehab_person_follow_action_t last_action;
    float last_bbox_width_ratio;
    float last_person_center_x_ratio;
    float last_left_background_ratio;
    float last_right_background_ratio;
    rehab_person_follow_approach_phase_t approach_phase;
    bool first_align_completed;
    rehab_person_follow_align_gate_t active_align_gate;
    uint32_t align_stable_count;
    uint32_t align_stable_required;
    uint32_t first_align_trigger_frame_id;
    uint32_t final_align_trigger_frame_id;
    uint32_t align_turn_hold_ms;
    rehab_person_follow_action_t align_last_turn_direction;
    uint32_t align_overshoot_count;
    uint32_t turn_hold_ms;
    bool target_acquired_once;
    bool turn_check_pending;
    rehab_person_follow_action_t last_successful_turn_direction;
    uint32_t final_forward_attempted_frame_id;
    bool arrived;
    rehab_person_follow_arrival_reason_t arrival_reason;
    uint32_t post_action_wait_ms;
    esp_err_t last_car_result;
    bool forward_attempted;
    bool motion_unconfirmed;
    bool blocked_unknown;
    bool shutdown_pending;
    bool has_forwarded_once;
    bool fine_turn_mode;
    bool pending_target_turn_check;
    rehab_person_follow_action_t last_target_turn_action;
    uint32_t next_turn_hold_ms;
    bool forward_rolling;
    uint32_t forward_lease_remaining_ms;
    uint32_t forward_segments_completed;
    uint32_t last_center_confirmed_age_ms;
    char rolling_stop_reason[REHAB_PERSON_FOLLOW_ROLLING_STOP_REASON_MAX];
    bool rolling_task_alive;
    bool rolling_task_busy;
    bool startup_observation_active;
    uint32_t startup_observation_remaining_ms;
    rehab_person_follow_startup_observation_exit_reason_t
        startup_observation_exit_reason;
    uint32_t runtime_ms;
    rehab_person_follow_snapshot_info_t snapshot;
} rehab_person_follow_status_t;

esp_err_t rehab_person_follow_create(
    const rehab_person_follow_config_t *config,
    rehab_person_follow_handle_t *out_handle);
esp_err_t rehab_person_follow_delete(rehab_person_follow_handle_t handle);
esp_err_t rehab_person_follow_start(rehab_person_follow_handle_t handle);
esp_err_t rehab_person_follow_stop(rehab_person_follow_handle_t handle);
esp_err_t rehab_person_follow_get_status(
    rehab_person_follow_handle_t handle,
    rehab_person_follow_status_t *out_status);
esp_err_t rehab_person_follow_snapshot_acquire(
    rehab_person_follow_handle_t handle,
    uint64_t expected_snapshot_seq,
    rehab_person_follow_snapshot_frame_t *out_frame);
void rehab_person_follow_snapshot_release(
    rehab_person_follow_handle_t handle,
    rehab_person_follow_snapshot_frame_t *frame);
esp_err_t rehab_person_follow_register_capabilities(
    rehab_person_follow_handle_t handle);

const char *rehab_person_follow_state_name(rehab_person_follow_state_t state);
const char *rehab_person_follow_action_name(rehab_person_follow_action_t action);
const char *rehab_person_follow_approach_phase_name(
    rehab_person_follow_approach_phase_t phase);
const char *rehab_person_follow_align_gate_name(
    rehab_person_follow_align_gate_t gate);
const char *rehab_person_follow_arrival_reason_name(
    rehab_person_follow_arrival_reason_t reason);
const char *rehab_person_follow_mock_mode_name(
    rehab_person_follow_mock_mode_t mode);
const char *rehab_person_follow_perception_provider_name(
    rehab_person_follow_perception_provider_t provider);
const char *rehab_person_follow_target_zone_name(
    rehab_person_follow_target_zone_t zone);
const char *rehab_person_follow_snapshot_image_format_name(
    rehab_person_follow_snapshot_image_format_t format);
const char *rehab_person_follow_startup_observation_exit_reason_name(
    rehab_person_follow_startup_observation_exit_reason_t reason);

#ifdef __cplusplus
}
#endif
