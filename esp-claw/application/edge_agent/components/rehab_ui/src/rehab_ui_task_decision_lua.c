// SPDX-License-Identifier: Apache-2.0
#include "rehab_ui.h"

#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "cap_lua.h"
#include "cJSON.h"
#include "claw_paths.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "lauxlib.h"
#include "lua.h"
#include "lua_image.h"

#define REHAB_TASK_DECISION_UI_LUA_MODULE "rehab_task_decision_ui"
#define TARGET_IMAGE_WIDTH 640
#define TARGET_IMAGE_HEIGHT 360
#define TARGET_IMAGE_BYTES (TARGET_IMAGE_WIDTH * TARGET_IMAGE_HEIGHT * 2U)
#define REASONING_ARTIFACT_SUBPATH \
    "rehab/latest_decision_reasoning.json"
#define REASONING_ARTIFACT_SCHEMA "rehab_decision_reasoning.v1"
#define REASONING_ARTIFACT_MAX_BYTES (128U * 1024U)

static rehab_task_decision_handle_t s_task_decision;
static bool s_registered;

static bool push_json_value(lua_State *L, const cJSON *item, unsigned depth)
{
    if (item == NULL || depth > 16U) {
        lua_pushnil(L);
        return false;
    }
    if (cJSON_IsNull(item)) {
        lua_pushnil(L);
    } else if (cJSON_IsBool(item)) {
        lua_pushboolean(L, cJSON_IsTrue(item));
    } else if (cJSON_IsNumber(item)) {
        lua_pushnumber(L, item->valuedouble);
    } else if (cJSON_IsString(item) && item->valuestring != NULL) {
        lua_pushstring(L, item->valuestring);
    } else if (cJSON_IsArray(item)) {
        lua_newtable(L);
        const cJSON *child = NULL;
        lua_Integer index = 1;
        cJSON_ArrayForEach(child, item) {
            (void)push_json_value(L, child, depth + 1U);
            lua_rawseti(L, -2, index++);
        }
    } else if (cJSON_IsObject(item)) {
        lua_newtable(L);
        const cJSON *child = NULL;
        cJSON_ArrayForEach(child, item) {
            if (child->string != NULL) {
                (void)push_json_value(L, child, depth + 1U);
                lua_setfield(L, -2, child->string);
            }
        }
    } else {
        lua_pushnil(L);
        return false;
    }
    return true;
}

static const char *details_error_code(esp_err_t err)
{
    if (err == ESP_ERR_NOT_FOUND) return "missing";
    if (err == ESP_ERR_INVALID_STATE) return "decision_mismatch";
    if (err == ESP_ERR_INVALID_RESPONSE || err == ESP_ERR_INVALID_SIZE) {
        return "invalid";
    }
    return "io_error";
}

