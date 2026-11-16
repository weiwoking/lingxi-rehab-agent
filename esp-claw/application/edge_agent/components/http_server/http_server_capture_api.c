// SPDX-License-Identifier: Apache-2.0

#include "http_server_priv.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "rehab_capture.h"

#define CAPTURE_HTTP_TIMEOUT_MS 6000U

static const char *TAG = "capture_http";

static esp_err_t capture_send_error(httpd_req_t *req, esp_err_t err)
{
    if (err == ESP_ERR_NOT_FINISHED || err == ESP_ERR_INVALID_STATE) {
        httpd_resp_set_status(req, "503 Service Unavailable");
    } else if (err == ESP_ERR_TIMEOUT) {
        httpd_resp_set_status(req, "504 Gateway Timeout");
    } else if (err == ESP_ERR_NOT_FOUND) {
        httpd_resp_set_status(req, "422 Unprocessable Entity");
    } else {
        httpd_resp_set_status(req, "500 Internal Server Error");
    }
    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    char body[96];
    int length = snprintf(body, sizeof(body),
                          "{\"ok\":false,\"error\":\"%s\"}",
                          esp_err_to_name(err));
    if (length < 0 || (size_t)length >= sizeof(body)) {
        return httpd_resp_send_500(req);
    }
    return httpd_resp_send(req, body, length);
}

static esp_err_t capture_status_handler(httpd_req_t *req)
{
    rehab_capture_status_t status;
    esp_err_t err = rehab_capture_get_status(&status);
    if (err != ESP_OK) {
        return capture_send_error(req, err);
    }
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) {
        return httpd_resp_send_500(req);
    }
    cJSON_AddBoolToObject(root, "ready", status.ready);
    cJSON_AddBoolToObject(root, "busy", status.busy);
    cJSON_AddBoolToObject(root, "camera_ready", status.camera_ready);
    cJSON_AddBoolToObject(root, "tof_ready", status.tof_ready);
    cJSON_AddNumberToObject(root, "configured_candidate_count",
                            status.configured_candidate_count);
    cJSON_AddNumberToObject(root, "max_candidate_count",
                            status.max_candidate_count);
    cJSON_AddNumberToObject(root, "max_pair_offset_ms",
                            status.max_pair_offset_ms);
    cJSON_AddNumberToObject(root, "request_count", (double)status.request_count);
    cJSON_AddNumberToObject(root, "success_count", (double)status.success_count);
    cJSON_AddNumberToObject(root, "error_count", (double)status.error_count);
    cJSON_AddNumberToObject(root, "next_capture_seq",
                            (double)status.next_capture_seq);
    http_server_json_add_string(root, "last_capture_id", status.last_capture_id);
    cJSON_AddNumberToObject(root, "last_image_seq",
                            (double)status.last_image_seq);
    cJSON_AddNumberToObject(root, "last_tof_seq", (double)status.last_tof_seq);
    cJSON_AddNumberToObject(root, "last_actual_candidate_count",
                            status.last_actual_candidate_count);
    cJSON_AddNumberToObject(root, "last_selected_index",
                            status.last_selected_index);
    http_server_json_add_string(root, "last_selection_policy",
                                "nearest_timestamp");
    cJSON_AddNumberToObject(root, "last_signed_offset_us",
                            (double)status.last_signed_offset_us);
    cJSON_AddNumberToObject(root, "last_absolute_offset_us",
                            (double)status.last_absolute_offset_us);
    cJSON_AddNumberToObject(root, "last_offset_ms", status.last_offset_ms);
    cJSON_AddBoolToObject(root, "last_pair_within_limit",
                          status.last_pair_within_limit);
    cJSON_AddBoolToObject(root, "last_ready_for_cloud",
                          status.last_ready_for_cloud);
    cJSON_AddNumberToObject(root, "last_packet_content_length",
                            (double)status.last_packet_content_length);
    cJSON_AddBoolToObject(root, "packet_lease", status.packet_lease);
    http_server_json_add_string(root, "last_error", status.last_error);
    cJSON *timeouts = cJSON_AddObjectToObject(root, "timeout_counters");
    if (timeouts != NULL) {
        cJSON_AddNumberToObject(timeouts, "caller",
                                (double)status.caller_timeout_count);
        cJSON_AddNumberToObject(timeouts, "camera",
                                (double)status.camera_timeout_count);
        cJSON_AddNumberToObject(timeouts, "tof",
                                (double)status.tof_timeout_count);
    }
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return http_server_send_json_response(req, root);
}

