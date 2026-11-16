// SPDX-License-Identifier: Apache-2.0
#include "rehab_scan_capture_priv.h"

#include <limits.h>
#include <stdio.h>

static bool add_string(cJSON *object, const char *name, const char *value)
{
    return cJSON_AddStringToObject(object, name, value) != NULL;
}

static bool add_number(cJSON *object, const char *name, double value)
{
    return cJSON_AddNumberToObject(object, name, value) != NULL;
}

static cJSON *make_u8_array(const uint8_t *values)
{
    cJSON *array = cJSON_CreateArray();
    if (array == NULL) {
        return NULL;
    }
    for (size_t i = 0; i < REHAB_TOF_ZONE_COUNT; ++i) {
        if (!cJSON_AddItemToArray(array, cJSON_CreateNumber(values[i]))) {
            cJSON_Delete(array);
            return NULL;
        }
    }
    return array;
}

static cJSON *make_u16_array(const uint16_t *values)
{
    cJSON *array = cJSON_CreateArray();
    if (array == NULL) {
        return NULL;
    }
    for (size_t i = 0; i < REHAB_TOF_ZONE_COUNT; ++i) {
        if (!cJSON_AddItemToArray(array, cJSON_CreateNumber(values[i]))) {
            cJSON_Delete(array);
            return NULL;
        }
    }
    return array;
}

static cJSON *make_distance_array(const int16_t *values)
{
    cJSON *array = cJSON_CreateArray();
    if (array == NULL) {
        return NULL;
    }
    for (size_t i = 0; i < REHAB_TOF_ZONE_COUNT; ++i) {
        if (values[i] < 0 ||
            !cJSON_AddItemToArray(array, cJSON_CreateNumber(values[i]))) {
            cJSON_Delete(array);
            return NULL;
        }
    }
    return array;
}

cJSON *rehab_scan_capture_make_tof_json(const rehab_scan_frame_record_t *record)
{
    if (record == NULL || record->sequence >= REHAB_SCAN_MAX_FRAMES ||
        record->tof.timestamp_us <= 0 || record->tof.timestamp_us > 9007199254740991LL) {
        return NULL;
    }
    cJSON *root = cJSON_CreateObject();
    cJSON *distance = make_distance_array(record->tof.distance_mm);
    cJSON *sigma = make_u16_array(record->tof.range_sigma_mm);
    cJSON *status = make_u8_array(record->tof.target_status);
    cJSON *targets = make_u8_array(record->tof.nb_target_detected);
    if (root == NULL || distance == NULL || sigma == NULL || status == NULL || targets == NULL ||
        !add_string(root, "schema_version", REHAB_TOF_SCHEMA) ||
        !add_number(root, "sequence", record->sequence) ||
        !add_number(root, "timestamp_us", (double)record->tof.timestamp_us)) {
        cJSON_Delete(distance);
        cJSON_Delete(sigma);
        cJSON_Delete(status);
        cJSON_Delete(targets);
        cJSON_Delete(root);
        return NULL;
    }
    cJSON_AddItemToObject(root, "distance_mm", distance);
    cJSON_AddItemToObject(root, "range_sigma_mm", sigma);
    cJSON_AddItemToObject(root, "target_status", status);
    cJSON_AddItemToObject(root, "nb_target_detected", targets);
    return root;
}

