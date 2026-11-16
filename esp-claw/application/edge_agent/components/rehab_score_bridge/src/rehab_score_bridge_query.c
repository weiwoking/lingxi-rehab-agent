/* SPDX-License-Identifier: Apache-2.0 */
#include "rehab_score_bridge_priv.h"

#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

typedef enum {
    SESSION_REF_LATEST = 0,
    SESSION_REF_FULL,
    SESSION_REF_TASK,
} session_ref_kind_t;

static esp_err_t query_failure(rehab_score_query_result_t *result,
                               rehab_score_query_error_t error,
                               esp_err_t err)
{
    result->error = error;
    return err;
}

static bool task_reference_valid(const char *value)
{
    if (!value || strlen(value) != 7 || memcmp(value, "TASK", 4) != 0) {
        return false;
    }
    return value[4] >= '0' && value[4] <= '9' &&
           value[5] >= '0' && value[5] <= '9' &&
           value[6] >= '0' && value[6] <= '9';
}

static bool candidate_is_directory(const char *session_id)
{
    char path[REHAB_SCORE_PATH_LEN];
    int written = snprintf(path, sizeof(path), "%s/%s", REHAB_SCORE_SESSION_ROOT, session_id);
    if (written < 0 || (size_t)written >= sizeof(path)) {
        return false;
    }
    struct stat info = {0};
    return stat(path, &info) == 0 && S_ISDIR(info.st_mode);
}

static esp_err_t resolve_session(const char *session_ref,
                                 rehab_score_session_t *out_session,
                                 rehab_score_query_result_t *out_result)
{
    session_ref_kind_t kind = SESSION_REF_LATEST;
    if (session_ref && session_ref[0]) {
        if (rehab_score_contract_session_id(session_ref)) {
            kind = SESSION_REF_FULL;
        } else if (task_reference_valid(session_ref)) {
            kind = SESSION_REF_TASK;
        } else {
            return query_failure(out_result,
                                 REHAB_SCORE_QUERY_ERROR_INVALID_SESSION_REF,
                                 ESP_ERR_INVALID_ARG);
        }
    }

    DIR *directory = opendir(REHAB_SCORE_SESSION_ROOT);
    if (!directory) {
        return query_failure(out_result,
                             REHAB_SCORE_QUERY_ERROR_STORAGE_NOT_READY,
                             ESP_ERR_INVALID_STATE);
    }
    rehab_score_session_t *candidate = calloc(1, sizeof(*candidate));
    if (!candidate) {
        closedir(directory);
        return query_failure(out_result,
                             REHAB_SCORE_QUERY_ERROR_INTERNAL,
                             ESP_ERR_NO_MEM);
    }

    size_t match_count = 0;
    int read_error = 0;
    while (true) {
        errno = 0;
        struct dirent *entry = readdir(directory);
        if (!entry) {
            read_error = errno;
            break;
        }
        if (!rehab_score_contract_session_id(entry->d_name)) {
            continue;
        }
        if (kind == SESSION_REF_FULL && strcmp(entry->d_name, session_ref) != 0) {
            continue;
        }
        if (kind == SESSION_REF_TASK && strcmp(entry->d_name + 9, session_ref) != 0) {
            continue;
        }
        if (!candidate_is_directory(entry->d_name) ||
                rehab_score_storage_load_session_metadata(entry->d_name, candidate) != ESP_OK) {
            continue;
        }
        match_count++;
        if (kind != SESSION_REF_LATEST || !out_session->session_id[0] ||
                strcmp(candidate->session_id, out_session->session_id) > 0) {
            *out_session = *candidate;
        }
        if (kind == SESSION_REF_TASK && match_count > 1) {
            break;
        }
    }
    free(candidate);
    closedir(directory);

    if (read_error != 0) {
        return query_failure(out_result,
                             REHAB_SCORE_QUERY_ERROR_STORAGE_NOT_READY,
                             ESP_ERR_INVALID_STATE);
    }
    if (kind == SESSION_REF_TASK && match_count > 1) {
        return query_failure(out_result,
                             REHAB_SCORE_QUERY_ERROR_SESSION_REF_AMBIGUOUS,
                             ESP_ERR_INVALID_STATE);
    }
    if (match_count == 0) {
        return query_failure(out_result,
                             REHAB_SCORE_QUERY_ERROR_SESSION_NOT_FOUND,
                             ESP_ERR_NOT_FOUND);
    }
    return ESP_OK;
}

