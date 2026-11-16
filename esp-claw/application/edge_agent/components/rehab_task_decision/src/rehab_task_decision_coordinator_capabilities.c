// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_priv.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "claw_cap.h"
#include "esp_log.h"
#include "rehab_task_decision_llm_input_adapter.h"
#include "rehab_task_decision_llm_input_artifact.h"
#include "rehab_task_decision_reasoning_artifact.h"

#define COORDINATOR_GROUP_ID "rehab_task_decision_coordinator"
#define FORMAL_FAST_SKILL_OUTPUT_MAX (24U * 1024U)

static const char FORMAL_FAST_CONTEXT_PREFIX[] =
    "<rehab_formal_startup_context authority=\"authoritative\">\n"
    "<workflow_state>Programmatic activation and formal_input_get are "
    "already complete for this LOCAL_UI request. Do not call activate_skill, "
    "rehab_task_decision_formal_input_get, or status. Apply the complete "
    "Skill document to the exact frozen input and call "
    "rehab_task_decision_commit.</workflow_state>\n";
static const char FORMAL_FAST_INPUT_PREFIX[] =
    "\n<formal_input source=\"local_ui\">\n";
static const char FORMAL_FAST_CONTEXT_SUFFIX[] =
    "\n</formal_input>\n</rehab_formal_startup_context>";

static const char *TAG = "rehab_decision_v2_cap";
static rehab_task_decision_handle_t s_coordinator;

static const char *model_input_prepare(const char *formal_input,
                                       char **out_compact)
{
    *out_compact = NULL;
    esp_err_t err = rehab_task_decision_llm_input_compact(
        formal_input, out_compact);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "LLM_COMPACT_INPUT_FAILED err=%s fallback=full",
                 esp_err_to_name(err));
        return formal_input;
    }

    err = rehab_task_decision_llm_input_artifact_save_latest(
        *out_compact, strlen(*out_compact));
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "LLM_COMPACT_INPUT_ARTIFACT_SAVE_FAILED err=%s",
                 esp_err_to_name(err));
    }
    return *out_compact;
}

static esp_err_t formal_fast_context_collect(
    const claw_core_request_t *request,
    claw_core_context_t *out_context,
    void *user_ctx)
{
    (void)user_ctx;
    if (request == NULL || out_context == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_context, 0, sizeof(*out_context));
    if (request->session_id == NULL ||
        strcmp(request->session_id,
               REHAB_TASK_DECISION_FORMAL_UI_SESSION_ID) != 0) {
        return ESP_ERR_NOT_FOUND;
    }
    if (request->flags != 0U || request->user_text == NULL ||
        strcmp(request->user_text,
               REHAB_TASK_DECISION_FORMAL_ROOT_REQUEST_TEXT) != 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_coordinator == NULL || request->request_id == 0U) {
        return ESP_ERR_INVALID_STATE;
    }

    ESP_LOGI(TAG, "FORMAL_FAST_CONTEXT_BEGIN request_id=%" PRIu32
                  " session=%s",
             request->request_id, request->session_id);
    esp_err_t err = rehab_task_decision_formal_fast_request_validate(
        s_coordinator, request->request_id, request->session_id);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "formal fast request rejected request_id=%" PRIu32
                      " err=%s",
                 request->request_id, esp_err_to_name(err));
        return err;
    }

    char *skill = calloc(1U, FORMAL_FAST_SKILL_OUTPUT_MAX);
    char *formal_input = calloc(1U, REHAB_TASK_DECISION_LLM_INPUT_MAX);
    if (skill == NULL || formal_input == NULL) {
        free(skill);
        free(formal_input);
        return ESP_ERR_NO_MEM;
    }
    const claw_cap_call_context_t call_context = {
        .request_id = request->request_id,
        .session_id = request->session_id,
        .channel = request->source_channel,
        .chat_id = request->source_chat_id,
        .target_channel = request->target_channel,
        .target_chat_id = request->target_chat_id,
        .source_cap = request->source_cap,
        .caller = CLAW_CAP_CALLER_SYSTEM,
    };
    err = claw_cap_call("activate_skill",
                        "{\"skill_id\":\"rehab-task-decision\"}",
                        &call_context, skill, FORMAL_FAST_SKILL_OUTPUT_MAX);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "formal fast skill activation failed request_id=%" PRIu32
                      " err=%s",
                 request->request_id, esp_err_to_name(err));
        goto cleanup;
    }
    ESP_LOGI(TAG, "FORMAL_FAST_SKILL_READY request_id=%" PRIu32
                  " bytes=%u",
             request->request_id, (unsigned)strlen(skill));

    rehab_task_decision_tool_error_t tool_error = {0};
    err = rehab_task_decision_formal_fast_input_render(
        s_coordinator, request->request_id, request->session_id,
        formal_input, REHAB_TASK_DECISION_LLM_INPUT_MAX, &tool_error);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "formal fast input failed request_id=%" PRIu32
                      " code=%s err=%s",
                 request->request_id, tool_error.code, esp_err_to_name(err));
        goto cleanup;
    }
    ESP_LOGI(TAG, "FORMAL_FAST_INPUT_READY request_id=%" PRIu32
                  " bytes=%u",
             request->request_id, (unsigned)strlen(formal_input));

    char *compact_input = NULL;
    const char *model_input = model_input_prepare(formal_input,
                                                   &compact_input);

    const size_t context_size = strlen(FORMAL_FAST_CONTEXT_PREFIX) +
        strlen(skill) + strlen(FORMAL_FAST_INPUT_PREFIX) +
        strlen(model_input) + strlen(FORMAL_FAST_CONTEXT_SUFFIX) + 1U;
    char *context = calloc(1U, context_size);
    if (context == NULL) {
        cJSON_free(compact_input);
        err = ESP_ERR_NO_MEM;
        goto cleanup;
    }
    const int written = snprintf(
        context, context_size, "%s%s%s%s%s", FORMAL_FAST_CONTEXT_PREFIX,
        skill, FORMAL_FAST_INPUT_PREFIX, model_input,
        FORMAL_FAST_CONTEXT_SUFFIX);
    cJSON_free(compact_input);
    if (written < 0 || (size_t)written >= context_size) {
        free(context);
        err = ESP_ERR_INVALID_SIZE;
        goto cleanup;
    }
    err = rehab_task_decision_trace_context_ready(
        s_coordinator, request->request_id);
    if (err != ESP_OK) {
        free(context);
        ESP_LOGE(TAG, "formal trace context transition failed request_id=%" PRIu32
                      " err=%s",
                 request->request_id, esp_err_to_name(err));
        goto cleanup;
    }
    out_context->kind = CLAW_CORE_CONTEXT_KIND_SYSTEM_PROMPT;
    out_context->content = context;
    ESP_LOGI(TAG, "FORMAL_FAST_CONTEXT_READY request_id=%" PRIu32
                  " bytes=%u",
             request->request_id, (unsigned)strlen(context));

