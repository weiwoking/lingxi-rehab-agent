/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "rehab_fatigue.h"

#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "cJSON.h"
#include "claw_paths.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "rehab_fatigue_rules.h"

#define REHAB_FATIGUE_STATE_SUBPATH "rehab/fatigue_state.json"
#define REHAB_FATIGUE_DIR_SUBPATH   "rehab"
#define REHAB_FATIGUE_PATH_LEN      160
#define REHAB_FATIGUE_FILE_MAX      (8U * 1024U)

static const char *TAG = "rehab_fatigue";

_Static_assert(REHAB_FATIGUE_STATUS_LIMIT == REHAB_FATIGUE_LIMIT,
               "public and rule fatigue limits must match");
_Static_assert(REHAB_FATIGUE_STATUS_RECOVERY_RATE ==
                   REHAB_FATIGUE_RECOVERY_PER_MINUTE,
               "public and rule recovery rates must match");

typedef struct {
    SemaphoreHandle_t mutex;
    bool init_attempted;
    bool available;
    char state_path[REHAB_FATIGUE_PATH_LEN];
    rehab_fatigue_rules_state_t state;
} rehab_fatigue_runtime_t;

static rehab_fatigue_runtime_t s_fatigue;

static bool fatigue_time_now(int64_t *out_epoch,
                             char out_date[REHAB_FATIGUE_DATE_LEN])
{
    time_t now = time(NULL);
    struct tm local = {0};
    if (now <= 0 || localtime_r(&now, &local) == NULL ||
            local.tm_year + 1900 < 2024) {
        return false;
    }
    if (out_date != NULL &&
            strftime(out_date, REHAB_FATIGUE_DATE_LEN, "%Y%m%d", &local) != 8) {
        return false;
    }
    if (out_epoch != NULL) {
        *out_epoch = (int64_t)now;
    }
    return true;
}

static bool fatigue_accounting_date_is_valid(
    const char date[REHAB_FATIGUE_DATE_LEN])
{
    if (date == NULL || strlen(date) != 8U) {
        return false;
    }
    for (size_t index = 0; index < 8U; ++index) {
        if (!isdigit((unsigned char)date[index])) {
            return false;
        }
    }
    return true;
}

static esp_err_t fatigue_ensure_directory(void)
{
    char directory[REHAB_FATIGUE_PATH_LEN];
    esp_err_t err = claw_paths_join(CLAW_PATH_DATA,
                                    REHAB_FATIGUE_DIR_SUBPATH,
                                    directory,
                                    sizeof(directory));
    if (err != ESP_OK) {
        return err;
    }
    struct stat info = {0};
    if (stat(directory, &info) == 0) {
        return S_ISDIR(info.st_mode) ? ESP_OK : ESP_ERR_INVALID_STATE;
    }
    if (errno != ENOENT || mkdir(directory, 0755) != 0) {
        return ESP_FAIL;
    }
    return ESP_OK;
}

static esp_err_t fatigue_read_file(const char *path,
                                   char **out_bytes,
                                   size_t *out_length,
                                   bool *out_exists)
{
    *out_bytes = NULL;
    *out_length = 0;
    *out_exists = false;
    struct stat info = {0};
    if (stat(path, &info) != 0) {
        return errno == ENOENT ? ESP_OK : ESP_FAIL;
    }
    *out_exists = true;
    if (info.st_size <= 0 ||
            (uint64_t)info.st_size > (uint64_t)REHAB_FATIGUE_FILE_MAX) {
        return ESP_ERR_INVALID_SIZE;
    }
    char *bytes = malloc((size_t)info.st_size + 1U);
    if (bytes == NULL) {
        return ESP_ERR_NO_MEM;
    }
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        free(bytes);
        return ESP_FAIL;
    }
    size_t received = fread(bytes, 1, (size_t)info.st_size, file);
    bool valid = received == (size_t)info.st_size && !ferror(file);
    if (fclose(file) != 0) {
        valid = false;
    }
    if (!valid) {
        free(bytes);
        return ESP_FAIL;
    }
    bytes[received] = '\0';
    *out_bytes = bytes;
    *out_length = received;
    return ESP_OK;
}

static bool json_int(const cJSON *item, int64_t minimum, int64_t maximum,
                     int64_t *out_value)
{
    if (!cJSON_IsNumber(item) || item->valuedouble < (double)minimum ||
            item->valuedouble > (double)maximum ||
            item->valuedouble != (double)(int64_t)item->valuedouble) {
        return false;
    }
    *out_value = (int64_t)item->valuedouble;
    return true;
}