static int lua_task_decision_details(lua_State *L)
{
    const char *expected = luaL_optstring(L, 1, NULL);
    char *json = heap_caps_calloc(
        1U, REHAB_TASK_DECISION_DETAILS_JSON_MAX,
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (json == NULL) {
        json = heap_caps_calloc(
            1U, REHAB_TASK_DECISION_DETAILS_JSON_MAX, MALLOC_CAP_8BIT);
    }
    esp_err_t err = json == NULL ? ESP_ERR_NO_MEM :
        (s_task_decision == NULL ? ESP_ERR_INVALID_STATE :
         rehab_task_decision_details_render(
             s_task_decision, expected, json,
             REHAB_TASK_DECISION_DETAILS_JSON_MAX));
    cJSON *root = err == ESP_OK
        ? cJSON_ParseWithOpts(json, NULL, true) : NULL;
    if (err == ESP_OK && !cJSON_IsObject(root)) {
        err = ESP_ERR_INVALID_RESPONSE;
    }
    if (err == ESP_OK) {
        (void)push_json_value(L, root, 0U);
        lua_pushboolean(L, true);
        lua_setfield(L, -2, "ok");
        lua_pushboolean(L, true);
        lua_setfield(L, -2, "available");
        lua_pushliteral(L, "ok");
        lua_setfield(L, -2, "code");
    } else {
        lua_newtable(L);
        lua_pushboolean(L, false);
        lua_setfield(L, -2, "ok");
        lua_pushboolean(L, false);
        lua_setfield(L, -2, "available");
        lua_pushstring(L, details_error_code(err));
        lua_setfield(L, -2, "code");
        lua_pushstring(L, esp_err_to_name(err));
        lua_setfield(L, -2, "error");
    }
    cJSON_Delete(root);
    heap_caps_free(json);
    return 1;
}

static esp_err_t reasoning_artifact_path(char *path, size_t path_size)
{
    return claw_paths_join(CLAW_PATH_DATA, REASONING_ARTIFACT_SUBPATH,
                           path, path_size);
}

static esp_err_t reasoning_artifact_stat(const char *path, size_t *out_bytes)
{
    struct stat st = {0};
    if (stat(path, &st) != 0) {
        return errno == ENOENT ? ESP_ERR_NOT_FOUND : ESP_FAIL;
    }
    if (st.st_size <= 0 ||
        (uint64_t)st.st_size > REASONING_ARTIFACT_MAX_BYTES) {
        return ESP_ERR_INVALID_SIZE;
    }
    if (out_bytes != NULL) {
        *out_bytes = (size_t)st.st_size;
    }
    return ESP_OK;
}

static bool reasoning_string(const cJSON *root, const char *name,
                             bool allow_empty)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, name);
    return cJSON_IsString(item) && item->valuestring != NULL &&
        (allow_empty || item->valuestring[0] != '\0');
}

static bool reasoning_nonnegative_number(const cJSON *root,
                                         const char *name,
                                         bool integer_only)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, name);
    return cJSON_IsNumber(item) && isfinite(item->valuedouble) &&
        item->valuedouble >= 0.0 &&
        (!integer_only || floor(item->valuedouble) == item->valuedouble);
}

static bool reasoning_artifact_valid(const cJSON *root)
{
    const cJSON *schema = cJSON_GetObjectItemCaseSensitive(
        root, "schema_version");
    const cJSON *content = cJSON_GetObjectItemCaseSensitive(
        root, "reasoning_content");
    const cJSON *bytes = cJSON_GetObjectItemCaseSensitive(
        root, "reasoning_bytes");
    return cJSON_IsObject(root) && cJSON_GetArraySize(root) == 10 &&
        cJSON_IsString(schema) && schema->valuestring != NULL &&
        strcmp(schema->valuestring, REASONING_ARTIFACT_SCHEMA) == 0 &&
        reasoning_string(root, "decision_id", false) &&
        reasoning_nonnegative_number(root, "generation", true) &&
        reasoning_string(root, "task_id", false) &&
        reasoning_string(root, "task_name", false) &&
        reasoning_nonnegative_number(root, "latency_sec", false) &&
        reasoning_nonnegative_number(root, "reasoning_tokens", true) &&
        reasoning_nonnegative_number(root, "reasoning_bytes", true) &&
        reasoning_string(root, "reasoning_content", false) &&
        reasoning_string(root, "created_at", false) &&
        cJSON_IsString(content) && content->valuestring != NULL &&
        cJSON_IsNumber(bytes) &&
        bytes->valuedouble == (double)strlen(content->valuestring);
}

