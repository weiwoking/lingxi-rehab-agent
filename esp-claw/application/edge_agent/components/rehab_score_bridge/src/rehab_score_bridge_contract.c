/* SPDX-License-Identifier: Apache-2.0 */
#include "rehab_score_bridge_priv.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"

static const char *const TASK_FIELDS[] = {
    "task_name", "csv_file", "start_time", "end_time",
    "start_log_seq", "end_log_seq", "end_reason",
};

static const char *const RESULT_FIELDS[] = {
    "schema_version", "session_id", "task_name", "task_number", "valid",
    "invalid_reason", "average_score", "max_score", "min_score",
    "repetition_count", "target_repetition_count", "consistency_score",
    "data_quality_level", "warnings", "compensation_detected",
    "compensation_action_count", "score_version", "model_version",
    "completed_at", "pc_job_id",
};

static bool exact_object_fields(const cJSON *root,
                                const char *const *fields,
                                size_t field_count)
{
    if (!cJSON_IsObject(root) || (size_t)cJSON_GetArraySize(root) != field_count) {
        return false;
    }
    for (const cJSON *child = root->child; child; child = child->next) {
        size_t matches = 0;
        for (size_t i = 0; i < field_count; ++i) {
            if (child->string && strcmp(child->string, fields[i]) == 0) {
                matches++;
            }
        }
        if (matches != 1) {
            return false;
        }
    }
    for (size_t i = 0; i < field_count; ++i) {
        size_t matches = 0;
        for (const cJSON *child = root->child; child; child = child->next) {
            if (child->string && strcmp(child->string, fields[i]) == 0) {
                matches++;
            }
        }
        if (matches != 1) {
            return false;
        }
    }
    return true;
}

static bool json_nonempty_string(const cJSON *item, size_t max_length)
{
    return cJSON_IsString(item) && item->valuestring && item->valuestring[0] &&
           strlen(item->valuestring) < max_length;
}

static bool machine_error_code(const cJSON *item)
{
    if (!json_nonempty_string(item, REHAB_SCORE_LAST_ERROR_LEN)) {
        return false;
    }
    for (const unsigned char *cursor = (const unsigned char *)item->valuestring;
            *cursor; ++cursor) {
        if (!((*cursor >= 'a' && *cursor <= 'z') ||
                (*cursor >= '0' && *cursor <= '9') || *cursor == '_')) {
            return false;
        }
    }
    return true;
}

static bool finalized_end_reason(const cJSON *item)
{
    return cJSON_IsString(item) && item->valuestring &&
           (strcmp(item->valuestring, "user_stop") == 0 ||
            strcmp(item->valuestring, "completed") == 0);
}

static bool json_uint(const cJSON *item, uint64_t *out)
{
    if (!cJSON_IsNumber(item) || item->valuedouble < 0 ||
            item->valuedouble > 9007199254740991.0) {
        return false;
    }
    uint64_t value = (uint64_t)item->valuedouble;
    if ((double)value != item->valuedouble) {
        return false;
    }
    if (out) {
        *out = value;
    }
    return true;
}

static bool iso8601_with_timezone(const cJSON *item)
{
    if (!cJSON_IsString(item) || !item->valuestring) {
        return false;
    }
    const char *value = item->valuestring;
    size_t length = strlen(value);
    if (length < 20 || length >= REHAB_SCORE_TIMESTAMP_LEN ||
            value[4] != '-' || value[7] != '-' || value[10] != 'T' ||
            value[13] != ':' || value[16] != ':') {
        return false;
    }
    const size_t digits[] = {0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18};
    for (size_t i = 0; i < sizeof(digits) / sizeof(digits[0]); ++i) {
        if (!isdigit((unsigned char)value[digits[i]])) {
            return false;
        }
    }
    if (value[length - 1] == 'Z') {
        return true;
    }
    return length >= 25 &&
           (value[length - 6] == '+' || value[length - 6] == '-') &&
           isdigit((unsigned char)value[length - 5]) &&
           isdigit((unsigned char)value[length - 4]) &&
           value[length - 3] == ':' &&
           isdigit((unsigned char)value[length - 2]) &&
           isdigit((unsigned char)value[length - 1]);
}

