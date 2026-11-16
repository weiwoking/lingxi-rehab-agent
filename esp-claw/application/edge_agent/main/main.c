/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "app_claw.h"
#include "app_fs.h"
#include "rehab_system.h"
#include "claw_paths.h"
#include "rehab_ui.h"
#include <string.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdio.h>
#include "wifi_manager.h"
#include "time.h"
#include "nvs_flash.h"
#include "http_server.h"
#include "esp_log.h"
#include "esp_err.h"
#include "esp_check.h"
#include "esp_system.h"
#include "esp_board_manager_includes.h"
#include "captive_dns.h"
#include "cmd_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#if CONFIG_APP_CLAW_CAP_IM_WECHAT
#include "cap_im_wechat.h"
#endif
#include "app_config.h"
#include "rehab_follow.h"
#include "rehab_tof.h"
#include "rehab_capture.h"
#include "rehab_cloud_fusion.h"
#include "rehab_car.h"
#include "rehab_task_decision.h"
#include "rehab_target_follow.h"
#include "rehab_person_follow.h"
#include "rehab_scan_capture.h"
#include "rehab_scan_transfer.h"
#include "rehab_environment_scan.h"
#include "rehab_audio_output.h"
#include "rehab_business_audio.h"
#include "rehab_sensor_recording.h"
#include "dev_camera.h"

#define APP_ENABLE_MEM_LOG        (0)

static const char *TAG = "app";

static app_config_t *s_config;
static app_claw_config_t *s_claw_config;
static rehab_car_handle_t s_rehab_car;
static rehab_task_decision_handle_t s_task_decision;
static rehab_target_follow_handle_t s_target_follow;
static rehab_person_follow_handle_t s_person_follow;
static rehab_scan_capture_handle_t s_scan_capture;
static rehab_scan_transfer_handle_t s_scan_transfer;
static rehab_environment_scan_handle_t s_environment_scan;
static rehab_audio_output_handle_t s_audio_output;

static bool rehab_training_active_probe(void *ctx)
{
    (void)ctx;
    rehab_training_status_t *status = calloc(1, sizeof(*status));
    if (status == NULL) {
        return true;
    }
    bool active = rehab_training_get_status(status) == ESP_OK &&
        status->initialized && status->state != REHAB_TRAINING_STATE_IDLE;
    free(status);
    return active;
}

#if CONFIG_REHAB_PERSON_FOLLOW_ENABLED
static void person_follow_endpoint(char *output, size_t output_size)
{
    strlcpy(output, CONFIG_REHAB_PERSON_FOLLOW_PC_ENDPOINT, output_size);
    if (s_config == NULL || s_config->perception_fusion_url[0] == '\0') {
        return;
    }
    const char *path = strstr(s_config->perception_fusion_url, "/api/");
    if (path == NULL) {
        return;
    }
    size_t origin_length = (size_t)(path - s_config->perception_fusion_url);
    static const char endpoint_path[] = "/api/v1/person-follow/detect";
    if (origin_length + sizeof(endpoint_path) > output_size) {
        return;
    }
    memcpy(output, s_config->perception_fusion_url, origin_length);
    memcpy(output + origin_length, endpoint_path, sizeof(endpoint_path));
}
#endif

