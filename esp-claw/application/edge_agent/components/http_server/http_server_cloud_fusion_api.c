// SPDX-License-Identifier: Apache-2.0
#include "http_server_priv.h"

#include <stdlib.h>
#include <string.h>
#include "rehab_cloud_fusion.h"

#define CLOUD_HTTP_TIMEOUT_MS 30000U

static void cloud_set_local_status(httpd_req_t *req,
                                   const rehab_cloud_fusion_result_t *result)
{
    if (result->ok) {
        httpd_resp_set_status(req, "200 OK");
    } else if (strcmp(result->last_error, "capture_busy") == 0 ||
               strcmp(result->last_error, "capture_failed") == 0 ||
               strcmp(result->last_error, "wifi_not_connected") == 0 ||
               strcmp(result->last_error, "time_not_synchronized") == 0) {
        httpd_resp_set_status(req, "503 Service Unavailable");
    } else if (strcmp(result->last_error, "pair_not_ready") == 0) {
        httpd_resp_set_status(req, "422 Unprocessable Entity");
    } else {
        httpd_resp_set_status(req, "502 Bad Gateway");
    }
}

static cJSON *cloud_detection_json(
    const rehab_cloud_fusion_detection_t *detection)
{
    cJSON *item = cJSON_CreateObject();
    if (item == NULL) {
        return NULL;
    }
    if (detection->has_object_id) {
        cJSON_AddNumberToObject(item, "object_id", detection->object_id);
    }
    if (detection->class_name[0]) {
        cJSON_AddStringToObject(item, "class_name", detection->class_name);
    }
    if (detection->has_confidence) {
        cJSON_AddNumberToObject(item, "confidence", detection->confidence);
    }
    if (detection->has_bbox_px) {
        cJSON *bbox = cJSON_AddObjectToObject(item, "bbox_px");
        if (bbox != NULL) {
            cJSON_AddNumberToObject(bbox, "x1", detection->bbox_px.x1);
            cJSON_AddNumberToObject(bbox, "y1", detection->bbox_px.y1);
            cJSON_AddNumberToObject(bbox, "x2", detection->bbox_px.x2);
            cJSON_AddNumberToObject(bbox, "y2", detection->bbox_px.y2);
        }
    }
    if (detection->has_center_normalized) {
        cJSON *center = cJSON_AddObjectToObject(item, "center_normalized");
        if (center != NULL) {
            cJSON_AddNumberToObject(center, "x",
                                    detection->center_normalized.x);
            cJSON_AddNumberToObject(center, "y",
                                    detection->center_normalized.y);
        }
    }
    if (detection->horizontal_zone[0]) {
        cJSON_AddStringToObject(item, "horizontal_zone",
                                detection->horizontal_zone);
    }
    cJSON *geometry = cJSON_AddObjectToObject(item, "geometry");
    if (geometry != NULL) {
        if (detection->has_distance_cm) {
            cJSON_AddNumberToObject(geometry, "distance_cm", detection->distance_cm);
        } else {
            cJSON_AddNullToObject(geometry, "distance_cm");
        }
        cJSON_AddBoolToObject(geometry, "distance_valid", detection->distance_valid);
        if (detection->has_object_height_cm) {
            cJSON_AddNumberToObject(geometry, "object_height_cm",
                                    detection->object_height_cm);
        } else {
            cJSON_AddNullToObject(geometry, "object_height_cm");
        }
        cJSON_AddBoolToObject(geometry, "object_height_valid",
                              detection->object_height_valid);
        if (detection->has_bottom_height_from_ground_cm) {
            cJSON_AddNumberToObject(geometry, "bottom_height_from_ground_cm",
                                    detection->bottom_height_from_ground_cm);
        } else {
            cJSON_AddNullToObject(geometry, "bottom_height_from_ground_cm");
        }
        cJSON_AddBoolToObject(geometry, "bottom_height_valid",
                              detection->bottom_height_valid);
        if (detection->has_geometry_quality) {
            cJSON_AddNumberToObject(geometry, "quality",
                                    detection->geometry_quality);
        }
        cJSON *tof_zone_ids = cJSON_AddArrayToObject(geometry, "tof_zone_ids");
        for (uint32_t i = 0;
             tof_zone_ids != NULL && i < detection->tof_zone_count; ++i) {
            cJSON_AddItemToArray(
                tof_zone_ids,
                cJSON_CreateNumber(detection->tof_zone_ids[i]));
        }
        if (detection->geometry_invalid_reason[0]) {
            cJSON_AddStringToObject(geometry, "invalid_reason",
                                    detection->geometry_invalid_reason);
        }
    }
    return item;
}

