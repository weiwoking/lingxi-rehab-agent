// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_priv.h"

#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "claw_paths.h"
#include "esp_log.h"
#include "mbedtls/sha256.h"

#define SELECTED_TASK_SCHEMA "rehab.selected_task.v1"
#define SELECTED_TASK_SCHEMA_V2 "rehab_selected_task.v2"
#define SELECTED_TASK_DIRECTORY_SUBPATH "rehab"
#define SELECTED_TASK_SUBPATH "rehab/selected_task.json"
#define SELECTED_TASK_TMP_SUBPATH "rehab/selected_task.json.tmp"
#define SELECTED_TASK_NEXT_SUBPATH "rehab/selected_task.json.next"
#define SELECTED_TASK_PREVIOUS_SUBPATH "rehab/selected_task.json.previous"
#define SELECTED_TASK_PATH_MAX 192U
#define SELECTED_TASK_FILE_MAX (16U * 1024U)

static const char *TAG = "rehab_decision_file";

typedef struct {
    char task_id[REHAB_TASK_DECISION_SELECTED_TASK_ID_MAX];
    char task_name[REHAB_TASK_DECISION_SELECTED_TASK_NAME_MAX];
    char reason[REHAB_TASK_DECISION_REASON_MAX];
    double score;
    int distance_cm;
    uint32_t generation;
    char rules_version[REHAB_TASK_DECISION_RULES_VERSION_MAX];
    char decision_id[REHAB_TASK_DECISION_DECISION_ID_MAX];
    char source_scan_id[REHAB_TASK_DECISION_SCAN_ID_MAX];
    char source_scene_sha256[REHAB_TASK_DECISION_SCENE_SHA256_MAX];
    bool target_available;
    char target_object_id[REHAB_TASK_DECISION_OBJECT_ID_MAX];
    char target_class[REHAB_TASK_DECISION_CLASS_NAME_MAX];
    char target_source_frame_id[REHAB_TASK_DECISION_FRAME_ID_MAX];
    uint32_t target_source_frame_sequence;
    double target_bbox_xyxy[4];
    bool target_geometry_available;
    bool target_geometry_valid;
    bool target_object_height_available;
    double target_object_height_mm;
    bool target_support_height_available;
    double target_support_height_mm;
    bool target_top_height_available;
    double target_top_height_above_floor_mm;
    bool target_distance_available;
    double target_distance_mm;
    char target_distance_status[REHAB_TASK_DECISION_DISTANCE_STATUS_MAX];
} selected_task_value_t;

static const char *s_fields[] = {
    "schema_version",
    "task_id",
    "task_name",
    "suggested_distance_cm",
    "decision_generation",
    "decision_rules_version",
};

static bool selected_lock(rehab_task_decision_handle_t handle)
{
    return xSemaphoreTake(handle->selected_task_lock,
                          pdMS_TO_TICKS(2000)) == pdTRUE;
}

static void selected_unlock(rehab_task_decision_handle_t handle)
{
    xSemaphoreGive(handle->selected_task_lock);
}

static esp_err_t selected_paths(char directory[SELECTED_TASK_PATH_MAX],
                                char final_path[SELECTED_TASK_PATH_MAX],
                                char temporary[SELECTED_TASK_PATH_MAX])
{
    esp_err_t err = claw_paths_join(CLAW_PATH_DATA,
                                    SELECTED_TASK_DIRECTORY_SUBPATH,
                                    directory, SELECTED_TASK_PATH_MAX);
    if (err == ESP_OK) {
        err = claw_paths_join(CLAW_PATH_DATA, SELECTED_TASK_SUBPATH,
                              final_path, SELECTED_TASK_PATH_MAX);
    }
    if (err == ESP_OK) {
        err = claw_paths_join(CLAW_PATH_DATA, SELECTED_TASK_TMP_SUBPATH,
                              temporary, SELECTED_TASK_PATH_MAX);
    }
    return err;
}

static esp_err_t ensure_directory(const char *path)
{
    struct stat info = {0};
    if (stat(path, &info) == 0) {
        return S_ISDIR(info.st_mode) ? ESP_OK : ESP_ERR_INVALID_STATE;
    }
    if (errno != ENOENT) {
        return ESP_FAIL;
    }
    return mkdir(path, 0755) == 0 || errno == EEXIST ? ESP_OK : ESP_FAIL;
}

static bool object_has_exact_fields(const cJSON *object)
{
    if (!cJSON_IsObject(object) ||
        cJSON_GetArraySize(object) != (int)(sizeof(s_fields) /
                                            sizeof(s_fields[0]))) {
        return false;
    }
    uint32_t seen = 0;
    const cJSON *child = NULL;
    cJSON_ArrayForEach(child, object) {
        size_t index = sizeof(s_fields) / sizeof(s_fields[0]);
        for (size_t i = 0; i < sizeof(s_fields) / sizeof(s_fields[0]); ++i) {
            if (child->string != NULL &&
                strcmp(child->string, s_fields[i]) == 0) {
                index = i;
                break;
            }
        }
        if (index >= sizeof(s_fields) / sizeof(s_fields[0]) ||
            (seen & (UINT32_C(1) << index)) != 0) {
            return false;
        }
        seen |= UINT32_C(1) << index;
    }
    return seen == ((UINT32_C(1) <<
                     (sizeof(s_fields) / sizeof(s_fields[0]))) - 1U);
}

static bool json_integer(const cJSON *item, int minimum, int maximum,
                         int *out_value)
{
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) ||
        floor(item->valuedouble) != item->valuedouble ||
        item->valuedouble < minimum || item->valuedouble > maximum) {
        return false;
    }
    if (out_value != NULL) {
        *out_value = (int)item->valuedouble;
    }
    return true;
}

static bool bounded_string(const cJSON *item, size_t minimum, size_t maximum)
{
    return cJSON_IsString(item) && item->valuestring != NULL &&
        strlen(item->valuestring) >= minimum &&
        strlen(item->valuestring) <= maximum &&
        rehab_task_decision_text_valid(item->valuestring, maximum,
                                       maximum, minimum == 0);
}

