// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "rehab_task_decision.h"
#include "rehab_decision_trace_internal.h"

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "cJSON.h"
#include "claw_core.h"
#include "rehab_cloud_fusion.h"
#include "rehab_task_decision_policy.h"

#define REHAB_TASK_DECISION_RULES_VERSION \
    REHAB_TASK_POLICY_RULES_VERSION
#define REHAB_TASK_DECISION_OUTPUT_SCHEMA_VERSION \
    "rehab_task_decision_output.v4"
#define REHAB_TASK_DECISION_TASK_COUNT 5U
#define REHAB_TASK_DECISION_LLM_INPUT_MAX (48U * 1024U)
#define REHAB_TASK_DECISION_LLM_OUTPUT_MAX 8192U
#define REHAB_TASK_DECISION_FORMAL_ROOT_REQUEST_TEXT \
    "The authoritative request-start context contains the complete " \
    "rehab-task-decision Skill and this request's frozen formal input. " \
    "Make the formal business decision, call " \
    "rehab_task_decision_commit, retry only that commit at most once when " \
    "retry_allowed=true, and finish without publishing any external " \
    "message."

typedef struct rehab_task_decision_formal_package
    rehab_task_decision_formal_package_t;

typedef struct {
    char code[48];
    char field[48];
    char detail[128];
    bool retry_allowed;
} rehab_task_decision_tool_error_t;

struct rehab_task_decision_t {
    rehab_car_handle_t car;
    bool fusion_ready;
    SemaphoreHandle_t lock;
    SemaphoreHandle_t car_operation_lock;
    SemaphoreHandle_t worker_done;
    SemaphoreHandle_t selected_task_lock;
    SemaphoreHandle_t decision_watchdog_done;
    TaskHandle_t worker_task;
    TaskHandle_t decision_watchdog_task;
    rehab_car_control_lease_t car_lease;
    rehab_cloud_fusion_result_t *fusion_result;
    uint32_t run_generation;
    bool deleting;
    bool cancel_requested;
    char notification_channel[16];
    char notification_chat_id[96];
    uint32_t decision_trigger_attempt_generation;
    uint32_t decision_watchdog_generation;
    uint32_t decision_context_generation;
    uint32_t decision_context_request_id;
    uint32_t decision_rules_generation;
    uint32_t decision_rules_request_id;
    uint32_t validation_generation;
    uint8_t validation_failure_count;
    uint32_t last_decision_generation;
    char *last_decision_result;
    char *decision_rules_json;
    size_t decision_rules_json_length;
    rehab_environment_scan_handle_t environment_scan;
    claw_core_handle_t coordinator_llm_core;
    bool coordinator_llm_ready;
    uint32_t coordinator_llm_timeout_ms;
    SemaphoreHandle_t coordinator_lock;
    SemaphoreHandle_t coordinator_done;
    TaskHandle_t coordinator_task;
    bool coordinator_console_registered;
    uint32_t coordinator_generation;
    rehab_task_decision_start_source_t coordinator_entry_source;
    rehab_task_decision_formal_package_t *coordinator_formal_package;
    uint32_t coordinator_root_request_id;
    rehab_task_decision_coordinator_status_t coordinator_status;
    rehab_decision_trace_t coordinator_trace;
    rehab_task_decision_training_active_fn training_active;
    void *training_active_ctx;
    rehab_task_decision_status_t status;
};

uint32_t rehab_environment_aggregate_apply(
    rehab_task_decision_status_t *status,
    const rehab_cloud_fusion_result_t *result,
    uint64_t observed_at_ms);

esp_err_t rehab_task_decision_context_render(
    rehab_task_decision_handle_t handle,
    uint32_t request_id,
    char *output,
    size_t output_size);

esp_err_t rehab_task_decision_rules_initialize(
    rehab_task_decision_handle_t handle);
void rehab_task_decision_rules_release(
    rehab_task_decision_handle_t handle);
esp_err_t rehab_task_decision_rules_render(
    rehab_task_decision_handle_t handle,
    uint32_t request_id,
    char *output,
    size_t output_size);
esp_err_t rehab_task_decision_rules_task_metadata(
    rehab_task_decision_handle_t handle,
    const char *task_id,
    char *task_name,
    size_t task_name_size,
    char *rules_version,
    size_t rules_version_size);
esp_err_t rehab_task_decision_rules_recent_score_window(
    rehab_task_decision_handle_t handle,
    size_t *out_window);
esp_err_t rehab_task_decision_rules_hard_gate_eligible(
    rehab_task_decision_handle_t handle,
    const char *task_id,
    const rehab_task_decision_status_t *context_status,
    bool *out_eligible);
