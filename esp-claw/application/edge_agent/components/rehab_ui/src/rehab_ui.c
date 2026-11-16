/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "rehab_ui.h"

#include <stdbool.h>

#include "cap_lua.h"
#include "claw_paths.h"
#include "esp_log.h"
#include "sdkconfig.h"

#define REHAB_UI_START_SCRIPT "ui/rehab/start_rehab_ui.lua"
#define REHAB_UI_JOB_NAME "rehab_ui_phase1"

static const char *TAG = "rehab_ui";
static bool s_start_submitted;

esp_err_t rehab_ui_person_follow_lua_register(void);
void rehab_ui_person_follow_lua_bind(
    rehab_person_follow_handle_t person_follow);
esp_err_t rehab_ui_environment_scan_lua_register(void);
void rehab_ui_environment_scan_lua_bind(
    rehab_environment_scan_handle_t environment_scan);
esp_err_t rehab_ui_task_decision_lua_register(void);
void rehab_ui_task_decision_lua_bind(
    rehab_task_decision_handle_t task_decision);

esp_err_t rehab_ui_register_lua_module(void)
{
#if CONFIG_APP_CLAW_CAP_LUA
    esp_err_t err = rehab_ui_person_follow_lua_register();
    if (err != ESP_OK) {
        return err;
    }
    err = rehab_ui_environment_scan_lua_register();
    if (err != ESP_OK) {
        return err;
    }
    return rehab_ui_task_decision_lua_register();
#else
    return ESP_ERR_NOT_SUPPORTED;
#endif
}

esp_err_t rehab_ui_start(
    rehab_person_follow_handle_t person_follow,
    rehab_environment_scan_handle_t environment_scan,
    rehab_task_decision_handle_t task_decision)
{
#if CONFIG_APP_CLAW_CAP_LUA
    static char script_path[160];
    static char output[512];

    if (s_start_submitted) {
        ESP_LOGI(TAG, "REHAB_UI_START_SUBMITTED status=already_submitted name=%s",
                 REHAB_UI_JOB_NAME);
        return ESP_OK;
    }

    rehab_ui_person_follow_lua_bind(person_follow);
    rehab_ui_environment_scan_lua_bind(environment_scan);
    rehab_ui_task_decision_lua_bind(task_decision);

    esp_err_t err = claw_paths_join(CLAW_PATH_SYSTEM,
                                    REHAB_UI_START_SCRIPT,
                                    script_path,
                                    sizeof(script_path));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "REHAB_UI_START_FAILED reason=script_path err=%s",
                 esp_err_to_name(err));
        return err;
    }

    output[0] = '\0';
    err = cap_lua_run_script_async(script_path,
                                   NULL,
                                   0,
                                   REHAB_UI_JOB_NAME,
                                   REHAB_UI_JOB_NAME,
                                   false,
                                   output,
                                   sizeof(output));
    if (err != ESP_OK) {
        ESP_LOGE(TAG,
                 "REHAB_UI_START_FAILED reason=async_submit err=%s output=%s",
                 esp_err_to_name(err),
                 output[0] ? output : "(empty)");
        return err;
    }

    s_start_submitted = true;
    ESP_LOGI(TAG, "REHAB_UI_START_SUBMITTED path=%s output=%s",
             script_path,
             output[0] ? output : "(empty)");
    return ESP_OK;
#else
    ESP_LOGW(TAG, "REHAB_UI_START_FAILED reason=lua_capability_disabled");
    return ESP_ERR_NOT_SUPPORTED;
#endif
}