bool rehab_score_contract_session_id(const char *value)
{
    if (!value || strlen(value) != 16) {
        return false;
    }
    for (size_t i = 0; i < 8; ++i) {
        if (!isdigit((unsigned char)value[i])) {
            return false;
        }
    }
    if (memcmp(value + 8, "_TASK", 5) != 0) {
        return false;
    }
    return isdigit((unsigned char)value[13]) &&
           isdigit((unsigned char)value[14]) &&
           isdigit((unsigned char)value[15]);
}

bool rehab_score_contract_csv_file(const char *value)
{
    if (!value || strlen(value) != 22 || memcmp(value, "DATA_SEQ", 8) != 0 ||
            strcmp(value + 18, ".csv") != 0) {
        return false;
    }
    for (size_t i = 8; i < 18; ++i) {
        if (!isdigit((unsigned char)value[i])) {
            return false;
        }
    }
    return true;
}

static bool parse_port(const char *value, size_t length)
{
    if (length == 0 || length > 5) {
        return false;
    }
    unsigned port = 0;
    for (size_t i = 0; i < length; ++i) {
        if (!isdigit((unsigned char)value[i])) {
            return false;
        }
        port = port * 10U + (unsigned)(value[i] - '0');
    }
    return port >= 1U && port <= 65535U;
}

static bool parse_ipv4(const char *value, size_t length)
{
    unsigned octets = 0;
    size_t start = 0;
    for (size_t i = 0; i <= length; ++i) {
        if (i != length && value[i] != '.') {
            continue;
        }
        size_t digits = i - start;
        if (digits == 0 || digits > 3) {
            return false;
        }
        unsigned octet = 0;
        for (size_t j = start; j < i; ++j) {
            if (!isdigit((unsigned char)value[j])) {
                return false;
            }
            octet = octet * 10U + (unsigned)(value[j] - '0');
        }
        if (octet > 255U) {
            return false;
        }
        octets++;
        start = i + 1U;
    }
    return octets == 4;
}

bool rehab_score_contract_base_url(const char *value,
                                   char *normalized,
                                   size_t normalized_size)
{
    if (!value || !normalized || normalized_size == 0) {
        return false;
    }
    size_t length = strlen(value);
    while (length > 0 && value[length - 1] == '/') {
        length--;
    }
    const char prefix[] = "http://";
    if (length <= sizeof(prefix) - 1U ||
            strncmp(value, prefix, sizeof(prefix) - 1U) != 0 ||
            length >= normalized_size) {
        return false;
    }
    const char *host = value + sizeof(prefix) - 1U;
    const char *colon = memchr(host, ':', length - (sizeof(prefix) - 1U));
    if (!colon || memchr(colon + 1, ':', (value + length) - (colon + 1)) ||
            memchr(host, '/', (size_t)((value + length) - host))) {
        return false;
    }
    if (!parse_ipv4(host, (size_t)(colon - host)) ||
            !parse_port(colon + 1, (size_t)((value + length) - (colon + 1)))) {
        return false;
    }
    memcpy(normalized, value, length);
    normalized[length] = '\0';
    return true;
}

const char *rehab_score_state_to_string(rehab_score_state_t state)
{
    switch (state) {
    case REHAB_SCORE_STATE_PENDING: return "pending";
    case REHAB_SCORE_STATE_UPLOADING: return "uploading";
    case REHAB_SCORE_STATE_ACCEPTED: return "accepted";
    case REHAB_SCORE_STATE_SCORING: return "scoring";
    case REHAB_SCORE_STATE_SUCCEEDED: return "succeeded";
    case REHAB_SCORE_STATE_FAILED: return "failed";
    default: return NULL;
    }
}

rehab_score_recovery_action_t rehab_score_contract_recovery_action(
    bool status_exists,
    rehab_score_state_t state)
{
    if (!status_exists) {
        return REHAB_SCORE_RECOVERY_CREATE_PENDING;
    }
    switch (state) {
    case REHAB_SCORE_STATE_PENDING:
        return REHAB_SCORE_RECOVERY_SUBMIT;
    case REHAB_SCORE_STATE_UPLOADING:
        return REHAB_SCORE_RECOVERY_RESET_PENDING;
    case REHAB_SCORE_STATE_ACCEPTED:
    case REHAB_SCORE_STATE_SCORING:
        return REHAB_SCORE_RECOVERY_POLL;
    case REHAB_SCORE_STATE_SUCCEEDED:
        return REHAB_SCORE_RECOVERY_VALIDATE_RESULT;
    case REHAB_SCORE_STATE_FAILED:
    default:
        return REHAB_SCORE_RECOVERY_HOLD_FAILED;
    }
}

