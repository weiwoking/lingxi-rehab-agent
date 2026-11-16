// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "rehab_person_follow.h"
#include "rehab_follow.h"

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "freertos/timers.h"

/* Match the field-proven legacy target_follow worker; PF_STACK logs provide
 * measured high-water evidence for any later tuning. ESP32-P4 units are bytes. */
#define PERSON_FOLLOW_WORKER_STACK 8192U
#define PERSON_FOLLOW_WORKER_PRIORITY 4U
#define PERSON_FOLLOW_STOP_WAIT_MS 3000U
#define PERSON_FOLLOW_LEFT_BOUNDARY 0.20f
#define PERSON_FOLLOW_RIGHT_BOUNDARY 0.80f
#define PERSON_FOLLOW_LEFT_CENTER_X 0.10f
#define PERSON_FOLLOW_CENTER_CENTER_X 0.50f
#define PERSON_FOLLOW_RIGHT_CENTER_X 0.90f
#define PERSON_FOLLOW_MOCK_CONFIDENCE 0.90f
#define PERSON_FOLLOW_MAX_DETECTIONS_LIMIT 16U
#define PERSON_FOLLOW_RESPONSE_MAX (16U * 1024U)
#define PERSON_FOLLOW_HTTP_CHUNK_BYTES (16U * 1024U)
#define PERSON_FOLLOW_JPEG_MAX_BYTES (128U * 1024U)
#define PERSON_FOLLOW_FRAME_ID_MAX 64U
#define PERSON_FOLLOW_ERROR_CODE_MAX 48U
#define PERSON_FOLLOW_IMAGE_WIDTH 640U
#define PERSON_FOLLOW_IMAGE_HEIGHT 360U
#define PERSON_FOLLOW_TARGET_CLASS "person"
#define PERSON_FOLLOW_SNAPSHOT_SLOTS 2U
#define PERSON_FOLLOW_ROLLING_TASK_STACK 4096U
#define PERSON_FOLLOW_ROLLING_TASK_PRIORITY 4U
#define PERSON_FOLLOW_ROLLING_FORWARD_ENABLED true
#define PERSON_FOLLOW_ROLLING_FORWARD_SEGMENT_MS 500U
#define PERSON_FOLLOW_ROLLING_FORWARD_LEASE_MS 900U
#define PERSON_FOLLOW_ROLLING_IDLE_WAIT_MS 1000U

typedef struct {
    bool valid;
    bool target_found;
    float confidence;
    float center_x;
    float center_y;
    float bbox_x1;
    float bbox_y1;
    float bbox_x2;
    float bbox_y2;
    float area_normalized;
    uint32_t mock_sequence;
} rehab_person_follow_observation_t;

typedef struct {
    float confidence;
    float x1;
    float y1;
    float x2;
    float y2;
    float center_x;
    float center_y;
    float area_normalized;
} rehab_person_follow_detection_t;

typedef struct {
    char frame_id[PERSON_FOLLOW_FRAME_ID_MAX + 1U];
    uint32_t generation;
    int64_t capture_timestamp_us;
} rehab_person_follow_request_meta_t;

typedef struct {
    bool target_found;
    uint32_t detection_count;
    float pc_inference_ms;
    float pc_total_ms;
    rehab_person_follow_detection_t
        detections[PERSON_FOLLOW_MAX_DETECTIONS_LIMIT];
} rehab_person_follow_response_t;

typedef struct {
    rehab_person_follow_response_t parsed;
    rehab_person_follow_detection_t
        deduplicated[PERSON_FOLLOW_MAX_DETECTIONS_LIMIT];
} rehab_person_follow_workspace_t;

typedef struct {
    rehab_person_follow_observation_t observation;
    rehab_person_follow_target_zone_t zone;
    int http_status;
    size_t jpeg_bytes;
    uint32_t raw_detection_count;
    uint32_t dedup_detection_count;
    uint32_t http_ms;
    uint32_t camera_ms;
    uint32_t perception_ms;
    float pc_inference_ms;
    float pc_total_ms;
    uint64_t camera_generation;
    int64_t capture_timestamp_us;
    uint32_t image_stage_token;
    char error_stage[16];
    char error_code[PERSON_FOLLOW_ERROR_CODE_MAX];
} rehab_person_follow_perception_result_t;

