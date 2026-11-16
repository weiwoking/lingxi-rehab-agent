/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "rehab_task_decision.h"

#ifdef __cplusplus
extern "C" {
#endif

#define REHAB_SYSTEM_LAST_ERROR_LEN   64
#define REHAB_SYSTEM_STORAGE_BASE_LEN 32

typedef enum {
    REHAB_DEMO_MODE_MOCK = 0,
    REHAB_DEMO_MODE_REAL,
} rehab_demo_mode_t;

typedef enum {
    REHAB_SYSTEM_STATE_IDLE = 0,
} rehab_system_state_t;

typedef struct {
    bool initialized;
    rehab_demo_mode_t mode;
    rehab_system_state_t state;
    uint32_t generation;
    bool has_last_error;
    esp_err_t last_error_code;
    char last_error[REHAB_SYSTEM_LAST_ERROR_LEN];
    char storage_base[REHAB_SYSTEM_STORAGE_BASE_LEN];
} rehab_system_snapshot_t;

esp_err_t rehab_system_init(const char *storage_base,
                            rehab_task_decision_handle_t task_decision);
esp_err_t rehab_system_get_snapshot(rehab_system_snapshot_t *out_snapshot);
esp_err_t rehab_system_get_task_decision(
    rehab_task_decision_handle_t *out_task_decision);
esp_err_t rehab_system_set_demo_mode(rehab_demo_mode_t requested_mode,
                                     bool *out_changed,
                                     rehab_system_snapshot_t *out_snapshot);
esp_err_t rehab_system_reset_runtime(rehab_system_snapshot_t *out_snapshot);

/** Component-internal registration entry point used only by rehab_system_init(). */
esp_err_t rehab_capabilities_register_group(void);

#ifdef __cplusplus
}
#endif
