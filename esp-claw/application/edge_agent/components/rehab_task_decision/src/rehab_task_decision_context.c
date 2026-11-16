// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_priv.h"

#include <dirent.h>
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "cJSON.h"
#include "claw_paths.h"
#include "esp_log.h"
#include "rehab_score_bridge.h"

#define DECISION_CONTEXT_SCHEMA "rehab.task_decision.context.v1"
#define DECISION_PRESCRIPTION_SUBPATH "rehab/user_condition.txt"
#define DECISION_PRESCRIPTION_PATH_MAX 128U
#define DECISION_PRESCRIPTION_MAX_BYTES 4096U
#define DECISION_PRESCRIPTION_MAX_CODEPOINTS 2048U
#define DECISION_PRESCRIPTION_SOURCE_MAX_BYTES (64U * 1024U)
#define DECISION_RECENT_SCORE_WINDOW_MAX 10U
#define DECISION_ITEM_WINDOW 3U
#define DECISION_SESSION_CANDIDATE_LIMIT 64U
#define DECISION_SCORE_SESSION_ROOT "/sdcard/rehab_sessions"

static const char *TAG = "rehab_decision_ctx";

typedef struct {
    char *text;
    char *parse_text;
    const char *source;
    const char *status;
    size_t source_bytes;
    size_t sent_bytes;
    bool truncated;
} decision_prescription_t;

typedef struct {
    bool available;
    size_t count;
    double scores[DECISION_RECENT_SCORE_WINDOW_MAX];
    char task_ids[DECISION_RECENT_SCORE_WINDOW_MAX][32];
    char task_names[DECISION_RECENT_SCORE_WINDOW_MAX][REHAB_SCORE_TASK_NAME_LEN];
    char session_ids[DECISION_RECENT_SCORE_WINDOW_MAX][REHAB_SCORE_SESSION_ID_LEN];
    double average_score;
    char last_task_id[32];
    size_t mapped_count;
    unsigned cup_count;
    unsigned ball_count;
    char mapped_task_ids[DECISION_ITEM_WINDOW][32];
    char mapped_task_names[DECISION_ITEM_WINDOW][REHAB_SCORE_TASK_NAME_LEN];
    char mapped_session_ids[DECISION_ITEM_WINDOW][REHAB_SCORE_SESSION_ID_LEN];
    bool compensation_available;
    char compensation_task_name[REHAB_SCORE_TASK_NAME_LEN];
    char compensation_task_id[32];
    char compensation_action_family[24];
    char compensation_group[8];
    uint32_t compensation_action_count;
} decision_recent_scores_t;

static bool recent_scores_tend_level_two(
    const decision_recent_scores_t *recent)
{
    if (recent == NULL) {
        return false;
    }
    for (size_t i = 0U; i < recent->count; ++i) {
        if (isfinite(recent->scores[i]) && recent->scores[i] >= 50.0) {
            return true;
        }
    }
    return false;
}

static esp_err_t context_finish(cJSON *root, char *output, size_t output_size)
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

static esp_err_t context_error(char *output, size_t output_size,
                               const char *code, esp_err_t err)
{
    cJSON *root = cJSON_CreateObject();
    if (root != NULL) {
        cJSON_AddBoolToObject(root, "ok", false);
        cJSON_AddStringToObject(root, "code", code);
        cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
    }
    esp_err_t render_err = context_finish(root, output, output_size);
    return render_err == ESP_OK ? err : render_err;
}

static bool context_snapshot_ready(const rehab_task_decision_status_t *status)
{
    return status != NULL &&
           (status->state == REHAB_TASK_DECISION_STATE_COMPLETED ||
            status->state == REHAB_TASK_DECISION_STATE_DECIDING) &&
           status->final_state_frozen &&
           !status->worker_active &&
           !status->perception_in_flight &&
           status->generation != 0;
}

