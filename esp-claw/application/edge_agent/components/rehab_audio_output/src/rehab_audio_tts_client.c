#include "rehab_audio_output_priv.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_check.h"
#include "esp_http_client.h"
#include "esp_log.h"

#define TTS_RESPONSE_MAX 2048U

static const char *TAG = "rehab_audio_tts";

typedef struct {
    char data[TTS_RESPONSE_MAX];
    size_t length;
    bool overflow;
} tts_http_response_t;

static esp_err_t http_event(esp_http_client_event_t *event)
{
    tts_http_response_t *response = event ? event->user_data : NULL;
    if (!response || event->event_id != HTTP_EVENT_ON_DATA ||
        !event->data || event->data_len <= 0) {
        return ESP_OK;
    }
    size_t available = sizeof(response->data) - response->length - 1U;
    if ((size_t)event->data_len > available) {
        response->overflow = true;
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(response->data + response->length, event->data,
           (size_t)event->data_len);
    response->length += (size_t)event->data_len;
    response->data[response->length] = '\0';
    return ESP_OK;
}

static bool result_url_is_allowed(const char *base_url, const char *audio_url)
{
    size_t base_length = strlen(base_url);
    while (base_length > 0U && base_url[base_length - 1U] == '/') {
        base_length--;
    }
    return base_length > 7U &&
           strncmp(audio_url, base_url, base_length) == 0 &&
           audio_url[base_length] == '/' &&
           strstr(audio_url + base_length,
                  "/api/v1/tts/audio/") == audio_url + base_length;
}

static esp_err_t parse_response(const char *base_url,
                                const char *json,
                                rehab_audio_tts_result_t *out_result)
{
    cJSON *root = cJSON_Parse(json);
    if (!cJSON_IsObject(root)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    cJSON *ok = cJSON_GetObjectItemCaseSensitive(root, "ok");
    cJSON *url = cJSON_GetObjectItemCaseSensitive(root, "audio_url");
    cJSON *mime = cJSON_GetObjectItemCaseSensitive(root, "mime_type");
    const char *url_value = cJSON_GetStringValue(url);
    const char *mime_value = cJSON_GetStringValue(mime);
    bool valid = cJSON_IsTrue(ok) && url_value && mime_value &&
                 strcmp(mime_value, "audio/mpeg") == 0 &&
                 result_url_is_allowed(base_url, url_value) &&
                 strlcpy(out_result->audio_url, url_value,
                         sizeof(out_result->audio_url)) <
                     sizeof(out_result->audio_url) &&
                 strlcpy(out_result->mime_type, mime_value,
                         sizeof(out_result->mime_type)) <
                     sizeof(out_result->mime_type);
    cJSON_Delete(root);
    return valid ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}

esp_err_t rehab_audio_tts_synthesize(const char *base_url,
                                     uint32_t timeout_ms,
                                     const char *text,
                                     const char *request_id,
                                     rehab_audio_tts_result_t *out_result)
{
    char url[REHAB_AUDIO_TTS_URL_LEN + 32U] = {0};
    tts_http_response_t response = {0};
    esp_http_client_handle_t client = NULL;
    cJSON *payload = NULL;
    char *body = NULL;
    esp_err_t ret = ESP_OK;

    ESP_RETURN_ON_FALSE(base_url && text && request_id && out_result,
                        ESP_ERR_INVALID_ARG, TAG, "invalid arguments");
    int written = snprintf(url, sizeof(url), "%s/api/v1/tts/synthesize",
                           base_url);
    ESP_RETURN_ON_FALSE(written > 0 && (size_t)written < sizeof(url),
                        ESP_ERR_INVALID_SIZE, TAG, "TTS URL is too long");

    payload = cJSON_CreateObject();
    if (!payload ||
        !cJSON_AddStringToObject(payload, "request_id", request_id) ||
        !cJSON_AddStringToObject(payload, "text", text) ||
        !cJSON_AddNullToObject(payload, "voice") ||
        !cJSON_AddNullToObject(payload, "rate") ||
        !cJSON_AddNullToObject(payload, "pitch")) {
        cJSON_Delete(payload);
        return ESP_ERR_NO_MEM;
    }
    body = cJSON_PrintUnformatted(payload);
    cJSON_Delete(payload);
    ESP_RETURN_ON_FALSE(body, ESP_ERR_NO_MEM, TAG, "payload allocation failed");

    esp_http_client_config_t config = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .timeout_ms = (int)timeout_ms,
        .event_handler = http_event,
        .user_data = &response,
        .buffer_size = 1024,
        .buffer_size_tx = 1536,
    };
    client = esp_http_client_init(&config);
    if (!client) {
        free(body);
        return ESP_ERR_NO_MEM;
    }

    ESP_GOTO_ON_ERROR(
        esp_http_client_set_header(client, "Content-Type", "application/json"),
        cleanup, TAG, "failed to set content type");
    ESP_GOTO_ON_ERROR(
        esp_http_client_set_post_field(client, body, (int)strlen(body)),
        cleanup, TAG, "failed to set request body");
    ret = esp_http_client_perform(client);
    if (ret != ESP_OK) {
        goto cleanup;
    }
    int status = esp_http_client_get_status_code(client);
    if (status != 200 || response.overflow || response.length == 0U) {
        ret = status >= 500 ? ESP_ERR_NOT_FINISHED : ESP_ERR_INVALID_RESPONSE;
        goto cleanup;
    }
    ret = parse_response(base_url, response.data, out_result);

cleanup:
    esp_http_client_cleanup(client);
    free(body);
    return ret;
}
