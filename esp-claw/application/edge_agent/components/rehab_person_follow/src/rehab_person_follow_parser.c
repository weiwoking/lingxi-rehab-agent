// SPDX-License-Identifier: Apache-2.0
#include "rehab_person_follow_priv.h"

#include <math.h>
#include <string.h>

#include "cJSON.h"

static void set_error(char *error_code, size_t size, const char *value)
{
    if (error_code != NULL && size > 0U) {
        strlcpy(error_code, value, size);
    }
}

static cJSON *required(cJSON *object, const char *name)
{
    return cJSON_IsObject(object)
        ? cJSON_GetObjectItemCaseSensitive(object, name) : NULL;
}

static bool read_string(cJSON *object, const char *name, const char **out)
{
    cJSON *item = required(object, name);
    if (!cJSON_IsString(item) || item->valuestring == NULL) {
        return false;
    }
    *out = item->valuestring;
    return true;
}

static bool read_number(cJSON *object, const char *name, double *out)
{
    cJSON *item = required(object, name);
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble)) {
        return false;
    }
    *out = item->valuedouble;
    return true;
}

static bool read_optional_timing(cJSON *root,
                                 float *out_inference_ms,
                                 float *out_total_ms)
{
    cJSON *timing = required(root, "processing_time_ms");
    if (timing == NULL) {
        *out_inference_ms = 0.0f;
        *out_total_ms = 0.0f;
        return true;
    }
    double inference_ms = 0.0;
    double total_ms = 0.0;
    if (!cJSON_IsObject(timing) ||
        !read_number(timing, "inference", &inference_ms) ||
        !read_number(timing, "total", &total_ms) ||
        inference_ms < 0.0 || total_ms < 0.0 || total_ms < inference_ms ||
        inference_ms > (double)UINT32_MAX ||
        total_ms > (double)UINT32_MAX) {
        return false;
    }
    *out_inference_ms = (float)inference_ms;
    *out_total_ms = (float)total_ms;
    return true;
}

static bool read_uint32(cJSON *object, const char *name, uint32_t *out)
{
    double value = 0.0;
    if (!read_number(object, name, &value) || value < 0.0 ||
        value > UINT32_MAX || floor(value) != value) {
        return false;
    }
    *out = (uint32_t)value;
    return true;
}

static bool read_int64(cJSON *object, const char *name, int64_t *out)
{
    double value = 0.0;
    if (!read_number(object, name, &value) || value < 0.0 ||
        value > (double)INT64_MAX || floor(value) != value) {
        return false;
    }
    *out = (int64_t)value;
    return true;
}

static bool parse_detection(cJSON *item,
                            rehab_person_follow_detection_t *out)
{
    const char *class_name = NULL;
    double confidence = 0.0;
    double area = 0.0;
    cJSON *bbox = required(item, "bbox_px");
    cJSON *center = required(item, "center_normalized");
    double x1 = 0.0;
    double y1 = 0.0;
    double x2 = 0.0;
    double y2 = 0.0;
    double center_x = 0.0;
    double center_y = 0.0;
    if (!read_string(item, "class_name", &class_name) ||
        strcmp(class_name, PERSON_FOLLOW_TARGET_CLASS) != 0 ||
        !read_number(item, "confidence", &confidence) ||
        confidence < 0.0 || confidence > 1.0 ||
        !read_number(bbox, "x1", &x1) ||
        !read_number(bbox, "y1", &y1) ||
        !read_number(bbox, "x2", &x2) ||
        !read_number(bbox, "y2", &y2) ||
        x1 < 0.0 || y1 < 0.0 || x2 > PERSON_FOLLOW_IMAGE_WIDTH ||
        y2 > PERSON_FOLLOW_IMAGE_HEIGHT || x2 <= x1 || y2 <= y1 ||
        !read_number(center, "x", &center_x) ||
        !read_number(center, "y", &center_y) ||
        center_x < 0.0 || center_x > 1.0 ||
        center_y < 0.0 || center_y > 1.0 ||
        !read_number(item, "area_normalized", &area) ||
        area <= 0.0 || area > 1.0) {
        return false;
    }
    out->confidence = (float)confidence;
    out->x1 = (float)x1;
    out->y1 = (float)y1;
    out->x2 = (float)x2;
    out->y2 = (float)y2;
    out->center_x = (float)center_x;
    out->center_y = (float)center_y;
    out->area_normalized = (float)area;
    return true;
}