static bool score_session_id_valid(const char *value)
{
    if (value == NULL || strlen(value) != REHAB_SCORE_SESSION_ID_LEN - 1U ||
        value[8] != '_' || memcmp(value + 9, "TASK", 4) != 0) {
        return false;
    }
    for (size_t i = 0; i < 8U; ++i) {
        if (value[i] < '0' || value[i] > '9') {
            return false;
        }
    }
    for (size_t i = 13U; i < 16U; ++i) {
        if (value[i] < '0' || value[i] > '9') {
            return false;
        }
    }
    return true;
}

static void insert_recent_session_id(
    char (*ids)[REHAB_SCORE_SESSION_ID_LEN], size_t *count,
    const char *session_id)
{
    size_t used = *count;
    size_t position = 0;
    while (position < used && strcmp(ids[position], session_id) > 0) {
        ++position;
    }
    if (position < used && strcmp(ids[position], session_id) == 0) {
        return;
    }
    if (position >= DECISION_SESSION_CANDIDATE_LIMIT) {
        return;
    }
    size_t new_used = used < DECISION_SESSION_CANDIDATE_LIMIT
        ? used + 1U : used;
    for (size_t i = new_used - 1U; i > position; --i) {
        memcpy(ids[i], ids[i - 1U], REHAB_SCORE_SESSION_ID_LEN);
    }
    strlcpy(ids[position], session_id, REHAB_SCORE_SESSION_ID_LEN);
    *count = new_used;
}

static const char *task_id_from_name(rehab_task_decision_handle_t handle,
                                     const char *task_name)
{
    if (task_name == NULL) {
        return NULL;
    }
    for (size_t i = 0; i < rehab_task_decision_rules_task_count(); ++i) {
        const char *task_id = rehab_task_decision_rules_task_id_at(i);
        char canonical_name[REHAB_TASK_DECISION_SELECTED_TASK_NAME_MAX] = {0};
        char rules_version[REHAB_TASK_DECISION_RULES_VERSION_MAX] = {0};
        if (task_id != NULL &&
            rehab_task_decision_rules_task_metadata(
                handle, task_id, canonical_name, sizeof(canonical_name),
                rules_version, sizeof(rules_version)) == ESP_OK &&
            (strcmp(task_name, task_id) == 0 ||
             strcmp(task_name, canonical_name) == 0)) {
            return task_id;
        }
    }
    return NULL;
}