static bool sha256_string(const cJSON *item)
{
    if (!bounded_string(item, 64, 64)) {
        return false;
    }
    for (size_t i = 0; i < 64U; ++i) {
        char value = item->valuestring[i];
        if (!((value >= '0' && value <= '9') ||
              (value >= 'a' && value <= 'f'))) {
            return false;
        }
    }
    return true;
}

static bool json_finite_range(const cJSON *item, double minimum,
                              double maximum, double *out_value)
{
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) ||
        item->valuedouble < minimum || item->valuedouble > maximum) {
        return false;
    }
    if (out_value != NULL) {
        *out_value = item->valuedouble;
    }
    return true;
}

static bool bbox_value(const cJSON *bbox, double out[4])
{
    if (!cJSON_IsArray(bbox) || cJSON_GetArraySize(bbox) != 4) {
        return false;
    }
    for (size_t i = 0; i < 4U; ++i) {
        const cJSON *item = cJSON_GetArrayItem(bbox, (int)i);
        if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble)) {
            return false;
        }
        out[i] = item->valuedouble;
    }
    /* BBox rendering is optional UI evidence.  Keep a valid selected task
     * usable even if a stored box later proves out of bounds; the result
     * page clips or suppresses it without invalidating the task exchange. */
    return true;
}

static void selected_geometry_number(const cJSON *geometry,
                                     const char *field,
                                     bool *available,
                                     double *value)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(geometry, field);
    *available = cJSON_IsNumber(item) && isfinite(item->valuedouble) &&
        item->valuedouble > 0.0;
    *value = *available ? item->valuedouble : 0.0;
}

static void selected_geometry_from_target(const cJSON *target,
                                          selected_task_value_t *value)
{
    const cJSON *geometry = cJSON_GetObjectItemCaseSensitive(
        target, "geometry");
    if (!cJSON_IsObject(geometry)) {
        return;
    }
    value->target_geometry_available = true;
    value->target_geometry_valid = cJSON_IsTrue(
        cJSON_GetObjectItemCaseSensitive(geometry, "valid"));
    selected_geometry_number(
        geometry, "object_height_mm",
        &value->target_object_height_available,
        &value->target_object_height_mm);
    selected_geometry_number(
        geometry, "support_height_mm",
        &value->target_support_height_available,
        &value->target_support_height_mm);
    selected_geometry_number(
        geometry, "top_height_above_floor_mm",
        &value->target_top_height_available,
        &value->target_top_height_above_floor_mm);
    selected_geometry_number(
        geometry, "distance_mm",
        &value->target_distance_available,
        &value->target_distance_mm);
    const cJSON *distance_status = cJSON_GetObjectItemCaseSensitive(
        geometry, "distance_status");
    if (bounded_string(distance_status, 1,
                       REHAB_TASK_DECISION_DISTANCE_STATUS_MAX - 1U)) {
        strlcpy(value->target_distance_status,
                distance_status->valuestring,
                sizeof(value->target_distance_status));
    }
}

static bool selected_candidates_valid(const cJSON *candidates,
                                      const char *selected_task_id,
                                      const char *selected_reason,
                                      double *out_selected_score)
{
    if (!cJSON_IsArray(candidates) ||
        cJSON_GetArraySize(candidates) !=
            (int)rehab_task_decision_rules_task_count()) {
        return false;
    }
    bool selected_seen = false;
    for (size_t index = 0U;
         index < rehab_task_decision_rules_task_count(); ++index) {
        const cJSON *entry = cJSON_GetArrayItem(candidates, (int)index);
        const cJSON *score = cJSON_GetObjectItemCaseSensitive(entry, "score");
        const cJSON *reason = cJSON_GetObjectItemCaseSensitive(entry,
                                                               "reason");
        const cJSON *task_name = cJSON_GetObjectItemCaseSensitive(
            entry, "task_name");
        const rehab_task_policy_definition_t *task =
            rehab_task_policy_task_at(index);
        if (!cJSON_IsObject(entry) ||
            cJSON_GetArraySize(entry) != 3 || task == NULL ||
            !json_finite_range(score, 0.0, 100.0, NULL) ||
            !bounded_string(reason, 1,
                            REHAB_TASK_DECISION_REASON_MAX - 1U) ||
            !cJSON_IsString(task_name) || task_name->valuestring == NULL ||
            strcmp(task_name->valuestring, task->task_name) != 0) {
            return false;
        }
        if (strcmp(task->task_id, selected_task_id) == 0) {
            if (selected_reason == NULL ||
                strcmp(reason->valuestring, selected_reason) != 0) {
                return false;
            }
            selected_seen = true;
            if (out_selected_score != NULL) {
                *out_selected_score = score->valuedouble;
            }
        }
    }
    return selected_seen;
}