typedef enum {
    REHAB_PERSON_FOLLOW_ZONE_NONE = 0,
    REHAB_PERSON_FOLLOW_ZONE_LEFT,
    REHAB_PERSON_FOLLOW_ZONE_CENTER,
    REHAB_PERSON_FOLLOW_ZONE_RIGHT,
} rehab_person_follow_zone_t;

typedef struct {
    rehab_person_follow_state_t state;
    uint32_t generation;
    uint32_t confirm_streak;
    uint32_t missing_streak;
    uint32_t consecutive_errors;
    uint32_t search_sequence;
    bool target_confirmed;
    bool ever_had_target;
    rehab_person_follow_zone_t confirmation_zone;
    bool has_forwarded_once;
    bool fine_turn_mode;
    bool target_acquired_once;
    uint32_t turn_hold_ms;
    bool turn_check_pending;
    rehab_person_follow_action_t last_successful_turn_direction;
    rehab_person_follow_approach_phase_t approach_phase;
    bool first_align_completed;
    rehab_person_follow_align_gate_t active_align_gate;
    uint32_t align_stable_count;
    uint32_t last_stable_frame_id;
    uint32_t pending_align_trigger_frame_id;
    bool align_stop_completed;
    uint32_t first_align_trigger_frame_id;
    uint32_t final_align_trigger_frame_id;
    uint32_t align_overshoot_count;
    uint32_t final_forward_attempted_frame_id;
    rehab_person_follow_arrival_reason_t arrival_reason;
} rehab_person_follow_decision_context_t;

typedef struct {
    rehab_person_follow_state_t next_state;
    rehab_person_follow_action_t action;
    const char *reason;
    bool forward_attempted;
    float bbox_width_ratio;
    float person_center_x_ratio;
    float left_background_ratio;
    float right_background_ratio;
    uint32_t hold_ms;
    bool target_guided_turn;
    bool overshoot_reverse;
    bool consume_turn_check;
    bool reduce_turn_hold;
    bool stop_rolling;
    bool align_trigger;
    bool align_stable_reset;
    const char *align_stable_reset_reason;
    bool align_turn;
    bool final_forward;
    bool direct_arrival;
    bool first_target_acquired;
} rehab_person_follow_decision_t;

typedef struct {
    uint8_t *data;
    size_t length;
    uint64_t camera_generation;
    int64_t capture_timestamp_us;
    uint64_t image_snapshot_seq;
    uint32_t lease_count;
    uint32_t staged_worker_generation;
    bool valid;
    bool staging;
    bool staged;
} rehab_person_follow_snapshot_slot_t;

