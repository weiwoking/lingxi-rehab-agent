// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "claw_core.h"
#include "rehab_decision_trace.h"
#include "esp_err.h"
#include "rehab_car.h"
#include "rehab_environment_scan.h"

#ifdef __cplusplus
extern "C" {
#endif

#define REHAB_TASK_DECISION_SCAN_TIMEOUT_MS          30000U
#define REHAB_TASK_DECISION_SCAN_OBSERVATION_COUNT       3U
#define REHAB_TASK_DECISION_MAX_CLASSES                 12U
#define REHAB_TASK_DECISION_CAPTURE_SOURCES_PER_CLASS     3U
#define REHAB_TASK_DECISION_MAX_OBSERVATION_SUMMARIES      3U
#define REHAB_TASK_DECISION_JOB_ID_MAX                   48U
#define REHAB_TASK_DECISION_ERROR_MAX                    48U
#define REHAB_TASK_DECISION_CLASS_NAME_MAX               48U
#define REHAB_TASK_DECISION_NOTIFICATION_ERROR_MAX       64U
#define REHAB_TASK_DECISION_DECISION_ERROR_MAX           64U
#define REHAB_TASK_DECISION_STAGE_MAX                    24U
#define REHAB_TASK_DECISION_SELECTED_TASK_ID_MAX         32U
#define REHAB_TASK_DECISION_SELECTED_TASK_NAME_MAX       48U
#define REHAB_TASK_DECISION_RULES_VERSION_MAX            48U
#define REHAB_TASK_DECISION_REASON_MAX                  512U
#define REHAB_TASK_DECISION_DECISION_ID_MAX              64U
#define REHAB_TASK_DECISION_SCAN_ID_MAX                  64U
#define REHAB_TASK_DECISION_SCENE_SHA256_MAX             65U
#define REHAB_TASK_DECISION_OBJECT_ID_MAX               129U
#define REHAB_TASK_DECISION_FRAME_ID_MAX                 16U
#define REHAB_TASK_DECISION_DISTANCE_STATUS_MAX           64U
#define REHAB_TASK_DECISION_DETAILS_JSON_MAX       (48U * 1024U)
#define REHAB_TASK_DECISION_FORMAL_UI_SESSION_ID "rehab-formal-ui"

typedef struct rehab_task_decision_t *rehab_task_decision_handle_t;
typedef bool (*rehab_task_decision_training_active_fn)(void *ctx);

typedef enum {
    REHAB_TASK_DECISION_STATE_IDLE = 0,
    REHAB_TASK_DECISION_STATE_ACQUIRING_OWNER,
    REHAB_TASK_DECISION_STATE_ROTATING,
    REHAB_TASK_DECISION_STATE_STOPPING,
    REHAB_TASK_DECISION_STATE_SETTLING,
    REHAB_TASK_DECISION_STATE_SENSING,
    REHAB_TASK_DECISION_STATE_AGGREGATING,
    REHAB_TASK_DECISION_STATE_COMPLETED,
    REHAB_TASK_DECISION_STATE_DECIDING,
    REHAB_TASK_DECISION_STATE_VALIDATING,
    REHAB_TASK_DECISION_STATE_COMMITTING,
    REHAB_TASK_DECISION_STATE_READY_SELECTED,
    REHAB_TASK_DECISION_STATE_CANCELLED,
    REHAB_TASK_DECISION_STATE_DEADLINE_EXCEEDED,
    REHAB_TASK_DECISION_STATE_NO_VALID_OBSERVATION,
    REHAB_TASK_DECISION_STATE_ERROR,
} rehab_task_decision_state_t;

typedef struct {
    char class_name[REHAB_TASK_DECISION_CLASS_NAME_MAX];
    /** Number of fusion results containing this class, not unique objects. */
    uint32_t seen_count;
    uint32_t confidence_count;
    float max_confidence;
    uint32_t valid_distance_count;
    float last_distance_cm;
    float min_distance_cm;
    float max_distance_cm;
    float mean_distance_cm;
    uint32_t left_count;
    uint32_t center_count;
    uint32_t right_count;
    uint32_t unknown_zone_count;
    uint64_t first_seen_at_ms;
    uint64_t last_seen_at_ms;
    uint8_t source_capture_count;
    bool source_capture_overflow;
    char source_capture_ids[REHAB_TASK_DECISION_CAPTURE_SOURCES_PER_CLASS][24];
    bool last_object_height_valid;
    float last_object_height_cm;
    bool last_bottom_height_valid;
    float last_bottom_height_cm;
    bool last_geometry_quality_valid;
    float last_geometry_quality;
    uint32_t last_tof_zone_count;
    uint64_t source_tof_zone_mask;
    uint32_t unique_tof_zone_count;
    bool source_tof_zone_overflow;
} rehab_environment_class_summary_t;

typedef struct {
    char capture_id[24];
    uint64_t observed_at_ms;
    uint32_t step;
    bool valid;
    bool pairing_valid;
    bool cloud_valid;
    char cloud_status[24];
    uint32_t detection_count;
    char result_class[REHAB_TASK_DECISION_STAGE_MAX];
    char error_stage[REHAB_TASK_DECISION_STAGE_MAX];
    char error_code[REHAB_TASK_DECISION_ERROR_MAX];
    int http_status;
} rehab_environment_observation_summary_t;

typedef struct {
    rehab_task_decision_state_t state;
    bool worker_active;
    bool perception_in_flight;
    bool cancel_requested;
    bool fusion_ready;
    bool final_state_frozen;
    bool deadline_exceeded;
    bool start_ack_requested;
    bool start_ack_attempted;
    bool start_ack_sent;
    char start_ack_error[REHAB_TASK_DECISION_NOTIFICATION_ERROR_MAX];
    bool notification_requested;
    bool notification_attempted;
    bool notification_sent;
    char notification_error[REHAB_TASK_DECISION_NOTIFICATION_ERROR_MAX];
    bool decision_trigger_requested;
    bool decision_trigger_attempted;
    bool decision_trigger_published;
    char decision_trigger_error[REHAB_TASK_DECISION_DECISION_ERROR_MAX];
    uint64_t decision_started_at_ms;
    uint32_t decision_deadline_ms;
    bool selected_task_available;
    uint32_t generation;
    char job_id[REHAB_TASK_DECISION_JOB_ID_MAX];
    uint64_t started_at_ms;
    uint32_t elapsed_ms;
    uint32_t deadline_ms;
    uint32_t planned_observation_count;
    uint32_t current_step;
    uint32_t requested_observation_count;
    uint32_t completed_observation_count;
    uint32_t object_observation_count;
    uint32_t empty_observation_count;
    uint32_t technical_failure_count;
    /** Compatibility totals: valid=object+empty, rejected=technical failure. */
    uint32_t valid_observation_count;
    uint32_t rejected_observation_count;
    uint32_t rejection_no_detections_count;
    uint32_t rejection_aggregate_capacity_count;
    uint32_t rejection_capture_failed_count;
    uint32_t rejection_fusion_timeout_count;
    uint32_t rejection_fusion_invalid_count;
    uint32_t rejection_late_result_count;
    uint32_t last_rejection_step;
    char last_rejection_stage[REHAB_TASK_DECISION_STAGE_MAX];
    char last_rejection_error[REHAB_TASK_DECISION_ERROR_MAX];
    int last_rejection_http_status;
    char last_capture_id[24];
    char last_error[REHAB_TASK_DECISION_ERROR_MAX];
    esp_err_t last_stop_result;
    rehab_car_control_owner_t car_owner;
    size_t observation_summary_count;
    rehab_environment_observation_summary_t
        observations[REHAB_TASK_DECISION_MAX_OBSERVATION_SUMMARIES];
    size_t class_count;
    rehab_environment_class_summary_t classes[REHAB_TASK_DECISION_MAX_CLASSES];
} rehab_task_decision_status_t;

typedef struct {
    rehab_car_handle_t car;
    bool fusion_ready;
    const char *llm_api_key;
    const char *llm_backend_type;
    const char *llm_model;
    const char *llm_base_url;
    const char *llm_auth_type;
    const char *llm_max_tokens_field;
    uint32_t llm_timeout_ms;
    uint32_t llm_max_tokens;
    rehab_task_decision_training_active_fn training_active;
    void *training_active_ctx;
} rehab_task_decision_config_t;

typedef struct {
    bool available;
    char task_id[REHAB_TASK_DECISION_SELECTED_TASK_ID_MAX];
    char task_name[REHAB_TASK_DECISION_SELECTED_TASK_NAME_MAX];
    char reason[REHAB_TASK_DECISION_REASON_MAX];
    double score;
    int suggested_distance_cm;
    uint32_t decision_generation;
    char decision_rules_version[REHAB_TASK_DECISION_RULES_VERSION_MAX];
    char decision_id[REHAB_TASK_DECISION_DECISION_ID_MAX];
    char source_scan_id[REHAB_TASK_DECISION_SCAN_ID_MAX];
    char source_scene_sha256[REHAB_TASK_DECISION_SCENE_SHA256_MAX];
    bool target_available;
    char target_object_id[REHAB_TASK_DECISION_OBJECT_ID_MAX];
    char target_class[REHAB_TASK_DECISION_CLASS_NAME_MAX];
    char target_source_frame_id[REHAB_TASK_DECISION_FRAME_ID_MAX];
    uint32_t target_source_frame_sequence;
    double target_bbox_xyxy[4];
    bool target_geometry_available;
    bool target_geometry_valid;
    bool target_object_height_available;
    double target_object_height_mm;
    bool target_support_height_available;
    double target_support_height_mm;
    bool target_top_height_available;
    double target_top_height_above_floor_mm;
    bool target_distance_available;
    double target_distance_mm;
    char target_distance_status[REHAB_TASK_DECISION_DISTANCE_STATUS_MAX];
} rehab_task_decision_selected_snapshot_t;

typedef enum {
    REHAB_TASK_DECISION_COORDINATOR_IDLE = 0,
    REHAB_TASK_DECISION_COORDINATOR_PREPARING,
    REHAB_TASK_DECISION_COORDINATOR_DECIDING,
    REHAB_TASK_DECISION_COORDINATOR_VALIDATING,
    REHAB_TASK_DECISION_COORDINATOR_COMMITTING,
    REHAB_TASK_DECISION_COORDINATOR_SELECTED,
    REHAB_TASK_DECISION_COORDINATOR_FAILED,
    REHAB_TASK_DECISION_COORDINATOR_CANCELLED,
} rehab_task_decision_coordinator_state_t;

typedef struct {
    rehab_task_decision_coordinator_state_t state;
    bool active;
    rehab_decision_trace_t trace;
    uint32_t generation;
    char decision_id[REHAB_TASK_DECISION_DECISION_ID_MAX];
    char source_scan_id[REHAB_TASK_DECISION_SCAN_ID_MAX];
    char source_scene_sha256[REHAB_TASK_DECISION_SCENE_SHA256_MAX];
    char task_id[REHAB_TASK_DECISION_SELECTED_TASK_ID_MAX];
    char task_name[REHAB_TASK_DECISION_SELECTED_TASK_NAME_MAX];
    char reason[REHAB_TASK_DECISION_REASON_MAX];
    double score;
    bool target_available;
    char target_object_id[REHAB_TASK_DECISION_OBJECT_ID_MAX];
    char target_class[REHAB_TASK_DECISION_CLASS_NAME_MAX];
    char target_source_frame_id[REHAB_TASK_DECISION_FRAME_ID_MAX];
    uint32_t target_source_frame_sequence;
    double target_bbox_xyxy[4];
    bool target_geometry_available;
    bool target_geometry_valid;
    bool target_object_height_available;
    double target_object_height_mm;
    bool target_support_height_available;
    double target_support_height_mm;
    bool target_top_height_available;
    double target_top_height_above_floor_mm;
    bool target_distance_available;
    double target_distance_mm;
    char target_distance_status[REHAB_TASK_DECISION_DISTANCE_STATUS_MAX];
    bool retry_used;
    char last_error[REHAB_TASK_DECISION_ERROR_MAX];
} rehab_task_decision_coordinator_status_t;

/** Trusted caller class for the post-confirmation start boundary. */
typedef enum {
    REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI = 0,
    REHAB_TASK_DECISION_START_SOURCE_AGENT,
    REHAB_TASK_DECISION_START_SOURCE_CONSOLE,
    REHAB_TASK_DECISION_START_SOURCE_WECHAT,
    REHAB_TASK_DECISION_START_SOURCE_UNKNOWN,
} rehab_task_decision_start_source_t;

/** Normalized outcome of one confirmed start attempt. */
typedef enum {
    REHAB_TASK_DECISION_START_OUTCOME_STARTED = 0,
    REHAB_TASK_DECISION_START_OUTCOME_ATTACHED_BUSY,
    REHAB_TASK_DECISION_START_OUTCOME_REJECTED,
    REHAB_TASK_DECISION_START_OUTCOME_TERMINAL_FAILURE,
} rehab_task_decision_start_outcome_t;

typedef struct {
    rehab_task_decision_start_outcome_t outcome;
    rehab_task_decision_start_source_t source;
    bool created_new_generation;
    uint32_t generation;
    rehab_task_decision_coordinator_state_t state;
    bool active;
    char decision_id[REHAB_TASK_DECISION_DECISION_ID_MAX];
    char scan_id[REHAB_TASK_DECISION_SCAN_ID_MAX];
    char last_error[REHAB_TASK_DECISION_ERROR_MAX];
} rehab_task_decision_start_result_t;

esp_err_t rehab_task_decision_create(
    const rehab_task_decision_config_t *config,
    rehab_task_decision_handle_t *ret_handle);
esp_err_t rehab_task_decision_delete(rehab_task_decision_handle_t handle);

/** Start one locally managed environment scan after caller-side confirmation. */
esp_err_t rehab_task_decision_scan_start(rehab_task_decision_handle_t handle,
                                         const char *source_channel,
                                         const char *source_chat_id);

/** Idempotently cancel a running scan; reports whether no scan was active. */
esp_err_t rehab_task_decision_scan_cancel(rehab_task_decision_handle_t handle,
                                          bool *already_idle);

/** Read a bounded status snapshot without moving the car. */
esp_err_t rehab_task_decision_scan_get_status(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_status_t *status);

/**
 * Read and validate the persisted selected task without changing it.
 * A missing file returns ESP_OK with available=false. Invalid persisted data
 * returns its validation error and is never removed by this API.
 */
esp_err_t rehab_task_decision_selected_get(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_selected_snapshot_t *out_snapshot);

/**
 * Read and validate the non-authoritative decision details snapshot.
 * The loader always reads authoritative selected_task.json and requires the
 * details binding (decision/generation/scene/winner/target) to match it.
 * expected_decision_id is an additional caller binding when non-empty; NULL
 * never means "accept latest details". This API never changes either file.
 */
esp_err_t rehab_task_decision_details_render(
    rehab_task_decision_handle_t handle,
    const char *expected_decision_id,
    char *output,
    size_t output_size);

esp_err_t rehab_task_decision_bind_environment_scan(
    rehab_task_decision_handle_t handle,
    rehab_environment_scan_handle_t environment_scan);

/**
 * Validate the authoritative persisted scene independently of volatile
 * environment-scan RAM state. Missing and malformed files return distinct
 * errors; successful validation optionally returns the frozen identity.
 */
esp_err_t rehab_task_decision_latest_scene_validate(
    rehab_task_decision_handle_t handle,
    char *out_scan_id,
    size_t scan_id_size,
    char *out_sha256,
    size_t sha256_size);

/** Register the shared coordinator's read/start console command. */
esp_err_t rehab_task_decision_coordinator_console_register(
    rehab_task_decision_handle_t handle);
esp_err_t rehab_task_decision_coordinator_get_status(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_coordinator_status_t *out_status);

/**
 * Enter the single post-confirmation start boundary. This function calls the
 * coordinator start exactly once and reads authoritative status exactly once;
 * it never waits, polls, or performs UI navigation. out_status is optional;
 * when supplied, it receives the single authoritative full status snapshot.
 */
esp_err_t rehab_task_decision_start_confirmed(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_start_source_t source,
    rehab_task_decision_start_result_t *out_result,
    rehab_task_decision_coordinator_status_t *out_status);
const char *rehab_task_decision_start_source_name(
    rehab_task_decision_start_source_t source);
const char *rehab_task_decision_start_outcome_name(
    rehab_task_decision_start_outcome_t outcome);
esp_err_t rehab_task_decision_coordinator_wait(
    rehab_task_decision_handle_t handle,
    uint32_t generation,
    uint32_t timeout_ms,
    rehab_task_decision_coordinator_status_t *out_status);
const char *rehab_task_decision_coordinator_state_name(
    rehab_task_decision_coordinator_state_t state);

const char *rehab_task_decision_state_name(rehab_task_decision_state_t state);

/** Register start/status/cancel under one high-level capability group. */
esp_err_t rehab_task_decision_register_capabilities(
    rehab_task_decision_handle_t handle);

/** Request-start context for the reserved LOCAL_UI formal decision turn. */
extern const claw_core_context_provider_t
    rehab_task_decision_formal_fast_context_provider;
const claw_core_context_provider_t *
    rehab_task_decision_formal_fast_context_provider_get(void);

#ifdef __cplusplus
}
#endif