static void load_recent_scores(rehab_task_decision_handle_t handle,
                               decision_recent_scores_t *recent,
                               size_t recent_score_window)
{
    memset(recent, 0, sizeof(*recent));
    DIR *directory = opendir(DECISION_SCORE_SESSION_ROOT);
    if (directory == NULL) {
        return;
    }
    char (*ids)[REHAB_SCORE_SESSION_ID_LEN] = calloc(
        DECISION_SESSION_CANDIDATE_LIMIT, REHAB_SCORE_SESSION_ID_LEN);
    if (ids == NULL) {
        closedir(directory);
        return;
    }
    size_t candidate_count = 0;
    struct dirent *entry = NULL;
    while ((entry = readdir(directory)) != NULL) {
        if (score_session_id_valid(entry->d_name)) {
            insert_recent_session_id(ids, &candidate_count, entry->d_name);
        }
    }
    closedir(directory);

    for (size_t i = 0; i < candidate_count; ++i) {
        rehab_score_query_result_t *query = calloc(1, sizeof(*query));
        if (query == NULL) {
            break;
        }
        esp_err_t err = rehab_score_bridge_query(ids[i], query);
        bool valid = err == ESP_OK && query->error == REHAB_SCORE_QUERY_ERROR_NONE &&
            query->state == REHAB_SCORE_STATE_SUCCEEDED &&
            query->result_available && query->valid &&
            query->average_score_available && isfinite(query->average_score) &&
            query->average_score >= 0.0 && query->average_score <= 100.0;
        if (valid) {
            const char *task_id = task_id_from_name(handle, query->task_name);
            const rehab_task_policy_definition_t *mapped_task =
                task_id != NULL ? rehab_task_policy_find(task_id) : NULL;
            if (recent->count == 0U) {
                if (task_id != NULL) {
                    strlcpy(recent->last_task_id, task_id,
                            sizeof(recent->last_task_id));
                }
            }
            if (recent->count < recent_score_window) {
                size_t slot = recent->count++;
                recent->scores[slot] = query->average_score;
                if (task_id != NULL) {
                    strlcpy(recent->task_ids[slot], task_id,
                            sizeof(recent->task_ids[slot]));
                }
                strlcpy(recent->task_names[slot], query->task_name,
                        sizeof(recent->task_names[slot]));
                strlcpy(recent->session_ids[slot], ids[i],
                        sizeof(recent->session_ids[slot]));
            }
            if (mapped_task != NULL &&
                !recent->compensation_available) {
                recent->compensation_available = true;
                strlcpy(recent->compensation_task_name,
                        query->task_name,
                        sizeof(recent->compensation_task_name));
                recent->compensation_action_count =
                    query->compensation_action_count;
                strlcpy(recent->compensation_task_id,
                        mapped_task->task_id,
                        sizeof(recent->compensation_task_id));
                strlcpy(recent->compensation_action_family,
                        mapped_task->action_family,
                        sizeof(recent->compensation_action_family));
                strlcpy(recent->compensation_group,
                        mapped_task->semantic_group,
                        sizeof(recent->compensation_group));
            }
            if (task_id != NULL &&
                recent->mapped_count < DECISION_ITEM_WINDOW) {
                const rehab_task_policy_definition_t *task =
                    rehab_task_policy_find(task_id);
                size_t slot = recent->mapped_count++;
                strlcpy(recent->mapped_task_ids[slot], task_id,
                        sizeof(recent->mapped_task_ids[slot]));
                strlcpy(recent->mapped_task_names[slot], query->task_name,
                        sizeof(recent->mapped_task_names[slot]));
                strlcpy(recent->mapped_session_ids[slot], ids[i],
                        sizeof(recent->mapped_session_ids[slot]));
                if (task != NULL &&
                    strcmp(task->semantic_group, "cup") == 0) {
                    ++recent->cup_count;
                } else if (task != NULL) {
                    ++recent->ball_count;
                }
            }
        }
        free(query);
        if (recent->count >= recent_score_window &&
            recent->mapped_count >= DECISION_ITEM_WINDOW &&
            recent->compensation_available) {
            break;
        }
    }
    free(ids);

    if (recent->count > 0) {
        double total = 0.0;
        for (size_t i = 0; i < recent->count; ++i) {
            total += recent->scores[i];
        }
        recent->available = true;
        recent->average_score = total / (double)recent->count;
    }
}

static bool buffer_has_non_whitespace(const char *buffer, size_t length)
{
    for (size_t i = 0; i < length; ++i) {
        unsigned char value = (unsigned char)buffer[i];
        if (value != ' ' && value != '\t' && value != '\r' && value != '\n') {
            return true;
        }
    }
    return false;
}

static void prescription_release(decision_prescription_t *snapshot)
{
    if (snapshot == NULL) return;
    free(snapshot->text);
    free(snapshot->parse_text);
    memset(snapshot, 0, sizeof(*snapshot));
}

static esp_err_t prescription_empty(decision_prescription_t *snapshot,
                                    const char *status)
{
    snapshot->text = strdup("");
    snapshot->parse_text = strdup("");
    snapshot->source = "user_condition.txt";
    snapshot->status = status;
    return snapshot->text != NULL && snapshot->parse_text != NULL
        ? ESP_OK : ESP_ERR_NO_MEM;
}

static size_t utf8_prefix_length(const char *text, size_t length,
                                 size_t max_bytes, size_t max_codepoints)
{
    size_t offset = 0U;
    size_t codepoints = 0U;
    while (offset < length && offset < max_bytes &&
           codepoints < max_codepoints) {
        unsigned char first = (unsigned char)text[offset];
        size_t width = first < 0x80U ? 1U :
            ((first & 0xE0U) == 0xC0U ? 2U :
             ((first & 0xF0U) == 0xE0U ? 3U :
              ((first & 0xF8U) == 0xF0U ? 4U : 0U)));
        if (width == 0U || offset + width > length ||
            offset + width > max_bytes) break;
        bool valid = true;
        for (size_t i = 1U; i < width; ++i) {
            if (((unsigned char)text[offset + i] & 0xC0U) != 0x80U) {
                valid = false;
                break;
            }
        }
        if (!valid) break;
        offset += width;
        ++codepoints;
    }
    return offset;
}