const char *rehab_score_query_error_to_string(rehab_score_query_error_t error)
{
    switch (error) {
    case REHAB_SCORE_QUERY_ERROR_NONE: return "none";
    case REHAB_SCORE_QUERY_ERROR_INVALID_SESSION_REF: return "invalid_session_ref";
    case REHAB_SCORE_QUERY_ERROR_SESSION_NOT_FOUND: return "session_not_found";
    case REHAB_SCORE_QUERY_ERROR_SESSION_REF_AMBIGUOUS: return "session_ref_ambiguous";
    case REHAB_SCORE_QUERY_ERROR_STATUS_MISSING: return "score_status_missing";
    case REHAB_SCORE_QUERY_ERROR_STATUS_INVALID: return "score_status_invalid";
    case REHAB_SCORE_QUERY_ERROR_RESULT_MISSING: return "score_result_missing";
    case REHAB_SCORE_QUERY_ERROR_RESULT_INVALID: return "score_result_invalid";
    case REHAB_SCORE_QUERY_ERROR_STORAGE_NOT_READY: return "storage_not_ready";
    case REHAB_SCORE_QUERY_ERROR_INTERNAL: return "internal_error";
    default: return "internal_error";
    }
}

esp_err_t rehab_score_bridge_query(const char *session_ref,
                                   rehab_score_query_result_t *out_result)
{
    if (!out_result) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_result, 0, sizeof(*out_result));
    out_result->error = REHAB_SCORE_QUERY_ERROR_INTERNAL;
    if (!rehab_score_bridge_is_initialized()) {
        return query_failure(out_result,
                             REHAB_SCORE_QUERY_ERROR_STORAGE_NOT_READY,
                             ESP_ERR_INVALID_STATE);
    }

    rehab_score_session_t *session = calloc(1, sizeof(*session));
    if (!session) {
        return query_failure(out_result,
                             REHAB_SCORE_QUERY_ERROR_INTERNAL,
                             ESP_ERR_NO_MEM);
    }
    esp_err_t err = resolve_session(session_ref, session, out_result);
    if (err != ESP_OK) {
        free(session);
        return err;
    }
    strlcpy(out_result->session_id, session->session_id, sizeof(out_result->session_id));
    strlcpy(out_result->task_name, session->task.task_name, sizeof(out_result->task_name));

    rehab_score_status_t *status = calloc(1, sizeof(*status));
    if (!status) {
        free(session);
        return query_failure(out_result,
                             REHAB_SCORE_QUERY_ERROR_INTERNAL,
                             ESP_ERR_NO_MEM);
    }
    bool status_exists = false;
    err = rehab_score_storage_load_status(session->session_path, status, &status_exists);
    if (err != ESP_OK) {
        free(status);
        free(session);
        return query_failure(out_result,
                             REHAB_SCORE_QUERY_ERROR_STATUS_INVALID,
                             ESP_ERR_INVALID_RESPONSE);
    }
    if (!status_exists) {
        free(status);
        free(session);
        return query_failure(out_result,
                             REHAB_SCORE_QUERY_ERROR_STATUS_MISSING,
                             ESP_ERR_NOT_FOUND);
    }

    out_result->state = status->state;
    out_result->attempt = status->attempt;
    strlcpy(out_result->pc_job_id, status->pc_job_id, sizeof(out_result->pc_job_id));
    strlcpy(out_result->updated_at, status->updated_at, sizeof(out_result->updated_at));
    strlcpy(out_result->last_error, status->last_error, sizeof(out_result->last_error));
    if (status->state != REHAB_SCORE_STATE_SUCCEEDED) {
        out_result->error = REHAB_SCORE_QUERY_ERROR_NONE;
        free(status);
        free(session);
        return ESP_OK;
    }
    if (!status->pc_job_id[0]) {
        free(status);
        free(session);
        return query_failure(out_result,
                             REHAB_SCORE_QUERY_ERROR_RESULT_INVALID,
                             ESP_ERR_INVALID_RESPONSE);
    }

    err = rehab_score_storage_read_result(session, status->pc_job_id, out_result);
    free(status);
    free(session);
    if (err == ESP_ERR_NOT_FOUND) {
        return query_failure(out_result,
                             REHAB_SCORE_QUERY_ERROR_RESULT_MISSING,
                             err);
    }
    if (err != ESP_OK) {
        return query_failure(out_result,
                             REHAB_SCORE_QUERY_ERROR_RESULT_INVALID,
                             ESP_ERR_INVALID_RESPONSE);
    }
    out_result->error = REHAB_SCORE_QUERY_ERROR_NONE;
    return ESP_OK;
}
