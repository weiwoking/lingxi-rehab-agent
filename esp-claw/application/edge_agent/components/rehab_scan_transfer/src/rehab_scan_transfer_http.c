// SPDX-License-Identifier: Apache-2.0
#include "rehab_scan_transfer_priv.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "mbedtls/sha256.h"

static void *large_alloc(size_t bytes)
{
    void *memory = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    return memory ? memory : heap_caps_malloc(bytes, MALLOC_CAP_8BIT);
}

typedef struct {
    char content_type[64];
} response_headers_t;

static esp_err_t response_event(esp_http_client_event_t *event)
{
    response_headers_t *headers = event != NULL ? event->user_data : NULL;
    if (headers != NULL && event->event_id == HTTP_EVENT_ON_HEADER &&
        event->header_key != NULL && event->header_value != NULL &&
        strcasecmp(event->header_key, "Content-Type") == 0) {
        strlcpy(headers->content_type, event->header_value,
                sizeof(headers->content_type));
    }
    return ESP_OK;
}

static esp_err_t make_url(rehab_scan_transfer_handle_t handle,
                          const char *suffix, char *out, size_t out_size)
{
    size_t base_length = strlen(handle->server_base_url);
    while (base_length > 0 && handle->server_base_url[base_length - 1] == '/') {
        --base_length;
    }
    int count = snprintf(out, out_size, "%.*s%s", (int)base_length,
                         handle->server_base_url, suffix);
    return count > 0 && (size_t)count < out_size ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

static esp_http_client_handle_t make_client(rehab_scan_transfer_handle_t handle,
                                            const char *url,
                                            esp_http_client_method_t method,
                                            uint32_t timeout_ms,
                                            response_headers_t *response_headers)
{
    esp_http_client_config_t config = {
        .url = url,
        .method = method,
        .timeout_ms = (int)timeout_ms,
        .buffer_size = 2048,
        .buffer_size_tx = 2048,
        .keep_alive_enable = false,
        .event_handler = response_headers != NULL ? response_event : NULL,
        .user_data = response_headers,
    };
    return esp_http_client_init(&config);
}

static esp_err_t read_bounded_response(esp_http_client_handle_t client,
                                       size_t maximum,
                                       rehab_scan_transfer_http_response_t *out)
{
    int64_t declared = esp_http_client_fetch_headers(client);
    if (declared < -1 || declared > (int64_t)maximum) return ESP_ERR_INVALID_SIZE;
    out->http_status = esp_http_client_get_status_code(client);
    out->body = large_alloc(maximum + 1U);
    if (out->body == NULL) return ESP_ERR_NO_MEM;
    while (out->body_length < maximum) {
        int got = esp_http_client_read(client, out->body + out->body_length,
                                       (int)(maximum - out->body_length));
        if (got < 0) return ESP_FAIL;
        if (got == 0) break;
        out->body_length += (size_t)got;
    }
    if (out->body_length == maximum) {
        char extra;
        int got = esp_http_client_read(client, &extra, 1);
        if (got != 0) return got < 0 ? ESP_FAIL : ESP_ERR_INVALID_SIZE;
    }
    if (declared >= 0 && (uint64_t)declared != out->body_length) return ESP_ERR_INVALID_SIZE;
    out->body[out->body_length] = '\0';
    return out->body_length > 0 ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}

void rehab_scan_transfer_http_response_free(
    rehab_scan_transfer_http_response_t *response)
{
    if (response != NULL) {
        heap_caps_free(response->body);
        memset(response, 0, sizeof(*response));
    }
}

esp_err_t rehab_scan_transfer_http_upload(
    rehab_scan_transfer_handle_t handle,
    const rehab_scan_transfer_plan_t *plan, const char *boundary,
    size_t content_length, uint8_t *stream_buffer,
    rehab_scan_transfer_http_response_t *out_response)
{
    if (handle == NULL || plan == NULL || boundary == NULL ||
        stream_buffer == NULL || out_response == NULL || content_length > INT_MAX) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_response, 0, sizeof(*out_response));
    char url[256];
    const char *suffix =
        handle->mode == REHAB_SCAN_TRANSFER_MODE_UPLOAD_ONLY
            ? "/api/v1/scan/jobs?mode=upload_only"
            : "/api/v1/scan/jobs";
    esp_err_t err = make_url(handle, suffix, url, sizeof(url));
    esp_http_client_handle_t client = err == ESP_OK ?
        make_client(handle, url, HTTP_METHOD_POST, handle->config.upload_timeout_ms, NULL) : NULL;
    if (client == NULL) return err == ESP_OK ? ESP_ERR_NO_MEM : err;
    char content_type[REHAB_SCAN_TRANSFER_BOUNDARY_MAX + 32];
    int count = snprintf(content_type, sizeof(content_type),
                         "multipart/form-data; boundary=%s", boundary);
    if (count <= 0 || (size_t)count >= sizeof(content_type)) {
        err = ESP_ERR_INVALID_SIZE;
        goto cleanup;
    }
    esp_http_client_set_header(client, "Content-Type", content_type);
    esp_http_client_set_header(client, "Accept", "application/json");
    esp_http_client_set_header(client, "Connection", "close");
    err = esp_http_client_open(client, (int)content_length);
    if (err == ESP_OK) {
        err = rehab_scan_transfer_write_multipart(
            handle, client, plan, boundary, stream_buffer,
            handle->config.stream_buffer_bytes, content_length);
    }
    if (err == ESP_OK) {
        err = read_bounded_response(client, REHAB_SCAN_TRANSFER_RESPONSE_MAX,
                                    out_response);
        rehab_scan_transfer_set_http(handle, out_response->http_status);
    }
cleanup:
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    if (err != ESP_OK) rehab_scan_transfer_http_response_free(out_response);
    return err;
}