cleanup:
    free(skill);
    free(formal_input);
    return err;
}

const claw_core_context_provider_t
    rehab_task_decision_formal_fast_context_provider = {
        .name = "Rehab Formal Decision",
        .collect = formal_fast_context_collect,
        .user_ctx = NULL,
        .flags = CLAW_CORE_CONTEXT_PROVIDER_FLAG_REQUEST_START_ONLY,
    };

const claw_core_context_provider_t *
rehab_task_decision_formal_fast_context_provider_get(void)
{
    return &rehab_task_decision_formal_fast_context_provider;
}

static esp_err_t finish(cJSON *root, char *output, size_t output_size,
                        esp_err_t result)
{
    if (root == NULL) {
        return ESP_ERR_NO_MEM;
    }
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (json == NULL) {
        return ESP_ERR_NO_MEM;
    }
    size_t length = strlen(json);
    if (length >= output_size) {
        cJSON_free(json);
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(output, json, length + 1U);
    cJSON_free(json);
    return result;
}

static bool input_is_empty_object(const char *input)
{
    cJSON *root = cJSON_Parse(input != NULL ? input : "{}");
    bool valid = cJSON_IsObject(root) && cJSON_GetArraySize(root) == 0;
    cJSON_Delete(root);
    return valid;
}

static cJSON *status_json(
    const rehab_task_decision_coordinator_status_t *status,
    bool accepted,
    const char *code)
{
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) {
        return NULL;
    }
    cJSON_AddStringToObject(root, "schema_version",
                            "rehab_task_decision_coordinator.status.v1");
    cJSON_AddBoolToObject(root, "accepted", accepted);
    cJSON_AddStringToObject(root, "code", code);
    cJSON_AddStringToObject(
        root, "state",
        rehab_task_decision_coordinator_state_name(status->state));
    cJSON_AddBoolToObject(root, "active", status->active);
    cJSON_AddNumberToObject(root, "generation", status->generation);
    cJSON_AddStringToObject(root, "decision_id", status->decision_id);
    cJSON_AddStringToObject(root, "source_scan_id",
                            status->source_scan_id);
    cJSON_AddStringToObject(root, "source_scene_sha256",
                            status->source_scene_sha256);
    cJSON_AddStringToObject(root, "task_id", status->task_id);
    cJSON_AddStringToObject(root, "task_name", status->task_name);
    cJSON_AddStringToObject(root, "reason", status->reason);
    cJSON_AddNumberToObject(root, "score", status->score);
    cJSON_AddBoolToObject(root, "target_available",
                          status->target_available);
    if (status->target_available) {
        cJSON_AddStringToObject(root, "target_object_id",
                                status->target_object_id);
        cJSON_AddStringToObject(root, "target_class",
                                status->target_class);
        cJSON_AddStringToObject(root, "target_source_frame_id",
                                status->target_source_frame_id);
        cJSON_AddNumberToObject(root, "target_source_frame_sequence",
                                status->target_source_frame_sequence);
    }
    cJSON_AddBoolToObject(root, "retry_used", status->retry_used);
    cJSON_AddStringToObject(root, "last_error", status->last_error);
    cJSON_AddBoolToObject(root, "one_shot_query", true);
    cJSON_AddBoolToObject(root, "should_poll", false);
    cJSON_AddStringToObject(root, "next_action", "reply_immediately");
    return root;
}

