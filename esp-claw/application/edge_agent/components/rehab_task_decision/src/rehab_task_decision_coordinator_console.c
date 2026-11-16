// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_priv.h"

#include <stdio.h>
#include <string.h>

#include "esp_console.h"

static int coordinator_console_handler(void *context, int argc, char **argv)
{
    rehab_task_decision_handle_t handle = context;
    if (argc == 2 && strcmp(argv[1], "start") == 0) {
        rehab_task_decision_start_result_t result = {0};
        esp_err_t err = rehab_task_decision_start_confirmed(
            handle, REHAB_TASK_DECISION_START_SOURCE_CONSOLE, &result,
            NULL);
        if (err != ESP_OK) {
            printf("outcome=rejected generation=0 state=UNKNOWN error=%s\n",
                   esp_err_to_name(err));
            return 1;
        }
        printf("outcome=%s generation=%u state=%s last_error=%s\n",
               rehab_task_decision_start_outcome_name(result.outcome),
               (unsigned)result.generation,
               rehab_task_decision_coordinator_state_name(result.state),
               result.last_error);
        return result.outcome == REHAB_TASK_DECISION_START_OUTCOME_REJECTED
            ? 1 : 0;
    }
    if (argc == 2 && strcmp(argv[1], "status") == 0) {
        rehab_task_decision_coordinator_status_t status = {0};
        esp_err_t err = rehab_task_decision_coordinator_get_status(
            handle, &status);
        if (err != ESP_OK) {
            printf("error=%s\n", esp_err_to_name(err));
            return 1;
        }
        printf("state=%s active=%d generation=%u decision_id=%s "
               "scan_id=%s scene_sha256=%s task_id=%s task_name=%s "
               "score=%.1f target=%s last_error=%s\n",
               rehab_task_decision_coordinator_state_name(status.state),
               status.active, (unsigned)status.generation,
               status.decision_id, status.source_scan_id,
               status.source_scene_sha256, status.task_id,
               status.task_name, status.score,
               status.target_available
                   ? status.target_object_id : "none",
               status.last_error);
        return 0;
    }
    printf("error=usage usage='rehab_task_decision <start|status>'\n");
    return 1;
}

esp_err_t rehab_task_decision_coordinator_console_register(
    rehab_task_decision_handle_t handle)
{
    if (handle == NULL || handle->coordinator_console_registered) {
        return ESP_ERR_INVALID_ARG;
    }
    const esp_console_cmd_t command = {
        .command = "rehab_task_decision",
        .help = "Run or inspect the shared formal task-decision coordinator",
        .hint = "<start|status>",
        .func_w_context = coordinator_console_handler,
        .context = handle,
    };
    esp_err_t err = esp_console_cmd_register(&command);
    if (err == ESP_OK) {
        handle->coordinator_console_registered = true;
    }
    return err;
}