static esp_err_t fatigue_parse_state(const char *json,
                                     rehab_fatigue_rules_state_t *out_state)
{
    cJSON *root = cJSON_ParseWithOpts(json, NULL, true);
    const cJSON *schema = cJSON_GetObjectItemCaseSensitive(root, "schema_version");
    const cJSON *fatigue = cJSON_GetObjectItemCaseSensitive(root, "fatigue");
    const cJSON *count_date = cJSON_GetObjectItemCaseSensitive(root, "count_date");
    const cJSON *count_today = cJSON_GetObjectItemCaseSensitive(root, "count_today");
    const cJSON *ids = cJSON_GetObjectItemCaseSensitive(root, "counted_session_ids");
    const cJSON *idle = cJSON_GetObjectItemCaseSensitive(root, "idle_since_epoch");
    const cJSON *paused = cJSON_GetObjectItemCaseSensitive(root, "decay_paused");
    const cJSON *latest_id = cJSON_GetObjectItemCaseSensitive(root, "latest_session_id");
    const cJSON *latest_score = cJSON_GetObjectItemCaseSensitive(root, "latest_score");
    const cJSON *latest_delta = cJSON_GetObjectItemCaseSensitive(root, "latest_delta");
    const cJSON *updated = cJSON_GetObjectItemCaseSensitive(root, "updated_at_epoch");
    int64_t fatigue_value = 0;
    int64_t count_value = 0;
    int64_t idle_value = 0;
    int64_t delta_value = 0;
    int64_t updated_value = 0;
    bool valid = cJSON_IsObject(root) && cJSON_IsString(schema) &&
        strcmp(schema->valuestring, REHAB_FATIGUE_SCHEMA_VERSION) == 0 &&
        json_int(fatigue, 0, 100, &fatigue_value) &&
        cJSON_IsString(count_date) &&
        (count_date->valuestring[0] == '\0' || strlen(count_date->valuestring) == 8) &&
        json_int(count_today, 0, UINT32_MAX, &count_value) &&
        cJSON_IsArray(ids) &&
        cJSON_GetArraySize(ids) <= REHAB_FATIGUE_DEDUP_CAPACITY &&
        json_int(idle, 0, INT64_MAX, &idle_value) && cJSON_IsBool(paused) &&
        cJSON_IsString(latest_id) &&
        (latest_id->valuestring[0] == '\0' ||
         strlen(latest_id->valuestring) == REHAB_FATIGUE_SESSION_ID_LEN - 1U) &&
        (cJSON_IsNull(latest_score) || cJSON_IsNumber(latest_score)) &&
        json_int(latest_delta, 0, 12, &delta_value) &&
        json_int(updated, 0, INT64_MAX, &updated_value);
    rehab_fatigue_rules_state_t parsed;
    rehab_fatigue_rules_reset(&parsed);
    if (valid) {
        const cJSON *item = NULL;
        cJSON_ArrayForEach(item, ids) {
            char date[REHAB_FATIGUE_DATE_LEN];
            if (!cJSON_IsString(item) ||
                    !rehab_fatigue_rules_extract_session_date(item->valuestring, date) ||
                    rehab_fatigue_rules_contains_session(&parsed, item->valuestring)) {
                valid = false;
                break;
            }
            memcpy(parsed.counted_session_ids[parsed.counted_session_count],
                   item->valuestring,
                   REHAB_FATIGUE_SESSION_ID_LEN);
            parsed.counted_session_count++;
        }
    }
    if (valid && cJSON_IsNumber(latest_score) &&
            (latest_score->valuedouble < 0.0 || latest_score->valuedouble > 100.0)) {
        valid = false;
    }
    if (valid) {
        parsed.fatigue = (int)fatigue_value;
        memcpy(parsed.count_date, count_date->valuestring,
               strlen(count_date->valuestring) + 1U);
        parsed.count_today = (uint32_t)count_value;
        parsed.idle_since_epoch = idle_value;
        parsed.decay_paused = cJSON_IsTrue(paused);
        memcpy(parsed.latest_session_id, latest_id->valuestring,
               strlen(latest_id->valuestring) + 1U);
        parsed.latest_score_available = cJSON_IsNumber(latest_score);
        parsed.latest_score = parsed.latest_score_available
                                  ? latest_score->valuedouble : 0.0;
        parsed.latest_delta = (int)delta_value;
        parsed.updated_at_epoch = updated_value;
        *out_state = parsed;
    }
    cJSON_Delete(root);
    return valid ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}

