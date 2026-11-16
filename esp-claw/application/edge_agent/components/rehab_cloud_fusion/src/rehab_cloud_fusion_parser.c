// SPDX-License-Identifier: Apache-2.0
#include "rehab_cloud_fusion_priv.h"

#include <float.h>
#include <limits.h>
#include <math.h>
#include <string.h>

_Static_assert(REHAB_CLOUD_FUSION_MAX_DETECTIONS == 32U,
               "P4 detection boundary must remain 32");
_Static_assert(REHAB_CLOUD_FUSION_MAX_TOF_ZONE_IDS == 64U,
               "8x8 ToF zone capacity must remain 64");

static cJSON *field(const cJSON *object, const char *name)
{
    return cJSON_GetObjectItemCaseSensitive(object, name);
}

static bool read_string(const cJSON *object, const char *name,
                        char *out, size_t out_size)
{
    cJSON *item = field(object, name);
    if (!cJSON_IsString(item) || item->valuestring == NULL) {
        return false;
    }
    strlcpy(out, item->valuestring, out_size);
    return true;
}

static bool read_u32(const cJSON *object, const char *name, uint32_t *out)
{
    cJSON *item = field(object, name);
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) ||
        item->valuedouble < 0 || item->valuedouble > UINT32_MAX ||
        trunc(item->valuedouble) != item->valuedouble) {
        return false;
    }
    *out = (uint32_t)item->valuedouble;
    return true;
}

static bool read_int(const cJSON *object, const char *name, int *out)
{
    cJSON *item = field(object, name);
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) ||
        item->valuedouble < INT_MIN || item->valuedouble > INT_MAX ||
        trunc(item->valuedouble) != item->valuedouble) {
        return false;
    }
    *out = (int)item->valuedouble;
    return true;
}

static bool read_i64(const cJSON *object, const char *name, int64_t *out)
{
    cJSON *item = field(object, name);
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) ||
        trunc(item->valuedouble) != item->valuedouble) {
        return false;
    }
    *out = (int64_t)item->valuedouble;
    return true;
}

static bool read_bool(const cJSON *object, const char *name, bool *out)
{
    cJSON *item = field(object, name);
    if (!cJSON_IsBool(item)) {
        return false;
    }
    *out = cJSON_IsTrue(item);
    return true;
}

static bool read_float(const cJSON *object, const char *name, float *out)
{
    cJSON *item = field(object, name);
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) ||
        item->valuedouble < -FLT_MAX || item->valuedouble > FLT_MAX) {
        return false;
    }
    *out = (float)item->valuedouble;
    return true;
}

static bool read_nullable_float(const cJSON *object, const char *name,
                                bool *has_value, float *out)
{
    cJSON *item = field(object, name);
    if (cJSON_IsNull(item)) {
        *has_value = false;
        return true;
    }
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) ||
        item->valuedouble < -FLT_MAX || item->valuedouble > FLT_MAX) {
        return false;
    }
    *has_value = true;
    *out = (float)item->valuedouble;
    return true;
}

static bool read_bbox(const cJSON *object,
                      rehab_cloud_fusion_detection_t *out)
{
    cJSON *bbox = field(object, "bbox_px");
    if (!cJSON_IsObject(bbox)) {
        return false;
    }
    return read_float(bbox, "x1", &out->bbox_px.x1) &&
        read_float(bbox, "y1", &out->bbox_px.y1) &&
        read_float(bbox, "x2", &out->bbox_px.x2) &&
        read_float(bbox, "y2", &out->bbox_px.y2);
}

static bool read_center(const cJSON *object,
                        rehab_cloud_fusion_detection_t *out)
{
    cJSON *center = field(object, "center_normalized");
    if (!cJSON_IsObject(center)) {
        return false;
    }
    return read_float(center, "x", &out->center_normalized.x) &&
        read_float(center, "y", &out->center_normalized.y);
}

static bool read_tof_zone_ids(const cJSON *geometry,
                              rehab_cloud_fusion_detection_t *out)
{
    cJSON *array = field(geometry, "tof_zone_ids");
    if (!cJSON_IsArray(array)) {
        return false;
    }
    int count = cJSON_GetArraySize(array);
    if (count < 0 || count > (int)REHAB_CLOUD_FUSION_MAX_TOF_ZONE_IDS) {
        return false;
    }
    for (int i = 0; i < count; ++i) {
        cJSON *item = cJSON_GetArrayItem(array, i);
        if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) ||
            item->valuedouble < 0 || item->valuedouble >= 64 ||
            trunc(item->valuedouble) != item->valuedouble) {
            return false;
        }
        out->tof_zone_ids[i] = (uint8_t)item->valuedouble;
    }
    out->tof_zone_count = (uint32_t)count;
    return true;
}