size_t rehab_task_decision_rules_task_count(void);
const char *rehab_task_decision_rules_task_id_at(size_t index);

esp_err_t rehab_task_decision_publish_event(
    rehab_task_decision_handle_t handle,
    uint32_t generation,
    const char *channel,
    const char *chat_id);

esp_err_t rehab_task_decision_selected_initialize(
    rehab_task_decision_handle_t handle);
esp_err_t rehab_task_decision_selected_clear_storage(
    rehab_task_decision_handle_t handle);
esp_err_t rehab_task_decision_selected_invalidate(
    rehab_task_decision_handle_t handle);
esp_err_t rehab_task_decision_selected_begin(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_selected_snapshot_t *out_snapshot,
    char out_sha256[REHAB_TASK_DECISION_SCENE_SHA256_MAX]);
esp_err_t rehab_task_decision_selected_commit(
    rehab_task_decision_handle_t handle,
    const cJSON *decision);
esp_err_t rehab_task_decision_selected_commit_v2(
    rehab_task_decision_handle_t handle,
    const cJSON *selected);
esp_err_t rehab_task_decision_selected_render(
    rehab_task_decision_handle_t handle,
    char *output,
    size_t output_size);

cJSON *rehab_task_decision_details_build(
    rehab_task_decision_handle_t handle,
    const char *entry_source,
    const cJSON *decision_input,
    const cJSON *validated_output,
    const cJSON *committed_selected,
    const rehab_task_decision_selected_snapshot_t *readback);
esp_err_t rehab_task_decision_details_write_atomic(
    rehab_task_decision_handle_t handle,
    const cJSON *details,
    const rehab_task_decision_selected_snapshot_t *selected,
    const char **out_stage);

esp_err_t rehab_task_decision_context_record(
    rehab_task_decision_handle_t handle,
    uint32_t generation,
    uint32_t request_id);
esp_err_t rehab_task_decision_watchdog_start(
    rehab_task_decision_handle_t handle,
    uint32_t generation);
esp_err_t rehab_task_decision_watchdog_cancel(
    rehab_task_decision_handle_t handle,
    uint32_t timeout_ms);
esp_err_t rehab_task_decision_fail_to_idle(
    rehab_task_decision_handle_t handle,
    uint32_t generation,
    const char *reason);
esp_err_t rehab_task_decision_send_direct_message(
    const char *channel,
    const char *chat_id,
    const char *message);

bool rehab_task_decision_text_valid(const char *text,
                                    size_t max_bytes,
                                    size_t max_codepoints,
                                    bool allow_empty);

esp_err_t rehab_task_decision_context_add_inputs(
    rehab_task_decision_handle_t handle,
    cJSON *root);
esp_err_t rehab_task_decision_context_ability_level(
    int *out_level, bool *out_parsed);
esp_err_t rehab_task_decision_coordinator_initialize(
    rehab_task_decision_handle_t handle,
    const rehab_task_decision_config_t *config);
/** Raw start is private; production adapters use start_confirmed(). */
esp_err_t rehab_task_decision_coordinator_start(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_start_source_t source,
    uint32_t *out_generation);
esp_err_t rehab_task_decision_coordinator_start_root(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_start_source_t source,
    uint32_t *out_generation);
esp_err_t rehab_task_decision_formal_input_render(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_start_source_t source,
    uint32_t request_id,
    const char *session_id,
    char *output,
    size_t output_size,
    rehab_task_decision_tool_error_t *out_error);
esp_err_t rehab_task_decision_formal_fast_request_validate(
    rehab_task_decision_handle_t handle,
    uint32_t request_id,
    const char *session_id);
esp_err_t rehab_task_decision_formal_fast_input_render(
    rehab_task_decision_handle_t handle,
    uint32_t request_id,
    const char *session_id,
    char *output,
    size_t output_size,
    rehab_task_decision_tool_error_t *out_error);
esp_err_t rehab_task_decision_trace_context_ready(
    rehab_task_decision_handle_t handle,
    uint32_t request_id);
esp_err_t rehab_task_decision_formal_commit(
    rehab_task_decision_handle_t handle,
    uint32_t request_id,
    const char *session_id,
    const char *decision_output_json,
    rehab_task_decision_tool_error_t *out_error);
const char *rehab_task_decision_formal_output_schema_json(void);
void rehab_task_decision_coordinator_release(
    rehab_task_decision_handle_t handle);
esp_err_t rehab_task_decision_coordinator_register_capabilities(
    rehab_task_decision_handle_t handle);
esp_err_t rehab_task_decision_register_coordinator_capabilities(
    rehab_task_decision_handle_t handle);
