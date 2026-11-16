/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "http_server_priv.h"

#include <inttypes.h>

#include "esp_log.h"
#include "rehab_follow.h"

static const char *TAG = "http_camera";

#define CAMERA_SNAPSHOT_TIMEOUT_MS 3000

static const char s_camera_preview_html[] =
    "<!doctype html><html><head><meta charset=utf-8>"
    "<meta name=viewport content=\"width=device-width,initial-scale=1\">"
    "<title>Camera snapshot</title><style>"
    "body{margin:0;background:#111;color:#eee;font:14px system-ui;text-align:center}"
    "main{max-width:900px;margin:auto;padding:16px}img{max-width:100%;height:auto;"
    "background:#222;border-radius:8px}button{margin:0 0 12px;padding:10px 18px}"
    "#result{min-height:2em}</style></head><body><main>"
    "<h1>Camera snapshot</h1><button id=capture>拍摄/刷新</button>"
    "<div id=result>尚未拍照</div><img id=frame alt=\"Camera snapshot not ready\">"
    "</main><script>"
    "const frame=document.getElementById('frame'),button=document.getElementById('capture'),"
    "result=document.getElementById('result');let objectUrl='';"
    "async function capture(){if(button.disabled)return;button.disabled=true;"
    "const started=performance.now();result.textContent='拍照中…';try{"
    "const response=await fetch('/api/camera/frame.jpg?t='+Date.now(),{cache:'no-store'});"
    "if(!response.ok)throw new Error(await response.text()||('HTTP '+response.status));"
    "const blob=await response.blob();if(objectUrl)URL.revokeObjectURL(objectUrl);"
    "objectUrl=URL.createObjectURL(blob);const elapsed=Math.round(performance.now()-started);"
    "frame.onload=()=>{result.textContent='拍照时间：'+new Date().toLocaleString()+"
    "'；请求耗时：'+elapsed+' ms；尺寸：'+frame.naturalWidth+'×'+frame.naturalHeight;};"
    "frame.src=objectUrl;}catch(error){"
    "result.textContent='拍照失败：'+error.message+'；耗时：'+"
    "Math.round(performance.now()-started)+' ms';}finally{button.disabled=false;}}"
    "button.addEventListener('click',capture);capture();</script></body></html>";