static cJSON *cloud_result_json(const rehab_cloud_fusion_result_t *result)
{
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) {
        return NULL;
    }
    cJSON_AddBoolToObject(root, "ok", result->ok);
    cJSON_AddStringToObject(root, "capture_id", result->capture_id);
    if (!result->ok) {
        cJSON_AddStringToObject(root, "error", result->last_error);
    }
    cJSON *local = cJSON_AddObjectToObject(root, "local");
    cJSON_AddNumberToObject(local, "image_seq", (double)result->image_seq);
    cJSON_AddNumberToObject(local, "tof_seq", (double)result->tof_seq);
    cJSON_AddNumberToObject(local, "pair_offset_ms", result->local_pair_offset_ms);
    cJSON_AddNumberToObject(local, "request_content_length",
                            (double)result->request_content_length);
    cJSON *transport = cJSON_AddObjectToObject(root, "transport");
    cJSON_AddStringToObject(transport, "url", result->endpoint_url);
    cJSON_AddNumberToObject(transport, "http_status", result->http_status);
    cJSON_AddNumberToObject(transport, "response_length",
                            (double)result->response_length);
    cJSON_AddStringToObject(root, "provider", "pc_local");
    cJSON_AddStringToObject(root, "endpoint_url", result->endpoint_url);
    cJSON_AddStringToObject(root, "endpoint_host", result->endpoint_host);
    const rehab_cloud_write_stats_t *stats = &result->write_stats;
    cJSON *write_stats = cJSON_AddObjectToObject(root, "write_stats");
    cJSON_AddNumberToObject(write_stats, "application_chunk_size",
                            stats->application_chunk_size);
    cJSON_AddNumberToObject(write_stats, "writer_segment_count",
                            stats->writer_segment_count);
    cJSON_AddNumberToObject(write_stats, "http_write_call_count",
                            stats->http_write_call_count);
    cJSON_AddNumberToObject(write_stats, "http_write_partial_count",
                            stats->http_write_partial_count);
    cJSON_AddNumberToObject(write_stats, "bytes_requested_total",
                            (double)stats->bytes_requested_total);
    cJSON_AddNumberToObject(write_stats, "bytes_written_total",
                            (double)stats->bytes_written_total);
    cJSON_AddNumberToObject(write_stats, "write_return_min",
                            stats->write_return_min);
    cJSON_AddNumberToObject(write_stats, "write_return_max",
                            stats->write_return_max);
    cJSON_AddNumberToObject(write_stats, "write_return_average",
                            (double)stats->write_return_average);
    cJSON_AddNumberToObject(write_stats, "max_single_write_block_us",
                            (double)stats->max_single_write_block_us);
    cJSON_AddNumberToObject(write_stats, "total_write_block_us",
                            (double)stats->total_write_block_us);
    cJSON *buckets = cJSON_AddObjectToObject(write_stats, "latency_buckets");
    cJSON_AddNumberToObject(buckets, "lt_1ms", stats->write_lt_1ms_count);
    cJSON_AddNumberToObject(buckets, "ms_1_to_5",
                            stats->write_1_to_5ms_count);
    cJSON_AddNumberToObject(buckets, "ms_5_to_20",
                            stats->write_5_to_20ms_count);
    cJSON_AddNumberToObject(buckets, "ms_20_to_100",
                            stats->write_20_to_100ms_count);
    cJSON_AddNumberToObject(buckets, "gt_100ms",
                            stats->write_gt_100ms_count);
    cJSON_AddNumberToObject(write_stats, "zero_write_count",
                            stats->zero_write_count);
    cJSON_AddNumberToObject(write_stats, "negative_write_count",
                            stats->negative_write_count);
    cJSON_AddNumberToObject(write_stats, "image_segment_write_calls",
                            stats->image_segment_write_calls);
    cJSON_AddNumberToObject(write_stats, "header_footer_write_calls",
                            stats->header_footer_write_calls);
    cJSON_AddNumberToObject(write_stats, "tcp_mss_config",
                            stats->tcp_mss_config);
    cJSON_AddNumberToObject(write_stats, "tcp_snd_buf_config",
                            stats->tcp_snd_buf_config);
    cJSON_AddNumberToObject(write_stats, "internal_free_before_upload",
                            stats->internal_free_before_upload);
    cJSON_AddNumberToObject(write_stats, "internal_free_after_upload",
                            stats->internal_free_after_upload);
    cJSON_AddNumberToObject(write_stats, "internal_min_before_upload",
                            stats->internal_min_before_upload);
    cJSON_AddNumberToObject(write_stats, "internal_min_after_upload",
                            stats->internal_min_after_upload);
    cJSON_AddNumberToObject(write_stats, "upload_task_priority",
                            stats->upload_task_priority);
    cJSON_AddNumberToObject(write_stats, "upload_task_core",
                            stats->upload_task_core);

    cJSON *cloud = cJSON_AddObjectToObject(root, "cloud");
    cJSON_AddStringToObject(cloud, "schema_version", result->schema_version);
    cJSON_AddStringToObject(cloud, "capture_id",
                            result->capture_id_match ? result->capture_id : "");
    cJSON_AddStringToObject(cloud, "status", result->cloud_status);
    cJSON_AddBoolToObject(cloud, "valid", result->cloud_valid);
    bool success_contract = strcmp(result->cloud_status, "ok") == 0;
    if (success_contract && result->has_pairing) {
        cJSON *pairing = cJSON_AddObjectToObject(cloud, "pairing");
        cJSON_AddNumberToObject(pairing, "image_timestamp_ms",
                                (double)result->cloud_image_timestamp_ms);
        cJSON_AddNumberToObject(pairing, "tof_timestamp_ms",
                                (double)result->cloud_tof_timestamp_ms);
        cJSON_AddNumberToObject(pairing, "offset_ms",
                                result->cloud_pair_offset_ms);
        cJSON_AddNumberToObject(pairing, "max_offset_ms",
                                result->cloud_max_pair_offset_ms);
    }
    if (success_contract) {
        cJSON *detections = cJSON_AddArrayToObject(cloud, "detections");
        uint32_t parsed = result->detection_count;
        if (parsed > REHAB_CLOUD_FUSION_MAX_DETECTIONS) {
            parsed = REHAB_CLOUD_FUSION_MAX_DETECTIONS;
        }
        for (uint32_t i = 0; detections != NULL && i < parsed; ++i) {
            cJSON *item = cloud_detection_json(&result->detections[i]);
            if (item != NULL) {
                cJSON_AddItemToArray(detections, item);
            }
        }
        cJSON *summary = cJSON_AddObjectToObject(cloud, "summary");
        cJSON_AddNumberToObject(summary, "object_count",
                                result->summary_object_count);
        cJSON_AddBoolToObject(summary, "zxl_found",
                              result->summary_zxl_found);
        cJSON_AddNumberToObject(summary, "person_count",
                                result->summary_person_count);
        cJSON *processing = cJSON_AddObjectToObject(
            cloud, "processing_time_ms");
        cJSON_AddNumberToObject(processing, "decode",
                                result->processing_decode_ms);
        cJSON_AddNumberToObject(processing, "yolo",
                                result->processing_yolo_ms);
        cJSON_AddNumberToObject(processing, "fusion",
                                result->processing_fusion_ms);
        cJSON_AddNumberToObject(processing, "total",
                                result->processing_total_ms);
    }
    if (result->has_error) {
        cJSON *error = cJSON_AddObjectToObject(cloud, "error");
        cJSON_AddStringToObject(error, "code", result->error_code);
        cJSON_AddStringToObject(error, "message", result->error_message);
        cJSON_AddBoolToObject(error, "retryable", result->error_retryable);
    }
    if (!result->response_json_valid && result->raw_response_preview[0]) {
        cJSON_AddStringToObject(root, "raw_response_preview",
                                result->raw_response_preview);
    }
    cJSON *timing = cJSON_AddObjectToObject(root, "timing");
    cJSON_AddNumberToObject(timing, "capture_us", result->capture_us);
    cJSON_AddNumberToObject(timing, "connect_us", result->connect_us);
    cJSON_AddNumberToObject(timing, "upload_us", result->upload_us);
    cJSON_AddNumberToObject(timing, "response_wait_us", result->response_wait_us);
    cJSON_AddNumberToObject(timing, "parse_us", result->parse_us);
    cJSON_AddNumberToObject(timing, "total_us", result->total_us);
    return root;
}

static esp_err_t cloud_upload_once_handler(httpd_req_t *req)
{
    rehab_cloud_fusion_result_t *result = calloc(1, sizeof(*result));
    if (result == NULL) {
        return httpd_resp_send_500(req);
    }
    (void)rehab_cloud_fusion_upload_once(result, CLOUD_HTTP_TIMEOUT_MS);
    cloud_set_local_status(req, result);
    cJSON *root = cloud_result_json(result);
    free(result);
    if (root == NULL) {
        return httpd_resp_send_500(req);
    }
    return http_server_send_json_response(req, root);
}

esp_err_t http_server_register_cloud_fusion_routes(httpd_handle_t server)
{
    const httpd_uri_t handler = {
        .uri = "/api/perception/upload-once",
        .method = HTTP_POST,
        .handler = cloud_upload_once_handler,
    };
    return httpd_register_uri_handler(server, &handler);
}