static void register_rehab_car_command(bool fusion_ready)
{
    rehab_car_config_t config = REHAB_CAR_CONFIG_DEFAULT();
    config.default_transport = REHAB_CAR_TRANSPORT_UART;
    esp_err_t err = rehab_car_create(&config, &s_rehab_car);
    if (err != ESP_OK) {
        ESP_LOGE(TAG,
                 "Car init failed default_transport=uart service_available=0 "
                 "fallback_udp=0 error=%s",
                 esp_err_to_name(err));
        return;
    }

    err = rehab_car_stop(s_rehab_car);
    if (err != ESP_OK) {
        ESP_LOGE(TAG,
                 "Car startup STOP write failed default_transport=uart "
                 "startup_stop=failed service_available=0 fallback_udp=0 "
                 "error=%s; cleanup may retry STOP",
                 esp_err_to_name(err));
        esp_err_t cleanup_err = rehab_car_delete(s_rehab_car);
        if (cleanup_err != ESP_OK) {
            ESP_LOGW(TAG, "Car failed-start cleanup result=%s",
                     esp_err_to_name(cleanup_err));
        }
        s_rehab_car = NULL;
        return;
    }
    ESP_LOGI(TAG,
             "Car startup default_transport=uart transport=uart "
             "uart_initialized=1 startup_stop=ok "
             "result=local_uart_write_succeeded_no_ack");

    err = rehab_car_register_console(s_rehab_car);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Car CLI registration failed (nonfatal): %s", esp_err_to_name(err));
        rehab_car_delete(s_rehab_car);
        s_rehab_car = NULL;
        return;
    }

    err = rehab_car_register_capabilities(s_rehab_car);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Car capability registration failed (nonfatal): %s", esp_err_to_name(err));
    }

    const rehab_task_decision_config_t decision_config = {
        .car = s_rehab_car,
        .fusion_ready = fusion_ready,
        .llm_api_key = s_config != NULL ? s_config->llm_api_key : NULL,
        .llm_backend_type =
            s_config != NULL ? s_config->llm_backend_type : NULL,
        .llm_model = s_config != NULL ? s_config->llm_model : NULL,
        .llm_base_url = s_config != NULL ? s_config->llm_base_url : NULL,
        .llm_auth_type = s_config != NULL ? s_config->llm_auth_type : NULL,
        .llm_max_tokens_field =
            s_config != NULL ? s_config->llm_max_tokens_field : NULL,
        .llm_timeout_ms =
            s_config != NULL ? (uint32_t)strtoul(
                s_config->llm_timeout_ms, NULL, 10) : 0U,
        .llm_max_tokens =
            s_config != NULL ? (uint32_t)strtoul(
                s_config->llm_max_tokens, NULL, 10) : 0U,
        .training_active = rehab_training_active_probe,
        .training_active_ctx = NULL,
    };
    err = rehab_task_decision_create(&decision_config, &s_task_decision);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Task-decision scan init failed (nonfatal): %s",
                 esp_err_to_name(err));
    } else {
        err = rehab_task_decision_coordinator_console_register(
            s_task_decision);
        if (err == ESP_OK) {
            err = rehab_task_decision_register_capabilities(
                s_task_decision);
        }
        if (err != ESP_OK) {
            ESP_LOGE(TAG,
                     "Task-decision capability registration failed (nonfatal): %s",
                     esp_err_to_name(err));
            (void)rehab_task_decision_delete(s_task_decision);
            s_task_decision = NULL;
        }
    }

    const rehab_target_follow_config_t follow_config = {
        .car = s_rehab_car,
    };
    err = rehab_target_follow_create(&follow_config, &s_target_follow);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Target-follow init failed (nonfatal): %s", esp_err_to_name(err));
        return;
    }
    err = rehab_target_follow_register_console(s_target_follow);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Target-follow CLI registration failed (nonfatal): %s",
                 esp_err_to_name(err));
        (void)rehab_target_follow_delete(s_target_follow);
        s_target_follow = NULL;
        return;
    }
    err = rehab_target_follow_register_capabilities(s_target_follow);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Target-follow capability registration failed (nonfatal): %s",
                 esp_err_to_name(err));
    }

