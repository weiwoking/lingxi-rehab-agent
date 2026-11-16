/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "rehab_system.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "claw_cap.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "rehab_business_audio.h"
#include "rehab_fatigue.h"
#include "rehab_score_bridge.h"
#include "rehab_sensor_recording.h"

static const char *TAG = "rehab_cap";

#define REHAB_TRAINING_STOP_WAIT_MS 3000
#define REHAB_STATUS_ENUM_STRING_CAPACITY 16

static const char s_rehab_mode_mock[sizeof("mock")] = "mock";
static const char s_rehab_mode_real[sizeof("real")] = "real";
static const char s_rehab_state_idle[sizeof("idle")] = "idle";
static const char s_rehab_not_initialized[sizeof("not_initialized")] = "not_initialized";

typedef struct {
    bool initialized;
    esp_err_t err;
    rehab_training_state_t state;
    uint32_t generation;
    char session_path[REHAB_TRAINING_SESSION_PATH_LEN];
    char last_error[REHAB_TRAINING_ERROR_MESSAGE_LEN];
} rehab_training_status_log_cache_t;

typedef struct {
    bool initialized;
    esp_err_t err;
    rehab_fatigue_status_t status;
} rehab_fatigue_status_log_cache_t;

static portMUX_TYPE s_training_status_log_lock = portMUX_INITIALIZER_UNLOCKED;
static rehab_training_status_log_cache_t s_training_status_log_cache;
static portMUX_TYPE s_fatigue_status_log_lock = portMUX_INITIALIZER_UNLOCKED;
static rehab_fatigue_status_log_cache_t s_fatigue_status_log_cache;

_Static_assert(sizeof(s_rehab_mode_mock) == 5, "mock status must include its NUL terminator");
_Static_assert(sizeof(s_rehab_state_idle) == 5, "idle status must include its NUL terminator");
_Static_assert(sizeof("/sdcard") == 8, "/sdcard status must include its NUL terminator");
_Static_assert(sizeof(s_rehab_not_initialized) == 16,
               "not_initialized status must include its NUL terminator");
_Static_assert(REHAB_STATUS_ENUM_STRING_CAPACITY >= sizeof("acceptable"),
               "status enum string capacity must include its NUL terminator");
_Static_assert(sizeof(rehab_system_snapshot_t) <= 128,
               "rehab capability stack snapshots must remain small");

static bool rehab_training_status_should_log(const rehab_training_status_t *status,
                                             esp_err_t err)
{
    bool changed;

    portENTER_CRITICAL(&s_training_status_log_lock);
    changed = !s_training_status_log_cache.initialized ||
              s_training_status_log_cache.err != err ||
              s_training_status_log_cache.state != status->state ||
              s_training_status_log_cache.generation != status->generation ||
              strcmp(s_training_status_log_cache.session_path, status->session_path) != 0 ||
              strcmp(s_training_status_log_cache.last_error, status->last_error) != 0;
    s_training_status_log_cache.initialized = true;
    s_training_status_log_cache.err = err;
    s_training_status_log_cache.state = status->state;
    s_training_status_log_cache.generation = status->generation;
    strlcpy(s_training_status_log_cache.session_path,
            status->session_path,
            sizeof(s_training_status_log_cache.session_path));
    strlcpy(s_training_status_log_cache.last_error,
            status->last_error,
            sizeof(s_training_status_log_cache.last_error));
    portEXIT_CRITICAL(&s_training_status_log_lock);
    return err != ESP_OK || changed;
}

static bool rehab_fatigue_status_should_log(const rehab_fatigue_status_t *status,
                                            esp_err_t err)
{
    bool changed;

    portENTER_CRITICAL(&s_fatigue_status_log_lock);
    changed = !s_fatigue_status_log_cache.initialized ||
              s_fatigue_status_log_cache.err != err ||
              s_fatigue_status_log_cache.status.available != status->available ||
              s_fatigue_status_log_cache.status.fatigue != status->fatigue ||
              s_fatigue_status_log_cache.status.training_allowed != status->training_allowed ||
              s_fatigue_status_log_cache.status.count_today != status->count_today ||
              s_fatigue_status_log_cache.status.decay_paused != status->decay_paused ||
              s_fatigue_status_log_cache.status.time_trusted != status->time_trusted;
    s_fatigue_status_log_cache.initialized = true;
    s_fatigue_status_log_cache.err = err;
    s_fatigue_status_log_cache.status = *status;
    portEXIT_CRITICAL(&s_fatigue_status_log_lock);
    return changed;
}

static void rehab_fatigue_status_log(const rehab_fatigue_status_t *status,
                                     esp_err_t err,
                                     const claw_cap_call_context_t *ctx)
{
    if (!rehab_fatigue_status_should_log(status, err)) {
        return;
    }
    ESP_LOG_LEVEL(err == ESP_OK && status->available ? ESP_LOG_INFO : ESP_LOG_WARN,
                  TAG,
                  "rehab_fatigue_status request_id=%lu available=%d fatigue=%d",
                  (unsigned long)(ctx ? ctx->request_id : 0),
                  status->available, status->fatigue);
}

static const char *rehab_mode_to_string(rehab_demo_mode_t mode)
{
    return mode == REHAB_DEMO_MODE_REAL ? s_rehab_mode_real : s_rehab_mode_mock;
}

static const char *rehab_state_to_string(rehab_system_state_t state)
{
    (void)state;
    return s_rehab_state_idle;
}

static bool rehab_json_add_bounded_string(cJSON *object,
                                          const char *key,
                                          const char *value,
                                          size_t capacity)
{
    if (!object || !key || !value || capacity == 0 ||
            strnlen(value, capacity) >= capacity) {
        return false;
    }
    return cJSON_AddStringToObject(object, key, value) != NULL;
}

static const char *rehab_training_component_state(void)
{
    rehab_training_status_t *status = calloc(1, sizeof(*status));
    const char *state = "unavailable";

    if (!status) {
        return state;
    }
    if (rehab_training_get_status(status) == ESP_OK) {
        state = rehab_training_state_to_string(status->state);
    }
    free(status);
    return state;
}

static void rehab_write_literal_if_fits(char *output, size_t output_size, const char *literal)
{
    size_t length;

    if (!output || output_size == 0) {
        return;
    }
    output[0] = '\0';
    if (!literal) {
        return;
    }

    length = strlen(literal);
    if (length + 1 <= output_size) {
        memcpy(output, literal, length + 1);
    }
}

static esp_err_t rehab_json_no_mem(cJSON *root, char *output, size_t output_size)
{
    cJSON_Delete(root);
    rehab_write_literal_if_fits(output,
                                output_size,
                                "{\"schema_version\":\"rehab.error.v1\",\"accepted\":false,\"code\":\"out_of_memory\"}");
    return ESP_ERR_NO_MEM;
}

static esp_err_t rehab_render_json(cJSON *root, char *output, size_t output_size)
{
    char *rendered = NULL;
    size_t rendered_size;

    if (!root || !output || output_size == 0) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }
    output[0] = '\0';

    rendered = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!rendered) {
        rehab_write_literal_if_fits(output,
                                    output_size,
                                    "{\"schema_version\":\"rehab.error.v1\",\"accepted\":false,\"code\":\"out_of_memory\"}");
        return ESP_ERR_NO_MEM;
    }

    rendered_size = strlen(rendered) + 1;
    if (rendered_size > output_size) {
        cJSON_free(rendered);
        rehab_write_literal_if_fits(output,
                                    output_size,
                                    "{\"schema_version\":\"rehab.error.v1\",\"accepted\":false,\"code\":\"output_too_small\"}");
        return ESP_ERR_INVALID_SIZE;
    }

    memcpy(output, rendered, rendered_size);
    cJSON_free(rendered);
    return ESP_OK;
}

static esp_err_t rehab_write_error(char *output,
                                   size_t output_size,
                                   const char *code,
                                   const char *reason,
                                   const rehab_system_snapshot_t *snapshot)
{
    cJSON *root = cJSON_CreateObject();

    if (!root) {
        return rehab_json_no_mem(NULL, output, output_size);
    }
    if (!cJSON_AddStringToObject(root, "schema_version", "rehab.error.v1") ||
            !cJSON_AddBoolToObject(root, "accepted", false) ||
            !cJSON_AddStringToObject(root, "code", code) ||
            !cJSON_AddStringToObject(root, "reason", reason)) {
        return rehab_json_no_mem(root, output, output_size);
    }
    if (snapshot &&
            (!cJSON_AddStringToObject(root, "state", rehab_state_to_string(snapshot->state)) ||
             !cJSON_AddStringToObject(root, "mode", rehab_mode_to_string(snapshot->mode)) ||
             !cJSON_AddNumberToObject(root, "generation", snapshot->generation))) {
        return rehab_json_no_mem(root, output, output_size);
    }
    return rehab_render_json(root, output, output_size);
}

static esp_err_t rehab_parse_empty_object(const char *input_json,
                                          const char **out_code,
                                          const char **out_reason)
{
    cJSON *root = cJSON_Parse(input_json ? input_json : "{}");

    if (!root || !cJSON_IsObject(root)) {
        cJSON_Delete(root);
        *out_code = "invalid_json";
        *out_reason = "input must be a valid JSON object";
        return ESP_ERR_INVALID_ARG;
    }
    if (cJSON_GetArraySize(root) != 0) {
        cJSON_Delete(root);
        *out_code = "unexpected_field";
        *out_reason = "this capability accepts an empty object only";
        return ESP_ERR_INVALID_ARG;
    }

    cJSON_Delete(root);
    return ESP_OK;
}

