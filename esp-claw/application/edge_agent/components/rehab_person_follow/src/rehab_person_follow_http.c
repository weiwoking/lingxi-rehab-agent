// SPDX-License-Identifier: Apache-2.0
#include "rehab_person_follow_priv.h"

#include <limits.h>
#include <string.h>

#include "esp_http_client.h"
#include "esp_timer.h"

static const char IMAGE_HEADER[] =
    "------espclaw-person-follow-v1\r\n"
    "Content-Disposition: form-data; name=\"image\"; filename=\"frame.jpg\"\r\n"
    "Content-Type: image/jpeg\r\n\r\n";
static const char METADATA_HEADER[] =
    "\r\n------espclaw-person-follow-v1\r\n"
    "Content-Disposition: form-data; name=\"metadata\"\r\n"
    "Content-Type: application/json\r\n\r\n";
static const char FOOTER[] =
    "\r\n------espclaw-person-follow-v1--\r\n";
static const char CONTENT_TYPE[] =
    "multipart/form-data; boundary=----espclaw-person-follow-v1";

static bool deadline_expired(int64_t deadline_us)
{
    return esp_timer_get_time() >= deadline_us;
}

static esp_err_t http_write_all(esp_http_client_handle_t client,
                                const void *data, size_t length,
                                int64_t deadline_us)
{
    const uint8_t *cursor = data;
    while (length > 0U) {
        if (deadline_expired(deadline_us)) {
            return ESP_ERR_TIMEOUT;
        }
        size_t chunk = length > PERSON_FOLLOW_HTTP_CHUNK_BYTES
            ? PERSON_FOLLOW_HTTP_CHUNK_BYTES : length;
        int written = esp_http_client_write(client, (const char *)cursor,
                                            (int)chunk);
        if (written <= 0) {
            return ESP_FAIL;
        }
        cursor += written;
        length -= (size_t)written;
    }
    return ESP_OK;
}

esp_err_t rehab_person_follow_multipart_content_length(
    size_t jpeg_length, size_t metadata_length, size_t *out_content_length)
{
    if (jpeg_length == 0U || metadata_length == 0U ||
        out_content_length == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    size_t total = sizeof(IMAGE_HEADER) - 1U;
    if (SIZE_MAX - total < jpeg_length) {
        return ESP_ERR_INVALID_SIZE;
    }
    total += jpeg_length;
    if (SIZE_MAX - total < sizeof(METADATA_HEADER) - 1U) {
        return ESP_ERR_INVALID_SIZE;
    }
    total += sizeof(METADATA_HEADER) - 1U;
    if (SIZE_MAX - total < metadata_length) {
        return ESP_ERR_INVALID_SIZE;
    }
    total += metadata_length;
    if (SIZE_MAX - total < sizeof(FOOTER) - 1U) {
        return ESP_ERR_INVALID_SIZE;
    }
    total += sizeof(FOOTER) - 1U;
    if (total > INT_MAX) {
        return ESP_ERR_INVALID_SIZE;
    }
    *out_content_length = total;
    return ESP_OK;
}

static uint32_t elapsed_ms(int64_t started_us)
{
    int64_t elapsed = esp_timer_get_time() - started_us;
    if (elapsed <= 0) {
        return 0U;
    }
    uint64_t value = (uint64_t)elapsed / 1000U;
    return value > UINT32_MAX ? UINT32_MAX : (uint32_t)value;
}

__attribute__((weak)) esp_err_t rehab_person_follow_http_post(
    const char *url,
    uint32_t timeout_ms,
    const uint8_t *jpeg,
    size_t jpeg_length,
    const char *metadata_json,
    char *response,
    size_t response_capacity,
    size_t *out_response_length,
    int *out_http_status,
    uint32_t *out_elapsed_ms)
{
    if (url == NULL || url[0] == '\0' || timeout_ms == 0U || jpeg == NULL ||
        jpeg_length == 0U || metadata_json == NULL || response == NULL ||
        response_capacity < 2U || out_response_length == NULL ||
        out_http_status == NULL || out_elapsed_ms == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_response_length = 0U;
    *out_http_status = 0;
    *out_elapsed_ms = 0U;
    response[0] = '\0';
    int64_t started_us = esp_timer_get_time();
    int64_t deadline_us = started_us + (int64_t)timeout_ms * 1000LL;
    size_t metadata_length = strlen(metadata_json);
    size_t content_length = 0U;
    esp_err_t result = rehab_person_follow_multipart_content_length(
        jpeg_length, metadata_length, &content_length);
    if (result != ESP_OK) {
        return result;
    }

    esp_http_client_config_t config = {
        .url = url,
        .timeout_ms = (int)timeout_ms,
        .buffer_size = 2048,
        .buffer_size_tx = 2048,
        .keep_alive_enable = false,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == NULL) {
        return ESP_ERR_NO_MEM;
    }
    result = ESP_FAIL;
    esp_http_client_set_method(client, HTTP_METHOD_POST);
    esp_http_client_set_header(client, "Content-Type", CONTENT_TYPE);
    esp_http_client_set_header(client, "Accept", "application/json");
    esp_http_client_set_header(client, "Connection", "close");
    if (deadline_expired(deadline_us)) {
        result = ESP_ERR_TIMEOUT;
        goto done;
    }
    result = esp_http_client_open(client, (int)content_length);
    if (result != ESP_OK) {
        goto done;
    }
    result = http_write_all(client, IMAGE_HEADER,
                            sizeof(IMAGE_HEADER) - 1U, deadline_us);
    if (result == ESP_OK) {
        result = http_write_all(client, jpeg, jpeg_length, deadline_us);
    }
    if (result == ESP_OK) {
        result = http_write_all(client, METADATA_HEADER,
                                sizeof(METADATA_HEADER) - 1U, deadline_us);
    }
    if (result == ESP_OK) {
        result = http_write_all(client, metadata_json, metadata_length,
                                deadline_us);
    }
    if (result == ESP_OK) {
        result = http_write_all(client, FOOTER, sizeof(FOOTER) - 1U,
                                deadline_us);
    }
    if (result != ESP_OK) {
        goto done;
    }

    if (deadline_expired(deadline_us)) {
        result = ESP_ERR_TIMEOUT;
        goto done;
    }
    int64_t declared_length = esp_http_client_fetch_headers(client);
    if (declared_length < -1) {
        result = ESP_FAIL;
        goto done;
    }
    if (declared_length >= (int64_t)response_capacity) {
        result = ESP_ERR_INVALID_SIZE;
        goto done;
    }
    *out_http_status = esp_http_client_get_status_code(client);
    size_t used = 0U;
    while (used + 1U < response_capacity) {
        if (deadline_expired(deadline_us)) {
            result = ESP_ERR_TIMEOUT;
            goto done;
        }
        int received = esp_http_client_read(
            client, response + used, (int)(response_capacity - used - 1U));
        if (received < 0) {
            result = ESP_FAIL;
            goto done;
        }
        if (received == 0) {
            break;
        }
        used += (size_t)received;
    }
    if (used + 1U == response_capacity) {
        char extra = 0;
        int received = esp_http_client_read(client, &extra, 1);
        if (received != 0) {
            result = received < 0 ? ESP_FAIL : ESP_ERR_INVALID_SIZE;
            goto done;
        }
    }
    response[used] = '\0';
    *out_response_length = used;
    result = used > 0U ? ESP_OK : ESP_ERR_INVALID_RESPONSE;

done:
    *out_elapsed_ms = elapsed_ms(started_us);
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return result;
}