#if CONFIG_REHAB_PERSON_FOLLOW_ENABLED
    char person_follow_url[REHAB_PERSON_FOLLOW_ENDPOINT_MAX];
    person_follow_endpoint(person_follow_url, sizeof(person_follow_url));
    const rehab_person_follow_config_t person_follow_config = {
        .car = s_rehab_car,
        .perception_provider = CONFIG_REHAB_PERSON_FOLLOW_PERCEPTION_REAL
            ? REHAB_PERSON_FOLLOW_PERCEPTION_REAL
            : REHAB_PERSON_FOLLOW_PERCEPTION_MOCK,
        .mock_mode = (rehab_person_follow_mock_mode_t)
            CONFIG_REHAB_PERSON_FOLLOW_MOCK_MODE,
        .pc_endpoint = person_follow_url,
        .http_timeout_ms = CONFIG_REHAB_PERSON_FOLLOW_HTTP_TIMEOUT_MS,
        .camera_timeout_ms = CONFIG_REHAB_PERSON_FOLLOW_CAMERA_TIMEOUT_MS,
        .loop_interval_ms = CONFIG_REHAB_PERSON_FOLLOW_LOOP_INTERVAL_MS,
        .max_detections = CONFIG_REHAB_PERSON_FOLLOW_MAX_DETECTIONS,
        .max_runtime_ms = CONFIG_REHAB_PERSON_FOLLOW_MAX_RUNTIME_MS,
        .max_consecutive_errors =
            CONFIG_REHAB_PERSON_FOLLOW_MAX_CONSECUTIVE_ERRORS,
        .action_enabled = true,
        .action_speed = 3U,
        .turn_hold_ms = REHAB_PERSON_FOLLOW_TARGET_TURN_INITIAL_MS,
        .forward_hold_ms = 500U,
        .fallback_post_action_settle_ms = 100U,
        .forward_post_action_settle_ms = 10U,
        .turn_post_action_settle_ms = 40U,
    };
    err = rehab_person_follow_create(&person_follow_config, &s_person_follow);
    if (err != ESP_OK) {
        ESP_LOGE(TAG,
                 "Person-follow init failed (nonfatal): %s",
                 esp_err_to_name(err));
        return;
    }
    err = rehab_person_follow_register_capabilities(s_person_follow);
    if (err != ESP_OK) {
        ESP_LOGE(TAG,
                 "Person-follow capability registration failed (nonfatal): %s",
                 esp_err_to_name(err));
        (void)rehab_person_follow_delete(s_person_follow);
        s_person_follow = NULL;
    } else {
        ESP_LOGI(TAG, "Person-follow registered provider=%s "
                 "endpoint=%s action_enabled=1",
                 rehab_person_follow_perception_provider_name(
                     person_follow_config.perception_provider),
                 person_follow_url);
    }
#endif
}

static esp_err_t app_allocate_runtime_state(void)
{
    if (!s_config) {
        s_config = calloc(1, sizeof(*s_config));
    }
    if (!s_claw_config) {
        s_claw_config = calloc(1, sizeof(*s_claw_config));
    }

    ESP_RETURN_ON_FALSE(s_config && s_claw_config, ESP_ERR_NO_MEM, TAG,
                        "Failed to allocate runtime state");

    return ESP_OK;
}

static void app_free_runtime_state(void)
{
    free(s_claw_config);
    s_claw_config = NULL;

    free(s_config);
    s_config = NULL;
}

static void log_wifi_startup_config(const app_config_t *config)
{
    ESP_LOGI(TAG,
             "Wi-Fi startup STA: ssid=%s pwd_len=%u",
             config->wifi_ssid[0] ? config->wifi_ssid : "(empty)",
             (unsigned)strlen(config->wifi_password));

    ESP_LOGI(TAG,
             "Wi-Fi startup AP: ssid=%s pwd_len=%u behavior=%s",
             config->ap_ssid[0] ? config->ap_ssid : "(auto:mac-suffix)",
             (unsigned)strlen(config->ap_password),
             config->ap_behavior[0] ? config->ap_behavior : "keep");
}

static void on_wifi_state_changed(bool connected, void *user_ctx)
{
    (void)user_ctx;

    wifi_manager_status_t status = {0};
    wifi_manager_get_status(&status);
    const char *ap_ssid = status.ap_active ? status.ap_ssid : NULL;

    ESP_LOGI(TAG, "Wi-Fi state: sta_connected=%d ap_active=%d mode=%s ap_ssid=%s",
             connected,
             status.ap_active,
             status.mode ? status.mode : "off",
             ap_ssid ? ap_ssid : "(none)");

    esp_err_t err = app_claw_set_network_status(connected, ap_ssid);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Failed to update network emote: %s", esp_err_to_name(err));
    }
}