static cJSON *tool_error_json(
    const rehab_task_decision_tool_error_t *error)
{
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) {
        return NULL;
    }
    cJSON_AddStringToObject(root, "schema_version",
                            "rehab_task_decision_tool_error.v1");
    cJSON_AddBoolToObject(root, "accepted", false);
    cJSON_AddStringToObject(root, "code", error->code);
    cJSON_AddStringToObject(root, "field", error->field);
    cJSON_AddStringToObject(root, "detail", error->detail);
    cJSON_AddBoolToObject(root, "retry_allowed", error->retry_allowed);
    return root;
}

static bool formal_input_source(const char *input,
                                rehab_task_decision_start_source_t *out_source)
{
    cJSON *root = cJSON_ParseWithOpts(input != NULL ? input : "{}",
                                     NULL, true);
    const cJSON *source = cJSON_IsObject(root)
        ? cJSON_GetObjectItemCaseSensitive(root, "source") : NULL;
    bool valid = cJSON_IsObject(root) && cJSON_GetArraySize(root) == 1 &&
        cJSON_IsString(source) && source->valuestring != NULL;
    if (valid && strcmp(source->valuestring, "wechat") == 0) {
        *out_source = REHAB_TASK_DECISION_START_SOURCE_WECHAT;
    } else if (valid && strcmp(source->valuestring, "local_ui") == 0) {
        *out_source = REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI;
    } else {
        valid = false;
    }
    cJSON_Delete(root);
    return valid;
}

static esp_err_t status_execute(const char *input,
                                const claw_cap_call_context_t *ctx,
                                char *output,
                                size_t output_size)
{
    (void)ctx;
    if (s_coordinator == NULL || !input_is_empty_object(input)) {
        return ESP_ERR_INVALID_ARG;
    }
    rehab_task_decision_coordinator_status_t status = {0};
    esp_err_t err = rehab_task_decision_coordinator_get_status(
        s_coordinator, &status);
    if (err != ESP_OK) {
        return err;
    }
    return finish(status_json(&status, true, "status"),
                  output, output_size, ESP_OK);
}

static esp_err_t formal_input_execute(const char *input,
                                      const claw_cap_call_context_t *ctx,
                                      char *output,
                                      size_t output_size)
{
    rehab_task_decision_start_source_t source =
        REHAB_TASK_DECISION_START_SOURCE_UNKNOWN;
    if (s_coordinator == NULL || ctx == NULL || ctx->request_id == 0U ||
        !formal_input_source(input, &source)) {
        return ESP_ERR_INVALID_ARG;
    }
    rehab_task_decision_tool_error_t error = {0};
    char *formal_input = calloc(1U, REHAB_TASK_DECISION_LLM_INPUT_MAX);
    if (formal_input == NULL) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = rehab_task_decision_formal_input_render(
        s_coordinator, source, ctx->request_id, ctx->session_id,
        formal_input, REHAB_TASK_DECISION_LLM_INPUT_MAX, &error);
    if (err != ESP_OK) {
        free(formal_input);
        return finish(tool_error_json(&error), output, output_size, err);
    }

    char *compact_input = NULL;
    const char *model_input = model_input_prepare(formal_input,
                                                   &compact_input);
    const size_t model_input_length = strlen(model_input);
    if (model_input_length >= output_size) {
        err = ESP_ERR_INVALID_SIZE;
    } else {
        memcpy(output, model_input, model_input_length + 1U);
        err = ESP_OK;
    }
    cJSON_free(compact_input);
    free(formal_input);
    return err;
}

