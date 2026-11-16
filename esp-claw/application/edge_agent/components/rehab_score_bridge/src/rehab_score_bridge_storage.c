/* SPDX-License-Identifier: Apache-2.0 */
#include "rehab_score_bridge_priv.h"

#include <dirent.h>
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <ctype.h>

#include "cJSON.h"
#include "mbedtls/sha256.h"
#include "mbedtls/version.h"

static const char EXPECTED_CSV_HEADER[] =
    "log_seq,time_ms,output_rate_hz,flags,"
    "node1_rx,node1_tx_seq,node1_tx_gap,node1_age_ms,"
    "node2_rx,node2_tx_seq,node2_tx_gap,node2_age_ms,"
    "emg_valid,emg_latest_1,emg_latest_2,emg_latest_3,emg_latest_4,"
    "emg_rms_1,emg_rms_2,emg_rms_3,emg_rms_4,"
    "emg_mav_1,emg_mav_2,emg_mav_3,emg_mav_4,"
    "emg_peak_1,emg_peak_2,emg_peak_3,emg_peak_4,emg_n,"
    "imu1_valid,imu1_age_ms,imu1_roll,imu1_pitch,imu1_yaw,imu1_gx,imu1_gy,imu1_gz,imu1_ax,imu1_ay,imu1_az,imu1_lax,imu1_lay,imu1_laz,"
    "imu2_valid,imu2_age_ms,imu2_roll,imu2_pitch,imu2_yaw,imu2_gx,imu2_gy,imu2_gz,imu2_ax,imu2_ay,imu2_az,imu2_lax,imu2_lay,imu2_laz,"
    "imu3_valid,imu3_age_ms,imu3_roll,imu3_pitch,imu3_yaw,imu3_gx,imu3_gy,imu3_gz,imu3_ax,imu3_ay,imu3_az,imu3_lax,imu3_lay,imu3_laz,"
    "imu4_valid,imu4_age_ms,imu4_roll,imu4_pitch,imu4_yaw,imu4_gx,imu4_gy,imu4_gz,imu4_ax,imu4_ay,imu4_az,imu4_lax,imu4_lay,imu4_laz";

typedef esp_err_t (*file_validator_t)(const char *path, void *ctx);

static esp_err_t read_bounded_file(const char *path,
                                   size_t maximum,
                                   char **out,
                                   size_t *out_length)
{
    struct stat info = {0};
    if (!path || !out || !out_length) {
        return ESP_ERR_INVALID_ARG;
    }
    *out = NULL;
    *out_length = 0;
    if (stat(path, &info) != 0) {
        return errno == ENOENT ? ESP_ERR_NOT_FOUND : ESP_ERR_INVALID_SIZE;
    }
    if (!S_ISREG(info.st_mode) || info.st_size <= 0 ||
            (uint64_t)info.st_size > maximum) {
        return ESP_ERR_INVALID_SIZE;
    }
    char *bytes = malloc((size_t)info.st_size + 1U);
    if (!bytes) {
        return ESP_ERR_NO_MEM;
    }
    FILE *file = fopen(path, "rb");
    if (!file) {
        free(bytes);
        return ESP_FAIL;
    }
    size_t received = fread(bytes, 1, (size_t)info.st_size, file);
    bool valid = received == (size_t)info.st_size && !ferror(file) && fclose(file) == 0;
    if (!valid) {
        free(bytes);
        return ESP_FAIL;
    }
    bytes[received] = '\0';
    *out = bytes;
    *out_length = received;
    return ESP_OK;
}

