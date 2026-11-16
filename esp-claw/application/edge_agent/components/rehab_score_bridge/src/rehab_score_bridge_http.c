/* SPDX-License-Identifier: Apache-2.0 */
#include "rehab_score_bridge_priv.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_http_client.h"

typedef struct {
    int status_code;
    char *body;
    size_t body_length;
} http_response_t;

static void set_error(char output[REHAB_SCORE_LAST_ERROR_LEN], const char *value)
{
    if (output) {
        strlcpy(output, value, REHAB_SCORE_LAST_ERROR_LEN);
    }
}

static void normalize_pc_error(char error_code[REHAB_SCORE_LAST_ERROR_LEN])
{
    if (strncmp(error_code, "invalid_task_", 13) == 0 ||
            strcmp(error_code, "invalid_log_seq_range") == 0 ||
            strcmp(error_code, "invalid_csv_file") == 0) {
        set_error(error_code, "invalid_task_contract");
    }
}

static void *large_alloc(size_t size)
{
    void *buffer = heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    return buffer ? buffer : heap_caps_malloc(size, MALLOC_CAP_8BIT);
}

static esp_err_t make_url(char *output,
                          size_t output_size,
                          const char *base_url,
                          const char *suffix)
{
    int written = snprintf(output, output_size, "%s%s", base_url, suffix);
    return written > 0 && (size_t)written < output_size ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

static esp_err_t write_all(esp_http_client_handle_t client,
                           const void *bytes,
                           size_t length)
{
    const uint8_t *cursor = bytes;
    size_t remaining = length;
    while (remaining > 0) {
        size_t next = remaining > REHAB_SCORE_CHUNK_BYTES
                          ? REHAB_SCORE_CHUNK_BYTES
                          : remaining;
        int written = esp_http_client_write(client, (const char *)cursor, (int)next);
        if (written <= 0 || (size_t)written > next) {
            return ESP_FAIL;
        }
        cursor += written;
        remaining -= (size_t)written;
    }
    return ESP_OK;
}

static esp_err_t stream_csv(esp_http_client_handle_t client,
                            const rehab_score_session_t *session)
{
    FILE *file = fopen(session->csv_path, "rb");
    if (!file) {
        return ESP_ERR_NOT_FOUND;
    }
    uint8_t *chunk = large_alloc(REHAB_SCORE_CHUNK_BYTES);
    if (!chunk) {
        fclose(file);
        return ESP_ERR_NO_MEM;
    }

    uint64_t sent = 0;
    esp_err_t err = ESP_OK;
    while (sent < session->csv_size) {
        size_t wanted = (size_t)((session->csv_size - sent) > REHAB_SCORE_CHUNK_BYTES
                                     ? REHAB_SCORE_CHUNK_BYTES
                                     : (session->csv_size - sent));
        size_t got = fread(chunk, 1, wanted, file);
        if (got != wanted) {
            err = ESP_FAIL;
            break;
        }
        err = write_all(client, chunk, got);
        if (err != ESP_OK) {
            break;
        }
        sent += got;
    }
    if (err == ESP_OK && fgetc(file) != EOF) {
        err = ESP_ERR_INVALID_SIZE;
    }
    heap_caps_free(chunk);
    fclose(file);
    return err;
}

static esp_err_t read_response(esp_http_client_handle_t client,
                               size_t maximum,
                               http_response_t *response)
{
    (void)esp_http_client_fetch_headers(client);
    response->status_code = esp_http_client_get_status_code(client);
    response->body = large_alloc(maximum + 1U);
    if (!response->body) {
        return ESP_ERR_NO_MEM;
    }

    while (response->body_length <= maximum) {
        size_t space = maximum - response->body_length;
        if (space == 0) {
            char extra;
            int got = esp_http_client_read(client, &extra, 1);
            if (got > 0) {
                return ESP_ERR_INVALID_SIZE;
            }
            if (got < 0) {
                return ESP_FAIL;
            }
            break;
        }
        int got = esp_http_client_read(client,
                                       response->body + response->body_length,
                                       (int)space);
        if (got < 0) {
            return ESP_FAIL;
        }
        if (got == 0) {
            break;
        }
        response->body_length += (size_t)got;
    }
    response->body[response->body_length] = '\0';
    return ESP_OK;
}

static esp_err_t execute(const char *url,
                         esp_http_client_method_t method,
                         const char *content_type,
                         const void *body,
                         size_t body_length,
                         const rehab_score_session_t *csv_session,
                         size_t response_maximum,
                         http_response_t *response)
{
    if (body_length > INT32_MAX) {
        return ESP_ERR_INVALID_SIZE;
    }
    esp_http_client_config_t config = {
        .url = url,
        .method = method,
        .timeout_ms = 10000,
        .buffer_size = 1024,
        .buffer_size_tx = 1024,
        .keep_alive_enable = true,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        return ESP_ERR_NO_MEM;
    }
    if (content_type) {
        esp_http_client_set_header(client, "Content-Type", content_type);
    }

    esp_err_t err = esp_http_client_open(client, (int)body_length);
    if (err == ESP_OK && body_length > 0) {
        err = csv_session ? stream_csv(client, csv_session)
                          : write_all(client, body, body_length);
    }
    if (err == ESP_OK) {
        err = read_response(client, response_maximum, response);
    }
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    if (err != ESP_OK) {
        free(response->body);
        memset(response, 0, sizeof(*response));
    }
    return err;
}

static esp_err_t map_http_failure(const http_response_t *response,
                                  const char *fallback,
                                  char error_code[REHAB_SCORE_LAST_ERROR_LEN])
{
    if (response->body &&
            rehab_score_contract_parse_error_code(response->body, error_code) == ESP_OK) {
        normalize_pc_error(error_code);
    } else if (response->status_code == 404) {
        set_error(error_code, "job_not_found");
    } else if (response->status_code == 409) {
        set_error(error_code, "session_content_conflict");
    } else if (response->status_code == 503) {
        set_error(error_code, "score_queue_full");
    } else {
        set_error(error_code, fallback);
    }
    return ESP_FAIL;
}

esp_err_t rehab_score_http_check_health(const char *base_url,
                                        char error_code[REHAB_SCORE_LAST_ERROR_LEN])
{
    char url[REHAB_SCORE_URL_LEN];
    esp_err_t err = make_url(url,
                             sizeof(url),
                             base_url,
                             "/api/v1/score/health");
    http_response_t response = {0};
    if (err != ESP_OK) {
        set_error(error_code, "pc_unreachable");
        return err;
    }
    err = execute(url,
                  HTTP_METHOD_GET,
                  NULL,
                  NULL,
                  0,
                  NULL,
                  REHAB_SCORE_RESPONSE_MAX_BYTES,
                  &response);
    if (err != ESP_OK) {
        set_error(error_code,
                  err == ESP_ERR_INVALID_SIZE
                      ? "pc_service_invalid"
                      : "pc_unreachable");
        return err;
    }
    if (response.status_code != 200) {
        free(response.body);
        set_error(error_code, "pc_unreachable");
        return ESP_FAIL;
    }

    bool ready = false;
    err = rehab_score_contract_validate_health(response.body,
                                               response.body_length,
                                               &ready);
    free(response.body);
    if (err != ESP_OK) {
        set_error(error_code, "pc_service_invalid");
        return err;
    }
    if (!ready) {
        set_error(error_code, "pc_service_not_ready");
        return ESP_ERR_INVALID_STATE;
    }
    return ESP_OK;
}

esp_err_t rehab_score_http_create_job(const char *base_url,
                                      const rehab_score_session_t *session,
                                      rehab_score_job_response_t *out,
                                      char error_code[REHAB_SCORE_LAST_ERROR_LEN])
{
    char *task = NULL;
    size_t task_length = 0;
    esp_err_t err = rehab_score_storage_read_task_bytes(session, &task, &task_length);
    if (err != ESP_OK) {
        set_error(error_code, "job_create_failed");
        return err;
    }

    char url[REHAB_SCORE_URL_LEN];
    err = make_url(url, sizeof(url), base_url, "/api/v1/score/jobs");
    http_response_t response = {0};
    esp_http_client_config_t config = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .timeout_ms = 10000,
        .buffer_size = 1024,
        .buffer_size_tx = 1024,
        .keep_alive_enable = true,
    };
    esp_http_client_handle_t client = err == ESP_OK ? esp_http_client_init(&config) : NULL;
    if (!client) {
        free(task);
        set_error(error_code, "pc_unreachable");
        return err == ESP_OK ? ESP_ERR_NO_MEM : err;
    }

    char csv_size[24];
    snprintf(csv_size, sizeof(csv_size), "%" PRIu64, session->csv_size);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_header(client, "X-Session-Id", session->session_id);
    esp_http_client_set_header(client, "X-CSV-Size", csv_size);
    esp_http_client_set_header(client, "X-CSV-SHA256", session->csv_sha256);
    err = esp_http_client_open(client, (int)task_length);
    if (err == ESP_OK) {
        err = write_all(client, task, task_length);
    }
    if (err == ESP_OK) {
        err = read_response(client, REHAB_SCORE_RESPONSE_MAX_BYTES, &response);
    }
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    free(task);
    if (err != ESP_OK) {
        free(response.body);
        set_error(error_code, "pc_unreachable");
        return err;
    }
    if (response.status_code != 200 && response.status_code != 201) {
        err = map_http_failure(&response, "job_create_failed", error_code);
    } else {
        err = rehab_score_contract_parse_job_response(response.body, session->session_id, out);
        if (err != ESP_OK) {
            set_error(error_code, "job_create_failed");
        }
    }
    free(response.body);
    return err;
}

