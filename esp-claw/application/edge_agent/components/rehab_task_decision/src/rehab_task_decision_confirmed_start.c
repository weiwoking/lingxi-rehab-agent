// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision.h"

#include <inttypes.h>
#include <stdlib.h>
#include <string.h>

#include "esp_log.h"

static const char *TAG = "rehab_decision_start";

/* Deliberately private to this component; public callers use the facade. */
esp_err_t rehab_task_decision_coordinator_start(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_start_source_t source,
    uint32_t *out_generation);
esp_err_t rehab_task_decision_coordinator_start_root(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_start_source_t source,
    uint32_t *out_generation);

const char *rehab_task_decision_start_source_name(
    rehab_task_decision_start_source_t source)
{
    switch (source) {
    case REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI: return "local_ui";
    case REHAB_TASK_DECISION_START_SOURCE_CONSOLE: return "console";
    case REHAB_TASK_DECISION_START_SOURCE_WECHAT: return "wechat";
    case REHAB_TASK_DECISION_START_SOURCE_AGENT:
    case REHAB_TASK_DECISION_START_SOURCE_UNKNOWN:
    default: return "unknown";
    }
}

const char *rehab_task_decision_start_outcome_name(
    rehab_task_decision_start_outcome_t outcome)
{
    switch (outcome) {
    case REHAB_TASK_DECISION_START_OUTCOME_STARTED: return "started";
    case REHAB_TASK_DECISION_START_OUTCOME_ATTACHED_BUSY:
        return "attached_busy";
    case REHAB_TASK_DECISION_START_OUTCOME_REJECTED: return "rejected";
    case REHAB_TASK_DECISION_START_OUTCOME_TERMINAL_FAILURE:
        return "terminal_failure";
    default: return "unknown";
    }
}

static bool source_valid(rehab_task_decision_start_source_t source)
{
    return source == REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI ||
        source == REHAB_TASK_DECISION_START_SOURCE_AGENT ||
        source == REHAB_TASK_DECISION_START_SOURCE_CONSOLE ||
        source == REHAB_TASK_DECISION_START_SOURCE_WECHAT ||
        source == REHAB_TASK_DECISION_START_SOURCE_UNKNOWN;
}

static rehab_task_decision_start_source_t entry_source_for(
    rehab_task_decision_start_source_t source)
{
    if (source == REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI ||
        source == REHAB_TASK_DECISION_START_SOURCE_WECHAT ||
        source == REHAB_TASK_DECISION_START_SOURCE_CONSOLE) {
        return source;
    }
    return REHAB_TASK_DECISION_START_SOURCE_UNKNOWN;
}

static bool terminal_failure_state(
    rehab_task_decision_coordinator_state_t state)
{
    return state == REHAB_TASK_DECISION_COORDINATOR_FAILED ||
        state == REHAB_TASK_DECISION_COORDINATOR_CANCELLED;
}

esp_err_t rehab_task_decision_start_confirmed(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_start_source_t source,
    rehab_task_decision_start_result_t *out_result,
    rehab_task_decision_coordinator_status_t *out_status)
{
    if (handle == NULL || out_result == NULL || !source_valid(source)) {
        return ESP_ERR_INVALID_ARG;
    }

    memset(out_result, 0, sizeof(*out_result));
    out_result->source = source;

    rehab_task_decision_coordinator_status_t *status = out_status;
    bool status_owned = false;
    if (status == NULL) {
        status = calloc(1U, sizeof(*status));
        if (status == NULL) {
            return ESP_ERR_NO_MEM;
        }
        status_owned = true;
    } else {
        memset(status, 0, sizeof(*status));
    }

    uint32_t created_generation = 0;
    esp_err_t start_err = source == REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI
        ? rehab_task_decision_coordinator_start_root(
              handle, REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI,
              &created_generation)
        : rehab_task_decision_coordinator_start(
              handle, entry_source_for(source), &created_generation);
    esp_err_t status_err = rehab_task_decision_coordinator_get_status(
        handle, status);
    if (status_err != ESP_OK) {
        strlcpy(out_result->last_error, esp_err_to_name(status_err),
                sizeof(out_result->last_error));
        ESP_LOGE(TAG,
                 "TASK_DECISION_CONFIRMED_START source=%s outcome=rejected "
                 "generation=0 state=UNKNOWN decision_id= error=%s",
                 rehab_task_decision_start_source_name(source),
                 out_result->last_error);
        if (status_owned) {
            free(status);
        }
        return status_err;
    }

    out_result->created_new_generation = created_generation != 0U;
    out_result->generation = status->generation;
    out_result->state = status->state;
    out_result->active = status->active;
    strlcpy(out_result->decision_id, status->decision_id,
            sizeof(out_result->decision_id));
    strlcpy(out_result->scan_id, status->source_scan_id,
            sizeof(out_result->scan_id));
    strlcpy(out_result->last_error, status->last_error,
            sizeof(out_result->last_error));

    if (out_result->created_new_generation &&
        (start_err != ESP_OK || terminal_failure_state(status->state))) {
        out_result->outcome =
            REHAB_TASK_DECISION_START_OUTCOME_TERMINAL_FAILURE;
    } else if (start_err == ESP_OK) {
        out_result->outcome = REHAB_TASK_DECISION_START_OUTCOME_STARTED;
    } else if (status->active) {
        out_result->outcome =
            REHAB_TASK_DECISION_START_OUTCOME_ATTACHED_BUSY;
    } else {
        out_result->outcome = REHAB_TASK_DECISION_START_OUTCOME_REJECTED;
        if (out_result->last_error[0] == '\0' ||
            strcmp(out_result->last_error, "none") == 0) {
            strlcpy(out_result->last_error, esp_err_to_name(start_err),
                    sizeof(out_result->last_error));
        }
    }

    ESP_LOGI(TAG,
             "TASK_DECISION_CONFIRMED_START source=%s outcome=%s "
             "generation=%" PRIu32 " state=%s decision_id=%s error=%s",
             rehab_task_decision_start_source_name(source),
             rehab_task_decision_start_outcome_name(out_result->outcome),
             out_result->generation,
             rehab_task_decision_coordinator_state_name(out_result->state),
             out_result->decision_id, out_result->last_error);
    if (status_owned) {
        free(status);
    }
    return ESP_OK;
}
