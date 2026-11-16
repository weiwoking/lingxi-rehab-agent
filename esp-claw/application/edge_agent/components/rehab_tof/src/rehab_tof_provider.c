// SPDX-License-Identifier: Apache-2.0

#include "rehab_tof.h"

#include <inttypes.h>
#include <limits.h>
#include <string.h>

#include "driver/i2c_master.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "platform.h"
#include "rehab_tof_frame_core.h"
#include "rehab_tof_startup_core.h"
#include "vl53l5cx_api.h"

#define REHAB_TOF_TASK_STACK               6144
#define REHAB_TOF_TASK_PRIORITY            4
#define REHAB_TOF_I2C_ADDRESS_7BIT         0x29U
#define REHAB_TOF_RECOVERY_DELAY_MS        50U
#define REHAB_TOF_STARTUP_DISCARD_FRAMES   0U
#define REHAB_TOF_EXTENDED_WAIT_POLL_MS    1U
#define REHAB_TOF_DIAG_LOG_PERIOD_US        INT64_C(10000000)
#define REHAB_TOF_STARTUP_MAX_ATTEMPTS     3U
#define REHAB_TOF_STARTUP_RETRY_DELAY_MS   50U
#define REHAB_TOF_DEVICE_CLEANUP_ATTEMPTS  3U

typedef struct {
    SemaphoreHandle_t lock;
    StaticSemaphore_t lock_storage;
    SemaphoreHandle_t stopped;
    StaticSemaphore_t stopped_storage;
    SemaphoreHandle_t snapshot_done;
    StaticSemaphore_t snapshot_done_storage;
    bool initialized;
    volatile bool stop_requested;
    TaskHandle_t task;

    rehab_tof_config_t config;
    i2c_master_bus_handle_t bus;
    bool bus_owned;
    VL53L5CX_Configuration device;
    VL53L5CX_ResultsData results;
    bool platform_ready;
    bool sensor_ready;
    bool ranging;

    rehab_tof_state_t state;
    bool snapshot_busy;
    bool snapshot_waiter_active;
    bool request_pending;
    bool window_active;
    esp_err_t snapshot_result;
    rehab_tof_window_config_t pending_window_config;
    rehab_tof_window_config_t active_window_config;
    rehab_tof_window_result_t active_window;
    rehab_tof_window_result_t last_window;
    int64_t active_window_started_us;
    int64_t active_window_deadline_us;
    uint64_t request_count;
    uint64_t success_count;
    uint64_t error_count;
    rehab_tof_snapshot_t last_snapshot;

    rehab_tof_frame_collector_t collector;
    rehab_tof_frame_payload_t working_payload;
    rehab_tof_extended_frame_t working_extended;
    rehab_tof_extended_frame_t latest_extended;
    bool latest_extended_valid;
    rehab_tof_frame_summary_t frame_summary;
    rehab_tof_diag_log_gate_t diag_log_gate;
    uint8_t startup_frames_discarded;
    int64_t last_frame_finished_us;

    char last_error[REHAB_TOF_ERROR_MAX];
    esp_err_t last_esp_err;
    uint8_t startup_attempts;
    uint32_t startup_retry_count;
    char last_startup_operation[REHAB_TOF_ERROR_MAX];
    esp_err_t last_startup_raw_err;
    esp_err_t last_startup_mapped_err;
} rehab_tof_provider_t;

static const char *TAG = "rehab_tof";
static rehab_tof_provider_t s_tof = {
    .state = REHAB_TOF_UNINITIALIZED,
};

static bool tof_pipeline_quiescent_locked(void)
{
    return !s_tof.snapshot_busy &&
           !s_tof.snapshot_waiter_active &&
           !s_tof.request_pending &&
           !s_tof.window_active;
}

static void tof_set_state(rehab_tof_state_t state, bool clear_error)
{
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    s_tof.state = state;
    if (clear_error) {
        s_tof.last_error[0] = '\0';
        s_tof.last_esp_err = ESP_OK;
    }
    xSemaphoreGive(s_tof.lock);
}

static void tof_set_error(esp_err_t err, const char *operation)
{
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    s_tof.last_esp_err = err;
    strlcpy(s_tof.last_error, operation != NULL ? operation : "unknown",
            sizeof(s_tof.last_error));
    xSemaphoreGive(s_tof.lock);
    ESP_LOGE(TAG, "TOF_ERROR operation=%s err=%s",
             operation != NULL ? operation : "unknown", esp_err_to_name(err));
}

static bool tof_stop_requested(void)
{
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    bool requested = s_tof.stop_requested;
    xSemaphoreGive(s_tof.lock);
    return requested;
}

