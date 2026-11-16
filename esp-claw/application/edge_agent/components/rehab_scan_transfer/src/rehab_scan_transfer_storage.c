// SPDX-License-Identifier: Apache-2.0
#include "rehab_scan_transfer_priv.h"

#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "esp_heap_caps.h"

static esp_err_t ensure_directory(const char *path, int *out_errno)
{
    struct stat status;
    if (mkdir(path, 0777) == 0) return ESP_OK;
    int saved = errno;
    if (saved == EEXIST && stat(path, &status) == 0 && S_ISDIR(status.st_mode)) return ESP_OK;
    if (out_errno) *out_errno = saved;
    return ESP_FAIL;
}

esp_err_t rehab_scan_transfer_prepare_result_paths(
    rehab_scan_transfer_handle_t handle, const char *scan_id,
    char *temporary, size_t temporary_size,
    char *final, size_t final_size, int *out_errno)
{
    if (handle == NULL || scan_id == NULL || temporary == NULL || final == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    int saved = 0;
    esp_err_t err = ensure_directory(handle->result_root_path, &saved);
    char directory[REHAB_SCAN_TRANSFER_PATH_MAX];
    int count = snprintf(directory, sizeof(directory), "%s/%s",
                         handle->result_root_path, scan_id);
    if (err == ESP_OK && (count <= 0 || (size_t)count >= sizeof(directory))) {
        err = ESP_ERR_INVALID_SIZE;
    }
    if (err == ESP_OK) err = ensure_directory(directory, &saved);
    count = snprintf(temporary, temporary_size, "%s/result.json.tmp", directory);
    if (err == ESP_OK && (count <= 0 || (size_t)count >= temporary_size)) err = ESP_ERR_INVALID_SIZE;
    count = snprintf(final, final_size, "%s/result.json", directory);
    if (err == ESP_OK && (count <= 0 || (size_t)count >= final_size)) err = ESP_ERR_INVALID_SIZE;
    struct stat status;
    if (err == ESP_OK && stat(final, &status) == 0) {
        saved = EEXIST;
        err = ESP_ERR_INVALID_STATE;
    } else if (err == ESP_OK && errno != ENOENT) {
        saved = errno;
        err = ESP_FAIL;
    }
    if (err == ESP_OK && unlink(temporary) != 0 && errno != ENOENT) {
        saved = errno;
        err = ESP_FAIL;
    }
    if (out_errno) *out_errno = saved;
    return err;
}

esp_err_t rehab_scan_transfer_load_file(const char *path, size_t maximum,
                                        char **out_bytes, size_t *out_length,
                                        int *out_errno)
{
    if (path == NULL || out_bytes == NULL || out_length == NULL || maximum == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_bytes = NULL;
    *out_length = 0;
    struct stat status;
    if (stat(path, &status) != 0 || !S_ISREG(status.st_mode) || status.st_size <= 0 ||
        (uint64_t)status.st_size > maximum) {
        if (out_errno) *out_errno = errno;
        return ESP_ERR_INVALID_SIZE;
    }
    size_t length = (size_t)status.st_size;
    char *bytes = heap_caps_malloc(length + 1U, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (bytes == NULL) bytes = heap_caps_malloc(length + 1U, MALLOC_CAP_8BIT);
    if (bytes == NULL) return ESP_ERR_NO_MEM;
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        if (out_errno) *out_errno = errno;
        heap_caps_free(bytes);
        return ESP_ERR_NOT_FOUND;
    }
    size_t got = fread(bytes, 1, length, file);
    int extra = fgetc(file);
    int close_result = fclose(file);
    if (got != length || extra != EOF || close_result != 0) {
        if (out_errno) *out_errno = errno != 0 ? errno : EIO;
        heap_caps_free(bytes);
        return ESP_FAIL;
    }
    bytes[length] = '\0';
    *out_bytes = bytes;
    *out_length = length;
    return ESP_OK;
}

esp_err_t rehab_scan_transfer_commit_result(const char *temporary,
                                            const char *final,
                                            int *out_errno)
{
    if (temporary == NULL || final == NULL) return ESP_ERR_INVALID_ARG;
    struct stat status;
    if (stat(final, &status) == 0) {
        if (out_errno) *out_errno = EEXIST;
        return ESP_ERR_INVALID_STATE;
    }
    if (errno != ENOENT) {
        if (out_errno) *out_errno = errno;
        return ESP_FAIL;
    }
    if (rename(temporary, final) != 0) {
        if (out_errno) *out_errno = errno;
        return ESP_FAIL;
    }
    if (out_errno) *out_errno = 0;
    return ESP_OK;
}

esp_err_t rehab_scan_transfer_prepare_latest_scene_paths(
    rehab_scan_transfer_handle_t handle,
    char *temporary, size_t temporary_size,
    char *final, size_t final_size, int *out_errno)
{
    if (handle == NULL || temporary == NULL || final == NULL ||
        handle->latest_scene_result_path[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    if (strlcpy(final, handle->latest_scene_result_path, final_size) >=
        final_size) {
        return ESP_ERR_INVALID_SIZE;
    }
    int count = snprintf(temporary, temporary_size, "%s.tmp", final);
    if (count <= 0 || (size_t)count >= temporary_size) {
        return ESP_ERR_INVALID_SIZE;
    }
    char parent[REHAB_SCAN_TRANSFER_PATH_MAX];
    if (strlcpy(parent, final, sizeof(parent)) >= sizeof(parent)) {
        return ESP_ERR_INVALID_SIZE;
    }
    char *separator = strrchr(parent, '/');
    if (separator == NULL || separator == parent) {
        return ESP_ERR_INVALID_ARG;
    }
    *separator = '\0';
    int saved = 0;
    esp_err_t err = ensure_directory(parent, &saved);
    char backup[REHAB_SCAN_TRANSFER_PATH_MAX];
    int backup_count = snprintf(backup, sizeof(backup), "%s.previous", final);
    if (err == ESP_OK &&
        (backup_count <= 0 || (size_t)backup_count >= sizeof(backup))) {
        err = ESP_ERR_INVALID_SIZE;
    }
    struct stat final_status;
    struct stat backup_status;
    bool final_exists = false;
    bool backup_exists = false;
    if (err == ESP_OK) {
        final_exists = stat(final, &final_status) == 0;
        if (!final_exists && errno != ENOENT) {
            saved = errno;
            err = ESP_FAIL;
        } else if (final_exists && !S_ISREG(final_status.st_mode)) {
            saved = EINVAL;
            err = ESP_FAIL;
        }
    }
    if (err == ESP_OK) {
        backup_exists = stat(backup, &backup_status) == 0;
        if (!backup_exists && errno != ENOENT) {
            saved = errno;
            err = ESP_FAIL;
        } else if (backup_exists && !S_ISREG(backup_status.st_mode)) {
            saved = EINVAL;
            err = ESP_FAIL;
        }
    }
    if (err == ESP_OK && backup_exists && !final_exists) {
        if (rename(backup, final) != 0) {
            saved = errno;
            err = ESP_FAIL;
        }
    } else if (err == ESP_OK && backup_exists &&
               unlink(backup) != 0) {
        saved = errno;
        err = ESP_FAIL;
    }
    if (err == ESP_OK && unlink(temporary) != 0 && errno != ENOENT) {
        saved = errno;
        err = ESP_FAIL;
    }
    if (out_errno != NULL) *out_errno = saved;
    return err;
}

esp_err_t rehab_scan_transfer_commit_latest_scene(
    const char *temporary, const char *final, int *out_errno)
{
    if (temporary == NULL || final == NULL) return ESP_ERR_INVALID_ARG;
    char backup[REHAB_SCAN_TRANSFER_PATH_MAX];
    int count = snprintf(backup, sizeof(backup), "%s.previous", final);
    if (count <= 0 || (size_t)count >= sizeof(backup)) {
        return ESP_ERR_INVALID_SIZE;
    }
    (void)unlink(backup);
    bool had_previous = false;
    struct stat status;
    if (stat(final, &status) == 0) {
        if (!S_ISREG(status.st_mode) || rename(final, backup) != 0) {
            if (out_errno != NULL) *out_errno = errno;
            return ESP_FAIL;
        }
        had_previous = true;
    } else if (errno != ENOENT) {
        if (out_errno != NULL) *out_errno = errno;
        return ESP_FAIL;
    }
    if (rename(temporary, final) != 0) {
        int saved = errno;
        if (had_previous) (void)rename(backup, final);
        if (out_errno != NULL) *out_errno = saved;
        return ESP_FAIL;
    }
    FILE *check = fopen(final, "rb");
    if (check == NULL) {
        int saved = errno;
        (void)unlink(final);
        if (had_previous) (void)rename(backup, final);
        if (out_errno != NULL) *out_errno = saved;
        return ESP_FAIL;
    }
    int first = fgetc(check);
    int close_result = fclose(check);
    if (first == EOF || close_result != 0) {
        int saved = errno != 0 ? errno : EIO;
        (void)unlink(final);
        if (had_previous) (void)rename(backup, final);
        if (out_errno != NULL) *out_errno = saved;
        return ESP_FAIL;
    }
    if (had_previous) (void)unlink(backup);
    if (out_errno != NULL) *out_errno = 0;
    return ESP_OK;
}