static cJSON *capture_build_cloud_tof(const rehab_capture_packet_t *packet)
{
    cJSON *tof = cJSON_CreateObject();
    if (tof == NULL) {
        return NULL;
    }
    cJSON_AddStringToObject(tof, "capture_id", packet->capture_id);
    cJSON_AddNumberToObject(tof, "image_timestamp_ms",
                            (double)(packet->image_timestamp_us / 1000LL));
    cJSON_AddNumberToObject(tof, "tof_timestamp_ms",
                            (double)(packet->tof_timestamp_us / 1000LL));
    cJSON_AddStringToObject(tof, "resolution", "8x8");
    cJSON *distance = cJSON_AddArrayToObject(tof, "distance_mm");
    cJSON *status = cJSON_AddArrayToObject(tof, "status_code");
    if (distance == NULL || status == NULL) {
        cJSON_Delete(tof);
        return NULL;
    }
    for (size_t i = 0; i < REHAB_TOF_ZONE_COUNT; ++i) {
        cJSON *distance_value = cJSON_CreateNumber(packet->distance_mm[i]);
        cJSON *status_value = cJSON_CreateNumber(packet->status_code[i]);
        if (distance_value == NULL || status_value == NULL) {
            cJSON_Delete(distance_value);
            cJSON_Delete(status_value);
            cJSON_Delete(tof);
            return NULL;
        }
        cJSON_AddItemToArray(distance, distance_value);
        cJSON_AddItemToArray(status, status_value);
    }
    return tof;
}

static esp_err_t capture_once_handler(httpd_req_t *req)
{
    rehab_capture_packet_t packet;
    esp_err_t err = rehab_capture_packet_acquire(&packet,
                                                  CAPTURE_HTTP_TIMEOUT_MS);
    if (err != ESP_OK) {
        return capture_send_error(req, err);
    }
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) {
        rehab_capture_packet_release(&packet);
        return httpd_resp_send_500(req);
    }
    cJSON_AddBoolToObject(root, "ok", true);
    cJSON_AddStringToObject(root, "capture_id", packet.capture_id);
    cJSON_AddBoolToObject(root, "ready_for_cloud", packet.ready_for_cloud);

    cJSON *camera = cJSON_AddObjectToObject(root, "camera");
    cJSON_AddNumberToObject(camera, "image_seq", (double)packet.image_seq);
    cJSON_AddNumberToObject(camera, "timestamp_us",
                            (double)packet.image_timestamp_us);
    cJSON_AddNumberToObject(camera, "timestamp_ms",
                            (double)(packet.image_timestamp_us / 1000LL));
    cJSON_AddStringToObject(camera, "timestamp_domain", "boot_monotonic");
    cJSON_AddNumberToObject(camera, "width", REHAB_CAPTURE_IMAGE_WIDTH);
    cJSON_AddNumberToObject(camera, "height", REHAB_CAPTURE_IMAGE_HEIGHT);
    cJSON_AddNumberToObject(camera, "stride", REHAB_CAPTURE_IMAGE_STRIDE);
    cJSON_AddNumberToObject(camera, "length", packet.image_length);
    cJSON_AddStringToObject(camera, "pixel_format", "RGB565");
    cJSON_AddStringToObject(camera, "byte_order", "little_endian");
    cJSON_AddNumberToObject(camera, "crc32", packet.image_crc32);

    cJSON *tof_capture = cJSON_AddObjectToObject(root, "tof_capture");
    cJSON_AddNumberToObject(tof_capture, "configured_candidate_count",
                            packet.configured_candidate_count);
    cJSON_AddNumberToObject(tof_capture, "actual_candidate_count",
                            packet.actual_candidate_count);
    cJSON_AddNumberToObject(tof_capture, "selected_index",
                            packet.selected_index);
    cJSON_AddNumberToObject(tof_capture, "selected_tof_seq",
                            (double)packet.tof_seq);
    cJSON_AddStringToObject(tof_capture, "selection_policy",
                            "nearest_timestamp");

    cJSON *pairing = cJSON_AddObjectToObject(root, "pairing");
    cJSON_AddNumberToObject(pairing, "signed_offset_us",
                            (double)packet.signed_offset_us);
    cJSON_AddNumberToObject(pairing, "absolute_offset_us",
                            (double)packet.absolute_offset_us);
    cJSON_AddNumberToObject(pairing, "offset_ms", packet.offset_ms);
    cJSON_AddNumberToObject(pairing, "max_offset_ms",
                            REHAB_CAPTURE_MAX_PAIR_OFFSET_MS);
    cJSON_AddBoolToObject(pairing, "within_limit", packet.pair_within_limit);

    cJSON *cloud = cJSON_AddObjectToObject(root, "cloud_request");
    cJSON *tof = capture_build_cloud_tof(&packet);
    if (camera == NULL || tof_capture == NULL || pairing == NULL || cloud == NULL ||
        tof == NULL) {
        cJSON_Delete(tof);
        cJSON_Delete(root);
        rehab_capture_packet_release(&packet);
        return httpd_resp_send_500(req);
    }
    cJSON_AddItemToObject(cloud, "tof", tof);
    cJSON *multipart = cJSON_AddObjectToObject(cloud, "multipart");
    if (multipart == NULL) {
        cJSON_Delete(root);
        rehab_capture_packet_release(&packet);
        return httpd_resp_send_500(req);
    }
    cJSON_AddStringToObject(multipart, "boundary", packet.boundary);
    cJSON_AddStringToObject(multipart, "content_type", packet.content_type);
    cJSON_AddNumberToObject(multipart, "content_length", packet.content_length);
    cJSON_AddNumberToObject(multipart, "image_part_length", packet.image_length);
    cJSON_AddNumberToObject(multipart, "tof_json_length", packet.tof_json_length);
    cJSON *timing = cJSON_AddObjectToObject(root, "timing");
    if (timing != NULL) {
        cJSON_AddNumberToObject(timing, "total_us", (double)packet.total_us);
    }
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    err = http_server_send_json_response(req, root);
    rehab_capture_packet_release(&packet);
    return err;
}

