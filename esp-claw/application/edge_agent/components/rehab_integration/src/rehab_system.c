/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "rehab_system.h"

#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "rehab_fatigue.h"
#include "rehab_score_bridge.h"
#include "rehab_sensor_recording.h"

static const char *TAG = "rehab_system";

typedef struct {
    SemaphoreHandle_t mutex;
    bool init_attempted;
    rehab_task_decision_handle_t task_decision;
    rehab_system_snapshot_t snapshot;
} rehab_system_runtime_t;

static rehab_system_runtime_t s_rehab;

static void rehab_fatigue_score_succeeded(const char *session_id,
                                          double average_score,
                                          void *user_ctx)
{
    (void)user_ctx;
    esp_err_t err = rehab_fatigue_on_score_succeeded(session_id, average_score);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "FATIGUE_UPDATE_FAILED session=%.16s err=%s",
                 session_id ? session_id : "", esp_err_to_name(err));
    }
}

static esp_err_t rehab_system_lock(void)
{
    if (!s_rehab.mutex) {
        return ESP_ERR_INVALID_STATE;
    }
    return xSemaphoreTake(s_rehab.mutex, portMAX_DELAY) == pdTRUE ? ESP_OK : ESP_ERR_TIMEOUT;
}

static void rehab_system_unlock(void)
{
    xSemaphoreGive(s_rehab.mutex);
}

static void rehab_system_set_error_locked(esp_err_t err)
{
    s_rehab.snapshot.has_last_error = true;
    s_rehab.snapshot.last_error_code = err;
    strlcpy(s_rehab.snapshot.last_error,
            esp_err_to_name(err),
            sizeof(s_rehab.snapshot.last_error));
}

esp_err_t rehab_system_init(const char *storage_base,
                            rehab_task_decision_handle_t task_decision)
{
    esp_err_t err;

    if (s_rehab.init_attempted) {
        ESP_LOGW(TAG, "rehab init rejected: already attempted");
        return ESP_ERR_INVALID_STATE;
    }
    s_rehab.init_attempted = true;

    ESP_LOGI(TAG, "rehab init begin");
    if (!storage_base || !storage_base[0]) {
        ESP_LOGE(TAG, "rehab init failed stage=validate err=%s",
                 esp_err_to_name(ESP_ERR_INVALID_ARG));
        return ESP_ERR_INVALID_ARG;
    }
    if (strlen(storage_base) >= sizeof(s_rehab.snapshot.storage_base)) {
        ESP_LOGE(TAG, "rehab init failed stage=storage_path err=%s",
                 esp_err_to_name(ESP_ERR_INVALID_SIZE));
        return ESP_ERR_INVALID_SIZE;
    }

    s_rehab.snapshot.mode = REHAB_DEMO_MODE_MOCK;
    s_rehab.snapshot.state = REHAB_SYSTEM_STATE_IDLE;
    s_rehab.snapshot.generation = 1;
    s_rehab.snapshot.last_error_code = ESP_OK;
    s_rehab.task_decision = task_decision;
    strlcpy(s_rehab.snapshot.storage_base,
            storage_base,
            sizeof(s_rehab.snapshot.storage_base));

    s_rehab.mutex = xSemaphoreCreateMutex();
    if (!s_rehab.mutex) {
        ESP_LOGE(TAG, "rehab init failed stage=mutex err=%s",
                 esp_err_to_name(ESP_ERR_NO_MEM));
        return ESP_ERR_NO_MEM;
    }

    err = rehab_sensor_recording_init(storage_base);
    if (err != ESP_OK) {
        if (rehab_system_lock() == ESP_OK) {
            rehab_system_set_error_locked(err);
            rehab_system_unlock();
        }
        ESP_LOGE(TAG, "rehab init failed stage=sensor_recording err=%s",
                 esp_err_to_name(err));
        return err;
    }

    err = rehab_fatigue_init();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "fatigue unavailable; rehab continues: %s",
                 esp_err_to_name(err));
    }