static bool parse_detection(const cJSON *item,
                            rehab_cloud_fusion_detection_t *out)
{
    memset(out, 0, sizeof(*out));
    if (!cJSON_IsObject(item)) {
        return false;
    }
    out->has_object_id = read_int(item, "object_id", &out->object_id);
    bool class_valid = read_string(item, "class_name", out->class_name,
                                   sizeof(out->class_name));
    out->has_confidence = read_float(item, "confidence", &out->confidence);
    out->has_bbox_px = read_bbox(item, out);
    out->has_center_normalized = read_center(item, out);
    bool zone_valid = read_string(item, "horizontal_zone",
                                  out->horizontal_zone,
                                  sizeof(out->horizontal_zone));
    cJSON *geometry = field(item, "geometry");
    if (!cJSON_IsObject(geometry)) {
        return false;
    }
    bool distance_value_valid = read_nullable_float(
        geometry, "distance_cm", &out->has_distance_cm, &out->distance_cm);
    bool distance_flag_valid = read_bool(
        geometry, "distance_valid", &out->distance_valid);
    bool height_value_valid = read_nullable_float(
        geometry, "object_height_cm", &out->has_object_height_cm,
        &out->object_height_cm);
    bool height_flag_valid = read_bool(
        geometry, "object_height_valid", &out->object_height_valid);
    bool bottom_value_valid = read_nullable_float(
        geometry, "bottom_height_from_ground_cm",
        &out->has_bottom_height_from_ground_cm,
        &out->bottom_height_from_ground_cm);
    bool bottom_flag_valid = read_bool(
        geometry, "bottom_height_valid", &out->bottom_height_valid);
    out->has_geometry_quality = read_float(
        geometry, "quality", &out->geometry_quality);
    bool quality_valid = out->has_geometry_quality &&
        out->geometry_quality >= 0.0f && out->geometry_quality <= 1.0f;
    bool tof_ids_valid = read_tof_zone_ids(geometry, out);
    cJSON *invalid_reason = field(geometry, "invalid_reason");
    bool invalid_reason_valid = invalid_reason == NULL ||
        read_string(geometry, "invalid_reason", out->geometry_invalid_reason,
                    sizeof(out->geometry_invalid_reason));

    bool valid = out->has_object_id && class_valid && out->has_confidence &&
        out->confidence >= 0.0f && out->confidence <= 1.0f &&
        out->has_bbox_px && out->has_center_normalized && zone_valid &&
        distance_value_valid && distance_flag_valid &&
        height_value_valid && height_flag_valid &&
        bottom_value_valid && bottom_flag_valid && quality_valid &&
        tof_ids_valid && invalid_reason_valid;
    if ((out->distance_valid && !out->has_distance_cm) ||
        (out->object_height_valid && !out->has_object_height_cm) ||
        (out->bottom_height_valid &&
         !out->has_bottom_height_from_ground_cm)) {
        valid = false;
    }
    out->present = valid;
    return valid;
}