static cJSON *reasoning_artifact_read(esp_err_t *out_err)
{
    char path[320] = {0};
    size_t json_bytes = 0U;
    esp_err_t err = reasoning_artifact_path(path, sizeof(path));
    if (err == ESP_OK) {
        err = reasoning_artifact_stat(path, &json_bytes);
    }
    if (err != ESP_OK) {
        *out_err = err;
        return NULL;
    }

    char *json = heap_caps_malloc(
        json_bytes + 1U, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (json == NULL) {
        json = heap_caps_malloc(json_bytes + 1U, MALLOC_CAP_8BIT);
    }
    if (json == NULL) {
        *out_err = ESP_ERR_NO_MEM;
        return NULL;
    }

    FILE *file = fopen(path, "rb");
    const size_t read_bytes = file != NULL
        ? fread(json, 1U, json_bytes, file) : 0U;
    bool closed = file != NULL && fclose(file) == 0;
    if (read_bytes != json_bytes || !closed) {
        heap_caps_free(json);
        *out_err = ESP_FAIL;
        return NULL;
    }
    json[json_bytes] = '\0';

    cJSON *root = cJSON_ParseWithOpts(json, NULL, true);
    heap_caps_free(json);
    if (!reasoning_artifact_valid(root)) {
        cJSON_Delete(root);
        *out_err = ESP_ERR_INVALID_RESPONSE;
        return NULL;
    }
    *out_err = ESP_OK;
    return root;
}

static const char *reasoning_error_code(esp_err_t err)
{
    if (err == ESP_ERR_NOT_FOUND) return "missing";
    if (err == ESP_ERR_INVALID_STATE) return "decision_mismatch";
    if (err == ESP_ERR_INVALID_RESPONSE || err == ESP_ERR_INVALID_SIZE) {
        return "invalid";
    }
    return err == ESP_ERR_NO_MEM ? "no_memory" : "io_error";
}

static int lua_task_decision_reasoning_available(lua_State *L)
{
    const char *expected_decision_id = luaL_optstring(L, 1, NULL);
    char path[320] = {0};
    esp_err_t err = reasoning_artifact_path(path, sizeof(path));
    if (err == ESP_OK) {
        err = reasoning_artifact_stat(path, NULL);
    }
    if (err == ESP_OK && expected_decision_id != NULL &&
        expected_decision_id[0] != '\0') {
        cJSON *root = reasoning_artifact_read(&err);
        const cJSON *decision_id = root != NULL
            ? cJSON_GetObjectItemCaseSensitive(root, "decision_id") : NULL;
        if (err == ESP_OK && (!cJSON_IsString(decision_id) ||
            decision_id->valuestring == NULL ||
            strcmp(decision_id->valuestring, expected_decision_id) != 0)) {
            err = ESP_ERR_INVALID_STATE;
        }
        cJSON_Delete(root);
    }
    lua_pushboolean(L, err == ESP_OK);
    return 1;
}

static int lua_task_decision_reasoning(lua_State *L)
{
    const char *expected_decision_id = luaL_optstring(L, 1, NULL);
    esp_err_t err = ESP_OK;
    cJSON *root = reasoning_artifact_read(&err);
    const cJSON *decision_id = root != NULL
        ? cJSON_GetObjectItemCaseSensitive(root, "decision_id") : NULL;
    if (err == ESP_OK && expected_decision_id != NULL &&
        expected_decision_id[0] != '\0' &&
        (!cJSON_IsString(decision_id) || decision_id->valuestring == NULL ||
         strcmp(decision_id->valuestring, expected_decision_id) != 0)) {
        cJSON_Delete(root);
        root = NULL;
        err = ESP_ERR_INVALID_STATE;
    }
    lua_newtable(L);
    lua_pushboolean(L, err == ESP_OK);
    lua_setfield(L, -2, "ok");
    lua_pushboolean(L, err == ESP_OK);
    lua_setfield(L, -2, "available");
    lua_pushstring(L, err == ESP_OK ? "ok" : reasoning_error_code(err));
    lua_setfield(L, -2, "code");
    if (err != ESP_OK) {
        lua_pushstring(L, esp_err_to_name(err));
        lua_setfield(L, -2, "error");
        return 1;
    }

    static const char *fields[] = {
        "schema_version", "decision_id", "generation", "task_id",
        "task_name", "latency_sec", "reasoning_tokens",
        "reasoning_bytes", "reasoning_content", "created_at",
    };
    for (size_t i = 0U; i < sizeof(fields) / sizeof(fields[0]); ++i) {
        const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, fields[i]);
        (void)push_json_value(L, item, 0U);
        lua_setfield(L, -2, fields[i]);
    }
    cJSON_Delete(root);
    return 1;
}

