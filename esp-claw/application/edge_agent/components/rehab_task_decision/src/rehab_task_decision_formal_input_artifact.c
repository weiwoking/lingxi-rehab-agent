// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_formal_input_artifact.h"

#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "claw_paths.h"
#include "esp_log.h"

#define FORMAL_INPUT_ARTIFACT_DIRECTORY_SUBPATH "rehab"
#define FORMAL_INPUT_ARTIFACT_SUBPATH \
    "rehab/latest_formal_decision_input.json"
#define FORMAL_INPUT_ARTIFACT_NEXT_SUBPATH \
    "rehab/latest_formal_decision_input.json.next"
#define FORMAL_INPUT_ARTIFACT_ROLLBACK_SUBPATH \
    "rehab/latest_formal_decision_input.json.rollback"
#define FORMAL_INPUT_ARTIFACT_PATH_MAX 192U

static const char *TAG = "rehab_formal_input";

static esp_err_t artifact_paths(
    char directory[FORMAL_INPUT_ARTIFACT_PATH_MAX],
    char final_path[FORMAL_INPUT_ARTIFACT_PATH_MAX],
    char next_path[FORMAL_INPUT_ARTIFACT_PATH_MAX],
    char rollback_path[FORMAL_INPUT_ARTIFACT_PATH_MAX])
{
    esp_err_t err = claw_paths_join(
        CLAW_PATH_DATA, FORMAL_INPUT_ARTIFACT_DIRECTORY_SUBPATH,
        directory, FORMAL_INPUT_ARTIFACT_PATH_MAX);
    if (err == ESP_OK) {
        err = claw_paths_join(CLAW_PATH_DATA, FORMAL_INPUT_ARTIFACT_SUBPATH,
                              final_path, FORMAL_INPUT_ARTIFACT_PATH_MAX);
    }
    if (err == ESP_OK) {
        err = claw_paths_join(CLAW_PATH_DATA,
                              FORMAL_INPUT_ARTIFACT_NEXT_SUBPATH,
                              next_path, FORMAL_INPUT_ARTIFACT_PATH_MAX);
    }
    if (err == ESP_OK) {
        err = claw_paths_join(CLAW_PATH_DATA,
                              FORMAL_INPUT_ARTIFACT_ROLLBACK_SUBPATH,
                              rollback_path, FORMAL_INPUT_ARTIFACT_PATH_MAX);
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

static esp_err_t write_latest(const char *formal_input,
                              size_t formal_input_bytes,
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
    bool written =
        fwrite(formal_input, 1, formal_input_bytes, file) ==
            formal_input_bytes &&
        fflush(file) == 0 && !ferror(file) &&
        fsync(fileno(file)) == 0;
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

esp_err_t rehab_task_decision_formal_input_artifact_save_latest(
    const char *formal_input,
    size_t formal_input_bytes)
{
    if (formal_input == NULL || formal_input_bytes == 0U ||
        strlen(formal_input) != formal_input_bytes) {
        return ESP_ERR_INVALID_ARG;
    }

    char directory[FORMAL_INPUT_ARTIFACT_PATH_MAX] = {0};
    char final_path[FORMAL_INPUT_ARTIFACT_PATH_MAX] = {0};
    char next_path[FORMAL_INPUT_ARTIFACT_PATH_MAX] = {0};
    char rollback_path[FORMAL_INPUT_ARTIFACT_PATH_MAX] = {0};
    esp_err_t err = artifact_paths(directory, final_path, next_path,
                                   rollback_path);
    if (err == ESP_OK) {
        err = ensure_directory(directory);
    }
    if (err == ESP_OK) {
        err = write_latest(formal_input, formal_input_bytes, final_path,
                           next_path, rollback_path);
    }
    if (err == ESP_OK) {
        ESP_LOGI(TAG,
                 "FORMAL_INPUT_ARTIFACT_SAVED path=%s bytes=%u",
                 final_path, (unsigned)formal_input_bytes);
    }
    return err;
}