static esp_err_t rehab_parse_mode(const char *input_json,
                                  rehab_demo_mode_t *out_mode,
                                  const char **out_code,
                                  const char **out_reason)
{
    cJSON *root = cJSON_Parse(input_json ? input_json : "{}");
    cJSON *field = NULL;
    cJSON *mode_item = NULL;
    size_t mode_count = 0;
    esp_err_t err = ESP_ERR_INVALID_ARG;

    if (!root || !cJSON_IsObject(root)) {
        *out_code = "invalid_json";
        *out_reason = "input must be a valid JSON object";
        goto cleanup;
    }

    cJSON_ArrayForEach(field, root) {
        if (!field->string || strcmp(field->string, "mode") != 0) {
            *out_code = "unexpected_field";
            *out_reason = "only the mode field is allowed";
            goto cleanup;
        }
        mode_count++;
    }
    if (mode_count == 0) {
        *out_code = "missing_mode";
        *out_reason = "mode is required";
        goto cleanup;
    }
    if (mode_count != 1) {
        *out_code = "unexpected_field";
        *out_reason = "mode must appear exactly once";
        goto cleanup;
    }

    mode_item = cJSON_GetObjectItemCaseSensitive(root, "mode");
    if (!cJSON_IsString(mode_item) || !mode_item->valuestring) {
        *out_code = "invalid_mode";
        *out_reason = "mode must be the string mock or real";
        goto cleanup;
    }
    if (strcmp(mode_item->valuestring, "mock") == 0) {
        *out_mode = REHAB_DEMO_MODE_MOCK;
    } else if (strcmp(mode_item->valuestring, "real") == 0) {
        *out_mode = REHAB_DEMO_MODE_REAL;
    } else {
        *out_code = "invalid_mode";
        *out_reason = "mode must be mock or real";
        goto cleanup;
    }

    err = ESP_OK;

cleanup:
    cJSON_Delete(root);
    return err;
}

static void rehab_log_result(const char *capability,
                             const claw_cap_call_context_t *ctx,
                             esp_err_t err,
                             bool accepted,
                             bool changed,
                             const rehab_system_snapshot_t *snapshot)
{
    ESP_LOG_LEVEL(err == ESP_OK ? ESP_LOG_INFO : ESP_LOG_WARN,
                  TAG,
                  "%s execute result request_id=%lu err=%s accepted=%d changed=%d mode=%s state=%s generation=%lu",
                  capability,
                  (unsigned long)(ctx ? ctx->request_id : 0),
                  esp_err_to_name(err),
                  accepted,
                  changed,
                  snapshot ? rehab_mode_to_string(snapshot->mode) : "unknown",
                  snapshot ? rehab_state_to_string(snapshot->state) : "unknown",
                  (unsigned long)(snapshot ? snapshot->generation : 0));
}

static esp_err_t rehab_status_execute(const char *input_json,
                                      const claw_cap_call_context_t *ctx,
                                      char *output,
                                      size_t output_size)
{
    rehab_system_snapshot_t snapshot = {0};
    const char *error_code = NULL;
    const char *error_reason = NULL;
    cJSON *root = NULL;
    cJSON *components = NULL;
    esp_err_t err;

    ESP_LOGI(TAG, "rehab_system_status execute enter request_id=%lu",
             (unsigned long)(ctx ? ctx->request_id : 0));
    err = rehab_parse_empty_object(input_json, &error_code, &error_reason);
    if (err != ESP_OK) {
        (void)rehab_system_get_snapshot(&snapshot);
        esp_err_t render_err = rehab_write_error(output, output_size, error_code, error_reason, NULL);
        rehab_log_result("rehab_system_status", ctx, err, false, false, &snapshot);
        return render_err == ESP_OK ? err : render_err;
    }

    err = rehab_system_get_snapshot(&snapshot);
    if (err != ESP_OK) {
        esp_err_t render_err = rehab_write_error(output,
                                                 output_size,
                                                 "state_unavailable",
                                                 "rehab system state is unavailable",
                                                 NULL);
        rehab_log_result("rehab_system_status", ctx, err, false, false, NULL);
        return render_err == ESP_OK ? err : render_err;
    }

    root = cJSON_CreateObject();
    if (!root ||
            !cJSON_AddStringToObject(root, "schema_version", "rehab.system.status.v1") ||
            !cJSON_AddBoolToObject(root, "initialized", snapshot.initialized) ||
            !rehab_json_add_bounded_string(root,
                                           "mode",
                                           rehab_mode_to_string(snapshot.mode),
                                           sizeof(s_rehab_mode_mock)) ||
            !rehab_json_add_bounded_string(root,
                                           "state",
                                           rehab_state_to_string(snapshot.state),
                                           sizeof(s_rehab_state_idle)) ||
            !cJSON_AddNumberToObject(root, "generation", snapshot.generation) ||
            !rehab_json_add_bounded_string(root,
                                           "storage_base",
                                           snapshot.storage_base,
                                           sizeof(snapshot.storage_base))) {
        err = rehab_json_no_mem(root, output, output_size);
        rehab_log_result("rehab_system_status", ctx, err, false, false, &snapshot);
        return err;
    }
    if (snapshot.has_last_error) {
        if (!cJSON_AddStringToObject(root, "last_error", snapshot.last_error)) {
            err = rehab_json_no_mem(root, output, output_size);
            rehab_log_result("rehab_system_status", ctx, err, false, false, &snapshot);
            return err;
        }
    } else if (!cJSON_AddNullToObject(root, "last_error")) {
        err = rehab_json_no_mem(root, output, output_size);
        rehab_log_result("rehab_system_status", ctx, err, false, false, &snapshot);
        return err;
    }

    components = cJSON_AddObjectToObject(root, "components");
    if (!components ||
            !rehab_json_add_bounded_string(components,
                                           "follow",
                                           s_rehab_not_initialized,
                                           sizeof(s_rehab_not_initialized)) ||
            !rehab_json_add_bounded_string(components,
                                           "training",
                                           rehab_training_component_state(),
                                           REHAB_TRAINING_ERROR_MESSAGE_LEN) ||
            !rehab_json_add_bounded_string(components,
                                           "scoring",
                                           s_rehab_not_initialized,
                                           sizeof(s_rehab_not_initialized)) ||
            !rehab_json_add_bounded_string(components,
                                           "decision",
                                           s_rehab_not_initialized,
                                           sizeof(s_rehab_not_initialized)) ||
            !rehab_json_add_bounded_string(components,
                                           "ui",
                                           s_rehab_not_initialized,
                                           sizeof(s_rehab_not_initialized)) ||
            !rehab_json_add_bounded_string(components,
                                           "voice",
                                           s_rehab_not_initialized,
                                           sizeof(s_rehab_not_initialized))) {
        err = rehab_json_no_mem(root, output, output_size);
        rehab_log_result("rehab_system_status", ctx, err, false, false, &snapshot);
        return err;
    }

    err = rehab_render_json(root, output, output_size);
    rehab_log_result("rehab_system_status", ctx, err, err == ESP_OK, false, &snapshot);
    return err;
}

static esp_err_t rehab_mode_set_execute(const char *input_json,
                                        const claw_cap_call_context_t *ctx,
                                        char *output,
                                        size_t output_size)
{
    rehab_system_snapshot_t snapshot = {0};
    rehab_demo_mode_t requested_mode = REHAB_DEMO_MODE_MOCK;
    const char *error_code = NULL;
    const char *error_reason = NULL;
    bool changed = false;
    cJSON *root = NULL;
    esp_err_t err;

    ESP_LOGI(TAG, "rehab_demo_mode_set execute enter request_id=%lu",
             (unsigned long)(ctx ? ctx->request_id : 0));
    err = rehab_parse_mode(input_json, &requested_mode, &error_code, &error_reason);
    if (err != ESP_OK) {
        (void)rehab_system_get_snapshot(&snapshot);
        esp_err_t render_err = rehab_write_error(output, output_size, error_code, error_reason, NULL);
        rehab_log_result("rehab_demo_mode_set", ctx, err, false, false, &snapshot);
        return render_err == ESP_OK ? err : render_err;
    }

    err = rehab_system_set_demo_mode(requested_mode, &changed, &snapshot);
    if (err == ESP_ERR_INVALID_STATE) {
        esp_err_t render_err = rehab_write_error(
                                   output,
                                   output_size,
                                   "state_not_idle",
                                   "demo mode can only change while the rehab system is idle",
                                   &snapshot);
        rehab_log_result("rehab_demo_mode_set", ctx, err, false, false, &snapshot);
        return render_err == ESP_OK ? err : render_err;
    }
    if (err != ESP_OK) {
        esp_err_t render_err = rehab_write_error(output,
                                                 output_size,
                                                 "state_unavailable",
                                                 "rehab system state is unavailable",
                                                 NULL);
        rehab_log_result("rehab_demo_mode_set", ctx, err, false, false, NULL);
        return render_err == ESP_OK ? err : render_err;
    }

    root = cJSON_CreateObject();
    if (!root ||
            !cJSON_AddStringToObject(root, "schema_version", "rehab.demo_mode.set.v1") ||
            !cJSON_AddBoolToObject(root, "accepted", true) ||
            !cJSON_AddStringToObject(root, "mode", rehab_mode_to_string(snapshot.mode)) ||
            !cJSON_AddBoolToObject(root, "changed", changed) ||
            !cJSON_AddNumberToObject(root, "generation", snapshot.generation)) {
        err = rehab_json_no_mem(root, output, output_size);
        rehab_log_result("rehab_demo_mode_set", ctx, err, false, changed, &snapshot);
        return err;
    }

    err = rehab_render_json(root, output, output_size);
    rehab_log_result("rehab_demo_mode_set", ctx, err, err == ESP_OK, changed, &snapshot);
    return err;
}

