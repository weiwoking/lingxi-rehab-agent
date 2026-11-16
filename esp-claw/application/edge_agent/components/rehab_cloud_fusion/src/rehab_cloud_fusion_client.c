// SPDX-License-Identifier: Apache-2.0
#include "rehab_cloud_fusion_priv.h"

#include <inttypes.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>

#include "cap_time.h"
#include "app_config.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "wifi_manager.h"

#define CLOUD_CAPTURE_TIMEOUT_MS 6000U
#define CLOUD_DEFAULT_TIMEOUT_MS 30000U
#define CLOUD_MIN_EPOCH_US       INT64_C(1704067200000000)

static const char *TAG = "cloud_fusion";
static SemaphoreHandle_t s_upload_lock;
static StaticSemaphore_t s_upload_lock_storage;

static void cloud_extract_endpoint_host(const char *url, char *host,
                                        size_t host_size)
{
    const char *start = strstr(url, "://");
    start = start == NULL ? url : start + 3;
    const char *end = strchr(start, '/');
    size_t length = end == NULL ? strlen(start) : (size_t)(end - start);
    if (length >= host_size) {
        length = host_size - 1U;
    }
    memcpy(host, start, length);
    host[length] = '\0';
}

static void cloud_load_endpoint(rehab_cloud_fusion_result_t *result)
{
    strlcpy(result->endpoint_url, REHAB_CLOUD_FUSION_DEFAULT_URL,
            sizeof(result->endpoint_url));
    app_config_t *app_config = calloc(1, sizeof(*app_config));
    if (app_config != NULL) {
        if (app_config_load(app_config) == ESP_OK &&
            app_config->perception_fusion_url[0] != '\0') {
            strlcpy(result->endpoint_url, app_config->perception_fusion_url,
                    sizeof(result->endpoint_url));
        }
        free(app_config);
    }
    cloud_extract_endpoint_host(result->endpoint_url, result->endpoint_host,
                                sizeof(result->endpoint_host));
}

typedef struct {
    esp_http_client_handle_t client;
    rehab_cloud_write_stats_t *stats;
    size_t expected_image_length;
    bool image_seen;
    int64_t write_return_sum;
} cloud_writer_t;

static uint64_t elapsed_u64(int64_t start_us)
{
    int64_t elapsed = esp_timer_get_time() - start_us;
    return elapsed <= 0 ? 0U : (uint64_t)elapsed;
}

static uint32_t elapsed_u32(int64_t start_us)
{
    int64_t elapsed = esp_timer_get_time() - start_us;
    return elapsed <= 0 ? 0U : elapsed > UINT32_MAX ? UINT32_MAX
                                                    : (uint32_t)elapsed;
}

static bool deadline_expired(int64_t deadline_us)
{
    return esp_timer_get_time() >= deadline_us;
}

