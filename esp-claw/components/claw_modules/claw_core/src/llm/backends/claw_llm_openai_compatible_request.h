/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#pragma once

#include "llm/claw_llm_types.h"

esp_err_t claw_llm_openai_compatible_reasoning_option_names(
    const claw_core_llm_request_options_t *options,
    const char **out_thinking,
    const char **out_effort,
    char **out_error_message);

esp_err_t claw_llm_openai_compatible_build_chat_body(
    const char *model,
    uint32_t max_tokens,
    const claw_llm_model_profile_t *profile,
    const claw_llm_chat_request_t *request,
    char **out_post_data,
    char **out_error_message);