static esp_err_t tof_init_bus(void)
{
    if (s_tof.bus != NULL) {
        return ESP_OK;
    }
    const i2c_master_bus_config_t bus_config = {
        .i2c_port = s_tof.config.i2c_port,
        .sda_io_num = s_tof.config.sda_gpio,
        .scl_io_num = s_tof.config.scl_gpio,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    esp_err_t err = i2c_new_master_bus(&bus_config, &s_tof.bus);
    if (err != ESP_OK) {
        tof_set_error(err, "i2c_new_master_bus");
        return err;
    }
    s_tof.bus_owned = true;
    ESP_LOGI(TAG,
             "TOF_I2C owner=rehab_tof port=%d sda=%d scl=%d frequency=%" PRIu32
             " address=0x%02x",
             s_tof.config.i2c_port,
             s_tof.config.sda_gpio,
             s_tof.config.scl_gpio,
             s_tof.config.i2c_frequency_hz,
             REHAB_TOF_I2C_ADDRESS_7BIT);
    return ESP_OK;
}

static esp_err_t tof_prepare_device(void)
{
    if (s_tof.platform_ready) {
        return ESP_ERR_INVALID_STATE;
    }
    memset(&s_tof.device, 0, sizeof(s_tof.device));
    esp_err_t err = vl53l5cx_platform_init(&s_tof.device.platform,
                                           s_tof.bus,
                                           VL53L5CX_DEFAULT_I2C_ADDRESS,
                                           s_tof.config.i2c_frequency_hz);
    if (err != ESP_OK) {
        tof_set_error(err, "vl53l5cx_platform_init");
        return err;
    }
    s_tof.platform_ready = true;
    return ESP_OK;
}

static void tof_begin_uld_operation(void)
{
    vl53l5cx_platform_begin_operation(&s_tof.device.platform);
}

static esp_err_t tof_configure_sensor(const char *phase)
{
    uint8_t alive = 0;
    tof_begin_uld_operation();
    uint8_t status = vl53l5cx_is_alive(&s_tof.device, &alive);
    if (status != 0U || alive == 0U) {
        tof_set_error(ESP_ERR_NOT_FOUND, "vl53l5cx_is_alive");
        return ESP_ERR_NOT_FOUND;
    }
    tof_begin_uld_operation();
    status = vl53l5cx_init(&s_tof.device);
    if (status != 0U) {
        tof_set_error(ESP_FAIL, "vl53l5cx_init");
        return ESP_FAIL;
    }
    tof_begin_uld_operation();
    status = vl53l5cx_set_resolution(&s_tof.device, VL53L5CX_RESOLUTION_8X8);
    if (status != 0U) {
        tof_set_error(ESP_FAIL, "vl53l5cx_set_resolution_8x8");
        return ESP_FAIL;
    }
    tof_begin_uld_operation();
    status = vl53l5cx_set_ranging_mode(&s_tof.device,
                                       VL53L5CX_RANGING_MODE_CONTINUOUS);
    if (status != 0U) {
        tof_set_error(ESP_FAIL, "vl53l5cx_set_continuous_mode");
        return ESP_FAIL;
    }
    tof_begin_uld_operation();
    status = vl53l5cx_set_ranging_frequency_hz(&s_tof.device,
                                                s_tof.config.ranging_frequency_hz);
    if (status != 0U) {
        tof_set_error(ESP_FAIL, "vl53l5cx_set_ranging_frequency");
        return ESP_FAIL;
    }

    uint8_t configured_frequency_hz = 0;
    uint8_t configured_mode = 0;
    tof_begin_uld_operation();
    status = vl53l5cx_get_ranging_frequency_hz(&s_tof.device,
                                                &configured_frequency_hz);
    status |= vl53l5cx_get_ranging_mode(&s_tof.device, &configured_mode);
    if (status != 0U ||
        configured_frequency_hz != s_tof.config.ranging_frequency_hz ||
        configured_mode != VL53L5CX_RANGING_MODE_CONTINUOUS) {
        tof_set_error(ESP_FAIL, "vl53l5cx_verify_configuration");
        return ESP_FAIL;
    }

    s_tof.sensor_ready = true;
    ESP_LOGI(TAG,
             "TOF_CONFIGURED phase=%s api=%s resolution=8x8 frequency=%uHz"
             " mode=continuous",
             phase,
             VL53L5CX_API_REVISION,
             s_tof.config.ranging_frequency_hz);
    return ESP_OK;
}

static esp_err_t tof_start_streaming_once(void)
{
    tof_begin_uld_operation();
    uint8_t status = vl53l5cx_start_ranging(&s_tof.device);
    if (status != 0U) {
        tof_set_error(ESP_FAIL, "vl53l5cx_start_ranging");
        return ESP_FAIL;
    }
    s_tof.ranging = true;
    rehab_tof_frame_collector_start(&s_tof.collector,
                                    REHAB_TOF_STARTUP_DISCARD_FRAMES);
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    s_tof.latest_extended_valid = false;
    memset(&s_tof.latest_extended, 0, sizeof(s_tof.latest_extended));
    xSemaphoreGive(s_tof.lock);
    s_tof.last_frame_finished_us = esp_timer_get_time();
    ESP_LOGI(TAG,
             "TOF_STREAM_STARTED mode=continuous startup_discard=%u"
             " packet_bytes=%" PRIu32 " i2c_hz=%" PRIu32,
             REHAB_TOF_STARTUP_DISCARD_FRAMES,
             s_tof.device.data_read_size,
             s_tof.config.i2c_frequency_hz);
    return ESP_OK;
}

static void tof_invalidate_extended_locked(void)
{
    s_tof.latest_extended_valid = false;
    memset(&s_tof.latest_extended, 0, sizeof(s_tof.latest_extended));
}

static esp_err_t tof_release_attempt_device(void)
{
    if (s_tof.ranging) {
        tof_begin_uld_operation();
        (void)vl53l5cx_stop_ranging(&s_tof.device);
        s_tof.ranging = false;
    }
    rehab_tof_frame_collector_stop(&s_tof.collector);
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    tof_invalidate_extended_locked();
    xSemaphoreGive(s_tof.lock);
    s_tof.sensor_ready = false;
    if (!s_tof.platform_ready) {
        return ESP_OK;
    }
    esp_err_t err = ESP_OK;
    for (uint8_t attempt = 0U;
         attempt < REHAB_TOF_DEVICE_CLEANUP_ATTEMPTS;
         ++attempt) {
        err = vl53l5cx_platform_deinit(&s_tof.device.platform);
        if (err == ESP_OK) {
            s_tof.platform_ready = false;
            return ESP_OK;
        }
        vTaskDelay(1);
    }
    ESP_LOGE(TAG, "TOF_DEVICE_CLEANUP_FAILED err=%s", esp_err_to_name(err));
    return err;
}

static esp_err_t tof_current_raw_error(esp_err_t mapped_error)
{
    if (!s_tof.platform_ready) {
        return mapped_error;
    }
    esp_err_t raw_error =
        vl53l5cx_platform_operation_error(&s_tof.device.platform);
    return raw_error != ESP_OK ? raw_error : mapped_error;
}

static bool tof_startup_error_retryable(esp_err_t raw_error,
                                        esp_err_t mapped_error,
                                        const char *operation)
{
    if (operation != NULL &&
        strcmp(operation, "vl53l5cx_verify_configuration") == 0) {
        return false;
    }
    esp_err_t error = raw_error != ESP_OK ? raw_error : mapped_error;
    return error == ESP_ERR_INVALID_STATE || error == ESP_ERR_TIMEOUT ||
           error == ESP_ERR_INVALID_RESPONSE || error == ESP_ERR_NOT_FOUND ||
           error == ESP_FAIL;
}

static rehab_tof_startup_attempt_result_t tof_startup_attempt(
    void *context,
    uint8_t attempt,
    uint8_t max_attempts)
{
    (void)context;
    ESP_LOGI(TAG, "TOF_START_ATTEMPT attempt=%u/%u", attempt, max_attempts);
    esp_err_t mapped_error = tof_init_bus();
    if (mapped_error == ESP_OK) {
        mapped_error = tof_prepare_device();
    }
    if (mapped_error == ESP_OK) {
        if (attempt == 1U) {
            vTaskDelay(pdMS_TO_TICKS(100));
        }
        mapped_error = tof_configure_sensor("startup");
    }
    if (mapped_error == ESP_OK) {
        mapped_error = tof_start_streaming_once();
    }
    if (mapped_error == ESP_OK) {
        rehab_tof_startup_attempt_result_t success = {0};
        return success;
    }

    const char *operation = s_tof.last_error[0] != '\0'
        ? s_tof.last_error
        : "startup_unknown";
    esp_err_t raw_error = tof_current_raw_error(mapped_error);
    rehab_tof_startup_attempt_result_t failure = {
        .mapped_error = mapped_error,
        .raw_error = raw_error,
        .operation = operation,
        .retryable = tof_startup_error_retryable(raw_error,
                                                 mapped_error,
                                                 operation),
    };
    return failure;
}

static int tof_startup_cleanup(void *context)
{
    (void)context;
    return (int)tof_release_attempt_device();
}

static bool tof_startup_wait(void *context, uint32_t delay_ms)
{
    (void)context;
    TickType_t delay_ticks = pdMS_TO_TICKS(delay_ms);
    if (delay_ticks == 0U) {
        delay_ticks = 1U;
    }
    (void)ulTaskNotifyTake(pdTRUE, delay_ticks);
    return !tof_stop_requested();
}

static bool tof_startup_stop_requested(void *context)
{
    (void)context;
    return tof_stop_requested();
}

static void tof_startup_failure_observed(
    void *context,
    uint8_t attempt,
    uint8_t max_attempts,
    const rehab_tof_startup_attempt_result_t *failure,
    bool retrying,
    int cleanup_error)
{
    (void)context;
    const char *operation = failure->operation != NULL
        ? failure->operation
        : "startup_unknown";
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    s_tof.startup_attempts = attempt;
    s_tof.last_startup_raw_err = (esp_err_t)failure->raw_error;
    s_tof.last_startup_mapped_err = (esp_err_t)failure->mapped_error;
    strlcpy(s_tof.last_startup_operation, operation,
            sizeof(s_tof.last_startup_operation));
    if (retrying) {
        ++s_tof.startup_retry_count;
    }
    xSemaphoreGive(s_tof.lock);

    if (retrying) {
        ESP_LOGW(TAG,
                 "TOF_START_RETRY attempt=%u/%u operation=%s raw_err=%s"
                 " mapped_error=%s retrying=1 retry_delay_ms=%" PRIu32,
                 attempt,
                 max_attempts,
                 operation,
                 esp_err_to_name((esp_err_t)failure->raw_error),
                 esp_err_to_name((esp_err_t)failure->mapped_error),
                 s_tof.config.startup_retry_delay_ms);
    } else {
        ESP_LOGE(TAG,
                 "TOF_START_FAILED attempts=%u operation=%s raw_err=%s"
                 " mapped_error=%s retrying=0 cleanup_error=%s",
                 attempt,
                 operation,
                 esp_err_to_name((esp_err_t)failure->raw_error),
                 esp_err_to_name((esp_err_t)failure->mapped_error),
                 esp_err_to_name((esp_err_t)cleanup_error));
    }
}

static esp_err_t tof_hardware_init(void)
{
    const rehab_tof_startup_policy_t policy = {
        .max_attempts = s_tof.config.startup_max_attempts,
        .retry_delay_ms = s_tof.config.startup_retry_delay_ms,
    };
    const rehab_tof_startup_ops_t ops = {
        .context = NULL,
        .attempt = tof_startup_attempt,
        .cleanup_failed_attempt = tof_startup_cleanup,
        .wait_retry_delay = tof_startup_wait,
        .stop_requested = tof_startup_stop_requested,
        .failure_observed = tof_startup_failure_observed,
    };
    rehab_tof_startup_run_result_t run = rehab_tof_startup_run(&policy, &ops);
    if (run.outcome == REHAB_TOF_STARTUP_SUCCESS) {
        xSemaphoreTake(s_tof.lock, portMAX_DELAY);
        s_tof.startup_attempts = run.attempts;
        xSemaphoreGive(s_tof.lock);
        if (run.attempts > 1U) {
            ESP_LOGI(TAG, "TOF_START_RECOVERED attempt=%u", run.attempts);
        }
        return ESP_OK;
    }
    if (run.outcome == REHAB_TOF_STARTUP_STOPPED) {
        return ESP_ERR_INVALID_STATE;
    }
    if (run.outcome == REHAB_TOF_STARTUP_CLEANUP_FAILED) {
        return (esp_err_t)run.cleanup_error;
    }
    if (run.outcome == REHAB_TOF_STARTUP_INVALID_ARGUMENT) {
        return ESP_ERR_INVALID_ARG;
    }
    return (esp_err_t)run.last_failure.mapped_error;
}

static void tof_hardware_deinit(void)
{
    esp_err_t cleanup_error = tof_release_attempt_device();
    if (s_tof.bus != NULL && s_tof.bus_owned && cleanup_error == ESP_OK) {
        esp_err_t err = i2c_del_master_bus(s_tof.bus);
        if (err == ESP_OK) {
            s_tof.bus = NULL;
            s_tof.bus_owned = false;
        } else {
            ESP_LOGE(TAG, "TOF_BUS_CLEANUP_FAILED err=%s", esp_err_to_name(err));
        }
    }
    s_tof.sensor_ready = false;
}

static bool tof_zone_valid(int16_t distance_mm, uint8_t status_code)
{
    return distance_mm > 0 && (status_code == 5U || status_code == 9U);
}

static bool tof_zone_usable(int16_t distance_mm, uint8_t status_code)
{
    return distance_mm > 0 &&
           (status_code == 5U || status_code == 6U || status_code == 9U);
}

static void tof_payload_from_results(rehab_tof_frame_payload_t *payload)
{
    for (size_t i = 0; i < REHAB_TOF_ZONE_COUNT; ++i) {
        size_t target_index = i * VL53L5CX_NB_TARGET_PER_ZONE;
        payload->distance_mm[i] = s_tof.results.distance_mm[target_index];
        payload->range_sigma_mm[i] = s_tof.results.range_sigma_mm[target_index];
        payload->target_status[i] = s_tof.results.target_status[target_index];
        payload->nb_target_detected[i] = s_tof.results.nb_target_detected[i];
    }
}

static void tof_candidate_from_extended(
    rehab_tof_candidate_frame_t *candidate,
    const rehab_tof_extended_frame_t *frame)
{
    candidate->tof_frame_seq = frame->sequence;
    candidate->timestamp_us = frame->timestamp_us;
    memcpy(candidate->distance_mm, frame->distance_mm,
           sizeof(candidate->distance_mm));
    memcpy(candidate->status_code, frame->target_status,
           sizeof(candidate->status_code));
    for (size_t i = 0; i < REHAB_TOF_ZONE_COUNT; ++i) {
        int16_t distance = candidate->distance_mm[i];
        uint8_t status = candidate->status_code[i];
        if (tof_zone_usable(distance, status)) {
            ++candidate->usable_zone_count;
        }
        if (distance > 0 && status == 5U) {
            ++candidate->high_confidence_zone_count;
        }
        if (tof_zone_valid(distance, status)) {
            ++candidate->valid_zone_count;
        }
    }
    for (size_t i = 0; i < REHAB_TOF_ZONE_COUNT; ++i) {
        uint8_t status = frame->target_status[i];
        if (status == 5U) {
            ++candidate->status_5_count;
        } else if (status == 6U) {
            ++candidate->status_6_count;
        } else if (status == 9U) {
            ++candidate->status_9_count;
        } else if (status == 255U) {
            ++candidate->status_255_count;
        }
    }
}

static void tof_snapshot_from_candidate(
    rehab_tof_snapshot_t *snapshot,
    const rehab_tof_candidate_frame_t *candidate,
    uint64_t generation,
    uint32_t total_time_ms,
    uint8_t frames_consumed)
{
    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->generation = generation;
    snapshot->timestamp_us = candidate->timestamp_us;
    snapshot->total_time_ms = total_time_ms;
    snapshot->resolution_width = 8;
    snapshot->resolution_height = 8;
    memcpy(snapshot->distance_mm, candidate->distance_mm,
           sizeof(snapshot->distance_mm));
    memcpy(snapshot->status_code, candidate->status_code,
           sizeof(snapshot->status_code));
    snapshot->valid_zone_count = candidate->valid_zone_count;
    snapshot->usable_zone_count = candidate->usable_zone_count;
    snapshot->high_confidence_zone_count = candidate->high_confidence_zone_count;
    snapshot->status_5_count = candidate->status_5_count;
    snapshot->status_6_count = candidate->status_6_count;
    snapshot->status_9_count = candidate->status_9_count;
    snapshot->status_255_count = candidate->status_255_count;
    snapshot->frames_consumed_per_snapshot = frames_consumed;
    snapshot->minimum_valid_distance_mm = -1;
    snapshot->maximum_valid_distance_mm = -1;
    for (size_t i = 0; i < REHAB_TOF_ZONE_COUNT; ++i) {
        if (!tof_zone_valid(snapshot->distance_mm[i], snapshot->status_code[i])) {
            continue;
        }
        if (snapshot->minimum_valid_distance_mm < 0 ||
            snapshot->distance_mm[i] < snapshot->minimum_valid_distance_mm) {
            snapshot->minimum_valid_distance_mm = snapshot->distance_mm[i];
        }
        if (snapshot->maximum_valid_distance_mm < 0 ||
            snapshot->distance_mm[i] > snapshot->maximum_valid_distance_mm) {
            snapshot->maximum_valid_distance_mm = snapshot->distance_mm[i];
        }
    }
}

static void tof_activate_pending_request(void)
{
    rehab_tof_warmup_notify_fn notify = NULL;
    void *notify_ctx = NULL;
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    if (s_tof.request_pending && !s_tof.window_active) {
        s_tof.request_pending = false;
        s_tof.window_active = true;
        s_tof.active_window_config = s_tof.pending_window_config;
        memset(&s_tof.active_window, 0, sizeof(s_tof.active_window));
        s_tof.active_window_started_us = esp_timer_get_time();
        s_tof.active_window_deadline_us =
            s_tof.active_window_started_us +
            (int64_t)s_tof.config.ready_timeout_ms * 1000LL;
        notify = s_tof.active_window_config.warmup_notify;
        notify_ctx = s_tof.active_window_config.warmup_notify_ctx;
    }
    xSemaphoreGive(s_tof.lock);

    /*
     * Legacy name retained for source compatibility. In continuous mode this
     * callback now marks the request boundary and starts Camera capture; it no
     * longer means that a sensor frame was discarded for every request.
     */
    if (notify != NULL) {
        notify(notify_ctx);
    }
}

static void tof_complete_active_error(esp_err_t err, const char *reason)
{
    bool notify_waiter = false;
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    if (s_tof.window_active || s_tof.request_pending) {
        s_tof.request_pending = false;
        s_tof.window_active = false;
        s_tof.snapshot_result = err;
        s_tof.last_esp_err = err;
        strlcpy(s_tof.last_error, reason, sizeof(s_tof.last_error));
        notify_waiter = s_tof.snapshot_waiter_active;
        if (notify_waiter) {
            ++s_tof.error_count;
        } else {
            s_tof.snapshot_busy = false;
        }
    }
    xSemaphoreGive(s_tof.lock);
    if (notify_waiter) {
        xSemaphoreGive(s_tof.snapshot_done);
    }
}

static bool tof_append_active_candidate(
    const rehab_tof_extended_frame_t *frame)
{
    bool notify_waiter = false;
    bool completed = false;
    uint64_t request_generation = 0;
    uint32_t total_time_ms = 0;
    uint8_t candidate_count = 0;

    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    if (s_tof.window_active &&
        s_tof.active_window.actual_candidate_count <
            s_tof.active_window_config.candidate_count) {
        uint8_t index = s_tof.active_window.actual_candidate_count;
        tof_candidate_from_extended(&s_tof.active_window.candidates[index], frame);
        ++s_tof.active_window.actual_candidate_count;
    }

    if (s_tof.window_active &&
        s_tof.active_window.actual_candidate_count ==
            s_tof.active_window_config.candidate_count) {
        total_time_ms = (uint32_t)(
            (esp_timer_get_time() - s_tof.active_window_started_us) / 1000LL);
        s_tof.active_window.total_time_ms = total_time_ms;
        request_generation = s_tof.last_snapshot.generation + 1U;
        s_tof.active_window.request_generation = request_generation;
        s_tof.last_window = s_tof.active_window;
        candidate_count = s_tof.active_window.actual_candidate_count;
        const rehab_tof_candidate_frame_t *candidate =
            &s_tof.active_window.candidates[candidate_count - 1U];
        tof_snapshot_from_candidate(&s_tof.last_snapshot,
                                    candidate,
                                    request_generation,
                                    total_time_ms,
                                    candidate_count);
        s_tof.snapshot_result = ESP_OK;
        s_tof.last_error[0] = '\0';
        s_tof.last_esp_err = ESP_OK;
        s_tof.window_active = false;
        notify_waiter = s_tof.snapshot_waiter_active;
        if (notify_waiter) {
            ++s_tof.success_count;
        } else {
            s_tof.snapshot_busy = false;
        }
        completed = true;
    }
    xSemaphoreGive(s_tof.lock);

    if (completed) {
        ESP_LOGI(TAG,
                 "TOF_WINDOW request_generation=%" PRIu64
                 " candidates=%u last_tof_seq=%" PRIu64
                 " frames_consumed=%u total_ms=%" PRIu32
                 " warmup_per_request=0",
                 request_generation,
                 candidate_count,
                 frame->sequence,
                 candidate_count,
                 total_time_ms);
    }
    if (notify_waiter) {
        xSemaphoreGive(s_tof.snapshot_done);
    }
    return completed;
}

static bool tof_active_window_timed_out(void)
{
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    bool timed_out = s_tof.window_active &&
                     esp_timer_get_time() >= s_tof.active_window_deadline_us;
    xSemaphoreGive(s_tof.lock);
    return timed_out;
}

static esp_err_t tof_recover_streaming(const char *reason)
{
    ESP_LOGW(TAG, "TOF_STREAM_RECOVERY reason=%s", reason);
    if (s_tof.ranging) {
        (void)vl53l5cx_stop_ranging(&s_tof.device);
        s_tof.ranging = false;
    }
    rehab_tof_frame_collector_stop(&s_tof.collector);
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    tof_invalidate_extended_locked();
    xSemaphoreGive(s_tof.lock);
    vTaskDelay(pdMS_TO_TICKS(REHAB_TOF_RECOVERY_DELAY_MS));
    s_tof.sensor_ready = false;
    esp_err_t err = tof_configure_sensor("stream_recovery");
    if (err == ESP_OK) {
        err = tof_start_streaming_once();
    }
    return err;
}

static esp_err_t tof_read_and_publish_ready_frame(void)
{
    int64_t ready_us = esp_timer_get_time();
    uint32_t data_ready_wait_us = s_tof.last_frame_finished_us > 0
        ? (uint32_t)(ready_us - s_tof.last_frame_finished_us)
        : 0U;

    memset(&s_tof.results, 0, sizeof(s_tof.results));
    s_tof.device.platform.last_read_size = 0U;
    s_tof.device.platform.last_read_duration_us = 0U;
    tof_begin_uld_operation();
    int64_t get_started_us = esp_timer_get_time();
    uint8_t driver_status =
        vl53l5cx_get_ranging_data(&s_tof.device, &s_tof.results);
    int64_t get_finished_us = esp_timer_get_time();
    uint32_t get_total_us = (uint32_t)(get_finished_us - get_started_us);

    rehab_tof_io_validation_t io_validation = rehab_tof_frame_validate_io(
        driver_status,
        s_tof.device.platform.last_io_error,
        s_tof.device.data_read_size,
        s_tof.device.platform.last_read_size);
    if (io_validation != REHAB_TOF_IO_OK) {
        ESP_LOGE(TAG,
                 "TOF_FRAME_IO validation=%d driver=0x%02x esp=%s"
                 " expected=%" PRIu32 " actual=%" PRIu32,
                 (int)io_validation,
                 driver_status,
                 esp_err_to_name(s_tof.device.platform.last_io_error),
                 s_tof.device.data_read_size,
                 s_tof.device.platform.last_read_size);
        return ESP_FAIL;
    }

    int64_t copy_started_us = esp_timer_get_time();
    tof_payload_from_results(&s_tof.working_payload);
    uint64_t sequence = 0;
    rehab_tof_frame_ingest_result_t ingest =
        rehab_tof_frame_collector_ingest(&s_tof.collector,
                                         &s_tof.working_payload,
                                         &sequence);
    if (ingest == REHAB_TOF_FRAME_STARTUP_DISCARDED) {
        ++s_tof.startup_frames_discarded;
        s_tof.last_frame_finished_us = esp_timer_get_time();
        ESP_LOGI(TAG,
                 "TOF_STARTUP_FRAME discarded=1 policy=start_only"
                 " status6_may_be_first_range");
        return ESP_OK;
    }
    if (ingest != REHAB_TOF_FRAME_PUBLISHED) {
        return ESP_ERR_INVALID_STATE;
    }

    memset(&s_tof.working_extended, 0, sizeof(s_tof.working_extended));
    s_tof.working_extended.sequence = sequence;
    s_tof.working_extended.timestamp_us = get_finished_us;
    s_tof.working_extended.resolution_width = 8;
    s_tof.working_extended.resolution_height = 8;
    memcpy(s_tof.working_extended.distance_mm,
           s_tof.collector.latest.distance_mm,
           sizeof(s_tof.working_extended.distance_mm));
    memcpy(s_tof.working_extended.range_sigma_mm,
           s_tof.collector.latest.range_sigma_mm,
           sizeof(s_tof.working_extended.range_sigma_mm));
    memcpy(s_tof.working_extended.target_status,
           s_tof.collector.latest.target_status,
           sizeof(s_tof.working_extended.target_status));
    memcpy(s_tof.working_extended.nb_target_detected,
           s_tof.collector.latest.nb_target_detected,
           sizeof(s_tof.working_extended.nb_target_detected));
    s_tof.working_extended.data_ready_wait_us = data_ready_wait_us;
    s_tof.working_extended.get_ranging_data_us = get_total_us;
    s_tof.working_extended.i2c_read_us =
        s_tof.device.platform.last_read_duration_us;
    uint32_t uld_parse_us = get_total_us >
            s_tof.device.platform.last_read_duration_us
        ? get_total_us - s_tof.device.platform.last_read_duration_us
        : 0U;
    s_tof.working_extended.parse_and_copy_us =
        uld_parse_us + (uint32_t)(esp_timer_get_time() - copy_started_us);
    s_tof.working_extended.i2c_packet_bytes =
        s_tof.device.platform.last_read_size;
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    s_tof.latest_extended = s_tof.working_extended;
    uint32_t provider_publish_us =
        (uint32_t)(esp_timer_get_time() - ready_us);
    s_tof.latest_extended.provider_publish_us = provider_publish_us;
    s_tof.working_extended.provider_publish_us = provider_publish_us;
    s_tof.latest_extended_valid = true;
    xSemaphoreGive(s_tof.lock);

    (void)tof_append_active_candidate(&s_tof.working_extended);
    s_tof.last_frame_finished_us = esp_timer_get_time();
    bool diagnostic_log_due = rehab_tof_diag_log_gate_due(
        &s_tof.diag_log_gate,
        s_tof.last_frame_finished_us,
        REHAB_TOF_DIAG_LOG_PERIOD_US);
    if (diagnostic_log_due) {
        ESP_LOGD(TAG,
                 "TOF_FRAME seq=%" PRIu64
                 " ready_wait_us=%" PRIu32
                 " get_us=%" PRIu32
                 " i2c_read_us=%" PRIu32
                 " parse_copy_us=%" PRIu32
                 " publish_us=%" PRIu32
                 " packet_bytes=%" PRIu32,
                 sequence,
                 s_tof.working_extended.data_ready_wait_us,
                 s_tof.working_extended.get_ranging_data_us,
                 s_tof.working_extended.i2c_read_us,
                 s_tof.working_extended.parse_and_copy_us,
                 s_tof.working_extended.provider_publish_us,
                 s_tof.working_extended.i2c_packet_bytes);
        /* collector.latest is the exact payload published above by this task. */
        rehab_tof_frame_summarize(&s_tof.collector.latest,
                                  &s_tof.frame_summary);
        ESP_LOGD(TAG,
                 "TOF_EXT_SUMMARY seq=%" PRIu64
                 " valid_zones=%u target0_zones=%u target1_zones=%u"
                 " target_gt1_zones=%u status5=%u status6=%u status9=%u"
                 " status255=%u status_other=%u distance_min_mm=%" PRId32
                 " distance_median_mm=%" PRId32 " distance_max_mm=%" PRId32
                 " sigma_min_mm=%" PRId32 " sigma_median_mm=%" PRId32
                 " sigma_max_mm=%" PRId32 " sigma_zero_zones=%u",
                 sequence,
                 s_tof.frame_summary.valid_zones,
                 s_tof.frame_summary.target0_zones,
                 s_tof.frame_summary.target1_zones,
                 s_tof.frame_summary.target_gt1_zones,
                 s_tof.frame_summary.status5_zones,
                 s_tof.frame_summary.status6_zones,
                 s_tof.frame_summary.status9_zones,
                 s_tof.frame_summary.status255_zones,
                 s_tof.frame_summary.status_other_zones,
                 s_tof.frame_summary.distance_min_mm,
                 s_tof.frame_summary.distance_median_mm,
                 s_tof.frame_summary.distance_max_mm,
                 s_tof.frame_summary.sigma_min_mm,
                 s_tof.frame_summary.sigma_median_mm,
                 s_tof.frame_summary.sigma_max_mm,
                 s_tof.frame_summary.sigma_zero_zones);
    }
    return ESP_OK;
}

static void tof_provider_task(void *argument)
{
    (void)argument;
    esp_err_t err = tof_hardware_init();
    if (err == ESP_OK) {
        tof_set_state(REHAB_TOF_READY, true);
        while (!tof_stop_requested()) {
            tof_activate_pending_request();
            if (tof_active_window_timed_out()) {
                tof_complete_active_error(ESP_ERR_TIMEOUT,
                                          "candidate_frame_timeout");
            }

            uint8_t ready = 0;
            tof_begin_uld_operation();
            uint8_t status = vl53l5cx_check_data_ready(&s_tof.device, &ready);
            if (status != 0U) {
                tof_complete_active_error(ESP_FAIL,
                                          "check_data_ready_failed");
                err = tof_recover_streaming("check_data_ready_failed");
                if (err != ESP_OK) {
                    tof_set_error(err, "stream_recovery_failed");
                    break;
                }
                continue;
            }
            if (ready != 0U) {
                err = tof_read_and_publish_ready_frame();
                if (err != ESP_OK) {
                    tof_complete_active_error(err, "get_ranging_data_failed");
                    err = tof_recover_streaming("get_ranging_data_failed");
                    if (err != ESP_OK) {
                        tof_set_error(err, "stream_recovery_failed");
                        break;
                    }
                }
                continue;
            }

            TickType_t poll_ticks =
                pdMS_TO_TICKS(s_tof.config.ready_poll_interval_ms);
            if (poll_ticks == 0) {
                poll_ticks = 1;
            }
            (void)ulTaskNotifyTake(pdTRUE, poll_ticks);
        }
    } else {
        tof_set_state(REHAB_TOF_ERROR, false);
    }

    tof_complete_active_error(ESP_ERR_INVALID_STATE, "stopped");
    tof_hardware_deinit();
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    if (s_tof.stop_requested) {
        s_tof.state = REHAB_TOF_STOPPED;
    } else if (err != ESP_OK) {
        s_tof.state = REHAB_TOF_ERROR;
    }
    s_tof.request_pending = false;
    s_tof.window_active = false;
    s_tof.snapshot_busy = false;
    s_tof.snapshot_waiter_active = false;
    s_tof.task = NULL;
    xSemaphoreGive(s_tof.lock);
    xSemaphoreGive(s_tof.stopped);
    vTaskDelete(NULL);
}

esp_err_t rehab_tof_init(const rehab_tof_config_t *config)
{
    if (config == NULL ||
        (config->i2c_frequency_hz != 400000U &&
         config->i2c_frequency_hz != 1000000U) ||
        config->ranging_frequency_hz == 0 ||
        config->ranging_frequency_hz > 15 ||
        config->ready_poll_interval_ms == 0 ||
        config->ready_timeout_ms == 0 ||
        config->startup_max_attempts > REHAB_TOF_STARTUP_MAX_ATTEMPTS) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_tof.lock == NULL) {
        s_tof.lock = xSemaphoreCreateMutexStatic(&s_tof.lock_storage);
        s_tof.stopped = xSemaphoreCreateBinaryStatic(&s_tof.stopped_storage);
        s_tof.snapshot_done =
            xSemaphoreCreateBinaryStatic(&s_tof.snapshot_done_storage);
        if (s_tof.lock == NULL || s_tof.stopped == NULL ||
            s_tof.snapshot_done == NULL) {
            return ESP_ERR_NO_MEM;
        }
    }

    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    if (s_tof.initialized) {
        xSemaphoreGive(s_tof.lock);
        return ESP_ERR_INVALID_STATE;
    }
    s_tof.initialized = true;
    s_tof.stop_requested = false;
    s_tof.state = REHAB_TOF_STOPPED;
    s_tof.config = *config;
    if (s_tof.config.startup_max_attempts == 0U) {
        s_tof.config.startup_max_attempts = REHAB_TOF_STARTUP_MAX_ATTEMPTS;
    }
    if (s_tof.config.startup_retry_delay_ms == 0U) {
        s_tof.config.startup_retry_delay_ms =
            REHAB_TOF_STARTUP_RETRY_DELAY_MS;
    }
    s_tof.snapshot_busy = false;
    s_tof.snapshot_waiter_active = false;
    s_tof.request_pending = false;
    s_tof.window_active = false;
    s_tof.snapshot_result = ESP_ERR_INVALID_STATE;
    s_tof.startup_frames_discarded = 0U;
    s_tof.diag_log_gate = (rehab_tof_diag_log_gate_t){0};
    s_tof.startup_attempts = 0U;
    s_tof.startup_retry_count = 0U;
    s_tof.last_startup_operation[0] = '\0';
    s_tof.last_startup_raw_err = ESP_OK;
    s_tof.last_startup_mapped_err = ESP_OK;
    tof_invalidate_extended_locked();
    s_tof.last_error[0] = '\0';
    s_tof.last_esp_err = ESP_OK;
    xSemaphoreGive(s_tof.lock);
    return ESP_OK;
}