struct rehab_person_follow {
    SemaphoreHandle_t lock;
    SemaphoreHandle_t worker_done_event;
    SemaphoreHandle_t rolling_done_event;
    TaskHandle_t worker_task;
    TaskHandle_t rolling_task;
    TimerHandle_t rolling_lease_timer;
    rehab_car_handle_t car;
    rehab_car_control_lease_t car_lease;
    rehab_person_follow_config_t config;
    char endpoint_url[REHAB_PERSON_FOLLOW_ENDPOINT_MAX];
    char *response_buffer;
    rehab_person_follow_workspace_t *workspace;
    rehab_person_follow_decision_context_t decision_context;
    rehab_person_follow_state_t state;
    uint32_t generation;
    uint32_t mock_sequence;
    uint32_t frame_sequence;
    int64_t started_at_us;
    int64_t startup_observation_started_us;
    bool initialized;
    bool running;
    bool start_in_progress;
    bool cancel_requested;
    bool worker_alive;
    bool worker_done;
    bool owner_acquired;
    bool shutdown_pending;
    bool forward_rolling;
    bool rolling_task_alive;
    bool rolling_task_busy;
    bool rolling_exit_requested;
    uint32_t rolling_generation;
    uint32_t forward_segments_completed;
    int64_t last_center_confirmed_us;
    int64_t forward_lease_deadline_us;
    char rolling_stop_reason[REHAB_PERSON_FOLLOW_ROLLING_STOP_REASON_MAX];
    bool last_selected_center_valid;
    float last_selected_center_x;
    char last_error[64];
    char last_reason[64];
    bool last_target_found;
    float last_center_x;
    float last_confidence;
    rehab_person_follow_target_zone_t last_zone;
    int last_http_status;
    size_t last_jpeg_bytes;
    uint32_t last_detection_count;
    uint32_t last_dedup_count;
    uint32_t last_perception_ms;
    rehab_person_follow_action_t last_planned_action;
    rehab_person_follow_action_t last_action;
    float last_bbox_width_ratio;
    float last_person_center_x_ratio;
    float last_left_background_ratio;
    float last_right_background_ratio;
    uint32_t last_post_action_wait_ms;
    esp_err_t last_car_result;
    bool forward_attempted;
    bool motion_unconfirmed;
    bool blocked_unknown;
    bool startup_observation_active;
    bool startup_observation_no_target_logged;
    rehab_person_follow_startup_observation_exit_reason_t
        startup_observation_exit_reason;
    uint32_t runtime_ms;
    rehab_person_follow_snapshot_slot_t
        snapshot_slots[PERSON_FOLLOW_SNAPSHOT_SLOTS];
    int snapshot_active_index;
    uint64_t snapshot_sequence;
    rehab_person_follow_snapshot_info_t snapshot_info;
};

esp_err_t rehab_person_follow_mock_observe(
    rehab_person_follow_mock_mode_t mode,
    uint32_t sequence,
    rehab_person_follow_observation_t *out_observation);
esp_err_t rehab_person_follow_decide(
    rehab_person_follow_decision_context_t *context,
    const rehab_person_follow_observation_t *observation,
    uint32_t frame_id,
    uint32_t current_generation,
    bool cancel_requested,
    uint32_t max_consecutive_errors,
    uint32_t turn_hold_ms,
    uint32_t forward_hold_ms,
    rehab_person_follow_decision_t *out_decision);
void rehab_person_follow_reset_adaptive_turn(
    rehab_person_follow_decision_context_t *context,
    uint32_t turn_hold_ms);
void rehab_person_follow_commit_action(
    rehab_person_follow_decision_context_t *context,
    const rehab_person_follow_decision_t *decision,
    bool executed_successfully);
void rehab_person_follow_commit_align_stop(
    rehab_person_follow_decision_context_t *context,
    const rehab_person_follow_decision_t *decision,
    bool stop_completed);
bool rehab_person_follow_suppress_startup_no_target(
    rehab_person_follow_handle_t handle,
    uint32_t generation,
    int64_t now_us,
    esp_err_t observe_result,
    const rehab_person_follow_observation_t *observation,
    rehab_person_follow_decision_t *out_decision,
    bool *out_log_suppression);
void rehab_person_follow_accept_startup_target(
    rehab_person_follow_handle_t handle,
    uint32_t generation,
    esp_err_t observe_result,
    const rehab_person_follow_observation_t *observation,
    esp_err_t decision_result);
esp_err_t rehab_person_follow_execute_action(
    rehab_person_follow_handle_t handle,
    uint32_t generation,
    rehab_person_follow_action_t action,
    uint32_t planned_hold_ms,
    uint32_t *out_hold_ms);
uint32_t rehab_person_follow_post_action_settle_ms(
    const rehab_person_follow_config_t *config,
    rehab_person_follow_action_t executed_action,
    esp_err_t action_result,
    bool generation_current);
esp_err_t rehab_person_follow_car_move(
    rehab_car_handle_t car,
    const rehab_car_control_lease_t *lease,
    rehab_car_direction_t direction,
    uint8_t speed_level,
    uint32_t hold_ms);
esp_err_t rehab_person_follow_car_stop(
    rehab_car_handle_t car,
    const rehab_car_control_lease_t *lease);