static esp_err_t main_load_config(app_config_t *config)
{
    return app_config_load(config);
}

static esp_err_t main_save_config(const app_config_t *config)
{
    ESP_RETURN_ON_FALSE(config, ESP_ERR_INVALID_ARG, TAG, "config is NULL");
    ESP_RETURN_ON_ERROR(app_config_validate_wifi(config, NULL), TAG, "Invalid Wi-Fi config");

    return app_config_save(config);
}

static esp_err_t main_get_wifi_status(http_server_wifi_status_t *status)
{
    ESP_RETURN_ON_FALSE(status, ESP_ERR_INVALID_ARG, TAG, "status is NULL");

    wifi_manager_status_t wifi_status = {0};
    wifi_manager_get_status(&wifi_status);
    status->wifi_connected = wifi_status.sta_connected;
    status->ip = wifi_status.sta_ip;
    status->ap_active = wifi_status.ap_active;
    status->ap_ssid = wifi_status.ap_ssid;
    status->ap_ip = wifi_status.ap_ip;
    status->wifi_mode = wifi_status.mode;
    return ESP_OK;
}

static void main_restart_task(void *arg)
{
    (void)arg;
    vTaskDelay(pdMS_TO_TICKS(500));
    esp_restart();
}

static esp_err_t main_restart_device(void)
{
    BaseType_t ok = xTaskCreate(main_restart_task, "http_restart", 2048, NULL, 5, NULL);
    ESP_RETURN_ON_FALSE(ok == pdPASS, ESP_ERR_NO_MEM, TAG, "Failed to create restart task");
    return ESP_OK;
}

#if CONFIG_APP_CLAW_CAP_IM_WECHAT
static esp_err_t main_wechat_login_start(const char *account_id, bool force)
{
    return cap_im_wechat_qr_login_start(account_id, force);
}

static esp_err_t main_wechat_login_get_status(http_server_wechat_login_status_t *status)
{
    esp_err_t ret = ESP_OK;
    cap_im_wechat_qr_login_status_t *raw = NULL;

    ESP_RETURN_ON_FALSE(status, ESP_ERR_INVALID_ARG, TAG, "status is NULL");

    raw = calloc(1, sizeof(*raw));
    ESP_RETURN_ON_FALSE(raw, ESP_ERR_NO_MEM, TAG, "Failed to allocate login status");

    ESP_GOTO_ON_ERROR(cap_im_wechat_qr_login_get_status(raw), cleanup, TAG,
                      "Failed to query WeChat login status");

    memset(status, 0, sizeof(*status));
    status->active = raw->active;
    status->configured = raw->configured;
    status->completed = raw->completed;
    status->persisted = raw->persisted;
    strlcpy(status->session_key, raw->session_key, sizeof(status->session_key));
    strlcpy(status->status, raw->status, sizeof(status->status));
    strlcpy(status->message, raw->message, sizeof(status->message));
    strlcpy(status->qr_data_url, raw->qr_data_url, sizeof(status->qr_data_url));
    strlcpy(status->account_id, raw->account_id, sizeof(status->account_id));
    strlcpy(status->user_id, raw->user_id, sizeof(status->user_id));
    strlcpy(status->token, raw->token, sizeof(status->token));
    strlcpy(status->base_url, raw->base_url, sizeof(status->base_url));

cleanup:
    free(raw);
    return ret;
}

static esp_err_t main_wechat_login_cancel(void)
{
    return cap_im_wechat_qr_login_cancel();
}

static esp_err_t main_wechat_login_mark_persisted(void)
{
    return cap_im_wechat_qr_login_mark_persisted();
}
#endif