static esp_err_t selected_parse_v2(rehab_task_decision_handle_t handle,
                                   const cJSON *root,
                                   selected_task_value_t *value)
{
    const cJSON *task_id = cJSON_GetObjectItemCaseSensitive(root, "task_id");
    const cJSON *task_name = cJSON_GetObjectItemCaseSensitive(root,
                                                              "task_name");
    const cJSON *reason = cJSON_GetObjectItemCaseSensitive(root, "reason");
    const cJSON *score = cJSON_GetObjectItemCaseSensitive(root, "score");
    const cJSON *decision_id = cJSON_GetObjectItemCaseSensitive(
        root, "decision_id");
    const cJSON *source_scan_id = cJSON_GetObjectItemCaseSensitive(
        root, "source_scan_id");
    const cJSON *source_sha = cJSON_GetObjectItemCaseSensitive(
        root, "source_scene_sha256");
    const cJSON *target_id = cJSON_GetObjectItemCaseSensitive(
        root, "target_object_id");
    const cJSON *target = cJSON_GetObjectItemCaseSensitive(root,
                                                           "target_object");
    int generation = 0;
    int distance = 0;
    double selected_candidate_score = 0.0;
    char canonical_name[sizeof(value->task_name)] = {0};
    char rules_version[sizeof(value->rules_version)] = {0};
    bool valid = cJSON_IsString(task_id) && task_id->valuestring != NULL &&
        cJSON_GetArraySize(root) == 14 &&
        rehab_task_decision_rules_task_metadata(
            handle, task_id->valuestring, canonical_name,
            sizeof(canonical_name), rules_version,
            sizeof(rules_version)) == ESP_OK &&
        cJSON_IsString(task_name) && task_name->valuestring != NULL &&
        strcmp(task_name->valuestring, canonical_name) == 0 &&
        bounded_string(reason, 1, REHAB_TASK_DECISION_REASON_MAX - 1U) &&
        json_finite_range(score, 0.0, 100.0, NULL) &&
        bounded_string(cJSON_GetObjectItemCaseSensitive(root, "created_at"),
                       1, 64) &&
        bounded_string(decision_id, 1,
                       REHAB_TASK_DECISION_DECISION_ID_MAX - 1U) &&
        bounded_string(source_scan_id, 1,
                       REHAB_TASK_DECISION_SCAN_ID_MAX - 1U) &&
        sha256_string(source_sha) &&
        json_integer(cJSON_GetObjectItemCaseSensitive(
                         root, "decision_generation"),
                     1, INT32_MAX, &generation) &&
        json_integer(cJSON_GetObjectItemCaseSensitive(
                         root, "suggested_distance_cm"),
                     0, 300, &distance) &&
        selected_candidates_valid(
            cJSON_GetObjectItemCaseSensitive(root, "candidate_scores"),
            task_id->valuestring, reason->valuestring,
            &selected_candidate_score) &&
        fabs(score->valuedouble - selected_candidate_score) < 0.000001;
    if (!valid) {
        return ESP_ERR_INVALID_RESPONSE;
    }

    memset(value, 0, sizeof(*value));
    strlcpy(value->task_id, task_id->valuestring, sizeof(value->task_id));
    strlcpy(value->task_name, canonical_name, sizeof(value->task_name));
    strlcpy(value->reason, reason->valuestring, sizeof(value->reason));
    value->score = score->valuedouble;
    value->distance_cm = distance;
    value->generation = (uint32_t)generation;
    strlcpy(value->rules_version, rules_version,
            sizeof(value->rules_version));
    strlcpy(value->decision_id, decision_id->valuestring,
            sizeof(value->decision_id));
    strlcpy(value->source_scan_id, source_scan_id->valuestring,
            sizeof(value->source_scan_id));
    strlcpy(value->source_scene_sha256, source_sha->valuestring,
            sizeof(value->source_scene_sha256));

    if (cJSON_IsNull(target_id) && cJSON_IsNull(target)) {
        return ESP_OK;
    }
    if (!bounded_string(target_id, 1,
                        REHAB_TASK_DECISION_OBJECT_ID_MAX - 1U) ||
        !cJSON_IsObject(target)) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    const cJSON *object_id = cJSON_GetObjectItemCaseSensitive(target,
                                                              "object_id");
    const cJSON *class_name = cJSON_GetObjectItemCaseSensitive(target,
                                                               "class");
    const cJSON *frame_id = cJSON_GetObjectItemCaseSensitive(
        target, "source_frame_id");
    const cJSON *sequence_item = cJSON_GetObjectItemCaseSensitive(
        target, "source_frame_sequence");
    const cJSON *bbox_item = cJSON_GetObjectItemCaseSensitive(
        target, "bbox_xyxy");
    int sequence = 0;
    double bbox[4] = {0};
    valid = bounded_string(object_id, 1,
                           REHAB_TASK_DECISION_OBJECT_ID_MAX - 1U) &&
        strcmp(object_id->valuestring, target_id->valuestring) == 0 &&
        bounded_string(class_name, 1,
                       REHAB_TASK_DECISION_CLASS_NAME_MAX - 1U);
    bool has_frame = frame_id != NULL || sequence_item != NULL;
    if (valid && has_frame) {
        valid = bounded_string(frame_id, 12, 12) &&
            json_integer(sequence_item, 0, 63, &sequence);
        char expected_frame_id[16] = {0};
        snprintf(expected_frame_id, sizeof(expected_frame_id),
                 "frame_%06u", (unsigned)sequence);
        valid = valid &&
            strcmp(frame_id->valuestring, expected_frame_id) == 0;
    }
    if (valid && bbox_item != NULL) {
        valid = bbox_value(bbox_item, bbox);
    }
    if (!valid) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    value->target_available = true;
    strlcpy(value->target_object_id, object_id->valuestring,
            sizeof(value->target_object_id));
    strlcpy(value->target_class, class_name->valuestring,
            sizeof(value->target_class));
    if (has_frame) {
        strlcpy(value->target_source_frame_id, frame_id->valuestring,
                sizeof(value->target_source_frame_id));
        value->target_source_frame_sequence = (uint32_t)sequence;
    }
    if (bbox_item != NULL) {
        memcpy(value->target_bbox_xyxy, bbox, sizeof(bbox));
    }
    selected_geometry_from_target(target, value);
    return ESP_OK;
}