static esp_err_t rehab_reset_execute(const char *input_json,
                                     const claw_cap_call_context_t *ctx,
                                     char *output,
                                     size_t output_size)
{
    rehab_system_snapshot_t snapshot = {0};
    const char *error_code = NULL;
    const char *error_reason = NULL;
    cJSON *root = NULL;
    esp_err_t err;

    ESP_LOGI(TAG, "rehab_system_reset execute enter request_id=%lu",
             (unsigned long)(ctx ? ctx->request_id : 0));
    err = rehab_parse_empty_object(input_json, &error_code, &error_reason);
    if (err != ESP_OK) {
        (void)rehab_system_get_snapshot(&snapshot);
        esp_err_t render_err = rehab_write_error(output, output_size, error_code, error_reason, NULL);
        rehab_log_result("rehab_system_reset", ctx, err, false, false, &snapshot);
        return render_err == ESP_OK ? err : render_err;
    }

    err = rehab_system_reset_runtime(&snapshot);
    if (err != ESP_OK) {
        esp_err_t render_err = rehab_write_error(output,
                                                 output_size,
                                                 "state_unavailable",
                                                 "rehab system state is unavailable",
                                                 NULL);
        rehab_log_result("rehab_system_reset", ctx, err, false, false, NULL);
        return render_err == ESP_OK ? err : render_err;
    }

    root = cJSON_CreateObject();
    if (!root ||
            !cJSON_AddStringToObject(root, "schema_version", "rehab.system.reset.v1") ||
            !cJSON_AddBoolToObject(root, "accepted", true) ||
            !cJSON_AddStringToObject(root, "state", rehab_state_to_string(snapshot.state)) ||
            !cJSON_AddStringToObject(root, "mode", rehab_mode_to_string(snapshot.mode)) ||
            !cJSON_AddNumberToObject(root, "generation", snapshot.generation)) {
        err = rehab_json_no_mem(root, output, output_size);
        rehab_log_result("rehab_system_reset", ctx, err, false, false, &snapshot);
        return err;
    }

    err = rehab_render_json(root, output, output_size);
    rehab_log_result("rehab_system_reset", ctx, err, err == ESP_OK, false, &snapshot);
    return err;
}

static esp_err_t rehab_parse_training_start(const char *input_json,
                                            char task_name[REHAB_TRAINING_TASK_NAME_MAX_BYTES + 1],
                                            const char **out_code,
                                            const char **out_reason)
{
    cJSON *root = cJSON_Parse(input_json ? input_json : "{}");
    cJSON *field = NULL;
    cJSON *item;
    size_t count = 0;
    esp_err_t err = ESP_ERR_INVALID_ARG;

    if (!root || !cJSON_IsObject(root)) {
        *out_code = "invalid_json";
        *out_reason = "input must be a valid JSON object";
        goto cleanup;
    }
    cJSON_ArrayForEach(field, root) {
        if (!field->string || strcmp(field->string, "task_name") != 0) {
            *out_code = "unexpected_field";
            *out_reason = "only task_name is allowed";
            goto cleanup;
        }
        count++;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "task_name");
    if (count != 1 || !cJSON_IsString(item) || !item->valuestring) {
        *out_code = "missing_task_name";
        *out_reason = "task_name is required and must be a string";
        goto cleanup;
    }
    size_t task_length = strlen(item->valuestring);
    bool has_control = false;
    for (size_t index = 0; index < task_length; ++index) {
        unsigned char byte = (unsigned char)item->valuestring[index];
        if (byte < 0x20u || byte == 0x7fu) {
            has_control = true;
            break;
        }
    }
    if (task_length == 0 || task_length > REHAB_TRAINING_TASK_NAME_MAX_BYTES || has_control) {
        *out_code = "invalid_task_name";
        *out_reason = "task_name must be valid UTF-8, non-empty, free of control characters, and at most 96 bytes";
        goto cleanup;
    }
    strlcpy(task_name,
            item->valuestring,
            REHAB_TRAINING_TASK_NAME_MAX_BYTES + 1);
    err = ESP_OK;

cleanup:
    cJSON_Delete(root);
    return err;
}

static esp_err_t rehab_parse_training_stop(const char *input_json,
                                           char reason[24],
                                           const char **out_code,
                                           const char **out_reason)
{
    cJSON *root = cJSON_Parse(input_json ? input_json : "{}");
    cJSON *field = NULL;
    cJSON *item;
    size_t count = 0;
    esp_err_t err = ESP_ERR_INVALID_ARG;

    strlcpy(reason, "user_stop", 24);
    if (!root || !cJSON_IsObject(root)) {
        *out_code = "invalid_json";
        *out_reason = "input must be a valid JSON object";
        goto cleanup;
    }
    cJSON_ArrayForEach(field, root) {
        if (!field->string || strcmp(field->string, "reason") != 0) {
            *out_code = "unexpected_field";
            *out_reason = "only reason is allowed";
            goto cleanup;
        }
        count++;
    }
    if (count == 0) {
        err = ESP_OK;
        goto cleanup;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "reason");
    if (count != 1 || !cJSON_IsString(item) || !item->valuestring ||
            (strcmp(item->valuestring, "user_stop") != 0 &&
             strcmp(item->valuestring, "completed") != 0 &&
             strcmp(item->valuestring, "write_error") != 0 &&
             strcmp(item->valuestring, "sensor_timeout") != 0)) {
        *out_code = "invalid_reason";
        *out_reason = "reason must be user_stop, completed, write_error, or sensor_timeout";
        goto cleanup;
    }
    strlcpy(reason, item->valuestring, 24);
    err = ESP_OK;

cleanup:
    cJSON_Delete(root);
    return err;
}

static esp_err_t rehab_render_training_result(const char *schema,
                                              const rehab_training_result_t *result,
                                              char *output,
                                              size_t output_size)
{
    cJSON *root = cJSON_CreateObject();

    if (!root ||
            !cJSON_AddStringToObject(root, "schema_version", schema) ||
            !cJSON_AddBoolToObject(root, "accepted", result->accepted) ||
            !cJSON_AddStringToObject(root, "state", rehab_training_state_to_string(result->state)) ||
            !cJSON_AddStringToObject(root, "task_name", result->task_name) ||
            !cJSON_AddStringToObject(root, "session_path", result->session_path) ||
            !cJSON_AddStringToObject(root, "csv_path", result->csv_path) ||
            !cJSON_AddStringToObject(root, "task_json_path", result->task_json_path) ||
            !cJSON_AddNumberToObject(root, "rows_written", (double)result->rows_written) ||
            !cJSON_AddNumberToObject(root, "start_log_seq", (double)result->start_log_seq) ||
            !cJSON_AddNumberToObject(root, "end_log_seq", (double)result->end_log_seq) ||
            !cJSON_AddNumberToObject(root, "duration_ms", (double)result->duration_ms) ||
            !cJSON_AddNumberToObject(root, "queue_drop_count", (double)result->queue_drop_count) ||
            !cJSON_AddNumberToObject(root, "write_error_count", (double)result->write_error_count) ||
            !cJSON_AddStringToObject(root, "end_reason", result->end_reason) ||
            !cJSON_AddNumberToObject(root, "generation", result->generation)) {
        return rehab_json_no_mem(root, output, output_size);
    }
    if (result->error_code[0] &&
            (!cJSON_AddStringToObject(root, "code", result->error_code) ||
             !cJSON_AddStringToObject(root, "message", result->error_message))) {
        return rehab_json_no_mem(root, output, output_size);
    }
    return rehab_render_json(root, output, output_size);
}

static esp_err_t rehab_render_fatigue_blocked(
    const char *schema,
    const rehab_task_decision_selected_snapshot_t *selected,
    const rehab_fatigue_gate_t *gate,
    char *output,
    size_t output_size)
{
    char message[160];
    int written = snprintf(message, sizeof(message),
                           "当前训练疲劳指数为%d，超过允许上限%d，请休息后再训练。",
                           gate->fatigue, REHAB_FATIGUE_STATUS_LIMIT);
    cJSON *root = cJSON_CreateObject();
    if (written <= 0 || (size_t)written >= sizeof(message) || root == NULL ||
            cJSON_AddStringToObject(root, "schema_version", schema) == NULL ||
            cJSON_AddBoolToObject(root, "ok", false) == NULL ||
            cJSON_AddBoolToObject(root, "accepted", false) == NULL ||
            cJSON_AddBoolToObject(root, "blocked", true) == NULL ||
            cJSON_AddStringToObject(root, "reason", "fatigue_limit") == NULL ||
            cJSON_AddNumberToObject(root, "fatigue", gate->fatigue) == NULL ||
            cJSON_AddNumberToObject(root, "fatigue_limit",
                                   REHAB_FATIGUE_STATUS_LIMIT) == NULL ||
            cJSON_AddNumberToObject(root, "minutes_until_allowed",
                                   gate->minutes_until_allowed) == NULL ||
            cJSON_AddStringToObject(root, "message", message) == NULL) {
        return rehab_json_no_mem(root, output, output_size);
    }
    if (selected != NULL &&
            (cJSON_AddBoolToObject(root, "training_started", false) == NULL ||
             cJSON_AddBoolToObject(root, "selected_task_preserved", true) == NULL ||
             cJSON_AddStringToObject(root, "task_id", selected->task_id) == NULL ||
             cJSON_AddStringToObject(root, "task_name", selected->task_name) == NULL)) {
        return rehab_json_no_mem(root, output, output_size);
    }
    return rehab_render_json(root, output, output_size);
}