static bool parse_state(const cJSON *item, rehab_score_state_t *out)
{
    if (!cJSON_IsString(item) || !item->valuestring || !out) {
        return false;
    }
    for (int state = REHAB_SCORE_STATE_PENDING; state <= REHAB_SCORE_STATE_FAILED; ++state) {
        const char *name = rehab_score_state_to_string((rehab_score_state_t)state);
        if (name && strcmp(item->valuestring, name) == 0) {
            *out = (rehab_score_state_t)state;
            return true;
        }
    }
    return false;
}

esp_err_t rehab_score_contract_parse_task(const char *json,
                                          rehab_score_task_t *out_task)
{
    if (!json || !out_task) {
        return ESP_ERR_INVALID_ARG;
    }
    cJSON *root = cJSON_ParseWithOpts(json, NULL, true);
    if (!exact_object_fields(root, TASK_FIELDS, sizeof(TASK_FIELDS) / sizeof(TASK_FIELDS[0]))) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    const cJSON *task_name = cJSON_GetObjectItemCaseSensitive(root, "task_name");
    const cJSON *csv_file = cJSON_GetObjectItemCaseSensitive(root, "csv_file");
    const cJSON *start_time = cJSON_GetObjectItemCaseSensitive(root, "start_time");
    const cJSON *end_time = cJSON_GetObjectItemCaseSensitive(root, "end_time");
    const cJSON *start_seq = cJSON_GetObjectItemCaseSensitive(root, "start_log_seq");
    const cJSON *end_seq = cJSON_GetObjectItemCaseSensitive(root, "end_log_seq");
    const cJSON *end_reason = cJSON_GetObjectItemCaseSensitive(root, "end_reason");
    uint64_t start = 0;
    uint64_t end = 0;
    bool valid = json_nonempty_string(task_name, sizeof(out_task->task_name)) &&
        json_nonempty_string(csv_file, sizeof(out_task->csv_file)) &&
        rehab_score_contract_csv_file(csv_file->valuestring) &&
        iso8601_with_timezone(start_time) &&
        iso8601_with_timezone(end_time) &&
        finalized_end_reason(end_reason) &&
        json_uint(start_seq, &start) && json_uint(end_seq, &end) && end >= start;
    if (!valid) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    memset(out_task, 0, sizeof(*out_task));
    strlcpy(out_task->task_name, task_name->valuestring, sizeof(out_task->task_name));
    strlcpy(out_task->csv_file, csv_file->valuestring, sizeof(out_task->csv_file));
    strlcpy(out_task->start_time, start_time->valuestring, sizeof(out_task->start_time));
    strlcpy(out_task->end_time, end_time->valuestring, sizeof(out_task->end_time));
    strlcpy(out_task->end_reason, end_reason->valuestring, sizeof(out_task->end_reason));
    out_task->start_log_seq = start;
    out_task->end_log_seq = end;
    cJSON_Delete(root);
    return ESP_OK;
}