static esp_err_t load_prescription(decision_prescription_t *snapshot)
{
    if (snapshot == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(snapshot, 0, sizeof(*snapshot));
    char path[DECISION_PRESCRIPTION_PATH_MAX] = {0};
    esp_err_t err = claw_paths_join(CLAW_PATH_DATA,
                                    DECISION_PRESCRIPTION_SUBPATH,
                                    path, sizeof(path));
    if (err != ESP_OK) {
        return prescription_empty(snapshot, "read_error");
    }
    struct stat info = {0};
    if (stat(path, &info) != 0) {
        return prescription_empty(snapshot,
                                  errno == ENOENT ? "missing" : "read_error");
    }
    if (!S_ISREG(info.st_mode)) {
        return prescription_empty(snapshot, "read_error");
    }
    if (info.st_size == 0) {
        return prescription_empty(snapshot, "empty");
    }
    if (info.st_size < 0 ||
        (uint64_t)info.st_size > DECISION_PRESCRIPTION_SOURCE_MAX_BYTES) {
        return prescription_empty(snapshot, "too_large");
    }
    size_t length = (size_t)info.st_size;
    char *text = calloc(1, length + 1U);
    if (text == NULL) {
        return ESP_ERR_NO_MEM;
    }
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        free(text);
        return prescription_empty(snapshot, "read_error");
    }
    size_t read_length = fread(text, 1, length, file);
    bool read_ok = read_length == length && ferror(file) == 0;
    fclose(file);
    if (!read_ok) {
        free(text);
        return prescription_empty(snapshot, "read_error");
    }
    text[length] = '\0';
    if (!buffer_has_non_whitespace(text, length)) {
        free(text);
        return prescription_empty(snapshot, "empty");
    }
    if (!rehab_task_decision_text_valid(
            text, DECISION_PRESCRIPTION_SOURCE_MAX_BYTES,
            DECISION_PRESCRIPTION_SOURCE_MAX_BYTES, false)) {
        free(text);
        return prescription_empty(snapshot, "invalid_utf8");
    }
    size_t prefix = utf8_prefix_length(
        text, length, DECISION_PRESCRIPTION_MAX_BYTES,
        DECISION_PRESCRIPTION_MAX_CODEPOINTS);
    snapshot->text = calloc(1U, prefix + 1U);
    if (snapshot->text == NULL) {
        free(text);
        return ESP_ERR_NO_MEM;
    }
    memcpy(snapshot->text, text, prefix);
    snapshot->parse_text = text;
    snapshot->source = "user_condition.txt";
    snapshot->status = "ok";
    snapshot->source_bytes = length;
    snapshot->sent_bytes = prefix;
    snapshot->truncated = prefix < length;
    return ESP_OK;
}