static const char *rehab_session_id_from_path(const char *session_path)
{
    if (session_path == NULL || session_path[0] == '\0') {
        return "";
    }
    const char *separator = strrchr(session_path, '/');
    return separator != NULL ? separator + 1 : session_path;
}

static esp_err_t rehab_render_training_start_selected(
    const rehab_task_decision_selected_snapshot_t *selected,
    const rehab_training_status_t *current_status,
    const rehab_training_result_t *start_result,
    bool training_started,
    const char *code,
    char *output,
    size_t output_size)
{
    rehab_training_state_t state = REHAB_TRAINING_STATE_IDLE;
    bool accepted = false;
    if (start_result != NULL) {
        state = start_result->state;
        accepted = start_result->accepted;
    } else if (current_status != NULL) {
        state = current_status->state;
    }

    cJSON *root = cJSON_CreateObject();
    if (root == NULL ||
        cJSON_AddStringToObject(root, "schema_version",
                               "rehab.training.start_selected.v1") == NULL ||
        cJSON_AddBoolToObject(root, "accepted", accepted) == NULL ||
        cJSON_AddBoolToObject(root, "training_started", training_started) == NULL ||
        cJSON_AddStringToObject(root, "state",
                               rehab_training_state_to_string(state)) == NULL ||
        cJSON_AddBoolToObject(root, "selected_task_preserved", true) == NULL) {
        return rehab_json_no_mem(root, output, output_size);
    }
    if (code != NULL && code[0] != '\0' &&
        cJSON_AddStringToObject(root, "code", code) == NULL) {
        return rehab_json_no_mem(root, output, output_size);
    }
    if (selected != NULL && selected->available &&
        (cJSON_AddStringToObject(root, "task_id", selected->task_id) == NULL ||
         cJSON_AddStringToObject(root, "task_name", selected->task_name) == NULL ||
         cJSON_AddNumberToObject(root, "suggested_distance_cm",
                                selected->suggested_distance_cm) == NULL ||
         cJSON_AddNumberToObject(root, "decision_generation",
                                selected->decision_generation) == NULL)) {
        return rehab_json_no_mem(root, output, output_size);
    }
    if (current_status != NULL && current_status->state != REHAB_TRAINING_STATE_IDLE &&
        (cJSON_AddStringToObject(
             root, "current_session_id",
             rehab_session_id_from_path(current_status->session_path)) == NULL ||
         cJSON_AddStringToObject(root, "current_task_name",
                                current_status->task_name) == NULL)) {
        return rehab_json_no_mem(root, output, output_size);
    }
    if (start_result != NULL) {
        if (cJSON_AddStringToObject(
                root, "session_id",
                rehab_session_id_from_path(start_result->session_path)) == NULL ||
            cJSON_AddNumberToObject(root, "generation",
                                   start_result->generation) == NULL ||
            cJSON_AddNumberToObject(root, "rows_written",
                                   (double)start_result->rows_written) == NULL ||
            cJSON_AddNumberToObject(root, "start_log_seq",
                                   (double)start_result->start_log_seq) == NULL) {
            return rehab_json_no_mem(root, output, output_size);
        }
        if (start_result->error_message[0] != '\0' &&
            cJSON_AddStringToObject(root, "message",
                                   start_result->error_message) == NULL) {
            return rehab_json_no_mem(root, output, output_size);
        }
    }
    return rehab_render_json(root, output, output_size);
}

static const char *rehab_udp_quality_interpretation(rehab_udp_quality_t quality)
{
    switch (quality) {
        case REHAB_UDP_QUALITY_NORMAL:
            return "UDP sequence-gap quality is normal; report record queue drops and SD write errors separately.";
        case REHAB_UDP_QUALITY_ACCEPTABLE:
            return "UDP quality is acceptable and does not affect the current 50 Hz CSV recording.";
        case REHAB_UDP_QUALITY_DEGRADED:
            return "UDP quality is degraded, but the node may remain online and recording continues; check the network environment.";
        case REHAB_UDP_QUALITY_UNKNOWN:
        default:
            return "UDP quality is unknown until a sequence baseline and enough window samples are available.";
    }
}

static bool rehab_add_node_status(cJSON *root,
                                  const char *name,
                                  const rehab_sensor_node_status_t *status)
{
    cJSON *node = cJSON_AddObjectToObject(root, name);
    cJSON *errors;

    if (!node ||
            !cJSON_AddBoolToObject(node, "online", status->online) ||
            !cJSON_AddBoolToObject(node, "packet_valid", status->packet_valid) ||
            !cJSON_AddBoolToObject(node, "packet_updating", status->packet_updating) ||
            !cJSON_AddNumberToObject(node, "last_rx_age_ms", status->last_rx_age_ms) ||
            !cJSON_AddNumberToObject(node, "measured_pps", status->measured_pps) ||
            !cJSON_AddNumberToObject(node, "rx_count", (double)status->rx_count) ||
            !cJSON_AddNumberToObject(node, "valid_packet_count", (double)status->valid_packet_count) ||
            !cJSON_AddNumberToObject(node, "last_tx_seq", status->last_tx_seq) ||
            !cJSON_AddNumberToObject(node, "gap_count", (double)status->gap_count) ||
            !cJSON_AddNumberToObject(node, "duplicate_count", (double)status->duplicate_count) ||
            !cJSON_AddNumberToObject(node, "out_of_order_count", (double)status->out_of_order_count) ||
            !cJSON_AddNumberToObject(node, "reboot_or_rebaseline_count", (double)status->reboot_or_rebaseline_count) ||
            !cJSON_AddNumberToObject(node, "window_received", (double)status->window_received) ||
            !cJSON_AddNumberToObject(node, "window_gap", (double)status->window_gap) ||
            !cJSON_AddNumberToObject(node, "udp_gap_ratio_pct", status->udp_gap_ratio_pct) ||
            !cJSON_AddStringToObject(node,
                                    "udp_quality",
                                    rehab_udp_quality_to_string(status->udp_quality)) ||
            !cJSON_AddStringToObject(node,
                                    "udp_quality_interpretation",
                                    rehab_udp_quality_interpretation(status->udp_quality))) {
        return false;
    }
    errors = cJSON_AddObjectToObject(node, "errors");
    return errors &&
           cJSON_AddNumberToObject(errors, "bad_length", (double)status->bad_length_count) &&
           cJSON_AddNumberToObject(errors, "bad_magic", (double)status->bad_magic_count) &&
           cJSON_AddNumberToObject(errors, "bad_version", (double)status->bad_version_count) &&
           cJSON_AddNumberToObject(errors, "bad_packet_size", (double)status->bad_packet_size_count) &&
           cJSON_AddNumberToObject(errors, "bad_node", (double)status->bad_node_count) &&
           cJSON_AddNumberToObject(errors, "bad_flags", (double)status->bad_flags_count) &&
           cJSON_AddNumberToObject(errors, "checksum", (double)status->checksum_error_count) &&
           cJSON_AddNumberToObject(errors, "non_finite", (double)status->non_finite_count);
}

static bool rehab_add_imu_status(cJSON *root,
                                 const char *name,
                                 const rehab_imu_status_t *status)
{
    cJSON *imu = cJSON_AddObjectToObject(root, name);

    return imu &&
           cJSON_AddBoolToObject(imu, "valid", status->valid) &&
           cJSON_AddBoolToObject(imu, "updating", status->updating) &&
           cJSON_AddBoolToObject(imu, "metadata_updating", status->metadata_updating) &&
           cJSON_AddBoolToObject(imu, "changed_recently", status->changed_recently) &&
           cJSON_AddNumberToObject(imu, "source_age_ms", status->source_age_ms) &&
           cJSON_AddNumberToObject(imu, "p4_node_age_ms", status->p4_node_age_ms) &&
           cJSON_AddNumberToObject(imu, "update_count", status->update_count) &&
           cJSON_AddNumberToObject(imu, "source_ts_us", (double)status->source_ts_us);
}

static bool rehab_add_current_session(cJSON *root,
                                      const rehab_training_status_t *status)
{
    cJSON *current = cJSON_AddObjectToObject(root, "current_session");

    return current &&
           cJSON_AddBoolToObject(current, "active", status->current_session_active) &&
           cJSON_AddStringToObject(current,
                                   "state",
                                   rehab_training_state_to_string(status->state)) &&
           cJSON_AddNumberToObject(current, "generation", status->active_generation) &&
           rehab_json_add_bounded_string(current,
                                         "task_name",
                                         status->task_name,
                                         sizeof(status->task_name)) &&
           rehab_json_add_bounded_string(current,
                                         "session_path",
                                         status->session_path,
                                         sizeof(status->session_path)) &&
           rehab_json_add_bounded_string(current,
                                         "csv_path",
                                         status->csv_path,
                                         sizeof(status->csv_path)) &&
           rehab_json_add_bounded_string(current,
                                         "task_json_path",
                                         status->task_json_path,
                                         sizeof(status->task_json_path)) &&
           cJSON_AddNumberToObject(current, "rows_written", (double)status->rows_written) &&
           cJSON_AddNumberToObject(current, "start_log_seq", (double)status->start_log_seq) &&
           cJSON_AddNumberToObject(current, "end_log_seq", (double)status->end_log_seq) &&
           cJSON_AddNumberToObject(current,
                                   "record_queue_drop_count",
                                   (double)status->queue_drop_count) &&
           cJSON_AddNumberToObject(current,
                                   "sd_write_error_count",
                                   (double)status->write_error_count);
}