static esp_err_t fatigue_write_atomic(const char *path,
                                      const char *bytes,
                                      size_t length)
{
    char temporary[REHAB_FATIGUE_PATH_LEN + sizeof(".tmp")];
    int written = snprintf(temporary, sizeof(temporary), "%s.tmp", path);
    if (written <= 0 || (size_t)written >= sizeof(temporary)) {
        return ESP_ERR_INVALID_SIZE;
    }
    (void)unlink(temporary);
    FILE *file = fopen(temporary, "wb");
    if (file == NULL) {
        return ESP_FAIL;
    }
    bool complete = fwrite(bytes, 1, length, file) == length &&
                    fflush(file) == 0 && !ferror(file) &&
                    fsync(fileno(file)) == 0;
    if (fclose(file) != 0) {
        complete = false;
    }
    if (!complete) {
        (void)unlink(temporary);
        return ESP_FAIL;
    }
    if (rename(temporary, path) != 0) {
        int rename_error = errno;
        if ((rename_error != EEXIST && rename_error != EACCES) ||
                (unlink(path) != 0 && errno != ENOENT) ||
                rename(temporary, path) != 0) {
            (void)unlink(temporary);
            return ESP_FAIL;
        }
    }
    return ESP_OK;
}

static esp_err_t fatigue_save_locked(void)
{
    cJSON *root = cJSON_CreateObject();
    cJSON *ids = NULL;
    if (root == NULL ||
            cJSON_AddStringToObject(root, "schema_version",
                                   REHAB_FATIGUE_SCHEMA_VERSION) == NULL ||
            cJSON_AddNumberToObject(root, "fatigue", s_fatigue.state.fatigue) == NULL ||
            cJSON_AddStringToObject(root, "count_date",
                                   s_fatigue.state.count_date) == NULL ||
            cJSON_AddNumberToObject(root, "count_today",
                                   s_fatigue.state.count_today) == NULL ||
            (ids = cJSON_AddArrayToObject(root, "counted_session_ids")) == NULL) {
        cJSON_Delete(root);
        s_fatigue.available = false;
        return ESP_ERR_NO_MEM;
    }
    for (size_t index = 0; index < s_fatigue.state.counted_session_count; ++index) {
        if (cJSON_AddItemToArray(
                ids,
                cJSON_CreateString(s_fatigue.state.counted_session_ids[index])) == false) {
            cJSON_Delete(root);
            s_fatigue.available = false;
            return ESP_ERR_NO_MEM;
        }
    }
    bool tail_ok =
        cJSON_AddNumberToObject(root, "idle_since_epoch",
                               (double)s_fatigue.state.idle_since_epoch) != NULL &&
        cJSON_AddBoolToObject(root, "decay_paused",
                             s_fatigue.state.decay_paused) != NULL &&
        cJSON_AddStringToObject(root, "latest_session_id",
                               s_fatigue.state.latest_session_id) != NULL;
    if (tail_ok) {
        tail_ok = s_fatigue.state.latest_score_available
                      ? cJSON_AddNumberToObject(root, "latest_score",
                                               s_fatigue.state.latest_score) != NULL
                      : cJSON_AddNullToObject(root, "latest_score") != NULL;
    }
    tail_ok = tail_ok &&
        cJSON_AddNumberToObject(root, "latest_delta",
                               s_fatigue.state.latest_delta) != NULL &&
        cJSON_AddNumberToObject(root, "updated_at_epoch",
                               (double)s_fatigue.state.updated_at_epoch) != NULL;
    if (!tail_ok) {
        cJSON_Delete(root);
        s_fatigue.available = false;
        return ESP_ERR_NO_MEM;
    }
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (json == NULL) {
        s_fatigue.available = false;
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = fatigue_write_atomic(s_fatigue.state_path,
                                         json,
                                         strlen(json));
    cJSON_free(json);
    if (err != ESP_OK) {
        s_fatigue.available = false;
    }
    return err;
}

static esp_err_t fatigue_lock(void)
{
    if (s_fatigue.mutex == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    return xSemaphoreTake(s_fatigue.mutex, portMAX_DELAY) == pdTRUE
               ? ESP_OK : ESP_ERR_TIMEOUT;
}

static void fatigue_unlock(void)
{
    xSemaphoreGive(s_fatigue.mutex);
}

static esp_err_t fatigue_settle_locked(bool *out_changed,
                                       bool *out_time_trusted)
{
    int64_t now = 0;
    char date[REHAB_FATIGUE_DATE_LEN] = {0};
    bool trusted = fatigue_time_now(&now, date);
    int old_fatigue = s_fatigue.state.fatigue;
    int64_t old_anchor = s_fatigue.state.idle_since_epoch;
    bool date_changed = trusted &&
        rehab_fatigue_rules_rollover_day(&s_fatigue.state, date);
    (void)rehab_fatigue_rules_apply_decay(&s_fatigue.state, now, trusted);
    bool changed = date_changed || old_fatigue != s_fatigue.state.fatigue ||
                   old_anchor != s_fatigue.state.idle_since_epoch;
    if (date_changed) {
        s_fatigue.state.updated_at_epoch = now;
    }
    if (out_changed != NULL) {
        *out_changed = changed;
    }
    if (out_time_trusted != NULL) {
        *out_time_trusted = trusted;
    }
    return changed ? fatigue_save_locked() : ESP_OK;
}

esp_err_t rehab_fatigue_init(void)
{
    if (s_fatigue.init_attempted) {
        return s_fatigue.available ? ESP_OK : ESP_ERR_INVALID_STATE;
    }
    s_fatigue.init_attempted = true;
    rehab_fatigue_rules_reset(&s_fatigue.state);
    s_fatigue.mutex = xSemaphoreCreateMutex();
    if (s_fatigue.mutex == NULL) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = claw_paths_join(CLAW_PATH_DATA,
                                    REHAB_FATIGUE_STATE_SUBPATH,
                                    s_fatigue.state_path,
                                    sizeof(s_fatigue.state_path));
    if (err != ESP_OK) {
        return err;
    }
    err = fatigue_ensure_directory();
    if (err != ESP_OK) {
        return err;
    }
    char *json = NULL;
    size_t length = 0;
    bool exists = false;
    err = fatigue_read_file(s_fatigue.state_path, &json, &length, &exists);
    (void)length;
    if (err == ESP_OK && exists) {
        err = fatigue_parse_state(json, &s_fatigue.state);
    }
    free(json);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "fatigue state unavailable; existing file preserved: %s",
                 esp_err_to_name(err));
        return err;
    }
    s_fatigue.available = true;
    int64_t now = 0;
    char date[REHAB_FATIGUE_DATE_LEN] = {0};
    bool trusted = fatigue_time_now(&now, date);
    if (trusted && rehab_fatigue_rules_rollover_day(&s_fatigue.state, date)) {
        s_fatigue.state.updated_at_epoch = now;
    }
    if (s_fatigue.state.decay_paused) {
        s_fatigue.state.decay_paused = false;
        s_fatigue.state.idle_since_epoch = trusted ? now : 0;
    } else {
        (void)rehab_fatigue_rules_apply_decay(&s_fatigue.state, now, trusted);
    }
    esp_err_t save_err = fatigue_save_locked();
    if (save_err != ESP_OK) {
        ESP_LOGW(TAG, "%s fatigue state write failed",
                 exists ? "startup" : "initial");
        return save_err;
    }
    ESP_LOGI(TAG, "fatigue initialized value=%d counted=%u",
             s_fatigue.state.fatigue,
             (unsigned)s_fatigue.state.counted_session_count);
    return ESP_OK;
}