esp_err_t rehab_cloud_fusion_parse_response(
    const char *json, const char *expected_capture_id,
    rehab_cloud_fusion_result_t *result)
{
    cJSON *root = cJSON_Parse(json);
    if (root == NULL || !cJSON_IsObject(root)) {
        cJSON_Delete(root);
        strlcpy(result->last_error, "response_json_invalid",
                sizeof(result->last_error));
        return ESP_ERR_INVALID_RESPONSE;
    }
    result->response_json_valid = true;
    read_string(root, "schema_version", result->schema_version,
                sizeof(result->schema_version));
    char response_capture_id[REHAB_CAPTURE_ID_MAX] = {0};
    read_string(root, "capture_id", response_capture_id,
                sizeof(response_capture_id));
    result->capture_id_match = strcmp(response_capture_id,
                                      expected_capture_id) == 0;
    read_string(root, "status", result->cloud_status,
                sizeof(result->cloud_status));
    bool has_valid = read_bool(root, "valid", &result->cloud_valid);

    cJSON *pairing = field(root, "pairing");
    result->has_pairing = cJSON_IsObject(pairing) &&
        read_i64(pairing, "image_timestamp_ms", &result->cloud_image_timestamp_ms) &&
        read_i64(pairing, "tof_timestamp_ms", &result->cloud_tof_timestamp_ms) &&
        read_u32(pairing, "offset_ms", &result->cloud_pair_offset_ms) &&
        read_u32(pairing, "max_offset_ms", &result->cloud_max_pair_offset_ms);

    cJSON *detections = field(root, "detections");
    bool detections_valid = cJSON_IsArray(detections);
    if (detections_valid) {
        int count = cJSON_GetArraySize(detections);
        result->detection_count = count < 0 ? 0U : (uint32_t)count;
        result->detections_truncated =
            result->detection_count > REHAB_CLOUD_FUSION_MAX_DETECTIONS;
        uint32_t parsed = result->detection_count;
        if (parsed > REHAB_CLOUD_FUSION_MAX_DETECTIONS) {
            parsed = REHAB_CLOUD_FUSION_MAX_DETECTIONS;
        }
        for (uint32_t i = 0; i < parsed; ++i) {
            if (!parse_detection(cJSON_GetArrayItem(detections, (int)i),
                                 &result->detections[i])) {
                detections_valid = false;
                break;
            }
        }
    }

    cJSON *summary = field(root, "summary");
    bool summary_valid = cJSON_IsObject(summary) &&
        read_u32(summary, "object_count", &result->summary_object_count) &&
        read_bool(summary, "zxl_found", &result->summary_zxl_found) &&
        read_u32(summary, "person_count", &result->summary_person_count);
    cJSON *timing = field(root, "processing_time_ms");
    bool timing_valid = cJSON_IsObject(timing) &&
        read_u32(timing, "decode", &result->processing_decode_ms) &&
        read_u32(timing, "yolo", &result->processing_yolo_ms) &&
        read_u32(timing, "fusion", &result->processing_fusion_ms) &&
        read_u32(timing, "total", &result->processing_total_ms);

    bool error_valid = false;
    cJSON *error = field(root, "error");
    if (cJSON_IsObject(error)) {
        result->has_error = true;
        error_valid =
            read_string(error, "code", result->error_code,
                        sizeof(result->error_code)) &&
            read_string(error, "message", result->error_message,
                        sizeof(result->error_message)) &&
            read_bool(error, "retryable", &result->error_retryable);
    }

    esp_err_t err = ESP_OK;
    if (strcmp(result->schema_version, "vision_fusion.v1") != 0) {
        strlcpy(result->last_error, "schema_version_mismatch",
                sizeof(result->last_error));
        err = ESP_ERR_INVALID_RESPONSE;
    } else if (!result->capture_id_match) {
        strlcpy(result->last_error, "capture_id_mismatch",
                sizeof(result->last_error));
        err = ESP_ERR_INVALID_RESPONSE;
    } else if (strcmp(result->cloud_status, "error") == 0) {
        if (!has_valid || result->cloud_valid || !error_valid) {
            strlcpy(result->last_error, "response_json_invalid",
                    sizeof(result->last_error));
            err = ESP_ERR_INVALID_RESPONSE;
        } else {
            strlcpy(result->last_error, "cloud_status_error",
                    sizeof(result->last_error));
            err = ESP_FAIL;
        }
    } else if (strcmp(result->cloud_status, "ok") != 0 || result->has_error) {
        strlcpy(result->last_error, "cloud_status_error",
                sizeof(result->last_error));
        err = ESP_ERR_INVALID_RESPONSE;
    } else if (!has_valid || !result->cloud_valid) {
        strlcpy(result->last_error, "cloud_valid_false",
                sizeof(result->last_error));
        err = ESP_FAIL;
    } else if (!result->has_pairing || !detections_valid ||
               !summary_valid || !timing_valid) {
        strlcpy(result->last_error, "response_json_invalid",
                sizeof(result->last_error));
        err = ESP_ERR_INVALID_RESPONSE;
    } else {
        uint32_t delta = result->cloud_pair_offset_ms > result->local_pair_offset_ms
            ? result->cloud_pair_offset_ms - result->local_pair_offset_ms
            : result->local_pair_offset_ms - result->cloud_pair_offset_ms;
        if (delta > 1U) {
            strlcpy(result->last_error, "cloud_pairing_mismatch",
                    sizeof(result->last_error));
            err = ESP_ERR_INVALID_RESPONSE;
        }
    }
    cJSON_Delete(root);
    return err;
}