static bool rehab_add_last_completed_session(cJSON *root,
                                             const rehab_training_status_t *status)
{
    const rehab_training_result_t *last = &status->last_completed_session;
    cJSON *completed = cJSON_AddObjectToObject(root, "last_completed_session");

    if (!completed ||
            !cJSON_AddBoolToObject(completed,
                                   "exists",
                                   status->last_completed_session_exists)) {
        return false;
    }
    if (!status->last_completed_session_exists) {
        return true;
    }

    return cJSON_AddStringToObject(completed,
                                   "state",
                                   rehab_training_state_to_string(last->state)) &&
           cJSON_AddNumberToObject(completed, "generation", last->generation) &&
           rehab_json_add_bounded_string(completed,
                                         "task_name",
                                         last->task_name,
                                         sizeof(last->task_name)) &&
           rehab_json_add_bounded_string(completed,
                                         "session_path",
                                         last->session_path,
                                         sizeof(last->session_path)) &&
           rehab_json_add_bounded_string(completed,
                                         "csv_path",
                                         last->csv_path,
                                         sizeof(last->csv_path)) &&
           rehab_json_add_bounded_string(completed,
                                         "task_json_path",
                                         last->task_json_path,
                                         sizeof(last->task_json_path)) &&
           cJSON_AddNumberToObject(completed, "rows_written", (double)last->rows_written) &&
           cJSON_AddNumberToObject(completed,
                                   "start_log_seq",
                                   (double)last->start_log_seq) &&
           cJSON_AddNumberToObject(completed, "end_log_seq", (double)last->end_log_seq) &&
           cJSON_AddNumberToObject(completed, "duration_ms", (double)last->duration_ms) &&
           rehab_json_add_bounded_string(completed,
                                         "end_reason",
                                         last->end_reason,
                                         sizeof(last->end_reason)) &&
           cJSON_AddNumberToObject(completed,
                                   "record_queue_drop_count",
                                   (double)last->queue_drop_count) &&
           cJSON_AddNumberToObject(completed,
                                   "sd_write_error_count",
                                   (double)last->write_error_count);
}

static bool rehab_add_recording_integrity(cJSON *root,
                                          const rehab_training_status_t *status)
{
    cJSON *integrity = cJSON_AddObjectToObject(root, "recording_integrity");

    return integrity &&
           cJSON_AddNumberToObject(integrity,
                                   "record_queue_drop_count",
                                   (double)status->queue_drop_count) &&
           cJSON_AddNumberToObject(integrity,
                                   "sd_write_error_count",
                                   (double)status->write_error_count) &&
           cJSON_AddStringToObject(
               integrity,
               "interpretation",
               "Record queue drops and SD write errors are distinct from per-node UDP sequence gaps; do not describe zero queue/write errors as no packet loss.");
}

static esp_err_t rehab_training_start_execute(const char *input_json,
                                              const claw_cap_call_context_t *ctx,
                                              char *output,
                                              size_t output_size)
{
    rehab_training_result_t *result = calloc(1, sizeof(*result));
    char task_name[REHAB_TRAINING_TASK_NAME_MAX_BYTES + 1] = {0};
    const char *code = NULL;
    const char *reason = NULL;
    esp_err_t err;
    esp_err_t render_err;

    if (!result) {
        return rehab_write_error(output,
                                 output_size,
                                 "out_of_memory",
                                 "unable to allocate training result",
                                 NULL);
    }
    err = rehab_parse_training_start(input_json, task_name, &code, &reason);
    if (err != ESP_OK) {
        result->state = REHAB_TRAINING_STATE_IDLE;
        strlcpy(result->error_code, code, sizeof(result->error_code));
        strlcpy(result->error_message, reason, sizeof(result->error_message));
    } else {
        rehab_fatigue_gate_t gate = {0};
        esp_err_t fatigue_err = rehab_fatigue_before_training(&gate);
        if (fatigue_err == ESP_OK && gate.available && gate.blocked) {
            render_err = rehab_render_fatigue_blocked(
                "rehab.training.start.v1", NULL, &gate, output, output_size);
            ESP_LOGW(TAG, "rehab_training_start blocked fatigue=%d limit=%d",
                     gate.fatigue, REHAB_FATIGUE_STATUS_LIMIT);
            free(result);
            return render_err == ESP_OK ? ESP_ERR_INVALID_STATE : render_err;
        }
        if (fatigue_err != ESP_OK || !gate.available) {
            ESP_LOGW(TAG, "fatigue_warning start fail-open err=%s",
                     esp_err_to_name(fatigue_err));
        }
        err = rehab_training_start(task_name, result);
        if (err == ESP_OK && result->accepted &&
                result->state == REHAB_TRAINING_STATE_RECORDING) {
            fatigue_err = rehab_fatigue_on_training_started();
            if (fatigue_err != ESP_OK) {
                ESP_LOGW(TAG, "fatigue_warning start persistence failed err=%s",
                         esp_err_to_name(fatigue_err));
            }
            (void)rehab_business_audio_training_started(NULL);
        }
    }
    render_err = rehab_render_training_result("rehab.training.start.v1",
                                              result,
                                              output,
                                              output_size);
    ESP_LOG_LEVEL(err == ESP_OK ? ESP_LOG_INFO : ESP_LOG_WARN,
                  TAG,
                  "rehab_training_start request_id=%lu err=%s accepted=%d state=%s generation=%lu",
                  (unsigned long)(ctx ? ctx->request_id : 0),
                  esp_err_to_name(err),
                  result->accepted,
                  rehab_training_state_to_string(result->state),
                  (unsigned long)result->generation);
    free(result);
    return render_err == ESP_OK ? err : render_err;
}

static esp_err_t rehab_training_start_selected_execute(
    const char *input_json,
    const claw_cap_call_context_t *ctx,
    char *output,
    size_t output_size)
{
    const char *parse_code = NULL;
    const char *parse_reason = NULL;
    esp_err_t err = rehab_parse_empty_object(input_json,
                                             &parse_code,
                                             &parse_reason);
    if (err != ESP_OK) {
        return rehab_write_error(output, output_size,
                                 parse_code, parse_reason, NULL);
    }

    rehab_training_status_t *status = calloc(1, sizeof(*status));
    rehab_training_result_t *result = calloc(1, sizeof(*result));
    if (status == NULL || result == NULL) {
        free(status);
        free(result);
        return rehab_write_error(output, output_size,
                                 "out_of_memory",
                                 "unable to allocate training state",
                                 NULL);
    }

    const char *code = NULL;
    bool training_started = false;
    rehab_task_decision_coordinator_status_t *decision_status = NULL;
    rehab_task_decision_selected_snapshot_t *selected = NULL;
    esp_err_t operation_err = rehab_training_get_status(status);
    if (operation_err != ESP_OK || !status->initialized) {
        code = "not_ready";
        operation_err = ESP_ERR_INVALID_STATE;
        ESP_LOGW(TAG, "TRAIN_START_SELECTED_REJECT code=%s state=%s",
                 code, rehab_training_state_to_string(status->state));
        err = rehab_render_training_start_selected(NULL, status, NULL, false,
                                                   code, output, output_size);
        goto done;
    }
    if (status->state != REHAB_TRAINING_STATE_IDLE) {
        code = "already_recording";
        operation_err = ESP_ERR_INVALID_STATE;
        ESP_LOGW(TAG, "TRAIN_START_SELECTED_REJECT code=%s state=%s",
                 code, rehab_training_state_to_string(status->state));
        err = rehab_render_training_start_selected(NULL, status, NULL, false,
                                                   code, output, output_size);
        goto done;
    }

    rehab_task_decision_handle_t task_decision = NULL;
    operation_err = rehab_system_get_task_decision(&task_decision);
    if (operation_err != ESP_OK) {
        code = "not_ready";
        ESP_LOGW(TAG, "TRAIN_START_SELECTED_REJECT code=%s state=%s",
                 code, rehab_training_state_to_string(status->state));
        err = rehab_render_training_start_selected(NULL, status, NULL, false,
                                                   code, output, output_size);
        goto done;
    }

    decision_status = calloc(1, sizeof(*decision_status));
    if (decision_status == NULL) {
        err = rehab_write_error(output, output_size,
                                "out_of_memory",
                                "unable to allocate training state",
                                NULL);
        goto cleanup;
    }
    operation_err = rehab_task_decision_coordinator_get_status(
        task_decision, decision_status);
    const bool decision_active = decision_status->active;
    free(decision_status);
    decision_status = NULL;
    if (operation_err != ESP_OK || decision_active) {
        code = decision_active
            ? "task_decision_active" : "selected_task_unavailable";
        if (operation_err == ESP_OK) {
            operation_err = ESP_ERR_INVALID_STATE;
        }
        ESP_LOGW(TAG, "TRAIN_START_SELECTED_REJECT code=%s state=%s",
                 code, rehab_training_state_to_string(status->state));
        err = rehab_render_training_start_selected(NULL, status, NULL, false,
                                                   code, output, output_size);
        goto done;
    }

    selected = calloc(1, sizeof(*selected));
    if (selected == NULL) {
        err = rehab_write_error(output, output_size,
                                "out_of_memory",
                                "unable to allocate training state",
                                NULL);
        goto cleanup;
    }
    operation_err = rehab_task_decision_selected_get(task_decision, selected);
    if (operation_err != ESP_OK || !selected->available) {
        code = (operation_err == ESP_ERR_TIMEOUT || operation_err == ESP_FAIL ||
                operation_err == ESP_ERR_NO_MEM)
                   ? "selected_task_unavailable"
                   : "no_selected_task";
        if (operation_err == ESP_OK) {
            operation_err = ESP_ERR_NOT_FOUND;
        }
        ESP_LOGW(TAG, "TRAIN_START_SELECTED_REJECT code=%s state=%s",
                 code, rehab_training_state_to_string(status->state));
        err = rehab_render_training_start_selected(NULL, status, NULL, false,
                                                   code, output, output_size);
        goto done;
    }

    ESP_LOGI(TAG,
             "TRAIN_START_SELECTED_BEGIN task_id=%s decision_generation=%" PRIu32,
             selected->task_id, selected->decision_generation);
    rehab_fatigue_gate_t gate = {0};
    esp_err_t fatigue_err = rehab_fatigue_before_training(&gate);
    if (fatigue_err == ESP_OK && gate.available && gate.blocked) {
        code = "fatigue_limit";
        operation_err = ESP_ERR_INVALID_STATE;
        err = rehab_render_fatigue_blocked(
            "rehab.training.start_selected.v1", selected, &gate,
            output, output_size);
        ESP_LOGW(TAG, "TRAIN_START_SELECTED_REJECT code=%s fatigue=%d limit=%d",
                 code, gate.fatigue, REHAB_FATIGUE_STATUS_LIMIT);
        goto done;
    }
    if (fatigue_err != ESP_OK || !gate.available) {
        ESP_LOGW(TAG, "fatigue_warning selected start fail-open err=%s",
                 esp_err_to_name(fatigue_err));
    }
    operation_err = rehab_training_start(selected->task_name, result);
    training_started = operation_err == ESP_OK && result->accepted &&
        result->state == REHAB_TRAINING_STATE_RECORDING;
    if (operation_err == ESP_OK && !training_started) {
        operation_err = ESP_ERR_INVALID_STATE;
    }
    code = training_started ? NULL :
        (result->error_code[0] != '\0' ? result->error_code : "start_failed");
    if (training_started) {
        fatigue_err = rehab_fatigue_on_training_started();
        if (fatigue_err != ESP_OK) {
            ESP_LOGW(TAG, "fatigue_warning start persistence failed err=%s",
                     esp_err_to_name(fatigue_err));
        }
        ESP_LOGI(TAG,
                 "TRAIN_START_SELECTED_STARTED session_id=%s task_id=%s task_name=%s",
                 rehab_session_id_from_path(result->session_path),
                 selected->task_id, selected->task_name);
        (void)rehab_business_audio_training_started(selected->task_id);
    } else {
        ESP_LOGW(TAG, "TRAIN_START_SELECTED_REJECT code=%s state=%s",
                 code, rehab_training_state_to_string(result->state));
    }
    err = rehab_render_training_start_selected(selected, NULL, result,
                                               training_started, code,
                                               output, output_size);

done:
    ESP_LOG_LEVEL(training_started ? ESP_LOG_INFO : ESP_LOG_WARN,
                  TAG,
                  "TRAIN_START_SELECTED_DONE training_started=%d selected_preserved=1",
                  training_started);
    ESP_LOG_LEVEL(operation_err == ESP_OK ? ESP_LOG_INFO : ESP_LOG_WARN,
                  TAG,
                  "rehab_training_start_selected request_id=%lu err=%s code=%s",
                  (unsigned long)(ctx ? ctx->request_id : 0),
                  esp_err_to_name(operation_err),
                  code != NULL ? code : "none");
cleanup:
    free(selected);
    free(decision_status);
    free(result);
    free(status);
    return err == ESP_OK ? operation_err : err;
}

