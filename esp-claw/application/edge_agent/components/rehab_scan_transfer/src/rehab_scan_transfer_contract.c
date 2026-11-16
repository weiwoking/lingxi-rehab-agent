// SPDX-License-Identifier: Apache-2.0
#include "rehab_scan_transfer_priv.h"

#include <ctype.h>
#include <math.h>
#include <string.h>

static const cJSON *field(const cJSON *object, const char *name)
{
    return cJSON_GetObjectItemCaseSensitive(object, name);
}

static bool bounded_string(const cJSON *item, size_t minimum, size_t maximum)
{
    if (!cJSON_IsString(item) || item->valuestring == NULL) return false;
    size_t length = strlen(item->valuestring);
    return length >= minimum && length <= maximum;
}

static bool string_equals(const cJSON *item, const char *value)
{
    return cJSON_IsString(item) && item->valuestring != NULL &&
           strcmp(item->valuestring, value) == 0;
}

static bool one_of(const cJSON *item, const char *const *values, size_t count)
{
    if (!cJSON_IsString(item) || item->valuestring == NULL) return false;
    for (size_t i = 0; i < count; ++i) {
        if (strcmp(item->valuestring, values[i]) == 0) return true;
    }
    return false;
}

static bool object_exact(const cJSON *object,
                         const char *const *keys, size_t count)
{
    if (!cJSON_IsObject(object)) return false;
    const cJSON *child = NULL;
    size_t seen = 0;
    cJSON_ArrayForEach(child, object) {
        bool known = false;
        for (size_t i = 0; i < count; ++i) {
            if (child->string && strcmp(child->string, keys[i]) == 0) {
                known = true;
                break;
            }
        }
        if (!known) return false;
        ++seen;
    }
    if (seen != count) return false;
    for (size_t i = 0; i < count; ++i) {
        if (field(object, keys[i]) == NULL) return false;
    }
    return true;
}

static bool valid_utf8(const uint8_t *bytes, size_t length)
{
    for (size_t i = 0; i < length;) {
        uint8_t c = bytes[i++];
        if (c < 0x80) continue;
        unsigned n;
        uint32_t code;
        if ((c & 0xE0) == 0xC0) { n = 1; code = c & 0x1F; if (code < 2) return false; }
        else if ((c & 0xF0) == 0xE0) { n = 2; code = c & 0x0F; }
        else if ((c & 0xF8) == 0xF0) { n = 3; code = c & 7; if (code > 4) return false; }
        else return false;
        if (i + n > length) return false;
        for (unsigned j = 0; j < n; ++j) {
            uint8_t next = bytes[i++];
            if ((next & 0xC0) != 0x80) return false;
            code = (code << 6) | (next & 0x3F);
        }
        if ((n == 2 && code < 0x800) || (n == 3 && code < 0x10000) ||
            (code >= 0xD800 && code <= 0xDFFF) || code > 0x10FFFF) return false;
    }
    return true;
}

static cJSON *parse_strict(const char *json, size_t length)
{
    if (json == NULL || length == 0 || !valid_utf8((const uint8_t *)json, length)) return NULL;
    const char *end = NULL;
    cJSON *root = cJSON_ParseWithLengthOpts(json, length, &end, 0);
    if (root != NULL) {
        while (end < json + length && isspace((unsigned char)*end)) ++end;
        if (end != json + length) {
            cJSON_Delete(root);
            root = NULL;
        }
    }
    return root;
}

static bool all_numbers_finite(const cJSON *item)
{
    if (cJSON_IsNumber(item) && !isfinite(item->valuedouble)) return false;
    const cJSON *child = NULL;
    cJSON_ArrayForEach(child, item) {
        if (!all_numbers_finite(child)) return false;
    }
    return true;
}

static bool valid_service_id(const char *value)
{
    if (value == NULL || strlen(value) != 36 || strncmp(value, "svc_", 4) != 0) return false;
    for (size_t i = 4; i < 36; ++i) {
        if (!isdigit((unsigned char)value[i]) && !(value[i] >= 'a' && value[i] <= 'f')) return false;
    }
    return true;
}