esp_err_t rehab_fatigue_on_score_succeeded(const char *session_id,
                                           double average_score)
{
    esp_err_t err = fatigue_lock();
    if (err != ESP_OK) {
        return err;
    }
    if (!s_fatigue.available) {
        fatigue_unlock();
        return ESP_ERR_INVALID_STATE;
    }
    bool decay_changed = false;
    err = fatigue_settle_locked(&decay_changed, NULL);
    if (err != ESP_OK) {
        fatigue_unlock();
        return err;
    }
    int64_t now = 0;
    char date[REHAB_FATIGUE_DATE_LEN] = {0};
    bool trusted = fatigue_time_now(&now, date);
    if (!trusted) {
        if (!fatigue_accounting_date_is_valid(s_fatigue.state.count_date)) {
            ESP_LOGW(TAG,
                     "score fatigue skipped: time untrusted and accounting day unavailable session=%.16s",
                     session_id ? session_id : "");
            fatigue_unlock();
            return ESP_OK;
        }
        memcpy(date, s_fatigue.state.count_date, sizeof(date));
    }
    rehab_fatigue_event_result_t event = rehab_fatigue_rules_apply_score(
        &s_fatigue.state, session_id, average_score, date, trusted ? now : 0);
    if (event == REHAB_FATIGUE_EVENT_DUPLICATE) {
        ESP_LOGI(TAG, "duplicate score event ignored session=%.16s", session_id);
        fatigue_unlock();
        return ESP_OK;
    }
    if (event != REHAB_FATIGUE_EVENT_APPLIED) {
        fatigue_unlock();
        return ESP_ERR_INVALID_ARG;
    }
    err = fatigue_save_locked();
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "score fatigue applied session=%.16s delta=%d fatigue=%d",
                 session_id, s_fatigue.state.latest_delta,
                 s_fatigue.state.fatigue);
    }
    fatigue_unlock();
    return err;
}