static esp_err_t rehab_training_stop_execute(const char *input_json,
                                             const claw_cap_call_context_t *ctx,
                                             char *output,
                                             size_t output_size)
{
    rehab_training_result_t *result = calloc(1, sizeof(*result));
    char reason_value[24] = {0};
    const char *code = NULL;
    const char *reason = NULL;
    esp_err_t err;
    esp_err_t render_err;

    if (!result) {
        return rehab_write_error(output,
                                 output_size,
                                 "out_of_memory",
                                 "unable to allocate training result",
                                 NULL);
    }
    err = rehab_parse_training_stop(input_json, reason_value, &code, &reason);
    if (err != ESP_OK) {
        result->state = REHAB_TRAINING_STATE_IDLE;
        strlcpy(result->error_code, code, sizeof(result->error_code));
        strlcpy(result->error_message, reason, sizeof(result->error_message));
    } else {
        err = rehab_training_stop(reason_value, REHAB_TRAINING_STOP_WAIT_MS, result);
        if (err == ESP_OK && result->accepted &&
                result->state == REHAB_TRAINING_STATE_IDLE) {
            esp_err_t fatigue_err = rehab_fatigue_on_training_stopped();
            if (fatigue_err != ESP_OK) {
                ESP_LOGW(TAG, "fatigue_warning stop persistence failed err=%s",
                         esp_err_to_name(fatigue_err));
            }
        }
    }
    render_err = rehab_render_training_result("rehab.training.stop.v1",
                                              result,
                                              output,
                                              output_size);
    ESP_LOG_LEVEL(err == ESP_OK ? ESP_LOG_INFO : ESP_LOG_WARN,
                  TAG,
                  "rehab_training_stop request_id=%lu err=%s accepted=%d state=%s generation=%lu",
                  (unsigned long)(ctx ? ctx->request_id : 0),
                  esp_err_to_name(err),
                  result->accepted,
                  rehab_training_state_to_string(result->state),
                  (unsigned long)result->generation);
    free(result);
    return render_err == ESP_OK ? err : render_err;
}

static esp_err_t rehab_training_status_execute(const char *input_json,
                                               const claw_cap_call_context_t *ctx,
                                               char *output,
                                               size_t output_size)
{
    rehab_training_status_t *status = NULL;
    const char *code = NULL;
    const char *reason = NULL;
    cJSON *root = NULL;
    cJSON *emg = NULL;
    cJSON *ads1 = NULL;
    cJSON *ads2 = NULL;
    esp_err_t err = rehab_parse_empty_object(input_json, &code, &reason);

    if (err != ESP_OK) {
        return rehab_write_error(output, output_size, code, reason, NULL);
    }
    status = calloc(1, sizeof(*status));
    if (!status) {
        return rehab_write_error(output,
                                 output_size,
                                 "out_of_memory",
                                 "unable to allocate training status",
                                 NULL);
    }
    err = rehab_training_get_status(status);
    if (err != ESP_OK) {
        free(status);
        return rehab_write_error(output,
                                 output_size,
                                 "state_unavailable",
                                 "training status is unavailable",
                                 NULL);
    }

    root = cJSON_CreateObject();
    if (!root ||
            !cJSON_AddStringToObject(root, "schema_version", "rehab.training.status.v1") ||
            !cJSON_AddBoolToObject(root, "initialized", status->initialized) ||
            !rehab_json_add_bounded_string(root,
                                           "state",
                                           rehab_training_state_to_string(status->state),
                                           REHAB_STATUS_ENUM_STRING_CAPACITY) ||
            !cJSON_AddBoolToObject(root, "recording", status->recording) ||
            !cJSON_AddNumberToObject(root, "generation", status->generation) ||
            !rehab_json_add_bounded_string(root,
                                           "storage_base",
                                           status->storage_base,
                                           sizeof(status->storage_base)) ||
            !cJSON_AddBoolToObject(root, "sd_ready", status->sd_ready) ||
            !rehab_json_add_bounded_string(root,
                                           "session_path",
                                           status->session_path,
                                           sizeof(status->session_path)) ||
            !rehab_json_add_bounded_string(root,
                                           "csv_path",
                                           status->csv_path,
                                           sizeof(status->csv_path)) ||
            !rehab_json_add_bounded_string(root,
                                           "task_json_path",
                                           status->task_json_path,
                                           sizeof(status->task_json_path)) ||
            !cJSON_AddNumberToObject(root, "rows_written", (double)status->rows_written) ||
            !cJSON_AddNumberToObject(root, "start_log_seq", (double)status->start_log_seq) ||
            !cJSON_AddNumberToObject(root, "end_log_seq", (double)status->end_log_seq) ||
            !cJSON_AddNumberToObject(root, "queue_depth", status->queue_depth) ||
            !cJSON_AddNumberToObject(root, "queue_high_water", status->queue_high_water) ||
            !cJSON_AddNumberToObject(root, "queue_drop_count", (double)status->queue_drop_count) ||
            !cJSON_AddBoolToObject(root, "writer_alive", status->writer_alive) ||
            !cJSON_AddNumberToObject(root, "writer_last_progress_ms", (double)status->writer_last_progress_ms) ||
            !cJSON_AddNumberToObject(root, "write_error_count", (double)status->write_error_count) ||
            !rehab_add_node_status(root, "node1", &status->node1) ||
            !rehab_add_node_status(root, "node2", &status->node2) ||
            !rehab_add_current_session(root, status) ||
            !rehab_add_last_completed_session(root, status) ||
            !rehab_add_recording_integrity(root, status)) {
        free(status);
        return rehab_json_no_mem(root, output, output_size);
    }

    emg = cJSON_AddObjectToObject(root, "emg");
    ads1 = cJSON_AddObjectToObject(root, "ads_group_1");
    ads2 = cJSON_AddObjectToObject(root, "ads_group_2");
    if (!emg || !ads1 || !ads2 ||
            !cJSON_AddBoolToObject(emg, "valid", status->emg.valid) ||
            !cJSON_AddNumberToObject(emg, "sample_count", status->emg.sample_count) ||
            !cJSON_AddBoolToObject(emg, "window_updating", status->emg.window_updating) ||
            !cJSON_AddBoolToObject(ads1, "updating", status->emg.group_1_updating) ||
            !cJSON_AddBoolToObject(ads1, "changed_recently", status->emg.group_1_changed_recently) ||
            !cJSON_AddBoolToObject(ads2, "updating", status->emg.group_2_updating) ||
            !cJSON_AddBoolToObject(ads2, "changed_recently", status->emg.group_2_changed_recently) ||
            !rehab_add_imu_status(root, "imu1", &status->imu1) ||
            !rehab_add_imu_status(root, "imu2", &status->imu2) ||
            !rehab_add_imu_status(root, "imu3", &status->imu3) ||
            !rehab_add_imu_status(root, "imu4", &status->imu4) ||
            !cJSON_AddBoolToObject(root, "required_sensors_ok", status->required_sensors_ok) ||
            !cJSON_AddBoolToObject(root, "anomaly_active", status->anomaly_active) ||
            !cJSON_AddNumberToObject(root, "anomaly_duration_ms", status->anomaly_duration_ms) ||
            !cJSON_AddBoolToObject(root, "anomaly_over_10s", status->anomaly_over_10s) ||
            !cJSON_AddStringToObject(root, "last_error", status->last_error) ||
            !cJSON_AddNumberToObject(root, "last_error_code", status->last_error_code) ||
            !cJSON_AddNumberToObject(root, "last_errno", status->last_errno)) {
        free(status);
        return rehab_json_no_mem(root, output, output_size);
    }

    err = rehab_render_json(root, output, output_size);
    if (rehab_training_status_should_log(status, err)) {
        ESP_LOG_LEVEL(err == ESP_OK ? ESP_LOG_INFO : ESP_LOG_WARN,
                      TAG,
                      "rehab_training_status request_id=%lu err=%s state=%s generation=%lu",
                      (unsigned long)(ctx ? ctx->request_id : 0),
                      esp_err_to_name(err),
                      rehab_training_state_to_string(status->state),
                      (unsigned long)status->generation);
    }
    free(status);
    return err;
}