esp_err_t rehab_tof_start(void)
{
    if (s_tof.lock == NULL || !s_tof.initialized) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    if (s_tof.task != NULL) {
        xSemaphoreGive(s_tof.lock);
        return ESP_OK;
    }
    while (xSemaphoreTake(s_tof.stopped, 0) == pdTRUE) {
    }
    while (xSemaphoreTake(s_tof.snapshot_done, 0) == pdTRUE) {
    }
    s_tof.stop_requested = false;
    s_tof.state = REHAB_TOF_STARTING;
    s_tof.task = (TaskHandle_t)1;
    xSemaphoreGive(s_tof.lock);

    TaskHandle_t task = NULL;
    BaseType_t created = xTaskCreate(tof_provider_task,
                                     "tof_provider",
                                     REHAB_TOF_TASK_STACK,
                                     NULL,
                                     REHAB_TOF_TASK_PRIORITY,
                                     &task);
    if (created != pdPASS) {
        xSemaphoreTake(s_tof.lock, portMAX_DELAY);
        s_tof.task = NULL;
        s_tof.state = REHAB_TOF_ERROR;
        xSemaphoreGive(s_tof.lock);
        tof_set_error(ESP_ERR_NO_MEM, "provider_task_create");
        return ESP_ERR_NO_MEM;
    }
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    if (s_tof.task == (TaskHandle_t)1) {
        s_tof.task = task;
    }
    xSemaphoreGive(s_tof.lock);
    return ESP_OK;
}

