#include "rehab_audio_output.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "claw_cap.h"
#include "claw_event_router.h"
#include "esp_log.h"

static const char *TAG = "rehab_audio_cap";
static rehab_audio_output_handle_t s_audio;

static const char *json_string(const cJSON *root, const char *name)
{
    return cJSON_GetStringValue(
        cJSON_GetObjectItemCaseSensitive((cJSON *)root, name));
}

static esp_err_t write_message(char *output,
                               size_t output_size,
                               const char *message)
{
    if (!output || output_size == 0U || !message) {
        return ESP_ERR_INVALID_ARG;
    }
    return strlcpy(output, message, output_size) < output_size
               ? ESP_OK
               : ESP_ERR_INVALID_SIZE;
}

static esp_err_t speak_execute(const char *input,
                               const claw_cap_call_context_t *ctx,
                               char *output,
                               size_t output_size)
{
    cJSON *root = cJSON_Parse(input ? input : "");
    const char *text = cJSON_IsObject(root) ? json_string(root, "text") : NULL;
    const char *request_id =
        cJSON_IsObject(root) ? json_string(root, "request_id") : NULL;
    if ((!request_id || !request_id[0]) && ctx && ctx->correlation_id) {
        request_id = ctx->correlation_id;
    }

    esp_err_t err = text
                        ? rehab_audio_output_speak_text(
                              s_audio, text, request_id, NULL)
                        : ESP_ERR_INVALID_ARG;
    cJSON_Delete(root);

    if (err == ESP_OK) {
        return write_message(output, output_size, "播报已接收");
    }
    if (err == ESP_ERR_INVALID_SIZE) {
        return write_message(output, output_size, "播报失败：正文过长");
    }
    if (err == ESP_ERR_INVALID_ARG) {
        return write_message(output, output_size, "播报失败：正文为空");
    }
    return write_message(output, output_size, "播报失败：TTS服务不可用");
}

static esp_err_t stop_execute(const char *input,
                              const claw_cap_call_context_t *ctx,
                              char *output,
                              size_t output_size)
{
    (void)ctx;
    cJSON *root = cJSON_Parse(input ? input : "");
    bool valid = cJSON_IsObject(root) && cJSON_GetArraySize(root) == 0;
    cJSON_Delete(root);
    if (!valid) {
        return write_message(output, output_size, "停止播报失败");
    }
    esp_err_t err = rehab_audio_output_stop(s_audio, NULL);
    return write_message(output, output_size,
                         err == ESP_OK ? "已停止播报" : "停止播报失败");
}

static esp_err_t status_execute(const char *input,
                                const claw_cap_call_context_t *ctx,
                                char *output,
                                size_t output_size)
{
    (void)ctx;
    cJSON *input_root = cJSON_Parse(input ? input : "");
    bool valid = cJSON_IsObject(input_root) &&
                 cJSON_GetArraySize(input_root) == 0;
    cJSON_Delete(input_root);
    if (!valid) {
        return ESP_ERR_INVALID_ARG;
    }

    rehab_audio_output_status_t status = {0};
    esp_err_t err = rehab_audio_output_get_status(s_audio, &status);
    if (err != ESP_OK) {
        return err;
    }
    cJSON *root = cJSON_CreateObject();
    if (!root ||
        !cJSON_AddStringToObject(
            root, "state", rehab_audio_output_state_name(status.state)) ||
        !cJSON_AddNumberToObject(root, "generation", status.generation) ||
        !cJSON_AddBoolToObject(root, "desired", status.desired) ||
        !cJSON_AddStringToObject(root, "request_id", status.request_id) ||
        !cJSON_AddNumberToObject(root, "last_error", status.last_error) ||
        !cJSON_AddStringToObject(root, "reason", status.last_error_reason)) {
        cJSON_Delete(root);
        return ESP_ERR_NO_MEM;
    }
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!json) {
        return ESP_ERR_NO_MEM;
    }
    err = write_message(output, output_size, json);
    free(json);
    return err;
}

