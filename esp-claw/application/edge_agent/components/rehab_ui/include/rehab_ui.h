/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#pragma once

#include "esp_err.h"
#include "rehab_environment_scan.h"
#include "rehab_person_follow.h"
#include "rehab_task_decision.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t rehab_ui_register_lua_module(void);
esp_err_t rehab_ui_start(
    rehab_person_follow_handle_t person_follow,
    rehab_environment_scan_handle_t environment_scan,
    rehab_task_decision_handle_t task_decision);

#ifdef __cplusplus
}
#endif