static void start_camera_provider(void)
{
    dev_camera_handle_t *camera_handle = NULL;
    esp_err_t err = esp_board_manager_get_device_handle(
        ESP_BOARD_DEVICE_NAME_CAMERA,
        (void **)&camera_handle);

    if (err != ESP_OK || camera_handle == NULL ||
        camera_handle->dev_path == NULL || camera_handle->dev_path[0] == '\0') {
        ESP_LOGE(TAG,
                 "Camera provider init skipped: camera handle/path unavailable: %s",
                 esp_err_to_name(err));
        return;
    }

    rehab_follow_camera_config_t config = REHAB_FOLLOW_CAMERA_CONFIG_DEFAULT();
    config.device_path = camera_handle->dev_path;
    config.enabled_lua_modules = s_config ? s_config->enabled_lua_modules : NULL;

    err = rehab_follow_camera_init(&config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG,
                 "Camera provider init nonfatal failure: %s",
                 esp_err_to_name(err));
        return;
    }

    err = rehab_follow_camera_start();
    if (err != ESP_OK) {
        ESP_LOGW(TAG,
                 "Camera provider start skipped/nonfatal failure: %s",
                 esp_err_to_name(err));
    }
}

static void start_tof_provider(void)
{
    rehab_tof_config_t config = REHAB_TOF_CONFIG_DEFAULT();
    esp_err_t err = rehab_tof_init(&config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ToF provider init nonfatal failure: %s", esp_err_to_name(err));
        return;
    }

    err = rehab_tof_start();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "ToF provider start nonfatal failure: %s", esp_err_to_name(err));
    }
}

static esp_err_t init_nvs(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    return err;
}

static esp_err_t init_timezone(const char *timezone)
{
    esp_err_t ret = ESP_OK;

    ESP_GOTO_ON_FALSE(timezone && timezone[0] != '\0', ESP_ERR_INVALID_ARG, tz_default, TAG,
                      "Timezone is empty.");
    ESP_GOTO_ON_FALSE(setenv("TZ", timezone, 1) == 0, ESP_FAIL, tz_default, TAG,
                      "Failed to set TZ env");
    tzset();
    ESP_LOGI(TAG, "Timezone set to %s", timezone);
    return ESP_OK;

tz_default:
    assert(setenv("TZ", "CST-8", 1) == 0);
    tzset();
    ESP_LOGI(TAG, "Timezone set to default: CST-8");
    return ret;
}

#if APP_ENABLE_MEM_LOG

static void print_task_stack_info(void)
{
#ifdef CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS
    static TaskStatus_t s_task_status_snapshot[24];
    UBaseType_t count = uxTaskGetSystemState(s_task_status_snapshot,
                                             sizeof(s_task_status_snapshot) / sizeof(s_task_status_snapshot[0]),
                                             NULL);

    for (UBaseType_t i = 0; i < count; i++) {
        ESP_LOGI(TAG,
                 "Task %s  %u",
                 s_task_status_snapshot[i].pcTaskName,
                 s_task_status_snapshot[i].usStackHighWaterMark);
    }
#endif
}

/* Periodic task: print internal free, minimum free, and PSRAM free every 20s */
static void memory_monitor_task(void *arg)
{
    (void)arg;
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(5000));
        size_t internal_free = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
        size_t internal_min = heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL);
        size_t psram_free = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
        ESP_LOGI(TAG, "Memory: internal_free=%u bytes, internal_min_free=%u bytes, psram_free=%u bytes",
                 (unsigned)internal_free, (unsigned)internal_min, (unsigned)psram_free);
        print_task_stack_info();
    }
}

#endif