static esp_err_t selected_parse(rehab_task_decision_handle_t handle,
                                const char *json,
                                selected_task_value_t *value)
{
    if (handle == NULL || json == NULL || value == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    cJSON *root = cJSON_ParseWithOpts(json, NULL, true);
    const cJSON *schema = cJSON_GetObjectItemCaseSensitive(root,
                                                           "schema_version");
    if (cJSON_IsObject(root) && cJSON_IsString(schema) &&
        schema->valuestring != NULL &&
        strcmp(schema->valuestring, SELECTED_TASK_SCHEMA_V2) == 0) {
        esp_err_t v2_err = selected_parse_v2(handle, root, value);
        cJSON_Delete(root);
        return v2_err;
    }
    if (!object_has_exact_fields(root)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    const cJSON *task_id = cJSON_GetObjectItemCaseSensitive(root, "task_id");
    const cJSON *task_name = cJSON_GetObjectItemCaseSensitive(root,
                                                              "task_name");
    const cJSON *rules = cJSON_GetObjectItemCaseSensitive(
        root, "decision_rules_version");
    int distance = 0;
    int generation = 0;
    char canonical_name[sizeof(value->task_name)] = {0};
    char current_rules_version[sizeof(value->rules_version)] = {0};
    esp_err_t metadata_err = cJSON_IsString(task_id) &&
        task_id->valuestring != NULL
            ? rehab_task_decision_rules_task_metadata(
                  handle, task_id->valuestring,
                  canonical_name, sizeof(canonical_name),
                  current_rules_version, sizeof(current_rules_version))
            : ESP_ERR_NOT_FOUND;
    bool valid = cJSON_IsString(schema) && schema->valuestring != NULL &&
        strcmp(schema->valuestring, SELECTED_TASK_SCHEMA) == 0 &&
        metadata_err == ESP_OK &&
        cJSON_IsString(task_name) && task_name->valuestring != NULL &&
        strcmp(task_name->valuestring, canonical_name) == 0 &&
        json_integer(cJSON_GetObjectItemCaseSensitive(
                         root, "suggested_distance_cm"),
                     1, 300, &distance) &&
        json_integer(cJSON_GetObjectItemCaseSensitive(
                         root, "decision_generation"),
                     1, INT32_MAX, &generation) &&
        cJSON_IsString(rules) && rules->valuestring != NULL &&
        strcmp(rules->valuestring, current_rules_version) == 0;
    if (valid) {
        memset(value, 0, sizeof(*value));
        strlcpy(value->task_id, task_id->valuestring, sizeof(value->task_id));
        strlcpy(value->task_name, canonical_name,
                sizeof(value->task_name));
        value->distance_cm = distance;
        value->generation = (uint32_t)generation;
        strlcpy(value->rules_version, current_rules_version,
                sizeof(value->rules_version));
    }
    cJSON_Delete(root);
    return valid ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}

static esp_err_t read_file(const char *path, char **out_json)
{
    struct stat info = {0};
    *out_json = NULL;
    if (stat(path, &info) != 0) {
        return errno == ENOENT ? ESP_ERR_NOT_FOUND : ESP_FAIL;
    }
    if (!S_ISREG(info.st_mode) || info.st_size <= 0 ||
        (uint64_t)info.st_size > (uint64_t)SELECTED_TASK_FILE_MAX) {
        return ESP_ERR_INVALID_SIZE;
    }
    char *json = calloc(1, (size_t)info.st_size + 1U);
    if (json == NULL) {
        return ESP_ERR_NO_MEM;
    }
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        free(json);
        return ESP_FAIL;
    }
    size_t received = fread(json, 1, (size_t)info.st_size, file);
    bool ok = received == (size_t)info.st_size && !ferror(file);
    if (fclose(file) != 0) {
        ok = false;
    }
    if (!ok) {
        free(json);
        return ESP_FAIL;
    }
    json[received] = '\0';
    *out_json = json;
    return ESP_OK;
}

static esp_err_t validate_file(rehab_task_decision_handle_t handle,
                               const char *path,
                               selected_task_value_t *value)
{
    char *json = NULL;
    esp_err_t err = read_file(path, &json);
    if (err == ESP_OK) {
        err = selected_parse(handle, json, value);
    }
    free(json);
    return err;
}

static esp_err_t selected_aux_path(const char *subpath,
                                   char path[SELECTED_TASK_PATH_MAX])
{
    return claw_paths_join(CLAW_PATH_DATA, subpath, path,
                           SELECTED_TASK_PATH_MAX);
}

static esp_err_t selected_bytes_sha256(const unsigned char *bytes,
                                       size_t length, char output[65])
{
    unsigned char digest[32] = {0};
    mbedtls_sha256_context context;
    mbedtls_sha256_init(&context);
    int result = mbedtls_sha256_starts(&context, 0);
    if (result == 0) {
        result = mbedtls_sha256_update(&context, bytes, length);
    }
    if (result == 0) {
        result = mbedtls_sha256_finish(&context, digest);
    }
    mbedtls_sha256_free(&context);
    if (result != 0) {
        return ESP_FAIL;
    }
    for (size_t i = 0; i < sizeof(digest); ++i) {
        snprintf(output + i * 2U, 3U, "%02x", digest[i]);
    }
    output[64] = '\0';
    return ESP_OK;
}

static esp_err_t selected_file_sha256(const char *path, char output[65])
{
    char *bytes = NULL;
    esp_err_t err = read_file(path, &bytes);
    if (err == ESP_OK) {
        err = selected_bytes_sha256((const unsigned char *)bytes,
                                    strlen(bytes), output);
    }
    free(bytes);
    return err;
}

static void selected_snapshot_from_value(
    const selected_task_value_t *value,
    rehab_task_decision_selected_snapshot_t *snapshot)
{
    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->available = true;
    strlcpy(snapshot->task_id, value->task_id, sizeof(snapshot->task_id));
    strlcpy(snapshot->task_name, value->task_name,
            sizeof(snapshot->task_name));
    strlcpy(snapshot->reason, value->reason, sizeof(snapshot->reason));
    snapshot->score = value->score;
    snapshot->suggested_distance_cm = value->distance_cm;
    snapshot->decision_generation = value->generation;
    strlcpy(snapshot->decision_rules_version, value->rules_version,
            sizeof(snapshot->decision_rules_version));
    strlcpy(snapshot->decision_id, value->decision_id,
            sizeof(snapshot->decision_id));
    strlcpy(snapshot->source_scan_id, value->source_scan_id,
            sizeof(snapshot->source_scan_id));
    strlcpy(snapshot->source_scene_sha256, value->source_scene_sha256,
            sizeof(snapshot->source_scene_sha256));
    snapshot->target_available = value->target_available;
    strlcpy(snapshot->target_object_id, value->target_object_id,
            sizeof(snapshot->target_object_id));
    strlcpy(snapshot->target_class, value->target_class,
            sizeof(snapshot->target_class));
    strlcpy(snapshot->target_source_frame_id,
            value->target_source_frame_id,
            sizeof(snapshot->target_source_frame_id));
    snapshot->target_source_frame_sequence =
        value->target_source_frame_sequence;
    memcpy(snapshot->target_bbox_xyxy, value->target_bbox_xyxy,
           sizeof(snapshot->target_bbox_xyxy));
    snapshot->target_geometry_available = value->target_geometry_available;
    snapshot->target_geometry_valid = value->target_geometry_valid;
    snapshot->target_object_height_available =
        value->target_object_height_available;
    snapshot->target_object_height_mm = value->target_object_height_mm;
    snapshot->target_support_height_available =
        value->target_support_height_available;
    snapshot->target_support_height_mm = value->target_support_height_mm;
    snapshot->target_top_height_available =
        value->target_top_height_available;
    snapshot->target_top_height_above_floor_mm =
        value->target_top_height_above_floor_mm;
    snapshot->target_distance_available = value->target_distance_available;
    snapshot->target_distance_mm = value->target_distance_mm;
    strlcpy(snapshot->target_distance_status,
            value->target_distance_status,
            sizeof(snapshot->target_distance_status));
}

static esp_err_t selected_quarantine(const char *final_path,
                                     uint32_t generation)
{
    char corrupt[SELECTED_TASK_PATH_MAX] = {0};
    time_t now = time(NULL);
    int written = snprintf(corrupt, sizeof(corrupt), "%s.corrupt.%llu.%" PRIu32,
                           final_path, (unsigned long long)(now > 0 ? now : 0),
                           generation);
    if (written <= 0 || (size_t)written >= sizeof(corrupt)) {
        return ESP_ERR_INVALID_SIZE;
    }
    if (rename(final_path, corrupt) != 0) {
        return errno == ENOENT ? ESP_ERR_NOT_FOUND : ESP_FAIL;
    }
    ESP_LOGW(TAG, "SELECTED_TASK_CORRUPT_QUARANTINED path=%s", corrupt);
    return ESP_OK;
}

static esp_err_t selected_recover_locked(
    rehab_task_decision_handle_t handle,
    const char *final_path,
    const char *previous_path,
    selected_task_value_t *value)
{
    esp_err_t current_err = validate_file(handle, final_path, value);
    if (current_err == ESP_OK) {
        return ESP_OK;
    }

    selected_task_value_t rollback = {0};
    esp_err_t rollback_err = validate_file(handle, previous_path, &rollback);
    if (rollback_err != ESP_OK) {
        if (current_err != ESP_ERR_NOT_FOUND) {
            (void)selected_quarantine(final_path, handle->run_generation);
        }
        return ESP_ERR_NOT_FOUND;
    }
    if (current_err != ESP_ERR_NOT_FOUND) {
        esp_err_t quarantine_err = selected_quarantine(
            final_path, rollback.generation);
        if (quarantine_err != ESP_OK &&
            quarantine_err != ESP_ERR_NOT_FOUND) {
            return quarantine_err;
        }
    }
    if (rename(previous_path, final_path) != 0) {
        return ESP_FAIL;
    }
    memset(value, 0, sizeof(*value));
    esp_err_t err = validate_file(handle, final_path, value);
    if (err != ESP_OK) {
        (void)rename(final_path, previous_path);
        return err;
    }
    ESP_LOGW(TAG,
             "SELECTED_TASK_RECOVERED source=rollback schema=%s task_id=%s",
             value->source_scene_sha256[0] != '\0'
                 ? SELECTED_TASK_SCHEMA_V2 : SELECTED_TASK_SCHEMA,
             value->task_id);
    return ESP_OK;
}

static esp_err_t selected_abort_staging_locked(void)
{
    char temporary[SELECTED_TASK_PATH_MAX] = {0};
    char next[SELECTED_TASK_PATH_MAX] = {0};
    esp_err_t err = selected_aux_path(SELECTED_TASK_TMP_SUBPATH, temporary);
    if (err == ESP_OK) {
        err = selected_aux_path(SELECTED_TASK_NEXT_SUBPATH, next);
    }
    if (err == ESP_OK && unlink(temporary) != 0 && errno != ENOENT) {
        err = ESP_FAIL;
    }
    if (err == ESP_OK && unlink(next) != 0 && errno != ENOENT) {
        err = ESP_FAIL;
    }
    return err;
}

static cJSON *selected_json_from_decision(
    rehab_task_decision_handle_t handle,
    const cJSON *decision)
{
    const cJSON *task_id = cJSON_GetObjectItemCaseSensitive(
        decision, "selected_task_id");
    const cJSON *distance = cJSON_GetObjectItemCaseSensitive(
        decision, "suggested_distance_cm");
    const cJSON *generation = cJSON_GetObjectItemCaseSensitive(
        decision, "scan_generation");
    if (!cJSON_IsString(task_id) || task_id->valuestring == NULL) {
        return NULL;
    }
    char task_name[48] = {0};
    char rules_version[48] = {0};
    esp_err_t metadata_err = rehab_task_decision_rules_task_metadata(
        handle, task_id->valuestring, task_name, sizeof(task_name),
        rules_version, sizeof(rules_version));
    int distance_value = 0;
    int generation_value = 0;
    if (metadata_err != ESP_OK ||
        !json_integer(distance, 1, 300, &distance_value) ||
        !json_integer(generation, 1, INT32_MAX, &generation_value)) {
        return NULL;
    }
    cJSON *root = cJSON_CreateObject();
    if (root == NULL ||
        cJSON_AddStringToObject(root, "schema_version",
                               SELECTED_TASK_SCHEMA) == NULL ||
        cJSON_AddStringToObject(root, "task_id", task_id->valuestring) == NULL ||
        cJSON_AddStringToObject(root, "task_name", task_name) == NULL ||
        cJSON_AddNumberToObject(root, "suggested_distance_cm",
                               distance_value) == NULL ||
        cJSON_AddNumberToObject(root, "decision_generation",
                               generation_value) == NULL ||
        cJSON_AddStringToObject(root, "decision_rules_version",
                               rules_version) == NULL) {
        cJSON_Delete(root);
        return NULL;
    }
    return root;
}

esp_err_t rehab_task_decision_selected_clear_storage(
    rehab_task_decision_handle_t handle)
{
    if (handle == NULL || !selected_lock(handle)) {
        return handle == NULL ? ESP_ERR_INVALID_ARG : ESP_ERR_TIMEOUT;
    }
    char directory[SELECTED_TASK_PATH_MAX] = {0};
    char final_path[SELECTED_TASK_PATH_MAX] = {0};
    char temporary[SELECTED_TASK_PATH_MAX] = {0};
    char previous[SELECTED_TASK_PATH_MAX] = {0};
    esp_err_t err = selected_paths(directory, final_path, temporary);
    if (err == ESP_OK) {
        err = selected_aux_path(SELECTED_TASK_PREVIOUS_SUBPATH, previous);
    }
    if (err == ESP_OK) {
        err = selected_abort_staging_locked();
    }
    if (err == ESP_OK && unlink(final_path) != 0 && errno != ENOENT) {
        err = ESP_FAIL;
    }
    if (err == ESP_OK && unlink(previous) != 0 && errno != ENOENT) {
        err = ESP_FAIL;
    }
    selected_unlock(handle);
    return err;
}

esp_err_t rehab_task_decision_selected_invalidate(
    rehab_task_decision_handle_t handle)
{
    if (handle == NULL || !selected_lock(handle)) {
        return handle == NULL ? ESP_ERR_INVALID_ARG : ESP_ERR_TIMEOUT;
    }
    esp_err_t err = selected_abort_staging_locked();
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "SELECTED_TASK_STAGING_ABORTED current_preserved=1");
    }
    selected_unlock(handle);
    return err;
}