static esp_err_t tof_window_request(
    const rehab_tof_window_config_t *config,
    rehab_tof_window_result_t *result,
    uint32_t timeout_ms)
{
    if (config == NULL || timeout_ms == 0 ||
        config->candidate_count == 0 ||
        config->candidate_count > REHAB_TOF_MAX_CANDIDATE_FRAMES ||
        s_tof.lock == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }

    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    TaskHandle_t task = s_tof.task;
    bool ready = s_tof.initialized && s_tof.state == REHAB_TOF_READY &&
                 s_tof.sensor_ready && s_tof.ranging &&
                 task != NULL && task != (TaskHandle_t)1;
    if (!ready || s_tof.snapshot_busy) {
        xSemaphoreGive(s_tof.lock);
        return ESP_ERR_INVALID_STATE;
    }
    while (xSemaphoreTake(s_tof.snapshot_done, 0) == pdTRUE) {
    }
    s_tof.snapshot_busy = true;
    s_tof.snapshot_waiter_active = true;
    s_tof.request_pending = true;
    s_tof.snapshot_result = ESP_ERR_NOT_FINISHED;
    s_tof.pending_window_config = *config;
    ++s_tof.request_count;
    xSemaphoreGive(s_tof.lock);

    xTaskNotifyGive(task);
    TickType_t timeout_ticks = pdMS_TO_TICKS(timeout_ms);
    if (timeout_ticks == 0) {
        timeout_ticks = 1;
    }
    if (xSemaphoreTake(s_tof.snapshot_done, timeout_ticks) != pdTRUE) {
        xSemaphoreTake(s_tof.lock, portMAX_DELAY);
        s_tof.snapshot_waiter_active = false;
        ++s_tof.error_count;
        if (s_tof.snapshot_result != ESP_ERR_NOT_FINISHED) {
            s_tof.snapshot_busy = false;
        }
        xSemaphoreGive(s_tof.lock);
        return ESP_ERR_TIMEOUT;
    }

    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    esp_err_t request_result = s_tof.snapshot_result;
    if (request_result == ESP_OK && result != NULL) {
        *result = s_tof.last_window;
    }
    s_tof.snapshot_waiter_active = false;
    s_tof.snapshot_busy = false;
    xSemaphoreGive(s_tof.lock);
    return request_result;
}