esp_err_t rehab_task_decision_context_ability_level(
    int *out_level, bool *out_parsed)
{
    if (out_level == NULL || out_parsed == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    decision_prescription_t snapshot = {0};
    esp_err_t err = load_prescription(&snapshot);
    if (err != ESP_OK) {
        return err;
    }
    *out_level = rehab_task_policy_parse_ability(snapshot.parse_text,
                                                  out_parsed);
    if (!*out_parsed) {
        ESP_LOGW(TAG,
                 "ABILITY_LEVEL_FALLBACK level=1 source=%s reason=missing_or_invalid",
                 snapshot.source != NULL ? snapshot.source : "none");
    } else {
        ESP_LOGI(TAG, "ABILITY_LEVEL_PARSED level=%d source=%s",
                 *out_level, snapshot.source != NULL ? snapshot.source : "none");
    }
    prescription_release(&snapshot);
    return ESP_OK;
}

static cJSON *context_environment_json(
    const rehab_task_decision_status_t *status)
{
    cJSON *environment = cJSON_CreateObject();
    if (environment == NULL) {
        return NULL;
    }
    cJSON_AddStringToObject(environment, "state",
                            rehab_task_decision_state_name(status->state));
    cJSON_AddBoolToObject(environment, "final_state_frozen",
                          status->final_state_frozen);
    cJSON_AddNumberToObject(environment, "object_observation_count",
                            status->object_observation_count);
    cJSON *classes = cJSON_AddArrayToObject(environment, "classes");
    if (classes == NULL) {
        cJSON_Delete(environment);
        return NULL;
    }
    size_t class_count = status->class_count < REHAB_TASK_DECISION_MAX_CLASSES
        ? status->class_count : REHAB_TASK_DECISION_MAX_CLASSES;
    for (size_t i = 0; i < class_count; ++i) {
        const rehab_environment_class_summary_t *item = &status->classes[i];
        cJSON *entry = cJSON_CreateObject();
        if (entry == NULL) {
            cJSON_Delete(environment);
            return NULL;
        }
        cJSON_AddItemToArray(classes, entry);
        cJSON_AddStringToObject(entry, "class_name", item->class_name);
        cJSON_AddNumberToObject(entry, "seen_count", item->seen_count);
        cJSON_AddBoolToObject(entry, "max_confidence_valid",
                              item->confidence_count > 0);
        cJSON_AddNumberToObject(entry, "max_confidence",
                                item->confidence_count > 0
                                    ? item->max_confidence : 0.0);
        cJSON_AddBoolToObject(entry, "distance_valid",
                              item->valid_distance_count > 0);
        cJSON_AddNumberToObject(entry, "distance_cm",
                                item->valid_distance_count > 0
                                    ? item->min_distance_cm : 0.0);
        cJSON_AddNumberToObject(entry, "left_count", item->left_count);
        cJSON_AddNumberToObject(entry, "center_count", item->center_count);
        cJSON_AddNumberToObject(entry, "right_count", item->right_count);
    }
    return environment;
}

esp_err_t rehab_task_decision_context_render(
    rehab_task_decision_handle_t handle,
    uint32_t request_id,
    char *output,
    size_t output_size)
{
    if (handle == NULL || output == NULL || output_size == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    output[0] = '\0';
    rehab_task_decision_status_t *status = calloc(1, sizeof(*status));
    if (status == NULL) {
        return context_error(output, output_size, "context_no_memory",
                             ESP_ERR_NO_MEM);
    }
    esp_err_t err = rehab_task_decision_scan_get_status(handle, status);
    if (err != ESP_OK || !context_snapshot_ready(status)) {
        free(status);
        return context_error(output, output_size, "scan_not_completed",
                             err == ESP_OK ? ESP_ERR_INVALID_STATE : err);
    }

    size_t recent_score_window = 0;
    err = rehab_task_decision_rules_recent_score_window(
        handle, &recent_score_window);
    if (err != ESP_OK || recent_score_window == 0 ||
        recent_score_window > DECISION_RECENT_SCORE_WINDOW_MAX) {
        free(status);
        return context_error(output, output_size, "rules_unavailable",
                             err == ESP_OK ? ESP_ERR_INVALID_RESPONSE : err);
    }
    decision_recent_scores_t recent = {0};
    load_recent_scores(handle, &recent, recent_score_window);
    decision_prescription_t prescription_snapshot = {0};
    err = load_prescription(&prescription_snapshot);
    if (err != ESP_OK) {
        free(status);
        return context_error(output, output_size, "prescription_unavailable",
                             err);
    }

    cJSON *root = cJSON_CreateObject();
    cJSON *environment = context_environment_json(status);
    if (root == NULL || environment == NULL) {
        cJSON_Delete(root);
        cJSON_Delete(environment);
        prescription_release(&prescription_snapshot);
        free(status);
        return context_error(output, output_size, "context_no_memory",
                             ESP_ERR_NO_MEM);
    }
    cJSON_AddStringToObject(root, "schema_version", DECISION_CONTEXT_SCHEMA);
    cJSON_AddNumberToObject(root, "scan_generation", status->generation);
    cJSON_AddItemToObject(root, "environment", environment);

    bool ability_parsed = false;
    int ability_level = rehab_task_policy_parse_ability(
        prescription_snapshot.parse_text, &ability_parsed);
    cJSON *ability = cJSON_AddObjectToObject(root, "patient_ability");
    if (ability == NULL ||
        cJSON_AddNumberToObject(ability, "ability_level",
                                ability_level) == NULL ||
        cJSON_AddBoolToObject(ability, "parsed", ability_parsed) == NULL ||
        cJSON_AddStringToObject(
            ability, "fallback",
            ability_parsed ? "none" : "conservative_level_1") == NULL) {
        cJSON_Delete(root);
        prescription_release(&prescription_snapshot);
        free(status);
        return context_error(output, output_size, "context_no_memory",
                             ESP_ERR_NO_MEM);
    }

    cJSON *training = cJSON_AddObjectToObject(root, "recent_training");
    if (training == NULL) {
        cJSON_Delete(root);
        prescription_release(&prescription_snapshot);
        free(status);
        return context_error(output, output_size, "context_no_memory",
                             ESP_ERR_NO_MEM);
    }
    cJSON_AddBoolToObject(training, "recent_score_available", recent.available);
    cJSON_AddNumberToObject(training, "score_count", recent.count);
    if (recent.available) {
        cJSON_AddNumberToObject(training, "average_score",
                                recent.average_score);
    } else {
        cJSON_AddNullToObject(training, "average_score");
    }
    cJSON_AddStringToObject(
        training, "difficulty_tendency",
        recent_scores_tend_level_two(&recent)
            ? "level_2" : "level_1");
    if (recent.last_task_id[0] != '\0') {
        cJSON_AddStringToObject(training, "last_task_id",
                                recent.last_task_id);
    } else {
        cJSON_AddNullToObject(training, "last_task_id");
    }
    cJSON *scores = cJSON_AddArrayToObject(training, "scores");
    if (scores == NULL) {
        cJSON_Delete(root);
        prescription_release(&prescription_snapshot);
        free(status);
        return context_error(output, output_size, "context_no_memory",
                             ESP_ERR_NO_MEM);
    }
    for (size_t i = 0; i < recent.count; ++i) {
        cJSON_AddItemToArray(scores, cJSON_CreateNumber(recent.scores[i]));
    }

    cJSON *prescription = cJSON_AddObjectToObject(root, "prescription");
    cJSON *demo_context = cJSON_AddObjectToObject(root, "demo_context");
    if (prescription == NULL || demo_context == NULL) {
        cJSON_Delete(root);
        prescription_release(&prescription_snapshot);
        free(status);
        return context_error(output, output_size, "context_no_memory",
                             ESP_ERR_NO_MEM);
    }
    cJSON_AddStringToObject(prescription, "source",
                            prescription_snapshot.source);
    cJSON_AddStringToObject(prescription, "text",
                            prescription_snapshot.text);
    cJSON_AddStringToObject(demo_context, "virtual_target_zone", "right");

    ESP_LOGI(TAG,
             "DECISION_CONTEXT generation=%lu classes=%u recent_score_count=%u prescription_source=%s",
             (unsigned long)status->generation,
             (unsigned)status->class_count,
             (unsigned)recent.count,
             prescription_snapshot.source);
    uint32_t generation = status->generation;
    prescription_release(&prescription_snapshot);
    free(status);
    err = context_finish(root, output, output_size);
    if (err == ESP_OK) {
        err = rehab_task_decision_context_record(handle, generation,
                                                 request_id);
        if (err != ESP_OK) {
            return context_error(output, output_size, "context_stale", err);
        }
    }
    return err;
}

esp_err_t rehab_task_decision_context_add_inputs(
    rehab_task_decision_handle_t handle,
    cJSON *root)
{
    if (handle == NULL || !cJSON_IsObject(root)) {
        return ESP_ERR_INVALID_ARG;
    }
    decision_recent_scores_t recent = {0};
    load_recent_scores(handle, &recent, 3U);

    decision_prescription_t prescription = {0};
    esp_err_t err = load_prescription(&prescription);
    if (err != ESP_OK) {
        return err;
    }

    rehab_task_ability_parse_status_t ability_status =
        REHAB_TASK_ABILITY_MISSING;
    char matched_line[96] = {0};
    int ability_level = rehab_task_policy_parse_ability_line(
        prescription.parse_text, &ability_status,
        matched_line, sizeof(matched_line));
    cJSON *patient_condition = cJSON_AddObjectToObject(
        root, "patient_condition");
    cJSON *ability = cJSON_AddObjectToObject(root, "patient_ability");
    cJSON *performance = cJSON_AddObjectToObject(
        root, "recent_performance");
    cJSON *difficulty_records = performance != NULL
        ? cJSON_AddArrayToObject(performance, "records") : NULL;
    cJSON *frequency = cJSON_AddObjectToObject(root, "item_frequency");
    cJSON *mapped_records = frequency != NULL
        ? cJSON_AddArrayToObject(frequency, "records") : NULL;
    cJSON *compensation = cJSON_AddObjectToObject(
        root, "latest_compensation");
    bool ok = patient_condition != NULL && ability != NULL &&
        performance != NULL && difficulty_records != NULL &&
        frequency != NULL && mapped_records != NULL && compensation != NULL &&
        cJSON_AddStringToObject(
            patient_condition, "source",
            prescription.source != NULL
                ? prescription.source : "none") != NULL &&
        cJSON_AddStringToObject(
            patient_condition, "raw_text", prescription.text) != NULL &&
        cJSON_AddStringToObject(
            patient_condition, "status", prescription.status) != NULL &&
        cJSON_AddNumberToObject(
            patient_condition, "source_bytes",
            (double)prescription.source_bytes) != NULL &&
        cJSON_AddNumberToObject(
            patient_condition, "sent_bytes",
            (double)prescription.sent_bytes) != NULL &&
        cJSON_AddBoolToObject(
            patient_condition, "truncated", prescription.truncated) != NULL &&
        cJSON_AddNumberToObject(ability, "level",
                                ability_level) != NULL &&
        cJSON_AddStringToObject(
            ability, "parse_status",
            rehab_task_policy_ability_status_name(ability_status)) != NULL &&
        cJSON_AddStringToObject(ability, "source",
                                "user_condition.txt") != NULL &&
        cJSON_AddNumberToObject(performance, "window_limit", 3) != NULL &&
        cJSON_AddNumberToObject(performance, "record_count",
                                recent.count) != NULL &&
        cJSON_AddStringToObject(
            performance, "unknown_history_policy",
            "preserved: valid unknown or legacy tasks may occupy this window") != NULL &&
        cJSON_AddStringToObject(
            performance, "tendency_level",
            recent_scores_tend_level_two(&recent)
                ? "level_2" : "level_1") != NULL &&
        cJSON_AddStringToObject(
            performance, "status",
            recent.count > 0U ? "available" : "none") != NULL;
    if (ok) {
        ok = matched_line[0] != '\0'
            ? cJSON_AddStringToObject(ability, "matched_line",
                                      matched_line) != NULL
            : cJSON_AddNullToObject(ability, "matched_line") != NULL;
    }
    for (size_t i = 0; ok && i < recent.count; ++i) {
        cJSON *entry = cJSON_CreateObject();
        ok = entry != NULL;
        if (ok) {
            cJSON_AddItemToArray(difficulty_records, entry);
            ok = cJSON_AddStringToObject(
                     entry, "session_id", recent.session_ids[i]) != NULL &&
                 cJSON_AddStringToObject(
                     entry, "task_name", recent.task_names[i]) != NULL &&
                 cJSON_AddNumberToObject(
                     entry, "average_score", recent.scores[i]) != NULL;
        }
    }
    if (ok) {
        ok = cJSON_AddNumberToObject(frequency, "window_limit",
                                     DECISION_ITEM_WINDOW) != NULL &&
            cJSON_AddNumberToObject(frequency, "mapped_record_count",
                                    recent.mapped_count) != NULL &&
            cJSON_AddNumberToObject(frequency, "cup_count",
                                    recent.cup_count) != NULL &&
            cJSON_AddNumberToObject(frequency, "ball_count",
                                    recent.ball_count) != NULL &&
            cJSON_AddStringToObject(
                frequency, "status",
                recent.mapped_count > 0U ? "available" : "none") != NULL &&
            cJSON_AddStringToObject(
                frequency, "unknown_history_policy",
                "skip unknown or legacy tasks and continue searching") != NULL;
    }
    for (size_t i = 0; ok && i < recent.mapped_count; ++i) {
        const rehab_task_policy_definition_t *task =
            rehab_task_policy_find(recent.mapped_task_ids[i]);
        cJSON *entry = cJSON_CreateObject();
        ok = entry != NULL && task != NULL;
        if (ok) {
            cJSON_AddItemToArray(mapped_records, entry);
            ok = cJSON_AddStringToObject(
                     entry, "session_id",
                     recent.mapped_session_ids[i]) != NULL &&
                 cJSON_AddStringToObject(
                     entry, "task_name", recent.mapped_task_names[i]) != NULL &&
                 cJSON_AddStringToObject(
                     entry, "mapped_group", task->semantic_group) != NULL;
        } else {
            cJSON_Delete(entry);
        }
    }
    if (ok && recent.compensation_available) {
        ok = cJSON_AddStringToObject(
                 compensation, "status", "available") != NULL &&
            cJSON_AddStringToObject(
                 compensation, "task_name",
                 recent.compensation_task_name) != NULL &&
            cJSON_AddNumberToObject(
                 compensation, "compensation_action_count",
                 recent.compensation_action_count) != NULL;
        if (ok) {
            ok = recent.compensation_task_id[0] != '\0'
                ? cJSON_AddStringToObject(
                      compensation, "exact_task_id",
                      recent.compensation_task_id) != NULL
                : cJSON_AddNullToObject(
                      compensation, "exact_task_id") != NULL;
        }
        if (ok) {
            ok = recent.compensation_action_family[0] != '\0'
                ? cJSON_AddStringToObject(
                      compensation, "action_family",
                      recent.compensation_action_family) != NULL
                : cJSON_AddNullToObject(
                      compensation, "action_family") != NULL;
        }
        if (ok) {
            ok = recent.compensation_group[0] != '\0'
                ? cJSON_AddStringToObject(
                      compensation, "mapped_group",
                      recent.compensation_group) != NULL
                : cJSON_AddNullToObject(
                      compensation, "mapped_group") != NULL;
        }
        if (ok) {
            ok = cJSON_AddStringToObject(
                     compensation, "mapping_status",
                     recent.compensation_task_id[0] != '\0'
                         ? "mapped" : "unknown_task") != NULL;
        }
    } else if (ok) {
        ok = cJSON_AddStringToObject(compensation, "status", "none") != NULL &&
            cJSON_AddNullToObject(compensation, "task_name") != NULL &&
            cJSON_AddNullToObject(compensation, "exact_task_id") != NULL &&
            cJSON_AddNullToObject(compensation, "action_family") != NULL &&
            cJSON_AddNullToObject(compensation, "mapped_group") != NULL &&
            cJSON_AddStringToObject(compensation, "mapping_status",
                                    "none") != NULL &&
            cJSON_AddNumberToObject(
                compensation, "compensation_action_count", 0) != NULL;
    }
    if (ok) {
        const rehab_task_scoring_policy_t *policy =
            rehab_task_scoring_policy();
        const uint32_t n = recent.compensation_action_count;
        const double severity = n == 0U ? 0.0 :
            (n == 1U ? policy->compensation_severity_1 :
             (n == 2U ? policy->compensation_severity_2 :
                        policy->compensation_severity_3_plus));
        ok = cJSON_AddNumberToObject(compensation, "severity",
                                     severity) != NULL;
    }
    ESP_LOGI(TAG,
             "FORMAL_DECISION_INPUT recent_score_count=%u mapped_count=%u compensation=%s prescription_source=%s",
             (unsigned)recent.count,
             (unsigned)recent.mapped_count,
             recent.compensation_available ? "available" : "none",
             prescription.source != NULL ? prescription.source : "none");
    prescription_release(&prescription);
    return ok ? ESP_OK : ESP_ERR_NO_MEM;
}