esp_err_t rehab_person_follow_parse_response(
    const char *json,
    const rehab_person_follow_request_meta_t *expected,
    uint32_t max_detections,
    rehab_person_follow_response_t *out_response,
    char *error_code,
    size_t error_code_size)
{
    if (json == NULL || expected == NULL || out_response == NULL ||
        max_detections == 0U ||
        max_detections > PERSON_FOLLOW_MAX_DETECTIONS_LIMIT) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_response, 0, sizeof(*out_response));
    set_error(error_code, error_code_size, "response_invalid");
    cJSON *root = cJSON_Parse(json);
    if (!cJSON_IsObject(root)) {
        cJSON_Delete(root);
        set_error(error_code, error_code_size, "response_json_invalid");
        return ESP_ERR_INVALID_RESPONSE;
    }

    const char *schema = NULL;
    const char *frame_id = NULL;
    const char *clock = NULL;
    const char *target_class = NULL;
    uint32_t generation = 0U;
    uint32_t width = 0U;
    uint32_t height = 0U;
    int64_t timestamp = 0;
    cJSON *target_found = required(root, "target_found");
    cJSON *detections = required(root, "detections");
    bool valid_header = read_string(root, "schema_version", &schema) &&
        strcmp(schema, "person_follow.v1") == 0 &&
        read_string(root, "frame_id", &frame_id) &&
        strcmp(frame_id, expected->frame_id) == 0 &&
        read_uint32(root, "frame_generation", &generation) &&
        generation == expected->generation &&
        read_int64(root, "capture_timestamp_us", &timestamp) &&
        timestamp == expected->capture_timestamp_us &&
        read_string(root, "capture_clock_domain", &clock) &&
        strcmp(clock, "esp_timer_monotonic") == 0 &&
        read_uint32(root, "image_width", &width) &&
        width == PERSON_FOLLOW_IMAGE_WIDTH &&
        read_uint32(root, "image_height", &height) &&
        height == PERSON_FOLLOW_IMAGE_HEIGHT &&
        read_string(root, "target_class", &target_class) &&
        strcmp(target_class, PERSON_FOLLOW_TARGET_CLASS) == 0 &&
        cJSON_IsBool(target_found) && cJSON_IsArray(detections) &&
        read_optional_timing(root, &out_response->pc_inference_ms,
                             &out_response->pc_total_ms);
    if (!valid_header) {
        cJSON_Delete(root);
        set_error(error_code, error_code_size, "response_echo_or_schema_invalid");
        return ESP_ERR_INVALID_RESPONSE;
    }

    int count = cJSON_GetArraySize(detections);
    bool found = cJSON_IsTrue(target_found);
    if (count < 0 || (uint32_t)count > max_detections ||
        found != (count > 0)) {
        cJSON_Delete(root);
        set_error(error_code, error_code_size,
                  (uint32_t)(count < 0 ? 0 : count) > max_detections
                      ? "too_many_detections" : "target_found_mismatch");
        return count >= 0 && (uint32_t)count > max_detections
            ? ESP_ERR_INVALID_SIZE : ESP_ERR_INVALID_RESPONSE;
    }

    for (int i = 0; i < count; ++i) {
        if (!parse_detection(cJSON_GetArrayItem(detections, i),
                             &out_response->detections[i])) {
            cJSON_Delete(root);
            set_error(error_code, error_code_size, "detection_invalid");
            return ESP_ERR_INVALID_RESPONSE;
        }
    }
    out_response->target_found = found;
    out_response->detection_count = (uint32_t)count;
    cJSON_Delete(root);
    set_error(error_code, error_code_size, "none");
    return ESP_OK;
}

void rehab_person_follow_parse_error_code(
    const char *json, char *error_code, size_t error_code_size)
{
    set_error(error_code, error_code_size, "http_status_error");
    if (json == NULL) {
        return;
    }
    cJSON *root = cJSON_Parse(json);
    cJSON *error = required(root, "error");
    const char *code = NULL;
    if (read_string(root, "schema_version", &code) &&
        strcmp(code, "person_follow_error.v1") == 0 &&
        read_string(error, "code", &code)) {
        set_error(error_code, error_code_size, code);
    }
    cJSON_Delete(root);
}