static void push_status(
    lua_State *L,
    esp_err_t err,
    const rehab_task_decision_coordinator_status_t *status)
{
    lua_newtable(L);
    lua_pushboolean(L, err == ESP_OK);
    lua_setfield(L, -2, "ok");
    lua_pushstring(L, esp_err_to_name(err));
    lua_setfield(L, -2, "error");
    if (status == NULL) {
        return;
    }
    lua_pushstring(L, rehab_task_decision_coordinator_state_name(
        status->state));
    lua_setfield(L, -2, "state");
    lua_pushboolean(L, status->active);
    lua_setfield(L, -2, "active");
    lua_pushstring(L, rehab_decision_trace_stage_name(status->trace.stage));
    lua_setfield(L, -2, "trace_stage");
    lua_pushinteger(L, status->trace.elapsed_sec);
    lua_setfield(L, -2, "elapsed_sec");
    lua_pushinteger(L, status->trace.stage_seq);
    lua_setfield(L, -2, "trace_stage_seq");
    lua_pushboolean(L, status->trace.active);
    lua_setfield(L, -2, "trace_active");
    lua_pushstring(L, status->trace.failure_code);
    lua_setfield(L, -2, "trace_failure_code");
    lua_pushinteger(L, status->generation);
    lua_setfield(L, -2, "generation");
    lua_pushstring(L, status->decision_id);
    lua_setfield(L, -2, "decision_id");
    lua_pushstring(L, status->source_scan_id);
    lua_setfield(L, -2, "source_scan_id");
    lua_pushstring(L, status->source_scene_sha256);
    lua_setfield(L, -2, "source_scene_sha256");
    lua_pushstring(L, status->task_id);
    lua_setfield(L, -2, "task_id");
    lua_pushstring(L, status->task_name);
    lua_setfield(L, -2, "task_name");
    lua_pushstring(L, status->reason);
    lua_setfield(L, -2, "reason");
    lua_pushnumber(L, status->score);
    lua_setfield(L, -2, "score");
    lua_pushboolean(L, status->target_available);
    lua_setfield(L, -2, "target_available");
    lua_pushstring(L, status->target_object_id);
    lua_setfield(L, -2, "target_object_id");
    lua_pushstring(L, status->target_class);
    lua_setfield(L, -2, "target_class");
    lua_pushstring(L, status->target_source_frame_id);
    lua_setfield(L, -2, "target_source_frame_id");
    lua_pushinteger(L, status->target_source_frame_sequence);
    lua_setfield(L, -2, "target_source_frame_sequence");
    lua_newtable(L);
    for (size_t i = 0; i < 4U; ++i) {
        lua_pushnumber(L, status->target_bbox_xyxy[i]);
        lua_rawseti(L, -2, (lua_Integer)i + 1);
    }
    lua_setfield(L, -2, "target_bbox_xyxy");
    lua_newtable(L);
    lua_pushboolean(L, status->target_geometry_available);
    lua_setfield(L, -2, "available");
    lua_pushboolean(L, status->target_geometry_valid);
    lua_setfield(L, -2, "valid");
    if (status->target_object_height_available) {
        lua_pushnumber(L, status->target_object_height_mm);
        lua_setfield(L, -2, "object_height_mm");
    }
    if (status->target_support_height_available) {
        lua_pushnumber(L, status->target_support_height_mm);
        lua_setfield(L, -2, "support_height_mm");
    }
    if (status->target_top_height_available) {
        lua_pushnumber(L, status->target_top_height_above_floor_mm);
        lua_setfield(L, -2, "top_height_above_floor_mm");
    }
    if (status->target_distance_available) {
        lua_pushnumber(L, status->target_distance_mm);
        lua_setfield(L, -2, "distance_mm");
    }
    lua_pushstring(L, status->target_distance_status);
    lua_setfield(L, -2, "distance_status");
    lua_setfield(L, -2, "target_geometry");
    lua_pushboolean(L, status->retry_used);
    lua_setfield(L, -2, "retry_used");
    lua_pushstring(L, status->last_error);
    lua_setfield(L, -2, "last_error");
}

