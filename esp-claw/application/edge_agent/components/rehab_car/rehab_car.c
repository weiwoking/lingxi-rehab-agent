/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "rehab_car.h"

#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <arpa/inet.h>
#include <sys/socket.h>

#include "esp_console.h"
#include "esp_log.h"
#include "driver/uart.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#define TAG "REHAB_CAR"

/* The legacy parser rejects packets of 96 bytes or more. */
#define REHAB_CAR_PAYLOAD_CAPACITY       96U
#define REHAB_CAR_BUTTONS_CAPACITY       REHAB_CAR_PAYLOAD_CAPACITY
#define REHAB_CAR_MAX_SEQUENCE_STEPS     32U
#define REHAB_CAR_MAX_SEQUENCE_PAUSE_MS  5000U
#define REHAB_CAR_SEQUENCE_TASK_STACK    4096U
#define REHAB_CAR_SEQUENCE_TASK_PRIORITY 4U
#define REHAB_CAR_DELETE_WAIT_MS         2000U
#define REHAB_CAR_NEUTRAL_AXIS           128U
#define REHAB_CAR_UART_RX_BUFFER_SIZE     256U
#define REHAB_CAR_UART_DRAIN_MAX_BYTES    128U
#define REHAB_CAR_UART_DRAIN_CHUNK_BYTES  64U
#define REHAB_CAR_UART_TX_WAIT_MS         100U
#define REHAB_CAR_UART_LINE_CAPACITY      (REHAB_CAR_PAYLOAD_CAPACITY + 1U)
#define REHAB_CAR_S3_MODE_CONFIRMATION    "local_uart_write_only_no_ack"

struct rehab_car_t {
    SemaphoreHandle_t mutex;
    SemaphoreHandle_t sequence_done;
    int socket_fd;
    struct sockaddr_in target;
    char target_ipv4[INET_ADDRSTRLEN];
    uint16_t target_port;
    rehab_car_transport_t transport;
    bool uart_initialized;
    rehab_car_s3_input_mode_t requested_s3_input_mode;
    esp_err_t last_s3_mode_switch_result;
    uint8_t speed_level;
    uint32_t control_generation;
    uint32_t active_motion_generation;
    uint8_t active_motion_speed_level;
    rehab_car_control_owner_t control_owner;
    uint32_t control_owner_token;
    rehab_car_control_preempt_cb_t control_preempt_cb;
    void *control_preempt_ctx;
    bool motion_active;
    bool sequence_active;
    bool sequence_cancel_requested;
    bool deleting;
    TaskHandle_t sequence_task;
    uint8_t sequence_speed_level;
    size_t sequence_step_index;
    size_t sequence_step_count;
    char last_command[24];
    size_t last_payload_length;
    int last_send_result;
    int last_error;
    esp_err_t last_operation_result;
    uint32_t last_send_count;
    esp_err_t last_final_stop_result;
    bool car_command_registered;
    bool pad_command_registered;
    bool stop_command_registered;
};

typedef struct {
    rehab_car_handle_t handle;
    rehab_car_sequence_step_t *steps;
    size_t step_count;
    uint8_t speed_level;
} rehab_car_sequence_context_t;

/* Exact symmetric level 1/2/3 axis values from the verified legacy UI sender. */
static const uint8_t s_axis_low_by_level[3] = {64, 42, 21};
static const uint8_t s_axis_high_by_level[3] = {192, 214, 235};
static const char s_stop_payload[] = "STOP";

/* Exact tokens accepted by addVirtualButton() in the sealed car firmware. */
static const char *const s_button_tokens[] = {
    "NONE", "A", "B", "X", "Y", "L2", "R2", "UP", "DPAD_UP",
    "DOWN", "DPAD_DOWN", "LEFT", "DPAD_LEFT", "RIGHT", "DPAD_RIGHT",
};

_Static_assert(sizeof(s_stop_payload) - 1 == 4, "STOP must be exactly four bytes");
_Static_assert(REHAB_CAR_DEFAULT_SPEED_LEVEL == 3, "Phase 3A2 default speed is level 3");
_Static_assert(REHAB_CAR_HOLD_INTERVAL_MS == 40, "legacy PAD repeat interval changed");
_Static_assert(REHAB_CAR_UART_PORT == UART_NUM_1, "car UART must remain UART_NUM_1");