static esp_err_t commit_execute(const char *input,
                                const claw_cap_call_context_t *ctx,
                                char *output,
                                size_t output_size)
{
    if (s_coordinator == NULL || ctx == NULL || ctx->request_id == 0U) {
        return ESP_ERR_INVALID_ARG;
    }
    rehab_task_decision_tool_error_t error = {0};
    esp_err_t err = rehab_task_decision_formal_commit(
        s_coordinator, ctx->request_id, ctx->session_id, input, &error);
    if (err != ESP_OK) {
        return finish(tool_error_json(&error), output, output_size, err);
    }
    rehab_task_decision_coordinator_status_t status = {0};
    err = rehab_task_decision_coordinator_get_status(
        s_coordinator, &status);
    if (err != ESP_OK) {
        return err;
    }
    err = finish(status_json(&status, true, "selected"),
                 output, output_size, ESP_OK);
    if (err != ESP_OK) {
        return err;
    }

    esp_err_t artifact_err =
        rehab_task_decision_reasoning_artifact_save_latest(
            &status, ctx->llm_reasoning_content);
    if (artifact_err != ESP_OK) {
        ESP_LOGW(TAG, "reasoning artifact save failed: %s",
                 esp_err_to_name(artifact_err));
    }
    return ESP_OK;
}

static claw_cap_descriptor_t s_descriptors[] = {
    {
        .id = "rehab_task_decision_formal_input_get",
        .name = "rehab_task_decision_formal_input_get",
        .family = "rehab_task_decision",
        .description =
            "Return rehab_task_decision_llm_input.v1 derived from this Root "
            "request's complete frozen authoritative "
            "rehab_task_decision_input.v4. Machine facts override "
            "memory/history. Call once; do not call again before commit.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM |
                     CLAW_CAP_FLAG_RESTRICTED |
                     CLAW_CAP_FLAG_ROOT_AGENT_ONLY,
        .input_schema_json =
            "{\"type\":\"object\",\"additionalProperties\":false,"
            "\"required\":[\"source\"],\"properties\":{"
            "\"source\":{\"enum\":[\"wechat\",\"local_ui\"]}}}",
        .execute = formal_input_execute,
    },
    {
        .id = "rehab_task_decision_commit",
        .name = "rehab_task_decision_commit",
        .family = "rehab_task_decision",
        .description =
            "Validate and atomically commit one rehab_task_decision_output.v4 "
            "for this request's frozen package. Retry once only when "
            "retry_allowed=true; never reread formal input.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM |
                     CLAW_CAP_FLAG_RESTRICTED |
                     CLAW_CAP_FLAG_ROOT_AGENT_ONLY,
        .input_schema_json = NULL,
        .execute = commit_execute,
    },
    {
        .id = "rehab_task_decision_status",
        .name = "rehab_task_decision_status",
        .family = "rehab_task_decision",
        .description =
            "Read task-decision status once. Use only when the user explicitly "
            "asks for progress, result, or status. Never start a decision and "
            "never call this tool more than once in the same user turn.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM |
                     CLAW_CAP_FLAG_RESTRICTED |
                     CLAW_CAP_FLAG_ROOT_AGENT_ONLY,
        .input_schema_json =
            "{\"type\":\"object\",\"properties\":{},"
            "\"additionalProperties\":false}",
        .execute = status_execute,
    },
};

static const claw_cap_group_t s_group = {
    .group_id = COORDINATOR_GROUP_ID,
    .plugin_name = "Rehab Formal Task Decision",
    .version = "1",
    .descriptors = s_descriptors,
    .descriptor_count = sizeof(s_descriptors) / sizeof(s_descriptors[0]),
};

esp_err_t rehab_task_decision_coordinator_register_capabilities(
    rehab_task_decision_handle_t handle)
{
    if (handle == NULL || s_coordinator != NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    s_descriptors[1].input_schema_json =
        rehab_task_decision_formal_output_schema_json();
    s_coordinator = handle;
    esp_err_t err = claw_cap_register_group(&s_group);
    if (err == ESP_OK) {
        err = claw_cap_add_llm_visible_group(COORDINATOR_GROUP_ID);
    }
    if (err != ESP_OK) {
        s_coordinator = NULL;
        return err;
    }
    ESP_LOGI(TAG, "registered group=%s visibility=llm", COORDINATOR_GROUP_ID);
    return ESP_OK;
}
