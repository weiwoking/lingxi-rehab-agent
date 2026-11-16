// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "rehab_car.h"
#include "rehab_capture.h"

#ifdef __cplusplus
extern "C" {
#endif

#define FOLLOW_SPEED_LEVEL                         1U
#define FOLLOW_SEARCH_ROTATE_MS                  250U
#define FOLLOW_CENTER_DEADBAND                    0.08f
#define FOLLOW_ALIGN_NEAR_ERROR_MAX               0.18f
#define FOLLOW_ALIGN_MEDIUM_ERROR_MAX             0.30f
#define FOLLOW_ALIGN_NEAR_MS                     100U
#define FOLLOW_ALIGN_MEDIUM_MS                   140U
#define FOLLOW_ALIGN_FAR_MS                      200U
#define FOLLOW_ALIGN_BASE_MS        FOLLOW_ALIGN_FAR_MS
#define FOLLOW_ALIGN_DAMPED_MS      FOLLOW_ALIGN_MEDIUM_MS
#define FOLLOW_ALIGN_MIN_MS         FOLLOW_ALIGN_NEAR_MS
#define FOLLOW_MOVEMENT_SETTLE_MS                200U
#define FOLLOW_PERCEPTION_RETRY_MS                500U
#define FOLLOW_CAPTURE_RECOVERY_POLL_MS \
    REHAB_CAPTURE_QUIESCENCE_POLL_MS
#define FOLLOW_CAPTURE_RECOVERY_MAX_MS \
    REHAB_CAPTURE_QUIESCENCE_TIMEOUT_MS
#define FOLLOW_CAPTURE_POST_QUIESCENT_SETTLE_MS \
    REHAB_CAPTURE_QUIESCENCE_SETTLE_MS
#define FOLLOW_HOLD_RECHECK_MS                    500U
#define FOLLOW_STOP_DISTANCE_CM                  50.0f
#define FOLLOW_RESUME_DISTANCE_CM                60.0f
#define FOLLOW_APPROACH_NEAR_MAX_CM              80.0f
#define FOLLOW_APPROACH_MEDIUM_MAX_CM           120.0f
#define FOLLOW_APPROACH_NEAR_MS                  120U
#define FOLLOW_APPROACH_MEDIUM_MS                200U
#define FOLLOW_APPROACH_FAR_MS                   300U
#define FOLLOW_APPROACH_FORWARD_MS  FOLLOW_APPROACH_FAR_MS
#define FOLLOW_MAX_CONSECUTIVE_PERCEPTION_ERRORS   3U
#define FOLLOW_FUSION_TIMEOUT_MS                8000U
typedef struct rehab_target_follow_t *rehab_target_follow_handle_t;

typedef enum {
    REHAB_TARGET_FOLLOW_STATE_IDLE = 0,
    REHAB_TARGET_FOLLOW_STATE_SENSE,
    REHAB_TARGET_FOLLOW_STATE_SEARCH,
    REHAB_TARGET_FOLLOW_STATE_CONFIRM,
    REHAB_TARGET_FOLLOW_STATE_ALIGN,
    REHAB_TARGET_FOLLOW_STATE_APPROACH,
    REHAB_TARGET_FOLLOW_STATE_HOLD,
    REHAB_TARGET_FOLLOW_STATE_ERROR,
    REHAB_TARGET_FOLLOW_STATE_CANCELLED,
} rehab_target_follow_state_t;

typedef struct {
    rehab_car_handle_t car;
} rehab_target_follow_config_t;

typedef struct {
    char target[48];
    rehab_target_follow_state_t state;
    bool worker_active;
    bool perception_in_flight;
    bool cancel_requested;
    bool capture_recovery;
    uint32_t retry_delay_ms;
    rehab_car_control_owner_t car_owner;
    uint32_t confirm_streak;
    char last_target_zone[8];
    bool last_center_valid;
    float last_center_x;
    float last_error_x;
    bool last_distance_valid;
    float last_distance_cm;
    char last_action[24];
    uint32_t last_action_hold_ms;
    char last_decision_reason[48];
    uint8_t align_overshoot_level;
    uint32_t last_align_duration_ms;
    uint32_t consecutive_perception_errors;
    bool multiple_zxl_anomaly;
    char last_error[96];
    char root_error[96];
    esp_err_t last_stop_result;
} rehab_target_follow_status_t;

esp_err_t rehab_target_follow_create(const rehab_target_follow_config_t *config,
                                     rehab_target_follow_handle_t *out_handle);
esp_err_t rehab_target_follow_delete(rehab_target_follow_handle_t handle);
esp_err_t rehab_target_follow_start(rehab_target_follow_handle_t handle);
esp_err_t rehab_target_follow_stop(rehab_target_follow_handle_t handle);
esp_err_t rehab_target_follow_get_status(rehab_target_follow_handle_t handle,
                                         rehab_target_follow_status_t *status);
esp_err_t rehab_target_follow_register_console(rehab_target_follow_handle_t handle);
esp_err_t rehab_target_follow_register_capabilities(rehab_target_follow_handle_t handle);
const char *rehab_target_follow_state_name(rehab_target_follow_state_t state);

#ifdef __cplusplus
}
#endif