esp_err_t rehab_task_decision_selected_begin(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_selected_snapshot_t *out_snapshot,
    char out_sha256[REHAB_TASK_DECISION_SCENE_SHA256_MAX])
{
    if (handle == NULL || out_snapshot == NULL || out_sha256 == NULL ||
        !selected_lock(handle)) {
        return handle == NULL || out_snapshot == NULL || out_sha256 == NULL
            ? ESP_ERR_INVALID_ARG : ESP_ERR_TIMEOUT;
    }
    memset(out_snapshot, 0, sizeof(*out_snapshot));
    out_sha256[0] = '\0';
    char directory[SELECTED_TASK_PATH_MAX] = {0};
    char final_path[SELECTED_TASK_PATH_MAX] = {0};
    char temporary[SELECTED_TASK_PATH_MAX] = {0};
    char previous[SELECTED_TASK_PATH_MAX] = {0};
    esp_err_t err = selected_paths(directory, final_path, temporary);
    if (err == ESP_OK) {
        err = selected_aux_path(SELECTED_TASK_PREVIOUS_SUBPATH, previous);
    }
    if (err == ESP_OK) {
        err = selected_abort_staging_locked();
    }
    selected_task_value_t value = {0};
    if (err == ESP_OK) {
        err = selected_recover_locked(handle, final_path, previous, &value);
    }
    if (err == ESP_OK) {
        selected_snapshot_from_value(&value, out_snapshot);
        err = selected_file_sha256(final_path, out_sha256);
    } else if (err == ESP_ERR_NOT_FOUND) {
        err = ESP_OK;
    }
    if (err == ESP_OK) {
        ESP_LOGI(TAG,
                 "SELECTED_TASK_TRANSACTION_BEGIN current_available=%d sha256=%s",
                 out_snapshot->available,
                 out_snapshot->available ? out_sha256 : "none");
    }
    selected_unlock(handle);
    return err;
}