esp_err_t rehab_scan_transfer_parse_job(const char *json, size_t length,
                                        const char *expected_scan_id,
                                        rehab_scan_transfer_job_t *out_job)
{
    if (json == NULL || expected_scan_id == NULL || out_job == NULL) return ESP_ERR_INVALID_ARG;
    memset(out_job, 0, sizeof(*out_job));
    cJSON *root = parse_strict(json, length);
    if (!cJSON_IsObject(root) || !all_numbers_finite(root) ||
        !string_equals(field(root, "schema_version"), "deltar_scan_service_job.v1") ||
        !bounded_string(field(root, "service_job_id"), 36, 36) ||
        !valid_service_id(field(root, "service_job_id")->valuestring) ||
        !string_equals(field(root, "scan_id"), expected_scan_id)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    static const char *const states[] = {"accepted", "running", "completed", "failed"};
    if (!one_of(field(root, "status"), states, 4)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    const cJSON *pipeline = field(root, "pipeline_job_id");
    if (!cJSON_IsNull(pipeline) && !bounded_string(pipeline, 1,
            REHAB_SCAN_TRANSFER_PIPELINE_JOB_ID_MAX - 1U)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    const cJSON *pipeline_status = field(root, "pipeline_result_status");
    static const char *const result_states[] = {
        "success", "partial_success", "rejected", "failed",
    };
    if (pipeline_status != NULL && !cJSON_IsNull(pipeline_status) &&
        !one_of(pipeline_status, result_states, 4)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    strlcpy(out_job->service_job_id, field(root, "service_job_id")->valuestring,
            sizeof(out_job->service_job_id));
    strlcpy(out_job->scan_id, expected_scan_id, sizeof(out_job->scan_id));
    strlcpy(out_job->state, field(root, "status")->valuestring, sizeof(out_job->state));
    if (cJSON_IsString(pipeline)) {
        strlcpy(out_job->pipeline_job_id, pipeline->valuestring,
                sizeof(out_job->pipeline_job_id));
    }
    if (cJSON_IsString(pipeline_status)) {
        strlcpy(out_job->pipeline_result_status, pipeline_status->valuestring,
                sizeof(out_job->pipeline_result_status));
    }
    cJSON_Delete(root);
    return ESP_OK;
}

bool rehab_scan_transfer_parse_error_code(const char *json, size_t length,
                                          char *out_code, size_t out_size)
{
    if (json == NULL || out_code == NULL || out_size == 0) return false;
    cJSON *root = parse_strict(json, length);
    const cJSON *code = field(root, "error_code");
    bool valid = cJSON_IsObject(root) &&
        string_equals(field(root, "schema_version"), "deltar_scan_service_error.v1") &&
        bounded_string(code, 3, REHAB_SCAN_TRANSFER_ERROR_CODE_MAX - 1U) &&
        strncmp(code->valuestring, "E_", 2) == 0;
    if (valid) strlcpy(out_code, code->valuestring, out_size);
    cJSON_Delete(root);
    return valid;
}

static bool number_or_null(const cJSON *item)
{
    return cJSON_IsNull(item) || (cJSON_IsNumber(item) && isfinite(item->valuedouble));
}

static bool validate_visibility(const cJSON *value)
{
    static const char *const keys[] = {
        "status", "top_visible", "bottom_visible", "full_extent_visible",
    };
    static const char *const states[] = {"complete", "partial", "unknown"};
    return object_exact(value, keys, 4) && one_of(field(value, "status"), states, 3) &&
        cJSON_IsBool(field(value, "top_visible")) &&
        cJSON_IsBool(field(value, "bottom_visible")) &&
        cJSON_IsBool(field(value, "full_extent_visible"));
}

static bool validate_distance(const cJSON *value)
{
    static const char *const keys[] = {"value_m", "semantics", "valid", "source_frame_id"};
    if (!object_exact(value, keys, 4) || !number_or_null(field(value, "value_m")) ||
        !cJSON_IsBool(field(value, "valid")) ||
        !bounded_string(field(value, "source_frame_id"), 1, 128)) return false;
    const cJSON *semantics = field(value, "semantics");
    if (!cJSON_IsNull(semantics) && !bounded_string(semantics, 1, 128)) return false;
    bool valid = cJSON_IsTrue(field(value, "valid"));
    return !valid || (cJSON_IsNumber(field(value, "value_m")) &&
                      field(value, "value_m")->valuedouble >= 0);
}

static bool validate_height(const cJSON *value)
{
    static const char *const keys[] = {"value_m", "available", "absolute_valid"};
    if (!object_exact(value, keys, 3) || !number_or_null(field(value, "value_m")) ||
        !cJSON_IsBool(field(value, "available")) ||
        !cJSON_IsBool(field(value, "absolute_valid"))) return false;

    const cJSON *height = field(value, "value_m");
    if (cJSON_IsNumber(height) &&
        (!isfinite(height->valuedouble) || height->valuedouble < 0.0)) {
        return false;
    }

    bool available = cJSON_IsTrue(field(value, "available"));
    bool absolute = cJSON_IsTrue(field(value, "absolute_valid"));
    return !absolute || (available && cJSON_IsNumber(height));
}

static bool validate_object_summary(const cJSON *object)
{
    static const char *const class_scopes[] = {
        "core_now", "supported_but_not_core", "future_custom_class",
    };
    static const char *const placements[] = {
        "ground", "low", "tabletop", "elevated", "unknown",
    };
    const cJSON *rejection = field(object, "rejection_reason");
    return cJSON_IsObject(object) &&
        bounded_string(field(object, "scan_object_id"), 1, 128) &&
        bounded_string(field(object, "class_name"), 1, 128) &&
        one_of(field(object, "class_scope"), class_scopes, 3) &&
        validate_visibility(field(object, "visibility")) &&
        validate_distance(field(object, "distance")) &&
        validate_height(field(object, "physical_height")) &&
        validate_height(field(object, "bottom_height")) &&
        one_of(field(object, "placement_level"), placements, 5) &&
        (cJSON_IsNull(rejection) || bounded_string(rejection, 1, 512));
}

esp_err_t rehab_scan_transfer_validate_result(const char *json, size_t length,
                                              const char *scan_id,
                                              const char *pipeline_job_id,
                                              uint32_t max_objects)
{
    if (json == NULL || scan_id == NULL || pipeline_job_id == NULL ||
        pipeline_job_id[0] == '\0') return ESP_ERR_INVALID_ARG;
    cJSON *root = parse_strict(json, length);
    static const char *const keys[] = {
        "schema_version", "status", "scan_id", "job_id", "input", "models",
        "profiles", "objects", "warnings", "errors", "aggregation",
        "provenance", "processing",
    };
    static const char *const states[] = {"success", "partial_success", "rejected"};
    bool valid = object_exact(root, keys, 13) && all_numbers_finite(root) &&
        string_equals(field(root, "schema_version"), "deltar_scan_result.v1.1") &&
        one_of(field(root, "status"), states, 3) &&
        string_equals(field(root, "scan_id"), scan_id) &&
        string_equals(field(root, "job_id"), pipeline_job_id) &&
        cJSON_IsObject(field(root, "input")) && cJSON_IsObject(field(root, "models")) &&
        cJSON_IsObject(field(root, "profiles")) && cJSON_IsArray(field(root, "objects")) &&
        cJSON_IsArray(field(root, "warnings")) && cJSON_IsArray(field(root, "errors")) &&
        cJSON_IsObject(field(root, "aggregation")) &&
        cJSON_IsObject(field(root, "provenance")) && cJSON_IsObject(field(root, "processing"));
    if (valid) {
        int count = cJSON_GetArraySize(field(root, "objects"));
        valid = count >= 0 && (uint32_t)count <= max_objects;
        const cJSON *object = NULL;
        cJSON_ArrayForEach(object, field(root, "objects")) {
            if (!validate_object_summary(object)) {
                valid = false;
                break;
            }
        }
    }
    cJSON_Delete(root);
    return valid ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}

static bool lowercase_sha256(const cJSON *item)
{
    if (!bounded_string(item, 64, 64)) return false;
    for (size_t index = 0; index < 64; ++index) {
        char value = item->valuestring[index];
        if (!isdigit((unsigned char)value) &&
            !(value >= 'a' && value <= 'f')) {
            return false;
        }
    }
    return true;
}

esp_err_t rehab_scan_transfer_parse_decision_status(
    const char *json, size_t length, const char *expected_scan_id,
    rehab_scan_transfer_decision_status_t *out_status)
{
    if (json == NULL || expected_scan_id == NULL || out_status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_status, 0, sizeof(*out_status));
    cJSON *root = parse_strict(json, length);
    static const char *const states[] = {
        "not_found", "queued", "processing", "decision_ready", "failed",
    };
    const cJSON *state = field(root, "status");
    const cJSON *ready = field(root, "package_ready");
    const cJSON *package_length = field(root, "package_length");
    const cJSON *package_sha256 = field(root, "package_sha256");
    bool valid = cJSON_IsObject(root) && all_numbers_finite(root) &&
        string_equals(field(root, "scan_id"), expected_scan_id) &&
        one_of(state, states, sizeof(states) / sizeof(states[0])) &&
        cJSON_IsBool(ready);
    bool is_ready = valid && cJSON_IsTrue(ready);
    if (valid && is_ready) {
        valid = string_equals(state, "decision_ready") &&
            cJSON_IsNumber(package_length) &&
            package_length->valuedouble >= 1.0 &&
            package_length->valuedouble <=
                REHAB_SCAN_TRANSFER_DECISION_MAX_BYTES &&
            floor(package_length->valuedouble) ==
                package_length->valuedouble &&
            lowercase_sha256(package_sha256);
    } else if (valid) {
        valid = cJSON_IsNull(package_length) &&
            cJSON_IsNull(package_sha256) &&
            !string_equals(state, "decision_ready");
    }
    if (!valid) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    strlcpy(out_status->scan_id, expected_scan_id,
            sizeof(out_status->scan_id));
    strlcpy(out_status->state, state->valuestring,
            sizeof(out_status->state));
    out_status->package_ready = is_ready;
    if (is_ready) {
        out_status->package_length = (size_t)package_length->valuedouble;
        strlcpy(out_status->package_sha256,
                package_sha256->valuestring,
                sizeof(out_status->package_sha256));
    }
    const cJSON *error_code = field(root, "error_code");
    if (cJSON_IsString(error_code) &&
        bounded_string(error_code, 3,
            REHAB_SCAN_TRANSFER_ERROR_CODE_MAX - 1U)) {
        strlcpy(out_status->error_code, error_code->valuestring,
                sizeof(out_status->error_code));
    } else if (!cJSON_IsNull(error_code)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    cJSON_Delete(root);
    return ESP_OK;
}

static bool decision_class_allowed(const cJSON *item)
{
    static const char *const classes[] = {
        "cup", "bottle", "book", "sports ball"
    };
    return one_of(item, classes, sizeof(classes) / sizeof(classes[0]));
}

static bool decision_bbox_valid(const cJSON *bbox)
{
    if (!cJSON_IsArray(bbox) || cJSON_GetArraySize(bbox) != 4) return false;
    const cJSON *x1 = cJSON_GetArrayItem(bbox, 0);
    const cJSON *y1 = cJSON_GetArrayItem(bbox, 1);
    const cJSON *x2 = cJSON_GetArrayItem(bbox, 2);
    const cJSON *y2 = cJSON_GetArrayItem(bbox, 3);
    return cJSON_IsNumber(x1) && cJSON_IsNumber(y1) &&
        cJSON_IsNumber(x2) && cJSON_IsNumber(y2) &&
        isfinite(x1->valuedouble) && isfinite(y1->valuedouble) &&
        isfinite(x2->valuedouble) && isfinite(y2->valuedouble) &&
        x1->valuedouble >= 0.0 && x1->valuedouble < x2->valuedouble &&
        x2->valuedouble <= 640.0 &&
        y1->valuedouble >= 0.0 && y1->valuedouble < y2->valuedouble &&
        y2->valuedouble <= 360.0;
}

static bool decision_frame_reference_valid(const cJSON *object)
{
    const cJSON *sequence = field(object, "source_frame_sequence");
    const cJSON *frame_id = field(object, "source_frame_id");
    if (!cJSON_IsNumber(sequence) ||
        floor(sequence->valuedouble) != sequence->valuedouble ||
        sequence->valuedouble < 0.0 || sequence->valuedouble > 10.0 ||
        !bounded_string(frame_id, 12, 12)) {
        return false;
    }
    char expected[16];
    snprintf(expected, sizeof(expected), "frame_%06u",
             (unsigned)sequence->valuedouble);
    return strcmp(frame_id->valuestring, expected) == 0;
}

esp_err_t rehab_scan_transfer_validate_decision_package(
    const char *json, size_t length, const char *scan_id)
{
    if (json == NULL || scan_id == NULL) return ESP_ERR_INVALID_ARG;
    if (length == 0 ||
        length > REHAB_SCAN_TRANSFER_DECISION_MAX_BYTES) {
        return ESP_ERR_INVALID_SIZE;
    }
    cJSON *root = parse_strict(json, length);
    const cJSON *source = field(root, "source");
    const cJSON *objects = field(root, "objects");
    bool valid = cJSON_IsObject(root) && all_numbers_finite(root) &&
        string_equals(field(root, "schema_version"),
                      "stage8b4a_p4_decision_package.v1") &&
        string_equals(field(root, "scan_id"), scan_id) &&
        string_equals(field(root, "status"), "decision_ready") &&
        cJSON_IsObject(source) &&
        cJSON_IsNumber(field(source, "frame_count")) &&
        field(source, "frame_count")->valuedouble == 11.0 &&
        cJSON_IsArray(objects);
    const cJSON *object = NULL;
    cJSON_ArrayForEach(object, objects) {
        if (!valid) break;
        valid = cJSON_IsObject(object) &&
            bounded_string(field(object, "object_id"), 1, 128) &&
            decision_class_allowed(field(object, "class_name")) &&
            decision_frame_reference_valid(object) &&
            decision_bbox_valid(field(object, "bbox_xyxy")) &&
            cJSON_IsObject(field(object, "geometry")) &&
            cJSON_IsObject(field(object, "visibility"));
    }
    cJSON_Delete(root);
    return valid ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}