rehab_person_follow_zone_t rehab_person_follow_zone_from_center(float center_x);
const char *rehab_person_follow_zone_name(rehab_person_follow_zone_t zone);
bool rehab_person_follow_generation_current(
    rehab_person_follow_handle_t handle, uint32_t generation);
void rehab_person_follow_log_stack(const char *stage);
esp_err_t rehab_person_follow_parse_response(
    const char *json,
    const rehab_person_follow_request_meta_t *expected,
    uint32_t max_detections,
    rehab_person_follow_response_t *out_response,
    char *error_code,
    size_t error_code_size);
void rehab_person_follow_parse_error_code(
    const char *json, char *error_code, size_t error_code_size);
uint32_t rehab_person_follow_deduplicate(
    const rehab_person_follow_detection_t *input,
    uint32_t input_count,
    rehab_person_follow_detection_t *output,
    uint32_t output_capacity);
bool rehab_person_follow_select_target(
    const rehab_person_follow_detection_t *detections,
    uint32_t detection_count,
    bool last_center_valid,
    float last_center_x,
    rehab_person_follow_detection_t *out_selected);
rehab_person_follow_target_zone_t rehab_person_follow_classify_target(
    bool target_found, float center_x);
esp_err_t rehab_person_follow_perceive(
    rehab_person_follow_handle_t handle,
    uint32_t generation,
    uint32_t sequence,
    rehab_person_follow_perception_result_t *out_result);
esp_err_t rehab_person_follow_snapshot_stage(
    rehab_person_follow_handle_t handle,
    uint32_t worker_generation,
    const uint8_t *jpeg,
    size_t jpeg_length,
    uint64_t camera_generation,
    int64_t capture_timestamp_us,
    uint32_t *out_stage_token);
void rehab_person_follow_snapshot_discard_stage(
    rehab_person_follow_handle_t handle,
    uint32_t stage_token);
esp_err_t rehab_person_follow_snapshot_publish(
    rehab_person_follow_handle_t handle,
    uint32_t worker_generation,
    uint32_t stage_token,
    const rehab_person_follow_snapshot_info_t *info);
void rehab_person_follow_snapshot_reset_for_start(
    rehab_person_follow_handle_t handle);
bool rehab_person_follow_snapshot_leases_idle(
    rehab_person_follow_handle_t handle);

esp_err_t rehab_person_follow_http_post(
    const char *url,
    uint32_t timeout_ms,
    const uint8_t *jpeg,
    size_t jpeg_length,
    const char *metadata_json,
    char *response,
    size_t response_capacity,
    size_t *out_response_length,
    int *out_http_status,
    uint32_t *out_elapsed_ms);
esp_err_t rehab_person_follow_multipart_content_length(
    size_t jpeg_length, size_t metadata_length, size_t *out_content_length);
esp_err_t rehab_person_follow_camera_acquire(
    rehab_follow_preview_frame_t *frame, uint32_t timeout_ms);
void rehab_person_follow_camera_release(rehab_follow_preview_frame_t *frame);

/* Weak port seam used by the component test app to force task-create failure. */
BaseType_t rehab_person_follow_task_create(
    TaskFunction_t task_function,
    const char *task_name,
    uint32_t stack_depth,
    void *task_argument,
    UBaseType_t priority,
    TaskHandle_t *out_task);

/* Internal rolling state seams kept visible to the Unity component tests. */
esp_err_t rehab_person_follow_rolling_confirm_center(
    rehab_person_follow_handle_t handle, uint32_t generation, int64_t now_us);
esp_err_t rehab_person_follow_rolling_preempt(
    rehab_person_follow_handle_t handle, uint32_t generation,
    const char *reason, bool wait_idle);
bool rehab_person_follow_rolling_segment_allowed(
    rehab_person_follow_handle_t handle, uint32_t generation, int64_t now_us);
bool rehab_person_follow_rolling_segment_finished(
    rehab_person_follow_handle_t handle, uint32_t generation,
    esp_err_t result, int64_t now_us);