static int lua_task_decision_start(lua_State *L)
{
    rehab_task_decision_start_result_t result = {0};
    rehab_task_decision_coordinator_status_t *status = heap_caps_calloc(
        1U, sizeof(*status), MALLOC_CAP_8BIT);
    esp_err_t err = status == NULL ? ESP_ERR_NO_MEM :
        (s_task_decision == NULL ? ESP_ERR_INVALID_STATE :
         rehab_task_decision_start_confirmed(
             s_task_decision, REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI,
             &result, status));
    const bool usable = err == ESP_OK &&
        result.outcome != REHAB_TASK_DECISION_START_OUTCOME_REJECTED;
    push_status(L, err != ESP_OK ? err :
                (usable ? ESP_OK : ESP_ERR_INVALID_STATE),
                err == ESP_OK ? status : NULL);
    lua_pushboolean(L, usable);
    lua_setfield(L, -2, "accepted");
    lua_pushstring(L, rehab_task_decision_start_outcome_name(result.outcome));
    lua_setfield(L, -2, "outcome");
    lua_pushstring(L, err == ESP_OK && !usable
        ? result.last_error : (err == ESP_OK
            ? rehab_task_decision_start_outcome_name(result.outcome)
            : esp_err_to_name(err)));
    lua_setfield(L, -2, "error");
    lua_pushboolean(L, result.outcome ==
        REHAB_TASK_DECISION_START_OUTCOME_ATTACHED_BUSY);
    lua_setfield(L, -2, "busy");
    if (status != NULL) {
        heap_caps_free(status);
    }
    return 1;
}

static int lua_task_decision_status(lua_State *L)
{
    rehab_task_decision_coordinator_status_t status = {0};
    esp_err_t err = s_task_decision == NULL
        ? ESP_ERR_INVALID_STATE
        : rehab_task_decision_coordinator_get_status(
            s_task_decision, &status);
    push_status(L, err, err == ESP_OK ? &status : NULL);
    return 1;
}

static int lua_task_decision_scene_status(lua_State *L)
{
    char scan_id[REHAB_TASK_DECISION_SCAN_ID_MAX] = {0};
    char sha256[REHAB_TASK_DECISION_SCENE_SHA256_MAX] = {0};
    esp_err_t err = s_task_decision == NULL
        ? ESP_ERR_INVALID_STATE
        : rehab_task_decision_latest_scene_validate(
            s_task_decision, scan_id, sizeof(scan_id),
            sha256, sizeof(sha256));
    lua_newtable(L);
    lua_pushboolean(L, s_task_decision != NULL);
    lua_setfield(L, -2, "ok");
    lua_pushboolean(L, err == ESP_OK);
    lua_setfield(L, -2, "available");
    const char *code = err == ESP_OK ? "ok" :
        (err == ESP_ERR_NOT_FOUND ? "missing" :
         (err == ESP_ERR_INVALID_RESPONSE ||
          err == ESP_ERR_INVALID_SIZE ||
          err == ESP_ERR_INVALID_CRC ? "invalid" : "io_error"));
    lua_pushstring(L, code);
    lua_setfield(L, -2, "code");
    lua_pushstring(L, esp_err_to_name(err));
    lua_setfield(L, -2, "error");
    lua_pushstring(L, scan_id);
    lua_setfield(L, -2, "scan_id");
    lua_pushstring(L, sha256);
    lua_setfield(L, -2, "sha256");
    return 1;
}

static void free_frame(void *ctx, const uint8_t *data)
{
    (void)ctx;
    heap_caps_free((void *)data);
}

