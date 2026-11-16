/*
 * SPDX-License-Identifier: Apache-2.0
 */
#include "http_server_priv.h"

#include "esp_log.h"
#include "rehab_tof.h"

#define TOF_HTTP_SNAPSHOT_TIMEOUT_MS 3000

static const char *TAG = "http_tof";

static void tof_json_add_distance_array(cJSON *root, const int16_t values[REHAB_TOF_ZONE_COUNT])
{
    cJSON *array = cJSON_AddArrayToObject(root, "distance_mm");
    if (array == NULL) {
        return;
    }
    for (size_t i = 0; i < REHAB_TOF_ZONE_COUNT; ++i) {
        cJSON_AddItemToArray(array, cJSON_CreateNumber(values[i]));
    }
}

static void tof_json_add_status_array(cJSON *root, const uint8_t values[REHAB_TOF_ZONE_COUNT])
{
    cJSON *array = cJSON_AddArrayToObject(root, "status_code");
    if (array == NULL) {
        return;
    }
    for (size_t i = 0; i < REHAB_TOF_ZONE_COUNT; ++i) {
        cJSON_AddItemToArray(array, cJSON_CreateNumber(values[i]));
    }
}

static void tof_json_add_resolution(cJSON *root)
{
    cJSON *resolution = cJSON_AddObjectToObject(root, "resolution");
    if (resolution != NULL) {
        cJSON_AddNumberToObject(resolution, "width", 8);
        cJSON_AddNumberToObject(resolution, "height", 8);
    }
}

static void tof_json_add_summary(cJSON *root,
                                 uint8_t valid_zone_count,
                                 uint8_t usable_zone_count,
                                 uint8_t high_confidence_zone_count,
                                 uint8_t status_5_count,
                                 uint8_t status_6_count,
                                 uint8_t status_9_count,
                                 uint8_t status_255_count,
                                 uint8_t warmup_status_5_count,
                                 uint8_t warmup_status_6_count,
                                 uint8_t warmup_status_9_count,
                                 uint8_t warmup_status_255_count,
                                 uint8_t frames_consumed_per_snapshot)
{
    cJSON_AddNumberToObject(root, "valid_zone_count", valid_zone_count);
    cJSON_AddNumberToObject(root, "usable_zone_count", usable_zone_count);
    cJSON_AddNumberToObject(root, "high_confidence_zone_count", high_confidence_zone_count);
    cJSON_AddNumberToObject(root, "status_5_count", status_5_count);
    cJSON_AddNumberToObject(root, "status_6_count", status_6_count);
    cJSON_AddNumberToObject(root, "status_9_count", status_9_count);
    cJSON_AddNumberToObject(root, "status_255_count", status_255_count);
    cJSON_AddNumberToObject(root, "warmup_status_5_count", warmup_status_5_count);
    cJSON_AddNumberToObject(root, "warmup_status_6_count", warmup_status_6_count);
    cJSON_AddNumberToObject(root, "warmup_status_9_count", warmup_status_9_count);
    cJSON_AddNumberToObject(root, "warmup_status_255_count", warmup_status_255_count);
    cJSON_AddNumberToObject(root, "frames_consumed_per_snapshot",
                            frames_consumed_per_snapshot);
}

static esp_err_t tof_send_error(httpd_req_t *req, const char *status, const char *message)
{
    httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, "text/plain; charset=utf-8");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store, max-age=0");
    return httpd_resp_sendstr(req, message);
}