esp_err_t rehab_score_http_upload_csv(const char *base_url,
                                      const rehab_score_session_t *session,
                                      rehab_score_job_response_t *out,
                                      char error_code[REHAB_SCORE_LAST_ERROR_LEN])
{
    char suffix[96];
    snprintf(suffix, sizeof(suffix), "/api/v1/score/jobs/%s/csv", session->session_id);
    char url[REHAB_SCORE_URL_LEN];
    esp_err_t err = make_url(url, sizeof(url), base_url, suffix);
    http_response_t response = {0};
    if (err == ESP_OK) {
        err = execute(url,
                      HTTP_METHOD_PUT,
                      "text/csv",
                      NULL,
                      (size_t)session->csv_size,
                      session,
                      REHAB_SCORE_RESPONSE_MAX_BYTES,
                      &response);
    }
    if (err != ESP_OK) {
        set_error(error_code, "csv_upload_failed");
        return err;
    }
    if (response.status_code != 200 && response.status_code != 202) {
        err = map_http_failure(&response, "csv_upload_failed", error_code);
    } else {
        err = rehab_score_contract_parse_job_status(response.body, session->session_id, out);
        if (err != ESP_OK) {
            set_error(error_code, "csv_upload_failed");
        }
    }
    free(response.body);
    return err;
}

