/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "llm/backends/claw_llm_openai_compatible_request.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>

static char *dup_printf(const char *fmt, ...)
{
    va_list args;
    va_list copy;
    int needed;
    char *buf;

    va_start(args, fmt);
    va_copy(copy, args);
    needed = vsnprintf(NULL, 0, fmt, copy);
    va_end(copy);
    if (needed < 0) {
        va_end(args);
        return NULL;
    }
    buf = calloc(1, (size_t)needed + 1U);
    if (!buf) {
        va_end(args);
        return NULL;
    }
    vsnprintf(buf, (size_t)needed + 1U, fmt, args);
    va_end(args);
    return buf;
}

esp_err_t claw_llm_openai_compatible_reasoning_option_names(
    const claw_core_llm_request_options_t *options,
    const char **out_thinking,
    const char **out_effort,
    char **out_error_message)
{
    const char *thinking = "not_set";
    const char *effort = "not_set";

    if (!options || !out_thinking || !out_effort || !out_error_message) {
        return ESP_ERR_INVALID_ARG;
    }
    switch (options->thinking_mode) {
    case CLAW_CORE_LLM_THINKING_NOT_SET:
        break;
    case CLAW_CORE_LLM_THINKING_ENABLED:
        thinking = "enabled";
        break;
    case CLAW_CORE_LLM_THINKING_DISABLED:
        thinking = "disabled";
        break;
    default:
        *out_error_message = dup_printf("Unsupported thinking mode: %d",
                                        (int)options->thinking_mode);
        return ESP_ERR_NOT_SUPPORTED;
    }
    switch (options->reasoning_effort) {
    case CLAW_CORE_LLM_REASONING_EFFORT_NOT_SET:
        break;
    case CLAW_CORE_LLM_REASONING_EFFORT_HIGH:
        effort = "high";
        break;
    case CLAW_CORE_LLM_REASONING_EFFORT_MAX:
        effort = "max";
        break;
    default:
        *out_error_message = dup_printf("Unsupported reasoning effort: %d",
                                        (int)options->reasoning_effort);
        return ESP_ERR_NOT_SUPPORTED;
    }
    if (options->thinking_mode == CLAW_CORE_LLM_THINKING_DISABLED &&
            options->reasoning_effort !=
                CLAW_CORE_LLM_REASONING_EFFORT_NOT_SET) {
        *out_error_message = dup_printf(
            "Reasoning effort cannot be set when thinking is disabled");
        return ESP_ERR_INVALID_ARG;
    }
    *out_thinking = thinking;
    *out_effort = effort;
    return ESP_OK;
}

static esp_err_t add_reasoning_options(
    cJSON *body,
    const claw_llm_chat_request_t *request,
    char **out_error_message)
{
    const claw_core_llm_request_options_t *options = &request->llm_options;
    const char *thinking = NULL;
    const char *effort = NULL;
    cJSON *thinking_object = NULL;
    esp_err_t err = claw_llm_openai_compatible_reasoning_option_names(
        options, &thinking, &effort, out_error_message);

    if (err != ESP_OK) {
        return err;
    }
    if (options->thinking_mode != CLAW_CORE_LLM_THINKING_NOT_SET) {
        thinking_object = cJSON_AddObjectToObject(body, "thinking");
        if (!thinking_object ||
                !cJSON_AddStringToObject(thinking_object, "type", thinking)) {
            *out_error_message = dup_printf(
                "Out of memory adding thinking request option");
            return ESP_ERR_NO_MEM;
        }
    }
    if (options->reasoning_effort !=
            CLAW_CORE_LLM_REASONING_EFFORT_NOT_SET &&
            !cJSON_AddStringToObject(body, "reasoning_effort", effort)) {
        *out_error_message = dup_printf(
            "Out of memory adding reasoning effort request option");
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t claw_llm_openai_compatible_build_chat_body(
    const char *model,
    uint32_t max_tokens,
    const claw_llm_model_profile_t *profile,
    const claw_llm_chat_request_t *request,
    char **out_post_data,
    char **out_error_message)
{
    cJSON *body = NULL;
    cJSON *messages = NULL;
    cJSON *system_msg = NULL;
    cJSON *item;
    char *post_data = NULL;
    esp_err_t err;

    if (!model || !profile || !request || !request->system_prompt ||
            !request->messages || !out_post_data || !out_error_message) {
        return ESP_ERR_INVALID_ARG;
    }
    body = cJSON_CreateObject();
    messages = cJSON_CreateArray();
    system_msg = cJSON_CreateObject();
    if (!body || !messages || !system_msg) {
        *out_error_message = dup_printf("Out of memory building request");
        cJSON_Delete(body);
        cJSON_Delete(messages);
        cJSON_Delete(system_msg);
        return ESP_ERR_NO_MEM;
    }

    cJSON_AddStringToObject(body, "model", model);
    cJSON_AddNumberToObject(body, profile->max_tokens_field, max_tokens);
    err = add_reasoning_options(body, request, out_error_message);
    if (err != ESP_OK) {
        cJSON_Delete(body);
        cJSON_Delete(messages);
        cJSON_Delete(system_msg);
        return err;
    }

    cJSON_AddStringToObject(system_msg, "role", "system");
    cJSON_AddStringToObject(system_msg, "content", request->system_prompt);
    cJSON_AddItemToArray(messages, system_msg);
    system_msg = NULL;

    cJSON_ArrayForEach(item, request->messages) {
        cJSON *dup = cJSON_Duplicate(item, true);
        if (!dup) {
            *out_error_message = dup_printf("Out of memory copying messages");
            cJSON_Delete(body);
            cJSON_Delete(messages);
            return ESP_ERR_NO_MEM;
        }
        cJSON_AddItemToArray(messages, dup);
    }
    cJSON_AddItemToObject(body, "messages", messages);
    messages = NULL;

    if (request->tools_json && request->tools_json[0]) {
        cJSON *tools;

        if (!profile->supports_tools) {
            cJSON_Delete(body);
            *out_error_message = dup_printf(
                "Selected backend does not support tool calls");
            return ESP_ERR_NOT_SUPPORTED;
        }
        tools = cJSON_Parse(request->tools_json);
        if (!tools || !cJSON_IsArray(tools)) {
            cJSON_Delete(tools);
            cJSON_Delete(body);
            *out_error_message = dup_printf("Invalid tools JSON");
            return ESP_ERR_INVALID_ARG;
        }
        cJSON_AddItemToObject(body, "tools", tools);
    }

    post_data = cJSON_PrintUnformatted(body);
    cJSON_Delete(body);
    if (!post_data) {
        *out_error_message = dup_printf("Out of memory serializing request");
        return ESP_ERR_NO_MEM;
    }
    *out_post_data = post_data;
    return ESP_OK;
}