static const claw_cap_descriptor_t s_descriptors[] = {
    {
        .id = "audio_speak_text",
        .name = "audio_speak_text",
        .family = "rehab_audio_output",
        .description =
            "Speak exact text through the single rehabilitation audio output "
            "worker. The text is not rewritten and a newer request wins.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = 0,
        .input_schema_json =
            "{\"type\":\"object\",\"properties\":{\"text\":{\"type\":\"string\"},"
            "\"request_id\":{\"type\":\"string\"}},\"required\":[\"text\"],"
            "\"additionalProperties\":false}",
        .execute = speak_execute,
    },
    {
        .id = "audio_stop",
        .name = "audio_stop",
        .family = "rehab_audio_output",
        .description =
            "Stop only the current audio output and invalidate stale callbacks.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = 0,
        .input_schema_json =
            "{\"type\":\"object\",\"properties\":{},"
            "\"additionalProperties\":false}",
        .execute = stop_execute,
    },
    {
        .id = "audio_status",
        .name = "audio_status",
        .family = "rehab_audio_output",
        .description = "Return the single audio worker state snapshot.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = 0,
        .input_schema_json =
            "{\"type\":\"object\",\"properties\":{},"
            "\"additionalProperties\":false}",
        .execute = status_execute,
    },
};

static const claw_cap_group_t s_group = {
    .group_id = "rehab_audio_output",
    .plugin_name = "Rehab Audio Output",
    .version = "1",
    .descriptors = s_descriptors,
    .descriptor_count = sizeof(s_descriptors) / sizeof(s_descriptors[0]),
};

esp_err_t rehab_audio_output_register_capabilities(
    rehab_audio_output_handle_t handle)
{
    if (!handle || s_audio) {
        return ESP_ERR_INVALID_ARG;
    }
    s_audio = handle;
    esp_err_t err = claw_cap_register_group(&s_group);
    if (err != ESP_OK) {
        s_audio = NULL;
    }
    return err;
}

static esp_err_t provision_rule(const char *rule_json)
{
    esp_err_t ret = claw_event_router_update_rule_json(rule_json);
    if (ret == ESP_ERR_NOT_FOUND) {
        ret = claw_event_router_add_rule_json(rule_json);
    }
    return ret;
}

esp_err_t rehab_audio_output_provision_router_rules(void)
{
    static const char speak_rule[] =
        "{"
        "\"id\":\"p4_rehab_audio_speak_v1\","
        "\"description\":\"WeChat exact-text maximum-volume TTS\","
        "\"enabled\":true,"
        "\"consume_on_match\":true,"
        "\"match\":{"
          "\"event_type\":\"message\","
          "\"event_key\":\"text\","
          "\"source_cap\":\"wechat_gateway\","
          "\"source_channel\":\"wechat\","
          "\"content_type\":\"text\","
          "\"text\":\"最大音量播报：\","
          "\"text_match_rule\":\"prefix\""
        "},"
        "\"actions\":["
          "{"
            "\"type\":\"call_cap\","
            "\"cap\":\"audio_speak_text\","
            "\"caller\":\"system\","
            "\"capture_output\":true,"
            "\"input\":{"
              "\"text\":\"{{match.remainder}}\","
              "\"request_id\":\"{{event.message_id}}\""
            "}"
          "},"
          "{"
            "\"type\":\"send_message\","
            "\"caller\":\"system\","
            "\"capture_output\":false,"
            "\"input\":{"
              "\"channel\":\"{{event.source_channel}}\","
              "\"chat_id\":\"{{event.chat_id}}\","
              "\"message\":\"{{last.output}}\""
            "}"
          "}"
        "]"
        "}";
    static const char stop_rule[] =
        "{"
        "\"id\":\"p4_rehab_audio_stop_v1\","
        "\"description\":\"WeChat exact audio-only stop\","
        "\"enabled\":true,"
        "\"consume_on_match\":true,"
        "\"match\":{"
          "\"event_type\":\"message\","
          "\"event_key\":\"text\","
          "\"source_cap\":\"wechat_gateway\","
          "\"source_channel\":\"wechat\","
          "\"content_type\":\"text\","
          "\"text\":\"停止播报\","
          "\"text_match_rule\":\"exact\""
        "},"
        "\"actions\":["
          "{"
            "\"type\":\"call_cap\","
            "\"cap\":\"audio_stop\","
            "\"caller\":\"system\","
            "\"capture_output\":true,"
            "\"input\":{}"
          "},"
          "{"
            "\"type\":\"send_message\","
            "\"caller\":\"system\","
            "\"capture_output\":false,"
            "\"input\":{"
              "\"channel\":\"{{event.source_channel}}\","
              "\"chat_id\":\"{{event.chat_id}}\","
              "\"message\":\"{{last.output}}\""
            "}"
          "}"
        "]"
        "}";

    esp_err_t err = provision_rule(speak_rule);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "speak rule provision failed: %s", esp_err_to_name(err));
        return err;
    }
    err = provision_rule(stop_rule);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "stop rule provision failed: %s", esp_err_to_name(err));
        return err;
    }
    ESP_LOGI(TAG, "audio router rules provisioned idempotently");
    return ESP_OK;
}