esp_err_t rehab_fatigue_before_training(rehab_fatigue_gate_t *out_gate)
{
    if (out_gate == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_gate, 0, sizeof(*out_gate));
    esp_err_t err = fatigue_lock();
    if (err != ESP_OK) {
        return err;
    }
    out_gate->available = s_fatigue.available;
    if (!s_fatigue.available) {
        fatigue_unlock();
        return ESP_ERR_INVALID_STATE;
    }
    err = fatigue_settle_locked(NULL, NULL);
    if (err == ESP_OK) {
        out_gate->fatigue = s_fatigue.state.fatigue;
        out_gate->blocked =
            !rehab_fatigue_rules_training_allowed(s_fatigue.state.fatigue);
        out_gate->minutes_until_allowed =
            rehab_fatigue_rules_minutes_until_allowed(s_fatigue.state.fatigue);
    }
    fatigue_unlock();
    return err;
}

esp_err_t rehab_fatigue_on_training_started(void)
{
    esp_err_t err = fatigue_lock();
    if (err != ESP_OK) {
        return err;
    }
    if (!s_fatigue.available) {
        fatigue_unlock();
        return ESP_ERR_INVALID_STATE;
    }
    s_fatigue.state.decay_paused = true;
    s_fatigue.state.idle_since_epoch = 0;
    int64_t now = 0;
    if (fatigue_time_now(&now, NULL)) {
        s_fatigue.state.updated_at_epoch = now;
    }
    err = fatigue_save_locked();
    fatigue_unlock();
    return err;
}

esp_err_t rehab_fatigue_on_training_stopped(void)
{
    esp_err_t err = fatigue_lock();
    if (err != ESP_OK) {
        return err;
    }
    if (!s_fatigue.available) {
        fatigue_unlock();
        return ESP_ERR_INVALID_STATE;
    }
    int64_t now = 0;
    bool trusted = fatigue_time_now(&now, NULL);
    s_fatigue.state.decay_paused = false;
    s_fatigue.state.idle_since_epoch = trusted ? now : 0;
    if (trusted) {
        s_fatigue.state.updated_at_epoch = now;
    }
    err = fatigue_save_locked();
    fatigue_unlock();
    return err;
}

esp_err_t rehab_fatigue_get_status(rehab_fatigue_status_t *out_status)
{
    if (out_status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_status, 0, sizeof(*out_status));
    esp_err_t err = fatigue_lock();
    if (err != ESP_OK) {
        return ESP_OK;
    }
    out_status->available = s_fatigue.available;
    bool time_trusted = fatigue_time_now(NULL, NULL);
    if (s_fatigue.available) {
        esp_err_t settle_err = fatigue_settle_locked(NULL, &time_trusted);
        if (settle_err != ESP_OK) {
            out_status->available = false;
        }
    }
    if (out_status->available) {
        out_status->fatigue = s_fatigue.state.fatigue;
        out_status->training_allowed =
            rehab_fatigue_rules_training_allowed(s_fatigue.state.fatigue);
        memcpy(out_status->count_date, s_fatigue.state.count_date,
               sizeof(out_status->count_date));
        out_status->count_today = s_fatigue.state.count_today;
        memcpy(out_status->latest_session_id,
               s_fatigue.state.latest_session_id,
               sizeof(out_status->latest_session_id));
        out_status->latest_score_available =
            s_fatigue.state.latest_score_available;
        out_status->latest_score = s_fatigue.state.latest_score;
        out_status->latest_delta = s_fatigue.state.latest_delta;
        out_status->counted_session_count =
            s_fatigue.state.counted_session_count;
        out_status->decay_paused = s_fatigue.state.decay_paused;
        out_status->minutes_until_allowed =
            rehab_fatigue_rules_minutes_until_allowed(s_fatigue.state.fatigue);
    }
    out_status->time_trusted = time_trusted;
    fatigue_unlock();
    return ESP_OK;
}