void app_main(void)
{
    esp_log_level_set("esp-x509-crt-bundle", ESP_LOG_WARN);
    esp_log_level_set("http_reuse", ESP_LOG_WARN);

    ESP_LOGI(TAG, "Starting app");
    ESP_ERROR_CHECK(app_allocate_runtime_state());
    ESP_ERROR_CHECK(init_nvs());
    ESP_ERROR_CHECK(app_config_init());
    ESP_ERROR_CHECK(app_config_load(s_config));
    app_config_to_claw(s_config, s_claw_config);
    init_timezone(app_config_get_timezone(s_config)); // no need to check error
    ESP_ERROR_CHECK(esp_board_manager_init());
    start_camera_provider();
    start_tof_provider();
    esp_err_t capture_err = rehab_capture_init();
    if (capture_err != ESP_OK) {
        ESP_LOGE(TAG, "Capture coordinator init nonfatal failure: %s",
                 esp_err_to_name(capture_err));
    }
    esp_err_t cloud_fusion_err = rehab_cloud_fusion_init();
    if (cloud_fusion_err != ESP_OK) {
        ESP_LOGE(TAG, "Cloud fusion init nonfatal failure: %s",
                 esp_err_to_name(cloud_fusion_err));
    }
    ESP_ERROR_CHECK(app_claw_ui_start());
    ESP_ERROR_CHECK(app_fs_init());

    /* Publish the resolved storage roots so any component can compose paths
     * without knowing whether data lives on flash or an SD card. */
    ESP_ERROR_CHECK(claw_paths_set(CLAW_PATH_DATA, app_fs_storage_base_path()));
    ESP_ERROR_CHECK(claw_paths_set(CLAW_PATH_SYSTEM, app_fs_system_base_path()));

    ESP_ERROR_CHECK(wifi_manager_init());
    ESP_ERROR_CHECK(http_server_init(&(http_server_config_t) {
        .storage_base_path = app_fs_storage_base_path(),
        .services = {
            .load_config = main_load_config,
            .save_config = main_save_config,
            .get_wifi_status = main_get_wifi_status,
            .restart_device = main_restart_device,
#if CONFIG_APP_CLAW_CAP_IM_WECHAT
            .wechat_login_start = main_wechat_login_start,
            .wechat_login_get_status = main_wechat_login_get_status,
            .wechat_login_cancel = main_wechat_login_cancel,
            .wechat_login_mark_persisted = main_wechat_login_mark_persisted,
#endif
        },
    }));
    ESP_ERROR_CHECK(wifi_manager_register_state_callback(on_wifi_state_changed, NULL));

    log_wifi_startup_config(s_config);

    esp_err_t wifi_err = wifi_manager_start(&(wifi_manager_config_t) {
        .sta_ssid = s_config->wifi_ssid,
        .sta_password = s_config->wifi_password,
        .ap_ssid = s_config->ap_ssid[0] ? s_config->ap_ssid : NULL,
        .ap_password = s_config->ap_password[0] ? s_config->ap_password : NULL,
        .ap_behavior = s_config->ap_behavior,
    });
    if (wifi_err != ESP_OK) {
        ESP_LOGE(TAG, "Wi-Fi start failed: %s", esp_err_to_name(wifi_err));
    } else {
        ESP_ERROR_CHECK(http_server_start());
        if (captive_dns_start(&(captive_dns_config_t) {
                .ap_netif = wifi_manager_get_ap_netif(),
                .configure_dhcp_dns = true,
            }) != ESP_OK) {
            ESP_LOGW(TAG, "Captive DNS could not start, portal pop-up disabled");
        }

        if (s_config->wifi_ssid[0] != '\0') {
            esp_err_t wait_err = wifi_manager_wait_connected(30000);
            if (wait_err == ESP_OK) {
                wifi_manager_status_t status = {0};
                wifi_manager_get_status(&status);
                ESP_LOGI(TAG, "Wi-Fi STA ready: %s", status.sta_ip);
            } else if (wait_err == ESP_FAIL) {
                wifi_manager_status_t status = {0};
                wifi_manager_get_status(&status);
                ESP_LOGW(TAG,
                         "Wi-Fi STA failed after retries: mode=%s ap_active=%d ap_ip=%s",
                         status.mode ? status.mode : "off",
                         status.ap_active,
                         status.ap_ip ? status.ap_ip : "0.0.0.0");
            } else if (wait_err == ESP_ERR_TIMEOUT) {
                wifi_manager_status_t status = {0};
                wifi_manager_get_status(&status);
                ESP_LOGW(TAG,
                         "Wi-Fi STA wait timeout: mode=%s ap_active=%d sta_configured=%d",
                         status.mode ? status.mode : "off",
                         status.ap_active,
                         status.sta_configured);
            } else {
                ESP_LOGW(TAG, "Wi-Fi STA wait returned error: %s", esp_err_to_name(wait_err));
            }
        }

        wifi_manager_status_t status = {0};
        wifi_manager_get_status(&status);
        if (status.ap_active) {
            const char *portal_auth = s_config->ap_password[0] ? "wpa2" : "open";
            ESP_LOGW(TAG,
                     "*** Provisioning portal: SSID=\"%s\" (auth=%s) IP=%s URL=http://%s/ ***",
                     status.ap_ssid,
                     portal_auth,
                     status.ap_ip,
                     status.ap_ip);
        }
    }

    esp_err_t rehab_ui_module_err = rehab_ui_register_lua_module();
    if (rehab_ui_module_err != ESP_OK) {
        ESP_LOGE(TAG, "Rehab UI Lua module registration failed (nonfatal): %s",
                 esp_err_to_name(rehab_ui_module_err));
    }
    ESP_ERROR_CHECK(app_claw_start(s_claw_config));
    {
        const rehab_audio_output_config_t audio_config =
            REHAB_AUDIO_OUTPUT_CONFIG_DEFAULT();
        esp_err_t audio_err = rehab_audio_output_create(
            &audio_config, &s_audio_output);
        if (audio_err == ESP_OK) {
            audio_err = rehab_business_audio_register_output(s_audio_output);
        }
        if (audio_err == ESP_OK) {
            audio_err = rehab_audio_output_register_capabilities(
                s_audio_output);
        }
        if (audio_err == ESP_OK) {
            audio_err = rehab_audio_output_provision_router_rules();
        }
        if (audio_err != ESP_OK) {
            ESP_LOGE(TAG, "Rehab audio output init nonfatal failure: %s",
                     esp_err_to_name(audio_err));
        }
    }
    char scan_root[REHAB_SCAN_CAPTURE_PATH_MAX];
    esp_err_t scan_err = claw_paths_join(CLAW_PATH_DATA, "rehab/scans",
                                         scan_root, sizeof(scan_root));
    if (scan_err == ESP_OK) {
        const rehab_scan_capture_config_t scan_config =
            REHAB_SCAN_CAPTURE_CONFIG_DEFAULT(scan_root);
        scan_err = rehab_scan_capture_create(&scan_config, &s_scan_capture);
    }
    if (scan_err == ESP_OK) {
        scan_err = rehab_scan_capture_console_register(s_scan_capture);
    }
    if (scan_err != ESP_OK) {
        ESP_LOGE(TAG, "Scan capture init nonfatal failure: %s",
                 esp_err_to_name(scan_err));
        rehab_scan_capture_delete(s_scan_capture);
        s_scan_capture = NULL;
    }
    char transfer_scan_root[REHAB_SCAN_TRANSFER_PATH_MAX];
    char result_root[REHAB_SCAN_TRANSFER_PATH_MAX];
    char latest_scene_result[REHAB_SCAN_TRANSFER_PATH_MAX];
    esp_err_t transfer_err = claw_paths_join(CLAW_PATH_DATA, "rehab/scans",
                                              transfer_scan_root,
                                              sizeof(transfer_scan_root));
    if (transfer_err == ESP_OK) {
        transfer_err = claw_paths_join(CLAW_PATH_DATA, "rehab/scan_results",
                                       result_root, sizeof(result_root));
    }
    if (transfer_err == ESP_OK) {
        transfer_err = claw_paths_join(
            CLAW_PATH_DATA, "rehab/latest_successful_scene_result.json",
            latest_scene_result, sizeof(latest_scene_result));
    }
    if (transfer_err == ESP_OK) {
        rehab_scan_transfer_config_t transfer_config =
            REHAB_SCAN_TRANSFER_CONFIG_DEFAULT(transfer_scan_root, result_root);
        transfer_config.latest_scene_result_path = latest_scene_result;
        transfer_err = rehab_scan_transfer_create(&transfer_config, &s_scan_transfer);
    }
    if (transfer_err == ESP_OK) {
        transfer_err = rehab_scan_transfer_console_register(s_scan_transfer);
    }
    if (transfer_err != ESP_OK) {
        ESP_LOGE(TAG, "Scan transfer init nonfatal failure: %s",
                 esp_err_to_name(transfer_err));
        rehab_scan_transfer_delete(s_scan_transfer);
        s_scan_transfer = NULL;
    }
    /* claw_cap is initialized by app_claw_start(). Register the dynamic
     * high-level rehab groups. The retained legacy task-decision group is
     * registered for internal compatibility only and is never LLM-visible. */
    register_rehab_car_command(cloud_fusion_err == ESP_OK);
    if (s_rehab_car != NULL && s_scan_capture != NULL &&
        s_scan_transfer != NULL) {
        const rehab_environment_scan_config_t environment_config =
            REHAB_ENVIRONMENT_SCAN_CONFIG_DEFAULT(
                s_rehab_car, s_scan_capture, s_scan_transfer);
        esp_err_t environment_err = rehab_environment_scan_create(
            &environment_config, &s_environment_scan);
        if (environment_err == ESP_OK) {
            environment_err = rehab_environment_scan_console_register(
                s_environment_scan);
        }
        if (environment_err != ESP_OK) {
            ESP_LOGE(TAG, "Environment scan console init nonfatal failure: %s",
                     esp_err_to_name(environment_err));
            rehab_environment_scan_delete(s_environment_scan);
            s_environment_scan = NULL;
        } else {
            if (s_task_decision != NULL) {
                const esp_err_t bind_err =
                    rehab_task_decision_bind_environment_scan(
                        s_task_decision, s_environment_scan);
                if (bind_err != ESP_OK) {
                    ESP_LOGE(TAG,
                             "Task-decision environment bind nonfatal failure: %s",
                             esp_err_to_name(bind_err));
                } else {
                    ESP_LOGI(TAG, "TASK_DECISION_COORDINATOR_READY");
                }
            }
            const esp_err_t capability_err =
                rehab_environment_scan_register_capabilities(
                    s_environment_scan);
            if (capability_err != ESP_OK) {
                ESP_LOGE(TAG,
                         "Environment scan capability init nonfatal failure: %s",
                         esp_err_to_name(capability_err));
            }
        }
    } else {
        ESP_LOGE(TAG,
                 "Environment scan console unavailable: car/capture/transfer dependency missing");
    }
    esp_err_t rehab_err = rehab_system_init(app_fs_storage_base_path(),
                                            s_task_decision);
    if (rehab_err != ESP_OK) {
        ESP_LOGE(TAG, "Rehab init nonfatal failure: %s", esp_err_to_name(rehab_err));
    } else {
        esp_err_t ui_err = rehab_ui_start(
            s_person_follow, s_environment_scan, s_task_decision);
        if (ui_err != ESP_OK) {
            ESP_LOGE(TAG, "Rehab UI start nonfatal failure: %s",
                     esp_err_to_name(ui_err));
        }
    }
#if CONFIG_APP_CLAW_CAP_IM_LOCAL
    ESP_ERROR_CHECK(http_server_webim_bind_im());
#endif

    register_wifi_command();

#if APP_ENABLE_MEM_LOG
    /* Start memory monitor: print internal free, min free, PSRAM free every 20s */
    xTaskCreate(memory_monitor_task, "mem_mon", 4096, NULL, 1, NULL);
#endif

    app_free_runtime_state();
}