static esp_err_t cloud_http_write(void *ctx, const void *data, size_t length)
{
    cloud_writer_t *writer = ctx;
    rehab_cloud_write_stats_t *stats = writer->stats;
    const uint8_t *cursor = data;
    bool is_image = !writer->image_seen &&
        length == writer->expected_image_length &&
        writer->expected_image_length == REHAB_CAPTURE_IMAGE_BYTES;
    if (is_image) {
        writer->image_seen = true;
    }
    stats->writer_segment_count++;

    size_t segment_remaining = length;
    while (segment_remaining > 0) {
        size_t chunk_length = segment_remaining > REHAB_CLOUD_UPLOAD_CHUNK_BYTES
            ? REHAB_CLOUD_UPLOAD_CHUNK_BYTES : segment_remaining;
        stats->bytes_requested_total += chunk_length;
        size_t chunk_remaining = chunk_length;
        while (chunk_remaining > 0) {
            int64_t write_started = esp_timer_get_time();
            int written = esp_http_client_write(writer->client,
                                                 (const char *)cursor,
                                                 (int)chunk_remaining);
            uint64_t block_us = elapsed_u64(write_started);
            stats->http_write_call_count++;
            if (is_image) {
                stats->image_segment_write_calls++;
            } else {
                stats->header_footer_write_calls++;
            }
            if (stats->http_write_call_count == 1U) {
                stats->write_return_min = written;
                stats->write_return_max = written;
            } else {
                if (written < stats->write_return_min) {
                    stats->write_return_min = written;
                }
                if (written > stats->write_return_max) {
                    stats->write_return_max = written;
                }
            }
            if (written > 0 && writer->write_return_sum <= INT64_MAX - written) {
                writer->write_return_sum += written;
            } else if (written < 0 &&
                       writer->write_return_sum >= INT64_MIN - (int64_t)written) {
                writer->write_return_sum += written;
            }
            stats->write_return_average = writer->write_return_sum /
                (int64_t)stats->http_write_call_count;
            if (block_us > stats->max_single_write_block_us) {
                stats->max_single_write_block_us = block_us;
            }
            if (UINT64_MAX - stats->total_write_block_us < block_us) {
                stats->total_write_block_us = UINT64_MAX;
            } else {
                stats->total_write_block_us += block_us;
            }
            if (block_us < 1000U) {
                stats->write_lt_1ms_count++;
            } else if (block_us < 5000U) {
                stats->write_1_to_5ms_count++;
            } else if (block_us < 20000U) {
                stats->write_5_to_20ms_count++;
            } else if (block_us <= 100000U) {
                stats->write_20_to_100ms_count++;
            } else {
                stats->write_gt_100ms_count++;
            }
            if (written == 0) {
                stats->zero_write_count++;
                return ESP_FAIL;
            }
            if (written < 0) {
                stats->negative_write_count++;
                return ESP_FAIL;
            }
            if ((size_t)written < chunk_remaining) {
                stats->http_write_partial_count++;
            }
            cursor += written;
            chunk_remaining -= (size_t)written;
            stats->bytes_written_total += (size_t)written;
        }
        segment_remaining -= chunk_length;
    }
    return ESP_OK;
}