static esp_err_t rehab_parse_score_query(const char *input_json,
                                         char session_ref[REHAB_SCORE_SESSION_ID_LEN])
{
    cJSON *root = cJSON_Parse(input_json ? input_json : "{}");
    if (!root || !cJSON_IsObject(root)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }
    session_ref[0] = '\0';
    size_t field_count = 0;
    cJSON *field = NULL;
    cJSON_ArrayForEach(field, root) {
        if (!field->string || strcmp(field->string, "session_id") != 0) {
            cJSON_Delete(root);
            return ESP_ERR_INVALID_ARG;
        }
        field_count++;
    }
    cJSON *session = cJSON_GetObjectItemCaseSensitive(root, "session_id");
    if (field_count > 1 ||
            (field_count == 1 &&
             (!cJSON_IsString(session) || !session->valuestring ||
              strlen(session->valuestring) >= REHAB_SCORE_SESSION_ID_LEN))) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }
    if (field_count == 1) {
        strlcpy(session_ref, session->valuestring, REHAB_SCORE_SESSION_ID_LEN);
    }
    cJSON_Delete(root);
    return ESP_OK;
}

static const char *rehab_score_query_error_message(rehab_score_query_error_t error)
{
    switch (error) {
    case REHAB_SCORE_QUERY_ERROR_INVALID_SESSION_REF:
        return "session_id must be YYYYMMDD_TASKNNN or TASKNNN";
    case REHAB_SCORE_QUERY_ERROR_SESSION_NOT_FOUND:
        return "no matching training session was found";
    case REHAB_SCORE_QUERY_ERROR_SESSION_REF_AMBIGUOUS:
        return "multiple dates match; provide the full session id";
    case REHAB_SCORE_QUERY_ERROR_STATUS_MISSING:
        return "training data exists but no score status is recorded";
    case REHAB_SCORE_QUERY_ERROR_STATUS_INVALID:
        return "the score status record is invalid";
    case REHAB_SCORE_QUERY_ERROR_RESULT_MISSING:
        return "scoring succeeded but the score result is missing";
    case REHAB_SCORE_QUERY_ERROR_RESULT_INVALID:
        return "the score result record is invalid";
    case REHAB_SCORE_QUERY_ERROR_STORAGE_NOT_READY:
        return "SD score storage is not ready";
    case REHAB_SCORE_QUERY_ERROR_INTERNAL:
    case REHAB_SCORE_QUERY_ERROR_NONE:
    default:
        return "the score query could not be completed";
    }
}

static esp_err_t rehab_render_score_query_error(rehab_score_query_error_t error,
                                                char *output,
                                                size_t output_size)
{
    cJSON *root = cJSON_CreateObject();
    const char *error_code = rehab_score_query_error_to_string(error);
    if (!root ||
            !cJSON_AddBoolToObject(root, "ok", false) ||
            !cJSON_AddStringToObject(root, "error_code", error_code) ||
            !cJSON_AddStringToObject(root,
                                     "message",
                                     rehab_score_query_error_message(error))) {
        return rehab_json_no_mem(root, output, output_size);
    }
    return rehab_render_json(root, output, output_size);
}

static bool rehab_add_optional_number(cJSON *object,
                                      const char *name,
                                      bool available,
                                      double value)
{
    return available ? cJSON_AddNumberToObject(object, name, value) != NULL :
                       cJSON_AddNullToObject(object, name) != NULL;
}

static bool rehab_add_string_to_array(cJSON *array, const char *value)
{
    cJSON *item = cJSON_CreateString(value);
    if (!item) {
        return false;
    }
    if (!cJSON_AddItemToArray(array, item)) {
        cJSON_Delete(item);
        return false;
    }
    return true;
}

static bool rehab_add_score_result(cJSON *root,
                                   const rehab_score_query_result_t *query)
{
    if (!query->result_available) {
        return cJSON_AddNullToObject(root, "result") != NULL;
    }
    cJSON *result = cJSON_AddObjectToObject(root, "result");
    cJSON *warnings = NULL;
    if (!result ||
            !cJSON_AddBoolToObject(result, "valid", query->valid)) {
        return false;
    }
    if (query->invalid_reason[0]) {
        if (!cJSON_AddStringToObject(result, "invalid_reason", query->invalid_reason)) {
            return false;
        }
    } else if (!cJSON_AddNullToObject(result, "invalid_reason")) {
        return false;
    }
    if (!rehab_add_optional_number(result,
                                   "average_score",
                                   query->average_score_available,
                                   query->average_score) ||
            !rehab_add_optional_number(result,
                                       "max_score",
                                       query->max_score_available,
                                       query->max_score) ||
            !rehab_add_optional_number(result,
                                       "min_score",
                                       query->min_score_available,
                                       query->min_score) ||
            !cJSON_AddNumberToObject(result,
                                     "repetition_count",
                                     query->repetition_count) ||
            !cJSON_AddNumberToObject(result,
                                     "target_repetition_count",
                                     query->target_repetition_count) ||
            !rehab_add_optional_number(result,
                                       "consistency_score",
                                       query->consistency_score_available,
                                       query->consistency_score) ||
            !rehab_json_add_bounded_string(result,
                                           "data_quality_level",
                                           query->data_quality_level,
                                           sizeof(query->data_quality_level))) {
        return false;
    }
    warnings = cJSON_AddArrayToObject(result, "warnings");
    if (!warnings) {
        return false;
    }
    if (query->warning_count > 0 &&
            !rehab_add_string_to_array(warnings, query->warnings_summary)) {
        return false;
    }
    if (query->warnings_truncated &&
            !rehab_add_string_to_array(
                warnings,
                "additional warning text was truncated by the fixed query limit")) {
        return false;
    }
    return cJSON_AddBoolToObject(result,
                                 "compensation_detected",
                                 query->compensation_detected) &&
           cJSON_AddNumberToObject(result,
                                   "compensation_action_count",
                                   query->compensation_action_count) &&
           rehab_json_add_bounded_string(result,
                                         "score_version",
                                         query->score_version,
                                         sizeof(query->score_version)) &&
           rehab_json_add_bounded_string(result,
                                         "model_version",
                                         query->model_version,
                                         sizeof(query->model_version)) &&
           rehab_json_add_bounded_string(result,
                                         "completed_at",
                                         query->completed_at,
                                         sizeof(query->completed_at));
}

static esp_err_t rehab_render_score_query(const rehab_score_query_result_t *query,
                                          char *output,
                                          size_t output_size)
{
    cJSON *root = cJSON_CreateObject();
    if (!root ||
            !cJSON_AddBoolToObject(root, "ok", true) ||
            !cJSON_AddStringToObject(root, "schema_version", "rehab_score_query_v1") ||
            !rehab_json_add_bounded_string(root,
                                           "session_id",
                                           query->session_id,
                                           sizeof(query->session_id)) ||
            !rehab_json_add_bounded_string(root,
                                           "task_name",
                                           query->task_name,
                                           sizeof(query->task_name)) ||
            !cJSON_AddStringToObject(root,
                                     "state",
                                     rehab_score_state_to_string(query->state)) ||
            !cJSON_AddNumberToObject(root, "attempt", query->attempt) ||
            !rehab_json_add_bounded_string(root,
                                           "updated_at",
                                           query->updated_at,
                                           sizeof(query->updated_at))) {
        return rehab_json_no_mem(root, output, output_size);
    }
    if (query->last_error[0]) {
        if (!cJSON_AddStringToObject(root, "last_error", query->last_error)) {
            return rehab_json_no_mem(root, output, output_size);
        }
    } else if (!cJSON_AddNullToObject(root, "last_error")) {
        return rehab_json_no_mem(root, output, output_size);
    }
    if (!cJSON_AddBoolToObject(root, "result_available", query->result_available) ||
            !rehab_add_score_result(root, query)) {
        return rehab_json_no_mem(root, output, output_size);
    }
    return rehab_render_json(root, output, output_size);
}