esp_err_t rehab_score_contract_parse_status(const char *json,
                                            rehab_score_status_t *out_status)
{
    static const char *const fields[] = {
        "schema_version", "state", "attempt", "pc_job_id", "updated_at", "last_error",
    };
    if (!json || !out_status) {
        return ESP_ERR_INVALID_ARG;
    }
    cJSON *root = cJSON_ParseWithOpts(json, NULL, true);
    const cJSON *schema = cJSON_GetObjectItemCaseSensitive(root, "schema_version");
    const cJSON *state = cJSON_GetObjectItemCaseSensitive(root, "state");
    const cJSON *attempt = cJSON_GetObjectItemCaseSensitive(root, "attempt");
    const cJSON *job = cJSON_GetObjectItemCaseSensitive(root, "pc_job_id");
    const cJSON *updated = cJSON_GetObjectItemCaseSensitive(root, "updated_at");
    const cJSON *error = cJSON_GetObjectItemCaseSensitive(root, "last_error");
    uint64_t attempt_value = 0;
    rehab_score_state_t parsed_state;
    bool valid = exact_object_fields(root, fields, sizeof(fields) / sizeof(fields[0])) &&
        cJSON_IsString(schema) && strcmp(schema->valuestring, "rehab_score_status_v1") == 0 &&
        parse_state(state, &parsed_state) && json_uint(attempt, &attempt_value) &&
        attempt_value <= UINT32_MAX && iso8601_with_timezone(updated) &&
        (cJSON_IsNull(job) || (json_nonempty_string(job, REHAB_SCORE_JOB_ID_LEN) &&
                              rehab_score_contract_session_id(job->valuestring))) &&
        (cJSON_IsNull(error) || json_nonempty_string(error, REHAB_SCORE_LAST_ERROR_LEN));
    if (!valid) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    memset(out_status, 0, sizeof(*out_status));
    out_status->state = parsed_state;
    out_status->attempt = (uint32_t)attempt_value;
    if (cJSON_IsString(job)) {
        strlcpy(out_status->pc_job_id, job->valuestring, sizeof(out_status->pc_job_id));
    }
    strlcpy(out_status->updated_at, updated->valuestring, sizeof(out_status->updated_at));
    if (cJSON_IsString(error)) {
        strlcpy(out_status->last_error, error->valuestring, sizeof(out_status->last_error));
    }
    cJSON_Delete(root);
    return ESP_OK;
}

static esp_err_t parse_pc_job_common(const char *json,
                                     const char *session_id,
                                     bool creation,
                                     rehab_score_job_response_t *out)
{
    if (!json || !session_id || !out) {
        return ESP_ERR_INVALID_ARG;
    }
    cJSON *root = cJSON_ParseWithOpts(json, NULL, true);
    const cJSON *job = cJSON_GetObjectItemCaseSensitive(root, "job_id");
    const cJSON *session = cJSON_GetObjectItemCaseSensitive(root, "session_id");
    const cJSON *state = cJSON_GetObjectItemCaseSensitive(root, "state");
    const cJSON *upload = cJSON_GetObjectItemCaseSensitive(root, "upload_required");
    const cJSON *error = cJSON_GetObjectItemCaseSensitive(root, "error_code");
    rehab_score_state_t parsed_state;
    bool valid = cJSON_IsObject(root) && json_nonempty_string(job, REHAB_SCORE_JOB_ID_LEN) &&
        json_nonempty_string(session, REHAB_SCORE_SESSION_ID_LEN) &&
        strcmp(job->valuestring, session_id) == 0 &&
        strcmp(session->valuestring, session_id) == 0 && parse_state(state, &parsed_state) &&
        parsed_state != REHAB_SCORE_STATE_PENDING &&
        (!creation || cJSON_IsBool(upload)) &&
        (!error || cJSON_IsNull(error) || machine_error_code(error));
    if (!valid) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    memset(out, 0, sizeof(*out));
    strlcpy(out->job_id, job->valuestring, sizeof(out->job_id));
    out->state = parsed_state;
    out->upload_required = creation && cJSON_IsTrue(upload);
    if (cJSON_IsString(error)) {
        strlcpy(out->error_code, error->valuestring, sizeof(out->error_code));
    }
    cJSON_Delete(root);
    return ESP_OK;
}

esp_err_t rehab_score_contract_parse_job_response(const char *json,
                                                  const char *session_id,
                                                  rehab_score_job_response_t *out)
{
    return parse_pc_job_common(json, session_id, true, out);
}

esp_err_t rehab_score_contract_parse_job_status(const char *json,
                                                const char *session_id,
                                                rehab_score_job_response_t *out)
{
    return parse_pc_job_common(json, session_id, false, out);
}

