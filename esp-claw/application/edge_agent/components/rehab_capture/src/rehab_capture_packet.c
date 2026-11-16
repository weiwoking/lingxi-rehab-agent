// SPDX-License-Identifier: Apache-2.0

#include "rehab_capture_priv.h"

#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    char image_header[192];
    size_t image_header_length;
    char tof_header[128];
    size_t tof_header_length;
    char closing[64];
    size_t closing_length;
} multipart_segments_t;

static uint32_t capture_crc32(const uint8_t *data, size_t length)
{
    uint32_t crc = UINT32_MAX;
    for (size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (unsigned bit = 0; bit < 8; ++bit) {
            crc = (crc >> 1U) ^ (0xEDB88320U & (uint32_t)-(int32_t)(crc & 1U));
        }
    }
    return ~crc;
}

static esp_err_t json_append(char *buffer, size_t capacity, size_t *used,
                             const char *format, ...)
{
    if (*used >= capacity) {
        return ESP_ERR_INVALID_SIZE;
    }
    va_list args;
    va_start(args, format);
    int written = vsnprintf(buffer + *used, capacity - *used, format, args);
    va_end(args);
    if (written < 0 || (size_t)written >= capacity - *used) {
        return ESP_ERR_INVALID_SIZE;
    }
    *used += (size_t)written;
    return ESP_OK;
}

static esp_err_t build_tof_json(rehab_capture_packet_t *packet,
                                int64_t epoch_offset_us)
{
    size_t used = 0;
    esp_err_t err = json_append(packet->tof_json, sizeof(packet->tof_json), &used,
        "{\"capture_id\":\"%s\",\"image_timestamp_ms\":%" PRId64
        ",\"tof_timestamp_ms\":%" PRId64
        ",\"resolution\":\"8x8\",\"distance_mm\":[",
        packet->capture_id,
        (packet->image_timestamp_us + epoch_offset_us) / 1000LL,
        (packet->tof_timestamp_us + epoch_offset_us) / 1000LL);
    if (err != ESP_OK) {
        return err;
    }
    for (size_t i = 0; i < REHAB_TOF_ZONE_COUNT; ++i) {
        err = json_append(packet->tof_json, sizeof(packet->tof_json), &used,
                          i == 0 ? "%d" : ",%d", packet->distance_mm[i]);
        if (err != ESP_OK) {
            return err;
        }
    }
    err = json_append(packet->tof_json, sizeof(packet->tof_json), &used,
                      "],\"status_code\":[");
    if (err != ESP_OK) {
        return err;
    }
    for (size_t i = 0; i < REHAB_TOF_ZONE_COUNT; ++i) {
        err = json_append(packet->tof_json, sizeof(packet->tof_json), &used,
                          i == 0 ? "%u" : ",%u", packet->status_code[i]);
        if (err != ESP_OK) {
            return err;
        }
    }
    err = json_append(packet->tof_json, sizeof(packet->tof_json), &used, "]}");
    if (err != ESP_OK) {
        return err;
    }
    packet->tof_json_length = used;
    return ESP_OK;
}

static esp_err_t build_segments(const rehab_capture_packet_t *packet,
                                multipart_segments_t *segments)
{
    int written = snprintf(segments->image_header, sizeof(segments->image_header),
        "--%s\r\n"
        "Content-Disposition: form-data; name=\"image\"; filename=\"%s.rgb565\"\r\n"
        "Content-Type: application/octet-stream\r\n\r\n",
        packet->boundary, packet->capture_id);
    if (written < 0 || (size_t)written >= sizeof(segments->image_header)) {
        return ESP_ERR_INVALID_SIZE;
    }
    segments->image_header_length = (size_t)written;

    written = snprintf(segments->tof_header, sizeof(segments->tof_header),
        "--%s\r\nContent-Disposition: form-data; name=\"tof\"\r\n\r\n",
        packet->boundary);
    if (written < 0 || (size_t)written >= sizeof(segments->tof_header)) {
        return ESP_ERR_INVALID_SIZE;
    }
    segments->tof_header_length = (size_t)written;

    written = snprintf(segments->closing, sizeof(segments->closing),
                       "\r\n--%s--\r\n", packet->boundary);
    if (written < 0 || (size_t)written >= sizeof(segments->closing)) {
        return ESP_ERR_INVALID_SIZE;
    }
    segments->closing_length = (size_t)written;
    return ESP_OK;
}

static bool checked_add_size(size_t *value, size_t addend)
{
    if (SIZE_MAX - *value < addend) {
        return false;
    }
    *value += addend;
    return true;
}