cJSON *rehab_scan_capture_make_frame_json(const rehab_scan_frame_record_t *record)
{
    if (record == NULL || record->sequence >= REHAB_SCAN_MAX_FRAMES ||
        record->camera_timestamp_us <= 0 || record->tof.timestamp_us <= 0 ||
        record->camera_timestamp_us > 9007199254740991LL ||
        record->tof.timestamp_us > 9007199254740991LL ||
        record->pair_delta_us > 9007199254740991ULL) {
        return NULL;
    }
    char frame_id[16];
    int written = snprintf(frame_id, sizeof(frame_id), "frame_%06u",
                           (unsigned)record->sequence);
    if (written <= 0 || written >= (int)sizeof(frame_id)) {
        return NULL;
    }

    cJSON *root = cJSON_CreateObject();
    cJSON *image = cJSON_CreateObject();
    cJSON *tof = cJSON_CreateObject();
    cJSON *resolution = cJSON_CreateArray();
    if (root == NULL || image == NULL || tof == NULL || resolution == NULL ||
        !add_string(root, "schema_version", REHAB_FRAME_SCHEMA) ||
        !add_string(root, "frame_id", frame_id) ||
        !add_number(root, "sequence", record->sequence) ||
        !add_number(root, "camera_timestamp_us", (double)record->camera_timestamp_us) ||
        !add_number(root, "tof_timestamp_us", (double)record->tof.timestamp_us) ||
        !add_number(root, "pair_delta_us", (double)record->pair_delta_us) ||
        !add_string(image, "filename", "image.rgb565") ||
        !add_number(image, "width", REHAB_SCAN_IMAGE_WIDTH) ||
        !add_number(image, "height", REHAB_SCAN_IMAGE_HEIGHT) ||
        !add_string(image, "pixel_format", "RGB565_LE") ||
        !add_number(image, "stride_bytes", REHAB_SCAN_IMAGE_STRIDE) ||
        !add_number(image, "payload_bytes", REHAB_SCAN_IMAGE_BYTES) ||
        !add_string(tof, "filename", "tof.json") ||
        !cJSON_AddItemToArray(resolution, cJSON_CreateNumber(REHAB_SCAN_TOF_WIDTH)) ||
        !cJSON_AddItemToArray(resolution, cJSON_CreateNumber(REHAB_SCAN_TOF_HEIGHT))) {
        cJSON_Delete(root);
        cJSON_Delete(image);
        cJSON_Delete(tof);
        cJSON_Delete(resolution);
        return NULL;
    }
    cJSON_AddItemToObject(tof, "resolution", resolution);
    cJSON_AddItemToObject(root, "image", image);
    cJSON_AddItemToObject(root, "tof", tof);
    return root;
}

cJSON *rehab_scan_capture_make_scan_json(rehab_scan_capture_handle_t handle)
{
    if (handle == NULL) {
        return NULL;
    }
    cJSON *root = cJSON_CreateObject();
    cJSON *device = cJSON_CreateObject();
    cJSON *profiles = cJSON_CreateObject();
    if (root == NULL || device == NULL || profiles == NULL ||
        !add_string(root, "schema_version", REHAB_SCAN_SCHEMA) ||
        !add_string(root, "scan_id", handle->status.scan_id) ||
        !add_string(root, "created_at", handle->created_at) ||
        !add_number(root, "frame_count", handle->status.requested_frames) ||
        !add_string(root, "capture_mode",
                    handle->status.requested_frames == 1 ? "single_frame" : "multi_frame") ||
        !add_string(device, "board", "esp32p4_function_ev") ||
        !add_string(device, "camera", "SC2336") ||
        !add_string(device, "tof", "VL53L5CX") ||
        !add_string(profiles, "camera_profile_id", REHAB_CAMERA_PROFILE_ID) ||
        !add_string(profiles, "tof_profile_id", REHAB_TOF_PROFILE_ID) ||
        !add_string(profiles, "calibration_profile_id", REHAB_CALIBRATION_PROFILE_ID) ||
        !add_string(profiles, "algorithm_profile_id", REHAB_ALGORITHM_PROFILE_ID) ||
        !add_string(profiles, "scan_strategy_profile_id", REHAB_SCAN_STRATEGY_PROFILE_ID)) {
        cJSON_Delete(root);
        cJSON_Delete(device);
        cJSON_Delete(profiles);
        return NULL;
    }
    cJSON_AddItemToObject(root, "device", device);
    cJSON_AddItemToObject(root, "profiles", profiles);
    if (cJSON_AddNullToObject(root, "notes") == NULL) {
        cJSON_Delete(root);
        return NULL;
    }
    return root;
}
