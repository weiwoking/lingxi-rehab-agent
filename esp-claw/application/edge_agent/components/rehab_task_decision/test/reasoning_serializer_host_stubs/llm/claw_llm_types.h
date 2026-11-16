#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "cJSON.h"

typedef int esp_err_t;

#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_NOT_SUPPORTED 0x106

typedef enum {
    CLAW_CORE_LLM_THINKING_NOT_SET = 0,
    CLAW_CORE_LLM_THINKING_ENABLED,
    CLAW_CORE_LLM_THINKING_DISABLED,
} claw_core_llm_thinking_mode_t;

typedef enum {
    CLAW_CORE_LLM_REASONING_EFFORT_NOT_SET = 0,
    CLAW_CORE_LLM_REASONING_EFFORT_HIGH,
    CLAW_CORE_LLM_REASONING_EFFORT_MAX,
} claw_core_llm_reasoning_effort_t;

typedef struct {
    claw_core_llm_thinking_mode_t thinking_mode;
    claw_core_llm_reasoning_effort_t reasoning_effort;
    const char *scope;
} claw_core_llm_request_options_t;

typedef struct {
    const char *chat_path;
    const char *max_tokens_field;
    bool supports_tools;
    bool supports_vision;
    bool image_remote_url_only;
} claw_llm_model_profile_t;

typedef struct {
    const char *system_prompt;
    cJSON *messages;
    const char *tools_json;
    const char *purpose;
    claw_core_llm_request_options_t llm_options;
    volatile bool *abort_flag;
} claw_llm_chat_request_t;