static char *cloud_response_alloc(size_t capacity)
{
    return heap_caps_calloc(1, capacity + 1U,
                            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
}

static esp_err_t cloud_read_response(esp_http_client_handle_t client,
                                     int64_t declared_length,
                                     int64_t deadline_us,
                                     char **out_body,
                                     size_t *out_length)
{
    if (declared_length > (int64_t)REHAB_CLOUD_RESPONSE_MAX) {
        return ESP_ERR_INVALID_SIZE;
    }
    size_t capacity = REHAB_CLOUD_RESPONSE_INITIAL;
    if (declared_length > 0 && (size_t)declared_length > capacity) {
        capacity = (size_t)declared_length;
    }
    char *body = cloud_response_alloc(capacity);
    if (body == NULL) {
        return ESP_ERR_NO_MEM;
    }
    size_t used = 0;
    for (;;) {
        if (deadline_expired(deadline_us)) {
            heap_caps_free(body);
            return ESP_ERR_TIMEOUT;
        }
        if (used == capacity) {
            if (capacity == REHAB_CLOUD_RESPONSE_MAX) {
                heap_caps_free(body);
                return ESP_ERR_INVALID_SIZE;
            }
            size_t next = capacity * 2U;
            if (next > REHAB_CLOUD_RESPONSE_MAX) {
                next = REHAB_CLOUD_RESPONSE_MAX;
            }
            char *grown = heap_caps_realloc(
                body, next + 1U, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
            if (grown == NULL) {
                heap_caps_free(body);
                return ESP_ERR_NO_MEM;
            }
            body = grown;
            capacity = next;
        }
        int received = esp_http_client_read(client, body + used,
                                            (int)(capacity - used));
        if (received < 0) {
            heap_caps_free(body);
            return ESP_FAIL;
        }
        if (received == 0) {
            break;
        }
        used += (size_t)received;
        if (used > REHAB_CLOUD_RESPONSE_MAX) {
            heap_caps_free(body);
            return ESP_ERR_INVALID_SIZE;
        }
    }
    body[used] = '\0';
    *out_body = body;
    *out_length = used;
    return ESP_OK;
}

static esp_err_t cloud_set_epoch_timestamps(rehab_capture_packet_t *packet)
{
    struct timeval wall = {0};
    int64_t monotonic_now_us = esp_timer_get_time();
    if (gettimeofday(&wall, NULL) != 0 || !cap_time_is_valid()) {
        return ESP_ERR_INVALID_STATE;
    }
    int64_t wall_now_us = (int64_t)wall.tv_sec * INT64_C(1000000) + wall.tv_usec;
    if (wall_now_us < CLOUD_MIN_EPOCH_US || monotonic_now_us <= 0) {
        return ESP_ERR_INVALID_STATE;
    }
    int64_t epoch_offset_us = wall_now_us - monotonic_now_us;
    return rehab_capture_packet_set_epoch_offset(packet, epoch_offset_us);
}

esp_err_t rehab_cloud_fusion_init(void)
{
    if (s_upload_lock != NULL) {
        return ESP_OK;
    }
    s_upload_lock = xSemaphoreCreateMutexStatic(&s_upload_lock_storage);
    if (s_upload_lock == NULL) {
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "PC_LOCAL_FUSION_READY default_url=%s response_max=%u",
             REHAB_CLOUD_FUSION_DEFAULT_URL, REHAB_CLOUD_RESPONSE_MAX);
    return ESP_OK;
}

esp_err_t rehab_cloud_fusion_upload_once(
    rehab_cloud_fusion_result_t *result, uint32_t timeout_ms)
{
    if (result == NULL || s_upload_lock == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(result, 0, sizeof(*result));
    cloud_load_endpoint(result);
    result->write_stats.application_chunk_size =
        REHAB_CLOUD_UPLOAD_CHUNK_BYTES;
    result->write_stats.tcp_mss_config = CONFIG_LWIP_TCP_MSS;
    result->write_stats.tcp_snd_buf_config =
        CONFIG_LWIP_TCP_SND_BUF_DEFAULT;
    strlcpy(result->last_error, "unknown", sizeof(result->last_error));
    if (xSemaphoreTake(s_upload_lock, 0) != pdTRUE) {
        strlcpy(result->last_error, "capture_busy", sizeof(result->last_error));
        return ESP_ERR_NOT_FINISHED;
    }

    esp_err_t err = ESP_FAIL;
    rehab_capture_packet_t packet = {0};
    esp_http_client_handle_t client = NULL;
    char *response = NULL;
    cloud_writer_t writer = {
        .stats = &result->write_stats,
    };
    int64_t total_started = esp_timer_get_time();
    if (timeout_ms == 0) {
        timeout_ms = CLOUD_DEFAULT_TIMEOUT_MS;
    }
    int64_t deadline_us = total_started + (int64_t)timeout_ms * 1000LL;

    wifi_manager_status_t wifi = {0};
    wifi_manager_get_status(&wifi);
    if (!wifi.sta_connected) {
        strlcpy(result->last_error, "wifi_not_connected",
                sizeof(result->last_error));
        err = ESP_ERR_INVALID_STATE;
        goto done;
    }
    if (!cap_time_is_valid()) {
        strlcpy(result->last_error, "time_not_synchronized",
                sizeof(result->last_error));
        err = ESP_ERR_INVALID_STATE;
        goto done;
    }

    int64_t capture_started = esp_timer_get_time();
    uint32_t capture_timeout = timeout_ms < CLOUD_CAPTURE_TIMEOUT_MS
        ? timeout_ms : CLOUD_CAPTURE_TIMEOUT_MS;
    err = rehab_capture_packet_acquire(&packet, capture_timeout);
    result->capture_us = elapsed_u32(capture_started);
    if (err != ESP_OK) {
        strlcpy(result->last_error,
                err == ESP_ERR_NOT_FINISHED ? "capture_busy" : "capture_failed",
                sizeof(result->last_error));
        goto done;
    }
    strlcpy(result->capture_id, packet.capture_id, sizeof(result->capture_id));
    result->image_seq = packet.image_seq;
    result->tof_seq = packet.tof_seq;
    result->local_pair_offset_ms = packet.offset_ms;
    if (!packet.ready_for_cloud) {
        strlcpy(result->last_error, "pair_not_ready", sizeof(result->last_error));
        err = ESP_ERR_NOT_FOUND;
        goto done;
    }
    if (packet.image_length != REHAB_CAPTURE_IMAGE_BYTES) {
        strlcpy(result->last_error, "capture_failed", sizeof(result->last_error));
        err = ESP_ERR_INVALID_SIZE;
        goto done;
    }
    err = cloud_set_epoch_timestamps(&packet);
    if (err != ESP_OK) {
        strlcpy(result->last_error, "time_not_synchronized",
                sizeof(result->last_error));
        goto done;
    }
    result->request_content_length = packet.content_length;
    if (deadline_expired(deadline_us)) {
        err = ESP_ERR_TIMEOUT;
        strlcpy(result->last_error, "http_open_failed",
                sizeof(result->last_error));
        goto done;
    }

    esp_http_client_config_t config = {
        .url = result->endpoint_url,
        .timeout_ms = (int)timeout_ms,
        .keep_alive_enable = false,
        .buffer_size = 2048,
        .buffer_size_tx = 2048,
    };
    client = esp_http_client_init(&config);
    if (client == NULL) {
        strlcpy(result->last_error, "http_client_init_failed",
                sizeof(result->last_error));
        err = ESP_ERR_NO_MEM;
        goto done;
    }
    esp_http_client_set_method(client, HTTP_METHOD_POST);
    esp_http_client_set_header(client, "Content-Type", packet.content_type);
    esp_http_client_set_header(client, "Accept", "application/json");
    esp_http_client_set_header(client, "Connection", "close");

    int64_t connect_started = esp_timer_get_time();
    err = esp_http_client_open(client, (int)packet.content_length);
    result->connect_us = elapsed_u32(connect_started);
    if (err != ESP_OK) {
        strlcpy(result->last_error, "http_open_failed",
                sizeof(result->last_error));
        goto done;
    }

    writer.client = client;
    writer.expected_image_length = packet.image_length;
    result->write_stats.internal_free_before_upload =
        heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    result->write_stats.internal_min_before_upload =
        heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL);
    result->write_stats.upload_task_priority = uxTaskPriorityGet(NULL);
    result->write_stats.upload_task_core = xPortGetCoreID();
    int64_t upload_started = esp_timer_get_time();
    err = rehab_capture_packet_write_multipart(&packet, cloud_http_write, &writer);
    result->upload_us = elapsed_u32(upload_started);
    result->write_stats.internal_free_after_upload =
        heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    result->write_stats.internal_min_after_upload =
        heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL);
    if (err != ESP_OK ||
        result->write_stats.bytes_requested_total != packet.content_length ||
        result->write_stats.bytes_written_total != packet.content_length) {
        strlcpy(result->last_error, "http_write_failed",
                sizeof(result->last_error));
        err = ESP_FAIL;
        goto done;
    }
    if (deadline_expired(deadline_us)) {
        strlcpy(result->last_error, "response_headers_failed",
                sizeof(result->last_error));
        err = ESP_ERR_TIMEOUT;
        goto done;
    }

    int64_t response_started = esp_timer_get_time();
    int64_t response_content_length = esp_http_client_fetch_headers(client);
    if (response_content_length < -1) {
        strlcpy(result->last_error, "response_headers_failed",
                sizeof(result->last_error));
        err = ESP_FAIL;
        goto done;
    }
    result->http_status = esp_http_client_get_status_code(client);
    err = cloud_read_response(client, response_content_length, deadline_us,
                              &response, &result->response_length);
    result->response_wait_us = elapsed_u32(response_started);
    if (err != ESP_OK) {
        strlcpy(result->last_error,
                err == ESP_ERR_INVALID_SIZE ? "response_too_large"
                                            : "response_read_failed",
                sizeof(result->last_error));
        goto done;
    }
    size_t preview = result->response_length;
    if (preview >= sizeof(result->raw_response_preview)) {
        preview = sizeof(result->raw_response_preview) - 1U;
    }
    memcpy(result->raw_response_preview, response, preview);
    result->raw_response_preview[preview] = '\0';
    result->transport_ok = true;
    if (result->response_length == 0) {
        strlcpy(result->last_error, "response_json_invalid",
                sizeof(result->last_error));
        err = ESP_ERR_INVALID_RESPONSE;
        goto done;
    }

    int64_t parse_started = esp_timer_get_time();
    err = rehab_cloud_fusion_parse_response(response, packet.capture_id, result);
    result->parse_us = elapsed_u32(parse_started);
    if (result->http_status != 200) {
        strlcpy(result->last_error, "http_status_error",
                sizeof(result->last_error));
        err = ESP_FAIL;
        goto done;
    }
    if (err == ESP_OK) {
        result->ok = true;
        strlcpy(result->last_error, "none", sizeof(result->last_error));
        ESP_LOGI(TAG,
            "CLOUD_FUSION capture_id=%s http_status=%d request_bytes=%u "
            "response_bytes=%u cloud_valid=%d detections=%" PRIu32
            " object_count=%" PRIu32 " zxl_found=%d person_count=%" PRIu32
            " local_offset_ms=%" PRIu32 " cloud_offset_ms=%" PRIu32
            " capture_ms=%" PRIu32 " upload_ms=%" PRIu32
            " cloud_processing_ms=%" PRIu32 " total_ms=%" PRIu32,
            result->capture_id, result->http_status,
            (unsigned)result->request_content_length,
            (unsigned)result->response_length, result->cloud_valid,
            result->detection_count, result->summary_object_count,
            result->summary_zxl_found, result->summary_person_count,
            result->local_pair_offset_ms, result->cloud_pair_offset_ms,
            result->capture_us / 1000U, result->upload_us / 1000U,
            result->processing_total_ms, elapsed_u32(total_started) / 1000U);
    }

done:
    result->total_us = elapsed_u32(total_started);
    ESP_LOGI(TAG,
        "CLOUD_WRITE_STATS capture_id=%s chunk=%" PRIu32
        " segments=%" PRIu32 " calls=%" PRIu32
        " image_calls=%" PRIu32 " other_calls=%" PRIu32
        " requested=%" PRIu64 " written=%" PRIu64
        " partial=%" PRIu32 " return_min=%" PRId32
        " return_max=%" PRId32 " return_avg=%" PRId64
        " max_block_us=%" PRIu64 " total_block_us=%" PRIu64
        " buckets={lt1:%" PRIu32 ",1_5:%" PRIu32 ",5_20:%" PRIu32
        ",20_100:%" PRIu32 ",gt100:%" PRIu32 "}"
        " zero=%" PRIu32 " negative=%" PRIu32
        " tcp_mss=%" PRIu32 " tcp_snd_buf=%" PRIu32
        " heap_before=%" PRIu32 " heap_after=%" PRIu32
        " heap_min_before=%" PRIu32 " heap_min_after=%" PRIu32
        " task_prio=%" PRIu32 " task_core=%" PRId32,
        result->capture_id, result->write_stats.application_chunk_size,
        result->write_stats.writer_segment_count,
        result->write_stats.http_write_call_count,
        result->write_stats.image_segment_write_calls,
        result->write_stats.header_footer_write_calls,
        result->write_stats.bytes_requested_total,
        result->write_stats.bytes_written_total,
        result->write_stats.http_write_partial_count,
        result->write_stats.write_return_min,
        result->write_stats.write_return_max,
        result->write_stats.write_return_average,
        result->write_stats.max_single_write_block_us,
        result->write_stats.total_write_block_us,
        result->write_stats.write_lt_1ms_count,
        result->write_stats.write_1_to_5ms_count,
        result->write_stats.write_5_to_20ms_count,
        result->write_stats.write_20_to_100ms_count,
        result->write_stats.write_gt_100ms_count,
        result->write_stats.zero_write_count,
        result->write_stats.negative_write_count,
        result->write_stats.tcp_mss_config,
        result->write_stats.tcp_snd_buf_config,
        result->write_stats.internal_free_before_upload,
        result->write_stats.internal_free_after_upload,
        result->write_stats.internal_min_before_upload,
        result->write_stats.internal_min_after_upload,
        result->write_stats.upload_task_priority,
        result->write_stats.upload_task_core);
    if (!result->ok) {
        ESP_LOGE(TAG, "CLOUD_FUSION_FAILED stage=%s capture_id=%s http_status=%d",
                 result->last_error, result->capture_id, result->http_status);
    }
    if (client != NULL) {
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
    }
    heap_caps_free(response);
    rehab_capture_packet_release(&packet);
    xSemaphoreGive(s_upload_lock);
    return err;
}