esp_err_t rehab_tof_snapshot_acquire(rehab_tof_snapshot_t *snapshot,
                                     uint32_t timeout_ms)
{
    if (snapshot == NULL || timeout_ms == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(snapshot, 0, sizeof(*snapshot));
    const rehab_tof_window_config_t config = {
        .candidate_count = 1,
        .warmup_notify = NULL,
        .warmup_notify_ctx = NULL,
    };
    esp_err_t err = tof_window_request(&config, NULL, timeout_ms);
    if (err != ESP_OK) {
        return err;
    }
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    *snapshot = s_tof.last_snapshot;
    xSemaphoreGive(s_tof.lock);
    return ESP_OK;
}

esp_err_t rehab_tof_window_acquire(const rehab_tof_window_config_t *config,
                                   rehab_tof_window_result_t *result,
                                   uint32_t timeout_ms)
{
    if (result == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    return tof_window_request(config, result, timeout_ms);
}

esp_err_t rehab_tof_extended_get_latest(rehab_tof_extended_frame_t *frame,
                                        uint64_t after_sequence)
{
    if (frame == NULL || s_tof.lock == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(frame, 0, sizeof(*frame));
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    bool running = s_tof.initialized &&
                   s_tof.state == REHAB_TOF_READY &&
                   s_tof.sensor_ready &&
                   s_tof.ranging;
    if (!running) {
        xSemaphoreGive(s_tof.lock);
        return ESP_ERR_INVALID_STATE;
    }
    if (!s_tof.latest_extended_valid ||
        s_tof.latest_extended.sequence <= after_sequence) {
        xSemaphoreGive(s_tof.lock);
        return ESP_ERR_NOT_FOUND;
    }
    *frame = s_tof.latest_extended;
    xSemaphoreGive(s_tof.lock);
    return ESP_OK;
}

esp_err_t rehab_tof_extended_wait_next(rehab_tof_extended_frame_t *frame,
                                       uint64_t after_sequence,
                                       uint32_t timeout_ms)
{
    if (frame == NULL || timeout_ms == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    int64_t deadline_us =
        esp_timer_get_time() + (int64_t)timeout_ms * 1000LL;
    while (esp_timer_get_time() < deadline_us) {
        esp_err_t err =
            rehab_tof_extended_get_latest(frame, after_sequence);
        if (err == ESP_OK || err == ESP_ERR_INVALID_STATE) {
            return err;
        }
        if (err != ESP_ERR_NOT_FOUND) {
            return err;
        }
        vTaskDelay(pdMS_TO_TICKS(REHAB_TOF_EXTENDED_WAIT_POLL_MS));
    }
    return ESP_ERR_TIMEOUT;
}

esp_err_t rehab_tof_get_status(rehab_tof_status_t *status)
{
    if (status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(status, 0, sizeof(*status));
    status->resolution_width = 8;
    status->resolution_height = 8;
    if (s_tof.lock == NULL) {
        status->state = REHAB_TOF_UNINITIALIZED;
        status->pipeline_quiescent = true;
        return ESP_OK;
    }

    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    status->state = s_tof.state;
    status->ready = s_tof.state == REHAB_TOF_READY && s_tof.sensor_ready;
    status->snapshot_busy = s_tof.snapshot_busy;
    status->request_pending = s_tof.request_pending;
    status->window_active = s_tof.window_active;
    status->pipeline_quiescent = tof_pipeline_quiescent_locked();
    status->generation = s_tof.last_snapshot.generation;
    status->request_count = s_tof.request_count;
    status->success_count = s_tof.success_count;
    status->error_count = s_tof.error_count;
    status->timestamp_us = s_tof.last_snapshot.timestamp_us;
    status->total_time_ms = s_tof.last_snapshot.total_time_ms;
    memcpy(status->distance_mm, s_tof.last_snapshot.distance_mm,
           sizeof(status->distance_mm));
    memcpy(status->status_code, s_tof.last_snapshot.status_code,
           sizeof(status->status_code));
    status->valid_zone_count = s_tof.last_snapshot.valid_zone_count;
    status->usable_zone_count = s_tof.last_snapshot.usable_zone_count;
    status->high_confidence_zone_count =
        s_tof.last_snapshot.high_confidence_zone_count;
    status->status_5_count = s_tof.last_snapshot.status_5_count;
    status->status_6_count = s_tof.last_snapshot.status_6_count;
    status->status_9_count = s_tof.last_snapshot.status_9_count;
    status->status_255_count = s_tof.last_snapshot.status_255_count;
    status->warmup_status_5_count = s_tof.last_snapshot.warmup_status_5_count;
    status->warmup_status_6_count = s_tof.last_snapshot.warmup_status_6_count;
    status->warmup_status_9_count = s_tof.last_snapshot.warmup_status_9_count;
    status->warmup_status_255_count =
        s_tof.last_snapshot.warmup_status_255_count;
    status->frames_consumed_per_snapshot =
        s_tof.last_snapshot.frames_consumed_per_snapshot;
    status->minimum_valid_distance_mm =
        s_tof.last_snapshot.minimum_valid_distance_mm;
    status->maximum_valid_distance_mm =
        s_tof.last_snapshot.maximum_valid_distance_mm;
    strlcpy(status->last_error, s_tof.last_error, sizeof(status->last_error));
    status->last_esp_err = s_tof.last_esp_err;
    status->i2c_port = s_tof.config.i2c_port;
    status->sda_gpio = s_tof.config.sda_gpio;
    status->scl_gpio = s_tof.config.scl_gpio;
    status->i2c_frequency_hz = s_tof.config.i2c_frequency_hz;
    status->ranging_frequency_hz = s_tof.config.ranging_frequency_hz;
    status->continuous_ranging = s_tof.ranging;
    status->extended_frame_valid = s_tof.latest_extended_valid;
    status->startup_frames_discarded = s_tof.startup_frames_discarded;
    status->extended_sequence = s_tof.latest_extended.sequence;
    status->last_data_ready_wait_us =
        s_tof.latest_extended.data_ready_wait_us;
    status->last_get_ranging_data_us =
        s_tof.latest_extended.get_ranging_data_us;
    status->last_i2c_read_us = s_tof.latest_extended.i2c_read_us;
    status->last_parse_and_copy_us =
        s_tof.latest_extended.parse_and_copy_us;
    status->last_provider_publish_us =
        s_tof.latest_extended.provider_publish_us;
    status->last_i2c_packet_bytes =
        s_tof.latest_extended.i2c_packet_bytes;
    status->i2c_read_error_count =
        s_tof.device.platform.read_error_count;
    status->i2c_write_error_count =
        s_tof.device.platform.write_error_count;
    status->startup_attempts = s_tof.startup_attempts;
    status->startup_max_attempts = s_tof.config.startup_max_attempts;
    status->startup_retry_count = s_tof.startup_retry_count;
    status->last_startup_raw_err = s_tof.last_startup_raw_err;
    status->last_startup_mapped_err = s_tof.last_startup_mapped_err;
    strlcpy(status->last_startup_operation,
            s_tof.last_startup_operation,
            sizeof(status->last_startup_operation));
    xSemaphoreGive(s_tof.lock);
    return ESP_OK;
}

bool rehab_tof_status_is_quiescent(const rehab_tof_status_t *status)
{
    return status != NULL &&
           !status->snapshot_busy &&
           !status->request_pending &&
           !status->window_active;
}

bool rehab_tof_is_ready(void)
{
    rehab_tof_status_t status;
    return rehab_tof_get_status(&status) == ESP_OK && status.ready;
}

esp_err_t rehab_tof_stop(void)
{
    if (s_tof.lock == NULL || !s_tof.initialized) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    TaskHandle_t task = s_tof.task;
    s_tof.stop_requested = true;
    tof_invalidate_extended_locked();
    if (task != NULL) {
        s_tof.state = REHAB_TOF_STOPPING;
    }
    xSemaphoreGive(s_tof.lock);
    if (task == NULL) {
        return ESP_OK;
    }
    if (task != (TaskHandle_t)1) {
        xTaskNotifyGive(task);
    }
    if (xSemaphoreTake(s_tof.stopped, pdMS_TO_TICKS(5000)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

void rehab_tof_deinit(void)
{
    if (s_tof.lock == NULL || !s_tof.initialized) {
        return;
    }
    if (rehab_tof_stop() != ESP_OK) {
        return;
    }
    xSemaphoreTake(s_tof.lock, portMAX_DELAY);
    s_tof.initialized = false;
    s_tof.state = REHAB_TOF_UNINITIALIZED;
    tof_invalidate_extended_locked();
    xSemaphoreGive(s_tof.lock);
}

const char *rehab_tof_state_name(rehab_tof_state_t state)
{
    switch (state) {
    case REHAB_TOF_UNINITIALIZED:
        return "UNINITIALIZED";
    case REHAB_TOF_STARTING:
        return "STARTING";
    case REHAB_TOF_READY:
        return "READY";
    case REHAB_TOF_STOPPING:
        return "STOPPING";
    case REHAB_TOF_STOPPED:
        return "STOPPED";
    case REHAB_TOF_ERROR:
        return "ERROR";
    default:
        return "UNKNOWN";
    }
}