esp_err_t rehab_task_decision_selected_initialize(
    rehab_task_decision_handle_t handle)
{
    if (handle == NULL || !selected_lock(handle)) {
        return handle == NULL ? ESP_ERR_INVALID_ARG : ESP_ERR_TIMEOUT;
    }
    char directory[SELECTED_TASK_PATH_MAX] = {0};
    char final_path[SELECTED_TASK_PATH_MAX] = {0};
    char temporary[SELECTED_TASK_PATH_MAX] = {0};
    char previous[SELECTED_TASK_PATH_MAX] = {0};
    esp_err_t err = selected_paths(directory, final_path, temporary);
    if (err == ESP_OK) {
        err = selected_aux_path(SELECTED_TASK_PREVIOUS_SUBPATH, previous);
    }
    if (err == ESP_OK) {
        err = selected_abort_staging_locked();
    }
    selected_task_value_t value = {0};
    if (err == ESP_OK) {
        err = selected_recover_locked(handle, final_path, previous, &value);
        if (err == ESP_ERR_NOT_FOUND) {
            err = ESP_OK;
        } else if (err == ESP_OK) {
            handle->run_generation = value.generation;
            handle->status.generation = value.generation;
            handle->status.state = REHAB_TASK_DECISION_STATE_READY_SELECTED;
            handle->status.selected_task_available = true;
            strlcpy(handle->status.last_error, "none",
                    sizeof(handle->status.last_error));
            ESP_LOGI(TAG,
                     "SELECTED_TASK_RECOVERY available=1 generation=%" PRIu32
                     " task_id=%s",
                     value.generation, value.task_id);
        }
    }
    selected_unlock(handle);
    return err;
}