static esp_err_t rehab_score_query_execute(const char *input_json,
                                           const claw_cap_call_context_t *ctx,
                                           char *output,
                                           size_t output_size)
{
    char session_ref[REHAB_SCORE_SESSION_ID_LEN] = {0};
    esp_err_t err = rehab_parse_score_query(input_json, session_ref);
    if (err != ESP_OK) {
        return rehab_render_score_query_error(REHAB_SCORE_QUERY_ERROR_INVALID_SESSION_REF,
                                              output,
                                              output_size);
    }
    rehab_score_query_result_t *query = calloc(1, sizeof(*query));
    if (!query) {
        return rehab_render_score_query_error(REHAB_SCORE_QUERY_ERROR_INTERNAL,
                                              output,
                                              output_size);
    }
    err = rehab_score_bridge_query(session_ref, query);
    esp_err_t render_err = err == ESP_OK ?
        rehab_render_score_query(query, output, output_size) :
        rehab_render_score_query_error(query->error, output, output_size);
    ESP_LOG_LEVEL(err == ESP_OK ? ESP_LOG_INFO : ESP_LOG_WARN,
                  TAG,
                  "rehab_score_query request_id=%lu ref=%.16s err=%s query_error=%s",
                  (unsigned long)(ctx ? ctx->request_id : 0),
                  session_ref,
                  esp_err_to_name(err),
                  rehab_score_query_error_to_string(query->error));
    free(query);
    return render_err;
}

static esp_err_t rehab_fatigue_status_execute(
    const char *input_json,
    const claw_cap_call_context_t *ctx,
    char *output,
    size_t output_size)
{
    const char *code = NULL;
    const char *reason = NULL;
    esp_err_t err = rehab_parse_empty_object(input_json, &code, &reason);
    if (err != ESP_OK) {
        return rehab_write_error(output, output_size, code, reason, NULL);
    }
    rehab_fatigue_status_t *status = calloc(1, sizeof(*status));
    if (status == NULL) {
        return rehab_write_error(output, output_size, "out_of_memory",
                                 "unable to allocate fatigue status", NULL);
    }
    err = rehab_fatigue_get_status(status);
    cJSON *root = cJSON_CreateObject();
    if (err != ESP_OK || root == NULL ||
            cJSON_AddBoolToObject(root, "ok", true) == NULL ||
            cJSON_AddBoolToObject(root, "available", status->available) == NULL ||
            cJSON_AddStringToObject(root, "schema_version",
                                   REHAB_FATIGUE_STATUS_SCHEMA_VERSION) == NULL ||
            cJSON_AddNumberToObject(root, "fatigue", status->fatigue) == NULL ||
            cJSON_AddNumberToObject(root, "fatigue_limit",
                                   REHAB_FATIGUE_STATUS_LIMIT) == NULL ||
            cJSON_AddBoolToObject(root, "training_allowed",
                                 status->available
                                     ? status->training_allowed : true) == NULL ||
            cJSON_AddStringToObject(root, "count_date",
                                   status->count_date) == NULL ||
            cJSON_AddNumberToObject(root, "count_today",
                                   status->count_today) == NULL ||
            cJSON_AddStringToObject(root, "latest_session_id",
                                   status->latest_session_id) == NULL) {
        rehab_fatigue_status_log(status,
                                 err != ESP_OK ? err : ESP_ERR_NO_MEM,
                                 ctx);
        free(status);
        return rehab_json_no_mem(root, output, output_size);
    }
    bool score_ok = status->latest_score_available
        ? cJSON_AddNumberToObject(root, "latest_score", status->latest_score) != NULL
        : cJSON_AddNullToObject(root, "latest_score") != NULL;
    if (!score_ok ||
            cJSON_AddNumberToObject(root, "latest_delta",
                                   status->latest_delta) == NULL ||
            cJSON_AddNumberToObject(root, "counted_session_count",
                                   status->counted_session_count) == NULL ||
            cJSON_AddNumberToObject(root, "recovery_per_minute",
                                   REHAB_FATIGUE_STATUS_RECOVERY_RATE) == NULL ||
            cJSON_AddNumberToObject(root, "minutes_until_allowed",
                                   status->minutes_until_allowed) == NULL ||
            cJSON_AddBoolToObject(root, "decay_paused",
                                 status->decay_paused) == NULL ||
            cJSON_AddBoolToObject(root, "time_trusted",
                                 status->time_trusted) == NULL ||
            (!status->available &&
             cJSON_AddStringToObject(root, "warning",
                                    "fatigue_unavailable; training remains fail-open") == NULL)) {
        rehab_fatigue_status_log(status, ESP_ERR_NO_MEM, ctx);
        free(status);
        return rehab_json_no_mem(root, output, output_size);
    }
    esp_err_t render_err = rehab_render_json(root, output, output_size);
    rehab_fatigue_status_log(status, render_err, ctx);
    free(status);
    return render_err;
}

static const claw_cap_descriptor_t s_rehab_descriptors[] = {
    {
        .id = "rehab_fatigue_status",
        .name = "rehab_fatigue_status",
        .family = "rehab",
        .description = "Return the persisted training fatigue state and settle completed idle minutes without reading score or session history.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_RESTRICTED,
        .input_schema_json =
        "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = rehab_fatigue_status_execute,
    },
    {
        .id = "rehab_score_query",
        .name = "rehab_score_query",
        .family = "rehab",
        .description = "Read the latest or a specified persisted rehab score from SD only; never retry, resubmit, call HTTP, or modify session files.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM,
        .input_schema_json =
        "{\"type\":\"object\",\"properties\":{\"session_id\":{\"type\":\"string\",\"description\":\"Optional full YYYYMMDD_TASKNNN or short TASKNNN; omit for latest.\"}},\"additionalProperties\":false}",
        .execute = rehab_score_query_execute,
    },
    {
        .id = "rehab_training_start_selected",
        .name = "rehab_training_start_selected",
        .family = "rehab",
        .description = "Start the currently selected validated rehab task without accepting or rewriting task_name; the selected task remains persisted for future sessions.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM,
        .input_schema_json =
        "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = rehab_training_start_selected_execute,
    },
    {
        .id = "rehab_training_start",
        .name = "rehab_training_start",
        .family = "rehab",
        .description = "Start one formal sensor recording task on the mounted SD card after all required S3 sensors are ready.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM,
        .input_schema_json =
        "{\"type\":\"object\",\"properties\":{\"task_name\":{\"type\":\"string\",\"minLength\":1,\"maxLength\":96}},\"required\":[\"task_name\"],\"additionalProperties\":false}",
        .execute = rehab_training_start_execute,
    },
    {
        .id = "rehab_training_status",
        .name = "rehab_training_status",
        .family = "rehab",
        .description = "idle only means there is no active recording; inspect last_completed_session.exists for a training completed during this boot. Queue drops, SD write errors, and per-node UDP gaps are distinct; zero queue/write errors never means no packet loss.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM,
        .input_schema_json =
        "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = rehab_training_status_execute,
    },
    {
        .id = "rehab_training_stop",
        .name = "rehab_training_stop",
        .family = "rehab",
        .description = "Stop the active recording, drain its bounded queue, safely close the CSV, and finalize the seven-field task.json.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM,
        .input_schema_json =
        "{\"type\":\"object\",\"properties\":{\"reason\":{\"type\":\"string\",\"enum\":[\"user_stop\",\"completed\",\"write_error\",\"sensor_timeout\"]}},\"additionalProperties\":false}",
        .execute = rehab_training_stop_execute,
    },
    {
        .id = "rehab_system_status",
        .name = "rehab_system_status",
        .family = "rehab",
        .description = "Return the truthful Phase 1 rehab control-plane status. Future hardware and business components remain not_initialized.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM,
        .input_schema_json =
        "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = rehab_status_execute,
    },
    {
        .id = "rehab_system_reset",
        .name = "rehab_system_reset",
        .family = "rehab",
        .description = "Reset only the Phase 1 rehab runtime control state without deleting files, changing configuration, or controlling hardware.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM,
        .input_schema_json =
        "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = rehab_reset_execute,
    },
    {
        .id = "rehab_demo_mode_set",
        .name = "rehab_demo_mode_set",
        .family = "rehab",
        .description = "Set the Phase 1 mock or real mode flag while idle. Real is only a flag and does not initialize or operate hardware or cloud services.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM,
        .input_schema_json =
        "{\"type\":\"object\",\"properties\":{\"mode\":{\"type\":\"string\",\"enum\":[\"mock\",\"real\"]}},\"required\":[\"mode\"],\"additionalProperties\":false}",
        .execute = rehab_mode_set_execute,
    },
};

static const claw_cap_group_t s_rehab_group = {
    .group_id = "rehab_integration",
    .plugin_name = "Rehab Integration",
    .version = "1",
    .descriptors = s_rehab_descriptors,
    .descriptor_count = sizeof(s_rehab_descriptors) / sizeof(s_rehab_descriptors[0]),
};

esp_err_t rehab_capabilities_register_group(void)
{
    esp_err_t err = claw_cap_register_group(&s_rehab_group);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "rehab capability group registration failed err=%s", esp_err_to_name(err));
        return err;
    }
    
    const esp_err_t expose_err =
        claw_cap_add_llm_visible_group("rehab_integration");
    if (expose_err != ESP_OK) {
        ESP_LOGE(TAG,
                 "failed to expose rehab_integration to LLM: 0x%x",
                 (unsigned)expose_err);
        return expose_err;
    }

    ESP_LOGI(TAG, "rehab capability group registered id=%s count=%u",
             s_rehab_group.group_id,
             (unsigned)s_rehab_group.descriptor_count);
    return ESP_OK;
}