static esp_err_t tof_status_handler(httpd_req_t *req)
{
    rehab_tof_status_t status;
    esp_err_t err = rehab_tof_get_status(&status);
    if (err != ESP_OK) {
        return tof_send_error(req, "503 Service Unavailable", "ToF status unavailable");
    }

    cJSON *root = cJSON_CreateObject();
    if (root == NULL) {
        return httpd_resp_send_500(req);
    }
    cJSON_AddBoolToObject(root, "ok", true);
    http_server_json_add_string(root, "capture_mode", "on_demand");
    http_server_json_add_string(root, "state", rehab_tof_state_name(status.state));
    cJSON_AddBoolToObject(root, "ready", status.ready);
    cJSON_AddBoolToObject(root, "snapshot_busy", status.snapshot_busy);
    cJSON_AddNumberToObject(root, "generation", (double)status.generation);
    cJSON_AddNumberToObject(root, "request_count", (double)status.request_count);
    cJSON_AddNumberToObject(root, "success_count", (double)status.success_count);
    cJSON_AddNumberToObject(root, "error_count", (double)status.error_count);
    cJSON_AddNumberToObject(root, "timestamp_us", (double)status.timestamp_us);
    cJSON_AddNumberToObject(root, "total_time_ms", status.total_time_ms);
    tof_json_add_summary(root,
                         status.valid_zone_count,
                         status.usable_zone_count,
                         status.high_confidence_zone_count,
                         status.status_5_count,
                         status.status_6_count,
                         status.status_9_count,
                         status.status_255_count,
                         status.warmup_status_5_count,
                         status.warmup_status_6_count,
                         status.warmup_status_9_count,
                         status.warmup_status_255_count,
                         status.frames_consumed_per_snapshot);
    cJSON_AddNumberToObject(root, "minimum_valid_distance_mm", status.minimum_valid_distance_mm);
    cJSON_AddNumberToObject(root, "maximum_valid_distance_mm", status.maximum_valid_distance_mm);
    http_server_json_add_string(root, "last_error", status.last_error);
    cJSON_AddNumberToObject(root, "last_esp_err", status.last_esp_err);
    cJSON_AddNumberToObject(root, "i2c_port", status.i2c_port);
    cJSON_AddNumberToObject(root, "i2c_sda_gpio", status.sda_gpio);
    cJSON_AddNumberToObject(root, "i2c_scl_gpio", status.scl_gpio);
    cJSON_AddNumberToObject(root, "i2c_frequency_hz", status.i2c_frequency_hz);
    cJSON_AddNumberToObject(root, "ranging_frequency_hz", status.ranging_frequency_hz);
    tof_json_add_resolution(root);
    tof_json_add_distance_array(root, status.distance_mm);
    tof_json_add_status_array(root, status.status_code);
    return http_server_send_json_response(req, root);
}

static esp_err_t tof_snapshot_handler(httpd_req_t *req)
{
    if (!rehab_tof_is_ready()) {
        return tof_send_error(req, "503 Service Unavailable", "ToF provider is not ready");
    }

    rehab_tof_snapshot_t snapshot;
    esp_err_t err = rehab_tof_snapshot_acquire(&snapshot, TOF_HTTP_SNAPSHOT_TIMEOUT_MS);
    if (err == ESP_ERR_TIMEOUT) {
        return tof_send_error(req, "504 Gateway Timeout", "ToF snapshot timed out");
    }
    if (err == ESP_ERR_INVALID_STATE) {
        return tof_send_error(req, "503 Service Unavailable", "ToF snapshot is busy");
    }
    if (err != ESP_OK) {
        return tof_send_error(req, "503 Service Unavailable", "ToF snapshot failed");
    }

    cJSON *root = cJSON_CreateObject();
    if (root == NULL) {
        return httpd_resp_send_500(req);
    }
    cJSON_AddBoolToObject(root, "ok", true);
    http_server_json_add_string(root, "capture_mode", "on_demand");
    cJSON_AddNumberToObject(root, "generation", (double)snapshot.generation);
    cJSON_AddNumberToObject(root, "timestamp_us", (double)snapshot.timestamp_us);
    cJSON_AddNumberToObject(root, "timestamp_ms", (double)(snapshot.timestamp_us / 1000LL));
    cJSON_AddNumberToObject(root, "total_time_ms", snapshot.total_time_ms);
    tof_json_add_summary(root,
                         snapshot.valid_zone_count,
                         snapshot.usable_zone_count,
                         snapshot.high_confidence_zone_count,
                         snapshot.status_5_count,
                         snapshot.status_6_count,
                         snapshot.status_9_count,
                         snapshot.status_255_count,
                         snapshot.warmup_status_5_count,
                         snapshot.warmup_status_6_count,
                         snapshot.warmup_status_9_count,
                         snapshot.warmup_status_255_count,
                         snapshot.frames_consumed_per_snapshot);
    cJSON_AddNumberToObject(root, "minimum_valid_distance_mm", snapshot.minimum_valid_distance_mm);
    cJSON_AddNumberToObject(root, "maximum_valid_distance_mm", snapshot.maximum_valid_distance_mm);
    tof_json_add_resolution(root);
    tof_json_add_distance_array(root, snapshot.distance_mm);
    tof_json_add_status_array(root, snapshot.status_code);
    return http_server_send_json_response(req, root);
}

esp_err_t http_server_register_tof_routes(httpd_handle_t server)
{
    const httpd_uri_t handlers[] = {
        { .uri = "/api/tof/status", .method = HTTP_GET, .handler = tof_status_handler },
        { .uri = "/api/tof/snapshot", .method = HTTP_GET, .handler = tof_snapshot_handler },
    };
    for (size_t i = 0; i < sizeof(handlers) / sizeof(handlers[0]); ++i) {
        esp_err_t err = httpd_register_uri_handler(server, &handlers[i]);
        if (err != ESP_OK) {
            return err;
        }
    }
    ESP_LOGI(TAG, "TOF_HTTP_ROUTES status=/api/tof/status snapshot=/api/tof/snapshot");
    return ESP_OK;
}