static esp_err_t selected_write_transaction_locked(
    rehab_task_decision_handle_t handle,
    const cJSON *selected,
    selected_task_value_t *out_value)
{
    char directory[SELECTED_TASK_PATH_MAX] = {0};
    char final_path[SELECTED_TASK_PATH_MAX] = {0};
    char legacy_temporary[SELECTED_TASK_PATH_MAX] = {0};
    char next[SELECTED_TASK_PATH_MAX] = {0};
    char previous[SELECTED_TASK_PATH_MAX] = {0};
    char *json = NULL;
    bool rollback_created = false;
    esp_err_t result = selected_paths(directory, final_path, legacy_temporary);
    if (result == ESP_OK) {
        result = selected_aux_path(SELECTED_TASK_NEXT_SUBPATH, next);
    }
    if (result == ESP_OK) {
        result = selected_aux_path(SELECTED_TASK_PREVIOUS_SUBPATH, previous);
    }
    if (result == ESP_OK) {
        result = ensure_directory(directory);
    }
    if (result != ESP_OK) {
        goto cleanup;
    }
    json = cJSON_PrintUnformatted(selected);
    if (json == NULL || strlen(json) > SELECTED_TASK_FILE_MAX) {
        result = json == NULL ? ESP_ERR_NO_MEM : ESP_ERR_INVALID_SIZE;
        goto cleanup;
    }
    (void)unlink(next);
    FILE *file = fopen(next, "wb");
    if (file == NULL) {
        result = ESP_FAIL;
        goto cleanup;
    }
    size_t length = strlen(json);
    bool written = fwrite(json, 1, length, file) == length &&
        fflush(file) == 0 && !ferror(file) &&
        fsync(fileno(file)) == 0;
    if (fclose(file) != 0) {
        written = false;
    }
    if (!written) {
        result = ESP_FAIL;
        goto cleanup;
    }
    selected_task_value_t next_value = {0};
    result = validate_file(handle, next, &next_value);
    if (result != ESP_OK) {
        goto cleanup;
    }

    selected_task_value_t current_value = {0};
    esp_err_t current_err = selected_recover_locked(
        handle, final_path, previous, &current_value);
    if (current_err != ESP_OK && current_err != ESP_ERR_NOT_FOUND) {
        result = current_err;
        goto cleanup;
    }
    if (current_err == ESP_OK) {
        if (unlink(previous) != 0 && errno != ENOENT) {
            result = ESP_FAIL;
            goto cleanup;
        }
        if (rename(final_path, previous) != 0) {
            result = ESP_FAIL;
            goto cleanup;
        }
        rollback_created = true;
    }
    if (rename(next, final_path) != 0) {
        result = ESP_FAIL;
        goto restore;
    }
    memset(&next_value, 0, sizeof(next_value));
    result = validate_file(handle, final_path, &next_value);
    if (result != ESP_OK) {
        (void)unlink(final_path);
        goto restore;
    }
    if (rollback_created && unlink(previous) != 0 && errno != ENOENT) {
        ESP_LOGW(TAG, "SELECTED_TASK_ROLLBACK_CLEANUP_FAILED path=%s",
                 previous);
    }
    if (out_value != NULL) {
        memcpy(out_value, &next_value, sizeof(*out_value));
    }
    result = ESP_OK;
    goto cleanup;

restore:
    if (rollback_created) {
        if (rename(previous, final_path) != 0) {
            ESP_LOGE(TAG,
                     "SELECTED_TASK_ROLLBACK_RESTORE_FAILED current=%s rollback=%s",
                     final_path, previous);
            result = ESP_FAIL;
        } else {
            selected_task_value_t restored = {0};
            if (validate_file(handle, final_path, &restored) != ESP_OK) {
                result = ESP_FAIL;
            } else {
                ESP_LOGW(TAG,
                         "SELECTED_TASK_ROLLBACK_RESTORED task_id=%s",
                         restored.task_id);
            }
        }
    }

cleanup:
    (void)unlink(next);
    cJSON_free(json);
    return result;
}

esp_err_t rehab_task_decision_selected_commit(
    rehab_task_decision_handle_t handle,
    const cJSON *decision)
{
    if (handle == NULL || !cJSON_IsObject(decision) || !selected_lock(handle)) {
        return handle == NULL || !cJSON_IsObject(decision)
            ? ESP_ERR_INVALID_ARG : ESP_ERR_TIMEOUT;
    }
    cJSON *selected = NULL;
    selected = selected_json_from_decision(handle, decision);
    if (selected == NULL) {
        selected_unlock(handle);
        return ESP_ERR_INVALID_ARG;
    }
    selected_task_value_t checked = {0};
    esp_err_t result = selected_write_transaction_locked(
        handle, selected, &checked);
    if (result == ESP_OK) {
        ESP_LOGI(TAG,
                 "SELECTED_TASK_COMMIT generation=%" PRIu32 " task_id=%s",
                 checked.generation, checked.task_id);
    }
    cJSON_Delete(selected);
    selected_unlock(handle);
    return result;
}

esp_err_t rehab_task_decision_selected_commit_v2(
    rehab_task_decision_handle_t handle,
    const cJSON *selected)
{
    if (handle == NULL || !cJSON_IsObject(selected) ||
        !selected_lock(handle)) {
        return handle == NULL || !cJSON_IsObject(selected)
            ? ESP_ERR_INVALID_ARG : ESP_ERR_TIMEOUT;
    }
    selected_task_value_t parsed = {0};
    esp_err_t result = selected_parse_v2(handle, selected, &parsed);
    if (result != ESP_OK) {
        selected_unlock(handle);
        return result;
    }
    memset(&parsed, 0, sizeof(parsed));
    result = selected_write_transaction_locked(handle, selected, &parsed);
    if (result == ESP_OK) {
        ESP_LOGI(TAG,
                 "SELECTED_TASK_V2_COMMIT generation=%" PRIu32
                 " decision_id=%s task_id=%s",
                 parsed.generation, parsed.decision_id, parsed.task_id);
    }
    selected_unlock(handle);
    return result;
}