esp_err_t rehab_score_contract_parse_error_code(
    const char *json,
    char error_code[REHAB_SCORE_LAST_ERROR_LEN])
{
    if (!json || !error_code) {
        return ESP_ERR_INVALID_ARG;
    }
    cJSON *root = cJSON_ParseWithOpts(json, NULL, true);
    const cJSON *error = cJSON_GetObjectItemCaseSensitive(root, "error_code");
    if (!cJSON_IsObject(root) || !machine_error_code(error)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    strlcpy(error_code, error->valuestring, REHAB_SCORE_LAST_ERROR_LEN);
    cJSON_Delete(root);
    return ESP_OK;
}

static bool score_or_null(const cJSON *item)
{
    return cJSON_IsNull(item) ||
           (cJSON_IsNumber(item) && item->valuedouble >= 0.0 &&
            item->valuedouble <= 100.0);
}

esp_err_t rehab_score_contract_validate_result(const char *json,
                                               size_t length,
                                               const char *session_id,
                                               const char *job_id,
                                               const char *task_name)
{
    if (!json || length == 0 || length > REHAB_SCORE_RESULT_MAX_BYTES ||
            !session_id || !job_id || !task_name) {
        return ESP_ERR_INVALID_ARG;
    }
    if (memchr(json, '\0', length)) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    cJSON *root = cJSON_ParseWithOpts(json, NULL, true);
    const cJSON *schema = cJSON_GetObjectItemCaseSensitive(root, "schema_version");
    const cJSON *session = cJSON_GetObjectItemCaseSensitive(root, "session_id");
    const cJSON *job = cJSON_GetObjectItemCaseSensitive(root, "pc_job_id");
    const cJSON *name = cJSON_GetObjectItemCaseSensitive(root, "task_name");
    const cJSON *number = cJSON_GetObjectItemCaseSensitive(root, "task_number");
    const cJSON *valid_field = cJSON_GetObjectItemCaseSensitive(root, "valid");
    const cJSON *invalid_reason = cJSON_GetObjectItemCaseSensitive(root, "invalid_reason");
    const cJSON *warnings = cJSON_GetObjectItemCaseSensitive(root, "warnings");
    const cJSON *compensation = cJSON_GetObjectItemCaseSensitive(root, "compensation_detected");
    const cJSON *average = cJSON_GetObjectItemCaseSensitive(root, "average_score");
    const cJSON *maximum = cJSON_GetObjectItemCaseSensitive(root, "max_score");
    const cJSON *minimum = cJSON_GetObjectItemCaseSensitive(root, "min_score");
    const cJSON *consistency = cJSON_GetObjectItemCaseSensitive(root, "consistency_score");
    uint64_t task_number_value = 0;
    uint64_t repetitions = 0;
    uint64_t target_repetitions = 0;
    uint64_t compensation_actions = 0;
    bool valid = exact_object_fields(root, RESULT_FIELDS, sizeof(RESULT_FIELDS) / sizeof(RESULT_FIELDS[0])) &&
        cJSON_IsString(schema) && strcmp(schema->valuestring, "rehab_score_result_v1") == 0 &&
        cJSON_IsString(session) && strcmp(session->valuestring, session_id) == 0 &&
        cJSON_IsString(job) && strcmp(job->valuestring, job_id) == 0 &&
        cJSON_IsString(name) && strcmp(name->valuestring, task_name) == 0 &&
        json_uint(number, &task_number_value) && task_number_value > 0 &&
        task_number_value <= UINT32_MAX &&
        cJSON_IsBool(valid_field) &&
        (cJSON_IsNull(invalid_reason) || cJSON_IsString(invalid_reason)) &&
        cJSON_IsArray(warnings) && cJSON_IsBool(compensation) &&
        score_or_null(average) && score_or_null(maximum) &&
        score_or_null(minimum) && score_or_null(consistency) &&
        json_uint(cJSON_GetObjectItemCaseSensitive(root, "repetition_count"), &repetitions) &&
        repetitions <= UINT32_MAX &&
        json_uint(cJSON_GetObjectItemCaseSensitive(root, "target_repetition_count"),
                  &target_repetitions) && target_repetitions > 0 &&
        target_repetitions <= UINT32_MAX &&
        json_uint(cJSON_GetObjectItemCaseSensitive(root, "compensation_action_count"),
                  &compensation_actions) && compensation_actions <= repetitions &&
        (cJSON_IsTrue(compensation) == (compensation_actions > 0)) &&
        json_nonempty_string(cJSON_GetObjectItemCaseSensitive(root, "data_quality_level"), 48) &&
        json_nonempty_string(cJSON_GetObjectItemCaseSensitive(root, "score_version"), 48) &&
        json_nonempty_string(cJSON_GetObjectItemCaseSensitive(root, "model_version"), 96) &&
        iso8601_with_timezone(cJSON_GetObjectItemCaseSensitive(root, "completed_at"));
    if (valid && cJSON_IsTrue(valid_field)) {
        valid = cJSON_IsNull(invalid_reason) && cJSON_IsNumber(average) &&
            cJSON_IsNumber(maximum) && cJSON_IsNumber(minimum) &&
            cJSON_IsNumber(consistency) &&
            minimum->valuedouble <= average->valuedouble &&
            average->valuedouble <= maximum->valuedouble;
    } else if (valid) {
        valid = json_nonempty_string(invalid_reason, REHAB_SCORE_LAST_ERROR_LEN);
    }
    if (valid) {
        for (const cJSON *warning = warnings->child; warning; warning = warning->next) {
            if (!cJSON_IsString(warning)) {
                valid = false;
                break;
            }
        }
    }
    cJSON_Delete(root);
    return valid ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}

static void copy_utf8_summary(char *destination,
                              size_t destination_size,
                              const char *source)
{
    if (!destination || destination_size == 0) {
        return;
    }
    destination[0] = '\0';
    if (!source) {
        return;
    }
    size_t length = strlen(source);
    if (length < destination_size) {
        memcpy(destination, source, length + 1U);
        return;
    }
    if (destination_size <= sizeof("...")) {
        return;
    }
    size_t prefix = destination_size - sizeof("...");
    while (prefix > 0 && ((unsigned char)source[prefix] & 0xc0U) == 0x80U) {
        prefix--;
    }
    memcpy(destination, source, prefix);
    memcpy(destination + prefix, "...", sizeof("..."));
}

static bool append_warning(char *summary,
                           size_t summary_size,
                           const char *warning)
{
    size_t used = strlen(summary);
    const char separator[] = "; ";
    size_t separator_length = used > 0 ? sizeof(separator) - 1U : 0;
    size_t warning_length = strlen(warning);
    if (used + separator_length + warning_length < summary_size) {
        if (separator_length > 0) {
            memcpy(summary + used, separator, separator_length);
            used += separator_length;
        }
        memcpy(summary + used, warning, warning_length + 1U);
        return true;
    }

    if (used + separator_length + sizeof("...") > summary_size) {
        return false;
    }
    if (separator_length > 0) {
        memcpy(summary + used, separator, separator_length);
        used += separator_length;
    }
    size_t available = summary_size - used;
    size_t prefix = available - sizeof("...");
    while (prefix > 0 && ((unsigned char)warning[prefix] & 0xc0U) == 0x80U) {
        prefix--;
    }
    memcpy(summary + used, warning, prefix);
    memcpy(summary + used + prefix, "...", sizeof("..."));
    return false;
}

static void parse_optional_score(const cJSON *item,
                                 bool *out_available,
                                 double *out_value)
{
    *out_available = cJSON_IsNumber(item);
    *out_value = *out_available ? item->valuedouble : 0.0;
}

esp_err_t rehab_score_contract_parse_result(const char *json,
                                            size_t length,
                                            const char *session_id,
                                            const char *job_id,
                                            const char *task_name,
                                            rehab_score_query_result_t *out_result)
{
    if (!out_result) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = rehab_score_contract_validate_result(json,
                                                         length,
                                                         session_id,
                                                         job_id,
                                                         task_name);
    if (err != ESP_OK) {
        return err;
    }

    cJSON *root = cJSON_ParseWithOpts(json, NULL, true);
    if (!root) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    const cJSON *repetitions = cJSON_GetObjectItemCaseSensitive(root, "repetition_count");
    const cJSON *target = cJSON_GetObjectItemCaseSensitive(root, "target_repetition_count");
    const cJSON *actions = cJSON_GetObjectItemCaseSensitive(root, "compensation_action_count");
    uint64_t repetition_value = 0;
    uint64_t target_value = 0;
    uint64_t action_value = 0;
    if (!json_uint(repetitions, &repetition_value) || repetition_value > UINT32_MAX ||
            !json_uint(target, &target_value) || target_value > UINT32_MAX ||
            !json_uint(actions, &action_value) || action_value > UINT32_MAX) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }

    const cJSON *name = cJSON_GetObjectItemCaseSensitive(root, "task_name");
    const cJSON *valid = cJSON_GetObjectItemCaseSensitive(root, "valid");
    const cJSON *invalid_reason = cJSON_GetObjectItemCaseSensitive(root, "invalid_reason");
    const cJSON *warnings = cJSON_GetObjectItemCaseSensitive(root, "warnings");
    const cJSON *compensation = cJSON_GetObjectItemCaseSensitive(root, "compensation_detected");
    strlcpy(out_result->task_name, name->valuestring, sizeof(out_result->task_name));
    out_result->valid = cJSON_IsTrue(valid);
    if (cJSON_IsString(invalid_reason)) {
        copy_utf8_summary(out_result->invalid_reason,
                          sizeof(out_result->invalid_reason),
                          invalid_reason->valuestring);
    }
    parse_optional_score(cJSON_GetObjectItemCaseSensitive(root, "average_score"),
                         &out_result->average_score_available,
                         &out_result->average_score);
    parse_optional_score(cJSON_GetObjectItemCaseSensitive(root, "max_score"),
                         &out_result->max_score_available,
                         &out_result->max_score);
    parse_optional_score(cJSON_GetObjectItemCaseSensitive(root, "min_score"),
                         &out_result->min_score_available,
                         &out_result->min_score);
    parse_optional_score(cJSON_GetObjectItemCaseSensitive(root, "consistency_score"),
                         &out_result->consistency_score_available,
                         &out_result->consistency_score);
    out_result->repetition_count = (uint32_t)repetition_value;
    out_result->target_repetition_count = (uint32_t)target_value;
    copy_utf8_summary(
        out_result->data_quality_level,
        sizeof(out_result->data_quality_level),
        cJSON_GetObjectItemCaseSensitive(root, "data_quality_level")->valuestring);
    out_result->warning_count = (uint32_t)cJSON_GetArraySize(warnings);
    out_result->warnings_summary[0] = '\0';
    for (const cJSON *warning = warnings->child; warning; warning = warning->next) {
        if (!append_warning(out_result->warnings_summary,
                            sizeof(out_result->warnings_summary),
                            warning->valuestring)) {
            out_result->warnings_truncated = true;
            break;
        }
    }
    out_result->compensation_detected = cJSON_IsTrue(compensation);
    out_result->compensation_action_count = (uint32_t)action_value;
    copy_utf8_summary(out_result->score_version,
                      sizeof(out_result->score_version),
                      cJSON_GetObjectItemCaseSensitive(root, "score_version")->valuestring);
    copy_utf8_summary(out_result->model_version,
                      sizeof(out_result->model_version),
                      cJSON_GetObjectItemCaseSensitive(root, "model_version")->valuestring);
    strlcpy(out_result->completed_at,
            cJSON_GetObjectItemCaseSensitive(root, "completed_at")->valuestring,
            sizeof(out_result->completed_at));
    out_result->result_available = true;
    cJSON_Delete(root);
    return ESP_OK;
}

esp_err_t rehab_score_contract_validate_health(const char *json,
                                               size_t length,
                                               bool *out_ready)
{
    if (!json || length == 0 || length > REHAB_SCORE_RESPONSE_MAX_BYTES ||
            !out_ready || memchr(json, '\0', length)) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_ready = false;
    cJSON *root = cJSON_ParseWithOpts(json, NULL, true);
    const cJSON *service = cJSON_GetObjectItemCaseSensitive(root, "service");
    const cJSON *status = cJSON_GetObjectItemCaseSensitive(root, "status");
    const cJSON *version = cJSON_GetObjectItemCaseSensitive(root, "service_version");
    bool valid = cJSON_IsObject(root) &&
        cJSON_IsString(service) && service->valuestring &&
        strcmp(service->valuestring, "rehab_score_service") == 0 &&
        cJSON_IsString(status) && status->valuestring && status->valuestring[0] &&
        cJSON_IsString(version) && version->valuestring && version->valuestring[0];
    if (valid) {
        *out_ready = strcmp(status->valuestring, "ready") == 0;
    }
    cJSON_Delete(root);
    return valid ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}