esp_err_t rehab_score_http_get_status(const char *base_url,
                                      const char *job_id,
                                      rehab_score_job_response_t *out,
                                      char error_code[REHAB_SCORE_LAST_ERROR_LEN])
{
    char suffix[96];
    snprintf(suffix, sizeof(suffix), "/api/v1/score/jobs/%s", job_id);
    char url[REHAB_SCORE_URL_LEN];
    esp_err_t err = make_url(url, sizeof(url), base_url, suffix);
    http_response_t response = {0};
    if (err == ESP_OK) {
        err = execute(url, HTTP_METHOD_GET, NULL, NULL, 0, NULL,
                      REHAB_SCORE_RESPONSE_MAX_BYTES, &response);
    }
    if (err != ESP_OK) {
        set_error(error_code, "pc_unreachable");
        return err;
    }
    if (response.status_code != 200) {
        err = map_http_failure(&response, "pc_unreachable", error_code);
    } else {
        err = rehab_score_contract_parse_job_status(response.body, job_id, out);
        if (err != ESP_OK) {
            set_error(error_code, "job_status_failed");
        }
    }
    free(response.body);
    return err;
}

esp_err_t rehab_score_http_get_result(const char *base_url,
                                      const rehab_score_session_t *session,
                                      const char *job_id,
                                      char **out_json,
                                      size_t *out_length,
                                      char error_code[REHAB_SCORE_LAST_ERROR_LEN])
{
    if (!out_json || !out_length) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_json = NULL;
    *out_length = 0;
    char suffix[112];
    snprintf(suffix, sizeof(suffix), "/api/v1/score/jobs/%s/result", job_id);
    char url[REHAB_SCORE_URL_LEN];
    esp_err_t err = make_url(url, sizeof(url), base_url, suffix);
    http_response_t response = {0};
    if (err == ESP_OK) {
        err = execute(url, HTTP_METHOD_GET, NULL, NULL, 0, NULL,
                      REHAB_SCORE_RESULT_MAX_BYTES, &response);
    }
    if (err != ESP_OK) {
        set_error(error_code, "result_download_failed");
        return err;
    }
    if (response.status_code != 200) {
        if (response.status_code == 404) {
            set_error(error_code, "job_not_found");
        } else if (response.status_code == 409) {
            set_error(error_code, "result_not_ready");
        } else {
            set_error(error_code, "result_download_failed");
        }
        err = ESP_FAIL;
    } else {
        err = rehab_score_contract_validate_result(response.body,
                                                   response.body_length,
                                                   session->session_id,
                                                   job_id,
                                                   session->task.task_name);
        if (err != ESP_OK) {
            set_error(error_code, "result_contract_invalid");
        }
    }
    if (err == ESP_OK) {
        *out_json = response.body;
        *out_length = response.body_length;
        return ESP_OK;
    }
    free(response.body);
    return err;
}