esp_err_t rehab_score_storage_format_now(char out[REHAB_SCORE_TIMESTAMP_LEN])
{
    if (!out) {
        return ESP_ERR_INVALID_ARG;
    }
    time_t now = time(NULL);
    struct tm local = {0};
    char base[24] = {0};
    char zone[8] = {0};
    if (now <= 0 || !localtime_r(&now, &local) || local.tm_year + 1900 < 2024 ||
            strftime(base, sizeof(base), "%Y-%m-%dT%H:%M:%S", &local) == 0) {
        return ESP_ERR_INVALID_STATE;
    }
    if (strftime(zone, sizeof(zone), "%z", &local) == 5) {
        int written = snprintf(out, REHAB_SCORE_TIMESTAMP_LEN, "%s%c%c%c:%c%c",
                               base, zone[0], zone[1], zone[2], zone[3], zone[4]);
        return written > 0 && written < REHAB_SCORE_TIMESTAMP_LEN ? ESP_OK : ESP_ERR_INVALID_SIZE;
    }
    int written = snprintf(out, REHAB_SCORE_TIMESTAMP_LEN, "%sZ", base);
    return written > 0 && written < REHAB_SCORE_TIMESTAMP_LEN ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

static esp_err_t write_atomic(const char *final_path, const char *bytes, size_t length)
{
    char *temporary = NULL;
    char *backup = NULL;
    esp_err_t result = ESP_FAIL;
    size_t path_size = strlen(final_path) + 5U;
    temporary = malloc(path_size);
    backup = malloc(path_size);
    if (!temporary || !backup) {
        result = ESP_ERR_NO_MEM;
        goto cleanup;
    }
    snprintf(temporary, path_size, "%s.tmp", final_path);
    snprintf(backup, path_size, "%s.bak", final_path);
    (void)unlink(temporary);

    FILE *file = fopen(temporary, "wb");
    if (!file) {
        goto cleanup;
    }
    bool written = fwrite(bytes, 1, length, file) == length &&
                   fflush(file) == 0 && !ferror(file) && fsync(fileno(file)) == 0;
    if (fclose(file) != 0) {
        written = false;
    }
    if (!written) {
        goto cleanup;
    }
    if (rename(temporary, final_path) == 0) {
        (void)unlink(backup);
        result = ESP_OK;
        goto cleanup;
    }
    (void)unlink(backup);
    if (rename(final_path, backup) != 0 && errno != ENOENT) {
        goto cleanup;
    }
    if (rename(temporary, final_path) != 0) {
        (void)rename(backup, final_path);
        goto cleanup;
    }
    (void)unlink(backup);
    result = ESP_OK;

cleanup:
    if (temporary) {
        (void)unlink(temporary);
    }
    free(temporary);
    free(backup);
    return result;
}

static esp_err_t validate_status_path(const char *path, void *ctx)
{
    (void)ctx;
    char *json = NULL;
    size_t length = 0;
    esp_err_t err = read_bounded_file(path, REHAB_SCORE_STATUS_MAX_BYTES, &json, &length);
    if (err == ESP_OK) {
        rehab_score_status_t *status = calloc(1, sizeof(*status));
        if (!status) {
            free(json);
            return ESP_ERR_NO_MEM;
        }
        err = rehab_score_contract_parse_status(json, status);
        free(status);
    }
    free(json);
    return err;
}

typedef struct {
    const rehab_score_session_t *session;
    const char *job_id;
} result_validation_ctx_t;

static esp_err_t validate_result_path(const char *path, void *opaque)
{
    result_validation_ctx_t *ctx = opaque;
    char *json = NULL;
    size_t length = 0;
    esp_err_t err = read_bounded_file(path, REHAB_SCORE_RESULT_MAX_BYTES, &json, &length);
    if (err == ESP_OK) {
        err = rehab_score_contract_validate_result(json,
                                                   length,
                                                   ctx->session->session_id,
                                                   ctx->job_id,
                                                   ctx->session->task.task_name);
    }
    free(json);
    return err;
}

static esp_err_t recover_file(const char *final_path,
                              file_validator_t validator,
                              void *ctx)
{
    size_t path_size = strlen(final_path) + 5U;
    char *temporary = malloc(path_size);
    char *backup = malloc(path_size);
    if (!temporary || !backup) {
        free(temporary);
        free(backup);
        return ESP_ERR_NO_MEM;
    }
    snprintf(temporary, path_size, "%s.tmp", final_path);
    snprintf(backup, path_size, "%s.bak", final_path);
    if (validator(final_path, ctx) == ESP_OK) {
        (void)unlink(temporary);
        (void)unlink(backup);
        free(temporary);
        free(backup);
        return ESP_OK;
    }

    const char *chosen = NULL;
    if (validator(temporary, ctx) == ESP_OK) {
        chosen = temporary;
    } else if (validator(backup, ctx) == ESP_OK) {
        chosen = backup;
    }
    if (!chosen) {
        (void)unlink(temporary);
        (void)unlink(backup);
        free(temporary);
        free(backup);
        return ESP_ERR_NOT_FOUND;
    }

    if (chosen == temporary) {
        (void)unlink(backup);
        if (rename(final_path, backup) != 0 && errno != ENOENT) {
            free(temporary);
            free(backup);
            return ESP_FAIL;
        }
        if (rename(temporary, final_path) != 0) {
            (void)rename(backup, final_path);
            free(temporary);
            free(backup);
            return ESP_FAIL;
        }
    } else {
        (void)unlink(temporary);
        if (rename(final_path, temporary) != 0 && errno != ENOENT) {
            free(temporary);
            free(backup);
            return ESP_FAIL;
        }
        if (rename(backup, final_path) != 0) {
            (void)rename(temporary, final_path);
            free(temporary);
            free(backup);
            return ESP_FAIL;
        }
    }
    (void)unlink(temporary);
    (void)unlink(backup);
    free(temporary);
    free(backup);
    return ESP_OK;
}

static esp_err_t hash_csv(const char *path, char output[REHAB_SCORE_SHA256_HEX_LEN])
{
    uint8_t *buffer = malloc(REHAB_SCORE_CHUNK_BYTES);
    if (!buffer) {
        return ESP_ERR_NO_MEM;
    }
    FILE *file = fopen(path, "rb");
    if (!file) {
        free(buffer);
        return ESP_FAIL;
    }
    uint8_t digest[32] = {0};
    mbedtls_sha256_context sha;
    mbedtls_sha256_init(&sha);
#if MBEDTLS_VERSION_NUMBER >= 0x03000000
    int sha_err = mbedtls_sha256_starts(&sha, 0);
#else
    int sha_err = mbedtls_sha256_starts_ret(&sha, 0);
#endif
    while (sha_err == 0) {
        size_t received = fread(buffer, 1, REHAB_SCORE_CHUNK_BYTES, file);
        if (received > 0) {
#if MBEDTLS_VERSION_NUMBER >= 0x03000000
            sha_err = mbedtls_sha256_update(&sha, buffer, received);
#else
            sha_err = mbedtls_sha256_update_ret(&sha, buffer, received);
#endif
        }
        if (received < REHAB_SCORE_CHUNK_BYTES) {
            if (ferror(file)) {
                sha_err = -1;
            }
            break;
        }
    }
    if (sha_err == 0) {
#if MBEDTLS_VERSION_NUMBER >= 0x03000000
        sha_err = mbedtls_sha256_finish(&sha, digest);
#else
        sha_err = mbedtls_sha256_finish_ret(&sha, digest);
#endif
    }
    mbedtls_sha256_free(&sha);
    bool close_ok = fclose(file) == 0;
    free(buffer);
    if (sha_err != 0 || !close_ok) {
        return ESP_FAIL;
    }
    for (size_t i = 0; i < sizeof(digest); ++i) {
        snprintf(output + i * 2U, 3U, "%02x", digest[i]);
    }
    output[64] = '\0';
    return ESP_OK;
}

static bool parse_log_seq(const char *line, uint64_t *out)
{
    if (!line || !out || !isdigit((unsigned char)line[0])) {
        return false;
    }
    errno = 0;
    char *end = NULL;
    unsigned long long value = strtoull(line, &end, 10);
    if (errno != 0 || !end || *end != ',') {
        return false;
    }
    *out = (uint64_t)value;
    return true;
}

static esp_err_t validate_csv_rows(const rehab_score_session_t *session)
{
    char *line = malloc(REHAB_SCORE_CHUNK_BYTES);
    if (!line) {
        return ESP_ERR_NO_MEM;
    }
    FILE *file = fopen(session->csv_path, "rb");
    if (!file) {
        free(line);
        return ESP_FAIL;
    }
    esp_err_t err = ESP_ERR_INVALID_RESPONSE;
    if (!fgets(line, REHAB_SCORE_CHUNK_BYTES, file)) {
        goto cleanup;
    }
    size_t length = strlen(line);
    while (length > 0 && (line[length - 1] == '\n' || line[length - 1] == '\r')) {
        line[--length] = '\0';
    }
    if (strcmp(line, EXPECTED_CSV_HEADER) != 0) {
        goto cleanup;
    }
    uint64_t first = 0;
    uint64_t last = 0;
    bool seen = false;
    while (fgets(line, REHAB_SCORE_CHUNK_BYTES, file)) {
        if (!strchr(line, '\n') && !feof(file)) {
            goto cleanup;
        }
        uint64_t current = 0;
        if (!parse_log_seq(line, &current)) {
            goto cleanup;
        }
        if (!seen) {
            first = current;
            seen = true;
        }
        last = current;
    }
    if (ferror(file) || !seen || first != session->task.start_log_seq ||
            last != session->task.end_log_seq) {
        goto cleanup;
    }
    err = ESP_OK;

cleanup:
    fclose(file);
    free(line);
    return err;
}

esp_err_t rehab_score_storage_read_task_bytes(const rehab_score_session_t *session,
                                              char **out_bytes,
                                              size_t *out_length)
{
    if (!session) {
        return ESP_ERR_INVALID_ARG;
    }
    return read_bounded_file(session->task_path,
                             REHAB_SCORE_TASK_MAX_BYTES,
                             out_bytes,
                             out_length);
}

esp_err_t rehab_score_storage_load_session_metadata(const char *session_id,
                                                    rehab_score_session_t *out_session)
{
    if (!rehab_score_contract_session_id(session_id) || !out_session) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_session, 0, sizeof(*out_session));
    strlcpy(out_session->session_id, session_id, sizeof(out_session->session_id));
    int written = snprintf(out_session->session_path,
                           sizeof(out_session->session_path),
                           "%s/%s",
                           REHAB_SCORE_SESSION_ROOT,
                           session_id);
    if (written < 0 || (size_t)written >= sizeof(out_session->session_path)) {
        return ESP_ERR_INVALID_SIZE;
    }
    written = snprintf(out_session->task_path,
                       sizeof(out_session->task_path),
                       "%s/task.json",
                       out_session->session_path);
    if (written < 0 || (size_t)written >= sizeof(out_session->task_path)) {
        return ESP_ERR_INVALID_SIZE;
    }
    char *task_json = NULL;
    size_t task_length = 0;
    esp_err_t err = rehab_score_storage_read_task_bytes(out_session, &task_json, &task_length);
    if (err != ESP_OK) {
        return err;
    }
    err = rehab_score_contract_parse_task(task_json, &out_session->task);
    free(task_json);
    if (err != ESP_OK) {
        return err;
    }
    return ESP_OK;
}

