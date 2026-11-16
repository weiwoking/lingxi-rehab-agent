// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "rehab_target_follow.h"
#include "rehab_capture.h"
#include "rehab_cloud_fusion.h"

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#define FOLLOW_TARGET_CLASS_NAME "person"

typedef enum {
    REHAB_TARGET_ZONE_NONE = 0,
    REHAB_TARGET_ZONE_LEFT,
    REHAB_TARGET_ZONE_CENTER,
    REHAB_TARGET_ZONE_RIGHT,
} rehab_target_zone_t;

typedef struct {
    rehab_target_zone_t last_non_center_zone;
    uint32_t last_align_duration_ms;
    uint8_t align_overshoot_level;
    uint8_t center_streak;
} rehab_target_follow_damping_t;

typedef struct {
    bool found;
    rehab_target_zone_t zone;
    bool center_valid;
    float center_x;
    bool distance_valid;
    bool has_distance_cm;
    float distance_cm;
    bool multiple_zxl;
    int object_id;
    float confidence;
} rehab_target_follow_observation_t;

typedef struct {
    rehab_target_follow_state_t next_state;
    bool stop_required;
    rehab_car_direction_t direction;
    uint32_t movement_duration_ms;
    uint32_t next_sense_delay_ms;
    uint32_t base_align_ms;
    uint32_t final_align_ms;
    const char *reason;
} rehab_target_follow_decision_t;

typedef enum {
    REHAB_TARGET_PERCEPTION_RESULT_SUCCESS = 0,
    REHAB_TARGET_PERCEPTION_RESULT_REAL_FAILURE,
    REHAB_TARGET_PERCEPTION_RESULT_BUSY_CLEANUP,
} rehab_target_perception_result_t;

typedef enum {
    REHAB_TARGET_CAPTURE_RECOVERY_WAIT = 0,
    REHAB_TARGET_CAPTURE_RECOVERY_COMPLETE,
    REHAB_TARGET_CAPTURE_RECOVERY_TIMEOUT,
} rehab_target_capture_recovery_result_t;

struct rehab_target_follow_t {
    SemaphoreHandle_t lock;
    SemaphoreHandle_t worker_done;
    TaskHandle_t worker_task;
    rehab_car_handle_t car;
    rehab_car_control_lease_t car_lease;
    rehab_cloud_fusion_result_t *fusion_result;
    rehab_target_follow_state_t state;
    uint32_t run_generation;
    bool worker_active;
    bool perception_in_flight;
    bool cancel_requested;
    bool capture_recovery;
    bool console_registered;
    bool hold_latched;
    uint32_t next_sense_delay_ms;
    uint32_t last_retry_delay_ms;
    uint32_t confirm_streak;
    rehab_target_zone_t planned_zone;
    uint32_t planned_movement_duration_ms;
    rehab_target_follow_damping_t damping;
    rehab_target_zone_t last_target_zone;
    bool last_center_valid;
    float last_center_x;
    float last_error_x;
    bool last_distance_valid;
    float last_distance_cm;
    char last_action[24];
    uint32_t last_action_hold_ms;
    char last_decision_reason[48];
    uint32_t consecutive_perception_errors;
    bool multiple_zxl_anomaly;
    char last_error[96];
    char root_error[96];
    esp_err_t last_stop_result;
};

void rehab_target_follow_damping_reset(rehab_target_follow_damping_t *damping);
void rehab_target_follow_damping_observe_center(rehab_target_follow_damping_t *damping);
uint32_t rehab_target_follow_damping_prepare_align(
    rehab_target_follow_damping_t *damping,
    rehab_target_zone_t zone,
    uint32_t base_align_ms);
esp_err_t rehab_target_follow_decide_observation(
    const rehab_target_follow_observation_t *observation,
    uint32_t *confirm_streak,
    bool *hold_latched,
    rehab_target_follow_damping_t *damping,
    rehab_target_follow_decision_t *decision);
rehab_target_follow_state_t rehab_target_follow_record_perception_error(
    bool retryable, uint32_t *consecutive_errors);
rehab_target_perception_result_t rehab_target_follow_classify_perception_result(
    esp_err_t err, bool result_ok, const char *last_error);
rehab_target_follow_state_t rehab_target_follow_apply_perception_result(
    rehab_target_perception_result_t result,
    bool retryable,
    uint32_t *consecutive_errors);
rehab_target_capture_recovery_result_t
rehab_target_follow_evaluate_capture_recovery(
    esp_err_t status_result,
    const rehab_capture_status_t *status,
    uint32_t waited_ms,
    uint32_t max_wait_ms);
esp_err_t rehab_target_follow_select_observation(
    const rehab_cloud_fusion_result_t *result,
    rehab_target_follow_observation_t *observation,
    char *error,
    size_t error_size);
const char *rehab_target_follow_zone_name(rehab_target_zone_t zone);
const char *rehab_target_follow_action_name(rehab_car_direction_t direction);