static esp_err_t camera_preview_page_handler(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store, max-age=0");
    return httpd_resp_send(req, s_camera_preview_html, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t camera_send_unavailable(httpd_req_t *req, const char *message)
{
    httpd_resp_set_status(req, "503 Service Unavailable");
    httpd_resp_set_type(req, "text/plain; charset=utf-8");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store, max-age=0");
    return httpd_resp_sendstr(req, message);
}

static esp_err_t camera_send_timeout(httpd_req_t *req)
{
    httpd_resp_set_status(req, "504 Gateway Timeout");
    httpd_resp_set_type(req, "text/plain; charset=utf-8");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store, max-age=0");
    return httpd_resp_sendstr(req, "Camera snapshot timed out");
}

static esp_err_t camera_frame_handler(httpd_req_t *req)
{
    if (!rehab_follow_camera_is_ready()) {
        return camera_send_unavailable(req, "Camera provider is not ready");
    }

    rehab_follow_preview_frame_t frame;
    esp_err_t err = rehab_follow_camera_snapshot_acquire(&frame,
                                                         CAMERA_SNAPSHOT_TIMEOUT_MS);
    if (err == ESP_ERR_TIMEOUT) {
        return camera_send_timeout(req);
    }
    if (err == ESP_ERR_NOT_FINISHED) {
        return camera_send_unavailable(req, "Camera snapshot is busy");
    }
    if (err != ESP_OK) {
        return camera_send_unavailable(req, "Camera snapshot failed");
    }

    httpd_resp_set_type(req, "image/jpeg");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store, max-age=0");
    httpd_resp_set_hdr(req, "Pragma", "no-cache");
    err = httpd_resp_send(req, (const char *)frame.data, frame.length);
    rehab_follow_camera_preview_release(&frame);
    return err;
}

static void camera_status_add_memory(cJSON *root,
                                     const rehab_follow_camera_status_t *status)
{
    cJSON *memory = cJSON_AddObjectToObject(root, "memory");
    if (memory == NULL) {
        return;
    }
    cJSON_AddNumberToObject(memory, "psram_free_bytes", (double)status->psram_free_bytes);
    cJSON_AddNumberToObject(memory, "psram_min_free_bytes", (double)status->psram_min_free_bytes);
    cJSON_AddNumberToObject(memory, "psram_largest_free_block", (double)status->psram_largest_free_block);
    cJSON_AddNumberToObject(memory, "internal_free_bytes", (double)status->internal_free_bytes);
    cJSON_AddNumberToObject(memory, "internal_min_free_bytes", (double)status->internal_min_free_bytes);
    cJSON_AddNumberToObject(memory, "internal_largest_free_block", (double)status->internal_largest_free_block);
}

static esp_err_t camera_status_handler(httpd_req_t *req)
{
    rehab_follow_camera_status_t status;
    esp_err_t err = rehab_follow_camera_get_status(&status);
    if (err != ESP_OK) {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                                   "Failed to read camera status");
    }

    cJSON *root = cJSON_CreateObject();
    if (root == NULL) {
        httpd_resp_send_500(req);
        return ESP_ERR_NO_MEM;
    }

    http_server_json_add_string(root, "state", rehab_follow_camera_state_name(status.state));
    http_server_json_add_string(root, "capture_mode", "on_demand");
    http_server_json_add_string(root, "last_error", status.last_error);
    cJSON_AddNumberToObject(root, "last_esp_err", status.last_esp_err);
    cJSON_AddNumberToObject(root, "last_errno", status.last_errno);
    cJSON_AddBoolToObject(root, "owner_conflict", status.owner_conflict);
    cJSON_AddBoolToObject(root, "fd_valid", status.fd_valid);
    cJSON_AddBoolToObject(root, "streaming", status.streaming);
    http_server_json_add_string(root, "device_path", status.device_path);
    cJSON_AddNumberToObject(root, "width", status.width);
    cJSON_AddNumberToObject(root, "height", status.height);
    http_server_json_add_string(root, "pixel_format_fourcc", status.pixel_format_fourcc);
    http_server_json_add_string(root, "pixel_format_name", status.pixel_format_name);
    http_server_json_add_string(root, "byte_order", status.byte_order);
    cJSON_AddNumberToObject(root, "bytesperline", status.bytesperline);
    cJSON_AddNumberToObject(root, "sizeimage", status.sizeimage);
    cJSON_AddNumberToObject(root, "compact_payload_bytes",
                            (double)status.compact_payload_bytes);
    cJSON_AddNumberToObject(root, "source_width", status.source_width);
    cJSON_AddNumberToObject(root, "source_height", status.source_height);
    cJSON_AddNumberToObject(root, "source_pixel_format", status.source_pixel_format);
    http_server_json_add_string(root, "source_pixel_format_fourcc",
                                status.source_pixel_format_fourcc);
    http_server_json_add_string(root, "source_pixel_format_name",
                                status.source_pixel_format_name);
    http_server_json_add_string(root, "source_byte_order", status.source_byte_order);
    cJSON_AddNumberToObject(root, "source_bytesperline", status.source_bytesperline);
    cJSON_AddNumberToObject(root, "source_sizeimage", status.source_sizeimage);
    cJSON_AddNumberToObject(root, "source_payload_bytes",
                            (double)status.source_payload_bytes);
    cJSON_AddNumberToObject(root, "output_width", status.output_width);
    cJSON_AddNumberToObject(root, "output_height", status.output_height);
    http_server_json_add_string(root, "output_pixel_format", status.output_pixel_format);
    http_server_json_add_string(root, "output_byte_order", status.output_byte_order);
    cJSON_AddNumberToObject(root, "output_bytesperline", status.output_bytesperline);
    cJSON_AddNumberToObject(root, "output_payload_bytes",
                            (double)status.output_payload_bytes);
    http_server_json_add_string(root, "scaler_mode", status.scaler_mode);

    cJSON *format = cJSON_AddObjectToObject(root, "format");
    if (format != NULL) {
        cJSON_AddNumberToObject(format, "width", status.width);
        cJSON_AddNumberToObject(format, "height", status.height);
        cJSON_AddNumberToObject(format, "pixel_format", status.pixel_format);
        http_server_json_add_string(format, "fourcc", status.pixel_format_fourcc);
        http_server_json_add_string(format, "name", status.pixel_format_name);
        http_server_json_add_string(format, "byte_order", status.byte_order);
        cJSON_AddNumberToObject(format, "bytesperline", status.bytesperline);
        cJSON_AddNumberToObject(format, "sizeimage", status.sizeimage);
        cJSON_AddNumberToObject(format, "compact_payload_bytes",
                                (double)status.compact_payload_bytes);
    }

    cJSON_AddNumberToObject(root, "driver_buffer_count", status.driver_buffer_count);
    cJSON *driver_lengths = cJSON_AddArrayToObject(root, "driver_buffer_lengths");
    if (driver_lengths != NULL) {
        for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_DRIVER_BUFS; ++i) {
            cJSON_AddItemToArray(driver_lengths,
                                 cJSON_CreateNumber((double)status.driver_buffer_lengths[i]));
        }
    }
    cJSON_AddNumberToObject(root, "last_bytesused", status.last_bytesused);
    cJSON_AddNumberToObject(root, "frame_generation", (double)status.frame_generation);
    cJSON_AddNumberToObject(root, "capture_generation", (double)status.frame_generation);
    cJSON_AddNumberToObject(root, "preview_generation", (double)status.jpeg_generation);
    cJSON_AddNumberToObject(root, "capture_count", (double)status.capture_count);
    cJSON_AddNumberToObject(root, "jpeg_count", (double)status.jpeg_publish_count);
    cJSON_AddNumberToObject(root, "preview_encode_count", (double)status.jpeg_publish_count);
    cJSON_AddNumberToObject(root, "preview_drop_count", (double)status.preview_drop_count);
    cJSON_AddNumberToObject(root, "dq_error_count", (double)status.dq_error_count);
    cJSON_AddNumberToObject(root, "dqbuf_error_count", (double)status.dq_error_count);
    cJSON_AddNumberToObject(root, "q_error_count", (double)status.q_error_count);
    cJSON_AddNumberToObject(root, "qbuf_error_count", (double)status.q_error_count);
    cJSON_AddNumberToObject(root, "copy_error_count", (double)status.copy_error_count);
    cJSON_AddNumberToObject(root, "reopen_count", (double)status.reopen_count);
    cJSON_AddNumberToObject(root, "retry_count", status.retry_count);
    cJSON_AddNumberToObject(root, "last_frame_timestamp_us",
                            (double)status.last_frame_timestamp_us);
    cJSON_AddNumberToObject(root, "capture_interval_us_latest",
                            (double)status.last_interval_us);
    cJSON_AddNumberToObject(root, "capture_interval_us_min",
                            (double)status.min_interval_us);
    cJSON_AddNumberToObject(root, "capture_interval_us_max",
                            (double)status.max_interval_us);
    cJSON_AddNumberToObject(root, "capture_interval_us_average",
                            (double)status.avg_interval_us);
    cJSON_AddNumberToObject(root, "spiram_free", (double)status.psram_free_bytes);
    cJSON_AddNumberToObject(root, "spiram_min_free", (double)status.psram_min_free_bytes);
    cJSON_AddNumberToObject(root, "spiram_largest_block",
                            (double)status.psram_largest_free_block);
    cJSON_AddNumberToObject(root, "internal_free", (double)status.internal_free_bytes);
    cJSON_AddNumberToObject(root, "internal_min_free",
                            (double)status.internal_min_free_bytes);
    cJSON_AddNumberToObject(root, "internal_largest_block",
                            (double)status.internal_largest_free_block);
    cJSON_AddNumberToObject(root, "raw_slot_bytes_each",
                            (double)status.raw_slot_capacity_each);
    cJSON_AddNumberToObject(root, "raw_slot_capacity_each",
                            (double)status.raw_slot_capacity_each);
    cJSON_AddNumberToObject(root, "raw_slot_total_bytes",
                            (double)status.raw_slot_total_bytes);
    cJSON_AddNumberToObject(root, "jpeg_slot_capacity_each",
                            (double)status.jpeg_slot_capacity_each);
    cJSON_AddNumberToObject(root, "jpeg_slot_total_bytes",
                            (double)status.jpeg_slot_total_bytes);
    cJSON *raw_leases = cJSON_AddArrayToObject(root, "raw_slot_lease_counts");
    if (raw_leases != NULL) {
        for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_RAW_SLOTS; ++i) {
            cJSON_AddItemToArray(raw_leases,
                                 cJSON_CreateNumber(status.raw_slot_lease_counts[i]));
        }
    }
    cJSON *jpeg_leases = cJSON_AddArrayToObject(root, "jpeg_slot_lease_counts");
    if (jpeg_leases != NULL) {
        for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_JPEG_SLOTS; ++i) {
            cJSON_AddItemToArray(jpeg_leases,
                                 cJSON_CreateNumber(status.jpeg_slot_lease_counts[i]));
        }
    }
    cJSON_AddNumberToObject(root, "preview_interval_ms", status.preview_interval_ms);
    cJSON_AddBoolToObject(root, "snapshot_busy", status.snapshot_busy);
    cJSON_AddNumberToObject(root, "snapshot_request_count",
                            (double)status.snapshot_request_count);
    cJSON_AddNumberToObject(root, "snapshot_success_count",
                            (double)status.snapshot_success_count);
    cJSON_AddNumberToObject(root, "snapshot_error_count",
                            (double)status.snapshot_error_count);
    cJSON_AddNumberToObject(root, "raw_snapshot_request_count",
                            (double)status.raw_snapshot_request_count);
    cJSON_AddNumberToObject(root, "raw_snapshot_success_count",
                            (double)status.raw_snapshot_success_count);
    cJSON_AddNumberToObject(root, "jpeg_snapshot_request_count",
                            (double)status.jpeg_snapshot_request_count);
    cJSON_AddNumberToObject(root, "jpeg_snapshot_success_count",
                            (double)status.jpeg_snapshot_success_count);
    cJSON_AddNumberToObject(root, "last_snapshot_timestamp_us",
                            (double)status.last_snapshot_timestamp_us);
    cJSON_AddNumberToObject(root, "last_snapshot_total_us",
                            (double)status.last_snapshot_total_us);
    cJSON_AddNumberToObject(root, "last_snapshot_jpeg_us",
                            (double)status.last_snapshot_jpeg_us);
    cJSON_AddNumberToObject(root, "capture_us_latest", (double)status.capture_us_latest);
    cJSON_AddNumberToObject(root, "capture_us_max", (double)status.capture_us_max);
    cJSON_AddNumberToObject(root, "capture_us_average", (double)status.capture_us_average);
    cJSON_AddNumberToObject(root, "downscale_us_latest", (double)status.downscale_us_latest);
    cJSON_AddNumberToObject(root, "downscale_us_max", (double)status.downscale_us_max);
    cJSON_AddNumberToObject(root, "downscale_us_average", (double)status.downscale_us_average);
    cJSON_AddNumberToObject(root, "jpeg_us_latest", (double)status.jpeg_us_latest);
    cJSON_AddNumberToObject(root, "jpeg_us_max", (double)status.jpeg_us_max);
    cJSON_AddNumberToObject(root, "jpeg_us_average", (double)status.jpeg_us_average);
    cJSON_AddNumberToObject(root, "snapshot_total_us_latest",
                            (double)status.snapshot_total_us_latest);
    cJSON_AddNumberToObject(root, "snapshot_total_us_max",
                            (double)status.snapshot_total_us_max);
    cJSON_AddNumberToObject(root, "snapshot_total_us_average",
                            (double)status.snapshot_total_us_average);

    cJSON *timing = cJSON_AddObjectToObject(root, "timing");
    if (timing != NULL) {
        cJSON_AddNumberToObject(timing, "last_frame_timestamp_us",
                                (double)status.last_frame_timestamp_us);
        cJSON_AddNumberToObject(timing, "last_interval_us", (double)status.last_interval_us);
        cJSON_AddNumberToObject(timing, "min_interval_us", (double)status.min_interval_us);
        cJSON_AddNumberToObject(timing, "max_interval_us", (double)status.max_interval_us);
        cJSON_AddNumberToObject(timing, "avg_interval_us", (double)status.avg_interval_us);
        cJSON_AddNumberToObject(timing, "preview_interval_ms", status.preview_interval_ms);
        cJSON_AddNumberToObject(timing, "last_snapshot_total_us",
                                (double)status.last_snapshot_total_us);
        cJSON_AddNumberToObject(timing, "last_snapshot_jpeg_us",
                                (double)status.last_snapshot_jpeg_us);
        cJSON_AddNumberToObject(timing, "capture_us_latest",
                                (double)status.capture_us_latest);
        cJSON_AddNumberToObject(timing, "capture_us_max",
                                (double)status.capture_us_max);
        cJSON_AddNumberToObject(timing, "capture_us_average",
                                (double)status.capture_us_average);
        cJSON_AddNumberToObject(timing, "downscale_us_latest",
                                (double)status.downscale_us_latest);
        cJSON_AddNumberToObject(timing, "downscale_us_max",
                                (double)status.downscale_us_max);
        cJSON_AddNumberToObject(timing, "downscale_us_average",
                                (double)status.downscale_us_average);
        cJSON_AddNumberToObject(timing, "jpeg_us_latest",
                                (double)status.jpeg_us_latest);
        cJSON_AddNumberToObject(timing, "jpeg_us_max",
                                (double)status.jpeg_us_max);
        cJSON_AddNumberToObject(timing, "jpeg_us_average",
                                (double)status.jpeg_us_average);
        cJSON_AddNumberToObject(timing, "snapshot_total_us_latest",
                                (double)status.snapshot_total_us_latest);
        cJSON_AddNumberToObject(timing, "snapshot_total_us_max",
                                (double)status.snapshot_total_us_max);
        cJSON_AddNumberToObject(timing, "snapshot_total_us_average",
                                (double)status.snapshot_total_us_average);
    }

    cJSON *preview = cJSON_AddObjectToObject(root, "preview");
    if (preview != NULL) {
        cJSON_AddNumberToObject(preview, "jpeg_generation", (double)status.jpeg_generation);
        cJSON_AddNumberToObject(preview, "publish_count", (double)status.jpeg_publish_count);
        cJSON_AddNumberToObject(preview, "drop_count", (double)status.preview_drop_count);
        cJSON_AddNumberToObject(preview, "error_count", (double)status.jpeg_error_count);
        cJSON_AddNumberToObject(preview, "timestamp_us", (double)status.jpeg_timestamp_us);
        cJSON_AddNumberToObject(preview, "raw_slot_capacity_each",
                                (double)status.raw_slot_capacity_each);
        cJSON_AddNumberToObject(preview, "jpeg_slot_capacity_each",
                                (double)status.jpeg_slot_capacity_each);
        cJSON *leases = cJSON_AddArrayToObject(preview, "jpeg_slot_lease_counts");
        if (leases != NULL) {
            for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_JPEG_SLOTS; ++i) {
                cJSON_AddItemToArray(leases,
                                     cJSON_CreateNumber(status.jpeg_slot_lease_counts[i]));
            }
        }
    }
    camera_status_add_memory(root, &status);
    return http_server_send_json_response(req, root);
}

esp_err_t http_server_register_camera_routes(httpd_handle_t server)
{
    const httpd_uri_t handlers[] = {
        { .uri = "/camera-preview", .method = HTTP_GET, .handler = camera_preview_page_handler },
        { .uri = "/api/camera/frame.jpg", .method = HTTP_GET, .handler = camera_frame_handler },
        { .uri = "/api/camera/status", .method = HTTP_GET, .handler = camera_status_handler },
    };

    for (size_t i = 0; i < sizeof(handlers) / sizeof(handlers[0]); ++i) {
        esp_err_t err = httpd_register_uri_handler(server, &handlers[i]);
        if (err != ESP_OK) {
            return err;
        }
    }
    ESP_LOGI(TAG,
             "CAM_HTTP_ROUTES preview=/camera-preview frame=/api/camera/frame.jpg"
             " status=/api/camera/status mode=short_request");
    return ESP_OK;
}