esp_err_t rehab_scan_transfer_http_get_json(
    rehab_scan_transfer_handle_t handle, const char *suffix,
    rehab_scan_transfer_http_response_t *out_response)
{
    if (handle == NULL || suffix == NULL || out_response == NULL) return ESP_ERR_INVALID_ARG;
    memset(out_response, 0, sizeof(*out_response));
    char url[320];
    esp_err_t err = make_url(handle, suffix, url, sizeof(url));
    esp_http_client_handle_t client = err == ESP_OK ?
        make_client(handle, url, HTTP_METHOD_GET, handle->config.request_timeout_ms, NULL) : NULL;
    if (client == NULL) return err == ESP_OK ? ESP_ERR_NO_MEM : err;
    esp_http_client_set_header(client, "Accept", "application/json");
    esp_http_client_set_header(client, "Connection", "close");
    err = esp_http_client_open(client, 0);
    if (err == ESP_OK) {
        err = read_bounded_response(client, REHAB_SCAN_TRANSFER_RESPONSE_MAX,
                                    out_response);
        rehab_scan_transfer_set_http(handle, out_response->http_status);
    }
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    if (err != ESP_OK) rehab_scan_transfer_http_response_free(out_response);
    return err;
}

esp_err_t rehab_scan_transfer_http_download_result(
    rehab_scan_transfer_handle_t handle, const char *suffix,
    const char *temporary_path, uint8_t *stream_buffer,
    size_t *out_length, int *out_http_status, int *out_errno)
{
    if (handle == NULL || suffix == NULL || temporary_path == NULL ||
        stream_buffer == NULL || out_length == NULL || out_http_status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_length = 0;
    *out_http_status = 0;
    int saved_errno = 0;
    char url[320];
    esp_err_t err = make_url(handle, suffix, url, sizeof(url));
    response_headers_t response_headers = {0};
    esp_http_client_handle_t client = err == ESP_OK ?
        make_client(handle, url, HTTP_METHOD_GET, handle->config.request_timeout_ms,
                    &response_headers) : NULL;
    if (client == NULL) return err == ESP_OK ? ESP_ERR_NO_MEM : err;
    FILE *file = NULL;
    esp_http_client_set_header(client, "Accept", "application/json");
    esp_http_client_set_header(client, "Connection", "close");
    err = esp_http_client_open(client, 0);
    int64_t declared = -1;
    if (err == ESP_OK) {
        declared = esp_http_client_fetch_headers(client);
        if (declared < -1 || declared == 0 ||
            declared > (int64_t)handle->config.max_result_bytes) err = ESP_ERR_INVALID_SIZE;
    }
    if (err == ESP_OK) {
        *out_http_status = esp_http_client_get_status_code(client);
        rehab_scan_transfer_set_http(handle, *out_http_status);
        if (*out_http_status != 200) err = ESP_FAIL;
    }
    if (err == ESP_OK && strncasecmp(response_headers.content_type,
                                     "application/json", 16) != 0) {
        err = ESP_ERR_INVALID_RESPONSE;
    }
    int fd = -1;
    if (err == ESP_OK) {
        fd = open(temporary_path, O_WRONLY | O_CREAT | O_EXCL, 0666);
        if (fd < 0) {
            saved_errno = errno;
            err = ESP_FAIL;
        }
    }
    if (err == ESP_OK) {
        file = fdopen(fd, "wb");
        if (file == NULL) {
            saved_errno = errno;
            close(fd);
            fd = -1;
            err = ESP_FAIL;
        }
    }
    while (err == ESP_OK) {
        if (rehab_scan_transfer_abort_requested(handle)) {
            err = ESP_ERR_INVALID_STATE;
            break;
        }
        int got = esp_http_client_read(client, (char *)stream_buffer,
                                       (int)handle->config.stream_buffer_bytes);
        if (got < 0) {
            err = ESP_FAIL;
            break;
        }
        if (got == 0) break;
        if ((size_t)got > handle->config.max_result_bytes - *out_length) {
            err = ESP_ERR_INVALID_SIZE;
            break;
        }
        size_t written = fwrite(stream_buffer, 1, (size_t)got, file);
        if (written != (size_t)got) {
            saved_errno = errno != 0 ? errno : EIO;
            err = ESP_FAIL;
            break;
        }
        *out_length += (size_t)got;
    }
    if (err == ESP_OK && (*out_length == 0 ||
        (declared >= 0 && (uint64_t)declared != *out_length))) err = ESP_ERR_INVALID_SIZE;
    if (file != NULL) {
        if (err == ESP_OK && (fflush(file) != 0 || fsync(fileno(file)) != 0)) {
            saved_errno = errno;
            err = ESP_FAIL;
        }
        if (fclose(file) != 0 && err == ESP_OK) {
            saved_errno = errno;
            err = ESP_FAIL;
        }
    }
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    if (err != ESP_OK) (void)unlink(temporary_path);
    if (out_errno != NULL) *out_errno = saved_errno;
    return err;
}

esp_err_t rehab_scan_transfer_http_download_decision(
    rehab_scan_transfer_handle_t handle, const char *suffix,
    const char *temporary_path, uint8_t *stream_buffer,
    size_t expected_length, const char *expected_sha256,
    size_t *out_length, char out_sha256[REHAB_SCAN_TRANSFER_SHA256_HEX_MAX],
    int *out_http_status, int *out_errno)
{
    if (handle == NULL || suffix == NULL || temporary_path == NULL ||
        stream_buffer == NULL || expected_length == 0 ||
        expected_length > handle->config.max_decision_bytes ||
        expected_sha256 == NULL ||
        strlen(expected_sha256) != REHAB_SCAN_TRANSFER_SHA256_HEX_MAX - 1U ||
        out_length == NULL || out_sha256 == NULL ||
        out_http_status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_length = 0;
    *out_http_status = 0;
    out_sha256[0] = '\0';
    int saved_errno = 0;
    char url[320];
    esp_err_t err = make_url(handle, suffix, url, sizeof(url));
    response_headers_t headers = {0};
    esp_http_client_handle_t client = err == ESP_OK ?
        make_client(handle, url, HTTP_METHOD_GET,
                    handle->config.request_timeout_ms, &headers) : NULL;
    if (client == NULL) return err == ESP_OK ? ESP_ERR_NO_MEM : err;

    FILE *file = NULL;
    mbedtls_sha256_context sha;
    mbedtls_sha256_init(&sha);
    esp_http_client_set_header(client, "Accept", "application/json");
    esp_http_client_set_header(client, "Connection", "close");
    err = esp_http_client_open(client, 0);
    int64_t declared = -1;
    if (err == ESP_OK) {
        declared = esp_http_client_fetch_headers(client);
        if (declared < -1 || declared == 0 ||
            declared > (int64_t)handle->config.max_decision_bytes ||
            (declared >= 0 && (size_t)declared != expected_length)) {
            err = ESP_ERR_INVALID_SIZE;
        }
    }
    if (err == ESP_OK) {
        *out_http_status = esp_http_client_get_status_code(client);
        rehab_scan_transfer_set_http(handle, *out_http_status);
        if (*out_http_status != 200) err = ESP_FAIL;
    }
    if (err == ESP_OK &&
        strncasecmp(headers.content_type, "application/json", 16) != 0) {
        err = ESP_ERR_INVALID_RESPONSE;
    }
    int fd = -1;
    if (err == ESP_OK) {
        fd = open(temporary_path, O_WRONLY | O_CREAT | O_EXCL, 0666);
        if (fd < 0) {
            saved_errno = errno;
            err = ESP_FAIL;
        }
    }
    if (err == ESP_OK) {
        file = fdopen(fd, "wb");
        if (file == NULL) {
            saved_errno = errno;
            close(fd);
            err = ESP_FAIL;
        }
    }
    if (err == ESP_OK && mbedtls_sha256_starts(&sha, 0) != 0) {
        err = ESP_FAIL;
    }
    while (err == ESP_OK) {
        if (rehab_scan_transfer_abort_requested(handle)) {
            err = ESP_ERR_INVALID_STATE;
            break;
        }
        int got = esp_http_client_read(
            client, (char *)stream_buffer,
            (int)handle->config.stream_buffer_bytes);
        if (got < 0) {
            err = ESP_FAIL;
            break;
        }
        if (got == 0) break;
        if ((size_t)got > handle->config.max_decision_bytes - *out_length) {
            err = ESP_ERR_INVALID_SIZE;
            break;
        }
        if (fwrite(stream_buffer, 1, (size_t)got, file) != (size_t)got) {
            saved_errno = errno != 0 ? errno : EIO;
            err = ESP_FAIL;
            break;
        }
        if (mbedtls_sha256_update(&sha, stream_buffer, (size_t)got) != 0) {
            err = ESP_FAIL;
            break;
        }
        *out_length += (size_t)got;
    }
    unsigned char digest[32];
    if (err == ESP_OK && mbedtls_sha256_finish(&sha, digest) != 0) {
        err = ESP_FAIL;
    }
    if (err == ESP_OK && (*out_length != expected_length ||
        (declared >= 0 && (size_t)declared != *out_length))) {
        err = ESP_ERR_INVALID_SIZE;
    }
    if (err == ESP_OK) {
        for (size_t index = 0; index < sizeof(digest); ++index) {
            snprintf(out_sha256 + index * 2U, 3U, "%02x", digest[index]);
        }
        if (strcmp(out_sha256, expected_sha256) != 0) {
            err = ESP_ERR_INVALID_CRC;
        }
    }
    if (file != NULL) {
        if (err == ESP_OK &&
            (fflush(file) != 0 || fsync(fileno(file)) != 0)) {
            saved_errno = errno;
            err = ESP_FAIL;
        }
        if (fclose(file) != 0 && err == ESP_OK) {
            saved_errno = errno;
            err = ESP_FAIL;
        }
    }
    mbedtls_sha256_free(&sha);
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    if (err != ESP_OK) (void)unlink(temporary_path);
    if (out_errno != NULL) *out_errno = saved_errno;
    return err;
}
