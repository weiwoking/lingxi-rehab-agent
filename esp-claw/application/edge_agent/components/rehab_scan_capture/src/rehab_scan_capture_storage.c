// SPDX-License-Identifier: Apache-2.0
#include "rehab_scan_capture_priv.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static esp_err_t format_path(char *out, size_t out_size, const char *format,
                             const char *base, const char *leaf)
{
    int written = snprintf(out, out_size, format, base, leaf);
    return written > 0 && written < (int)out_size ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

static esp_err_t ensure_directory(const char *path)
{
    struct stat status;
    if (mkdir(path, 0777) == 0) {
        return ESP_OK;
    }
    int saved_errno = errno;
    if (saved_errno == EEXIST && stat(path, &status) == 0 && S_ISDIR(status.st_mode)) {
        return ESP_OK;
    }
    errno = saved_errno;
    return ESP_FAIL;
}

static esp_err_t ensure_scan_root(const char *scan_root)
{
    char parent[REHAB_SCAN_CAPTURE_PATH_MAX];
    size_t length = strnlen(scan_root, sizeof(parent));
    if (length == 0 || length >= sizeof(parent)) {
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(parent, scan_root, length + 1);
    char *slash = strrchr(parent, '/');
    if (slash == NULL || slash == parent) {
        return ESP_ERR_INVALID_ARG;
    }
    *slash = '\0';
    esp_err_t err = ensure_directory(parent);
    return err == ESP_OK ? ensure_directory(scan_root) : err;
}

esp_err_t rehab_scan_capture_prepare_working(rehab_scan_capture_handle_t handle,
                                             char *working_path,
                                             size_t working_path_size,
                                             char *final_path,
                                             size_t final_path_size)
{
    if (handle == NULL || working_path == NULL || final_path == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = ensure_scan_root(handle->scan_root_path);
    if (err != ESP_OK) {
        return err;
    }
    err = format_path(final_path, final_path_size, "%s/%s",
                      handle->scan_root_path, handle->status.scan_id);
    if (err != ESP_OK) {
        return err;
    }
    char incomplete[REHAB_SCAN_CAPTURE_SCAN_ID_MAX + 16];
    int written = snprintf(incomplete, sizeof(incomplete), "%s.incomplete",
                           handle->status.scan_id);
    if (written <= 0 || written >= (int)sizeof(incomplete)) {
        return ESP_ERR_INVALID_SIZE;
    }
    err = format_path(working_path, working_path_size, "%s/%s",
                      handle->scan_root_path, incomplete);
    if (err != ESP_OK) {
        return err;
    }
    if (mkdir(working_path, 0777) != 0) {
        return errno == EEXIST ? ESP_ERR_INVALID_STATE : ESP_FAIL;
    }
    char frames_path[REHAB_SCAN_CAPTURE_PATH_MAX];
    err = format_path(frames_path, sizeof(frames_path), "%s/%s",
                      working_path, "frames");
    if (err != ESP_OK || mkdir(frames_path, 0777) != 0) {
        return err != ESP_OK ? err : ESP_FAIL;
    }
    return ESP_OK;
}

esp_err_t rehab_scan_capture_make_frame_dir(const char *working_path,
                                            uint32_t sequence,
                                            char *frame_path,
                                            size_t frame_path_size)
{
    if (working_path == NULL || frame_path == NULL || sequence >= REHAB_SCAN_MAX_FRAMES) {
        return ESP_ERR_INVALID_ARG;
    }
    int written = snprintf(frame_path, frame_path_size, "%s/frames/frame_%06u",
                           working_path, (unsigned)sequence);
    if (written <= 0 || written >= (int)frame_path_size) {
        return ESP_ERR_INVALID_SIZE;
    }
    if (mkdir(frame_path, 0777) != 0) {
        return errno == EEXIST ? ESP_ERR_INVALID_STATE : ESP_FAIL;
    }
    return ESP_OK;
}

static esp_err_t write_new_atomic(const char *directory, const char *filename,
                                  const void *data, size_t length, int *out_errno)
{
    char target[REHAB_SCAN_CAPTURE_PATH_MAX];
    char temporary[REHAB_SCAN_CAPTURE_PATH_MAX];
    FILE *file = NULL;
    int fd = -1;
    int saved_errno = 0;
    esp_err_t result = format_path(target, sizeof(target), "%s/%s", directory, filename);
    if (result != ESP_OK) {
        return result;
    }
    char temp_leaf[64];
    int written = snprintf(temp_leaf, sizeof(temp_leaf), "%s.tmp", filename);
    if (written <= 0 || written >= (int)sizeof(temp_leaf)) {
        return ESP_ERR_INVALID_SIZE;
    }
    result = format_path(temporary, sizeof(temporary), "%s/%s", directory, temp_leaf);
    if (result != ESP_OK) {
        return result;
    }
    struct stat status;
    if (stat(target, &status) == 0) {
        saved_errno = EEXIST;
        result = ESP_ERR_INVALID_STATE;
        goto cleanup;
    } else if (errno != ENOENT) {
        saved_errno = errno;
        result = ESP_FAIL;
        goto cleanup;
    }

    fd = open(temporary, O_WRONLY | O_CREAT | O_EXCL, 0666);
    if (fd < 0) {
        saved_errno = errno;
        result = ESP_FAIL;
        goto cleanup;
    }
    file = fdopen(fd, "wb");
    if (file == NULL) {
        saved_errno = errno;
        close(fd);
        fd = -1;
        result = ESP_FAIL;
        goto cleanup;
    }
    fd = -1;
    size_t offset = 0;
    while (offset < length) {
        size_t count = fwrite((const uint8_t *)data + offset, 1, length - offset, file);
        if (count == 0) {
            saved_errno = errno != 0 ? errno : EIO;
            result = ESP_FAIL;
            goto cleanup;
        }
        offset += count;
    }
    if (fflush(file) != 0 || fsync(fileno(file)) != 0) {
        saved_errno = errno;
        result = ESP_FAIL;
        goto cleanup;
    }
    if (fclose(file) != 0) {
        file = NULL;
        saved_errno = errno;
        result = ESP_FAIL;
        goto cleanup;
    }
    file = NULL;
    if (stat(target, &status) == 0) {
        saved_errno = EEXIST;
        result = ESP_ERR_INVALID_STATE;
        goto cleanup;
    } else if (errno != ENOENT) {
        saved_errno = errno;
        result = ESP_FAIL;
        goto cleanup;
    }
    if (rename(temporary, target) != 0) {
        saved_errno = errno;
        result = ESP_FAIL;
        goto cleanup;
    }
    result = ESP_OK;

cleanup:
    if (file != NULL && fclose(file) != 0 && saved_errno == 0) {
        saved_errno = errno;
        result = ESP_FAIL;
    }
    if (result != ESP_OK) {
        (void)unlink(temporary);
    }
    if (out_errno != NULL) {
        *out_errno = saved_errno;
    }
    errno = saved_errno;
    return result;
}

esp_err_t rehab_scan_capture_write_binary_new_atomic(const char *directory,
                                                     const char *filename,
                                                     const void *data,
                                                     size_t length,
                                                     int *out_errno)
{
    if (directory == NULL || filename == NULL || data == NULL || length == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    return write_new_atomic(directory, filename, data, length, out_errno);
}

esp_err_t rehab_scan_capture_write_json_new_atomic(const char *directory,
                                                   const char *filename,
                                                   const cJSON *json,
                                                   int *out_errno)
{
    if (directory == NULL || filename == NULL || json == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    char *rendered = cJSON_PrintUnformatted(json);
    if (rendered == NULL) {
        return ESP_ERR_NO_MEM;
    }
    size_t length = strlen(rendered);
    esp_err_t err = write_new_atomic(directory, filename, rendered, length, out_errno);
    cJSON_free(rendered);
    return err;
}

esp_err_t rehab_scan_capture_commit_directory(const char *working_path,
                                              const char *final_path,
                                              int *out_errno)
{
    if (working_path == NULL || final_path == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    struct stat status;
    if (stat(final_path, &status) == 0) {
        if (out_errno != NULL) {
            *out_errno = EEXIST;
        }
        return ESP_ERR_INVALID_STATE;
    }
    if (errno != ENOENT) {
        if (out_errno != NULL) {
            *out_errno = errno;
        }
        return ESP_FAIL;
    }
    if (rename(working_path, final_path) != 0) {
        if (out_errno != NULL) {
            *out_errno = errno;
        }
        return ESP_FAIL;
    }
    if (out_errno != NULL) {
        *out_errno = 0;
    }
    return ESP_OK;
}