typedef struct {
    httpd_handle_t server;
    int socket_fd;
} capture_socket_writer_t;

static esp_err_t capture_socket_write(void *ctx, const void *data, size_t length)
{
    capture_socket_writer_t *writer = ctx;
    const uint8_t *cursor = data;
    while (length > 0) {
        int sent = httpd_socket_send(writer->server, writer->socket_fd,
                                     (const char *)cursor, length, 0);
        if (sent <= 0) {
            return ESP_FAIL;
        }
        cursor += sent;
        length -= (size_t)sent;
    }
    return ESP_OK;
}

static esp_err_t capture_multipart_handler(httpd_req_t *req)
{
    rehab_capture_packet_t packet;
    esp_err_t err = rehab_capture_packet_acquire(&packet,
                                                  CAPTURE_HTTP_TIMEOUT_MS);
    if (err != ESP_OK) {
        return capture_send_error(req, err);
    }
    char header[640];
    int header_length = snprintf(header, sizeof(header),
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: %s\r\n"
        "Content-Length: %u\r\n"
        "X-Capture-Id: %s\r\n"
        "X-Image-Seq: %" PRIu64 "\r\n"
        "X-ToF-Seq: %" PRIu64 "\r\n"
        "X-Pair-Offset-Ms: %" PRIu32 "\r\n"
        "X-Pair-Within-Limit: %s\r\n"
        "X-Ready-For-Cloud: %s\r\n"
        "Cache-Control: no-store\r\n"
        "Pragma: no-cache\r\n\r\n",
        packet.content_type,
        (unsigned)packet.content_length,
        packet.capture_id,
        packet.image_seq,
        packet.tof_seq,
        packet.offset_ms,
        packet.pair_within_limit ? "true" : "false",
        packet.ready_for_cloud ? "true" : "false");
    if (header_length < 0 || (size_t)header_length >= sizeof(header)) {
        rehab_capture_packet_release(&packet);
        return capture_send_error(req, ESP_ERR_INVALID_SIZE);
    }
    capture_socket_writer_t writer = {
        .server = req->handle,
        .socket_fd = httpd_req_to_sockfd(req),
    };
    err = capture_socket_write(&writer, header, (size_t)header_length);
    if (err == ESP_OK) {
        err = rehab_capture_packet_write_multipart(&packet,
                                                    capture_socket_write,
                                                    &writer);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "http_send_failed capture_id=%s", packet.capture_id);
    }
    rehab_capture_packet_release(&packet);
    return err;
}

esp_err_t http_server_register_capture_routes(httpd_handle_t server)
{
    const httpd_uri_t handlers[] = {
        { .uri = "/api/perception/status", .method = HTTP_GET,
          .handler = capture_status_handler },
        { .uri = "/api/perception/capture-once", .method = HTTP_POST,
          .handler = capture_once_handler },
        { .uri = "/api/perception/capture-once.multipart", .method = HTTP_POST,
          .handler = capture_multipart_handler },
    };
    for (size_t i = 0; i < sizeof(handlers) / sizeof(handlers[0]); ++i) {
        esp_err_t err = httpd_register_uri_handler(server, &handlers[i]);
        if (err != ESP_OK) {
            return err;
        }
    }
    ESP_LOGI(TAG, "CAPTURE_HTTP_ROUTES count=3");
    return ESP_OK;
}

