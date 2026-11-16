// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
#include "rehab_capture.h"

#ifdef __cplusplus
extern "C" {
#endif

#define REHAB_CLOUD_FUSION_DEFAULT_URL \
    "http://192.168.137.1:8000/api/v1/vision/fuse"
#define REHAB_CLOUD_FUSION_MAX_DETECTIONS 32U
#define REHAB_CLOUD_FUSION_MAX_TOF_ZONE_IDS 64U
#define REHAB_CLOUD_FUSION_RAW_PREVIEW_MAX 512U
#define REHAB_CLOUD_UPLOAD_CHUNK_BYTES (16U * 1024U)

typedef struct {
    uint32_t application_chunk_size;
    uint32_t writer_segment_count;
    uint32_t http_write_call_count;
    uint32_t http_write_partial_count;
    uint64_t bytes_requested_total;
    uint64_t bytes_written_total;
    int32_t write_return_min;
    int32_t write_return_max;
    int64_t write_return_average;
    uint64_t max_single_write_block_us;
    uint64_t total_write_block_us;
    uint32_t write_lt_1ms_count;
    uint32_t write_1_to_5ms_count;
    uint32_t write_5_to_20ms_count;
    uint32_t write_20_to_100ms_count;
    uint32_t write_gt_100ms_count;
    uint32_t zero_write_count;
    uint32_t negative_write_count;
    uint32_t image_segment_write_calls;
    uint32_t header_footer_write_calls;
    uint32_t tcp_mss_config;
    uint32_t tcp_snd_buf_config;
    uint32_t internal_free_before_upload;
    uint32_t internal_free_after_upload;
    uint32_t internal_min_before_upload;
    uint32_t internal_min_after_upload;
    uint32_t upload_task_priority;
    int32_t upload_task_core;
} rehab_cloud_write_stats_t;

typedef struct {
    bool present;
    bool has_object_id;
    int object_id;
    char class_name[48];
    bool has_confidence;
    float confidence;
    bool has_bbox_px;
    struct {
        float x1;
        float y1;
        float x2;
        float y2;
    } bbox_px;
    bool has_center_normalized;
    struct {
        float x;
        float y;
    } center_normalized;
    char horizontal_zone[24];
    bool has_distance_cm;
    float distance_cm;
    bool distance_valid;
    bool has_object_height_cm;
    float object_height_cm;
    bool object_height_valid;
    bool has_bottom_height_from_ground_cm;
    float bottom_height_from_ground_cm;
    bool bottom_height_valid;
    bool has_geometry_quality;
    float geometry_quality;
    uint8_t tof_zone_ids[REHAB_CLOUD_FUSION_MAX_TOF_ZONE_IDS];
    uint32_t tof_zone_count;
    char geometry_invalid_reason[64];
} rehab_cloud_fusion_detection_t;

typedef struct {
    bool ok;
    bool transport_ok;
    bool response_json_valid;
    bool capture_id_match;
    int http_status;
    size_t request_content_length;
    size_t response_length;
    char endpoint_url[320];
    char endpoint_host[96];
    char raw_response_preview[REHAB_CLOUD_FUSION_RAW_PREVIEW_MAX];
    char capture_id[REHAB_CAPTURE_ID_MAX];
    uint64_t image_seq;
    uint64_t tof_seq;
    uint32_t local_pair_offset_ms;
    char schema_version[32];
    char cloud_status[24];
    bool cloud_valid;
    uint32_t detection_count;
    bool detections_truncated;
    rehab_cloud_fusion_detection_t detections[REHAB_CLOUD_FUSION_MAX_DETECTIONS];
    uint32_t summary_object_count;
    bool summary_zxl_found;
    uint32_t summary_person_count;
    uint32_t processing_decode_ms;
    uint32_t processing_yolo_ms;
    uint32_t processing_fusion_ms;
    uint32_t processing_total_ms;
    bool has_pairing;
    int64_t cloud_image_timestamp_ms;
    int64_t cloud_tof_timestamp_ms;
    uint32_t cloud_pair_offset_ms;
    uint32_t cloud_max_pair_offset_ms;
    bool has_error;
    char error_code[48];
    char error_message[160];
    bool error_retryable;
    uint32_t capture_us;
    uint32_t connect_us;
    uint32_t upload_us;
    uint32_t response_wait_us;
    uint32_t parse_us;
    uint32_t total_us;
    rehab_cloud_write_stats_t write_stats;
    char last_error[64];
} rehab_cloud_fusion_result_t;

esp_err_t rehab_cloud_fusion_init(void);
esp_err_t rehab_cloud_fusion_upload_once(
    rehab_cloud_fusion_result_t *result,
    uint32_t timeout_ms);

#ifdef __cplusplus
}
#endif
