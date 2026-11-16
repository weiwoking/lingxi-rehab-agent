// SPDX-License-Identifier: Apache-2.0
#include "rehab_scan_transfer_priv.h"

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"

static bool valid_scan_id(const char *value)
{
    size_t length = value ? strlen(value) : 0;
    if (length == 0 || length >= REHAB_SCAN_TRANSFER_SCAN_ID_MAX) {
        return false;
    }
    for (size_t i = 0; i < length; ++i) {
        unsigned char c = (unsigned char)value[i];
        if (!isalnum(c) && c != '_' && c != '-') {
            return false;
        }
    }
    return true;
}

static bool regular_file_size(const char *path, uint64_t minimum,
                              uint64_t maximum, uint64_t *out_size)
{
    struct stat status;
    if (stat(path, &status) != 0 || !S_ISREG(status.st_mode) ||
        status.st_size < 0 || (uint64_t)status.st_size < minimum ||
        (uint64_t)status.st_size > maximum) {
        return false;
    }
    if (out_size != NULL) {
        *out_size = (uint64_t)status.st_size;
    }
    return true;
}

static bool directory_exists(const char *path)
{
    struct stat status;
    return stat(path, &status) == 0 && S_ISDIR(status.st_mode);
}

static esp_err_t format_path(char *out, size_t out_size,
                             const char *format, const char *a,
                             const char *b, unsigned sequence)
{
    int count = snprintf(out, out_size, format, a, b, sequence);
    return count > 0 && (size_t)count < out_size ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

static bool valid_utf8(const uint8_t *bytes, size_t length)
{
    for (size_t i = 0; i < length;) {
        uint8_t c = bytes[i++];
        if (c < 0x80) {
            continue;
        }
        unsigned continuation = 0;
        uint32_t code = 0;
        if ((c & 0xE0) == 0xC0) { continuation = 1; code = c & 0x1F; if (code < 2) return false; }
        else if ((c & 0xF0) == 0xE0) { continuation = 2; code = c & 0x0F; }
        else if ((c & 0xF8) == 0xF0) { continuation = 3; code = c & 0x07; if (code > 4) return false; }
        else return false;
        if (i + continuation > length) return false;
        for (unsigned n = 0; n < continuation; ++n) {
            uint8_t next = bytes[i++];
            if ((next & 0xC0) != 0x80) return false;
            code = (code << 6) | (next & 0x3F);
        }
        if ((continuation == 2 && code < 0x800) ||
            (continuation == 3 && code < 0x10000) ||
            (code >= 0xD800 && code <= 0xDFFF) || code > 0x10FFFF) return false;
    }
    return true;
}

static cJSON *load_json(const char *path, size_t maximum)
{
    char *bytes = NULL;
    size_t length = 0;
    if (rehab_scan_transfer_load_file(path, maximum, &bytes, &length, NULL) != ESP_OK ||
        !valid_utf8((const uint8_t *)bytes, length)) {
        heap_caps_free(bytes);
        return NULL;
    }
    const char *end = NULL;
    cJSON *root = cJSON_ParseWithLengthOpts(bytes, length, &end, 0);
    if (root != NULL) {
        while (end < bytes + length && isspace((unsigned char)*end)) ++end;
        if (end != bytes + length) {
            cJSON_Delete(root);
            root = NULL;
        }
    }
    heap_caps_free(bytes);
    return root;
}

static bool object_has_only(const cJSON *object,
                            const char *const *keys, size_t key_count)
{
    if (!cJSON_IsObject(object)) return false;
    const cJSON *child = NULL;
    cJSON_ArrayForEach(child, object) {
        bool found = false;
        for (size_t i = 0; i < key_count; ++i) {
            if (child->string != NULL && strcmp(child->string, keys[i]) == 0) {
                found = true;
                break;
            }
        }
        if (!found) return false;
    }
    for (size_t i = 0; i < key_count; ++i) {
        if (cJSON_GetObjectItemCaseSensitive(object, keys[i]) == NULL) return false;
    }
    return true;
}

static const cJSON *field(const cJSON *object, const char *name)
{
    return cJSON_GetObjectItemCaseSensitive(object, name);
}

static bool string_equals(const cJSON *item, const char *expected)
{
    return cJSON_IsString(item) && item->valuestring != NULL &&
           strcmp(item->valuestring, expected) == 0;
}

static bool bounded_string(const cJSON *item, size_t minimum, size_t maximum)
{
    if (!cJSON_IsString(item) || item->valuestring == NULL) return false;
    size_t length = strlen(item->valuestring);
    return length >= minimum && length <= maximum;
}

static bool integer_range(const cJSON *item, double minimum, double maximum)
{
    return cJSON_IsNumber(item) && isfinite(item->valuedouble) &&
           floor(item->valuedouble) == item->valuedouble &&
           item->valuedouble >= minimum && item->valuedouble <= maximum;
}

static bool validate_profiles(const cJSON *profiles)
{
    static const char *const keys[] = {
        "camera_profile_id", "tof_profile_id", "calibration_profile_id",
        "algorithm_profile_id", "scan_strategy_profile_id",
    };
    return object_has_only(profiles, keys, 5) &&
        string_equals(field(profiles, keys[0]), "camera_sc2336_640x360_rgb565_v1") &&
        string_equals(field(profiles, keys[1]), "tof_vl53l5cx_orientation_identity_v1") &&
        string_equals(field(profiles, keys[2]), "calibration_unverified_stage6_v1") &&
        string_equals(field(profiles, keys[3]), "algorithm_stage6_official_v1") &&
        string_equals(field(profiles, keys[4]), "scan_strategy_stage6_guidance_v1");
}

static bool validate_scan_json(const cJSON *root, const char *scan_id,
                               uint32_t maximum_frames, uint32_t *out_count)
{
    static const char *const keys[] = {
        "schema_version", "scan_id", "created_at", "frame_count",
        "capture_mode", "device", "profiles", "notes",
    };
    static const char *const device_keys[] = {"board", "camera", "tof"};
    if (!object_has_only(root, keys, 8) ||
        !string_equals(field(root, "schema_version"), "deltar_scan_input.v1") ||
        !string_equals(field(root, "scan_id"), scan_id) ||
        !bounded_string(field(root, "created_at"), 1, 64) ||
        !integer_range(field(root, "frame_count"), 1, maximum_frames)) return false;
    uint32_t count = (uint32_t)field(root, "frame_count")->valuedouble;
    if (!string_equals(field(root, "capture_mode"),
                       count == 1 ? "single_frame" : "multi_frame")) return false;
    const cJSON *device = field(root, "device");
    if (!object_has_only(device, device_keys, 3) ||
        !bounded_string(field(device, "board"), 1, 64) ||
        !bounded_string(field(device, "camera"), 1, 64) ||
        !bounded_string(field(device, "tof"), 1, 64) ||
        !validate_profiles(field(root, "profiles"))) return false;
    const cJSON *notes = field(root, "notes");
    if (!cJSON_IsNull(notes) && !bounded_string(notes, 0, 512)) return false;
    *out_count = count;
    return true;
}

static bool validate_frame_json(const cJSON *root, uint32_t sequence,
                                double *out_tof_timestamp)
{
    static const char *const keys[] = {
        "schema_version", "frame_id", "sequence", "camera_timestamp_us",
        "tof_timestamp_us", "pair_delta_us", "image", "tof",
    };
    static const char *const image_keys[] = {
        "filename", "width", "height", "pixel_format", "stride_bytes", "payload_bytes",
    };
    static const char *const tof_keys[] = {"filename", "resolution"};
    char expected_id[32];
    snprintf(expected_id, sizeof(expected_id), "frame_%06u", (unsigned)sequence);
    const cJSON *image = field(root, "image");
    const cJSON *tof = field(root, "tof");
    if (!object_has_only(root, keys, 8) ||
        !string_equals(field(root, "schema_version"), "deltar_scan_frame.v1") ||
        !string_equals(field(root, "frame_id"), expected_id) ||
        !integer_range(field(root, "sequence"), sequence, sequence) ||
        !integer_range(field(root, "camera_timestamp_us"), 0, 9007199254740991.0) ||
        !integer_range(field(root, "tof_timestamp_us"), 0, 9007199254740991.0) ||
        !integer_range(field(root, "pair_delta_us"), 0, 9007199254740991.0) ||
        !object_has_only(image, image_keys, 6) ||
        !string_equals(field(image, "filename"), "image.rgb565") ||
        !integer_range(field(image, "width"), 640, 640) ||
        !integer_range(field(image, "height"), 360, 360) ||
        !string_equals(field(image, "pixel_format"), "RGB565_LE") ||
        !integer_range(field(image, "stride_bytes"), 1280, 1280) ||
        !integer_range(field(image, "payload_bytes"), 460800, 460800) ||
        !object_has_only(tof, tof_keys, 2) ||
        !string_equals(field(tof, "filename"), "tof.json")) return false;
    const cJSON *resolution = field(tof, "resolution");
    if (!cJSON_IsArray(resolution) || cJSON_GetArraySize(resolution) != 2 ||
        !integer_range(cJSON_GetArrayItem(resolution, 0), 8, 8) ||
        !integer_range(cJSON_GetArrayItem(resolution, 1), 8, 8)) return false;
    double camera_ts = field(root, "camera_timestamp_us")->valuedouble;
    double tof_ts = field(root, "tof_timestamp_us")->valuedouble;
    if (fabs(camera_ts - tof_ts) != field(root, "pair_delta_us")->valuedouble) return false;
    *out_tof_timestamp = tof_ts;
    return true;
}

static bool validate_integer_array(const cJSON *array, double maximum)
{
    if (!cJSON_IsArray(array) || cJSON_GetArraySize(array) != 64) return false;
    const cJSON *item = NULL;
    cJSON_ArrayForEach(item, array) {
        if (!integer_range(item, 0, maximum)) return false;
    }
    return true;
}

static bool validate_tof_json(const cJSON *root, uint32_t sequence,
                              double timestamp)
{
    static const char *const keys[] = {
        "schema_version", "sequence", "timestamp_us", "distance_mm",
        "range_sigma_mm", "target_status", "nb_target_detected",
    };
    return object_has_only(root, keys, 7) &&
        string_equals(field(root, "schema_version"), "rehab_tof_raw.v1") &&
        integer_range(field(root, "sequence"), sequence, sequence) &&
        integer_range(field(root, "timestamp_us"), timestamp, timestamp) &&
        validate_integer_array(field(root, "distance_mm"), 65535) &&
        validate_integer_array(field(root, "range_sigma_mm"), 65535) &&
        validate_integer_array(field(root, "target_status"), 255) &&
        validate_integer_array(field(root, "nb_target_detected"), 255);
}

static esp_err_t add_part(rehab_scan_transfer_plan_t *plan, const char *path,
                          const char *field_name, const char *filename,
                          const char *content_type, uint64_t minimum,
                          uint64_t maximum)
{
    if (plan->part_count >= REHAB_SCAN_TRANSFER_MAX_PARTS) return ESP_ERR_INVALID_SIZE;
    rehab_scan_transfer_part_t *part = &plan->parts[plan->part_count];
    if (!regular_file_size(path, minimum, maximum, &part->size) ||
        strlcpy(part->path, path, sizeof(part->path)) >= sizeof(part->path) ||
        strlcpy(part->field, field_name, sizeof(part->field)) >= sizeof(part->field) ||
        UINT64_MAX - plan->raw_bytes < part->size) return ESP_ERR_INVALID_SIZE;
    part->filename = filename;
    part->content_type = content_type;
    plan->raw_bytes += part->size;
    plan->part_count++;
    return ESP_OK;
}

static bool result_already_exists(rehab_scan_transfer_handle_t handle,
                                  const char *scan_id)
{
    char path[REHAB_SCAN_TRANSFER_PATH_MAX];
    struct stat status;
    int count = snprintf(path, sizeof(path), "%s/%s/result.json",
                         handle->result_root_path, scan_id);
    return count > 0 && (size_t)count < sizeof(path) && stat(path, &status) == 0;
}

static esp_err_t select_latest(rehab_scan_transfer_handle_t handle,
                               char *out, size_t out_size)
{
    DIR *directory = opendir(handle->scan_root_path);
    if (directory == NULL) return ESP_ERR_NOT_FOUND;
    char best[REHAB_SCAN_TRANSFER_SCAN_ID_MAX] = {0};
    struct dirent *entry;
    while ((entry = readdir(directory)) != NULL) {
        const char *name = entry->d_name;
        if (strncmp(name, "scan_", 5) != 0 || !valid_scan_id(name) ||
            result_already_exists(handle, name)) continue;
        char path[REHAB_SCAN_TRANSFER_PATH_MAX];
        char scan_json[REHAB_SCAN_TRANSFER_PATH_MAX];
        if (format_path(path, sizeof(path), "%s/%s", handle->scan_root_path, name, 0) != ESP_OK ||
            format_path(scan_json, sizeof(scan_json), "%s/%s/scan.json", handle->scan_root_path, name, 0) != ESP_OK ||
            !directory_exists(path) || !regular_file_size(scan_json, 1,
                REHAB_SCAN_TRANSFER_SCAN_JSON_MAX, NULL)) continue;
        if (best[0] == '\0' || strcmp(name, best) > 0) strlcpy(best, name, sizeof(best));
    }
    closedir(directory);
    if (best[0] == '\0') return ESP_ERR_NOT_FOUND;
    return strlcpy(out, best, out_size) < out_size ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

esp_err_t rehab_scan_transfer_select_and_validate(
    rehab_scan_transfer_handle_t handle, const char *selector,
    rehab_scan_transfer_plan_t *out_plan)
{
    if (handle == NULL || selector == NULL || out_plan == NULL) return ESP_ERR_INVALID_ARG;
    memset(out_plan, 0, sizeof(*out_plan));
    esp_err_t err;
    if (strcmp(selector, "latest") == 0) {
        err = select_latest(handle, out_plan->scan_id, sizeof(out_plan->scan_id));
        if (err != ESP_OK) return err;
    } else {
        if (!valid_scan_id(selector)) return ESP_ERR_INVALID_ARG;
        strlcpy(out_plan->scan_id, selector, sizeof(out_plan->scan_id));
    }
    if (result_already_exists(handle, out_plan->scan_id)) return ESP_ERR_INVALID_STATE;
    if (format_path(out_plan->scan_path, sizeof(out_plan->scan_path), "%s/%s",
                    handle->scan_root_path, out_plan->scan_id, 0) != ESP_OK ||
        !directory_exists(out_plan->scan_path)) return ESP_ERR_NOT_FOUND;

    char path[REHAB_SCAN_TRANSFER_PATH_MAX];
    if (format_path(path, sizeof(path), "%s/%s/scan.json", handle->scan_root_path,
                    out_plan->scan_id, 0) != ESP_OK) return ESP_ERR_INVALID_SIZE;
    cJSON *scan = load_json(path, REHAB_SCAN_TRANSFER_SCAN_JSON_MAX);
    if (scan == NULL || !validate_scan_json(scan, out_plan->scan_id,
                                            handle->config.max_frames,
                                            &out_plan->frame_count)) {
        cJSON_Delete(scan);
        return ESP_ERR_INVALID_RESPONSE;
    }
    cJSON_Delete(scan);
    err = add_part(out_plan, path, "scan_json", "scan.json", "application/json",
                   1, REHAB_SCAN_TRANSFER_SCAN_JSON_MAX);
    if (err != ESP_OK) return err;

    for (uint32_t sequence = 0; sequence < out_plan->frame_count; ++sequence) {
        char frame_dir[REHAB_SCAN_TRANSFER_PATH_MAX];
        if (format_path(frame_dir, sizeof(frame_dir), "%s/%s/frames/frame_%06u",
                        handle->scan_root_path, out_plan->scan_id, sequence) != ESP_OK ||
            !directory_exists(frame_dir)) return ESP_ERR_NOT_FOUND;
        char frame_json[REHAB_SCAN_TRANSFER_PATH_MAX];
        char tof_json[REHAB_SCAN_TRANSFER_PATH_MAX];
        char image[REHAB_SCAN_TRANSFER_PATH_MAX];
        if (format_path(frame_json, sizeof(frame_json), "%s/%s", frame_dir, "frame.json", 0) != ESP_OK ||
            format_path(tof_json, sizeof(tof_json), "%s/%s", frame_dir, "tof.json", 0) != ESP_OK ||
            format_path(image, sizeof(image), "%s/%s", frame_dir, "image.rgb565", 0) != ESP_OK) return ESP_ERR_INVALID_SIZE;
        cJSON *frame = load_json(frame_json, handle->config.max_json_part_bytes);
        cJSON *tof = load_json(tof_json, handle->config.max_json_part_bytes);
        double timestamp = 0;
        bool valid = frame != NULL && tof != NULL &&
            validate_frame_json(frame, sequence, &timestamp) &&
            validate_tof_json(tof, sequence, timestamp);
        cJSON_Delete(frame);
        cJSON_Delete(tof);
        if (!valid) return ESP_ERR_INVALID_RESPONSE;
        char field_name[REHAB_SCAN_TRANSFER_FIELD_MAX];
        snprintf(field_name, sizeof(field_name), "frame_%06u_frame_json", (unsigned)sequence);
        err = add_part(out_plan, frame_json, field_name, "frame.json", "application/json",
                       1, handle->config.max_json_part_bytes);
        if (err != ESP_OK) return err;
        snprintf(field_name, sizeof(field_name), "frame_%06u_tof_json", (unsigned)sequence);
        err = add_part(out_plan, tof_json, field_name, "tof.json", "application/json",
                       1, handle->config.max_json_part_bytes);
        if (err != ESP_OK) return err;
        snprintf(field_name, sizeof(field_name), "frame_%06u_image_rgb565", (unsigned)sequence);
        err = add_part(out_plan, image, field_name, "image.rgb565",
                       "application/octet-stream", REHAB_SCAN_TRANSFER_IMAGE_BYTES,
                       REHAB_SCAN_TRANSFER_IMAGE_BYTES);
        if (err != ESP_OK) return err;
    }
    return out_plan->raw_bytes <= handle->config.max_upload_bytes ? ESP_OK : ESP_ERR_INVALID_SIZE;
}