esp_err_t rehab_capture_build_packet(
    rehab_capture_packet_t *packet,
    uint8_t *image_slot,
    const rehab_follow_camera_raw_frame_t *camera,
    const rehab_tof_window_result_t *window,
    uint8_t selected_index,
    const char *capture_id,
    uint32_t lease_token,
    uint64_t total_us)
{
    if (packet == NULL || image_slot == NULL || camera == NULL || window == NULL ||
        capture_id == NULL || selected_index >= window->actual_candidate_count ||
        camera->length != REHAB_CAPTURE_IMAGE_BYTES) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(packet, 0, sizeof(*packet));
    packet->lease_token = lease_token;
    strlcpy(packet->capture_id, capture_id, sizeof(packet->capture_id));
    memcpy(image_slot, camera->data, camera->length);
    packet->image = image_slot;
    packet->image_length = camera->length;
    packet->image_seq = camera->generation;
    packet->image_timestamp_us = camera->timestamp_us;
    packet->image_crc32 = capture_crc32(image_slot, camera->length);
    packet->configured_candidate_count = REHAB_CAPTURE_TOF_CANDIDATE_COUNT;
    packet->actual_candidate_count = window->actual_candidate_count;
    packet->selected_index = selected_index;

    const rehab_tof_candidate_frame_t *candidate = &window->candidates[selected_index];
    packet->tof_seq = candidate->tof_frame_seq;
    packet->tof_timestamp_us = candidate->timestamp_us;
    memcpy(packet->distance_mm, candidate->distance_mm, sizeof(packet->distance_mm));
    memcpy(packet->status_code, candidate->status_code, sizeof(packet->status_code));
    packet->signed_offset_us = packet->tof_timestamp_us - packet->image_timestamp_us;
    packet->absolute_offset_us = packet->signed_offset_us < 0
        ? (uint64_t)(-packet->signed_offset_us)
        : (uint64_t)packet->signed_offset_us;
    packet->offset_ms = (uint32_t)(packet->absolute_offset_us / 1000ULL);
    packet->pair_within_limit =
        packet->absolute_offset_us <= (uint64_t)REHAB_CAPTURE_MAX_PAIR_OFFSET_MS * 1000ULL;
    packet->ready_for_cloud = packet->pair_within_limit;
    packet->total_us = total_us;

    int written = snprintf(packet->boundary, sizeof(packet->boundary),
                           "espclaw_%s", packet->capture_id);
    if (written < 0 || (size_t)written >= sizeof(packet->boundary)) {
        return ESP_ERR_INVALID_SIZE;
    }
    written = snprintf(packet->content_type, sizeof(packet->content_type),
                       "multipart/form-data; boundary=%s", packet->boundary);
    if (written < 0 || (size_t)written >= sizeof(packet->content_type)) {
        return ESP_ERR_INVALID_SIZE;
    }
    esp_err_t err = build_tof_json(packet, 0);
    if (err != ESP_OK) {
        return err;
    }
    multipart_segments_t segments;
    err = build_segments(packet, &segments);
    if (err != ESP_OK) {
        return err;
    }
    size_t total = 0;
    if (!checked_add_size(&total, segments.image_header_length) ||
        !checked_add_size(&total, packet->image_length) ||
        !checked_add_size(&total, 2U) ||
        !checked_add_size(&total, segments.tof_header_length) ||
        !checked_add_size(&total, packet->tof_json_length) ||
        !checked_add_size(&total, segments.closing_length)) {
        return ESP_ERR_INVALID_SIZE;
    }
    packet->content_length = total;
    return ESP_OK;
}

esp_err_t rehab_capture_packet_set_epoch_offset(
    rehab_capture_packet_t *packet,
    int64_t epoch_offset_us)
{
    if (packet == NULL || packet->lease_token == 0 || epoch_offset_us <= 0) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = build_tof_json(packet, epoch_offset_us);
    if (err != ESP_OK) {
        return err;
    }
    multipart_segments_t segments;
    err = build_segments(packet, &segments);
    if (err != ESP_OK) {
        return err;
    }
    size_t total = 0;
    if (!checked_add_size(&total, segments.image_header_length) ||
        !checked_add_size(&total, packet->image_length) ||
        !checked_add_size(&total, 2U) ||
        !checked_add_size(&total, segments.tof_header_length) ||
        !checked_add_size(&total, packet->tof_json_length) ||
        !checked_add_size(&total, segments.closing_length)) {
        return ESP_ERR_INVALID_SIZE;
    }
    packet->content_length = total;
    return ESP_OK;
}

esp_err_t rehab_capture_packet_write_multipart(
    const rehab_capture_packet_t *packet,
    rehab_capture_write_fn writer,
    void *writer_ctx)
{
    if (packet == NULL || writer == NULL || packet->lease_token == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    xSemaphoreTake(g_rehab_capture.lock, portMAX_DELAY);
    bool valid = g_rehab_capture.packet_lease &&
        packet->lease_token == g_rehab_capture.packet_token &&
        packet->image == g_rehab_capture.image_slot;
    xSemaphoreGive(g_rehab_capture.lock);
    if (!valid) {
        return ESP_ERR_INVALID_STATE;
    }

    multipart_segments_t segments;
    esp_err_t err = build_segments(packet, &segments);
    if (err != ESP_OK) {
        return err;
    }
    const struct {
        const void *data;
        size_t length;
    } chunks[] = {
        {segments.image_header, segments.image_header_length},
        {packet->image, packet->image_length},
        {"\r\n", 2U},
        {segments.tof_header, segments.tof_header_length},
        {packet->tof_json, packet->tof_json_length},
        {segments.closing, segments.closing_length},
    };
    size_t written = 0;
    for (size_t i = 0; i < sizeof(chunks) / sizeof(chunks[0]); ++i) {
        err = writer(writer_ctx, chunks[i].data, chunks[i].length);
        if (err != ESP_OK) {
            return err;
        }
        if (!checked_add_size(&written, chunks[i].length)) {
            return ESP_ERR_INVALID_SIZE;
        }
    }
    return written == packet->content_length ? ESP_OK : ESP_ERR_INVALID_SIZE;
}
