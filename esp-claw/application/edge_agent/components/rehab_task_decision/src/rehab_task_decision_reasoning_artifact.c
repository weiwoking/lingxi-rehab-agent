// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_reasoning_artifact.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "cJSON.h"
#include "claw_paths.h"
#include "esp_log.h"

#define REASONING_ARTIFACT_SCHEMA "rehab_decision_reasoning.v1"
#define REASONING_ARTIFACT_DIRECTORY_SUBPATH "rehab"
#define REASONING_ARTIFACT_SUBPATH \
    "rehab/latest_decision_reasoning.json"
#define REASONING_ARTIFACT_NEXT_SUBPATH \
    "rehab/latest_decision_reasoning.json.next"
#define REASONING_ARTIFACT_ROLLBACK_SUBPATH \
    "rehab/latest_decision_reasoning.json.rollback"
#define REASONING_ARTIFACT_PATH_MAX 192U

static const char *TAG = "claw_reasoning";

static esp_err_t artifact_paths(
    char directory[REASONING_ARTIFACT_PATH_MAX],
    char final_path[REASONING_ARTIFACT_PATH_MAX],
    char next_path[REASONING_ARTIFACT_PATH_MAX],
    char rollback_path[REASONING_ARTIFACT_PATH_MAX])
{
    esp_err_t err = claw_paths_join(
        CLAW_PATH_DATA, REASONING_ARTIFACT_DIRECTORY_SUBPATH,
        directory, REASONING_ARTIFACT_PATH_MAX);
    if (err == ESP_OK) {
        err = claw_paths_join(CLAW_PATH_DATA, REASONING_ARTIFACT_SUBPATH,
                              final_path, REASONING_ARTIFACT_PATH_MAX);
    }
    if (err == ESP_OK) {
        err = claw_paths_join(CLAW_PATH_DATA,
                              REASONING_ARTIFACT_NEXT_SUBPATH,
                              next_path, REASONING_ARTIFACT_PATH_MAX);
    }
    if (err == ESP_OK) {
        err = claw_paths_join(CLAW_PATH_DATA,
                              REASONING_ARTIFACT_ROLLBACK_SUBPATH,
                              rollback_path, REASONING_ARTIFACT_PATH_MAX);
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

static void created_at_now(char output[40])
{
    time_t now = time(NULL);
    struct tm utc = {0};
    if (now > 0 && gmtime_r(&now, &utc) != NULL &&
        strftime(output, 40, "%Y-%m-%dT%H:%M:%SZ", &utc) > 0) {
        return;
    }
    strlcpy(output, "1970-01-01T00:00:00Z", 40);
}

static cJSON *artifact_json(
    const rehab_task_decision_coordinator_status_t *status,
    const char *reasoning_content,
    size_t reasoning_bytes)
{
    char created_at[40] = {0};
    created_at_now(created_at);

    cJSON *root = cJSON_CreateObject();
    if (root == NULL ||
        cJSON_AddStringToObject(root, "schema_version",
                               REASONING_ARTIFACT_SCHEMA) == NULL ||
        cJSON_AddStringToObject(root, "decision_id",
                               status->decision_id) == NULL ||
        cJSON_AddNumberToObject(root, "generation",
                               status->generation) == NULL ||
        cJSON_AddStringToObject(root, "task_id", status->task_id) == NULL ||
        cJSON_AddStringToObject(root, "task_name",
                               status->task_name) == NULL ||
        cJSON_AddNumberToObject(root, "latency_sec",
                               status->trace.elapsed_sec) == NULL ||
        /* Usage is logged by the backend but is not retained in the response. */
        cJSON_AddNumberToObject(root, "reasoning_tokens", 0) == NULL ||
        cJSON_AddNumberToObject(root, "reasoning_bytes",
                               (double)reasoning_bytes) == NULL ||
        cJSON_AddStringToObject(root, "reasoning_content",
                               reasoning_content) == NULL ||
        cJSON_AddStringToObject(root, "created_at", created_at) == NULL) {
        cJSON_Delete(root);
        return NULL;
    }
    return root;
}

static esp_err_t recover_interrupted_transaction(const char *final_path,
                                                  const char *next_path,
                                                  const char *rollback_path)
{
    struct stat info = {0};
    const bool final_exists = stat(final_path, &info) == 0;
    const bool rollback_exists = stat(rollback_path, &info) == 0;

    (void)unlink(next_path);
    if (!final_exists && rollback_exists) {
        return rename(rollback_path, final_path) == 0 ? ESP_OK : ESP_FAIL;
    }
    if (final_exists && rollback_exists &&
        unlink(rollback_path) != 0 && errno != ENOENT) {
        return ESP_FAIL;
    }
    return ESP_OK;
}

static esp_err_t write_latest(const char *json, size_t json_bytes,
                              const char *final_path,
                              const char *next_path,
                              const char *rollback_path)
{
    esp_err_t err = recover_interrupted_transaction(
        final_path, next_path, rollback_path);
    if (err != ESP_OK) {
        return err;
    }

    FILE *file = fopen(next_path, "wb");
    if (file == NULL) {
        return ESP_FAIL;
    }
    bool written = fwrite(json, 1, json_bytes, file) == json_bytes &&
        fflush(file) == 0 && !ferror(file) && fsync(fileno(file)) == 0;
    if (fclose(file) != 0) {
        written = false;
    }
    if (!written) {
        (void)unlink(next_path);
        return ESP_FAIL;
    }

    struct stat info = {0};
    const bool had_previous_artifact = stat(final_path, &info) == 0;
    if (had_previous_artifact && rename(final_path, rollback_path) != 0) {
        (void)unlink(next_path);
        return ESP_FAIL;
    }
    if (rename(next_path, final_path) != 0) {
        if (had_previous_artifact) {
            (void)rename(rollback_path, final_path);
        }
        (void)unlink(next_path);
        return ESP_FAIL;
    }
    if (had_previous_artifact &&
        unlink(rollback_path) != 0 && errno != ENOENT) {
        ESP_LOGW(TAG, "rollback cleanup failed path=%s", rollback_path);
    }
    return ESP_OK;
}

esp_err_t rehab_task_decision_reasoning_artifact_save_latest(
    const rehab_task_decision_coordinator_status_t *status,
    const char *reasoning_content)
{
    if (status == NULL || reasoning_content == NULL ||
        reasoning_content[0] == '\0' ||
        status->state != REHAB_TASK_DECISION_COORDINATOR_SELECTED ||
        status->generation == 0U || status->decision_id[0] == '\0' ||
        status->task_id[0] == '\0' || status->task_name[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }

    char directory[REASONING_ARTIFACT_PATH_MAX] = {0};
    char final_path[REASONING_ARTIFACT_PATH_MAX] = {0};
    char next_path[REASONING_ARTIFACT_PATH_MAX] = {0};
    char rollback_path[REASONING_ARTIFACT_PATH_MAX] = {0};
    esp_err_t err = artifact_paths(directory, final_path, next_path,
                                   rollback_path);
    if (err == ESP_OK) {
        err = ensure_directory(directory);
    }
    if (err != ESP_OK) {
        return err;
    }

    const size_t reasoning_bytes = strlen(reasoning_content);
    cJSON *root = artifact_json(status, reasoning_content, reasoning_bytes);
    char *json = root != NULL ? cJSON_PrintUnformatted(root) : NULL;
    cJSON_Delete(root);
    if (json == NULL) {
        return ESP_ERR_NO_MEM;
    }

    const size_t json_bytes = strlen(json);
    err = write_latest(json, json_bytes, final_path, next_path,
                       rollback_path);
    cJSON_free(json);
    if (err == ESP_OK) {
        ESP_LOGI(TAG,
                 "LATEST_ARTIFACT_SAVED path=%s bytes=%u tokens=0",
                 final_path, (unsigned)reasoning_bytes);
    }
    return err;
}