/* Weak hardware seam: component tests replace these without a physical UART. */
__attribute__((weak)) esp_err_t rehab_car_uart_backend_install(void)
{
    if (uart_is_driver_installed(UART_NUM_1)) {
        return ESP_ERR_INVALID_STATE;
    }
    const uart_config_t config = {
        .baud_rate = REHAB_CAR_UART_BAUD_RATE,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    esp_err_t err = uart_driver_install(UART_NUM_1,
                                        REHAB_CAR_UART_RX_BUFFER_SIZE,
                                        0, 0, NULL, 0);
    if (err != ESP_OK) {
        return err;
    }
    err = uart_param_config(UART_NUM_1, &config);
    if (err == ESP_OK) {
        err = uart_set_pin(UART_NUM_1,
                           REHAB_CAR_UART_TX_GPIO,
                           REHAB_CAR_UART_RX_GPIO,
                           UART_PIN_NO_CHANGE,
                           UART_PIN_NO_CHANGE);
    }
    if (err != ESP_OK) {
        (void)uart_driver_delete(UART_NUM_1);
    }
    return err;
}

__attribute__((weak)) esp_err_t rehab_car_uart_backend_uninstall(void)
{
    return uart_driver_delete(UART_NUM_1);
}

__attribute__((weak)) int rehab_car_uart_backend_write(const void *data,
                                                        size_t length)
{
    return uart_write_bytes(UART_NUM_1, data, length);
}

__attribute__((weak)) esp_err_t rehab_car_uart_backend_wait_tx_done(
    TickType_t ticks_to_wait)
{
    return uart_wait_tx_done(UART_NUM_1, ticks_to_wait);
}

__attribute__((weak)) void rehab_car_uart_backend_drain_rx(void)
{
    size_t buffered = 0;
    if (uart_get_buffered_data_len(UART_NUM_1, &buffered) != ESP_OK) {
        return;
    }
    size_t remaining = buffered < REHAB_CAR_UART_DRAIN_MAX_BYTES
                           ? buffered
                           : REHAB_CAR_UART_DRAIN_MAX_BYTES;
    uint8_t discarded[REHAB_CAR_UART_DRAIN_CHUNK_BYTES];
    while (remaining > 0) {
        size_t chunk = remaining < sizeof(discarded) ? remaining : sizeof(discarded);
        int read = uart_read_bytes(UART_NUM_1, discarded, chunk, 0);
        if (read <= 0) {
            break;
        }
        remaining -= (size_t)read;
    }
}

static esp_err_t rehab_car_lock(rehab_car_handle_t handle)
{
    if (handle == NULL || handle->mutex == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    return xSemaphoreTake(handle->mutex, portMAX_DELAY) == pdTRUE
               ? ESP_OK
               : ESP_ERR_TIMEOUT;
}

static void rehab_car_unlock(rehab_car_handle_t handle)
{
    xSemaphoreGive(handle->mutex);
}

static const char *rehab_car_direction_name(rehab_car_direction_t direction)
{
    switch (direction) {
    case REHAB_CAR_DIRECTION_FORWARD:
        return "forward";
    case REHAB_CAR_DIRECTION_BACKWARD:
        return "backward";
    case REHAB_CAR_DIRECTION_STRAFE_LEFT:
        return "strafe_left";
    case REHAB_CAR_DIRECTION_STRAFE_RIGHT:
        return "strafe_right";
    case REHAB_CAR_DIRECTION_ROTATE_CCW:
        return "rotate_ccw";
    case REHAB_CAR_DIRECTION_ROTATE_CW:
        return "rotate_cw";
    case REHAB_CAR_DIRECTION_STOP:
        return "stop";
    default:
        return "invalid";
    }
}

const char *rehab_car_control_owner_name(rehab_car_control_owner_t owner)
{
    switch (owner) {
    case REHAB_CAR_CONTROL_OWNER_NONE:
        return "none";
    case REHAB_CAR_CONTROL_OWNER_DEMO:
        return "demo";
    case REHAB_CAR_CONTROL_OWNER_TARGET_FOLLOW:
        return "target_follow";
    case REHAB_CAR_CONTROL_OWNER_ENVIRONMENT_SCAN:
        return "environment_scan";
    case REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW:
        return "person_follow";
    default:
        return "unknown";
    }
}

const char *rehab_car_transport_name(rehab_car_transport_t transport)
{
    switch (transport) {
    case REHAB_CAR_TRANSPORT_UDP:
        return "udp";
    case REHAB_CAR_TRANSPORT_UART:
        return "uart";
    default:
        return "unknown";
    }
}

const char *rehab_car_s3_input_mode_name(rehab_car_s3_input_mode_t mode)
{
    switch (mode) {
    case REHAB_CAR_S3_INPUT_MODE_SERIAL:
        return "SERIAL";
    case REHAB_CAR_S3_INPUT_MODE_BLE:
        return "BLE";
    default:
        return "UNKNOWN";
    }
}

static uint32_t rehab_car_next_owner_token_locked(rehab_car_handle_t handle)
{
    uint32_t token = ++handle->control_owner_token;
    if (token == 0) {
        token = ++handle->control_owner_token;
    }
    return token;
}

static bool rehab_car_lease_valid_locked(
    rehab_car_handle_t handle, const rehab_car_control_lease_t *lease)
{
    return lease != NULL && lease->owner != REHAB_CAR_CONTROL_OWNER_NONE &&
           lease->token != 0 && handle->control_owner == lease->owner &&
           handle->control_owner_token == lease->token;
}

static bool rehab_car_direction_is_motion(rehab_car_direction_t direction)
{
    return direction >= REHAB_CAR_DIRECTION_FORWARD &&
           direction <= REHAB_CAR_DIRECTION_ROTATE_CW;
}

static bool rehab_car_button_token_allowed(const char *token, size_t token_length)
{
    if (token_length == 0) {
        return true;
    }
    for (size_t i = 0; i < sizeof(s_button_tokens) / sizeof(s_button_tokens[0]); ++i) {
        size_t allowed_length = strlen(s_button_tokens[i]);
        if (token_length == allowed_length &&
            memcmp(token, s_button_tokens[i], token_length) == 0) {
            return true;
        }
    }
    return false;
}

static int rehab_car_normalize_buttons(const char *buttons,
                                       char *normalized,
                                       size_t normalized_capacity)
{
    if (buttons == NULL || normalized == NULL || normalized_capacity == 0) {
        return EINVAL;
    }

    size_t length = strnlen(buttons, normalized_capacity);
    if (length >= normalized_capacity) {
        return EMSGSIZE;
    }

    for (size_t i = 0; i < length; ++i) {
        unsigned char value = (unsigned char)buttons[i];
        if (value >= 'a' && value <= 'z') {
            value = (unsigned char)(value - 'a' + 'A');
        }
        if (!((value >= 'A' && value <= 'Z') ||
              (value >= '0' && value <= '9') || value == '_' || value == '|')) {
            return EINVAL;
        }
        normalized[i] = (char)value;
    }
    normalized[length] = '\0';

    size_t token_start = 0;
    for (size_t pos = 0; pos <= length; ++pos) {
        if (pos == length || normalized[pos] == '|') {
            if (!rehab_car_button_token_allowed(normalized + token_start,
                                                pos - token_start)) {
                return EINVAL;
            }
            token_start = pos + 1;
        }
    }
    return 0;
}

/* The only PAD payload formatter in the component. */
static int rehab_car_format_pad_payload(char *payload,
                                        size_t payload_capacity,
                                        size_t *payload_length,
                                        uint8_t lx,
                                        uint8_t ly,
                                        uint8_t rx,
                                        uint8_t ry,
                                        const char *buttons)
{
    if (payload == NULL || payload_capacity == 0 || payload_length == NULL) {
        return EINVAL;
    }

    char normalized_buttons[REHAB_CAR_BUTTONS_CAPACITY];
    int validation_error = rehab_car_normalize_buttons(buttons,
                                                       normalized_buttons,
                                                       sizeof(normalized_buttons));
    if (validation_error != 0) {
        return validation_error;
    }

    int written = snprintf(payload,
                           payload_capacity,
                           "PAD,%u,%u,%u,%u,%s",
                           (unsigned)lx,
                           (unsigned)ly,
                           (unsigned)rx,
                           (unsigned)ry,
                           normalized_buttons);
    if (written < 0) {
        return EINVAL;
    }
    if ((size_t)written >= payload_capacity) {
        return EMSGSIZE;
    }
    *payload_length = (size_t)written;
    return 0;
}

static esp_err_t rehab_car_map_direction(uint8_t speed_level,
                                         rehab_car_direction_t direction,
                                         uint8_t *lx,
                                         uint8_t *ly,
                                         uint8_t *rx,
                                         uint8_t *ry)
{
    if (speed_level < 1 || speed_level > 3 || !rehab_car_direction_is_motion(direction) ||
        lx == NULL || ly == NULL || rx == NULL || ry == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    const uint8_t low = s_axis_low_by_level[speed_level - 1];
    const uint8_t high = s_axis_high_by_level[speed_level - 1];
    *lx = REHAB_CAR_NEUTRAL_AXIS;
    *ly = REHAB_CAR_NEUTRAL_AXIS;
    *rx = REHAB_CAR_NEUTRAL_AXIS;
    *ry = REHAB_CAR_NEUTRAL_AXIS;

    switch (direction) {
    case REHAB_CAR_DIRECTION_FORWARD:
        *ly = low;
        break;
    case REHAB_CAR_DIRECTION_BACKWARD:
        *ly = high;
        break;
    case REHAB_CAR_DIRECTION_STRAFE_LEFT:
        *lx = low;
        break;
    case REHAB_CAR_DIRECTION_STRAFE_RIGHT:
        *lx = high;
        break;
    case REHAB_CAR_DIRECTION_ROTATE_CCW:
        *rx = low;
        break;
    case REHAB_CAR_DIRECTION_ROTATE_CW:
        *rx = high;
        break;
    default:
        return ESP_ERR_INVALID_ARG;
    }
    return ESP_OK;
}

static esp_err_t rehab_car_contract_self_check(void)
{
    static const char *const level3_expected[] = {
        "PAD,128,21,128,128,NONE",
        "PAD,128,235,128,128,NONE",
        "PAD,21,128,128,128,NONE",
        "PAD,235,128,128,128,NONE",
        "PAD,128,128,21,128,NONE",
        "PAD,128,128,235,128,NONE",
    };

    for (uint8_t level = 1; level <= 3; ++level) {
        if (s_axis_low_by_level[level - 1] >= REHAB_CAR_NEUTRAL_AXIS ||
            s_axis_high_by_level[level - 1] <= REHAB_CAR_NEUTRAL_AXIS) {
            return ESP_ERR_INVALID_STATE;
        }
    }
    for (int direction = REHAB_CAR_DIRECTION_FORWARD;
         direction <= REHAB_CAR_DIRECTION_ROTATE_CW;
         ++direction) {
        uint8_t lx, ly, rx, ry;
        char payload[REHAB_CAR_PAYLOAD_CAPACITY];
        size_t payload_length = 0;
        if (rehab_car_map_direction(3, (rehab_car_direction_t)direction,
                                    &lx, &ly, &rx, &ry) != ESP_OK ||
            rehab_car_format_pad_payload(payload, sizeof(payload), &payload_length,
                                         lx, ly, rx, ry, "NONE") != 0 ||
            payload_length != strlen(level3_expected[direction]) ||
            memcmp(payload, level3_expected[direction], payload_length) != 0) {
            return ESP_ERR_INVALID_STATE;
        }
    }
    return ESP_OK;
}

static esp_err_t rehab_car_ensure_socket_locked(rehab_car_handle_t handle)
{
    if (handle->socket_fd >= 0) {
        return ESP_OK;
    }

    int socket_fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (socket_fd < 0) {
        handle->last_error = errno;
        return ESP_FAIL;
    }
    int enabled = 1;
    if (setsockopt(socket_fd, SOL_SOCKET, SO_BROADCAST, &enabled, sizeof(enabled)) < 0) {
        handle->last_error = errno;
        close(socket_fd);
        return ESP_FAIL;
    }
    handle->socket_fd = socket_fd;
    handle->last_error = 0;
    return ESP_OK;
}

static esp_err_t rehab_car_ensure_uart_locked(rehab_car_handle_t handle)
{
    if (handle->uart_initialized) {
        return ESP_OK;
    }
    esp_err_t err = rehab_car_uart_backend_install();
    if (err != ESP_OK) {
        handle->last_error = (int)err;
        handle->last_operation_result = err;
        ESP_LOGE(TAG,
                 "transport=uart uart=%d tx_gpio=%d rx_gpio=%d baud=%d init=failed error=%s",
                 REHAB_CAR_UART_PORT,
                 REHAB_CAR_UART_TX_GPIO,
                 REHAB_CAR_UART_RX_GPIO,
                 REHAB_CAR_UART_BAUD_RATE,
                 esp_err_to_name(err));
        return err;
    }
    handle->uart_initialized = true;
    handle->last_error = 0;
    ESP_LOGI(TAG,
             "transport=uart uart=%d tx_gpio=%d rx_gpio=%d baud=%d data=8 parity=none "
             "stop_bits=1 flow_control=none rx_policy=bounded_discard init=ok",
             REHAB_CAR_UART_PORT,
             REHAB_CAR_UART_TX_GPIO,
             REHAB_CAR_UART_RX_GPIO,
             REHAB_CAR_UART_BAUD_RATE);
    return ESP_OK;
}

static void rehab_car_log_send_locked(rehab_car_handle_t handle,
                                      const char *command,
                                      const char *payload,
                                      size_t payload_length,
                                      int send_result,
                                      int last_error)
{
    strlcpy(handle->last_command, command, sizeof(handle->last_command));
    handle->last_payload_length = payload_length;
    handle->last_send_result = send_result;
    handle->last_error = last_error;

    ESP_LOG_LEVEL(last_error == 0 && send_result == (int)payload_length
                      ? ESP_LOG_INFO
                      : ESP_LOG_WARN,
                  TAG,
                  "transport=%s target_ip=%s target_port=%u command=%s payload=%s "
                  "wire_len=%u send_result=%d speed=%u last_error=%d",
                  rehab_car_transport_name(handle->transport),
                  handle->target_ipv4,
                  (unsigned)handle->target_port,
                  command,
                  payload,
                  (unsigned)payload_length,
                  send_result,
                  (unsigned)(handle->motion_active
                      ? handle->active_motion_speed_level
                      : handle->speed_level),
                  last_error);
}

/* The only transport seam. PAD/STOP formatters and timed movement stay above it. */
static esp_err_t rehab_car_send_raw_locked(rehab_car_handle_t handle,
                                           const char *command,
                                           const char *payload,
                                           size_t payload_length)
{
    if (command == NULL || payload == NULL || payload_length == 0 ||
        payload_length >= REHAB_CAR_PAYLOAD_CAPACITY) {
        return ESP_ERR_INVALID_ARG;
    }
    for (size_t i = 0; i < payload_length; ++i) {
        unsigned char value = (unsigned char)payload[i];
        /* Allow printable ASCII space for the fixed "MODE BLE/SERIAL" lines. */
        if (value < 0x20 || value > 0x7e) {
            rehab_car_log_send_locked(handle, command, "-", 0, -1, EINVAL);
            return ESP_ERR_INVALID_ARG;
        }
    }

    if (handle->transport == REHAB_CAR_TRANSPORT_UDP) {
        if (rehab_car_ensure_socket_locked(handle) != ESP_OK) {
            rehab_car_log_send_locked(handle, command, payload, payload_length,
                                      -1, handle->last_error);
            return ESP_FAIL;
        }

        errno = 0;
        int sent = sendto(handle->socket_fd,
                          payload,
                          payload_length,
                          0,
                          (const struct sockaddr *)&handle->target,
                          sizeof(handle->target));
        int send_error = sent < 0 ? errno : 0;
        if (sent >= 0 && sent != (int)payload_length) {
            send_error = EIO;
        }
        rehab_car_log_send_locked(handle, command, payload, payload_length,
                                  sent, send_error);
        return send_error == 0 ? ESP_OK : ESP_FAIL;
    }

    if (handle->transport != REHAB_CAR_TRANSPORT_UART ||
        rehab_car_ensure_uart_locked(handle) != ESP_OK) {
        rehab_car_log_send_locked(handle, command, payload, payload_length,
                                  -1, handle->last_error != 0
                                          ? handle->last_error : EINVAL);
        return ESP_FAIL;
    }

    char line[REHAB_CAR_UART_LINE_CAPACITY];
    memcpy(line, payload, payload_length);
    line[payload_length] = '\n';
    size_t line_length = payload_length + 1U;
    rehab_car_uart_backend_drain_rx();
    int sent = rehab_car_uart_backend_write(line, line_length);
    int send_error = sent < 0 || sent != (int)line_length ? EIO : 0;
    if (send_error == 0) {
        esp_err_t wait_result = rehab_car_uart_backend_wait_tx_done(
            pdMS_TO_TICKS(REHAB_CAR_UART_TX_WAIT_MS));
        if (wait_result != ESP_OK) {
            send_error = (int)wait_result;
        }
    }
    rehab_car_log_send_locked(handle, command, payload, line_length,
                              sent, send_error);
    return send_error == 0 ? ESP_OK : ESP_FAIL;
}

static esp_err_t rehab_car_send_raw_checked(rehab_car_handle_t handle,
                                            const char *command,
                                            const char *payload,
                                            size_t payload_length,
                                            uint32_t expected_generation)
{
    esp_err_t err = rehab_car_lock(handle);
    if (err != ESP_OK) {
        return err;
    }
    if (handle->control_generation != expected_generation) {
        rehab_car_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    err = rehab_car_send_raw_locked(handle, command, payload, payload_length);
    rehab_car_unlock(handle);
    return err;
}

static esp_err_t rehab_car_begin_motion(rehab_car_handle_t handle,
                                        bool from_sequence,
                                        const rehab_car_control_lease_t *lease,
                                        uint8_t speed_override,
                                        uint32_t *generation,
                                        uint8_t *speed_level)
{
    esp_err_t err = rehab_car_lock(handle);
    if (err != ESP_OK) {
        return err;
    }
    bool sequence_owner = from_sequence && handle->sequence_active &&
                          handle->sequence_task == xTaskGetCurrentTaskHandle();
    bool behavior_owner = lease != NULL && rehab_car_lease_valid_locked(handle, lease);
    bool manual_owner_free = lease == NULL && !from_sequence &&
                             handle->control_owner == REHAB_CAR_CONTROL_OWNER_NONE;
    if (handle->deleting || handle->motion_active ||
        (from_sequence && !sequence_owner) ||
        (!from_sequence && handle->sequence_active) ||
        (lease != NULL && !behavior_owner) ||
        (lease == NULL && !from_sequence && !manual_owner_free)) {
        rehab_car_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }

    handle->motion_active = true;
    handle->active_motion_generation = handle->control_generation;
    *generation = handle->active_motion_generation;
    *speed_level = speed_override != 0 ? speed_override : handle->speed_level;
    handle->active_motion_speed_level = *speed_level;
    rehab_car_unlock(handle);
    return ESP_OK;
}

static void rehab_car_end_motion(rehab_car_handle_t handle,
                                 uint32_t generation)
{
    if (rehab_car_lock(handle) != ESP_OK) {
        return;
    }
    if (handle->motion_active &&
        handle->active_motion_generation == generation) {
        handle->motion_active = false;
        handle->active_motion_speed_level = 0;
    }
    rehab_car_unlock(handle);
}

static esp_err_t rehab_car_stop_internal(rehab_car_handle_t handle,
                                         bool cancel_sequence,
                                         bool preempt_owner,
                                         const char *command)
{
    rehab_car_control_preempt_cb_t preempt_cb = NULL;
    void *preempt_ctx = NULL;
    esp_err_t err = rehab_car_lock(handle);
    if (err != ESP_OK) {
        return err;
    }
    if (cancel_sequence && handle->sequence_active) {
        handle->sequence_cancel_requested = true;
    }
    if (preempt_owner && handle->control_owner != REHAB_CAR_CONTROL_OWNER_NONE) {
        preempt_cb = handle->control_preempt_cb;
        preempt_ctx = handle->control_preempt_ctx;
        handle->control_owner = REHAB_CAR_CONTROL_OWNER_NONE;
        (void)rehab_car_next_owner_token_locked(handle);
        handle->control_preempt_cb = NULL;
        handle->control_preempt_ctx = NULL;
    }

    /* Increment before STOP: no checked PAD send can occur after this STOP. */
    ++handle->control_generation;
    err = rehab_car_send_raw_locked(handle,
                                    command,
                                    s_stop_payload,
                                    sizeof(s_stop_payload) - 1);
    if (err == ESP_OK && handle->transport == REHAB_CAR_TRANSPORT_UART) {
        handle->requested_s3_input_mode = REHAB_CAR_S3_INPUT_MODE_SERIAL;
    }
    handle->last_operation_result = err;
    handle->last_send_count = 1;
    handle->last_final_stop_result = err;
    rehab_car_unlock(handle);
    if (preempt_cb != NULL) {
        preempt_cb(preempt_ctx);
    }
    return err;
}

static esp_err_t rehab_car_run_prepared_pad(rehab_car_handle_t handle,
                                            const char *command,
                                            const char *payload,
                                            size_t payload_length,
                                            uint32_t hold_ms,
                                            uint32_t generation)
{
    if (hold_ms == 0) {
        esp_err_t result = rehab_car_send_raw_checked(handle, command, payload,
                                                      payload_length, generation);
        esp_err_t stop_result = ESP_ERR_NOT_FINISHED;
        int pad_last_error = 0;
        if (result != ESP_OK) {
            if (rehab_car_lock(handle) == ESP_OK) {
                pad_last_error = handle->last_error;
                rehab_car_unlock(handle);
            }
            /* A failed one-shot movement must converge to an exact STOP attempt. */
            stop_result = rehab_car_stop_internal(handle, false, false, "STOP");
        }
        if (rehab_car_lock(handle) == ESP_OK) {
            int stop_last_error = handle->last_error;
            if (result != ESP_OK) {
                handle->last_error = stop_result != ESP_OK
                                         ? stop_last_error : pad_last_error;
            }
            handle->last_operation_result = result;
            handle->last_send_count = result == ESP_OK ? 1 : 0;
            handle->last_final_stop_result = stop_result;
            rehab_car_unlock(handle);
        }
        return result;
    }

    TickType_t interval_ticks = pdMS_TO_TICKS(REHAB_CAR_HOLD_INTERVAL_MS);
    if (interval_ticks == 0) {
        interval_ticks = 1;
    }
    const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(hold_ms);
    uint32_t send_count = 0;
    esp_err_t pad_result = ESP_OK;
    int pad_last_error = 0;

    while ((int32_t)(xTaskGetTickCount() - deadline) < 0) {
        pad_result = rehab_car_send_raw_checked(handle, command, payload,
                                                payload_length, generation);
        if (pad_result != ESP_OK) {
            if (rehab_car_lock(handle) == ESP_OK) {
                pad_last_error = handle->last_error;
                rehab_car_unlock(handle);
            }
            break;
        }
        ++send_count;

        TickType_t now = xTaskGetTickCount();
        TickType_t wake_at = now + interval_ticks;
        if ((int32_t)(wake_at - deadline) > 0) {
            wake_at = deadline;
        }
        if ((int32_t)(now - wake_at) < 0) {
            vTaskDelay(wake_at - now);
        }
    }

    /* Valid timed actions always reach this exact STOP attempt, including failures. */
    esp_err_t final_stop_result = rehab_car_stop_internal(handle, false, false, "STOP");
    int stop_last_error = 0;
    if (rehab_car_lock(handle) == ESP_OK) {
        stop_last_error = handle->last_error;
        int operation_last_error = final_stop_result != ESP_OK
                                       ? stop_last_error
                                       : (pad_result != ESP_OK ? pad_last_error : 0);
        handle->last_error = operation_last_error;
        handle->last_operation_result = final_stop_result != ESP_OK
                                            ? final_stop_result
                                            : pad_result;
        handle->last_send_count = send_count;
        handle->last_final_stop_result = final_stop_result;
        rehab_car_unlock(handle);

        ESP_LOG_LEVEL(pad_result == ESP_OK && final_stop_result == ESP_OK
                          ? ESP_LOG_INFO
                          : ESP_LOG_WARN,
                      TAG,
                      "command=%s payload=%s hold_ms=%u interval_ms=%u send_count=%u "
                      "final_stop_result=%d last_error=%d",
                      command,
                      payload,
                      (unsigned)hold_ms,
                      (unsigned)REHAB_CAR_HOLD_INTERVAL_MS,
                      (unsigned)send_count,
                      (int)final_stop_result,
                      operation_last_error);
    }

    return final_stop_result != ESP_OK ? final_stop_result : pad_result;
}

static esp_err_t rehab_car_send_pad_internal(rehab_car_handle_t handle,
                                             const char *command,
                                             uint8_t lx,
                                             uint8_t ly,
                                             uint8_t rx,
                                             uint8_t ry,
                                             const char *buttons,
                                             uint32_t hold_ms,
                                             bool from_sequence,
                                             const rehab_car_control_lease_t *lease,
                                             uint8_t speed_override)
{
    if (handle == NULL || command == NULL ||
        (hold_ms != 0 && (hold_ms < REHAB_CAR_HOLD_MIN_MS ||
                          hold_ms > REHAB_CAR_HOLD_MAX_MS))) {
        return ESP_ERR_INVALID_ARG;
    }

    uint32_t generation = 0;
    uint8_t ignored_speed = 0;
    esp_err_t err = rehab_car_begin_motion(handle, from_sequence, lease, speed_override,
                                           &generation, &ignored_speed);
    if (err != ESP_OK) {
        return err;
    }

    char payload[REHAB_CAR_PAYLOAD_CAPACITY];
    size_t payload_length = 0;
    int format_error = rehab_car_format_pad_payload(payload, sizeof(payload),
                                                    &payload_length,
                                                    lx, ly, rx, ry, buttons);
    if (format_error != 0) {
        rehab_car_end_motion(handle, generation);
        if (rehab_car_lock(handle) == ESP_OK) {
            handle->last_error = format_error;
            handle->last_operation_result = format_error == EMSGSIZE
                                                ? ESP_ERR_INVALID_SIZE
                                                : ESP_ERR_INVALID_ARG;
            rehab_car_unlock(handle);
        }
        return format_error == EMSGSIZE ? ESP_ERR_INVALID_SIZE : ESP_ERR_INVALID_ARG;
    }

    err = rehab_car_run_prepared_pad(handle, command, payload, payload_length,
                                     hold_ms, generation);
    rehab_car_end_motion(handle, generation);
    return err;
}

static esp_err_t rehab_car_move_internal(rehab_car_handle_t handle,
                                         rehab_car_direction_t direction,
                                         uint32_t hold_ms,
                                         bool from_sequence,
                                         const rehab_car_control_lease_t *lease,
                                         uint8_t speed_override)
{
    if (handle == NULL || !rehab_car_direction_is_motion(direction) ||
        (hold_ms != 0 && (hold_ms < REHAB_CAR_HOLD_MIN_MS ||
                          hold_ms > REHAB_CAR_HOLD_MAX_MS))) {
        return ESP_ERR_INVALID_ARG;
    }

    uint32_t generation = 0;
    uint8_t speed_level = 0;
    esp_err_t err = rehab_car_begin_motion(handle, from_sequence, lease, speed_override,
                                           &generation, &speed_level);
    if (err != ESP_OK) {
        return err;
    }

    uint8_t lx, ly, rx, ry;
    err = rehab_car_map_direction(speed_level, direction, &lx, &ly, &rx, &ry);
    if (err == ESP_OK) {
        char payload[REHAB_CAR_PAYLOAD_CAPACITY];
        size_t payload_length = 0;
        int format_error = rehab_car_format_pad_payload(payload, sizeof(payload),
                                                        &payload_length,
                                                        lx, ly, rx, ry, "NONE");
        if (format_error != 0) {
            err = format_error == EMSGSIZE ? ESP_ERR_INVALID_SIZE : ESP_ERR_INVALID_ARG;
        } else {
            err = rehab_car_run_prepared_pad(handle,
                                             rehab_car_direction_name(direction),
                                             payload,
                                             payload_length,
                                             hold_ms,
                                             generation);
        }
    }
    rehab_car_end_motion(handle, generation);
    return err;
}

esp_err_t rehab_car_set_speed(rehab_car_handle_t handle, uint8_t level)
{
    if (handle == NULL || level < 1 || level > 3) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = rehab_car_lock(handle);
    if (err != ESP_OK) {
        return err;
    }
    if (handle->deleting || handle->motion_active || handle->sequence_active ||
        handle->control_owner != REHAB_CAR_CONTROL_OWNER_NONE) {
        rehab_car_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    handle->speed_level = level;
    rehab_car_unlock(handle);
    ESP_LOGI(TAG, "command=speed ok=1 speed=%u send_triggered=0", (unsigned)level);
    return ESP_OK;
}

esp_err_t rehab_car_send_pad(rehab_car_handle_t handle,
                             uint8_t lx,
                             uint8_t ly,
                             uint8_t rx,
                             uint8_t ry,
                             const char *buttons,
                             uint32_t hold_ms)
{
    return rehab_car_send_pad_internal(handle, "PAD", lx, ly, rx, ry, buttons,
                                       hold_ms, false, NULL, 0);
}

esp_err_t rehab_car_move(rehab_car_handle_t handle,
                         rehab_car_direction_t direction,
                         uint32_t hold_ms)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    bool from_sequence = false;
    uint8_t speed_override = 0;
    if (rehab_car_lock(handle) == ESP_OK) {
        from_sequence = handle->sequence_active &&
                        handle->sequence_task == xTaskGetCurrentTaskHandle();
        if (from_sequence) {
            speed_override = handle->sequence_speed_level;
        }
        rehab_car_unlock(handle);
    }
    return rehab_car_move_internal(handle, direction, hold_ms,
                                   from_sequence, NULL, speed_override);
}

esp_err_t rehab_car_stop(rehab_car_handle_t handle)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    return rehab_car_stop_internal(handle, true, true, "STOP");
}

esp_err_t rehab_car_send_stop(rehab_car_handle_t handle)
{
    return rehab_car_stop(handle);
}

esp_err_t rehab_car_get_status(rehab_car_handle_t handle,
                               rehab_car_status_t *status)
{
    if (handle == NULL || status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = rehab_car_lock(handle);
    if (err != ESP_OK) {
        return err;
    }
    memset(status, 0, sizeof(*status));
    strlcpy(status->target_ipv4, handle->target_ipv4, sizeof(status->target_ipv4));
    status->target_port = handle->target_port;
    status->transport = handle->transport;
    status->uart_initialized = handle->uart_initialized;
    status->uart_port = REHAB_CAR_UART_PORT;
    status->uart_tx_gpio = REHAB_CAR_UART_TX_GPIO;
    status->uart_rx_gpio = REHAB_CAR_UART_RX_GPIO;
    status->uart_baud_rate = REHAB_CAR_UART_BAUD_RATE;
    status->requested_s3_input_mode = handle->requested_s3_input_mode;
    status->last_s3_mode_switch_result = handle->last_s3_mode_switch_result;
    status->speed_level = handle->speed_level;
    status->control_owner = handle->control_owner;
    status->control_owner_token = handle->control_owner_token;
    status->socket_open = handle->socket_fd >= 0;
    status->motion_active = handle->motion_active;
    status->sequence_active = handle->sequence_active;
    status->sequence_cancel_requested = handle->sequence_cancel_requested;
    status->sequence_step_index = handle->sequence_step_index;
    status->sequence_step_count = handle->sequence_step_count;
    strlcpy(status->last_command, handle->last_command, sizeof(status->last_command));
    status->last_payload_length = handle->last_payload_length;
    status->last_send_result = handle->last_send_result;
    status->last_error = handle->last_error;
    status->last_operation_result = handle->last_operation_result;
    status->last_send_count = handle->last_send_count;
    status->last_final_stop_result = handle->last_final_stop_result;
    rehab_car_unlock(handle);
    return ESP_OK;
}

esp_err_t rehab_car_set_s3_input_mode(rehab_car_handle_t handle,
                                      rehab_car_s3_input_mode_t mode)
{
    if (handle == NULL ||
        (mode != REHAB_CAR_S3_INPUT_MODE_SERIAL &&
         mode != REHAB_CAR_S3_INPUT_MODE_BLE)) {
        return ESP_ERR_INVALID_ARG;
    }

    esp_err_t err = rehab_car_lock(handle);
    if (err != ESP_OK) {
        return err;
    }
    if (handle->transport != REHAB_CAR_TRANSPORT_UART ||
        handle->deleting || handle->motion_active || handle->sequence_active ||
        handle->control_owner != REHAB_CAR_CONTROL_OWNER_NONE) {
        handle->last_s3_mode_switch_result = ESP_ERR_INVALID_STATE;
        rehab_car_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }

    const char *mode_payload = mode == REHAB_CAR_S3_INPUT_MODE_BLE
                                   ? "MODE BLE" : "MODE SERIAL";
    ++handle->control_generation;
    err = rehab_car_send_raw_locked(handle, "STOP", s_stop_payload,
                                    sizeof(s_stop_payload) - 1U);
    handle->last_final_stop_result = err;
    handle->last_send_count = err == ESP_OK ? 1U : 0U;
    if (err == ESP_OK) {
        err = rehab_car_send_raw_locked(handle, mode_payload, mode_payload,
                                        strlen(mode_payload));
        if (err == ESP_OK) {
            handle->last_send_count = 2U;
            handle->requested_s3_input_mode = mode;
        }
    }
    handle->last_operation_result = err;
    handle->last_s3_mode_switch_result = err;
    rehab_car_unlock(handle);

    ESP_LOG_LEVEL(err == ESP_OK ? ESP_LOG_INFO : ESP_LOG_WARN,
                  TAG,
                  "command=s3_input_mode requested_mode=%s sequence=STOP_then_MODE "
                  "confirmation=%s result=%s",
                  rehab_car_s3_input_mode_name(mode),
                  REHAB_CAR_S3_MODE_CONFIRMATION,
                  esp_err_to_name(err));
    return err;
}

esp_err_t rehab_car_set_transport(rehab_car_handle_t handle,
                                  rehab_car_transport_t transport)
{
    if (handle == NULL ||
        (transport != REHAB_CAR_TRANSPORT_UDP &&
         transport != REHAB_CAR_TRANSPORT_UART)) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = rehab_car_lock(handle);
    if (err != ESP_OK) {
        return err;
    }
    if (handle->transport == transport) {
        rehab_car_unlock(handle);
        return ESP_OK;
    }
    if (handle->deleting || handle->motion_active || handle->sequence_active ||
        handle->control_owner != REHAB_CAR_CONTROL_OWNER_NONE) {
        rehab_car_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }

    rehab_car_transport_t previous = handle->transport;
    ++handle->control_generation;
    err = rehab_car_send_raw_locked(handle, "STOP", s_stop_payload,
                                    sizeof(s_stop_payload) - 1U);
    handle->last_send_count = 1;
    handle->last_final_stop_result = err;
    if (err == ESP_OK && transport == REHAB_CAR_TRANSPORT_UART) {
        err = rehab_car_ensure_uart_locked(handle);
    }
    if (err == ESP_OK) {
        handle->transport = transport;
    }
    handle->last_operation_result = err;
    rehab_car_unlock(handle);

    ESP_LOG_LEVEL(err == ESP_OK ? ESP_LOG_INFO : ESP_LOG_WARN,
                  TAG,
                  "command=transport previous=%s requested=%s active=%s "
                  "stop_first=1 result=%s",
                  rehab_car_transport_name(previous),
                  rehab_car_transport_name(transport),
                  rehab_car_transport_name(err == ESP_OK ? transport : previous),
                  esp_err_to_name(err));
    return err;
}

esp_err_t rehab_car_control_acquire(rehab_car_handle_t handle,
                                    rehab_car_control_owner_t owner,
                                    rehab_car_control_preempt_cb_t preempt_cb,
                                    void *preempt_ctx,
                                    rehab_car_control_lease_t *out_lease)
{
    if (handle == NULL || out_lease == NULL ||
        (owner != REHAB_CAR_CONTROL_OWNER_TARGET_FOLLOW &&
         owner != REHAB_CAR_CONTROL_OWNER_ENVIRONMENT_SCAN &&
         owner != REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW)) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_lease, 0, sizeof(*out_lease));
    esp_err_t err = rehab_car_lock(handle);
    if (err != ESP_OK) {
        return err;
    }
    if (handle->deleting || handle->motion_active || handle->sequence_active ||
        handle->control_owner != REHAB_CAR_CONTROL_OWNER_NONE) {
        rehab_car_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    handle->control_owner = owner;
    uint32_t token = rehab_car_next_owner_token_locked(handle);
    handle->control_preempt_cb = preempt_cb;
    handle->control_preempt_ctx = preempt_ctx;
    out_lease->owner = owner;
    out_lease->token = token;
    rehab_car_unlock(handle);
    return ESP_OK;
}

esp_err_t rehab_car_control_move(rehab_car_handle_t handle,
                                 const rehab_car_control_lease_t *lease,
                                 rehab_car_direction_t direction,
                                 uint8_t speed_level,
                                 uint32_t hold_ms)
{
    if (handle == NULL || lease == NULL || speed_level < 1 || speed_level > 3 ||
        hold_ms < REHAB_CAR_HOLD_MIN_MS || hold_ms > REHAB_CAR_HOLD_MAX_MS) {
        return ESP_ERR_INVALID_ARG;
    }
    return rehab_car_move_internal(handle, direction, hold_ms, false, lease,
                                   speed_level);
}

esp_err_t rehab_car_control_stop(rehab_car_handle_t handle,
                                 const rehab_car_control_lease_t *lease)
{
    if (handle == NULL || lease == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = rehab_car_lock(handle);
    if (err != ESP_OK) {
        return err;
    }
    if (!rehab_car_lease_valid_locked(handle, lease)) {
        rehab_car_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    ++handle->control_generation;
    err = rehab_car_send_raw_locked(handle, "STOP", s_stop_payload,
                                    sizeof(s_stop_payload) - 1);
    handle->last_operation_result = err;
    handle->last_send_count = 1;
    handle->last_final_stop_result = err;
    rehab_car_unlock(handle);
    return err;
}

esp_err_t rehab_car_control_release(rehab_car_handle_t handle,
                                    rehab_car_control_lease_t *lease)
{
    if (handle == NULL || lease == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = rehab_car_lock(handle);
    if (err != ESP_OK) {
        return err;
    }
    if (!rehab_car_lease_valid_locked(handle, lease) || handle->motion_active) {
        rehab_car_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    handle->control_owner = REHAB_CAR_CONTROL_OWNER_NONE;
    (void)rehab_car_next_owner_token_locked(handle);
    handle->control_preempt_cb = NULL;
    handle->control_preempt_ctx = NULL;
    rehab_car_unlock(handle);
    memset(lease, 0, sizeof(*lease));
    return ESP_OK;
}

static bool rehab_car_sequence_cancelled(rehab_car_handle_t handle)
{
    bool cancelled = true;
    if (rehab_car_lock(handle) == ESP_OK) {
        cancelled = handle->sequence_cancel_requested || handle->deleting;
        rehab_car_unlock(handle);
    }
    return cancelled;
}

static esp_err_t rehab_car_sequence_delay(rehab_car_handle_t handle,
                                          uint32_t delay_ms)
{
    TickType_t remaining = pdMS_TO_TICKS(delay_ms);
    TickType_t quantum = pdMS_TO_TICKS(REHAB_CAR_HOLD_INTERVAL_MS);
    if (quantum == 0) {
        quantum = 1;
    }
    while (remaining > 0) {
        if (rehab_car_sequence_cancelled(handle)) {
            return ESP_ERR_INVALID_STATE;
        }
        TickType_t slice = remaining < quantum ? remaining : quantum;
        vTaskDelay(slice);
        remaining -= slice;
    }
    return ESP_OK;
}

static void rehab_car_sequence_task(void *arg)
{
    rehab_car_sequence_context_t *context = arg;
    rehab_car_handle_t handle = context->handle;
    esp_err_t result = ESP_OK;
    bool final_stop_already_sent = false;

    for (size_t i = 0; i < context->step_count; ++i) {
        if (rehab_car_sequence_cancelled(handle)) {
            result = ESP_ERR_INVALID_STATE;
            break;
        }
        if (rehab_car_lock(handle) == ESP_OK) {
            handle->sequence_step_index = i + 1;
            rehab_car_unlock(handle);
        }

        const rehab_car_sequence_step_t *step = &context->steps[i];
        ESP_LOGI(TAG,
                 "demo_step=%u/%u direction=%s hold_ms=%u pause_after_ms=%u speed=%u",
                 (unsigned)(i + 1),
                 (unsigned)context->step_count,
                 rehab_car_direction_name(step->direction),
                 (unsigned)step->hold_ms,
                 (unsigned)step->pause_after_ms,
                 (unsigned)context->speed_level);

        if (step->direction == REHAB_CAR_DIRECTION_STOP) {
            result = rehab_car_stop_internal(handle, false, false, "STOP");
            final_stop_already_sent = result == ESP_OK && i + 1 == context->step_count;
        } else {
            final_stop_already_sent = false;
            result = rehab_car_move(handle, step->direction, step->hold_ms);
        }
        if (result != ESP_OK) {
            break;
        }
        if (step->pause_after_ms != 0) {
            result = rehab_car_sequence_delay(handle, step->pause_after_ms);
            if (result != ESP_OK) {
                break;
            }
        }
    }

    if (!final_stop_already_sent || result != ESP_OK) {
        esp_err_t stop_result = rehab_car_stop_internal(handle, false, false, "STOP");
        if (result == ESP_OK && stop_result != ESP_OK) {
            result = stop_result;
        }
    }

    if (rehab_car_lock(handle) == ESP_OK) {
        handle->last_operation_result = result;
        handle->sequence_active = false;
        handle->sequence_cancel_requested = false;
        handle->sequence_task = NULL;
        if (handle->control_owner == REHAB_CAR_CONTROL_OWNER_DEMO) {
            handle->control_owner = REHAB_CAR_CONTROL_OWNER_NONE;
            (void)rehab_car_next_owner_token_locked(handle);
        }
        rehab_car_unlock(handle);
    }
    ESP_LOG_LEVEL(result == ESP_OK ? ESP_LOG_INFO : ESP_LOG_WARN,
                  TAG,
                  "demo_finished result=%s final_stop_attempted=1",
                  esp_err_to_name(result));

    xSemaphoreGive(handle->sequence_done);
    free(context->steps);
    free(context);
    vTaskDelete(NULL);
}

esp_err_t rehab_car_sequence_start(rehab_car_handle_t handle,
                                   const rehab_car_sequence_step_t *sequence,
                                   size_t step_count)
{
    if (handle == NULL || sequence == NULL || step_count == 0 ||
        step_count > REHAB_CAR_MAX_SEQUENCE_STEPS) {
        return ESP_ERR_INVALID_ARG;
    }
    for (size_t i = 0; i < step_count; ++i) {
        bool valid_motion = rehab_car_direction_is_motion(sequence[i].direction) &&
                            sequence[i].hold_ms >= REHAB_CAR_HOLD_MIN_MS &&
                            sequence[i].hold_ms <= REHAB_CAR_HOLD_MAX_MS;
        bool valid_stop = sequence[i].direction == REHAB_CAR_DIRECTION_STOP &&
                          sequence[i].hold_ms == 0;
        if ((!valid_motion && !valid_stop) ||
            sequence[i].pause_after_ms > REHAB_CAR_MAX_SEQUENCE_PAUSE_MS) {
            return ESP_ERR_INVALID_ARG;
        }
    }

    rehab_car_sequence_context_t *context = calloc(1, sizeof(*context));
    rehab_car_sequence_step_t *steps = calloc(step_count, sizeof(*steps));
    if (context == NULL || steps == NULL) {
        free(context);
        free(steps);
        return ESP_ERR_NO_MEM;
    }
    memcpy(steps, sequence, step_count * sizeof(*steps));
    context->handle = handle;
    context->steps = steps;
    context->step_count = step_count;

    esp_err_t err = rehab_car_lock(handle);
    if (err != ESP_OK) {
        free(steps);
        free(context);
        return err;
    }
    if (handle->deleting || handle->sequence_active || handle->motion_active ||
        handle->control_owner != REHAB_CAR_CONTROL_OWNER_NONE) {
        rehab_car_unlock(handle);
        free(steps);
        free(context);
        return ESP_ERR_INVALID_STATE;
    }
    while (xSemaphoreTake(handle->sequence_done, 0) == pdTRUE) {
    }
    context->speed_level = handle->speed_level;
    handle->sequence_speed_level = context->speed_level;
    handle->sequence_active = true;
    handle->sequence_cancel_requested = false;
    handle->sequence_step_index = 0;
    handle->sequence_step_count = step_count;
    handle->control_owner = REHAB_CAR_CONTROL_OWNER_DEMO;
    (void)rehab_car_next_owner_token_locked(handle);

    BaseType_t created = xTaskCreate(rehab_car_sequence_task,
                                     "car_demo",
                                     REHAB_CAR_SEQUENCE_TASK_STACK,
                                     context,
                                     REHAB_CAR_SEQUENCE_TASK_PRIORITY,
                                     &handle->sequence_task);
    if (created != pdPASS) {
        handle->sequence_active = false;
        handle->sequence_task = NULL;
        handle->control_owner = REHAB_CAR_CONTROL_OWNER_NONE;
        (void)rehab_car_next_owner_token_locked(handle);
        rehab_car_unlock(handle);
        free(steps);
        free(context);
        return ESP_ERR_NO_MEM;
    }
    rehab_car_unlock(handle);
    return ESP_OK;
}

esp_err_t rehab_car_sequence_cancel(rehab_car_handle_t handle)
{
    return rehab_car_stop(handle);
}

esp_err_t rehab_car_demo_start(rehab_car_handle_t handle)
{
    static const rehab_car_sequence_step_t demo[] = {
        {REHAB_CAR_DIRECTION_STOP,          0, 300},
        {REHAB_CAR_DIRECTION_FORWARD,    1200,   0},
        {REHAB_CAR_DIRECTION_BACKWARD,   1200, 300},
        {REHAB_CAR_DIRECTION_STRAFE_LEFT,1000,   0},
        {REHAB_CAR_DIRECTION_STRAFE_RIGHT,1000,300},
        {REHAB_CAR_DIRECTION_ROTATE_CCW,  700,   0},
        {REHAB_CAR_DIRECTION_ROTATE_CW,   700, 300},
        {REHAB_CAR_DIRECTION_FORWARD,     900,   0},
        {REHAB_CAR_DIRECTION_BACKWARD,    900, 250},
        {REHAB_CAR_DIRECTION_STRAFE_LEFT, 700,   0},
        {REHAB_CAR_DIRECTION_STRAFE_RIGHT,700, 250},
        {REHAB_CAR_DIRECTION_ROTATE_CCW,  450,   0},
        {REHAB_CAR_DIRECTION_ROTATE_CW,   450, 250},
        {REHAB_CAR_DIRECTION_FORWARD,     600,   0},
        {REHAB_CAR_DIRECTION_BACKWARD,    600,   0},
        {REHAB_CAR_DIRECTION_STOP,          0,   0},
    };

    esp_err_t err = rehab_car_set_speed(handle, 3);
    if (err != ESP_OK) {
        return err;
    }
    return rehab_car_sequence_start(handle, demo, sizeof(demo) / sizeof(demo[0]));
}

static bool rehab_car_parse_axis(const char *text, uint8_t *value)
{
    if (text == NULL || value == NULL || text[0] == '\0') {
        return false;
    }
    unsigned parsed = 0;
    for (size_t i = 0; text[i] != '\0'; ++i) {
        if (text[i] < '0' || text[i] > '9') {
            return false;
        }
        parsed = parsed * 10U + (unsigned)(text[i] - '0');
        if (parsed > 255U) {
            return false;
        }
    }
    *value = (uint8_t)parsed;
    return true;
}

static bool rehab_car_parse_hold_ms(const char *text, uint32_t *hold_ms)
{
    if (text == NULL || hold_ms == NULL || text[0] == '\0') {
        return false;
    }
    uint32_t parsed = 0;
    for (size_t i = 0; text[i] != '\0'; ++i) {
        if (text[i] < '0' || text[i] > '9') {
            return false;
        }
        parsed = parsed * 10U + (uint32_t)(text[i] - '0');
        if (parsed > REHAB_CAR_HOLD_MAX_MS) {
            return false;
        }
    }
    if (parsed < REHAB_CAR_HOLD_MIN_MS) {
        return false;
    }
    *hold_ms = parsed;
    return true;
}

static bool rehab_car_parse_direction(const char *text,
                                      rehab_car_direction_t *direction,
                                      bool *deprecated_alias)
{
    *deprecated_alias = false;
    if (strcmp(text, "forward") == 0) {
        *direction = REHAB_CAR_DIRECTION_FORWARD;
    } else if (strcmp(text, "backward") == 0) {
        *direction = REHAB_CAR_DIRECTION_BACKWARD;
    } else if (strcmp(text, "strafe-left") == 0) {
        *direction = REHAB_CAR_DIRECTION_STRAFE_LEFT;
    } else if (strcmp(text, "strafe-right") == 0) {
        *direction = REHAB_CAR_DIRECTION_STRAFE_RIGHT;
    } else if (strcmp(text, "rotate-ccw") == 0) {
        *direction = REHAB_CAR_DIRECTION_ROTATE_CCW;
    } else if (strcmp(text, "rotate-cw") == 0) {
        *direction = REHAB_CAR_DIRECTION_ROTATE_CW;
    } else if (strcmp(text, "left") == 0) {
        *direction = REHAB_CAR_DIRECTION_STRAFE_LEFT;
        *deprecated_alias = true;
    } else if (strcmp(text, "right") == 0) {
        *direction = REHAB_CAR_DIRECTION_STRAFE_RIGHT;
        *deprecated_alias = true;
    } else {
        return false;
    }
    return true;
}

static void rehab_car_print_usage(void)
{
    ESP_LOGI(TAG,
             "usage:\n"
             " car status\n car speed <1|2|3>\n"
             " car transport <status|uart|udp>\n"
             " car forward|backward|strafe-left|strafe-right [hold_ms]\n"
             " car rotate-ccw|rotate-cw [hold_ms]\n"
             " car stop\n car demo <start|stop|status>\n"
             " deprecated aliases: car left=car strafe-left, car right=car strafe-right\n"
             " raw: STOP | PAD <LX> <LY> <RX> <RY> <BUTTONS> [hold_ms]");
}

static void rehab_car_print_pad_usage(void)
{
    ESP_LOGI(TAG,
             "usage: PAD <LX> <LY> <RX> <RY> <BUTTONS> [hold_ms]\n"
             "axes: decimal 0..255; hold_ms: optional 100..5000\n"
             "examples:\n PAD 128 21 128 128 NONE\n"
             " PAD 128 21 128 128 NONE 1000");
}

static int rehab_car_console_handler(void *context, int argc, char **argv)
{
    rehab_car_handle_t handle = context;
    if (handle == NULL) {
        return 1;
    }

    if (argc == 2 && strcmp(argv[1], "status") == 0) {
        rehab_car_status_t status;
        if (rehab_car_get_status(handle, &status) != ESP_OK) {
            return 1;
        }
        ESP_LOGI(TAG,
                 "target_ip=%s target_port=%u command=status action_sent=0 transport=%s "
                 "socket=%s uart_initialized=%d uart=%d tx_gpio=%d rx_gpio=%d baud=%d "
                 "speed=%u owner=%s motion=%d demo=%d cancel=%d step=%u/%u last_command=%s "
                 "last_payload_len=%u last_send_result=%d last_error=%d",
                 status.target_ipv4, (unsigned)status.target_port,
                 rehab_car_transport_name(status.transport),
                 status.socket_open ? "open" : "lazy_closed",
                 status.uart_initialized,
                 status.uart_port,
                 status.uart_tx_gpio,
                 status.uart_rx_gpio,
                 status.uart_baud_rate,
                 (unsigned)status.speed_level,
                 rehab_car_control_owner_name(status.control_owner),
                 status.motion_active,
                 status.sequence_active, status.sequence_cancel_requested,
                 (unsigned)status.sequence_step_index,
                 (unsigned)status.sequence_step_count,
                 status.last_command, (unsigned)status.last_payload_length,
                 status.last_send_result, status.last_error);
        return 0;
    }

    if (argc == 3 && strcmp(argv[1], "transport") == 0) {
        if (strcmp(argv[2], "status") == 0) {
            rehab_car_status_t status;
            if (rehab_car_get_status(handle, &status) != ESP_OK) {
                return 1;
            }
            ESP_LOGI(TAG,
                     "command=transport status action_sent=0 active=%s "
                     "uart_initialized=%d uart=%d tx_gpio=%d rx_gpio=%d baud=%d",
                     rehab_car_transport_name(status.transport),
                     status.uart_initialized,
                     status.uart_port,
                     status.uart_tx_gpio,
                     status.uart_rx_gpio,
                     status.uart_baud_rate);
            return 0;
        }
        if (strcmp(argv[2], "uart") == 0) {
            return rehab_car_set_transport(handle,
                                           REHAB_CAR_TRANSPORT_UART) == ESP_OK
                       ? 0 : 1;
        }
        if (strcmp(argv[2], "udp") == 0) {
            return rehab_car_set_transport(handle,
                                           REHAB_CAR_TRANSPORT_UDP) == ESP_OK
                       ? 0 : 1;
        }
    }

    if (argc == 2 && strcmp(argv[1], "stop") == 0) {
        return rehab_car_stop(handle) == ESP_OK ? 0 : 1;
    }
    if (argc == 2 && strcmp(argv[1], "help") == 0) {
        rehab_car_print_usage();
        return 0;
    }
    if (argc == 3 && strcmp(argv[1], "speed") == 0) {
        if (strlen(argv[2]) != 1 || argv[2][0] < '1' || argv[2][0] > '3') {
            ESP_LOGW(TAG, "command=speed ok=0 reason=level_must_be_1_2_or_3");
            return 1;
        }
        return rehab_car_set_speed(handle, (uint8_t)(argv[2][0] - '0')) == ESP_OK ? 0 : 1;
    }
    if (argc == 3 && strcmp(argv[1], "demo") == 0) {
        if (strcmp(argv[2], "start") == 0) {
            return rehab_car_demo_start(handle) == ESP_OK ? 0 : 1;
        }
        if (strcmp(argv[2], "stop") == 0) {
            return rehab_car_sequence_cancel(handle) == ESP_OK ? 0 : 1;
        }
        if (strcmp(argv[2], "status") == 0) {
            rehab_car_status_t status;
            if (rehab_car_get_status(handle, &status) != ESP_OK) {
                return 1;
            }
            ESP_LOGI(TAG, "command=demo status active=%d cancel=%d step=%u/%u action_sent=0",
                     status.sequence_active, status.sequence_cancel_requested,
                     (unsigned)status.sequence_step_index,
                     (unsigned)status.sequence_step_count);
            return 0;
        }
    }

    if ((argc == 2 || argc == 3) && strcmp(argv[1], "demo") != 0) {
        rehab_car_direction_t direction;
        bool deprecated_alias = false;
        if (rehab_car_parse_direction(argv[1], &direction, &deprecated_alias)) {
            uint32_t hold_ms = 0;
            if (argc == 3 && !rehab_car_parse_hold_ms(argv[2], &hold_ms)) {
                ESP_LOGW(TAG, "command=%s ok=0 reason=hold_ms_must_be_100_to_5000", argv[1]);
                return 1;
            }
            if (deprecated_alias) {
                ESP_LOGW(TAG, "command=%s deprecated=1 use=%s",
                         argv[1], direction == REHAB_CAR_DIRECTION_STRAFE_LEFT
                                      ? "strafe-left" : "strafe-right");
            }
            esp_err_t err = rehab_car_move(handle, direction, hold_ms);
            if (err == ESP_ERR_INVALID_STATE) {
                rehab_car_status_t status = {0};
                if (rehab_car_get_status(handle, &status) == ESP_OK &&
                    status.control_owner == REHAB_CAR_CONTROL_OWNER_TARGET_FOLLOW) {
                    ESP_LOGW(TAG,
                             "command=%s action_sent=0 ok=0 reason=controlled_by_target_follow",
                             argv[1]);
                } else if (status.control_owner ==
                           REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW) {
                    ESP_LOGW(TAG,
                             "command=%s action_sent=0 ok=0 reason=controlled_by_person_follow",
                             argv[1]);
                }
            }
            return err == ESP_OK ? 0 : 1;
        }
    }

    rehab_car_print_usage();
    ESP_LOGW(TAG, "command=car action_sent=0 ok=0 reason=invalid_arguments");
    return 1;
}

static int rehab_car_stop_console_handler(void *context, int argc, char **argv)
{
    (void)argv;
    rehab_car_handle_t handle = context;
    if (handle == NULL || argc != 1) {
        ESP_LOGW(TAG, "usage: STOP");
        return 1;
    }
    return rehab_car_stop(handle) == ESP_OK ? 0 : 1;
}

static int rehab_car_pad_console_handler(void *context, int argc, char **argv)
{
    rehab_car_handle_t handle = context;
    if (handle == NULL || (argc != 6 && argc != 7)) {
        rehab_car_print_pad_usage();
        ESP_LOGW(TAG, "command=PAD action_sent=0 ok=0 reason=argument_count");
        return 1;
    }

    uint8_t axes[4];
    for (size_t i = 0; i < sizeof(axes) / sizeof(axes[0]); ++i) {
        if (!rehab_car_parse_axis(argv[i + 1], &axes[i])) {
            ESP_LOGW(TAG, "command=PAD action_sent=0 ok=0 reason=axis_%u_must_be_0_to_255",
                     (unsigned)i);
            return 1;
        }
    }
    uint32_t hold_ms = 0;
    if (argc == 7 && !rehab_car_parse_hold_ms(argv[6], &hold_ms)) {
        ESP_LOGW(TAG, "command=PAD action_sent=0 ok=0 reason=hold_ms_must_be_100_to_5000");
        return 1;
    }
    esp_err_t err = rehab_car_send_pad(handle, axes[0], axes[1], axes[2], axes[3],
                                       argv[5], hold_ms);
    if (err == ESP_ERR_INVALID_STATE) {
        rehab_car_status_t status = {0};
        if (rehab_car_get_status(handle, &status) == ESP_OK &&
            status.control_owner == REHAB_CAR_CONTROL_OWNER_TARGET_FOLLOW) {
            ESP_LOGW(TAG,
                     "command=PAD action_sent=0 ok=0 reason=controlled_by_target_follow");
        } else if (status.control_owner ==
                   REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW) {
            ESP_LOGW(TAG,
                     "command=PAD action_sent=0 ok=0 reason=controlled_by_person_follow");
        }
    }
    return err == ESP_OK ? 0 : 1;
}

esp_err_t rehab_car_create(const rehab_car_config_t *config,
                           rehab_car_handle_t *ret_handle)
{
    if (ret_handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *ret_handle = NULL;
    if (config == NULL || config->target_ipv4 == NULL ||
        config->target_port == 0 ||
        (config->default_transport != REHAB_CAR_TRANSPORT_UDP &&
         config->default_transport != REHAB_CAR_TRANSPORT_UART)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (rehab_car_contract_self_check() != ESP_OK) {
        return ESP_ERR_INVALID_STATE;
    }

    rehab_car_handle_t handle = calloc(1, sizeof(*handle));
    if (handle == NULL) {
        return ESP_ERR_NO_MEM;
    }
    handle->mutex = xSemaphoreCreateMutex();
    handle->sequence_done = xSemaphoreCreateBinary();
    if (handle->mutex == NULL || handle->sequence_done == NULL) {
        if (handle->mutex != NULL) {
            vSemaphoreDelete(handle->mutex);
        }
        if (handle->sequence_done != NULL) {
            vSemaphoreDelete(handle->sequence_done);
        }
        free(handle);
        return ESP_ERR_NO_MEM;
    }

    handle->socket_fd = -1;
    handle->target.sin_family = AF_INET;
    handle->target.sin_port = htons(config->target_port);
    handle->target_port = config->target_port;
    handle->transport = config->default_transport;
    handle->requested_s3_input_mode = REHAB_CAR_S3_INPUT_MODE_SERIAL;
    handle->last_s3_mode_switch_result = ESP_ERR_NOT_FINISHED;
    handle->speed_level = REHAB_CAR_DEFAULT_SPEED_LEVEL;
    handle->control_generation = 1;
    handle->control_owner_token = 1;
    handle->last_operation_result = ESP_OK;
    handle->last_final_stop_result = ESP_ERR_NOT_FINISHED;
    strlcpy(handle->last_command, "none", sizeof(handle->last_command));

    if (inet_pton(AF_INET, config->target_ipv4, &handle->target.sin_addr) != 1) {
        vSemaphoreDelete(handle->sequence_done);
        vSemaphoreDelete(handle->mutex);
        free(handle);
        return ESP_ERR_INVALID_ARG;
    }
    strlcpy(handle->target_ipv4, config->target_ipv4, sizeof(handle->target_ipv4));

    if (handle->transport == REHAB_CAR_TRANSPORT_UART) {
        esp_err_t uart_result = rehab_car_ensure_uart_locked(handle);
        if (uart_result != ESP_OK) {
            vSemaphoreDelete(handle->sequence_done);
            vSemaphoreDelete(handle->mutex);
            free(handle);
            return uart_result;
        }
    }
    *ret_handle = handle;

    ESP_LOGI(TAG,
             "initialized target_ip=%s target_port=%u default_transport=%s "
             "speed=%u socket=lazy_closed uart_initialized=%d action_sent=0",
             handle->target_ipv4, (unsigned)handle->target_port,
             rehab_car_transport_name(handle->transport),
             (unsigned)handle->speed_level,
             handle->uart_initialized);
    return ESP_OK;
}

static esp_err_t rehab_car_deregister_commands(rehab_car_handle_t handle)
{
    esp_err_t result = ESP_OK;
    if (handle->pad_command_registered) {
        esp_err_t err = esp_console_cmd_deregister("PAD");
        if (result == ESP_OK && err != ESP_OK) {
            result = err;
        }
        handle->pad_command_registered = false;
    }
    if (handle->stop_command_registered) {
        esp_err_t err = esp_console_cmd_deregister("STOP");
        if (result == ESP_OK && err != ESP_OK) {
            result = err;
        }
        handle->stop_command_registered = false;
    }
    if (handle->car_command_registered) {
        esp_err_t err = esp_console_cmd_deregister("car");
        if (result == ESP_OK && err != ESP_OK) {
            result = err;
        }
        handle->car_command_registered = false;
    }
    return result;
}

esp_err_t rehab_car_delete(rehab_car_handle_t handle)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t result = rehab_car_deregister_commands(handle);

    bool sequence_was_active = false;
    bool motion_was_active = false;
    bool service_was_used = false;
    if (rehab_car_lock(handle) == ESP_OK) {
        handle->deleting = true;
        sequence_was_active = handle->sequence_active;
        motion_was_active = handle->motion_active;
        service_was_used = handle->socket_fd >= 0 || handle->uart_initialized ||
                           handle->motion_active || handle->sequence_active;
        rehab_car_unlock(handle);
    }
    if (service_was_used) {
        esp_err_t stop_result = rehab_car_stop(handle);
        if (result == ESP_OK && stop_result != ESP_OK) {
            result = stop_result;
        }
    }
    if (sequence_was_active &&
        xSemaphoreTake(handle->sequence_done,
                       pdMS_TO_TICKS(REHAB_CAR_DELETE_WAIT_MS)) != pdTRUE) {
        ESP_LOGE(TAG, "delete timed out waiting for sequence worker; handle retained");
        return ESP_ERR_TIMEOUT;
    }
    if (motion_was_active && !sequence_was_active) {
        const TickType_t deadline = xTaskGetTickCount() +
                                    pdMS_TO_TICKS(REHAB_CAR_DELETE_WAIT_MS);
        for (;;) {
            bool motion_active = true;
            if (rehab_car_lock(handle) == ESP_OK) {
                motion_active = handle->motion_active;
                rehab_car_unlock(handle);
            }
            if (!motion_active) {
                break;
            }
            if ((int32_t)(xTaskGetTickCount() - deadline) >= 0) {
                ESP_LOGE(TAG, "delete timed out waiting for motion caller; handle retained");
                return ESP_ERR_TIMEOUT;
            }
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }

    if (rehab_car_lock(handle) == ESP_OK) {
        if (handle->socket_fd >= 0) {
            close(handle->socket_fd);
            handle->socket_fd = -1;
        }
        if (handle->uart_initialized) {
            esp_err_t uart_result = rehab_car_uart_backend_uninstall();
            if (result == ESP_OK && uart_result != ESP_OK) {
                result = uart_result;
            }
            handle->uart_initialized = false;
        }
        rehab_car_unlock(handle);
    }
    vSemaphoreDelete(handle->sequence_done);
    vSemaphoreDelete(handle->mutex);
    free(handle);
    return result;
}

esp_err_t rehab_car_register_console(rehab_car_handle_t handle)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (handle->car_command_registered || handle->pad_command_registered ||
        handle->stop_command_registered) {
        return ESP_ERR_INVALID_STATE;
    }

    const esp_console_cmd_t car_command = {
        .command = "car",
        .help = "Unified UDP/UART car service. Semantic moves share the raw PAD formatter; "
                 "timed moves repeat every 40ms and end with STOP. Demo is temporary and cancellable.\n"
                 "Examples:\n car status\n car transport status\n car transport uart\n"
                 " car transport udp\n car speed 3\n car forward\n car forward 1000\n"
                " car strafe-left 700\n car rotate-cw 450\n car stop\n"
                " car demo start\n car demo stop\n car demo status\n"
                "Deprecated aliases: car left, car right (strafe only).",
        .hint = "<status|transport|speed|forward|backward|strafe-left|strafe-right|rotate-ccw|rotate-cw|stop|demo>",
        .func_w_context = rehab_car_console_handler,
        .context = handle,
    };
    const esp_console_cmd_t stop_command = {
        .command = "STOP",
        .help = "Cancel any demo and send exact STOP on the active UDP/UART transport. Example: STOP",
        .func_w_context = rehab_car_stop_console_handler,
        .context = handle,
    };
    const esp_console_cmd_t pad_command = {
        .command = "PAD",
        .help = "Send one validated legacy PAD payload, or repeat every 40ms for "
                "hold_ms=100..5000 and finish with exact STOP.\n"
                "Examples:\n PAD 128 21 128 128 NONE\n"
                " PAD 128 21 128 128 NONE 1000\n"
                " PAD 128 128 21 128 NONE\n PAD 128 128 235 128 NONE",
        .hint = "<LX> <LY> <RX> <RY> <BUTTONS> [hold_ms]",
        .func_w_context = rehab_car_pad_console_handler,
        .context = handle,
    };

    esp_err_t err = esp_console_cmd_register(&car_command);
    if (err != ESP_OK) {
        return err;
    }
    handle->car_command_registered = true;
    err = esp_console_cmd_register(&stop_command);
    if (err != ESP_OK) {
        rehab_car_deregister_commands(handle);
        return err;
    }
    handle->stop_command_registered = true;
    err = esp_console_cmd_register(&pad_command);
    if (err != ESP_OK) {
        rehab_car_deregister_commands(handle);
        return err;
    }
    handle->pad_command_registered = true;
    return ESP_OK;
}
