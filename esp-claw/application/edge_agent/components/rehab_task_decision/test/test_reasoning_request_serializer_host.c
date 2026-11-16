#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "llm/backends/claw_llm_openai_compatible_request.h"

static cJSON *make_messages(void)
{
    cJSON *messages = cJSON_CreateArray();
    cJSON *user = cJSON_CreateObject();

    assert(messages != NULL);
    assert(user != NULL);
    assert(cJSON_AddStringToObject(user, "role", "user") != NULL);
    assert(cJSON_AddStringToObject(user, "content", "redacted") != NULL);
    assert(cJSON_AddItemToArray(messages, user));
    return messages;
}

static void assert_serialized(
    claw_core_llm_thinking_mode_t thinking_mode,
    claw_core_llm_reasoning_effort_t reasoning_effort,
    const char *expected)
{
    claw_llm_model_profile_t profile = {
        .max_tokens_field = "max_completion_tokens",
        .supports_tools = true,
    };
    claw_llm_chat_request_t request = {
        .system_prompt = "system-redacted",
        .messages = make_messages(),
        .tools_json =
            "[{\"type\":\"function\",\"function\":{\"name\":\"commit\"}}]",
        .purpose = "agent",
        .llm_options = {
            .thinking_mode = thinking_mode,
            .reasoning_effort = reasoning_effort,
            .scope = "rehab_formal",
        },
    };
    char *body = NULL;
    char *error_message = NULL;
    const esp_err_t err = claw_llm_openai_compatible_build_chat_body(
        "deepseek-v4-pro", 4096U, &profile, &request, &body,
        &error_message);

    assert(err == ESP_OK);
    assert(error_message == NULL);
    assert(body != NULL);
    assert(strcmp(body, expected) == 0);
    cJSON_free(body);
    cJSON_Delete(request.messages);
}

static void assert_invalid_options(void)
{
    claw_llm_model_profile_t profile = {
        .max_tokens_field = "max_tokens",
        .supports_tools = true,
    };
    claw_llm_chat_request_t request = {
        .system_prompt = "system-redacted",
        .messages = make_messages(),
        .llm_options = {
            .thinking_mode = (claw_core_llm_thinking_mode_t)99,
        },
    };
    char *body = NULL;
    char *error_message = NULL;
    esp_err_t err = claw_llm_openai_compatible_build_chat_body(
        "deepseek-v4-pro", 4096U, &profile, &request, &body,
        &error_message);

    assert(err == ESP_ERR_NOT_SUPPORTED);
    assert(body == NULL);
    assert(error_message != NULL);
    free(error_message);
    cJSON_Delete(request.messages);

    request.messages = make_messages();
    request.llm_options.thinking_mode = CLAW_CORE_LLM_THINKING_DISABLED;
    request.llm_options.reasoning_effort =
        CLAW_CORE_LLM_REASONING_EFFORT_HIGH;
    error_message = NULL;
    err = claw_llm_openai_compatible_build_chat_body(
        "deepseek-v4-pro", 4096U, &profile, &request, &body,
        &error_message);
    assert(err == ESP_ERR_INVALID_ARG);
    assert(body == NULL);
    assert(error_message != NULL);
    free(error_message);
    cJSON_Delete(request.messages);
}

int main(void)
{
    assert_serialized(
        CLAW_CORE_LLM_THINKING_ENABLED,
        CLAW_CORE_LLM_REASONING_EFFORT_HIGH,
        "{\"model\":\"deepseek-v4-pro\",\"max_completion_tokens\":4096,"
        "\"thinking\":{\"type\":\"enabled\"},\"reasoning_effort\":\"high\","
        "\"messages\":[{\"role\":\"system\",\"content\":\"system-redacted\"},"
        "{\"role\":\"user\",\"content\":\"redacted\"}],\"tools\":[{\"type\":"
        "\"function\",\"function\":{\"name\":\"commit\"}}]}");
    assert_serialized(
        CLAW_CORE_LLM_THINKING_DISABLED,
        CLAW_CORE_LLM_REASONING_EFFORT_NOT_SET,
        "{\"model\":\"deepseek-v4-pro\",\"max_completion_tokens\":4096,"
        "\"thinking\":{\"type\":\"disabled\"},\"messages\":[{\"role\":"
        "\"system\",\"content\":\"system-redacted\"},{\"role\":\"user\","
        "\"content\":\"redacted\"}],\"tools\":[{\"type\":\"function\","
        "\"function\":{\"name\":\"commit\"}}]}");
    assert_serialized(
        CLAW_CORE_LLM_THINKING_NOT_SET,
        CLAW_CORE_LLM_REASONING_EFFORT_NOT_SET,
        "{\"model\":\"deepseek-v4-pro\",\"max_completion_tokens\":4096,"
        "\"messages\":[{\"role\":\"system\",\"content\":\"system-redacted\"},"
        "{\"role\":\"user\",\"content\":\"redacted\"}],\"tools\":[{\"type\":"
        "\"function\",\"function\":{\"name\":\"commit\"}}]}");
    assert_invalid_options();
    puts("REQUEST_SERIALIZATION_PASS high disabled default invalid");
    return 0;
}