esp_err_t rehab_score_storage_prepare_session(const char *session_id,
                                              rehab_score_session_t *out_session)
{
    if (!out_session) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = rehab_score_storage_load_session_metadata(session_id, out_session);
    if (err != ESP_OK) {
        return err;
    }
    int written;
    written = snprintf(out_session->csv_path,
                       sizeof(out_session->csv_path),
                       "%s/%s",
                       out_session->session_path,
                       out_session->task.csv_file);
    if (written < 0 || (size_t)written >= sizeof(out_session->csv_path)) {
        return ESP_ERR_INVALID_SIZE;
    }
    struct stat info = {0};
    if (stat(out_session->csv_path, &info) != 0 || !S_ISREG(info.st_mode) ||
            info.st_size < 1 || (uint64_t)info.st_size > REHAB_SCORE_CSV_MAX_BYTES) {
        return ESP_ERR_INVALID_SIZE;
    }
    out_session->csv_size = (uint64_t)info.st_size;
    err = validate_csv_rows(out_session);
    if (err != ESP_OK) {
        return err;
    }
    return hash_csv(out_session->csv_path, out_session->csv_sha256);
}

static esp_err_t status_path(const char *session_path, char *out, size_t out_size)
{
    int written = snprintf(out, out_size, "%s/score_status.json", session_path);
    return written > 0 && (size_t)written < out_size ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

esp_err_t rehab_score_storage_load_status(const char *session_path,
                                          rehab_score_status_t *out_status,
                                          bool *out_exists)
{
    if (!session_path || !out_status || !out_exists) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_exists = false;
    char path[REHAB_SCORE_PATH_LEN];
    esp_err_t err = status_path(session_path, path, sizeof(path));
    if (err != ESP_OK) {
        return err;
    }
    char *json = NULL;
    size_t length = 0;
    err = read_bounded_file(path, REHAB_SCORE_STATUS_MAX_BYTES, &json, &length);
    if (err == ESP_ERR_NOT_FOUND) {
        return ESP_OK;
    }
    if (err != ESP_OK) {
        return err;
    }
    err = rehab_score_contract_parse_status(json, out_status);
    free(json);
    if (err == ESP_OK) {
        *out_exists = true;
    }
    return err;
}

esp_err_t rehab_score_storage_write_status(const char *session_path,
                                           const rehab_score_status_t *status)
{
    if (!session_path || !status || !rehab_score_state_to_string(status->state)) {
        return ESP_ERR_INVALID_ARG;
    }
    cJSON *root = cJSON_CreateObject();
    if (!root) {
        return ESP_ERR_NO_MEM;
    }
    cJSON_AddStringToObject(root, "schema_version", "rehab_score_status_v1");
    cJSON_AddStringToObject(root, "state", rehab_score_state_to_string(status->state));
    cJSON_AddNumberToObject(root, "attempt", status->attempt);
    if (status->pc_job_id[0]) {
        cJSON_AddStringToObject(root, "pc_job_id", status->pc_job_id);
    } else {
        cJSON_AddNullToObject(root, "pc_job_id");
    }
    cJSON_AddStringToObject(root, "updated_at", status->updated_at);
    if (status->last_error[0]) {
        cJSON_AddStringToObject(root, "last_error", status->last_error);
    } else {
        cJSON_AddNullToObject(root, "last_error");
    }
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!json) {
        return ESP_ERR_NO_MEM;
    }
    char path[REHAB_SCORE_PATH_LEN];
    esp_err_t err = status_path(session_path, path, sizeof(path));
    if (err == ESP_OK) {
        err = write_atomic(path, json, strlen(json));
    }
    free(json);
    return err;
}