static int lua_task_decision_target_image(lua_State *L)
{
    const char *expected_decision_id = luaL_optstring(L, 1, NULL);
    if (s_task_decision == NULL) {
        lua_pushnil(L);
        lua_pushliteral(L, "TASK_DECISION_UNAVAILABLE");
        return 2;
    }
    rehab_task_decision_selected_snapshot_t selected = {0};
    esp_err_t err = rehab_task_decision_selected_get(
        s_task_decision, &selected);
    if (err != ESP_OK || !selected.available ||
        (expected_decision_id != NULL && expected_decision_id[0] != '\0' &&
         strcmp(expected_decision_id, selected.decision_id) != 0) ||
        !selected.target_available ||
        selected.target_source_frame_id[0] == '\0' ||
        !isfinite(selected.target_bbox_xyxy[0]) ||
        !isfinite(selected.target_bbox_xyxy[1]) ||
        !isfinite(selected.target_bbox_xyxy[2]) ||
        !isfinite(selected.target_bbox_xyxy[3]) ||
        selected.target_bbox_xyxy[0] >= selected.target_bbox_xyxy[2] ||
        selected.target_bbox_xyxy[1] >= selected.target_bbox_xyxy[3]) {
        lua_pushnil(L);
        lua_pushliteral(L, "TARGET_IMAGE_UNAVAILABLE");
        return 2;
    }
    char relative[256];
    int written = snprintf(
        relative, sizeof(relative),
        "rehab/scans/%s/frames/frame_%06" PRIu32 "/image.rgb565",
        selected.source_scan_id,
        selected.target_source_frame_sequence);
    char path[320];
    if (written <= 0 || (size_t)written >= sizeof(relative) ||
        claw_paths_join(CLAW_PATH_DATA, relative,
                        path, sizeof(path)) != ESP_OK) {
        lua_pushnil(L);
        lua_pushliteral(L, "TARGET_IMAGE_PATH_INVALID");
        return 2;
    }
    struct stat st = {0};
    if (stat(path, &st) != 0 || st.st_size != TARGET_IMAGE_BYTES) {
        lua_pushnil(L);
        lua_pushliteral(L, "TARGET_IMAGE_SIZE_INVALID");
        return 2;
    }
    uint8_t *data = heap_caps_malloc(
        TARGET_IMAGE_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (data == NULL) {
        data = heap_caps_malloc(TARGET_IMAGE_BYTES, MALLOC_CAP_8BIT);
    }
    if (data == NULL) {
        return luaL_error(L, "TARGET_IMAGE_NO_MEMORY");
    }
    FILE *file = fopen(path, "rb");
    size_t bytes = file != NULL
        ? fread(data, 1, TARGET_IMAGE_BYTES, file) : 0U;
    if (file != NULL) {
        fclose(file);
    }
    if (bytes != TARGET_IMAGE_BYTES) {
        heap_caps_free(data);
        lua_pushnil(L);
        lua_pushliteral(L, "TARGET_IMAGE_READ_FAILED");
        return 2;
    }
    lua_image_frame_info_t info = {
        .width = TARGET_IMAGE_WIDTH,
        .height = TARGET_IMAGE_HEIGHT,
        .bytes = TARGET_IMAGE_BYTES,
        .timestamp_us = esp_timer_get_time(),
    };
    strlcpy(info.pixel_format, "RGBP", sizeof(info.pixel_format));
    err = lua_image_push_frame(L, data, TARGET_IMAGE_BYTES,
                               &info, free_frame, NULL);
    if (err != ESP_OK) {
        heap_caps_free(data);
        lua_pushnil(L);
        lua_pushstring(L, esp_err_to_name(err));
        return 2;
    }
    lua_pushnil(L);
    return 2;
}

static int luaopen_rehab_task_decision_ui(lua_State *L)
{
    static const luaL_Reg functions[] = {
        {"start", lua_task_decision_start},
        {"status", lua_task_decision_status},
        {"scene_status", lua_task_decision_scene_status},
        {"details", lua_task_decision_details},
        {"reasoning_available", lua_task_decision_reasoning_available},
        {"reasoning", lua_task_decision_reasoning},
        {"target_image", lua_task_decision_target_image},
        {NULL, NULL},
    };
    lua_newtable(L);
    luaL_setfuncs(L, functions, 0);
    return 1;
}

esp_err_t rehab_ui_task_decision_lua_register(void)
{
    if (s_registered) {
        return ESP_OK;
    }
    esp_err_t err = cap_lua_register_module(
        REHAB_TASK_DECISION_UI_LUA_MODULE,
        luaopen_rehab_task_decision_ui);
    if (err == ESP_OK) {
        s_registered = true;
    }
    return err;
}

void rehab_ui_task_decision_lua_bind(
    rehab_task_decision_handle_t task_decision)
{
    s_task_decision = task_decision;
}