#if CONFIG_REHAB_SCORE_BRIDGE_ENABLED
    err = rehab_score_bridge_set_success_observer(
        rehab_fatigue_score_succeeded, NULL);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "score success observer unavailable; rehab continues: %s",
                 esp_err_to_name(err));
    }
    err = rehab_score_bridge_init();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "score bridge unavailable; rehab continues: %s",
                 esp_err_to_name(err));
    }
#endif

    err = rehab_capabilities_register_group();
    if (err != ESP_OK) {
        if (rehab_system_lock() == ESP_OK) {
            rehab_system_set_error_locked(err);
            rehab_system_unlock();
        }
        ESP_LOGE(TAG, "rehab init failed stage=register err=%s", esp_err_to_name(err));
        return err;
    }

    err = rehab_system_lock();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "rehab init failed stage=state err=%s", esp_err_to_name(err));
        return err;
    }
    s_rehab.snapshot.initialized = true;
    rehab_system_unlock();

    ESP_LOGI(TAG, "rehab init success mode=mock state=idle generation=1");
    return ESP_OK;
}

esp_err_t rehab_system_get_task_decision(
    rehab_task_decision_handle_t *out_task_decision)
{
    if (out_task_decision == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_task_decision = NULL;

    esp_err_t err = rehab_system_lock();
    if (err != ESP_OK) {
        return err;
    }
    if (!s_rehab.snapshot.initialized || s_rehab.task_decision == NULL) {
        rehab_system_unlock();
        return ESP_ERR_INVALID_STATE;
    }
    *out_task_decision = s_rehab.task_decision;
    rehab_system_unlock();
    return ESP_OK;
}

esp_err_t rehab_system_get_snapshot(rehab_system_snapshot_t *out_snapshot)
{
    esp_err_t err;

    if (!out_snapshot) {
        return ESP_ERR_INVALID_ARG;
    }

    err = rehab_system_lock();
    if (err != ESP_OK) {
        return err;
    }
    *out_snapshot = s_rehab.snapshot;
    rehab_system_unlock();
    return ESP_OK;
}

esp_err_t rehab_system_set_demo_mode(rehab_demo_mode_t requested_mode,
                                     bool *out_changed,
                                     rehab_system_snapshot_t *out_snapshot)
{
    esp_err_t err;

    if ((requested_mode != REHAB_DEMO_MODE_MOCK &&
            requested_mode != REHAB_DEMO_MODE_REAL) ||
            !out_changed || !out_snapshot) {
        return ESP_ERR_INVALID_ARG;
    }

    err = rehab_system_lock();
    if (err != ESP_OK) {
        return err;
    }

    *out_changed = false;
    if (s_rehab.snapshot.state != REHAB_SYSTEM_STATE_IDLE) {
        *out_snapshot = s_rehab.snapshot;
        rehab_system_unlock();
        return ESP_ERR_INVALID_STATE;
    }

    if (s_rehab.snapshot.mode != requested_mode) {
        s_rehab.snapshot.mode = requested_mode;
        s_rehab.snapshot.generation++;
        *out_changed = true;
    }
    *out_snapshot = s_rehab.snapshot;
    rehab_system_unlock();
    return ESP_OK;
}

esp_err_t rehab_system_reset_runtime(rehab_system_snapshot_t *out_snapshot)
{
    esp_err_t err;

    if (!out_snapshot) {
        return ESP_ERR_INVALID_ARG;
    }

    err = rehab_system_lock();
    if (err != ESP_OK) {
        return err;
    }

    s_rehab.snapshot.state = REHAB_SYSTEM_STATE_IDLE;
    s_rehab.snapshot.has_last_error = false;
    s_rehab.snapshot.last_error_code = ESP_OK;
    s_rehab.snapshot.last_error[0] = '\0';
    s_rehab.snapshot.generation++;
    *out_snapshot = s_rehab.snapshot;

    rehab_system_unlock();
    return ESP_OK;
}