static esp_err_t selected_finish(cJSON *root, char *output, size_t output_size)
{
    if (root == NULL || output == NULL || output_size == 0) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (json == NULL) {
        return ESP_ERR_NO_MEM;
    }
    size_t length = strlen(json);
    if (length >= output_size) {
        cJSON_free(json);
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(output, json, length + 1U);
    cJSON_free(json);
    return ESP_OK;
}

esp_err_t rehab_task_decision_selected_get(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_selected_snapshot_t *out_snapshot)
{
    if (handle == NULL || out_snapshot == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_snapshot, 0, sizeof(*out_snapshot));
    if (!selected_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }

    char directory[SELECTED_TASK_PATH_MAX] = {0};
    char final_path[SELECTED_TASK_PATH_MAX] = {0};
    char temporary[SELECTED_TASK_PATH_MAX] = {0};
    char previous[SELECTED_TASK_PATH_MAX] = {0};
    selected_task_value_t value = {0};
    esp_err_t err = selected_paths(directory, final_path, temporary);
    if (err == ESP_OK) {
        err = selected_aux_path(SELECTED_TASK_PREVIOUS_SUBPATH, previous);
    }
    if (err == ESP_OK) {
        err = selected_recover_locked(handle, final_path, previous, &value);
    }
    if (err == ESP_OK) {
        selected_snapshot_from_value(&value, out_snapshot);
    } else if (err == ESP_ERR_NOT_FOUND) {
        err = ESP_OK;
    } else {
        ESP_LOGW(TAG, "SELECTED_TASK_TYPED_GET invalid_file err=%s",
                 esp_err_to_name(err));
        err = ESP_OK;
    }
    selected_unlock(handle);
    return err;
}

esp_err_t rehab_task_decision_selected_render(
    rehab_task_decision_handle_t handle,
    char *output,
    size_t output_size)
{
    if (handle == NULL || output == NULL || output_size == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    rehab_task_decision_selected_snapshot_t snapshot = {0};
    esp_err_t err = rehab_task_decision_selected_get(handle, &snapshot);
    bool available = err == ESP_OK && snapshot.available;

    cJSON *root = cJSON_CreateObject();
    if (root != NULL) {
        cJSON_AddBoolToObject(root, "available", available);
        if (available) {
            cJSON_AddStringToObject(root, "schema_version",
                                    snapshot.source_scene_sha256[0] != '\0'
                                        ? SELECTED_TASK_SCHEMA_V2
                                        : SELECTED_TASK_SCHEMA);
            cJSON_AddStringToObject(root, "task_id", snapshot.task_id);
            cJSON_AddStringToObject(root, "task_name", snapshot.task_name);
            cJSON_AddNumberToObject(root, "suggested_distance_cm",
                                    snapshot.suggested_distance_cm);
            cJSON_AddNumberToObject(root, "decision_generation",
                                    snapshot.decision_generation);
            cJSON_AddStringToObject(root, "decision_rules_version",
                                    snapshot.decision_rules_version);
            if (snapshot.source_scene_sha256[0] != '\0') {
                cJSON_AddStringToObject(root, "reason", snapshot.reason);
                cJSON_AddNumberToObject(root, "score", snapshot.score);
                cJSON_AddStringToObject(root, "decision_id",
                                        snapshot.decision_id);
                cJSON_AddStringToObject(root, "source_scan_id",
                                        snapshot.source_scan_id);
                cJSON_AddStringToObject(root, "source_scene_sha256",
                                        snapshot.source_scene_sha256);
                cJSON_AddBoolToObject(root, "target_available",
                                      snapshot.target_available);
                if (snapshot.target_available) {
                    cJSON_AddStringToObject(root, "target_object_id",
                                            snapshot.target_object_id);
                    cJSON_AddStringToObject(root, "target_class",
                                            snapshot.target_class);
                    cJSON_AddStringToObject(root, "target_source_frame_id",
                                            snapshot.target_source_frame_id);
                    cJSON_AddNumberToObject(
                        root, "target_source_frame_sequence",
                        snapshot.target_source_frame_sequence);
                    cJSON *bbox = cJSON_AddArrayToObject(root,
                                                         "target_bbox_xyxy");
                    for (size_t i = 0; bbox != NULL && i < 4U; ++i) {
                        cJSON_AddItemToArray(
                            bbox,
                            cJSON_CreateNumber(snapshot.target_bbox_xyxy[i]));
                    }
                    cJSON *geometry = cJSON_AddObjectToObject(
                        root, "target_geometry");
                    if (geometry != NULL) {
                        cJSON_AddBoolToObject(
                            geometry, "available",
                            snapshot.target_geometry_available);
                        cJSON_AddBoolToObject(
                            geometry, "valid",
                            snapshot.target_geometry_valid);
                        if (snapshot.target_object_height_available) {
                            cJSON_AddNumberToObject(
                                geometry, "object_height_mm",
                                snapshot.target_object_height_mm);
                        }
                        if (snapshot.target_support_height_available) {
                            cJSON_AddNumberToObject(
                                geometry, "support_height_mm",
                                snapshot.target_support_height_mm);
                        }
                        if (snapshot.target_top_height_available) {
                            cJSON_AddNumberToObject(
                                geometry, "top_height_above_floor_mm",
                                snapshot.target_top_height_above_floor_mm);
                        }
                        if (snapshot.target_distance_available) {
                            cJSON_AddNumberToObject(
                                geometry, "distance_mm",
                                snapshot.target_distance_mm);
                        }
                        if (snapshot.target_distance_status[0] != '\0') {
                            cJSON_AddStringToObject(
                                geometry, "distance_status",
                                snapshot.target_distance_status);
                        }
                    }
                }
            }
        }
    }
    return selected_finish(root, output, output_size);
}