esp_err_t rehab_score_storage_write_result(const rehab_score_session_t *session,
                                           const char *json,
                                           size_t length,
                                           const char *job_id)
{
    if (!session || !json || !job_id) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = rehab_score_contract_validate_result(json,
                                                         length,
                                                         session->session_id,
                                                         job_id,
                                                         session->task.task_name);
    if (err != ESP_OK) {
        return err;
    }
    char path[REHAB_SCORE_PATH_LEN];
    int written = snprintf(path, sizeof(path), "%s/score_result.json", session->session_path);
    if (written < 0 || (size_t)written >= sizeof(path)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return write_atomic(path, json, length);
}

esp_err_t rehab_score_storage_validate_result_file(const rehab_score_session_t *session,
                                                   const char *job_id)
{
    if (!session || !job_id || !job_id[0]) {
        return ESP_ERR_INVALID_ARG;
    }
    char path[REHAB_SCORE_PATH_LEN];
    int written = snprintf(path, sizeof(path), "%s/score_result.json", session->session_path);
    if (written < 0 || (size_t)written >= sizeof(path)) {
        return ESP_ERR_INVALID_SIZE;
    }
    result_validation_ctx_t ctx = {.session = session, .job_id = job_id};
    return validate_result_path(path, &ctx);
}

esp_err_t rehab_score_storage_read_result(const rehab_score_session_t *session,
                                          const char *job_id,
                                          rehab_score_query_result_t *out_result)
{
    if (!session || !job_id || !job_id[0] || !out_result) {
        return ESP_ERR_INVALID_ARG;
    }
    char path[REHAB_SCORE_PATH_LEN];
    int written = snprintf(path, sizeof(path), "%s/score_result.json", session->session_path);
    if (written < 0 || (size_t)written >= sizeof(path)) {
        return ESP_ERR_INVALID_SIZE;
    }
    char *json = NULL;
    size_t length = 0;
    esp_err_t err = read_bounded_file(path, REHAB_SCORE_RESULT_MAX_BYTES, &json, &length);
    if (err == ESP_OK) {
        err = rehab_score_contract_parse_result(json,
                                                length,
                                                session->session_id,
                                                job_id,
                                                session->task.task_name,
                                                out_result);
    }
    free(json);
    return err;
}

esp_err_t rehab_score_storage_recover_atomic_files(const rehab_score_session_t *session)
{
    if (!session) {
        return ESP_ERR_INVALID_ARG;
    }
    char status[REHAB_SCORE_PATH_LEN];
    esp_err_t err = status_path(session->session_path, status, sizeof(status));
    if (err != ESP_OK) {
        return err;
    }
    esp_err_t status_recovery = recover_file(status, validate_status_path, NULL);
    rehab_score_status_t *parsed = calloc(1, sizeof(*parsed));
    if (!parsed) {
        return ESP_ERR_NO_MEM;
    }
    bool exists = false;
    err = rehab_score_storage_load_status(session->session_path, parsed, &exists);
    if (err != ESP_OK || !exists || !parsed->pc_job_id[0]) {
        free(parsed);
        return status_recovery == ESP_ERR_NOT_FOUND ? ESP_OK : status_recovery;
    }
    char result[REHAB_SCORE_PATH_LEN];
    int written = snprintf(result, sizeof(result), "%s/score_result.json", session->session_path);
    if (written < 0 || (size_t)written >= sizeof(result)) {
        free(parsed);
        return ESP_ERR_INVALID_SIZE;
    }
    result_validation_ctx_t ctx = {.session = session, .job_id = parsed->pc_job_id};
    esp_err_t result_recovery = recover_file(result, validate_result_path, &ctx);
    free(parsed);
    if (result_recovery == ESP_ERR_NOT_FOUND) {
        return status_recovery == ESP_ERR_NOT_FOUND ? ESP_OK : status_recovery;
    }
    return result_recovery;
}

esp_err_t rehab_score_storage_find_latest(char out_session_id[REHAB_SCORE_SESSION_ID_LEN])
{
    if (!out_session_id) {
        return ESP_ERR_INVALID_ARG;
    }
    out_session_id[0] = '\0';
    DIR *dir = opendir(REHAB_SCORE_SESSION_ROOT);
    if (!dir) {
        return errno == ENOENT ? ESP_ERR_NOT_FOUND : ESP_FAIL;
    }
    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        if (rehab_score_contract_session_id(entry->d_name) &&
                (!out_session_id[0] || strcmp(entry->d_name, out_session_id) > 0)) {
            strlcpy(out_session_id, entry->d_name, REHAB_SCORE_SESSION_ID_LEN);
        }
    }
    closedir(dir);
    return out_session_id[0] ? ESP_OK : ESP_ERR_NOT_FOUND;
}
